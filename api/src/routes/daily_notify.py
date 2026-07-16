"""
Daily Route Notification Module

Every morning after the DOP xlsx and Route Sheet PDF are posted to
#dlv3-nday-info, this module:
  1. Detects both files via the Slack API
  2. Downloads + parses them
  3. Builds a DailyRouteAssignment row per driver
  4. Sends each driver a Slack DM with their route details + attendance link
  5. Tracks acknowledgments when drivers tap the confirm link

The scheduler in main.py calls check_and_notify() every 10 min from 8-10 AM PT.
Dispatch can also trigger manually from /daily-notify dashboard.
"""

import io
import os
import re
import uuid
import tempfile
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

import pdfplumber
import pandas as pd
import requests
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from api.src.database import (
    get_db,
    DailyRouteAssignment,
    SlackIngestLog,
    EcpRosterPrompt,
    DOP,
    Cortex,
    DriverRosterEntry,
    get_latest_dop_rows,
    get_latest_cortex_rows,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PACIFIC = ZoneInfo("America/Los_Angeles")
NOTIFY_CHANNEL = os.getenv("SLACK_NOTIFY_CHANNEL", "C0AF48TPAMV")
CORTEX_CHANNEL = os.getenv("CORTEX_NOTIFY_CHANNEL", "C0BE4ALL1EX")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")

# Added 2026-07-16: send_all_dms()/send_sweeper_notifications() are a
# separate, older driver-DM pipeline than rostering.py's — they had no
# gate of their own and would fire real driver DMs any time check_and_notify()
# ran, as soon as the master SLACK_NOTIFICATIONS_ACTIVE switch was on,
# regardless of rostering.py's DRIVER_DM_ACTIVE flag. Same env var, same
# semantics, now actually checked here too.
_DM_ACTIVE = os.getenv("DRIVER_DM_ACTIVE", "false").lower() == "true"

# Ops managers who receive DMs after DOP is detected each morning
OPS_MANAGER_IDS = [
    ("Spencer", os.getenv("SLACK_ID_SPENCER", "U0AJGCYKXPB")),
    ("Fabian",  os.getenv("SLACK_ID_FABIAN",  "U0AJPQALDLL")),
    ("Luis",    os.getenv("SLACK_ID_LUIS",     "U0B36C9R8N4")),
]


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _download_slack_file(url: str) -> Optional[bytes]:
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Slack file download failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Time / date helpers
# ─────────────────────────────────────────────────────────────────────────────

_TIME_FMTS = ["%I:%M %p", "%I:%M%p", "%H:%M", "%I %p"]
_TIME_STRIP_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)


def _parse_wave_dt(wave_str: str) -> Optional[datetime]:
    """Parse 'HH:MM AM' (or similar) → datetime for arithmetic. Date portion is irrelevant."""
    if not wave_str:
        return None
    s = str(wave_str).strip()
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = _TIME_STRIP_RE.search(s)
    if m:
        return _parse_wave_dt(m.group(0).strip())
    return None


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _fmt_date(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day)


def _calc_show_time(wave_str: str) -> Optional[str]:
    """Drivers show up 25 minutes before wave."""
    dt = _parse_wave_dt(wave_str)
    return _fmt_time(dt - timedelta(minutes=25)) if dt else None


def _calc_return_time(wave_str: str, route_duration_minutes: Optional[int]) -> Optional[str]:
    """Expected return = wave + route_duration - 30 min (station tasks not in DOP duration)."""
    if not route_duration_minutes:
        return None
    dt = _parse_wave_dt(wave_str)
    return _fmt_time(dt + timedelta(minutes=int(route_duration_minutes) - 30)) if dt else None


# ─────────────────────────────────────────────────────────────────────────────
# Channel scanning
# ─────────────────────────────────────────────────────────────────────────────

def scan_channel_for_files(for_date: date) -> Dict[str, Optional[dict]]:
    """
    List recent files shared in NOTIFY_CHANNEL using files.list (requires only
    files:read scope — avoids groups:history which private channels would need).
    Returns {"dop": {id, name, url}, "route_sheet": {id, name, url}} — None if not found.
    """
    client = _slack_client()
    result: Dict[str, Optional[dict]] = {"dop": None, "route_sheet": None}
    if not client:
        return result

    try:
        resp = client.files_list(channel=NOTIFY_CHANNEL, count=20)
        files_list = resp.get("files", [])
    except Exception as exc:
        logger.warning("Channel file scan failed: %s", exc)
        files_list = []

    for f in files_list:
        name: str = f.get("name", "")
        nl = name.lower()
        fid = f.get("id")
        url = f.get("url_private_download") or f.get("url_private")

        if ".xlsx" in nl and ("dop" in nl or "day" in nl):
            result["dop"] = {"id": fid, "name": name, "url": url}

        elif ".pdf" in nl and (
            "route" in nl or "sheet" in nl or "nday" in nl
        ):
            result["route_sheet"] = {"id": fid, "name": name, "url": url}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Cortex channel scanning + ingest
# ─────────────────────────────────────────────────────────────────────────────

def _infer_file_date(filename: str) -> Optional[date]:
    """Extract date from filenames like Routes_DLV3_2026-06-30_16_00 (PDT).xlsx"""
    import re as _re
    name = os.path.basename(filename or "")
    patterns = [
        (r"(20\d{2}[-_]\d{2}[-_]\d{2})", ["%Y-%m-%d", "%Y_%m_%d"]),
        (r"(20\d{6})", ["%Y%m%d"]),
    ]
    for pattern, fmts in patterns:
        m = _re.search(pattern, name)
        if m:
            for fmt in fmts:
                try:
                    return datetime.strptime(m.group(1), fmt).date()
                except ValueError:
                    continue
    return None


def scan_cortex_channel(for_date: date) -> Optional[dict]:
    """
    Scan CORTEX_CHANNEL (#nday-operations-management) for today's Cortex xlsx.
    Filename pattern: Routes_DLV3_YYYY-MM-DD_*.xlsx
    Returns {id, name, url} or None if not found.
    """
    client = _slack_client()
    if not client:
        return None

    try:
        resp = client.files_list(channel=CORTEX_CHANNEL, count=20)
        files_list = resp.get("files", [])
    except Exception as exc:
        logger.warning("Cortex channel scan failed: %s", exc)
        return None

    for f in files_list:
        name: str = f.get("name", "")
        nl = name.lower()
        if ".xlsx" in nl and ("routes" in nl or "dlv3" in nl):
            file_date = _infer_file_date(name)
            if file_date == for_date:
                return {
                    "id": f.get("id"),
                    "name": name,
                    "url": f.get("url_private_download") or f.get("url_private"),
                }
    return None


def ingest_cortex_bytes(
    content: bytes, filename: str, for_date: date, db: Session
) -> Tuple[int, str]:
    """Parse Cortex xlsx bytes and upsert into cortex_routes for for_date."""
    try:
        import tempfile as _tmp
        from api.src.orchestrator import orchestrator

        with _tmp.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        orchestrator.ingest_cortex(tmp_path)
        os.unlink(tmp_path)

        # Append-only — same-day uploads can arrive under different
        # filenames (corrections/re-drops). Readers use
        # get_latest_cortex_rows() to pick the most recent row per
        # route_code for the date instead of relying on delete-on-ingest.
        for record in orchestrator.status.cortex_records:
            db.add(Cortex(
                assignment_date=for_date,
                station=None,
                dsp_code=record.dsp,
                route_code=record.route_code,
                wave=None,
                packages=None,
                commercial_pct=None,
                zone=None,
                service_type=record.delivery_service_type,
                driver_name=record.driver_name,
                source_file=filename,
            ))
        db.commit()
        return len(orchestrator.status.cortex_records), ""

    except Exception as exc:
        db.rollback()
        return 0, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# DOP xlsx ingest
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(v) -> str:
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in ("nan", "none", "n/a") else s


def ingest_dop_bytes(
    content: bytes, filename: str, for_date: date, db: Session
) -> Tuple[int, str]:
    """Parse DOP Excel/CSV bytes and upsert into dop_routes for for_date.

    Uses the same shared, strict parser (api.src.ingest.parse_dop_excel) as
    the manual-upload and Slack-dispatch ingestion paths, so all three agree
    on column detection and never write a row with an unvalidated duration.
    The tempfile keeps the real extension so .csv vs .xlsx detection works.
    """
    from api.src.ingest import parse_dop_excel

    ext = os.path.splitext(filename)[1].lower() or ".xlsx"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        records, errors = parse_dop_excel(tmp_path)

        if errors:
            logger.warning(
                "ingest_dop_bytes: %d parse issue(s) for %s: %s",
                len(errors), filename, "; ".join(errors[:5]),
            )

        if not records:
            return 0, "; ".join(errors) if errors else "No DOP rows parsed from file."

        # Append-only — see matching comment in ingest_cortex_bytes() above.
        for record in records:
            db.add(DOP(
                schedule_date=for_date,
                station=record.staging_location,
                dsp_code=record.dsp,
                route_code=record.route_code,
                wave=record.wave,
                planned_packages=record.num_packages,
                route_duration=record.route_duration,
                service_type=record.service_type,
                source_file=filename,
            ))
        db.commit()
        return len(records), ""

    except Exception as exc:
        db.rollback()
        logger.warning("ingest_dop_bytes: failed for %s: %s", filename, exc)
        return 0, str(exc)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Route Sheet PDF parsing
# ─────────────────────────────────────────────────────────────────────────────

# Amazon route codes follow this pattern: DLV3-XXXX-1A  or  D3-XXXX-1
_ROUTE_RE = re.compile(r"D[A-Z0-9]*-[A-Z]{2,6}-\d+[A-Z]?", re.IGNORECASE)


def parse_route_sheet_pdf(content: bytes) -> List[Dict]:
    """
    Extract driver-to-van assignments from the route sheet PDF.
    Returns a list of dicts: {route_code, driver_name, van_number, stage, wave_time}.
    Tries structured table extraction first, then plain text.
    """
    assignments: List[Dict] = []

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:

                # ── Structured table extraction ──────────────────────────
                tables = page.extract_tables() or []
                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    headers = [
                        (str(c).strip().lower() if c else "") for c in table[0]
                    ]
                    col: Dict[str, int] = {}
                    for i, h in enumerate(headers):
                        if "route" in h:
                            col.setdefault("route", i)
                        if "van" in h or "vehicle" in h or "unit" in h:
                            col.setdefault("van", i)
                        if "driver" in h or "associate" in h or " da" in h:
                            col.setdefault("driver", i)
                        if "stage" in h:
                            col.setdefault("stage", i)
                        if "wave" in h:
                            col.setdefault("wave", i)

                    for row in table[1:]:
                        if not row:
                            continue
                        entry: Dict[str, str] = {}
                        for key, idx in col.items():
                            if idx < len(row):
                                val = _safe_str(row[idx])
                                if val:
                                    entry[{
                                        "route": "route_code",
                                        "van": "van_number",
                                        "driver": "driver_name",
                                        "stage": "stage",
                                        "wave": "wave_time",
                                    }[key]] = val
                        if entry.get("route_code") or entry.get("driver_name"):
                            assignments.append(entry)

                # ── Text extraction fallback ──────────────────────────────
                if not assignments:
                    text = page.extract_text() or ""
                    for line in text.split("\n"):
                        m = _ROUTE_RE.search(line)
                        if not m:
                            continue
                        entry = {"route_code": m.group(0).upper()}
                        # Van number: V-101, V101, 1901, 2-digit prefix + 3 digits
                        van_m = re.search(r"\b([A-Z][-\s]?\d{3,4}|\d{4,5})\b", line)
                        if van_m:
                            entry["van_number"] = van_m.group(0)
                        assignments.append(entry)

    except Exception as exc:
        logger.warning("Route sheet PDF parse error: %s", exc)

    return assignments


# ─────────────────────────────────────────────────────────────────────────────
# Build combined daily assignments
# ─────────────────────────────────────────────────────────────────────────────

def build_daily_assignments(
    for_date: date, pdf_data: List[Dict], db: Session
) -> int:
    """
    Merge DOP rows + Cortex driver names + PDF van data into DailyRouteAssignment.

    Upserts by (assignment_date, route_code) rather than deleting and
    recreating the day's rows on every call. The scheduler reruns this every
    ~10 min from 8-10 AM; a delete+recreate meant every rerun reset dm_sent/
    ack_token back to their defaults, which would have caused every driver
    to be re-DMed on each pass once driver DMs go live. Two rules for the
    merge:
      - On an EXISTING row, only overwrite a computed field when the new
        value is non-empty — this also fixes a related bug where pdf_data
        comes back [] on a replay (the route sheet was already ingested,
        so it's not re-parsed) and used to blank out previously-known
        van_number/wave/stage. dm_sent/dm_sent_at/ack_token/acknowledged
        are never touched on an existing row, only set at creation.
      - A route_code that disappears from a corrected DOP is left alone
        rather than deleted — silently removing a row out from under an
        already-DMed/acknowledged driver is worse than a stale leftover
        row; that's a case for a human to look at, not to auto-delete.
    """
    pdf_by_route = {
        a["route_code"].upper(): a for a in pdf_data if a.get("route_code")
    }
    pdf_by_driver = {
        a["driver_name"].lower(): a for a in pdf_data if a.get("driver_name")
    }

    dop_rows = get_latest_dop_rows(db, for_date)

    # Cortex driver lookup: prefer today, fall back to most recent date
    cortex_rows = get_latest_cortex_rows(db, for_date)
    if not cortex_rows:
        latest = (
            db.query(func.max(Cortex.assignment_date))
            .scalar()
        )
        if latest:
            cortex_rows = get_latest_cortex_rows(db, latest)

    cortex_by_route = {
        (c.route_code or "").upper(): c.driver_name
        for c in cortex_rows
        if c.driver_name
    }

    existing_by_route = {
        (a.route_code or "").upper(): a
        for a in db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == for_date)
        .all()
        if a.route_code
    }

    count = 0
    for dop in dop_rows:
        rc = (dop.route_code or "").upper()

        driver = (
            cortex_by_route.get(rc)
            or dop.driver_name  # some DOP formats include driver
            or ""
        )

        pdf = pdf_by_route.get(rc) or pdf_by_driver.get(driver.lower(), {})
        driver_name = driver or pdf.get("driver_name", "")
        van_number = pdf.get("van_number") or ""
        stage_location = dop.station or pdf.get("stage") or ""
        wave = dop.wave or pdf.get("wave_time") or ""

        existing = existing_by_route.get(rc)
        if existing:
            existing.driver_name = driver_name or existing.driver_name
            existing.van_number = van_number or existing.van_number
            existing.stage_location = stage_location or existing.stage_location
            existing.wave = wave or existing.wave
            existing.packages = dop.planned_packages if dop.planned_packages is not None else existing.packages
            existing.route_duration = dop.route_duration if dop.route_duration is not None else existing.route_duration
            existing.service_type = dop.service_type or existing.service_type
        else:
            db.add(DailyRouteAssignment(
                assignment_date=for_date,
                route_code=dop.route_code,
                driver_name=driver_name,
                van_number=van_number,
                stage_location=stage_location,
                wave=wave,
                packages=dop.planned_packages,
                route_duration=dop.route_duration,
                service_type=dop.service_type,
                ack_token=str(uuid.uuid4()).replace("-", ""),
            ))
        count += 1

    try:
        db.commit()
    except Exception as exc:
        # A concurrent call (e.g. the automatic background loop racing a
        # manual re-ingest) can still race on the unique (assignment_date,
        # route_code) index added 2026-07-13. Roll back this batch — the
        # other call's rows are already committed and correct.
        db.rollback()
        logger.warning("build_daily_assignments: commit failed for %s, likely a concurrent rebuild — rolled back: %s", for_date, exc)
        return 0
    return count


# ─────────────────────────────────────────────────────────────────────────────
# DM sending
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_slack_id(driver_name: str, db: Session) -> Optional[str]:
    """
    Return the verified Slack user ID for a driver from driver_roster.
    Future: once DriverRosterEntry has an email column and the bot has
    users:read.email scope, add a lookupByEmail fallback here.
    """
    if not driver_name:
        return None
    entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == driver_name.lower()
    ).first()
    return (entry.slack_member_id or None) if entry and entry.slack_verified else None


def _first_name(payroll_name: str) -> str:
    """'Watson, Jayson' → 'Jayson'"""
    parts = payroll_name.split(",")
    if len(parts) == 2:
        return parts[1].strip().split()[0]
    return payroll_name.split()[0]


_TRACKED_FIELDS = ("driver_name", "van_number", "stage_location", "wave", "packages", "route_duration", "service_type")


def _tracked_fields(assignment: DailyRouteAssignment) -> dict:
    """The subset of an assignment's fields a driver actually needs to know
    about — what notified_snapshot stores, and what a rerun diffs against
    to decide whether the driver's last DM is now stale."""
    return {f: getattr(assignment, f) for f in _TRACKED_FIELDS}


def send_driver_dm(assignment: DailyRouteAssignment, db: Session) -> bool:
    """Send a Slack DM to the driver with their route details + confirmation link."""
    client = _slack_client()
    if not client:
        return False

    slack_id = _lookup_slack_id(assignment.driver_name, db)
    if not slack_id:
        return False

    first = _first_name(assignment.driver_name or "Driver")
    date_str = _fmt_date(assignment.assignment_date)
    confirm_url = f"{FRONTEND_URL}/confirm?token={assignment.ack_token}"

    show_time = _calc_show_time(assignment.wave or "")
    return_time = _calc_return_time(assignment.wave or "", assignment.route_duration)

    lines = [
        f"Good morning, {first}!",
        f"Here are your route details for *{date_str}*:",
        "",
    ]
    if assignment.route_code:
        lines.append(f"📍 *Route:* {assignment.route_code}")
    if assignment.van_number:
        lines.append(f"🚐 *Van:* {assignment.van_number}")
    if assignment.stage_location:
        lines.append(f"🏢 *Stage:* {assignment.stage_location}")
    if show_time:
        lines.append(f"⏰ *Show Time:* {show_time}")
    if assignment.wave:
        lines.append(f"🚦 *Wave:* {assignment.wave}")
    if return_time:
        lines.append(f"🏁 *Expected Return:* {return_time}")
    if assignment.packages:
        lines.append(f"📦 *Planned Packages:* {assignment.packages}")
    lines += [
        "",
        "For wave lead info, connect on *Zello*.",
        "",
        f"✅ *Confirm attendance:* <{confirm_url}|Tap here>",
    ]

    try:
        resp = client.chat_postMessage(
            channel=slack_id,
            text="\n".join(lines),
        )
        assignment.dm_sent = True
        assignment.dm_sent_at = datetime.utcnow()
        assignment.dm_message_ts = resp["ts"]
        assignment.dm_channel = resp["channel"]
        assignment.notified_snapshot = _tracked_fields(assignment)
        db.commit()
        return True
    except Exception as exc:
        logger.warning("DM send failed for %s: %s", assignment.driver_name, exc)
        return False


def send_driver_dm_update(assignment: DailyRouteAssignment, old_snapshot: dict, db: Session) -> bool:
    """Send a "your assignment changed" DM — used by rerun_route_assignments()
    for a driver who was already notified once, but one or more tracked
    fields have changed since (any change, not just route/van/wave — per
    explicit 2026-07-16 decision). Shows old -> new for whatever changed."""
    client = _slack_client()
    if not client:
        return False

    slack_id = _lookup_slack_id(assignment.driver_name, db)
    if not slack_id:
        return False

    first = _first_name(assignment.driver_name or "Driver")
    date_str = _fmt_date(assignment.assignment_date)
    new_snapshot = _tracked_fields(assignment)

    field_labels = {
        "driver_name": "Driver", "van_number": "Van", "stage_location": "Stage",
        "wave": "Wave", "packages": "Planned Packages", "route_duration": "Route Duration (min)",
        "service_type": "Service Type",
    }
    changed_lines = []
    for f in _TRACKED_FIELDS:
        old_v, new_v = old_snapshot.get(f), new_snapshot.get(f)
        if old_v != new_v:
            changed_lines.append(f"• *{field_labels.get(f, f)}:* {old_v or '—'} → {new_v or '—'}")

    if not changed_lines:
        return False  # nothing actually changed — shouldn't happen, caller already diffed

    show_time = _calc_show_time(assignment.wave or "")
    return_time = _calc_return_time(assignment.wave or "", assignment.route_duration)
    confirm_url = f"{FRONTEND_URL}/confirm?token={assignment.ack_token}"

    lines = [
        f"⚠️ Update, {first} — your route for *{date_str}* has changed:",
        "",
        *changed_lines,
        "",
        f"📍 *Route:* {assignment.route_code or '—'}",
    ]
    if show_time:
        lines.append(f"⏰ *Show Time:* {show_time}")
    if return_time:
        lines.append(f"🏁 *Expected Return:* {return_time}")
    lines += ["", f"✅ *Confirm attendance:* <{confirm_url}|Tap here>"]

    try:
        client.chat_postMessage(channel=slack_id, text="\n".join(lines))
        assignment.notified_snapshot = new_snapshot
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Update DM send failed for %s: %s", assignment.driver_name, exc)
        return False


def send_driver_removal_dm(assignment: DailyRouteAssignment, db: Session) -> bool:
    """Send a "you're no longer on today's route" DM — used by
    rerun_route_assignments() when a route_code the driver was already
    notified about disappears from a freshly re-ingested DOP."""
    client = _slack_client()
    if not client:
        return False

    slack_id = _lookup_slack_id(assignment.driver_name, db)
    if not slack_id:
        return False

    first = _first_name(assignment.driver_name or "Driver")
    date_str = _fmt_date(assignment.assignment_date)
    lines = [
        f"🔄 {first}, you've been removed from route *{assignment.route_code or '?'}* for *{date_str}*.",
        "",
        "You're no longer assigned to that route — please check with dispatch for your updated assignment, if any.",
    ]

    try:
        client.chat_postMessage(channel=slack_id, text="\n".join(lines))
        assignment.assignment_status = "removed"
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Removal DM send failed for %s: %s", assignment.driver_name, exc)
        return False


def send_all_dms(for_date: date, db: Session) -> Dict:
    """Send DMs to all drivers with unsent assignments for for_date.
    Gated by DRIVER_DM_ACTIVE (default false)."""
    if not _DM_ACTIVE:
        return {"status": "inactive", "sent": 0, "skipped": 0, "total": 0}

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == for_date,
            DailyRouteAssignment.dm_sent == False,
            DailyRouteAssignment.driver_name != "",
        )
        .all()
    )

    sent = skipped = 0
    for a in assignments:
        ok = send_driver_dm(a, db)
        if ok:
            sent += 1
        else:
            skipped += 1

    return {"sent": sent, "skipped": skipped, "total": len(assignments)}


# ─────────────────────────────────────────────────────────────────────────────
# Ops manager prompt — fires after DOP is detected each morning
# ─────────────────────────────────────────────────────────────────────────────

def _ops_manager_prompt(for_date: date, route_count: int, db: Session) -> int:
    """
    DM Spencer, Fabian, and Luis once DOP is ingested for the day.
    Idempotent — uses a synthetic SlackIngestLog entry so it only fires once.
    Returns the number of DMs successfully sent (0 if already prompted today).
    """
    fake_id = f"ops_prompt_{for_date.isoformat()}"
    if db.query(SlackIngestLog).filter(SlackIngestLog.slack_file_id == fake_id).first():
        return 0

    client = _slack_client()
    if not client:
        return 0

    date_str = _fmt_date(for_date)
    message = (
        f"*DOP is in for {date_str}!*\n\n"
        f"Amazon has loaded *{route_count} routes* for today.\n\n"
        "Once you've finished rostering in Cortex, drop the Routes xlsx into "
        "*#nday-operations-management* so the system can send each driver their assignment.\n\n"
        "File format: `Routes_DLV3_YYYY-MM-DD_HH_MM (PDT).xlsx`"
    )

    sent = 0
    for name, uid in OPS_MANAGER_IDS:
        try:
            client.chat_postMessage(channel=uid, text=message)
            sent += 1
        except Exception as exc:
            logger.warning("Ops-prompt DM failed for %s (%s): %s", name, uid, exc)

    db.add(SlackIngestLog(
        ingest_date=for_date,
        file_type="ops_prompt",
        slack_file_id=fake_id,
        filename=f"ops_prompt_{for_date.isoformat()}",
        processed_at=datetime.utcnow(),
        status="success",
        records_processed=sent,
    ))
    db.commit()
    return sent


def _fleet_manager_prompt(for_date: date, db: Session) -> int:
    """
    DM Spencer, Fabian, and Luis once DOP is ingested, reminding them to also
    drop the daily fleet/vehicle data file (VIN, service type, operational
    status — used to build available vans for assignment) into
    *#nday-operations-management*.
    Idempotent — uses a synthetic SlackIngestLog entry so it only fires once.
    Returns the number of DMs successfully sent (0 if already prompted today).
    """
    fake_id = f"fleet_prompt_{for_date.isoformat()}"
    if db.query(SlackIngestLog).filter(SlackIngestLog.slack_file_id == fake_id).first():
        return 0

    client = _slack_client()
    if not client:
        return 0

    date_str = _fmt_date(for_date)
    message = (
        f"*Don't forget the daily fleet file for {date_str}!*\n\n"
        "Drop today's vehicle data (VIN / service type / operational status) into "
        "*#nday-operations-management* so grounded vans get excluded and the "
        "assignment engine has accurate availability."
    )

    sent = 0
    for name, uid in OPS_MANAGER_IDS:
        try:
            client.chat_postMessage(channel=uid, text=message)
            sent += 1
        except Exception as exc:
            logger.warning("Fleet-prompt DM failed for %s (%s): %s", name, uid, exc)

    db.add(SlackIngestLog(
        ingest_date=for_date,
        file_type="fleet_prompt",
        slack_file_id=fake_id,
        filename=f"fleet_prompt_{for_date.isoformat()}",
        processed_at=datetime.utcnow(),
        status="success",
        records_processed=sent,
    ))
    db.commit()
    return sent


# ─────────────────────────────────────────────────────────────────────────────
# Sweeper notifications — fires after Cortex assignments are built
# ─────────────────────────────────────────────────────────────────────────────

def send_sweeper_notifications(for_date: date, db: Session) -> Dict:
    """
    Find active roster drivers NOT assigned a route today and DM them as sweepers.
    Only runs once per day — idempotent via SlackIngestLog synthetic entry.
    Gated by DRIVER_DM_ACTIVE (default false).
    """
    if not _DM_ACTIVE:
        return {"status": "inactive", "sent": 0}

    fake_id = f"sweeper_notify_{for_date.isoformat()}"
    if db.query(SlackIngestLog).filter(SlackIngestLog.slack_file_id == fake_id).first():
        return {"status": "already_sent"}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_client", "sent": 0}

    # Active drivers who have a verified Slack ID
    roster = (
        db.query(DriverRosterEntry)
        .filter(
            DriverRosterEntry.is_active == True,
            DriverRosterEntry.slack_member_id.isnot(None),
            DriverRosterEntry.slack_verified == True,
        )
        .all()
    )

    # Names already assigned a route today (normalise to lower)
    assigned_lower = {
        (a.driver_name or "").lower()
        for a in db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == for_date)
        .all()
    }

    date_str = _fmt_date(for_date)
    sweeper_msg = (
        f"Good morning! For *{date_str}* you are on the *Sweeper List*.\n\n"
        "Please arrive at the station at your show time and stand by for sweep assignments from dispatch.\n\n"
        "Connect with your wave lead on *Zello* when you arrive."
    )

    sent = failed = 0
    for driver in roster:
        if (driver.payroll_name or "").lower() in assigned_lower:
            continue  # has a route — skip
        try:
            client.chat_postMessage(channel=driver.slack_member_id, text=sweeper_msg)
            sent += 1
        except Exception as exc:
            logger.warning("Sweeper DM failed for %s: %s", driver.payroll_name, exc)
            failed += 1

    db.add(SlackIngestLog(
        ingest_date=for_date,
        file_type="sweeper_notify",
        slack_file_id=fake_id,
        filename=f"sweeper_notify_{for_date.isoformat()}",
        processed_at=datetime.utcnow(),
        status="success",
        records_processed=sent,
    ))
    db.commit()
    return {"status": "sent", "sent": sent, "failed": failed, "total_sweepers": sent + failed}


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def check_and_notify(
    db: Session,
    for_date: Optional[date] = None,
    cortex_file: Optional[dict] = None,
) -> Dict:
    """
    Full morning pipeline:
      1. Cortex scan → ingest (driver names first)
      2. DOP scan → ingest → ops-manager DMs (Spencer/Fabian/Luis) + fleet-file reminder DM
      3. Route Sheet scan → ingest
      4. Build daily_route_assignments (DOP + Cortex + PDF)
      5. Send per-driver DMs (route, van, stage, show time, expected return)
      6. Send sweeper DMs to active roster not assigned a route

    Safe to call repeatedly — SlackIngestLog prevents double-processing files.
    Pass cortex_file={id, name, url} to skip the internal channel scan (useful when
    the caller already found the correct file among multiple candidates).
    """
    for_date = for_date or datetime.now(PACIFIC).date()
    result: Dict = {
        "date": for_date.isoformat(),
        "files_found": {},
        "already_ingested": {},
        "ingest": {},
        "assignments": 0,
        "dms": {},
        "ops_prompt_sent": 0,
        "fleet_prompt_sent": 0,
        "sweepers": {},
    }

    # ── 1. Ingest Cortex first (driver names needed before building assignments) ─
    if cortex_file is None:
        cortex_file = scan_cortex_channel(for_date)
    result["files_found"]["cortex"] = cortex_file["name"] if cortex_file else None

    if cortex_file:
        already = db.query(SlackIngestLog).filter(
            SlackIngestLog.slack_file_id == cortex_file["id"]
        ).first()
        if already:
            result["already_ingested"]["cortex"] = already.filename
        else:
            content = _download_slack_file(cortex_file["url"])
            if content:
                count, err = ingest_cortex_bytes(
                    content, cortex_file["name"], for_date, db
                )
                db.add(SlackIngestLog(
                    ingest_date=for_date,
                    file_type="cortex",
                    slack_file_id=cortex_file["id"],
                    filename=cortex_file["name"],
                    processed_at=datetime.utcnow(),
                    status="success" if not err else "failed",
                    error=err or None,
                    records_processed=count,
                ))
                db.commit()
                result["ingest"]["cortex"] = {"records": count, "error": err}

    files = scan_channel_for_files(for_date)
    result["files_found"]["dop"] = files["dop"]["name"] if files["dop"] else None
    result["files_found"]["route_sheet"] = (
        files["route_sheet"]["name"] if files["route_sheet"] else None
    )

    pdf_data: List[Dict] = []

    # ── 2. Ingest DOP → prompt ops managers ──────────────────────────────────
    dop_file = files.get("dop")
    if dop_file:
        already = db.query(SlackIngestLog).filter(
            SlackIngestLog.slack_file_id == dop_file["id"]
        ).first()
        if already:
            result["already_ingested"]["dop"] = already.filename
        else:
            content = _download_slack_file(dop_file["url"])
            if content:
                count, err = ingest_dop_bytes(content, dop_file["name"], for_date, db)
                db.add(SlackIngestLog(
                    ingest_date=for_date,
                    file_type="dop",
                    slack_file_id=dop_file["id"],
                    filename=dop_file["name"],
                    processed_at=datetime.utcnow(),
                    status="success" if not err else "failed",
                    error=err or None,
                    records_processed=count,
                ))
                db.commit()
                result["ingest"]["dop"] = {"records": count, "error": err}
                if not err and count:
                    result["ops_prompt_sent"] = _ops_manager_prompt(for_date, count, db)
                    result["fleet_prompt_sent"] = _fleet_manager_prompt(for_date, db)

    # ── 3. Ingest Route Sheet ─────────────────────────────────────────────────
    rs_file = files.get("route_sheet")
    if rs_file:
        already = db.query(SlackIngestLog).filter(
            SlackIngestLog.slack_file_id == rs_file["id"]
        ).first()
        if already:
            result["already_ingested"]["route_sheet"] = already.filename
        else:
            content = _download_slack_file(rs_file["url"])
            if content:
                pdf_data = parse_route_sheet_pdf(content)
                db.add(SlackIngestLog(
                    ingest_date=for_date,
                    file_type="route_sheet",
                    slack_file_id=rs_file["id"],
                    filename=rs_file["name"],
                    processed_at=datetime.utcnow(),
                    status="success",
                    records_processed=len(pdf_data),
                ))
                db.commit()
                result["ingest"]["route_sheet"] = {"records": len(pdf_data)}

    # ── 4. Build assignments (DOP + Cortex + PDF) ─────────────────────────────
    has_dop = db.query(DOP).filter(DOP.schedule_date == for_date).count() > 0
    if has_dop:
        count = build_daily_assignments(for_date, pdf_data, db)
        result["assignments"] = count

    # ── 5. Send per-driver DMs ────────────────────────────────────────────────
    has_assignments = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == for_date)
        .count()
    )
    if has_assignments:
        result["dms"] = send_all_dms(for_date, db)

    # ── 6. Send sweeper DMs (only after Cortex is present so the list is accurate)
    has_cortex = db.query(Cortex).filter(Cortex.assignment_date == for_date).count() > 0
    if has_cortex and has_assignments:
        result["sweepers"] = send_sweeper_notifications(for_date, db)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Manual re-run — "Re-Run Route Assignments" Dispatch Home button. Unlike
# check_and_notify() (which only ever asks "has this driver been notified
# yet?" and is meant to run unattended every 10 min all morning), this is
# meant to be clicked by a human any time after the initial run to pick up
# corrections: new assignments get the normal DM, drivers whose tracked
# fields changed since their last DM get an update DM, and drivers dropped
# from the route entirely get a removal DM. Refreshes the #nday-mgt
# assignment matrix too. Added 2026-07-16.
# ─────────────────────────────────────────────────────────────────────────────

def rerun_route_assignments(for_date: date, db: Session) -> Dict:
    before_notified_codes = {
        a.route_code
        for a in db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == for_date,
            DailyRouteAssignment.route_code.isnot(None),
            DailyRouteAssignment.notified_snapshot.isnot(None),
        )
        .all()
    }

    # Re-scan/ingest any newly corrected files, rebuild (upsert), and send
    # DMs for brand-new assignments — all of check_and_notify()'s existing,
    # already-tested logic.
    check_result = check_and_notify(db, for_date=for_date)

    current_dop_route_codes = {
        (d.route_code or "").upper() for d in get_latest_dop_rows(db, for_date)
    }

    changed_sent = changed_skipped = 0
    current_rows = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == for_date)
        .all()
    )
    for a in current_rows:
        if not a.driver_name or not a.notified_snapshot:
            continue  # never notified yet -> not a "change", handled as "new" above
        if _tracked_fields(a) == a.notified_snapshot:
            continue  # nothing actually changed
        if _DM_ACTIVE:
            if send_driver_dm_update(a, a.notified_snapshot, db):
                changed_sent += 1
            else:
                changed_skipped += 1

    removed_sent = removed_skipped = 0
    removed_codes = before_notified_codes - current_dop_route_codes
    if removed_codes:
        removed_rows = (
            db.query(DailyRouteAssignment)
            .filter(
                DailyRouteAssignment.assignment_date == for_date,
                DailyRouteAssignment.route_code.in_(removed_codes),
                DailyRouteAssignment.assignment_status != "removed",
            )
            .all()
        )
        for a in removed_rows:
            if _DM_ACTIVE:
                if send_driver_removal_dm(a, db):
                    removed_sent += 1
                else:
                    removed_skipped += 1

    summary_result = {"status": "skipped_no_active_flag"}
    try:
        from api.src.routes.rostering import post_assignment_matrix
        summary_result = post_assignment_matrix(for_date, db, force=True)
    except Exception as exc:
        logger.warning("Rerun: summary refresh failed: %s", exc)
        summary_result = {"status": "error", "detail": str(exc)}

    return {
        "status": "ok",
        "date": for_date.isoformat(),
        "dm_active": _DM_ACTIVE,
        "initial_check": check_result,
        "changed_dms": {"sent": changed_sent, "skipped": changed_skipped},
        "removed_dms": {"sent": removed_sent, "skipped": removed_skipped},
        "summary": summary_result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ECP watch — nightly roster prompt
# ─────────────────────────────────────────────────────────────────────────────

# Keywords that must both appear in the message (case-insensitive)
_ECP_KEYWORDS = ("ecp",)
_ROSTER_KEYWORDS = ("roster",)


def scan_for_ecp_message() -> Optional[Dict]:
    """
    Scan the last 100 messages in #dlv3-nday-info for the nightly ECP message.
    Requires groups:history bot scope (private channel).
    Returns {"ts": ..., "text": ...} if found, None otherwise.
    """
    client = _slack_client()
    if not client:
        return None

    try:
        resp = client.conversations_history(channel=NOTIFY_CHANNEL, limit=100)
        messages = resp.get("messages", [])
    except Exception as exc:
        logger.warning("ECP scan failed: %s", exc)
        return None

    for msg in messages:
        text = (msg.get("text") or "").lower()
        if any(k in text for k in _ECP_KEYWORDS) and any(k in text for k in _ROSTER_KEYWORDS):
            return {"ts": msg.get("ts"), "text": msg.get("text", "")}

    return None


def send_roster_prompt(ecp_ts: str, ecp_text: str, prompt_date: date, db: Session) -> bool:
    """Post a prompt to #nday-operations-management to upload the Cortex schedule."""
    client = _slack_client()
    if not client:
        return False

    message = (
        "📋 *ECP has run — rostering is open!*\n\n"
        "Once you've completed rostering in Cortex, drop the Routes xlsx into this channel "
        "so the system can send drivers their daily route assignments.\n\n"
        "File format: `Routes_DLV3_YYYY-MM-DD_HH_MM (PDT).xlsx`"
    )

    try:
        resp = client.chat_postMessage(channel=CORTEX_CHANNEL, text=message)
        db.add(EcpRosterPrompt(
            prompt_date=prompt_date,
            ecp_message_ts=ecp_ts,
            ecp_message_text=ecp_text[:500],
            prompted_at=datetime.utcnow(),
            prompt_message_ts=resp["ts"],
        ))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Roster prompt send failed: %s", exc)
        return False


def check_ecp_and_prompt(db: Session, for_date: Optional[date] = None) -> Dict:
    """
    Check #dlv3-nday-info for the ECP message and prompt #nday-operations-management
    to upload the Cortex schedule. Safe to call repeatedly — deduped by date.
    """
    for_date = for_date or datetime.now(PACIFIC).date()

    existing = db.query(EcpRosterPrompt).filter(
        EcpRosterPrompt.prompt_date == for_date
    ).first()
    if existing:
        return {
            "status": "already_prompted",
            "date": for_date.isoformat(),
            "prompted_at": existing.prompted_at.isoformat(),
        }

    msg = scan_for_ecp_message()
    if not msg:
        return {"status": "no_ecp_message", "date": for_date.isoformat()}

    ok = send_roster_prompt(msg["ts"], msg["text"], for_date, db)
    return {
        "status": "prompted" if ok else "failed",
        "date": for_date.isoformat(),
        "ecp_message_preview": msg["text"][:200],
    }


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/daily-notify/join-channel")
def join_channel():
    """
    Invite the bot into SLACK_NOTIFY_CHANNEL using a user OAuth token (xoxp-).
    Required for private channels — run once to make the bot a permanent member.
    Requires SLACK_USER_TOKEN (User OAuth Token with groups:write scope) in env.
    """
    user_token = os.getenv("SLACK_USER_TOKEN")
    if not user_token:
        raise HTTPException(
            status_code=400,
            detail="SLACK_USER_TOKEN not set. Add a User OAuth Token (xoxp-...) with groups:write scope.",
        )

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=400, detail="SLACK_BOT_TOKEN not set.")

    try:
        from slack_sdk import WebClient

        # Get the bot's user ID using the bot token
        bot_client = WebClient(token=bot_token)
        bot_info = bot_client.auth_test()
        bot_user_id = bot_info["user_id"]

        # Use the user token to invite the bot to the private channel
        user_client = WebClient(token=user_token)
        resp = user_client.conversations_invite(
            channel=NOTIFY_CHANNEL,
            users=bot_user_id,
        )
        name = resp.get("channel", {}).get("name", NOTIFY_CHANNEL)
        return {
            "joined": True,
            "channel": name,
            "bot_user_id": bot_user_id,
            "message": f"Bot ({bot_user_id}) successfully added to #{name}",
        }
    except Exception as exc:
        error = str(exc)
        if "already_in_channel" in error:
            return {"joined": True, "message": "Bot is already in the channel."}
        raise HTTPException(status_code=400, detail=error)


@router.post("/daily-notify/join-cortex-channel")
def join_cortex_channel():
    """
    Invite the bot into CORTEX_NOTIFY_CHANNEL (#nday-operations-management).
    Uses the user OAuth token (xoxp-) with groups:write scope.
    Run once after creating the channel.
    """
    user_token = os.getenv("SLACK_USER_TOKEN")
    if not user_token:
        raise HTTPException(
            status_code=400,
            detail="SLACK_USER_TOKEN not set.",
        )
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=400, detail="SLACK_BOT_TOKEN not set.")

    try:
        from slack_sdk import WebClient

        bot_client = WebClient(token=bot_token)
        bot_user_id = bot_client.auth_test()["user_id"]

        user_client = WebClient(token=user_token)
        resp = user_client.conversations_invite(
            channel=CORTEX_CHANNEL,
            users=bot_user_id,
        )
        name = resp.get("channel", {}).get("name", CORTEX_CHANNEL)
        return {
            "joined": True,
            "channel": name,
            "bot_user_id": bot_user_id,
            "message": f"Bot ({bot_user_id}) added to #{name}",
        }
    except Exception as exc:
        error = str(exc)
        if "already_in_channel" in error:
            return {"joined": True, "message": "Bot is already in the channel."}
        raise HTTPException(status_code=400, detail=error)


@router.get("/daily-notify/status")
def notify_status(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Today's ingest status, assignment roster, and roll call."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == for_date)
        .order_by(DailyRouteAssignment.wave, DailyRouteAssignment.route_code)
        .all()
    )
    logs = (
        db.query(SlackIngestLog)
        .filter(SlackIngestLog.ingest_date == for_date)
        .all()
    )

    return {
        "date": for_date.isoformat(),
        "ingest_logs": [
            {
                "file_type": l.file_type,
                "filename": l.filename,
                "status": l.status,
                "records": l.records_processed,
                "processed_at": l.processed_at.isoformat() if l.processed_at else None,
                "error": l.error,
            }
            for l in logs
        ],
        "summary": {
            "total": len(assignments),
            "dms_sent": sum(1 for a in assignments if a.dm_sent),
            "acknowledged": sum(1 for a in assignments if a.acknowledged),
            "no_slack": sum(
                1 for a in assignments if not a.dm_sent and a.driver_name
            ),
        },
        "assignments": [
            {
                "id": a.id,
                "route_code": a.route_code,
                "driver_name": a.driver_name,
                "van_number": a.van_number,
                "stage_location": a.stage_location,
                "wave": a.wave,
                "show_time": _calc_show_time(a.wave or ""),
                "expected_return": _calc_return_time(a.wave or "", a.route_duration),
                "packages": a.packages,
                "route_duration": a.route_duration,
                "dm_sent": a.dm_sent,
                "dm_sent_at": a.dm_sent_at.isoformat() if a.dm_sent_at else None,
                "acknowledged": a.acknowledged,
                "acknowledged_at": (
                    a.acknowledged_at.isoformat() if a.acknowledged_at else None
                ),
            }
            for a in assignments
        ],
    }


@router.post("/daily-notify/check")
def trigger_check(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Manually trigger the full pipeline: scan channel → ingest → build → send DMs."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    result = check_and_notify(db, for_date=for_date)
    return result


@router.post("/daily-notify/rerun")
def trigger_rerun(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Re-Run Route Assignments — pick up corrections since the initial
    morning run: new assignments get the normal DM, changed ones get an
    update DM, removed ones get a removal DM, and the #nday-mgt matrix is
    refreshed. Driver-DM sub-steps still respect DRIVER_DM_ACTIVE."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    return rerun_route_assignments(for_date, db)


@router.post("/daily-notify/send-dms")
def trigger_send_dms(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Re-send DMs for any driver who hasn't been notified yet."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    result = send_all_dms(for_date, db)
    return result


@router.post("/daily-notify/resend-dm/{assignment_id}")
def resend_single_dm(assignment_id: int, db: Session = Depends(get_db)):
    """Resend DM to a single driver (e.g., if they report not receiving it)."""
    a = db.query(DailyRouteAssignment).filter(
        DailyRouteAssignment.id == assignment_id
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    # Reset dm_sent so send_driver_dm will re-send
    a.dm_sent = False
    db.commit()

    ok = send_driver_dm(a, db)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Could not send DM — driver may not have a verified Slack ID.",
        )
    return {"sent": True, "driver": a.driver_name}


@router.post("/daily-notify/send-sweepers")
def trigger_sweepers(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Manually send sweeper DMs to active roster drivers not assigned a route today."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    return send_sweeper_notifications(for_date, db)


@router.post("/daily-notify/prompt-ops")
def trigger_ops_prompt(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Manually send the Cortex upload DM to Spencer, Fabian, and Luis."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    route_count = len(get_latest_dop_rows(db, for_date))
    sent = _ops_manager_prompt(for_date, route_count, db)
    return {"date": for_date.isoformat(), "route_count": route_count, "dms_sent": sent}


@router.post("/daily-notify/check-ecp")
def trigger_ecp_check(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Manually trigger the ECP watch — scans for the nightly ECP message and prompts roster upload."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    return check_ecp_and_prompt(db, for_date=for_date)


@router.get("/daily-notify/ecp-status")
def ecp_status(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Return today's ECP prompt status."""
    try:
        for_date = (
            datetime.strptime(date, "%Y-%m-%d").date()
            if date
            else datetime.now(PACIFIC).date()
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")

    prompt = db.query(EcpRosterPrompt).filter(
        EcpRosterPrompt.prompt_date == for_date
    ).first()

    return {
        "date": for_date.isoformat(),
        "prompted": prompt is not None,
        "prompted_at": prompt.prompted_at.isoformat() if prompt else None,
        "ecp_message_preview": (prompt.ecp_message_text or "")[:200] if prompt else None,
    }


@router.get("/daily-notify/today-status")
def today_confirmation_status(for_date: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Returns all driver assignments for a given date with confirmation status.
    Used by the dispatch confirmation dashboard. Defaults to today Pacific time.
    """
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
    if for_date:
        try:
            target = date.fromisoformat(for_date)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    else:
        target = datetime.now(PACIFIC).date()

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target)
        .order_by(DailyRouteAssignment.wave, DailyRouteAssignment.driver_name)
        .all()
    )

    rows = []
    for a in assignments:
        rows.append({
            "id": a.id,
            "driver_name": a.driver_name,
            "route_code": a.route_code,
            "van_number": a.van_number,
            "stage_location": a.stage_location,
            "wave": a.wave,
            "packages": a.packages,
            "dm_sent": a.dm_sent,
            "dm_sent_at": a.dm_sent_at.isoformat() if a.dm_sent_at else None,
            "acknowledged": a.acknowledged,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        })

    confirmed = sum(1 for r in rows if r["acknowledged"])
    dm_sent = sum(1 for r in rows if r["dm_sent"])

    return {
        "date": target.isoformat(),
        "total": len(rows),
        "dm_sent": dm_sent,
        "confirmed": confirmed,
        "pending": len(rows) - confirmed,
        "assignments": rows,
    }


@router.get("/daily-notify/confirm")
def confirm_attendance(token: str, db: Session = Depends(get_db)):
    """
    Driver taps the link in their DM → frontend page calls this to mark attendance.
    Returns route details on success (frontend renders a confirmation card).
    """
    a = db.query(DailyRouteAssignment).filter(
        DailyRouteAssignment.ack_token == token
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Invalid or expired link.")

    already = a.acknowledged
    if not already:
        a.acknowledged = True
        a.acknowledged_at = datetime.utcnow()
        db.commit()

    first = _first_name(a.driver_name or "Driver")
    return {
        "already_confirmed": already,
        "driver_name": a.driver_name,
        "first_name": first,
        "route_code": a.route_code,
        "stage_location": a.stage_location,
        "van_number": a.van_number,
        "wave": a.wave,
        "show_time": _calc_show_time(a.wave or ""),
        "expected_return": _calc_return_time(a.wave or "", a.route_duration),
        "packages": a.packages,
        "date": a.assignment_date.isoformat(),
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
    }
