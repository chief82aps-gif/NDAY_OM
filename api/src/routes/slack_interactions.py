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
# Setup — post the Call Out button to the driver channel
# ─────────────────────────────────────────────────────────────────────────────

DRIVER_CHANNEL = "C0BAQAYKANS"  # #nday-team-room

@router.post("/post-callout-button")
def post_callout_button():
    """One-time setup: posts the interactive Call Out button to #nday-team-room.
    Call this once after deploy to replace any existing plain-link button."""
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise HTTPException(500, "SLACK_BOT_TOKEN not set.")
        client = WebClient(token=token)
        client.chat_postMessage(
            channel=DRIVER_CHANNEL,
            text="Need to report an absence? Use the button below.",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🚨 Report an Absence", "emoji": True},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "If you are unable to make your shift, tap the button below.\n"
                                "A personal link will be sent *only to you* — it expires in 4 hours.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "callout_button",
                            "text": {"type": "plain_text", "text": "📋  Call Out", "emoji": True},
                            "style": "danger",
                        }
                    ],
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Only you will see your personal link. Do not share it."}
                    ],
                },
            ],
        )
        return {"status": "posted", "channel": DRIVER_CHANNEL}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/post-rts-button")
def post_rts_button():
    """One-time setup: posts the Return to Station button to #nday-team-room."""
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise HTTPException(500, "SLACK_BOT_TOKEN not set.")
        client = WebClient(token=token)
        client.chat_postMessage(
            channel=DRIVER_CHANNEL,
            text="Heading back to the station? Use the button below.",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🔄 Return to Station", "emoji": True},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Wrapping up your route? Tap the button below for a quick "
                                "(~3 min) debrief on any packages coming back before you head in.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "rts_button",
                            "text": {"type": "plain_text", "text": "🔄  Return to Station", "emoji": True},
                            "style": "primary",
                        }
                    ],
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "If you already have a rescue assignment, this will take you straight to it."}
                    ],
                },
            ],
        )
        return {"status": "posted", "channel": DRIVER_CHANNEL}
    except Exception as exc:
        raise HTTPException(500, str(exc))


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

    elif action_id == "acknowledge_callout_alert":
        _handle_acknowledge_callout(payload, db)

    elif action_id == "driver_arrived_shift":
        _handle_driver_arrived(payload, db)

    elif action_id == "driver_schedule_ack":
        _handle_schedule_ack(payload, db)

    elif action_id == "driver_eod_complete":
        _handle_eod_complete(payload, db)

    elif action_id == "rts_button":
        _handle_rts_button(payload, db)

    # Slack requires a 200 response within 3 seconds
    return {"ok": True}


def _handle_rts_button(payload: dict, db: Session) -> None:
    """Driver tapped 'Return to Station'. Routes to an existing rescue assignment
    if dispatch already opened one for them; otherwise sends a personal debrief link."""
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

    try:
        from api.src.routes.rts import start_rts
        result = start_rts(driver.payroll_name, user_id, db)
    except Exception as exc:
        logger.warning("start_rts failed for %s: %s", driver.payroll_name, exc)
        _send_ephemeral(channel_id, user_id, "⚠️ Something went wrong starting your RTS. Contact dispatch.")
        return

    if result["routed_to_rescue"]:
        _send_ephemeral(
            channel_id, user_id,
            f"🚨 *You're assigned a rescue* — assist *{result['rescued_driver_name']}*.\n\n"
            f"<{result['contribute_url']}|👆 Tap here to log your pickup>",
        )
    else:
        _send_ephemeral(
            channel_id, user_id,
            f"🔄 *Return to Station* — quick debrief before you head in (~3 min).\n\n"
            f"<{result['debrief_url']}|👆 Tap here to start> · _only you can see this link_",
        )


def _handle_acknowledge_callout(payload: dict, db: Session) -> None:
    """Manager clicked 'Acknowledge' on a tight-roster alert."""
    try:
        action = (payload.get("actions") or [{}])[0]
        queue_id = int(action.get("value", "0"))
        user = payload.get("user", {})
        manager_name = user.get("name") or user.get("id", "Unknown")

        from api.src.database import CalloutQueue
        entry = db.query(CalloutQueue).filter(CalloutQueue.id == queue_id).first()
        if entry and not entry.acknowledged_at:
            from datetime import datetime
            entry.acknowledged_at = datetime.utcnow()
            entry.acknowledged_by = manager_name
            db.commit()

        # Update the original message to show it's been handled
        channel_id = payload.get("channel", {}).get("id", "")
        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            try:
                token = os.getenv("SLACK_BOT_TOKEN")
                if token:
                    from slack_sdk import WebClient as _WC
                    _WC(token=token).chat_update(
                        channel=channel_id,
                        ts=msg_ts,
                        text="Roster alert acknowledged.",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"✅ *Roster alert acknowledged* by *{manager_name}*\n"
                                        f"Driver: {entry.driver_name if entry else '—'} · "
                                        f"{entry.shift_date if entry else '—'}"
                                    ),
                                },
                            }
                        ],
                    )
            except Exception as exc:
                logger.warning("Could not update tight-roster alert message: %s", exc)
    except Exception as exc:
        logger.warning("acknowledge_callout handler error: %s", exc)


def _handle_schedule_ack(payload: dict, db: Session) -> None:
    """Driver tapped 'I've Got My Schedule' on their night-before DM."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "")
        driver_name = value.get("driver_name", "")

        from api.src.routes.rostering import ack_schedule
        ack_schedule(shift_date_str, driver_name, db)

        channel_id = payload.get("channel", {}).get("id", "")
        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            token = os.getenv("SLACK_BOT_TOKEN")
            if token:
                from slack_sdk import WebClient as _WC
                # Update the two-button block: replace schedule-ack button with confirmation text
                _WC(token=token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="Schedule acknowledged.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"📋 *Schedule acknowledged* — See you tomorrow, {driver_name.split()[0]}! ✅"},
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "✅  I Have Arrived for My Shift", "emoji": True},
                                    "style": "primary",
                                    "action_id": "driver_arrived_shift",
                                    "value": action.get("value", "{}"),
                                }
                            ],
                        },
                    ],
                )
    except Exception as exc:
        logger.warning("schedule_ack handler error: %s", exc)


def _handle_eod_complete(payload: dict, db: Session) -> None:
    """Driver tapped 'EOD Complete' on their end-of-day DM."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "")
        driver_name = value.get("driver_name", "")

        from api.src.routes.rostering import eod_complete
        eod_complete(shift_date_str, driver_name, db)

        channel_id = payload.get("channel", {}).get("id", "")
        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            token = os.getenv("SLACK_BOT_TOKEN")
            if token:
                from slack_sdk import WebClient as _WC
                _WC(token=token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="EOD checklist complete.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *EOD Checklist Complete* — Thanks {driver_name.split()[0]}, great work today! See you next shift. 🚐",
                            },
                        }
                    ],
                )
    except Exception as exc:
        logger.warning("eod_complete handler error: %s", exc)


def _handle_driver_arrived(payload: dict, db: Session) -> None:
    """Driver tapped 'I Have Arrived for My Shift' in their DM."""
    try:
        import json as _json
        action = (payload.get("actions") or [{}])[0]
        value = _json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "")
        driver_name = value.get("driver_name", "")
        user = payload.get("user", {})
        slack_user_id = user.get("id", "")

        from api.src.routes.rostering import mark_driver_arrived
        success = mark_driver_arrived(shift_date_str, driver_name, slack_user_id, db)

        # Update the DM to show arrival confirmed
        channel_id = payload.get("channel", {}).get("id", "")
        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            token = os.getenv("SLACK_BOT_TOKEN")
            if token:
                from slack_sdk import WebClient as _WC
                from datetime import datetime as _dt
                _WC(token=token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="Arrival confirmed.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"✅ *Arrival Confirmed*\n"
                                    f"*{driver_name}* checked in at "
                                    f"{_dt.utcnow().strftime('%-I:%M %p')} UTC\n"
                                    f"Shift date: {shift_date_str}"
                                ),
                            },
                        }
                    ],
                )
    except Exception as exc:
        logger.warning("driver_arrived handler error: %s", exc)
