"""
ADP Workforce Now clock-in status integration.

Env vars (set on Render once API credentials are obtained from ADP Marketplace):
  ADP_CLIENT_ID      — OAuth2 client ID
  ADP_CLIENT_SECRET  — OAuth2 client secret
  ADP_API_URL        — base URL (default: https://api.adp.com)
  ADP_CERT_PATH      — path to client certificate .pem (required for ADP production mTLS)
  ADP_KEY_PATH       — path to client private key .pem (required for ADP production mTLS)

To obtain credentials:
  1. Log in to ADP Marketplace: https://apps.adp.com
  2. Go to Developer Resources → My Apps → Create App
  3. Select "ADP Workforce Now" as the product
  4. Copy Client ID and Client Secret into Render env vars
  5. Request "Time & Attendance" scope
  6. For production: download the ADP-issued SSL certificate and store paths in env vars

OAuth2 flow: client_credentials grant
  POST https://accounts.adp.com/auth/oauth/v2/token
  Body: grant_type=client_credentials
  Auth: HTTP Basic (client_id:client_secret)

Clock-in detection:
  For each worker, GET /time/v1/workers/{aoid}/time-cards?$filter=timeCards/timeCardDate eq '{today}'
  A worker is clocked in if any workingTimePair has a startDateTime but null stopDateTime.

Rate limiting: Results are cached — worker list 60 min, clock-in status 2 min.
Parallel requests (max 5 concurrent) keep the 2-min refresh under 3 seconds for a 20-person roster.

Endpoints:
  GET /adp/status             — configuration status + current cached clock-in list
  GET /adp/clock-status/{name} — is a specific driver currently clocked in?
  POST /adp/refresh           — force-expire the clock-in cache and re-fetch
  GET /adp/setup-guide        — credential setup instructions as JSON
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/adp", tags=["adp"])

# ─── Constants ────────────────────────────────────────────────────────────────

ADP_TOKEN_URL = "https://accounts.adp.com/auth/oauth/v2/token"
_API_BASE = ""  # resolved lazily from ADP_API_URL env var

WORKER_CACHE_TTL = 3600   # 60 min — worker list rarely changes
CLOCKIN_CACHE_TTL = 120   # 2 min  — punch events need to be near-real-time
API_TIMEOUT = 8           # seconds per request
MAX_PARALLEL = 5          # max concurrent ADP requests

# ─── Types ────────────────────────────────────────────────────────────────────

# PunchStatus per worker: {"clocked_in": bool, "clocked_out": bool,
#                           "in_at": "ISO str or None", "out_at": "ISO str or None"}
PunchStatus = dict

# ─── In-memory caches (module-level, survive request lifetime) ────────────────

_lock = threading.Lock()

_token_cache: dict = {"token": None, "expires_at": 0.0}
# {aoid: normalized_name}  e.g. {"G3349PZHN2N8V64H": "derric reed"}
_worker_cache: dict = {"map": None, "fetched_at": 0.0}
# {normalized_name: PunchStatus} — full in+out status per worker, 2-min TTL
_punch_cache: dict = {"status": None, "fetched_at": 0.0}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _configured() -> bool:
    return bool(os.getenv("ADP_CLIENT_ID")) and bool(os.getenv("ADP_CLIENT_SECRET"))


def _api_base() -> str:
    return os.getenv("ADP_API_URL", "https://api.adp.com").rstrip("/")


def _cert():
    """Return (cert_path, key_path) tuple for mutual TLS, or None."""
    cert = os.getenv("ADP_CERT_PATH")
    key = os.getenv("ADP_KEY_PATH")
    return (cert, key) if cert and key else None


def normalize_name(name: str) -> str:
    """
    Normalize a driver name to 'firstname lastname' lowercase for comparison.
    Handles both 'First Last' and 'Last, First' payroll formats.
    """
    n = (name or "").strip().lower()
    if "," in n:
        parts = n.split(",", 1)
        n = f"{parts[1].strip()} {parts[0].strip()}"
    return n


def _get_token() -> Optional[str]:
    """Fetch or return cached OAuth2 bearer token."""
    with _lock:
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expires_at"]:
            return _token_cache["token"]

    try:
        import requests as _req
        resp = _req.post(
            ADP_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(os.getenv("ADP_CLIENT_ID", ""), os.getenv("ADP_CLIENT_SECRET", "")),
            cert=_cert(),
            timeout=API_TIMEOUT,
            verify=True,
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 3600))
        if token:
            with _lock:
                _token_cache["token"] = token
                # Subtract 60 s safety margin so we refresh before actual expiry
                _token_cache["expires_at"] = time.time() + expires_in - 60
        return token
    except Exception as exc:
        logger.warning("ADP token fetch failed: %s", exc)
        return None


def _fetch_worker_map(token: str) -> dict[str, str]:
    """
    Fetch all active workers from ADP Workforce Now.
    Returns {aoid: normalized_name}.

    API: GET /hr/v2/workers?$top=100&$skip=N
    Response shape (each worker):
      {
        "associateOID": "G3349PZHN2N8V64H",
        "person": {
          "legalName": {"givenName": "Derric", "familyName": "Reed"}
        },
        "workAssignments": [{"primaryIndicator": true,
                              "assignmentStatus": {"statusCode": {"codeValue": "Active"}}}]
      }
    """
    import requests as _req
    headers = {"Authorization": f"Bearer {token}"}
    workers: dict[str, str] = {}
    skip = 0
    top = 100

    while True:
        try:
            resp = _req.get(
                f"{_api_base()}/hr/v2/workers",
                params={"$top": top, "$skip": skip, "$select": "associateOID,person/legalName,workAssignments/assignmentStatus"},
                headers=headers,
                cert=_cert(),
                timeout=API_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json().get("workers", [])
            if not batch:
                break

            for w in batch:
                aoid = w.get("associateOID", "")
                if not aoid:
                    continue
                # Only include active workers
                active = any(
                    (a.get("assignmentStatus", {}).get("statusCode", {}).get("codeValue") == "Active")
                    for a in w.get("workAssignments", [])
                    if a.get("primaryIndicator")
                )
                if not active:
                    continue

                legal = w.get("person", {}).get("legalName", {})
                given = legal.get("givenName", "")
                family = legal.get("familyName", "")
                if given or family:
                    workers[aoid] = normalize_name(f"{given} {family}")

            if len(batch) < top:
                break
            skip += top

        except Exception as exc:
            logger.warning("ADP worker fetch failed (skip=%d): %s", skip, exc)
            break

    logger.info("ADP worker map built: %d active workers", len(workers))
    return workers


def _check_worker_punch(token: str, aoid: str, today_str: str) -> PunchStatus:
    """
    Return full punch status for worker {aoid} today.

    API: GET /time/v1/workers/{aoid}/time-cards
    Filter: timeCards/timeCardDate eq 'YYYY-MM-DD'

    Detection logic (from workingTimePairs in dailySummaries):
      - Open pair  (startDateTime set, stopDateTime null) → clocked_in=True, clocked_out=False
      - Closed pair (both set)                           → clocked_in=False, clocked_out=True
      - No pairs                                         → both False

    Note: ADP org may use /time/v2/... — verify against ADP Marketplace API explorer.
    """
    result: PunchStatus = {"clocked_in": False, "clocked_out": False, "in_at": None, "out_at": None}
    import requests as _req
    try:
        resp = _req.get(
            f"{_api_base()}/time/v1/workers/{aoid}/time-cards",
            params={"$filter": f"timeCards/timeCardDate eq '{today_str}'"},
            headers={"Authorization": f"Bearer {token}"},
            cert=_cert(),
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 404:
            return result
        resp.raise_for_status()
        for card in resp.json().get("timeCards", []):
            for summary in card.get("dailySummaries", []):
                for pair in summary.get("timeInformation", {}).get("workingTimePairs", []):
                    start = pair.get("startDateTime")
                    stop = pair.get("stopDateTime")
                    if start and not stop:
                        result["clocked_in"] = True
                        result["in_at"] = start
                    elif start and stop:
                        result["clocked_out"] = True
                        result["in_at"] = result["in_at"] or start
                        result["out_at"] = stop
    except Exception as exc:
        logger.debug("ADP time card check failed for %s: %s", aoid, exc)
    return result


def _get_worker_map() -> Optional[dict[str, str]]:
    """Return cached worker map, refreshing if stale."""
    with _lock:
        if _worker_cache["map"] is not None and (time.time() - _worker_cache["fetched_at"]) < WORKER_CACHE_TTL:
            return _worker_cache["map"]

    token = _get_token()
    if not token:
        return None

    worker_map = _fetch_worker_map(token)
    with _lock:
        _worker_cache["map"] = worker_map
        _worker_cache["fetched_at"] = time.time()
    return worker_map


def get_adp_punch_status() -> Optional[dict[str, PunchStatus]]:
    """
    Returns {normalized_name: PunchStatus} for all active ADP workers.
    PunchStatus = {"clocked_in": bool, "clocked_out": bool, "in_at": str|None, "out_at": str|None}

    Returns None if ADP is not configured or the API call fails.
    Result is cached for CLOCKIN_CACHE_TTL seconds (2 minutes).
    Used by rostering.get_wave_status() to enrich driver checklist.
    """
    if not _configured():
        return None

    with _lock:
        if _punch_cache["status"] is not None and (time.time() - _punch_cache["fetched_at"]) < CLOCKIN_CACHE_TTL:
            return _punch_cache["status"]

    worker_map = _get_worker_map()
    if not worker_map:
        return None

    token = _get_token()
    if not token:
        return None

    today_str = date.today().isoformat()
    aoids = list(worker_map.keys())

    punch_by_aoid: dict[str, PunchStatus] = {}
    try:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = {
                ex.submit(_check_worker_punch, token, aoid, today_str): aoid
                for aoid in aoids
            }
            for f in as_completed(futures, timeout=15):
                aoid = futures[f]
                try:
                    punch_by_aoid[aoid] = f.result()
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("ADP punch status parallel fetch failed: %s", exc)
        return None

    status_by_name: dict[str, PunchStatus] = {
        worker_map[aoid]: punch_by_aoid[aoid]
        for aoid in punch_by_aoid
        if aoid in worker_map
    }

    with _lock:
        _punch_cache["status"] = status_by_name
        _punch_cache["fetched_at"] = time.time()

    clocked_in = sum(1 for s in status_by_name.values() if s["clocked_in"])
    clocked_out = sum(1 for s in status_by_name.values() if s["clocked_out"])
    logger.info("ADP punch refresh: %d in, %d out of %d workers", clocked_in, clocked_out, len(aoids))
    return status_by_name


def get_clocked_in_names() -> Optional[set[str]]:
    """Backward-compat wrapper. Returns set of normalized names currently clocked in."""
    status = get_adp_punch_status()
    if status is None:
        return None
    return {name for name, s in status.items() if s["clocked_in"]}


def _invalidate_punch_cache() -> None:
    with _lock:
        _punch_cache["status"] = None
        _punch_cache["fetched_at"] = 0.0


# ─── API endpoints ────────────────────────────────────────────────────────────

@router.get("/status")
def adp_status():
    """
    Returns ADP configuration status and the current cached list of clocked-in workers.
    If ADP is not configured, returns guidance on what env vars are needed.
    """
    if not _configured():
        return {
            "configured": False,
            "message": (
                "ADP integration not configured. "
                "Set ADP_CLIENT_ID and ADP_CLIENT_SECRET on Render to enable clock-in status on the Wave Status dashboard."
            ),
            "required_env_vars": ["ADP_CLIENT_ID", "ADP_CLIENT_SECRET"],
            "optional_env_vars": ["ADP_API_URL", "ADP_CERT_PATH", "ADP_KEY_PATH"],
        }

    punch_status = get_adp_punch_status()
    with _lock:
        cache_age = round(time.time() - _punch_cache["fetched_at"], 1)
        worker_count = len(_worker_cache["map"] or {})

    clocked_in = [n for n, s in (punch_status or {}).items() if s["clocked_in"]]
    clocked_out = [n for n, s in (punch_status or {}).items() if s["clocked_out"]]

    return {
        "configured": True,
        "clocked_in_count": len(clocked_in) if punch_status is not None else None,
        "clocked_out_count": len(clocked_out) if punch_status is not None else None,
        "clocked_in_names": sorted(clocked_in) if punch_status is not None else None,
        "clocked_out_names": sorted(clocked_out) if punch_status is not None else None,
        "cache_age_seconds": cache_age,
        "worker_count": worker_count,
        "status": "ok" if punch_status is not None else "error",
    }


@router.get("/clock-status/{driver_name}")
def adp_driver_status(driver_name: str):
    """
    Check if a specific driver is currently clocked in to ADP.
    Returns {"clocked_in": true|false|null} — null means ADP is not configured.
    """
    if not _configured():
        return {"driver_name": driver_name, "clocked_in": None, "configured": False}

    names = get_clocked_in_names()
    if names is None:
        return {"driver_name": driver_name, "clocked_in": None, "configured": True, "error": "ADP API unavailable"}

    norm = normalize_name(driver_name)
    clocked_in = norm in names

    return {
        "driver_name": driver_name,
        "normalized": norm,
        "clocked_in": clocked_in,
        "configured": True,
    }


@router.post("/refresh")
def adp_refresh():
    """Force-expire the punch status cache so the next wave-status request pulls fresh data from ADP."""
    _invalidate_punch_cache()
    return {"status": "cache_cleared", "message": "Punch status cache cleared. Next request will re-fetch from ADP."}


@router.get("/setup-guide")
def adp_setup_guide():
    """Step-by-step instructions for obtaining ADP Workforce Now API credentials."""
    return {
        "title": "ADP Workforce Now API Setup",
        "steps": [
            {
                "step": 1,
                "title": "Create an ADP Developer Account",
                "detail": "Go to https://apps.adp.com and sign in with your ADP Workforce Now admin credentials. Navigate to Developer Resources.",
            },
            {
                "step": 2,
                "title": "Register an Application",
                "detail": "Click 'Create App', select 'ADP Workforce Now' as the product, name it 'NDAY Route Manager', and request the 'Time & Attendance' and 'Human Resources' scopes.",
            },
            {
                "step": 3,
                "title": "Copy Credentials to Render",
                "detail": "Copy the Client ID and Client Secret from the app detail page. Add them as Render env vars: ADP_CLIENT_ID and ADP_CLIENT_SECRET.",
            },
            {
                "step": 4,
                "title": "Production mTLS Certificate (if required)",
                "detail": "ADP production APIs require mutual TLS. Download the ADP-issued certificate and key. Store the .pem files accessible to the Render service and set ADP_CERT_PATH and ADP_KEY_PATH env vars.",
            },
            {
                "step": 5,
                "title": "Verify",
                "detail": "Hit GET /adp/status — if configured correctly it will return clocked_in_names. Then check the Wave Status dashboard at /wave-status to see the ADP badge on each driver card.",
            },
        ],
        "env_vars": {
            "ADP_CLIENT_ID": "required",
            "ADP_CLIENT_SECRET": "required",
            "ADP_API_URL": "optional (default: https://api.adp.com)",
            "ADP_CERT_PATH": "optional (required for production mTLS)",
            "ADP_KEY_PATH": "optional (required for production mTLS)",
        },
    }
