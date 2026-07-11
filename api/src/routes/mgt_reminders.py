"""
Manager reminder DMs — nags #nday-mgt members individually when a
morning/afternoon/evening file hasn't landed in #nday-operations-management yet.

Four reminders, all DM-only to every member of #nday-mgt (never posted to
the channel itself, never sent to drivers):

  1. Cortex Routes file      — threshold 9:00 AM PT,  every 5 min until posted
  2. Fleet / Vehicle Data    — threshold 9:00 AM PT,  every 5 min until posted
  3. Okami capacity forecast — threshold 3:30 PM PT,  every 5 min until posted
  4. Driver schedule (post-rostering) — threshold 7:30 PM PT, every 5 min until posted

Each stops nagging for the day once a matching OpsIngestJob row is detected,
and resets automatically at midnight Pacific (state keyed by date).

Endpoints:
  POST /mgt-reminders/check   Manual trigger (same call the background loop makes)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, SessionLocal, OpsIngestJob

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mgt-reminders", tags=["mgt-reminders"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
PT = ZoneInfo("America/Los_Angeles")

REMINDER_INTERVAL_SECONDS = 5 * 60

# window = (start_hour, start_minute, end_hour, end_minute) in Pacific time
_REMINDERS = {
    "cortex":   {"detected_type": "cortex",         "label": "Cortex Routes file",        "window": (9, 0, 21, 0)},
    "fleet":    {"detected_type": "fleet",          "label": "Fleet / Vehicle Data file",  "window": (9, 0, 21, 0)},
    "okami":    {"detected_type": "okami_capacity", "label": "Okami capacity forecast",    "window": (15, 30, 21, 0)},
    "schedule": {"detected_type": "driver_schedule","label": "Driver schedule",            "window": (19, 30, 23, 59)},
}

# in-memory per-key state — resets on deploy/restart, same convention as
# dvic.py / dsp_scorecard_weekly.py's reminder throttling
_state: dict[str, dict] = {key: {"last_sent_at": None, "resolved_date": None} for key in _REMINDERS}


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _mgt_member_ids(client) -> list[str]:
    """All human members of #nday-mgt (excludes the bot's own user id)."""
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
        return []

    return [m for m in members if m != bot_id]


def _dm_all(client, text: str) -> int:
    sent = 0
    for uid in _mgt_member_ids(client):
        try:
            client.chat_postMessage(channel=uid, text=text)
            sent += 1
        except Exception as exc:
            logger.warning("mgt_reminders: DM to %s failed: %s", uid, exc)
    return sent


def _file_detected_today(db: Session, detected_type: str, today) -> bool:
    start_utc = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return (
        db.query(OpsIngestJob)
        .filter(OpsIngestJob.detected_type == detected_type)
        .filter(OpsIngestJob.detected_at >= start_utc)
        .first()
        is not None
    )


def _check_one(key: str, db: Session, client, now) -> None:
    cfg = _REMINDERS[key]
    state = _state[key]
    today = now.date()

    start_h, start_m, end_h, end_m = cfg["window"]
    past_start = (now.hour, now.minute) >= (start_h, start_m)
    past_end = (now.hour, now.minute) >= (end_h, end_m)

    if not past_start or past_end:
        return

    if state["resolved_date"] == today:
        return

    if _file_detected_today(db, cfg["detected_type"], today):
        state["resolved_date"] = today
        return

    last = state["last_sent_at"]
    if last and (now - last).total_seconds() < REMINDER_INTERVAL_SECONDS:
        return

    sent = _dm_all(
        client,
        f":alarm_clock: *{cfg['label']} reminder* — this hasn't been posted to "
        f"#nday-operations-management yet today. Please post it as soon as it's available.",
    )
    state["last_sent_at"] = now
    if sent:
        logger.info("mgt_reminders: sent '%s' reminder to %d #nday-mgt members", key, sent)


def run_mgt_reminders_check() -> None:
    """Called every 60s from the background loop in main.py."""
    client = _client()
    if not client:
        return
    now = datetime.now(PT)
    db = SessionLocal()
    try:
        for key in _REMINDERS:
            _check_one(key, db, client, now)
    finally:
        db.close()


@router.post("/check")
def manual_check():
    run_mgt_reminders_check()
    return {"status": "checked"}
