"""
Slack Interactions — platform adapter layer.

Receives button clicks from Slack channels via the Interactivity & Shortcuts
Request URL. Translates Slack user identity into a platform-agnostic signed
callout token and sends it back as an ephemeral message only the clicking
driver can see.

Migration path: if Slack is replaced, write a new adapter (e.g. teams_interactions.py)
that calls _issue_callout_token() with the same signature. No callout business
logic lives here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.src.database import get_db, DriverRosterEntry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/slack", tags=["slack"])

PACIFIC = ZoneInfo("America/Los_Angeles")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
TOKEN_TTL_HOURS = 4


# ─────────────────────────────────────────────────────────────────────────────
# Platform-agnostic token issuer
# ─────────────────────────────────────────────────────────────────────────────

def _issue_callout_token(driver_name: str, shift_date: str) -> str:
    """Generate a short-lived signed JWT for callout page access.
    Called by any platform adapter — Slack, Teams, email, etc."""
    import jwt
    secret = os.getenv("JWT_SECRET", "dev-secret")
    payload = {
        "driver_name": driver_name,
        "shift_date": shift_date,
        "purpose": "callout",
        "exp": int(time.time()) + TOKEN_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ─────────────────────────────────────────────────────────────────────────────
# Slack signature verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        logger.warning("SLACK_SIGNING_SECRET not set — skipping verification (set it on Render)")
        return True
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            signing_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Driver lookup
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_driver(slack_user_id: str, db: Session) -> Optional[DriverRosterEntry]:
    """Find roster entry by slack_member_id. On first match via Slack profile,
    saves the ID so future lookups are instant."""
    entry = db.query(DriverRosterEntry).filter(
        DriverRosterEntry.slack_member_id == slack_user_id,
        DriverRosterEntry.is_active == True,
    ).first()
    if entry:
        return entry

    # Fallback: fetch real name from Slack and match against payroll_name
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            return None
        client = WebClient(token=token)
        info = client.users_info(user=slack_user_id)
        user_info = info["user"]
        real_name = user_info.get("real_name") or user_info.get("profile", {}).get("real_name", "")
        display_name = user_info.get("profile", {}).get("display_name", "")

        for candidate in [real_name, display_name]:
            if not candidate:
                continue
            parts = candidate.strip().split()
            if len(parts) >= 2:
                # Try "Last, First" format match
                last, first = parts[-1], parts[0]
                entry = db.query(DriverRosterEntry).filter(
                    DriverRosterEntry.payroll_name.ilike(f"{last}%{first}%"),
                    DriverRosterEntry.is_active == True,
                ).first()
                if entry:
                    entry.slack_member_id = slack_user_id
                    entry.slack_display_name = real_name
                    entry.slack_verified = True
                    entry.slack_verified_at = datetime.utcnow()
                    db.commit()
                    logger.info("Auto-linked Slack user %s → %s", slack_user_id, entry.payroll_name)
                    return entry
    except Exception as exc:
        logger.warning("Slack user lookup failed for %s: %s", slack_user_id, exc)

    return None


def _default_shift_date() -> str:
    """Today's date; rolls to tomorrow after 6 PM Pacific (next-day shift prep)."""
    now = datetime.now(PACIFIC)
    d = now.date()
    if now.hour >= 18:
        d = d + timedelta(days=1)
    return d.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_ephemeral(channel_id: str, user_id: str, text: str) -> None:
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            return
        client = WebClient(token=token)
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )
    except Exception as exc:
        logger.warning("Ephemeral send failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Action handlers
# ─────────────────────────────────────────────────────────────────────────────

def _handle_callout_button(payload: dict, db: Session) -> None:
    channel_id = payload.get("channel", {}).get("id", "")
    user_id = payload.get("user", {}).get("id", "")

    driver = _resolve_driver(user_id, db)

    if not driver:
        _send_ephemeral(
            channel_id, user_id,
            "⚠️ *Your Slack account isn't linked to a driver roster entry.*\n"
            "Contact your dispatcher to get set up, then try again.",
        )
        return

    shift_date = _default_shift_date()
    token = _issue_callout_token(driver.payroll_name, shift_date)
    url = f"{FRONTEND_URL}/callout?token={token}"

    _send_ephemeral(
        channel_id, user_id,
        f"Your personal absence report link — *only you can see this message.*\n\n"
        f"<{url}|👆 Tap here to report your absence>\n\n"
        f"_Expires in {TOKEN_TTL_HOURS} hours. Do not share this link._",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interactions endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/interactions")
async def slack_interactions(request: Request, db: Session = Depends(get_db)):
    """Receives all Slack interactive component payloads."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        raise HTTPException(403, "Invalid Slack signature.")

    form = await request.form()
    raw = form.get("payload", "")
    if not raw:
        return {"ok": True}

    payload = json.loads(raw)
    action_id = (payload.get("actions") or [{}])[0].get("action_id", "")

    if action_id == "callout_button":
        _handle_callout_button(payload, db)

    # Slack requires a 200 response within 3 seconds
    return {"ok": True}
