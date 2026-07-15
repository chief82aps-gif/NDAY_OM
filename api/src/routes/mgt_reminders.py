"""
Manager reminder DMs — nags #nday-mgt members individually when a required
file hasn't landed in its monitored channel yet.

Six reminders, all DM-only to every member of #nday-mgt (never posted to
the channel itself, never sent to drivers):

  1. DOP file                — window 9:00-10:00 AM PT, every 5 min until posted (#dlv3-nday-info)
  2. Route Sheets file       — window 9:00-10:00 AM PT, every 5 min until posted (#dlv3-nday-info)
  3. Cortex Routes file      — window 9:00-10:00 AM PT, every 5 min until posted (#nday-operations-management)
  4. Fleet / Vehicle Data    — window 9:00-10:00 AM PT, every 5 min until posted (#nday-operations-management)
  5. Okami capacity forecast — window 3:30-9:00 PM PT,  every 5 min until posted (#nday-operations-management)
  6. Driver schedule (post-rostering) — window 5:00-8:00 PM PT, every 5 min until posted (#nday-operations-management)

Windows are all against our own server clock in Pacific local time (never
against a Slack message timestamp, which we don't control on the Amazon
side) and reflect when each file is actually expected to land, not just
an earliest-possible threshold. DOP/Route Sheets normally arrive before
9:00 AM (never before 7:00 AM); Fleet/Cortex ingest and Rostering follow
their own expected windows below. Each reminder stops nagging for the
day once a matching OpsIngestJob row is detected, or once its window
closes — and resets automatically at midnight Pacific (state keyed by
date).

Endpoints:
  POST /mgt-reminders/check   Manual trigger (same call the background loop makes)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, SessionLocal, OpsIngestJob, get_reminder_state, set_reminder_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mgt-reminders", tags=["mgt-reminders"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
PT = ZoneInfo("America/Los_Angeles")

REMINDER_INTERVAL_SECONDS = 5 * 60

# window = (start_hour, start_minute, end_hour, end_minute) in Pacific time —
# checked against our own server clock, never a Slack message timestamp.
_REMINDERS = {
    "dop":          {"detected_type": "dop",          "label": "DOP file",                   "window": (9, 0, 10, 0)},
    "route_sheets": {"detected_type": "route_sheets", "label": "Route Sheets file",           "window": (9, 0, 10, 0)},
    "cortex":       {"detected_type": "cortex",       "label": "Cortex Routes file",          "window": (9, 0, 10, 0)},
    "fleet":        {"detected_type": "fleet",        "label": "Fleet / Vehicle Data file",   "window": (9, 0, 10, 0)},
    "okami":        {"detected_type": "okami_capacity","label": "Okami capacity forecast",    "window": (15, 30, 21, 0)},
    "schedule":     {"detected_type": "driver_schedule","label": "Driver schedule",           "window": (17, 0, 20, 0)},
}

# Persisted in the database (ReminderThrottleState), not in-memory — an
# in-memory dict here resets on every process restart, which caused a
# 2026-07-13 incident where redeploys repeatedly wiped the "already sent"
# state and reminders spammed #nday-mgt on every restart's first tick.


def _load_state(db: Session, key: str) -> dict:
    raw = get_reminder_state(db, f"mgt_reminder_{key}")
    return {
        "last_sent_at": datetime.fromisoformat(raw["last_sent_at"]) if raw.get("last_sent_at") else None,
        "resolved_date": date.fromisoformat(raw["resolved_date"]) if raw.get("resolved_date") else None,
    }


def _save_state(db: Session, key: str, state: dict) -> None:
    set_reminder_state(db, f"mgt_reminder_{key}", {
        "last_sent_at": state["last_sent_at"].isoformat() if state.get("last_sent_at") else None,
        "resolved_date": state["resolved_date"].isoformat() if state.get("resolved_date") else None,
    })


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _mgt_member_ids(client) -> tuple[list[str], Optional[str]]:
    """All human members of #nday-mgt (excludes the bot's own user id).
    Returns (member_ids, error) — error is None on success."""
    try:
        bot_id = client.auth_test().get("user_id")
    except Exception as exc:
        logger.warning("mgt_reminders: auth_test failed: %s", exc)
        bot_id = None

    try:
        resp = client.conversations_members(channel=MGT_CHANNEL)
        members = resp.get("members", [])
    except Exception as exc:
        logger.warning("mgt_reminders: conversations_members failed: %s", exc)
        return [], str(exc)

    return [m for m in members if m != bot_id], None


def _file_detected_today(db: Session, detected_type: str, today) -> bool:
    # Midnight *Pacific*, converted to UTC — not midnight UTC on the same
    # calendar-day numbers, which is 7-8 hours too early and would count a
    # file posted late the previous Pacific evening as "detected today".
    start_local = datetime(today.year, today.month, today.day, tzinfo=PT)
    start_utc = start_local.astimezone(timezone.utc)
    return (
        db.query(OpsIngestJob)
        .filter(OpsIngestJob.detected_type == detected_type)
        .filter(OpsIngestJob.detected_at >= start_utc)
        .first()
        is not None
    )


def _resolved_today(db: Session, key: str, cfg: dict, today) -> bool:
    """Okami isn't a file — it's entered directly via the dashboard form
    (api/src/routes/okami_capacity.py), so it's resolved by a DB
    submission for the day, not an OpsIngestJob row."""
    if key == "okami":
        from api.src.routes.okami_capacity import has_submission_today
        return has_submission_today(db, today)
    return _file_detected_today(db, cfg["detected_type"], today)


def _check_one(key: str, db: Session, client, now) -> dict:
    """Runs the check for one reminder key and returns a diagnostic dict
    describing exactly what happened — used by both the silent background
    loop and the manual /check endpoint (which surfaces it in the response)."""
    cfg = _REMINDERS[key]
    state = _load_state(db, key)
    today = now.date()

    result: dict = {
        "key": key, "label": cfg["label"],
        "reason": None, "recipients": None, "sent": None, "error": None,
    }

    start_h, start_m, end_h, end_m = cfg["window"]
    past_start = (now.hour, now.minute) >= (start_h, start_m)
    past_end = (now.hour, now.minute) >= (end_h, end_m)

    if not past_start or past_end:
        result["reason"] = "outside_window"
        return result

    if state["resolved_date"] == today:
        result["reason"] = "already_resolved_this_process"
        return result

    if _resolved_today(db, key, cfg, today):
        state["resolved_date"] = today
        _save_state(db, key, state)
        result["reason"] = "file_detected_today"
        return result

    last = state["last_sent_at"]
    if last and (now - last).total_seconds() < REMINDER_INTERVAL_SECONDS:
        result["reason"] = "throttled"
        result["seconds_since_last_send"] = round((now - last).total_seconds())
        return result

    recipients, member_error = _mgt_member_ids(client)
    result["recipients"] = len(recipients)
    if member_error:
        result["error"] = f"member lookup failed: {member_error}"
        result["reason"] = "member_lookup_failed"
        state["last_sent_at"] = now
        _save_state(db, key, state)
        return result

    sent = 0
    send_errors: list[str] = []
    for uid in recipients:
        try:
            client.chat_postMessage(
                channel=uid,
                text=(
                    f":alarm_clock: *{cfg['label']} reminder* — this hasn't been posted "
                    f"yet today. Please post it as soon as it's available."
                ),
            )
            sent += 1
        except Exception as exc:
            send_errors.append(f"{uid}: {exc}")
            logger.warning("mgt_reminders: DM to %s failed: %s", uid, exc)

    state["last_sent_at"] = now
    _save_state(db, key, state)
    result["sent"] = sent
    result["reason"] = "sent"
    if send_errors:
        result["error"] = "; ".join(send_errors[:5])
    if sent:
        logger.info("mgt_reminders: sent '%s' reminder to %d #nday-mgt members", key, sent)
    return result


def run_mgt_reminders_check() -> list[dict]:
    """Called every 60s from the background loop in main.py. Returns a
    diagnostic dict per reminder key (ignored by the loop, surfaced by
    the manual /check endpoint)."""
    client = _client()
    if not client:
        return [{"key": k, "reason": "no_slack_token"} for k in _REMINDERS]
    now = datetime.now(PT)
    db = SessionLocal()
    try:
        return [_check_one(key, db, client, now) for key in _REMINDERS]
    finally:
        db.close()


@router.post("/check")
def manual_check():
    results = run_mgt_reminders_check()
    return {"status": "checked", "results": results}
