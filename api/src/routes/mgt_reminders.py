"""
Manager reminder DMs — nags #nday-mgt members individually when a required
file hasn't landed in its monitored channel yet.

Seven reminders, all DM-only to every member of #nday-mgt (never posted to
the channel itself, never sent to drivers):

  1. DOP file                — window 9:00-10:00 AM PT, every 5 min until posted (#dlv3-nday-info)
  2. Route Sheets file       — window 9:00-10:00 AM PT, every 5 min until posted (#dlv3-nday-info)
  3. Cortex Routes file      — window 9:00-10:00 AM PT, every 5 min until posted (#nday-operations-management)
  4. Fleet / Vehicle Data    — window 9:00-10:00 AM PT, every 5 min until posted (#nday-operations-management)
  5. Okami capacity forecast — window 3:30-9:00 PM PT,  every 5 min until posted (#nday-operations-management)
  6. Driver schedule (post-rostering) — window 5:30-8:00 PM PT, every 5 min until posted (#nday-operations-management)
  7. Tenured Workforce DAs Report — Fridays only, window 5:00 PM-11:59 PM PT ("by COB"), every 5 min until posted (#nday-operations-management) — includes where to find/export it in Amazon's portal

Windows are all against our own server clock in Pacific local time (never
against a Slack message timestamp, which we don't control on the Amazon
side) and reflect when each file is actually expected to land, not just
an earliest-possible threshold. DOP/Route Sheets normally arrive before
9:00 AM (never before 7:00 AM); Fleet/Cortex ingest and Rostering follow
their own expected windows below. Each reminder stops nagging for the
day once a matching OpsIngestJob row is detected, or once its window
closes — and resets automatically at midnight Pacific (state keyed by
date). Reminder #7 additionally only ever checks on Fridays (weekday=4);
every other day it's a no-op regardless of time.

Every reminder DM includes a direct link to the frontend page where the
action actually happens (the `/upload` tab for that file type, the Okami
form, the driver-schedule uploader, or `/ops-ingest` when no dedicated
upload tab exists yet) — same pattern as the EOD survey link sent to
drivers, so the recipient doesn't have to go find the right page.

Endpoints:
  POST /mgt-reminders/check   Manual trigger (same call the background loop makes)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal, OpsIngestJob, get_reminder_state, set_reminder_state,
    get_latest_dop_rows, get_latest_route_sheet_rows, get_latest_cortex_rows,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mgt-reminders", tags=["mgt-reminders"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")
PT = ZoneInfo("America/Los_Angeles")

REMINDER_INTERVAL_SECONDS = 5 * 60

# window = (start_hour, start_minute, end_hour, end_minute) in Pacific time —
# checked against our own server clock, never a Slack message timestamp.
# "page" is the frontend route where the reminded-of action actually happens
# (direct upload, or the ops-ingest monitor when no dedicated upload tab
# exists yet) — same pattern as the EOD survey link sent to drivers.
_REMINDERS = {
    "dop":          {"detected_type": "dop",          "label": "DOP file",                   "window": (9, 0, 10, 0), "page": "/upload?view=daily"},
    "route_sheets": {"detected_type": "route_sheets", "label": "Route Sheets file",           "window": (9, 0, 10, 0), "page": "/upload?view=daily"},
    "cortex":       {"detected_type": "cortex",       "label": "Cortex Routes file",          "window": (9, 0, 10, 0), "page": "/upload?view=daily"},
    "fleet":        {"detected_type": "fleet",        "label": "Fleet / Vehicle Data file",   "window": (9, 0, 10, 0), "page": "/upload?view=daily"},
    "okami":        {"detected_type": "okami_capacity","label": "Okami capacity forecast",    "window": (15, 30, 21, 0), "page": "/okami-capacity"},
    "schedule":     {"detected_type": "driver_schedule","label": "Driver schedule",           "window": (17, 30, 20, 0), "page": "/driver-schedule"},
    "tenured_workforce": {
        "detected_type": "tenured_workforce",
        "label": "Tenured Workforce DAs Report",
        "window": (17, 0, 23, 59),
        "weekday": 4,  # Friday only (Monday=0 ... Sunday=6) -- "by COB each Friday"
        "page": "/ops-ingest",
        "hint": (
            "Find it at logistics.amazon.com -> Performance -> Interactive Report -> "
            "Supplementary Reports -> *TWF Dashboard*. Export via the three-stacked-dots "
            "menu (⋮) -> *Export to CSV*."
        ),
    },
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
        "sent_final": date.fromisoformat(raw["sent_final"]) if raw.get("sent_final") else None,
    }


def _save_state(db: Session, key: str, state: dict) -> None:
    set_reminder_state(db, f"mgt_reminder_{key}", {
        "last_sent_at": state["last_sent_at"].isoformat() if state.get("last_sent_at") else None,
        "resolved_date": state["resolved_date"].isoformat() if state.get("resolved_date") else None,
        "sent_final": state["sent_final"].isoformat() if state.get("sent_final") else None,
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


# For dop/route_sheets/cortex: check the real table each type is meant to
# land in, not OpsIngestJob existence. Those three are deliberately excluded
# from ops_ingest.py's auto-ingest (separate pipeline, daily_notify.py owns
# them — see Governance/DOP_ROUTE_SHEET_INGEST_RULES.md), so their
# OpsIngestJob rows sit "pending" forever by design; a mere row appearing
# there (created within ~60s of the file landing, by the always-on
# ops_ingest.py scanner) used to be treated as "resolved" here even though
# daily_notify.py's own parse never ran or never succeeded — this is what
# let DOP/Route Sheet/Cortex misses go unnoticed on 2026-07-17 despite the
# files arriving on time. Fixed by checking real ingested data instead.
_REAL_DATA_CHECKS = {
    "dop": get_latest_dop_rows,
    "route_sheets": get_latest_route_sheet_rows,
    "cortex": get_latest_cortex_rows,
}


def _resolved_today(db: Session, key: str, cfg: dict, today) -> bool:
    """Okami isn't a file — it's entered directly via the dashboard form
    (api/src/routes/okami_capacity.py), so it's resolved by a DB
    submission for the day, not an OpsIngestJob row.

    dop/route_sheets/cortex are resolved by real ingested data (see
    _REAL_DATA_CHECKS above), not by OpsIngestJob existence.

    Everything else (fleet, schedule, tenured_workforce) genuinely goes
    through ops_ingest.py's auto-ingest and can fail mid-run (download
    error, dispatch exception) — require status=='complete', not mere
    existence, via ops_ingest.py's own single source of truth for that."""
    if key == "okami":
        from api.src.routes.okami_capacity import has_submission_today
        return has_submission_today(db, today)
    if key in _REAL_DATA_CHECKS:
        return len(_REAL_DATA_CHECKS[key](db, today)) > 0
    from api.src.routes.ops_ingest import is_type_ingested_today
    return is_type_ingested_today(db, cfg["detected_type"], today)


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

    weekday = cfg.get("weekday")  # Monday=0 ... Sunday=6, None = every day
    if weekday is not None and now.weekday() != weekday:
        result["reason"] = "wrong_weekday"
        return result

    start_h, start_m, end_h, end_m = cfg["window"]
    past_start = (now.hour, now.minute) >= (start_h, start_m)
    past_end = (now.hour, now.minute) >= (end_h, end_m)

    if not past_start:
        result["reason"] = "outside_window"
        return result

    if past_end:
        # Previously just went silent once the window closed, with no
        # distinction between "resolved during the window" and "never
        # showed up" — a dispatcher had no way to tell the two apart short
        # of reading logs. One clear channel post, once per key per day,
        # closes that gap; modeled on dvic.py's existing final-notice
        # pattern (its own separate reminder for the weekly DVIC file).
        if state.get("resolved_date") != today and state.get("sent_final") != today:
            if _resolved_today(db, key, cfg, today):
                state["resolved_date"] = today
                _save_state(db, key, state)
            else:
                state["sent_final"] = today
                _save_state(db, key, state)
                try:
                    client.chat_postMessage(
                        channel=MGT_CHANNEL,
                        text=(
                            f":warning: *{cfg['label']}* — window closed with no file received today. "
                            f"No further reminders will be sent for this today."
                        ),
                    )
                    result["reason"] = "window_closed_final_notice_sent"
                except Exception as exc:
                    logger.warning("mgt_reminders: final notice post failed for %s: %s", cfg["label"], exc)
                    result["reason"] = "window_closed_final_notice_failed"
                    result["error"] = str(exc)
                return result
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
    hint = cfg.get("hint")
    page_url = f"{APP_URL}{cfg['page']}"
    message = (
        f":alarm_clock: *{cfg['label']} reminder* — this hasn't been posted "
        f"yet today. Please post it as soon as it's available."
        + (f"\n{hint}" if hint else "")
        + f"\n👉 *<{page_url}|Open {cfg['label']}>*"
    )
    for uid in recipients:
        try:
            client.chat_postMessage(
                channel=uid,
                text=message,
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
