"""
Manager reminder DMs — nags #nday-mgt members individually when a required
file hasn't landed in its monitored channel yet.

Nine reminders, all DM-only to every member of #nday-mgt (never posted to
the channel itself, never sent to drivers):

  1. DOP file                — window 9:00-10:00 AM PT, every 10 min until posted (#dlv3-nday-info)
  2. Route Sheets file       — window 9:00-10:00 AM PT, every 10 min until posted (#dlv3-nday-info)
  3. Cortex Routes file      — window 9:00-10:00 AM PT, every 10 min until posted (#nday-operations-management)
  4. Fleet / Vehicle Data    — window 9:00-10:00 AM PT, every 10 min until posted (#nday-operations-management)
  5. Okami capacity forecast — window 3:30-9:00 PM PT,  every 10 min until posted (#nday-operations-management)
  6. Driver schedule (post-rostering) — window 5:30-8:00 PM PT, every 10 min until posted (#nday-operations-management)
  7. Tenured Workforce DAs Report — Fridays only, window 5:00 PM-11:59 PM PT ("by COB"), every 10 min until posted (#nday-operations-management) — includes where to find/export it in Amazon's portal
  8. Quality Overview (daily CSV) — window 3:30-9:00 PM PT, every 10 min until posted (#nday-operations-management) — added 2026-08-04, ingest already existed (api/src/ingest/daily_quality.py) but nothing prompted anyone to actually post it daily
  9. Safety Dashboard / Netradyne Events (daily CSV) — window 3:30-9:00 PM PT, every 10 min until posted (#nday-operations-management) — added 2026-08-04, same story (api/src/ingest/safety_events.py)

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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal, OpsIngestJob, get_reminder_state, set_reminder_state,
    get_latest_dop_rows, get_latest_route_sheet_rows, get_latest_cortex_rows,
)
from api.src.feature_flags import get_flag
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mgt-reminders", tags=["mgt-reminders"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

REMINDER_INTERVAL_SECONDS = 10 * 60

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
    # Added 2026-08-04 -- both ingest pipelines already existed
    # (api/src/ingest/daily_quality.py, api/src/ingest/safety_events.py,
    # both in ops_ingest.py's _AUTO_INGEST_TYPES) but nothing prompted
    # #nday-mgt to actually post either file daily. Freshens driver
    # rankings between weekly DSP Scorecard cycles and sets up a future
    # daily-vs-weekly scorecard audit. Window matches Okami's (afternoon)
    # per explicit direction -- adjust if the real arrival time turns out
    # different once this runs for a few days.
    "daily_quality": {
        "detected_type": "daily_quality",
        "label": "Quality Overview (daily CSV)",
        "window": (15, 30, 21, 0),
        "page": "/ops-ingest",
    },
    "safety_events": {
        "detected_type": "safety_events",
        "label": "Safety Dashboard / Netradyne Events (daily CSV)",
        "window": (15, 30, 21, 0),
        "page": "/ops-ingest",
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


# ─────────────────────────────────────────────────────────────────────────────
# Timecard report nudge — added 2026-07-29. Stopgap for driver_scoring.py's
# proposed expected-vs-actual clocked-time efficiency metric: real clock
# data needs a timekeeping API (ADP, currently paused on cost -- see
# adp.py), so until then this just nudges HR to post the daily timecard
# audit report into #nday-operations-management, once a day, after HR's
# own daily audit is normally done. Amanda confirmed (2026-07-29 via Slack
# DM) audits typically finish between 9-10 AM Pacific -- default hour set
# to 10 AM so the nudge lands right after that window closes.
# ─────────────────────────────────────────────────────────────────────────────

OPS_MGMT_CHANNEL = os.getenv("SLACK_OPS_MGMT_CHANNEL", "C0BE4ALL1EX")   # #nday-operations-management
TIMECARD_REPORT_NUDGE_HOUR = int(os.getenv("TIMECARD_REPORT_NUDGE_HOUR", "10"))  # confirmed with Amanda: audits finish 9-10 AM
_TIMECARD_NUDGE_KEY_PREFIX = "timecard_report_nudge_"


def run_timecard_report_nudge(db: Session, force: bool = False) -> dict:
    """Once a day: post a reminder to #nday-operations-management asking
    HR to drop today's timecard audit report there. force=True bypasses
    the hour gate and already-sent guard for manual testing."""
    if not get_flag("TIMECARD_REPORT_NUDGE_ACTIVE"):
        return {"status": "inactive", "note": "Set TIMECARD_REPORT_NUDGE_ACTIVE=true on Render to enable"}

    now_pt = datetime.now(PT)
    today = now_pt.date()

    if not force and now_pt.hour != TIMECARD_REPORT_NUDGE_HOUR:
        return {"status": "not_send_hour", "date": today.isoformat()}

    state_key = f"{_TIMECARD_NUDGE_KEY_PREFIX}{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    try:
        client.chat_postMessage(
            channel=OPS_MGMT_CHANNEL,
            text=(
                ":alarm_clock: *Daily reminder* — once today's timecard audit is wrapped up, "
                "please drop the timecard report here for the team."
            ),
        )
    except Exception as exc:
        logger.warning("Timecard report nudge post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "date": today.isoformat()}


@router.post("/timecard-nudge/trigger")
def trigger_timecard_nudge(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return run_timecard_report_nudge(db, force=force)


# ─────────────────────────────────────────────────────────────────────────────
# ECP screenshot reminder — added 2026-07-31. Once Amazon's ECP message
# lands in #dlv3-nday-info (same detection daily_notify.py already uses to
# prompt the Cortex-upload message), post to #nday-mgt asking someone to
# grab a screenshot of Amazon's Scheduling page's Unassigned section
# before dispatch rosters. At that exact moment nothing's rostered yet, so
# Unassigned shows every block for the day broken out by wave time — the
# raw per-wave capacity data the wave/rank rostering suggestion needs.
# Reuses daily_notify.py's scan_for_ecp_message() for detection rather
# than re-implementing the same #dlv3-nday-info ECP+"roster" keyword scan.
# Fires once the message is seen, not on a fixed clock time (Amazon
# typically posts it around 5 PM, but not at a guaranteed minute) —
# rostering must be complete by 7:00 PM regardless.
# ─────────────────────────────────────────────────────────────────────────────

ECP_SCREENSHOT_CHECK_START_HOUR = 17  # don't bother scanning before ~5 PM
_ECP_SCREENSHOT_KEY_PREFIX = "ecp_screenshot_reminder_"


def run_ecp_screenshot_reminder(db: Session, force: bool = False) -> dict:
    """Checked every ~60s from 5 PM Pacific onward. Fires once per day, the
    moment Amazon's ECP message shows up in #dlv3-nday-info. force=True
    bypasses the hour gate/already-sent guard for manual testing."""
    if not get_flag("ECP_SCREENSHOT_REMINDER_ACTIVE"):
        return {"status": "inactive", "note": "Set ECP_SCREENSHOT_REMINDER_ACTIVE=true on Render to enable"}

    now_pt = datetime.now(PT)
    today = now_pt.date()

    if not force and now_pt.hour < ECP_SCREENSHOT_CHECK_START_HOUR:
        return {"status": "before_window", "date": today.isoformat()}

    state_key = f"{_ECP_SCREENSHOT_KEY_PREFIX}{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    from api.src.routes.daily_notify import scan_for_ecp_message
    msg = scan_for_ecp_message()
    if not msg:
        return {"status": "no_ecp_message", "date": today.isoformat()}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=(
                ":camera: *ECP has run — grab the Scheduling screenshot for Blake*\n\n"
                "Amazon's ECP message just landed in #dlv3-nday-info. Before rostering "
                "starts, grab a screenshot of the *Scheduling* page's Unassigned section "
                "(nothing's rostered yet, so it'll show every block for today broken out "
                "by wave time) and upload it — that's the per-wave capacity data needed "
                "for today's ranked roster suggestion.\n\n"
                "Roster needs to be complete by 7:00 PM."
            ),
        )
    except Exception as exc:
        logger.warning("ECP screenshot reminder post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "date": today.isoformat()}


@router.post("/ecp-screenshot/trigger")
def trigger_ecp_screenshot_reminder(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return run_ecp_screenshot_reminder(db, force=force)


# ─────────────────────────────────────────────────────────────────────────────
# Daily fallback PIN — added 2026-08-01. A driver's personal callout/EOD PIN
# (DriverRosterEntry.ssn_last4) is the only way in today, and dispatchers have
# no way to get someone back in when it's forgotten short of an admin PIN
# reset. This is a single shared code, regenerated once per day and posted to
# #nday-mgt, that dispatch can hand out face-to-face to anyone locked out that
# day — it supplements each driver's own PIN everywhere it's checked
# (attendance.py's driver_status/submit_callout/change_driver_pin/
# family_pattern_check), it never replaces it. Gated by
# DAILY_FALLBACK_PIN_ACTIVE (default false) -- off means no code is ever
# generated or accepted, today's individual-PIN-only behavior is unchanged.
# Explicit, acknowledged tradeoff from the person who asked for this: if
# dispatchers can't be trusted to keep it from spreading past whoever
# actually needs it that day, this goes away in favor of one-off individual
# resets instead.
# ─────────────────────────────────────────────────────────────────────────────

import random as _random

_DAILY_FALLBACK_PIN_KEY = "daily_fallback_pin"
_DAILY_FALLBACK_PIN_POST_KEY_PREFIX = "daily_fallback_pin_posted_"
DAILY_FALLBACK_PIN_POST_HOUR = int(os.getenv("DAILY_FALLBACK_PIN_POST_HOUR", "6"))  # early, ahead of showtimes/callouts


def get_daily_fallback_pin(db: Session) -> Optional[str]:
    """Returns today's shared fallback PIN, generating one on first use each
    day if none exists yet -- so a PIN check that happens before the morning
    announcement job runs still works off the same code the announcement
    will post. Returns None when the feature is off."""
    if not get_flag("DAILY_FALLBACK_PIN_ACTIVE", db):
        return None
    today = datetime.now(PT).date().isoformat()
    state = get_reminder_state(db, _DAILY_FALLBACK_PIN_KEY)
    if state.get("date") == today and state.get("code"):
        return state["code"]
    code = f"{_random.randint(1000, 9999)}"
    set_reminder_state(db, _DAILY_FALLBACK_PIN_KEY, {"date": today, "code": code})
    return code


def run_daily_fallback_pin_post(db: Session, force: bool = False) -> dict:
    """Once a day: posts today's shared fallback PIN to #nday-mgt. force=True
    bypasses the already-sent guard for manual testing."""
    if not get_flag("DAILY_FALLBACK_PIN_ACTIVE"):
        return {"status": "inactive", "note": "Set DAILY_FALLBACK_PIN_ACTIVE=true on Render to enable"}

    now_pt = datetime.now(PT)
    today = now_pt.date()

    if not force and now_pt.hour != DAILY_FALLBACK_PIN_POST_HOUR:
        return {"status": "not_send_hour", "date": today.isoformat()}

    state_key = f"{_DAILY_FALLBACK_PIN_POST_KEY_PREFIX}{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    code = get_daily_fallback_pin(db)
    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=(
                f":key: *Today's fallback PIN: {code}*\n\n"
                "Give this to any driver locked out of the callout page, EOD survey PIN "
                "prompt, or their own PIN today — it works alongside their personal PIN, "
                "not instead of it. Only hand it out to the driver who actually needs it."
            ),
        )
    except Exception as exc:
        logger.warning("Daily fallback PIN post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "date": today.isoformat(), "code": code}


@router.post("/daily-fallback-pin/trigger")
def trigger_daily_fallback_pin_post(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return run_daily_fallback_pin_post(db, force=force)
