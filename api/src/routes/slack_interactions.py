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
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.src.database import get_db, DriverRosterEntry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/slack", tags=["slack"])

PACIFIC = ZoneInfo("America/Los_Angeles")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
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
        # Fail CLOSED: a missing secret must reject the request, not wave
        # every interactive payload through unverified. (Previously failed
        # open here — fixed 2026-07-13.)
        logger.error("SLACK_SIGNING_SECRET not set — rejecting interactive request (set it on Render)")
        return False
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
DRIVER_DASHBOARD_CHANNEL = os.getenv("DRIVER_DASHBOARD_CHANNEL_ID", "C0BEDCXNQNT")  # #driver-dashboard

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


def _driver_dashboard_hub_blocks() -> list:
    """The single #driver-dashboard hub card: one message, one group of buttons.
    Kept as a fallback alongside the Home tab (slack_home.py) — works even for
    drivers not yet Slack-linked and needs no per-user config."""
    team_id = os.getenv("SLACK_TEAM_ID")
    app_id = os.getenv("SLACK_APP_ID")
    home_deep_link = f"slack://app?team={team_id}&id={app_id}&tab=home" if team_id and app_id else None

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "New Day Logistics - Driver Dashboard", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Your one-stop hub for all driver tools. Tap a button below to get started."},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "btn_callout",
                    "text": {"type": "plain_text", "text": "Submit Call-Out", "emoji": True},
                    "style": "danger",
                    "url": f"{FRONTEND_URL}/callout",
                },
                {
                    "type": "button",
                    "action_id": "home_rto_button",
                    "text": {"type": "plain_text", "text": "Request PTO", "emoji": True},
                },
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "btn_eod",
                    "text": {"type": "plain_text", "text": "End of Day Survey", "emoji": True},
                    "url": f"{FRONTEND_URL}/eod",
                },
                {
                    "type": "button",
                    "action_id": "btn_rts",
                    "text": {"type": "plain_text", "text": "🔄 Return to Station", "emoji": True},
                    "style": "primary",
                    "url": f"{FRONTEND_URL}/rts",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "New Day Logistics LLC . Questions? Contact dispatch directly."}
            ],
        },
    ]

    if home_deep_link:
        blocks.insert(3, {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"✨ <{home_deep_link}|Open your full Driver Home> for your standing, quality scores, and more."},
        })

    return blocks


@router.post("/sync-driver-dashboard")
def sync_driver_dashboard():
    """Idempotent setup: finds the existing #driver-dashboard hub card and updates it
    in place (adding/replacing the Return to Station button), or posts it fresh if
    no hub card exists yet. Safe to call repeatedly."""
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise HTTPException(500, "SLACK_BOT_TOKEN not set.")
        client = WebClient(token=token)
        blocks = _driver_dashboard_hub_blocks()
        fallback_text = "New Day Logistics - Driver Dashboard: Call-Out, PTO, EOD Survey, Suggestion Box, Return to Station."

        existing_ts = None
        history = client.conversations_history(channel=DRIVER_DASHBOARD_CHANNEL, limit=50)
        for msg in history.get("messages", []):
            if not msg.get("bot_id"):
                continue
            has_marker = "Driver Dashboard" in (msg.get("text") or "") or any(
                "Driver Dashboard" in (b.get("text", {}).get("text", "") or "")
                for b in msg.get("blocks", []) if b.get("type") == "header"
            )
            if has_marker:
                existing_ts = msg["ts"]
                break

        if existing_ts:
            client.chat_update(channel=DRIVER_DASHBOARD_CHANNEL, ts=existing_ts, text=fallback_text, blocks=blocks)
            return {"status": "updated", "channel": DRIVER_DASHBOARD_CHANNEL, "ts": existing_ts}
        else:
            resp = client.chat_postMessage(channel=DRIVER_DASHBOARD_CHANNEL, text=fallback_text, blocks=blocks)
            return {"status": "posted", "channel": DRIVER_DASHBOARD_CHANNEL, "ts": resp["ts"]}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/sync-dispatch-hub")
def sync_dispatch_hub():
    """Idempotent setup for the dispatch-staff channel hub card, same
    find-or-post pattern as sync-driver-dashboard. Safe to call repeatedly."""
    try:
        from slack_sdk import WebClient
        from api.src.routes.slack_dispatch_home import DISPATCH_HOME_CHANNEL_ID

        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise HTTPException(500, "SLACK_BOT_TOKEN not set.")
        client = WebClient(token=token)

        team_id = os.getenv("SLACK_TEAM_ID")
        app_id = os.getenv("SLACK_APP_ID")
        home_deep_link = f"slack://app?team={team_id}&id={app_id}&tab=home" if team_id and app_id else None

        header_text = "New Day Logistics - Dispatch Home"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Dispatch/ops tools live in the Dispatch Home tab now."},
            },
        ]
        if home_deep_link:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"✨ <{home_deep_link}|Open Dispatch Home>"},
            })
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "New Day Logistics LLC"}],
        })
        fallback_text = "New Day Logistics - Dispatch Home: dispatch/ops tools."

        existing_ts = None
        history = client.conversations_history(channel=DISPATCH_HOME_CHANNEL_ID, limit=50)
        for msg in history.get("messages", []):
            if not msg.get("bot_id"):
                continue
            has_marker = header_text in (msg.get("text") or "") or any(
                header_text in (b.get("text", {}).get("text", "") or "")
                for b in msg.get("blocks", []) if b.get("type") == "header"
            )
            if has_marker:
                existing_ts = msg["ts"]
                break

        if existing_ts:
            client.chat_update(channel=DISPATCH_HOME_CHANNEL_ID, ts=existing_ts, text=fallback_text, blocks=blocks)
            return {"status": "updated", "channel": DISPATCH_HOME_CHANNEL_ID, "ts": existing_ts}
        else:
            resp = client.chat_postMessage(channel=DISPATCH_HOME_CHANNEL_ID, text=fallback_text, blocks=blocks)
            return {"status": "posted", "channel": DISPATCH_HOME_CHANNEL_ID, "ts": resp["ts"]}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Interactions endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/interactions")
async def slack_interactions(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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

    # Modal submissions (Home tab quick-capture forms) are a different
    # payload shape — no "actions" key, keyed by view.callback_id instead.
    if payload.get("type") == "view_submission":
        callback_id = payload.get("view", {}).get("callback_id", "")
        if callback_id == "home_report_submit":
            from api.src.routes.slack_home import _handle_home_report_submit
            return _handle_home_report_submit(payload, db)
        if callback_id == "home_rto_submit":
            from api.src.routes.slack_home import _handle_home_rto_submit
            return _handle_home_rto_submit(payload, db)
        if callback_id == "dispatch_remove_terminated_submit":
            from api.src.routes.slack_dispatch_home import _handle_dispatch_remove_terminated_submit
            return _handle_dispatch_remove_terminated_submit(payload, db)
        if callback_id == "dispatch_invite_user_submit":
            from api.src.routes.slack_dispatch_home import _handle_dispatch_invite_user_submit
            return _handle_dispatch_invite_user_submit(payload, db)
        if callback_id == "dispatch_reset_password_submit":
            from api.src.routes.slack_dispatch_home import _handle_dispatch_reset_password_submit
            return _handle_dispatch_reset_password_submit(payload, db)
        if callback_id == "dispatch_ingest_alerts_pin_submit":
            from api.src.routes.slack_dispatch_home import _handle_dispatch_ingest_alerts_pin_submit
            return _handle_dispatch_ingest_alerts_pin_submit(payload, db)
        return {"ok": True}

    action_id = (payload.get("actions") or [{}])[0].get("action_id", "")

    if action_id == "callout_button":
        _handle_callout_button(payload, db)

    elif action_id == "acknowledge_callout_alert":
        _handle_acknowledge_callout(payload, db)

    elif action_id == "driver_arrived_shift":
        _handle_driver_arrived(payload, db)

    elif action_id == "talk_to_lead":
        _handle_talk_to_lead(payload, db)

    elif action_id == "driver_schedule_ack":
        _handle_schedule_ack(payload, db)

    elif action_id == "driver_decline_shift":
        _handle_driver_decline_shift(payload, db)

    elif action_id == "driver_callout_from_dm":
        _handle_driver_callout_from_dm(payload, db)

    elif action_id == "driver_eod_complete":
        _handle_eod_complete(payload, db)

    elif action_id == "dvic_ack":
        _handle_dvic_ack(payload, db)

    elif action_id == "home_callout_button":
        from api.src.routes.slack_home import _handle_home_callout_button
        _handle_home_callout_button(payload, db)

    elif action_id in ("home_report_injury", "home_incident_report"):
        from api.src.routes.slack_home import _handle_home_report_button
        _handle_home_report_button(payload, db, action_id)

    elif action_id == "home_rto_button":
        from api.src.routes.slack_home import _handle_home_rto_button
        _handle_home_rto_button(payload, db)

    elif action_id == "dispatch_remove_terminated_button":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_remove_terminated_button
        _handle_dispatch_remove_terminated_button(payload, db)

    elif action_id == "dispatch_preview_driver_home":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_preview_driver_home
        _handle_dispatch_preview_driver_home(payload, db)

    elif action_id == "dispatch_back_from_preview":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_back_from_preview
        _handle_dispatch_back_from_preview(payload, db)

    elif action_id == "dispatch_rerun_route_assignments":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_rerun_route_assignments
        _handle_dispatch_rerun_route_assignments(payload, db, background_tasks)

    elif action_id == "dispatch_republish_showtime":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_republish_showtime
        _handle_dispatch_republish_showtime(payload, db)

    elif action_id == "dispatch_send_route_matrix":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_send_route_matrix
        _handle_dispatch_send_route_matrix(payload, db)

    elif action_id == "dispatch_invite_user_button":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_invite_user_button
        _handle_dispatch_invite_user_button(payload, db)

    elif action_id == "dispatch_reset_password_button":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_reset_password_button
        _handle_dispatch_reset_password_button(payload, db)

    elif action_id == "dispatch_ingest_alerts_button":
        from api.src.routes.slack_dispatch_home import _handle_dispatch_ingest_alerts_button
        _handle_dispatch_ingest_alerts_button(payload, db)

    elif action_id == "crash_report_approve":
        from api.src.routes.crash_report import _handle_crash_report_approve
        _handle_crash_report_approve(payload, db)

    elif action_id == "crash_report_drug_screen_done":
        from api.src.routes.crash_report import _handle_crash_report_drug_screen_done
        _handle_crash_report_drug_screen_done(payload, db)

    # Slack requires a 200 response within 3 seconds
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Events API — push-based file detection for #nday-operations-management,
# replacing/backstopping daily_notify.py's 10-min, 8-10AM-only poll for
# DOP/Route Sheet/Cortex (added 2026-07-20). Those three are the only Amazon-
# adjacent sources NOT gated behind the Amazon portal -- Amazon posts them
# directly into Slack each morning, so there's no download step to automate,
# only a detection-latency problem. This does NOT apply to Cortex/Fleet/
# DVIC/WST/driver schedule's actual download step -- see the Amazon portal
# automation ban in CLAUDE.md. The 10-min poll stays in place as a backstop
# in case an event is ever dropped; check_and_notify()'s own SlackIngestLog
# check makes calling it twice for the same file a no-op either way.
# ─────────────────────────────────────────────────────────────────────────────

def _run_event_triggered_notify() -> None:
    from api.src.database import SessionLocal
    from api.src.routes.daily_notify import check_and_notify

    db = SessionLocal()
    try:
        result = check_and_notify(db)
        logger.info("Slack event-triggered notify: %s", result)
    except Exception as exc:
        logger.warning("Slack event-triggered notify failed: %s", exc)
    finally:
        db.close()


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Slack Events API Request URL. Configure in the Slack app's Event
    Subscriptions with this endpoint, subscribed to message.channels (or
    file_shared) scoped to #nday-operations-management -- a manual step in
    Slack's app console, not something this code can set up on its own."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        raise HTTPException(403, "Invalid Slack signature.")

    payload = json.loads(body)

    # One-time handshake Slack requires when the Request URL is first saved.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        from api.src.routes.daily_notify import NOTIFY_CHANNEL

        has_files = bool(event.get("files")) or event.get("subtype") == "file_share"
        if event.get("type") == "message" and event.get("channel") == NOTIFY_CHANNEL and has_files:
            background_tasks.add_task(_run_event_triggered_notify)

    # Slack requires a 200 response within 3 seconds
    return {"ok": True}


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
                # Arrival confirmation belongs solely to the day-of Route
                # Assignment DM, not this one — no button re-added here.
                _WC(token=token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="Schedule acknowledged.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"📋 *Schedule acknowledged* — See you tomorrow, "
                                    f"{driver_name.split()[0]}! You'll get a separate DM "
                                    f"the day of your shift with route details and an "
                                    f"arrival confirmation button."
                                ),
                            },
                        },
                    ],
                )
    except Exception as exc:
        logger.warning("schedule_ack handler error: %s", exc)


def _handle_driver_decline_shift(payload: dict, db: Session) -> None:
    """Driver tapped 'Can't Make It' on their night-before Showtime DM."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "")
        driver_name = value.get("driver_name", "")
        channel_id = payload.get("channel", {}).get("id", "")
        user_id = payload.get("user", {}).get("id", "")

        # "Can't Make It" the night before is the same real-world event as
        # a same-day callout, so it goes through the one real write-up/
        # compliance pipeline (attendance.py's /callout — AttendanceEvent,
        # points, missed-shift count, #nday-mgt notification) rather than
        # a separate lighter-weight record. decline_shift() still logs a
        # DM-response timestamp for quick "did they respond" visibility.
        from api.src.routes.rostering import decline_shift
        decline_shift(shift_date_str, driver_name, db)

        token = _issue_callout_token(driver_name, shift_date_str)
        url = f"{FRONTEND_URL}/callout?token={token}"

        _send_ephemeral(
            channel_id, user_id,
            f"Your personal absence report link — *only you can see this message.*\n\n"
            f"<{url}|👆 Tap here to report your absence>\n\n"
            f"_Expires in {TOKEN_TTL_HOURS} hours. Do not share this link._",
        )

        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            bot_token = os.getenv("SLACK_BOT_TOKEN")
            if bot_token:
                from slack_sdk import WebClient as _WC
                _WC(token=bot_token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="Absence report link sent.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"❌ *Can't Make It* — {driver_name.split()[0]}, check the "
                                    f"message above for your private absence report link."
                                ),
                            },
                        },
                    ],
                )
    except Exception as exc:
        logger.warning("driver_decline_shift handler error: %s", exc)


def _handle_driver_callout_from_dm(payload: dict, db: Session) -> None:
    """Driver tapped 'Call Out' on their day-of Route Assignment DM.
    Reuses the same tokenized callout link as the standing #nday-team-room
    Call Out button (_handle_callout_button) rather than recording the
    callout a second, different way — per this module's design, no
    callout business logic lives here beyond issuing the token. The real
    /callout submission (attendance.py) is what notifies #nday-mgt, once
    the driver actually completes the form — nothing extra posted here."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "") or _default_shift_date()
        driver_name = value.get("driver_name", "")
        channel_id = payload.get("channel", {}).get("id", "")
        user_id = payload.get("user", {}).get("id", "")

        from api.src.routes.rostering import mark_callout_tapped
        mark_callout_tapped(shift_date_str, driver_name, db)

        token = _issue_callout_token(driver_name, shift_date_str)
        url = f"{FRONTEND_URL}/callout?token={token}"

        _send_ephemeral(
            channel_id, user_id,
            f"Your personal absence report link — *only you can see this message.*\n\n"
            f"<{url}|👆 Tap here to report your absence>\n\n"
            f"_Expires in {TOKEN_TTL_HOURS} hours. Do not share this link._",
        )
    except Exception as exc:
        logger.warning("driver_callout_from_dm handler error: %s", exc)


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


def _handle_talk_to_lead(payload: dict, db: Session) -> None:
    """Driver tapped 'Talk to My Lead' in their day-of shift DM. Resolves
    today's lead at press-time (never a cached contact), so a lead change
    earlier the same day is always picked up. See
    Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md §7."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        shift_date_str = value.get("shift_date", "")
        driver_name = value.get("driver_name", "")
        channel_id = payload.get("channel", {}).get("id", "")

        from api.src.routes.driver_lead_schedule import get_current_lead
        shift_date = date.fromisoformat(shift_date_str)
        lead_name, lead_slack_id, _source = get_current_lead(shift_date, db)

        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            return
        from slack_sdk import WebClient as _WC
        client = _WC(token=token)

        response_text = f"Couldn't reach {lead_name} automatically — try Zello instead."
        if lead_slack_id:
            try:
                client.chat_postMessage(
                    channel=lead_slack_id,
                    text=f"📻 {driver_name} wants to talk to you — they tapped *Talk to My Lead* from their shift DM.",
                )
                response_text = f"✅ Sent — {lead_name} has been notified and will reach out."
            except Exception as exc:
                logger.warning("Talk-to-lead DM send failed: %s", exc)

        if channel_id:
            client.chat_postMessage(channel=channel_id, text=response_text)
    except Exception as exc:
        logger.warning("Talk-to-lead handler error: %s", exc)


def _handle_dvic_ack(payload: dict, db: Session) -> None:
    """Driver tapped 'Acknowledge' on a DVIC safety notice DM."""
    try:
        import json as _json
        action = (payload.get("actions") or [{}])[0]
        value = _json.loads(action.get("value", "{}"))
        transporter_id = value.get("transporter_id", "")
        week = value.get("week", "")

        from api.src.routes.dvic import record_acknowledgment
        result = record_acknowledgment(transporter_id, week, "Acknowledged via Slack", db)

        channel_id = payload.get("channel", {}).get("id", "")
        msg_ts = payload.get("message", {}).get("ts", "")
        if channel_id and msg_ts:
            token = os.getenv("SLACK_BOT_TOKEN")
            if token:
                from slack_sdk import WebClient as _WC
                from datetime import datetime as _dt
                name = result.get("transporter_name") or transporter_id
                _WC(token=token).chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text="Acknowledged.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"✅ *Acknowledged*\n"
                                    f"*{name}* acknowledged this DVIC safety notice at "
                                    f"{_dt.utcnow().strftime('%-I:%M %p')} UTC."
                                ),
                            },
                        }
                    ],
                )
    except Exception as exc:
        logger.warning("dvic_ack handler error: %s", exc)
