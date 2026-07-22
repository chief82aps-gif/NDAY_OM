"""
Rostering module — nightly reminder, driver shift DMs, #nday-mgt summary matrix.

Background loops (started in main.py):
  _nightly_roster_reminder_loop()  — fires daily at 19:00 PT
  _grounded_van_watcher_loop()     — polls #nday-team-room for grounded-van mentions

Endpoints:
  POST /rostering/nightly-reminder        manual trigger for 1900hrs reminder
  POST /rostering/driver-dms/{date}       send shift DMs for a given date (YYYY-MM-DD)
  POST /rostering/mgt-summary/{date}      post/refresh #nday-mgt roster suggestion matrix
  POST /rostering/assignment-matrix/{date} post #nday-mgt day-of assignment matrix (driver/route/van/est. return)
  GET  /rostering/suggested/{date}        return ranked roster suggestion
  GET  /rostering/shift-dms/{date}        list DM status + arrival confirmations
  POST /rostering/mark-arrived            called by Slack interaction handler
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    DriverScheduleEntry,
    DriverRosterEntry,
    NightlyRosterReminder,
    DriverShiftDM,
    MgtSummaryPost,
    QualityMetricDriver,
    QualityMetricSnapshot,
    AttendanceEvent,
    DailyRouteAssignment,
    WaveLeadNotification,
    CortexSnapshot,
    SlackIngestLog,
    RtsDebrief,
    get_reminder_state,
    set_reminder_state,
)
from api.src.schedule_config import SHOWTIME_OFFSET_MINUTES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rostering", tags=["rostering"])

# ─── Slack constants ──────────────────────────────────────────────────────────
MGT_CHANNEL   = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
TEAM_CHANNEL  = os.getenv("SLACK_TEAM_CHANNEL", "C0BAQAYKANS")  # #nday-team-room
FRONTEND_URL  = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")

# Hard off-switch for ALL automatic messages to #nday-team-room, per
# explicit 2026-07-20 decision made during a run of same-day production
# bugs (showtime/DVIC/van-assignment) -- unreliable info reaching the
# whole team causes more harm than a missed post. Default false/off.
# Does NOT affect #nday-mgt (internal ops channel) or any manually-
# triggered send (e.g. the Send Route Matrix Dispatch Home button) --
# only this automatic showtime-summary destination.
TEAM_ROOM_MESSAGES_ACTIVE = os.getenv("TEAM_ROOM_MESSAGES_ACTIVE", "false").lower() == "true"

SPENCER_ID    = "U0BE493C5K9"
LUIS_ID       = "U0B36C9R8N4"
FABIAN_ID     = "U0AJPQALDLL"

# Managers by weekday (0=Mon … 6=Sun)
# Spencer: Sun(6), Mon(0), Tue(1), Wed(2)
# Fabian:  Wed(2), Thu(3), Fri(4), Sat(5)
_SPENCER_DAYS = {0, 1, 2, 6}
_FABIAN_DAYS  = {2, 3, 4, 5}

# Hard van constraints per driver (override all other assignment logic)
DRIVER_VAN_CONSTRAINTS: dict[str, str] = {
    "Austin Spitzer": "4WD P31 Delivery Truck",
    # Riley's last name TBD — add here once confirmed
}

# Known nursery-area routes (flagged as risk in summary — extend as needed)
NURSERY_ROUTE_PREFIXES: set[str] = set()   # e.g. {"NUR", "GRD"} — populated later

# Feature gate — set ROSTERING_ACTIVE=true on Render to enable live messages
_ACTIVE = os.getenv("ROSTERING_ACTIVE", "false").lower() == "true"

# Separate gate for driver-facing DMs — the assignment matrix (_ACTIVE above)
# stays live independently of this. Defaults to false: driver DMs must not
# go out until the rostering pipeline has been fully tested end-to-end.
# Set DRIVER_DM_ACTIVE=true on Render to enable.
_DM_ACTIVE = os.getenv("DRIVER_DM_ACTIVE", "false").lower() == "true"

# Standing rank for quality tiers
_STANDING_RANK = {"Platinum": 4, "Gold": 3, "Silver": 2, "Bronze": 1}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _wave_lead_name(shift_date: date) -> tuple[str, str]:
    """Return (display_name, slack_user_id) for the on-duty wave lead."""
    wd = shift_date.weekday()   # 0=Mon … 6=Sun
    if wd in _SPENCER_DAYS and wd not in (_FABIAN_DAYS - {2}):
        return "Spencer Colby", SPENCER_ID
    return "Galo (Fabian Marcillo)", FABIAN_ID


def _calc_showtime(wave_time_str: Optional[str]) -> Optional[str]:
    """Return showtime = wave_time - SHOWTIME_OFFSET_MINUTES, formatted HH:MM AM/PM.
    Mirrors ingest/driver_schedule.py's _calculate_show_times, which does the
    same offset against wave-consolidated groups — keep the two in sync."""
    if not wave_time_str:
        return None
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
        try:
            t = datetime.strptime(wave_time_str.strip(), fmt)
            show = (t - timedelta(minutes=SHOWTIME_OFFSET_MINUTES)).strftime("%-I:%M %p")
            return show
        except ValueError:
            continue
    return None


def _showtime_to_minutes(showtime_str: str) -> Optional[int]:
    """Parse an 'H:MM AM/PM'-style showtime string to minutes since midnight,
    for comparing a night-before showtime against a day-of one. Returns None
    if unparseable rather than raising, since these strings come from free-
    form ingest data."""
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
        try:
            t = datetime.strptime(showtime_str.strip(), fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    return None


def _resolve_showtime(night_prior: Optional[str], day_of: Optional[str]) -> Optional[str]:
    """Reconcile the showtime shown on the day-of assignment DM.

    Prefer the day-of, DOP-wave-derived showtime when it's genuinely
    earlier than what the driver was told the night before (from
    DriverScheduleEntry) — a real schedule improvement should be passed
    along. Cap it at the night-before value whenever the day-of one would
    be later, or is missing/unparseable, or no comparison is possible.

    Hard rule: never show a time later than the night-before value. Quietly
    pushing a driver's showtime later than what they were already told is a
    real problem even if the day-of DOP wave technically justifies it — the
    driver should never find out their day got longer only by comparing two
    DMs themselves.
    """
    if not night_prior:
        return day_of
    if day_of:
        night_prior_mins = _showtime_to_minutes(night_prior)
        day_of_mins = _showtime_to_minutes(day_of)
        if night_prior_mins is not None and day_of_mins is not None and day_of_mins <= night_prior_mins:
            return day_of
    return night_prior


def _latest_quality_map(db: Session) -> dict[str, dict]:
    """Return {driver_name: {standing, score}} from the most recent quality snapshot."""
    latest = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.id.desc())
        .first()
    )
    if not latest:
        return {}
    rows = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.snapshot_id == latest.id)
        .all()
    )
    return {
        r.driver_name: {
            "standing": r.overall_standing or "Bronze",
            "score": float(r.overall_score or 0),
        }
        for r in rows
    }


def _standing_emoji(st: str) -> str:
    return {"Platinum": "💎", "Gold": "🥇", "Silver": "🥈", "Bronze": "🥉"}.get(st, "❔")


def _called_out_today(shift_date: date, db: Session) -> set[str]:
    """Return names of drivers who called out for shift_date."""
    rows = (
        db.query(AttendanceEvent.driver_name)
        .filter(
            AttendanceEvent.event_date == shift_date,
            AttendanceEvent.is_missed == True,
        )
        .all()
    )
    return {r.driver_name for r in rows}


# ─── ETA helper ──────────────────────────────────────────────────────────────

def _calc_eta_dt(snap: "CortexSnapshot", wave_str: Optional[str], shift_date: "date") -> Optional[datetime]:
    """
    Estimate when a driver will return to station based on their latest Cortex snapshot.

    Logic: pace (pkgs/hr) = delivered / elapsed_since_wave
           time_remaining  = remaining / pace
           eta             = snapshot_at + time_remaining + 0.5h (return drive buffer)

    Returns a naive-UTC datetime, or None if the route is already done or data is
    insufficient (caller distinguishes "done" via packages_remaining/pct_complete).
    """
    if not snap:
        return None

    delivered = snap.packages_delivered
    remaining = snap.packages_remaining
    snap_at   = snap.snapshot_at   # naive UTC from datetime.utcnow()

    if not delivered or not remaining or not snap_at or not wave_str:
        return None

    from zoneinfo import ZoneInfo as _ZI
    _PACIFIC = _ZI("America/Los_Angeles")
    _UTC = _ZI("UTC")

    # Parse wave time into a naive UTC datetime on shift_date
    wave_utc: Optional[datetime] = None
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
        try:
            parsed = datetime.strptime(wave_str.strip(), fmt)
            wave_local = datetime(
                shift_date.year, shift_date.month, shift_date.day,
                parsed.hour, parsed.minute, 0, tzinfo=_PACIFIC,
            )
            wave_utc = wave_local.astimezone(_UTC).replace(tzinfo=None)
            break
        except ValueError:
            continue

    if not wave_utc:
        return None

    elapsed_hours = (snap_at - wave_utc).total_seconds() / 3600
    if elapsed_hours <= 0.1:
        return None

    pace = delivered / elapsed_hours        # packages per hour
    if pace <= 0:
        return None

    hours_left = remaining / pace           # hours until finished
    return snap_at + timedelta(hours=hours_left + 0.5)   # +30 min return buffer


def _calc_eta(snap: "CortexSnapshot", wave_str: Optional[str], shift_date: "date") -> Optional[str]:
    """Pacific-time label for the ETA, e.g. "3:45 PM", "Done", or None. See _calc_eta_dt."""
    from zoneinfo import ZoneInfo as _ZI
    _PACIFIC = _ZI("America/Los_Angeles")
    _UTC = _ZI("UTC")

    if not snap:
        return None
    if (snap.packages_remaining or 0) == 0 and (snap.pct_complete or 0) >= 99:
        return "Done"

    eta_utc_naive = _calc_eta_dt(snap, wave_str, shift_date)
    if eta_utc_naive is None:
        return None

    eta_pt = eta_utc_naive.replace(tzinfo=_UTC).astimezone(_PACIFIC)
    return eta_pt.strftime("%-I:%M %p")


# ─── Roster suggestion builder ───────────────────────────────────────────────

def _build_roster_suggestion(shift_date: date, db: Session) -> list[dict]:
    """
    Return drivers scheduled for shift_date, ranked by quality standing.
    Each entry: {driver_name, standing, score, rank, constraints, callout}
    """
    scheduled = (
        db.query(DriverScheduleEntry)
        .filter(DriverScheduleEntry.schedule_date == shift_date)
        .order_by(DriverScheduleEntry.driver_name)
        .all()
    )
    if not scheduled:
        return []

    quality_map = _latest_quality_map(db)
    called_out = _called_out_today(shift_date, db)

    suggestions = []
    for entry in scheduled:
        name = entry.driver_name
        q = quality_map.get(name, {"standing": "Unknown", "score": 0.0})
        constraint = DRIVER_VAN_CONSTRAINTS.get(name)
        suggestions.append({
            "driver_name": name,
            "standing": q["standing"],
            "score": q["score"],
            "rank": _STANDING_RANK.get(q["standing"], 0),
            "wave_time": entry.wave_time,
            "show_time": _calc_showtime(entry.wave_time) or entry.show_time,
            "service_type": entry.service_type,
            "is_sweeper": entry.is_sweeper,
            "van_constraint": constraint,
            "called_out": name in called_out,
        })

    suggestions.sort(key=lambda x: (-x["rank"], -x["score"], x["driver_name"]))
    return suggestions


# ─── Nightly reminder (1900 PT) ──────────────────────────────────────────────

def send_nightly_roster_reminder(shift_date: date, db: Session) -> dict:
    """
    DM Spencer, Luis, and Fabian with suggested roster for shift_date.
    Deduped by date — safe to call repeatedly.
    Returns {"status": "sent"|"already_sent"|"inactive"|"no_schedule", ...}
    """
    if not _ACTIVE:
        return {"status": "inactive", "note": "Set ROSTERING_ACTIVE=true on Render to enable"}

    existing = db.query(NightlyRosterReminder).filter(
        NightlyRosterReminder.shift_date == shift_date
    ).first()
    if existing:
        return {"status": "already_sent", "date": shift_date.isoformat()}

    suggestions = _build_roster_suggestion(shift_date, db)
    if not suggestions:
        return {"status": "no_schedule", "date": shift_date.isoformat()}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    wave_lead_name, _ = _wave_lead_name(shift_date)

    # Build roster text
    lines = []
    for i, s in enumerate(suggestions, 1):
        flag = "⚠️ CALLED OUT" if s["called_out"] else ""
        constraint = f" | 🔒 {s['van_constraint']}" if s["van_constraint"] else ""
        sweeper = " | 🧹 Sweeper" if s["is_sweeper"] else ""
        standing_emoji = {"Platinum": "💎", "Gold": "🥇", "Silver": "🥈", "Bronze": "🥉"}.get(s["standing"], "❔")
        lines.append(
            f"  {i}. {standing_emoji} *{s['driver_name']}* — {s['standing']}{constraint}{sweeper} {flag}"
        )

    roster_text = "\n".join(lines)
    active_count = sum(1 for s in suggestions if not s["called_out"])

    text = (
        f"📋 *Nightly Roster Reminder — {date_str}*\n\n"
        f"The schedule for tomorrow needs to be uploaded to the system before you leave tonight.\n"
        f"Wave Lead: *{wave_lead_name}*\n\n"
        f"*Suggested rostering order ({active_count} available):*\n"
        f"{roster_text}\n\n"
        f"Once rostering is complete in Cortex, upload the Routes xlsx to "
        f"#nday-operations-management so driver DMs can go out."
    )

    ts_map: dict[str, Optional[str]] = {
        "spencer": None, "luis": None, "fabian": None
    }
    for key, uid in [("spencer", SPENCER_ID), ("luis", LUIS_ID), ("fabian", FABIAN_ID)]:
        try:
            resp = client.chat_postMessage(channel=uid, text=text)
            ts_map[key] = resp.get("ts")
        except Exception as exc:
            logger.warning("Nightly reminder DM to %s failed: %s", key, exc)

    record = NightlyRosterReminder(
        shift_date=shift_date,
        driver_count=len(suggestions),
        reminder_ts_spencer=ts_map["spencer"],
        reminder_ts_luis=ts_map["luis"],
        reminder_ts_fabian=ts_map["fabian"],
    )
    db.add(record)
    db.commit()

    return {
        "status": "sent",
        "date": shift_date.isoformat(),
        "driver_count": len(suggestions),
        "ts": ts_map,
    }


# ─── Driver shift DMs ────────────────────────────────────────────────────────

def _get_driver_slack_id(driver_name: str, db: Session) -> Optional[str]:
    """Look up Slack user ID from driver_roster (populated by SSN import script).

    2026-07-22 fix #1: this read entry.slack_user_id via getattr(..., None),
    but DriverRosterEntry's real column is slack_member_id (slack_user_id
    is a field on other models — User, DriverShiftDM — not this one). The
    getattr default silently masked the AttributeError, so this returned
    None for every real driver, every time.

    2026-07-22 fix #2: exact-match alone still missed most real drivers
    even after fix #1 — DailyRouteAssignment/DriverScheduleEntry driver
    names come from DOP/Cortex/the schedule file as "First Last", while
    DriverRosterEntry.payroll_name (ADP import) commonly includes a
    middle name ("First Middle Last"), e.g. "Austin Gilmore" (assignment)
    vs. "Austin Lee Gilmore" (roster) — confirmed live: 44 of 50 real
    routes failed with no_slack_id despite all 44 drivers being properly
    Slack-linked. Falls back to the same token-overlap match (>=2 shared
    name tokens) eod_survey.py and driver_matching.py already use for
    this exact mismatch, only after an exact match fails.
    """
    entry = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.payroll_name == driver_name, DriverRosterEntry.is_active == True)
        .first()
    )
    if entry:
        return entry.slack_member_id

    name_tokens = frozenset((driver_name or "").lower().replace(",", "").split())
    if not name_tokens:
        return None
    candidates = db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all()
    for c in candidates:
        c_tokens = frozenset((c.payroll_name or "").lower().replace(",", "").split())
        if len(name_tokens & c_tokens) >= 2:
            return c.slack_member_id
    return None


def _build_shift_dm(entry: DriverScheduleEntry, wave_lead_name: str, date_str: str, shift_date: date) -> tuple[str, list, Optional[str]]:
    """Build the (fallback_text, blocks, showtime) for one driver's
    night-before Showtime DM. Shared by send_driver_shift_dms() (the
    real, gated batch send) and send_test_shift_dm() (a single-target
    preview/test send bypassing DRIVER_DM_ACTIVE) so the two can never
    drift apart in content — same precedent as _build_driver_dm()."""
    name = entry.driver_name
    showtime = _calc_showtime(entry.wave_time) or entry.show_time
    wave_display = entry.wave_time or "TBD"
    service_display = entry.service_type or ("Sweeper — van assigned morning-of" if entry.is_sweeper else "TBD")

    text_fallback = (
        f"👋 Hi {name.split()[0]}! Your shift is tomorrow ({date_str}).\n"
        f"🕐 *Showtime:* {showtime or 'See dispatch'} | *Wave:* {wave_display}\n"
        f"🚐 *Van/Service:* {service_display}\n"
        f"👤 *Wave Lead:* {wave_lead_name}\n"
        f"Please acknowledge below — you'll confirm arrival separately, on the day-of "
        f"route assignment DM."
    )

    btn_value = json.dumps({"shift_date": shift_date.isoformat(), "driver_name": name})

    # Baked in at send-time (not issued fresh on click) so "Can't Make
    # It" can be a genuine one-click button straight to the callout
    # page — Slack still delivers the interaction payload to our
    # handler (for decline_shift()'s tracking) AND opens this URL in
    # the same click. Needs a much longer TTL than the standing
    # button's 4-hour click-time token, since this DM might sit unread
    # for hours before the driver taps it.
    from api.src.routes.slack_interactions import _issue_callout_token
    callout_token = _issue_callout_token(name, shift_date.isoformat(), ttl_hours=48)
    callout_url = f"{FRONTEND_URL}/callout?token={callout_token}"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"👋 *Shift Reminder — {date_str}*\n\n"
                    f"Hi {name.split()[0]}! Here are your details for tomorrow:"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Showtime:*\n{showtime or 'See dispatch'}"},
                {"type": "mrkdwn", "text": f"*Wave:*\n{wave_display}"},
                {"type": "mrkdwn", "text": f"*Van/Service:*\n{service_display}"},
                {"type": "mrkdwn", "text": f"*Wave Lead:*\n{wave_lead_name}"},
                {"type": "mrkdwn", "text": f"*Date:*\n{date_str}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📋  I've Got My Schedule", "emoji": True},
                    "style": "primary",
                    "action_id": "driver_schedule_ack",
                    "value": btn_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌  Can't Make It", "emoji": True},
                    "style": "danger",
                    "action_id": "driver_decline_shift",
                    "value": btn_value,
                    "url": callout_url,
                },
            ],
        },
    ]
    return text_fallback, blocks, showtime


def send_driver_shift_dms(shift_date: date, db: Session) -> dict:
    """
    Send pre-shift DMs to all drivers scheduled for shift_date.
    Each DM includes showtime, wave lead, and an arrival confirmation button.
    Safe to call multiple times — skips drivers already DM'd.
    Gated by DRIVER_DM_ACTIVE=true (independent of ROSTERING_ACTIVE, which
    gates the assignment matrix and stays live on its own schedule).
    """
    if not _DM_ACTIVE:
        return {"status": "inactive", "note": "Set DRIVER_DM_ACTIVE=true on Render to enable driver DMs"}

    scheduled = (
        db.query(DriverScheduleEntry)
        .filter(DriverScheduleEntry.schedule_date == shift_date)
        .all()
    )
    if not scheduled:
        return {"status": "no_schedule", "date": shift_date.isoformat()}

    # Already-sent set
    already_sent = {
        r.driver_name
        for r in db.query(DriverShiftDM.driver_name)
        .filter(DriverShiftDM.shift_date == shift_date, DriverShiftDM.dm_sent_at != None)
        .all()
    }

    called_out = _called_out_today(shift_date, db)
    wave_lead_name, _ = _wave_lead_name(shift_date)
    client = _slack_client()
    date_str = shift_date.strftime("%A, %B %-d")

    sent, skipped, no_slack = 0, 0, 0

    for entry in scheduled:
        name = entry.driver_name
        if name in already_sent or name in called_out:
            skipped += 1
            continue

        slack_id = _get_driver_slack_id(name, db)
        text_fallback, blocks, showtime = _build_shift_dm(entry, wave_lead_name, date_str, shift_date)

        dm_ts = None
        if client and slack_id:
            try:
                resp = client.chat_postMessage(channel=slack_id, text=text_fallback, blocks=blocks)
                dm_ts = resp.get("ts")
                sent += 1
            except Exception as exc:
                logger.warning("Driver DM failed for %s: %s", name, exc)
                no_slack += 1
        else:
            no_slack += 1

        dm_record = db.query(DriverShiftDM).filter(
            DriverShiftDM.shift_date == shift_date,
            DriverShiftDM.driver_name == name,
        ).first()
        if not dm_record:
            dm_record = DriverShiftDM(shift_date=shift_date, driver_name=name)
            db.add(dm_record)

        dm_record.slack_user_id = slack_id
        dm_record.wave_time = entry.wave_time
        dm_record.showtime = showtime
        dm_record.wave_lead = wave_lead_name
        dm_record.dm_ts = dm_ts
        dm_record.dm_sent_at = datetime.utcnow()

    db.commit()

    return {
        "status": "done",
        "date": shift_date.isoformat(),
        "sent": sent,
        "skipped": skipped,
        "no_slack_id": no_slack,
    }


def send_test_shift_dm(shift_date: date, sample_driver_name: str, target_slack_id: str, db: Session) -> dict:
    """Send ONE real driver's Showtime DM content to an arbitrary target
    (e.g. a live end-to-end test, or a manager doing a pre-launch check).

    Deliberately bypasses DRIVER_DM_ACTIVE — the whole point is to test
    before that gate is ever flipped on for everyone. Unlike
    send_test_driver_dm() (day-of DM preview), this does NOT redirect to
    a different reviewer than the sampled driver — the button values
    carry the real shift_date/driver_name from the DriverScheduleEntry,
    so clicking Acknowledge/Arrived/Can't Make It writes real records
    for that driver, same as a real send. Still subject to
    SLACK_NOTIFICATIONS_ACTIVE like every other send here."""
    entry = (
        db.query(DriverScheduleEntry)
        .filter(
            DriverScheduleEntry.schedule_date == shift_date,
            DriverScheduleEntry.driver_name == sample_driver_name,
        )
        .first()
    )
    if not entry:
        return {"status": "no_schedule_entry", "date": shift_date.isoformat(), "sample_driver_name": sample_driver_name}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    wave_lead_name, _ = _wave_lead_name(shift_date)
    date_str = shift_date.strftime("%A, %B %-d")
    text_fallback, blocks, _showtime = _build_shift_dm(entry, wave_lead_name, date_str, shift_date)

    try:
        client.chat_postMessage(channel=target_slack_id, text=text_fallback, blocks=blocks)
        return {"status": "sent", "target_slack_id": target_slack_id, "sample_driver_name": sample_driver_name, "date": shift_date.isoformat()}
    except Exception as exc:
        logger.warning("Test shift DM send failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ─── #nday-mgt summary matrix ────────────────────────────────────────────────

def post_mgt_summary(shift_date: date, db: Session, grounded_vans: Optional[list[str]] = None, force: bool = False) -> dict:
    """
    Post (or update) the daily assignment matrix to #nday-mgt.
    Includes extras, van constraint risks, callout impacts, and grounded-van flags.

    force=True clears any stored message ts for the day first, so this
    posts a genuinely new message instead of chat_update-ing the existing
    one in place — same silent-update problem post_showtime_summary's
    docstring describes (Slack doesn't move an edited message or notify
    on it, so a manual re-post request that just edits an hours-old
    message looks like nothing happened). The automatic post-ingest call
    (ops_ingest.py, same shift_date re-triggered as corrections land
    through the day) deliberately keeps force=False — editing in place
    there is the wanted behavior, not the bug.
    """
    if not _ACTIVE:
        return {"status": "inactive"}

    if force:
        existing_row = db.query(MgtSummaryPost).filter(MgtSummaryPost.shift_date == shift_date).first()
        if existing_row:
            existing_row.slack_ts = None

    suggestions = _build_roster_suggestion(shift_date, db)
    if not suggestions:
        return {"status": "no_schedule", "date": shift_date.isoformat()}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    wave_lead_name, _ = _wave_lead_name(shift_date)
    date_str = shift_date.strftime("%A, %B %-d")
    called_out = {s["driver_name"] for s in suggestions if s["called_out"]}

    # Build sections
    active   = [s for s in suggestions if not s["called_out"] and not s["is_sweeper"]]
    sweepers = [s for s in suggestions if not s["called_out"] and s["is_sweeper"]]
    absent   = [s for s in suggestions if s["called_out"]]

    risks: list[str] = []

    def _driver_line(s: dict) -> str:
        em = _standing_emoji(s["standing"])
        parts = [f"{em} {s['driver_name']} ({s['standing']})"]
        if s["van_constraint"]:
            parts.append(f"🔒 {s['van_constraint']}")
            risks.append(f"⚠️ {s['driver_name']} requires {s['van_constraint']} — confirm van available")
        wave = s.get("wave_time") or "?"
        parts.append(f"Wave {wave}")
        return " | ".join(parts)

    roster_lines = [_driver_line(s) for s in active]
    sweeper_lines = [_driver_line(s) for s in sweepers]
    absent_lines = [f"❌ {s['driver_name']} — Called Out" for s in absent]

    if absent:
        risks.append(f"⚠️ {len(absent)} driver(s) called out — roster may be short")

    if grounded_vans:
        for v in grounded_vans:
            risks.append(f"🚫 Grounded van reported: *{v}* — remove from assignment pool")

    # Identify drivers with no quality history
    no_quality = [s["driver_name"] for s in active if s["standing"] == "Unknown"]
    if no_quality:
        risks.append(f"❔ No quality data for: {', '.join(no_quality)}")

    risk_block = "\n".join(f"• {r}" for r in risks) if risks else "✅ No flags"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Daily Roster Matrix — {date_str}", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Wave Lead:*\n{wave_lead_name}"},
                {"type": "mrkdwn", "text": f"*Active Drivers:*\n{len(active)}"},
                {"type": "mrkdwn", "text": f"*Sweepers:*\n{len(sweepers)}"},
                {"type": "mrkdwn", "text": f"*Called Out:*\n{len(absent)}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🏁 Active Drivers (ranked by quality):*\n" + "\n".join(roster_lines) if roster_lines else "_None_"},
        },
    ]

    if sweeper_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🧹 Sweepers:*\n" + "\n".join(sweeper_lines)},
        })

    if absent_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*❌ Absent:*\n" + "\n".join(absent_lines)},
        })

    blocks += [
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚠️ Risk Flags:*\n{risk_block}"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Posted by NDAY Route Manager · {datetime.utcnow().strftime('%H:%M UTC')}_"}],
        },
    ]

    # Check for existing post to update vs new post
    existing = db.query(MgtSummaryPost).filter(
        MgtSummaryPost.shift_date == shift_date
    ).first()

    try:
        if existing and existing.slack_ts:
            resp = client.chat_update(
                channel=MGT_CHANNEL,
                ts=existing.slack_ts,
                text=f"Daily Roster Matrix — {date_str}",
                blocks=blocks,
            )
            slack_ts = existing.slack_ts
        else:
            resp = client.chat_postMessage(
                channel=MGT_CHANNEL,
                text=f"Daily Roster Matrix — {date_str}",
                blocks=blocks,
            )
            slack_ts = resp.get("ts")

        if not existing:
            existing = MgtSummaryPost(shift_date=shift_date)
            db.add(existing)
        existing.slack_ts = slack_ts
        existing.driver_count = len(active)
        existing.risk_flags = json.dumps(risks)
        existing.posted_at = datetime.utcnow()
        db.commit()

        return {"status": "posted", "date": shift_date.isoformat(), "slack_ts": slack_ts, "risks": risks}

    except Exception as exc:
        logger.error("MGT summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ─── Escalating "schedule not ingested yet" reminder (new) ───────────────────
# Separate from send_nightly_roster_reminder (DMs 3 named managers a ranked
# suggestion once at 19:00 — an individual actionable nudge) and from
# mgt_reminders.py's "schedule" check (DMs individual #nday-mgt members,
# stops on file *detection*, window closes 20:00). This is the louder,
# public, escalating layer on top: a #nday-mgt CHANNEL post once the
# deadline is actually being missed, gated by SCHEDULE_ESCALATION_ACTIVE
# (kept separate from ROSTERING_ACTIVE/DRIVER_DM_ACTIVE — same reasoning as
# why DRIVER_DM_ACTIVE was split out: don't let an unproven, publicly-
# visible behavior go live just because another flag happens to be on).

_ESCALATION_ACTIVE = os.getenv("SCHEDULE_ESCALATION_ACTIVE", "false").lower() == "true"


def _tomorrow_schedule_landed(shift_date: date, db: Session) -> bool:
    """True once DriverScheduleEntry rows for shift_date carry a real
    wave/show time — not just bare name rows, which can exist early from a
    multi-week upload whose primary (annotated) date is a different day."""
    return db.query(DriverScheduleEntry).filter(
        DriverScheduleEntry.schedule_date == shift_date,
        or_(DriverScheduleEntry.wave_time.isnot(None), DriverScheduleEntry.show_time.isnot(None)),
    ).first() is not None


def run_schedule_escalation_check(db: Session) -> dict:
    """Called every ~60s from main.py's _schedule_escalation_loop. Posts an
    escalating nag to #nday-mgt: 15-min cadence 19:00-20:00 PT, then 5-min
    cadence with no upper bound — keeps firing until tomorrow's schedule
    has landed, even overnight, per explicit direction."""
    if not _ESCALATION_ACTIVE:
        return {"status": "inactive"}

    from zoneinfo import ZoneInfo as _ZI
    pacific = _ZI("America/Los_Angeles")
    now = datetime.now(pacific)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    if (now.hour, now.minute) < (19, 0):
        return {"status": "outside_window"}

    key = "schedule_escalation"
    raw = get_reminder_state(db, key)
    last_sent_at = datetime.fromisoformat(raw["last_sent_at"]) if raw.get("last_sent_at") else None
    resolved_date = date.fromisoformat(raw["resolved_date"]) if raw.get("resolved_date") else None

    if resolved_date == today:
        return {"status": "already_resolved"}

    if _tomorrow_schedule_landed(tomorrow, db):
        set_reminder_state(db, key, {
            "last_sent_at": last_sent_at.isoformat() if last_sent_at else None,
            "resolved_date": today.isoformat(),
        })
        return {"status": "resolved", "date": tomorrow.isoformat()}

    interval_seconds = 900 if now.hour < 20 else 300
    if last_sent_at and (now - last_sent_at).total_seconds() < interval_seconds:
        return {"status": "throttled"}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    urgent = now.hour >= 20
    prefix = ":rotating_light: *Still no schedule for tomorrow* :rotating_light:" if urgent else ":alarm_clock: *Tomorrow's schedule isn't in yet*"
    text = f"{prefix}\n{tomorrow.strftime('%A, %B %-d')}'s driver schedule hasn't been uploaded. Please post it as soon as it's available."

    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Schedule escalation post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, key, {"last_sent_at": now.isoformat(), "resolved_date": None})
    return {"status": "sent", "hour": now.hour}


# ─── Showtime summary to #nday-mgt (new, posted once the schedule lands) ────

def post_showtime_summary(shift_date: date, db: Session, force: bool = False) -> dict:
    """Post a Showtime-grouped breakdown to #nday-mgt AND #nday-team-room
    (driver-facing): drivers bucketed by show_time, name only — no van,
    service type, or sweeper tag per explicit 2026-07-20 decision (van
    assignment is the assignment matrix's job, a deliberately different
    lens). Sweepers land in the final wave's bucket automatically (their
    show_time now equals it, per the ingest fix). A different lens than
    post_mgt_summary's quality/risk matrix — same precedent as
    post_assignment_matrix being its own function.

    Deliberately does NOT include performance/safety metrics (unlike
    post_driver_summary_matrix) — this is the one roster-facing summary
    that's safe to show drivers directly, per explicit 2026-07-16
    decision to add #nday-team-room as a second destination.

    force=True clears any stored message ts for the day first, so this
    posts a genuinely NEW message instead of chat_update-ing the existing
    one in place. Slack does not move an edited message or notify on it,
    so silently updating something posted hours earlier looks exactly
    like "nothing happened" to someone expecting a fresh post at the
    bottom of the channel (confirmed live 2026-07-20 — the Re-Publish
    button correctly found and updated an old ts from manual testing
    earlier the same day, invisibly, from the clicker's perspective)."""
    if not _ACTIVE:
        return {"status": "inactive"}

    if force:
        set_reminder_state(db, f"showtime_summary_{shift_date.isoformat()}", {"mgt_slack_ts": None, "team_slack_ts": None})

    suggestions = _build_roster_suggestion(shift_date, db)
    if not suggestions:
        return {"status": "no_schedule", "date": shift_date.isoformat()}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    active = [s for s in suggestions if not s["called_out"]]

    buckets: dict[str, list[dict]] = {}
    for s in active:
        key = s.get("show_time") or "TBD"
        buckets.setdefault(key, []).append(s)

    def _sort_key(show_time: str) -> int:
        if show_time == "TBD":
            return 24 * 60
        for fmt in ("%I:%M %p", "%-I:%M %p"):
            try:
                t = datetime.strptime(show_time, fmt)
                return t.hour * 60 + t.minute
            except ValueError:
                continue
        return 24 * 60

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🕐 Showtime Summary — {date_str}", "emoji": True},
        },
    ]

    # Slack rejects any block whose text exceeds 3000 characters
    # (invalid_blocks) -- a single showtime bucket with enough drivers
    # crammed into one section can silently blow past that and fail the
    # ENTIRE post (confirmed live 2026-07-20: a bucket large enough to hit
    # this took down the whole showtime summary). Chunk each bucket across
    # as many section blocks as needed, well under the limit.
    _BLOCK_TEXT_LIMIT = 2800

    def _first_name(driver_name: str) -> str:
        driver_name = driver_name or ""
        parts = driver_name.split(",", 1)[1].strip().split() if "," in driver_name else driver_name.split()
        return parts[0].lower() if parts else ""

    for show_time in sorted(buckets, key=_sort_key):
        header_line = f"*Showtime {show_time}* ({len(buckets[show_time])})"
        chunk_lines: list[str] = []
        chunk_len = len(header_line) + 1
        first_chunk = True
        bucket_drivers = sorted(buckets[show_time], key=lambda s: _first_name(s["driver_name"]))

        def _flush():
            nonlocal chunk_lines, chunk_len, first_chunk
            if not chunk_lines:
                return
            prefix = f"{header_line}\n" if first_chunk else f"*Showtime {show_time} (cont'd)*\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": prefix + "\n".join(chunk_lines)},
            })
            chunk_lines = []
            chunk_len = len(header_line) + 1
            first_chunk = False

        for s in bucket_drivers:
            # Name + showtime only, per explicit 2026-07-20 decision — no
            # service type, van, or sweeper tag on this report (that's the
            # assignment matrix's job, a different lens on purpose).
            name = _truncate(s["driver_name"], 22)
            line = f"• {name}"
            if chunk_len + len(line) + 1 > _BLOCK_TEXT_LIMIT:
                _flush()
            chunk_lines.append(line)
            chunk_len += len(line) + 1
        _flush()

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_Posted by NDAY Route Manager · {datetime.utcnow().strftime('%H:%M UTC')}_"}],
    })

    state_key = f"showtime_summary_{shift_date.isoformat()}"
    raw = get_reminder_state(db, state_key)

    def _post_or_update(channel: str, ts_key: str) -> Optional[str]:
        existing_ts = raw.get(ts_key)
        if existing_ts:
            client.chat_update(channel=channel, ts=existing_ts, text=f"Showtime Summary — {date_str}", blocks=blocks)
            return existing_ts
        resp = client.chat_postMessage(channel=channel, text=f"Showtime Summary — {date_str}", blocks=blocks)
        return resp.get("ts")

    try:
        mgt_ts = _post_or_update(MGT_CHANNEL, "mgt_slack_ts")
        team_ts = None
        if TEAM_ROOM_MESSAGES_ACTIVE:
            try:
                team_ts = _post_or_update(TEAM_CHANNEL, "team_slack_ts")
            except Exception as exc:
                logger.warning("Showtime summary post to #nday-team-room failed: %s", exc)
        set_reminder_state(db, state_key, {"mgt_slack_ts": mgt_ts, "team_slack_ts": team_ts})
        return {"status": "posted", "date": shift_date.isoformat(), "mgt_slack_ts": mgt_ts, "team_slack_ts": team_ts}
    except Exception as exc:
        logger.error("Showtime summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ─── "Potential gap" soft alert (new, soft v1 — no automated callout) ────────

def send_schedule_gap_alert(shift_date: date, db: Session) -> dict:
    """Single daily check (not a repeating nag): if any scheduled drivers
    haven't tapped Confirm yet, post one summary to #nday-mgt naming them.
    Soft v1 per explicit direction — assumes they're still showing, does
    NOT touch AttendanceEvent/CalloutQueue. The future "tighten this up"
    hook, when ready, is attendance.py's queue_callout_notification()
    after creating an AttendanceEvent row with a new reason_code — not
    built now."""
    if not _ESCALATION_ACTIVE:
        return {"status": "inactive"}

    scheduled = (
        db.query(DriverScheduleEntry)
        .filter(DriverScheduleEntry.schedule_date == shift_date)
        .all()
    )
    if not scheduled:
        return {"status": "no_schedule", "date": shift_date.isoformat()}

    state_key = f"schedule_gap_alert_{shift_date.isoformat()}"
    if get_reminder_state(db, state_key).get("sent"):
        return {"status": "already_sent"}

    called_out = _called_out_today(shift_date, db)
    dm_by_driver = {
        d.driver_name: d
        for d in db.query(DriverShiftDM).filter(DriverShiftDM.shift_date == shift_date).all()
    }
    unconfirmed = [
        e.driver_name for e in scheduled
        if e.driver_name not in called_out
        and getattr(dm_by_driver.get(e.driver_name), "schedule_acked_at", None) is None
    ]
    if not unconfirmed:
        set_reminder_state(db, state_key, {"sent": True})
        return {"status": "none_unconfirmed"}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    names = "\n".join(f"• {n}" for n in sorted(unconfirmed))
    text = (
        f":warning: *Potential gap — {date_str}*\n"
        f"{len(unconfirmed)} scheduled driver(s) haven't confirmed their schedule yet "
        f"(assuming they're still showing):\n{names}"
    )

    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Schedule gap alert post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent": True})
    return {"status": "sent", "unconfirmed_count": len(unconfirmed)}


# ─── #nday-mgt day-of assignment matrix (driver / route / van / est. return) ─

def _first_name(driver_name: str) -> str:
    """Extract first name from 'Last, First' or 'First Last' format for sorting/display."""
    if not driver_name:
        return ""
    if "," in driver_name:
        parts = driver_name.split(",", 1)[1].strip().split()
    else:
        parts = driver_name.strip().split()
    return parts[0] if parts else driver_name.strip()


def _full_name(driver_name: str) -> str:
    """Normalize 'Last, First' or 'First Last' into 'First Last' for display."""
    if not driver_name:
        return ""
    if "," in driver_name:
        last, first = driver_name.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return driver_name.strip()


def _truncate(s: str, width: int) -> str:
    """Truncate with an ellipsis so a long name can't push a monospace
    Slack table's other columns out of alignment (Python's :<N format
    spec pads to a minimum width but never truncates on its own)."""
    if len(s) <= width:
        return s
    return s[: width - 1].rstrip() + "…"


def _build_assignment_matrix_blocks(shift_date: date, assignments: list, db: Session) -> tuple[list, str]:
    """Pure block builder shared by post_assignment_matrix() (#nday-mgt,
    idempotent) and send_assignment_matrix_to_channel() (manual, any
    channel, always fresh) — same table content either way."""
    def _sort_key(a):
        wave_dt = _parse_wave_dt(a.wave or "")
        return (wave_dt is None, wave_dt or datetime.max, _first_name(a.driver_name).lower())

    assignments = sorted(assignments, key=_sort_key)

    quality_map = _latest_quality_map(db)

    date_str = shift_date.strftime("%A, %B %-d")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🗂️ Assignment Matrix — {date_str}", "emoji": True},
        },
    ]

    col_header = f"{'Driver':<22} {'Route':<10} {'Van':<10} {'Stg Loc':<14} {'Return':<9} {'Perf'}"

    def _flush(wave_label: str, rows: list[str]):
        if not rows:
            return
        header = f"Wave {wave_label}" if wave_label else "Wave Unassigned"
        table = "\n".join([col_header, "-" * len(col_header)] + rows)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{header}*\n```{table}```"},
        })

    wave_rows: list[str] = []
    active_wave = None
    first_group = True
    for a in assignments:
        wave_label = a.wave or ""
        if not first_group and wave_label != active_wave:
            _flush(active_wave, wave_rows)
            wave_rows = []
        active_wave = wave_label
        first_group = False
        name = _truncate(_full_name(a.driver_name), 22)
        return_time = _calc_return_time(a.wave or "", a.route_duration) or "—"
        standing = quality_map.get(a.driver_name, {}).get("standing", "Unk")
        wave_rows.append(
            f"{name:<22} {a.route_code or '—':<10} {a.van_number or '—':<10} {a.stage_location or '—':<14} {return_time:<9} {standing}"
        )
    _flush(active_wave, wave_rows)

    # ── Sweepers — scheduled today but no fixed route yet ────────────────
    # Pulled from DriverScheduleEntry.is_sweeper (set explicitly from the
    # schedule file), not DailyRouteAssignment — sweepers have no route/van
    # by definition, so they'd otherwise never appear in this matrix at all.
    assigned_lower = {(a.driver_name or "").lower() for a in assignments}
    sweepers = (
        db.query(DriverScheduleEntry)
        .filter(
            DriverScheduleEntry.schedule_date == shift_date,
            DriverScheduleEntry.is_sweeper == True,  # noqa: E712
        )
        .order_by(DriverScheduleEntry.driver_name)
        .all()
    )
    sweeper_rows = [
        f"{_truncate(_full_name(s.driver_name), 22):<22} {s.show_time or '—':<10} standing by for dispatch"
        for s in sweepers
        if (s.driver_name or "").lower() not in assigned_lower
    ]
    if sweeper_rows:
        sweeper_header = f"{'Driver':<22} {'Show':<10}"
        sweeper_table = "\n".join([sweeper_header, "-" * len(sweeper_header)] + sweeper_rows)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🧹 Sweepers*\n```{sweeper_table}```"},
        })

    # ── Team-aggregate performance line ──────────────────────────────────
    scored = [quality_map[a.driver_name]["score"] for a in assignments if a.driver_name in quality_map]
    standings = [quality_map[a.driver_name]["standing"] for a in assignments if a.driver_name in quality_map]
    no_data = len(assignments) - len(scored)
    if scored:
        avg_score = sum(scored) / len(scored)
        counts: dict[str, int] = {}
        for st in standings:
            counts[st] = counts.get(st, 0) + 1
        breakdown = " · ".join(
            f"{_standing_emoji(st)} {ct} {st}"
            for st, ct in sorted(counts.items(), key=lambda kv: -_STANDING_RANK.get(kv[0], 0))
        )
        team_line = f"*Team Performance:* Avg score {avg_score:.1f} · {breakdown}"
        if no_data:
            team_line += f" · ❔ {no_data} No Data"
    else:
        team_line = "*Team Performance:* No quality data available for today's roster"

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": team_line},
    })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_Posted by NDAY Route Manager · {datetime.utcnow().strftime('%H:%M UTC')}_"}],
    })

    return blocks, date_str


def post_assignment_matrix(shift_date: date, db: Session, force: bool = False) -> dict:
    """
    Post the day-of assignment matrix to #nday-mgt: every driver's route, van,
    and estimated return time — the same data sent in each driver's DM —
    grouped and sorted by wave, then by driver first name within each wave.

    Idempotent per shift_date (via a synthetic SlackIngestLog entry) so it's
    safe to call from multiple trigger points (post-Cortex-ingest, post-finalize)
    without double-posting.
    """
    if not _ACTIVE:
        return {"status": "inactive", "note": "Set ROSTERING_ACTIVE=true on Render to enable"}

    fake_id = f"assignment_matrix_{shift_date.isoformat()}"
    existing_log = db.query(SlackIngestLog).filter(SlackIngestLog.slack_file_id == fake_id).first()
    if existing_log:
        if not force:
            return {"status": "already_posted", "date": shift_date.isoformat()}
        db.delete(existing_log)
        db.commit()

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .all()
    )
    if not assignments:
        return {"status": "no_assignments", "date": shift_date.isoformat()}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    blocks, date_str = _build_assignment_matrix_blocks(shift_date, assignments, db)

    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=f"Assignment Matrix — {date_str}",
            blocks=blocks,
        )
        db.add(SlackIngestLog(
            ingest_date=shift_date,
            file_type="assignment_matrix",
            slack_file_id=fake_id,
            filename=fake_id,
            processed_at=datetime.utcnow(),
        ))
        db.commit()
        return {"status": "posted", "date": shift_date.isoformat(), "drivers": len(assignments)}
    except Exception as exc:
        logger.error("Assignment matrix post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def send_assignment_matrix_to_channel(shift_date: date, db: Session, channel_id: str) -> dict:
    """Manual, on-demand send of the same assignment matrix to an arbitrary
    channel (e.g. the Dispatch Home 'Send Route Matrix' button) — always
    posts fresh, no idempotency guard, since this is an explicit per-click
    action rather than an automatic per-day trigger like
    post_assignment_matrix()."""
    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .all()
    )
    if not assignments:
        return {"status": "no_assignments", "date": shift_date.isoformat()}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    blocks, date_str = _build_assignment_matrix_blocks(shift_date, assignments, db)

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=f"Assignment Matrix — {date_str}",
            blocks=blocks,
        )
        return {"status": "posted", "date": shift_date.isoformat(), "drivers": len(assignments), "channel": channel_id}
    except Exception as exc:
        logger.error("Assignment matrix send-to-channel failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _latest_dvic_map(db: Session) -> dict[str, dict]:
    """{transporter_name: {"avg_seconds", "instances"}} from the most
    recently ingested DVIC week only (per explicit request 2026-07-14:
    "use the avg and the most recent previous dvic" — not the persistent
    counseling stage). Exact-name-match convention, same limitation as
    _latest_quality_map() (no fuzzy matching). Reuses dvic.py's own
    per-driver aggregation so the two never compute this differently."""
    from api.src.database import DvicSnapshot
    from api.src.routes.dvic import _mgt_summary_rows
    latest = db.query(DvicSnapshot).order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
    if not latest:
        return {}
    return {row["name"]: row for row in _mgt_summary_rows(latest.id, db)}


def post_driver_summary_matrix(shift_date: date, db: Session, force: bool = False) -> dict:
    """
    Post a #nday-mgt table containing every field each driver's individual
    DM would show, plus Performance (quality standing, same source as the
    route summary matrix's Perf column) and Safety (avg pre-trip inspection
    time + instance count from the most recently ingested DVIC week — per
    explicit request 2026-07-14: "use the avg and the most recent previous
    dvic") — content spec: Governance/DRIVER_DM_CONTENT_RULES.md), grouped
    by wave with the wave lead noted per group.

    Stand-in for the real per-driver DMs while driver Slack-linking is
    incomplete (see /drivers — 0 linked as of 2026-07-14) — management gets
    full visibility into exactly what drivers would receive, without
    needing a Slack account on file for each one. Gated by ROSTERING_ACTIVE
    (management-facing, like the route summary matrix) — NOT DRIVER_DM_ACTIVE,
    since nothing here is sent to a driver.

    Idempotent per shift_date via a synthetic SlackIngestLog entry, same
    pattern as post_assignment_matrix().
    """
    if not _ACTIVE:
        return {"status": "inactive", "note": "Set ROSTERING_ACTIVE=true on Render to enable"}

    fake_id = f"driver_summary_matrix_{shift_date.isoformat()}"
    existing_log = db.query(SlackIngestLog).filter(SlackIngestLog.slack_file_id == fake_id).first()
    if existing_log:
        if not force:
            return {"status": "already_posted", "date": shift_date.isoformat()}
        db.delete(existing_log)
        db.commit()

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .all()
    )
    if not assignments:
        return {"status": "no_assignments", "date": shift_date.isoformat()}

    def _sort_key(a):
        wave_dt = _parse_wave_dt(a.wave or "")
        return (wave_dt is None, wave_dt or datetime.max, _first_name(a.driver_name).lower())

    assignments.sort(key=_sort_key)

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    quality_map = _latest_quality_map(db)
    dvic_map = _latest_dvic_map(db)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 Driver Summary — {date_str}", "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Every field each driver's individual DM shows, plus performance + safety. Sent here while driver Slack-linking is incomplete._"}],
        },
    ]

    col_header = f"{'Driver':<22} {'Route':<8} {'Van':<9} {'Staging':<12} {'Showtime':<9} {'Return':<9} {'Perf':<8} {'Safety':<9} {'ACE'}"

    def _flush(wave_label: str, rows: list[str]):
        if not rows:
            return
        wave_lead_name, _ = _wave_lead_name(shift_date)
        header = f"Wave {wave_label} · Wave Lead: {wave_lead_name}" if wave_label else "Wave Unassigned"
        table = "\n".join([col_header, "-" * len(col_header)] + rows)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{header}*\n```{table}```"},
        })

    wave_rows: list[str] = []
    active_wave = None
    first_group = True
    for a in assignments:
        wave_label = a.wave or ""
        if not first_group and wave_label != active_wave:
            _flush(active_wave, wave_rows)
            wave_rows = []
        active_wave = wave_label
        first_group = False
        name = _truncate(_full_name(a.driver_name), 22)
        showtime = _calc_showtime(a.wave) or "—"
        return_time = _calc_return_time(a.wave or "", a.route_duration) or "—"
        standing = quality_map.get(a.driver_name, {}).get("standing", "Unk")
        dvic_row = dvic_map.get(a.driver_name)
        if dvic_row and dvic_row.get("avg_seconds") is not None:
            safety_val = f"{dvic_row['avg_seconds']:.0f}s/{dvic_row['instances']}x"
        else:
            safety_val = "—"
        wave_rows.append(
            f"{name:<22} {a.route_code or '—':<8} {a.van_number or '—':<9} {a.stage_location or '—':<12} "
            f"{showtime:<9} {return_time:<9} {standing:<8} {safety_val:<9} TBD"
        )
    _flush(active_wave, wave_rows)

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_Posted by NDAY Route Manager · {datetime.utcnow().strftime('%H:%M UTC')}_"}],
    })

    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=f"Driver Summary — {date_str}",
            blocks=blocks,
        )
        db.add(SlackIngestLog(
            ingest_date=shift_date,
            file_type="driver_summary_matrix",
            slack_file_id=fake_id,
            filename=fake_id,
            processed_at=datetime.utcnow(),
        ))
        db.commit()
        return {"status": "posted", "date": shift_date.isoformat(), "drivers": len(assignments)}
    except Exception as exc:
        logger.error("Driver summary matrix post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ─── Wave lead notifications ─────────────────────────────────────────────────

def _wave_assignments(shift_date: date, wave_time_str: str, db: Session) -> list:
    """All DailyRouteAssignment rows for a specific wave on shift_date."""
    return (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.wave == wave_time_str,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .order_by(DailyRouteAssignment.driver_name)
        .all()
    )


def _arrived_names(shift_date: date, db: Session) -> set[str]:
    """Set of driver names who have confirmed arrival for shift_date."""
    rows = (
        db.query(DriverShiftDM.driver_name)
        .filter(
            DriverShiftDM.shift_date == shift_date,
            DriverShiftDM.arrival_confirmed == True,
        )
        .all()
    )
    return {r.driver_name for r in rows}


def send_wave_lead_pre_wave_dm(shift_date: date, wave_time_str: str, db: Session) -> bool:
    """
    Send the wave lead a briefing DM 10 minutes before their wave.
    Lists every driver on that wave with their route, van, and staging.
    Deduped — fires once per wave per day.
    Gated by ROSTERING_ACTIVE=true.
    """
    if not _ACTIVE:
        return False

    # Dedup check
    already = db.query(WaveLeadNotification).filter(
        WaveLeadNotification.shift_date == shift_date,
        WaveLeadNotification.wave_time == wave_time_str,
        WaveLeadNotification.notif_type == "pre_wave",
    ).first()
    if already:
        return False

    assignments = _wave_assignments(shift_date, wave_time_str, db)
    if not assignments:
        return False

    _, wave_lead_id = _wave_lead_name(shift_date)
    client = _slack_client()
    if not client:
        return False

    date_str = shift_date.strftime("%A, %B %-d")
    arrived = _arrived_names(shift_date, db)

    lines = []
    for a in assignments:
        icon = "✅" if a.driver_name in arrived else "⏳"
        parts = [f"{icon} *{a.driver_name}*"]
        if a.route_code:
            parts.append(a.route_code)
        if a.van_number:
            parts.append(a.van_number)
        if a.stage_location:
            parts.append(a.stage_location)
        lines.append(" | ".join(parts))

    present_count = sum(1 for a in assignments if a.driver_name in arrived)
    total = len(assignments)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📋 Wave Briefing — {wave_time_str}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{date_str} · {wave_time_str} wave launches in ~10 minutes*\n"
                    f"Confirmed: *{present_count}/{total}* drivers on-site"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(lines) or "_No driver assignments found_",
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "✅ = arrived  ⏳ = not yet confirmed  —  You'll get a ping as each driver checks in."},
            ],
        },
    ]

    try:
        resp = client.chat_postMessage(
            channel=wave_lead_id,
            text=f"Wave briefing for {wave_time_str} — {present_count}/{total} confirmed",
            blocks=blocks,
        )
        db.add(WaveLeadNotification(
            shift_date=shift_date,
            wave_time=wave_time_str,
            notif_type="pre_wave",
            slack_ts=resp.get("ts"),
            wave_lead_slack_id=wave_lead_id,
        ))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Wave lead pre-wave DM failed: %s", exc)
        return False


def notify_wave_lead_driver_arrived(
    shift_date: date, driver_name: str, db: Session
) -> None:
    """
    Ping the wave lead immediately when a driver taps 'I Have Arrived'.
    Not deduped — each arrival is a distinct event.
    Gated by ROSTERING_ACTIVE=true.
    """
    if not _ACTIVE:
        return

    _, wave_lead_id = _wave_lead_name(shift_date)
    client = _slack_client()
    if not client:
        return

    # Look up this driver's assignment for context
    assignment = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name == driver_name,
        )
        .first()
    )

    # Tally arrived/total for this wave
    wave_time_str = assignment.wave if assignment else None
    total_on_wave = 0
    arrived_on_wave = 0
    if wave_time_str:
        wave_assignments = _wave_assignments(shift_date, wave_time_str, db)
        arrived = _arrived_names(shift_date, db)
        # Include the driver who just arrived
        arrived.add(driver_name)
        total_on_wave = len(wave_assignments)
        arrived_on_wave = sum(1 for a in wave_assignments if a.driver_name in arrived)

    now_pt_str = datetime.utcnow().strftime("%-I:%M %p") + " UTC"
    detail_parts = []
    if assignment:
        if assignment.route_code:
            detail_parts.append(f"Route *{assignment.route_code}*")
        if assignment.van_number:
            detail_parts.append(f"Van *{assignment.van_number}*")
        if assignment.stops or assignment.packages:
            detail_parts.append(f"{assignment.stops or assignment.packages} stops")
    detail_str = " · ".join(detail_parts) if detail_parts else ""

    wave_tally = f"  ({arrived_on_wave}/{total_on_wave} on {wave_time_str} wave)" if wave_time_str else ""

    text = f"✅ *{driver_name}* arrived at {now_pt_str}{wave_tally}"
    if detail_str:
        text += f"\n{detail_str}"

    try:
        client.chat_postMessage(channel=wave_lead_id, text=text)
    except Exception as exc:
        logger.warning("Wave lead arrival ping failed: %s", exc)


def send_missing_drivers_summary(shift_date: date, wave_time_str: str, db: Session) -> bool:
    """
    Send the wave lead a missing-drivers summary at wave time.
    Lists everyone on that wave who has NOT confirmed arrival.
    Deduped — fires once per wave per day.
    Gated by ROSTERING_ACTIVE=true.
    """
    if not _ACTIVE:
        return False

    already = db.query(WaveLeadNotification).filter(
        WaveLeadNotification.shift_date == shift_date,
        WaveLeadNotification.wave_time == wave_time_str,
        WaveLeadNotification.notif_type == "missing_summary",
    ).first()
    if already:
        return False

    assignments = _wave_assignments(shift_date, wave_time_str, db)
    if not assignments:
        return False

    _, wave_lead_id = _wave_lead_name(shift_date)
    client = _slack_client()
    if not client:
        return False

    arrived = _arrived_names(shift_date, db)
    missing = [a for a in assignments if a.driver_name not in arrived]
    present_count = len(assignments) - len(missing)
    total = len(assignments)

    if not missing:
        text = f"✅ *All {total} drivers confirmed for {wave_time_str} wave.* Full roster accounted for!"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    else:
        missing_lines = []
        for a in missing:
            parts = [f"• *{a.driver_name}*"]
            if a.route_code:
                parts.append(f"Route {a.route_code}")
            if a.van_number:
                parts.append(f"Van {a.van_number}")
            missing_lines.append(" — ".join(parts))

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 Missing Drivers — {wave_time_str} Wave",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Wave time has arrived.* "
                        f"{present_count}/{total} drivers confirmed on-site.\n\n"
                        f"*Not yet confirmed ({len(missing)}):*\n"
                        + "\n".join(missing_lines)
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "_Check attendance system or contact drivers directly._"},
                ],
            },
        ]

    try:
        resp = client.chat_postMessage(
            channel=wave_lead_id,
            text=f"Missing drivers for {wave_time_str} wave: {len(missing)}/{total}",
            blocks=blocks,
        )
        db.add(WaveLeadNotification(
            shift_date=shift_date,
            wave_time=wave_time_str,
            notif_type="missing_summary",
            slack_ts=resp.get("ts"),
            wave_lead_slack_id=wave_lead_id,
        ))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Missing drivers summary failed: %s", exc)
        return False


# ─── Arrival confirmation (called from slack_interactions.py) ────────────────

def mark_driver_arrived(shift_date_str: str, driver_name: str, slack_user_id: str, db: Session) -> bool:
    """Record driver arrival from the Slack button tap and ping the wave lead."""
    try:
        shift_date = date.fromisoformat(shift_date_str)
    except ValueError:
        return False

    record = db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == shift_date,
        DriverShiftDM.driver_name == driver_name,
    ).first()

    if not record:
        record = DriverShiftDM(shift_date=shift_date, driver_name=driver_name)
        db.add(record)

    record.arrived_at = datetime.utcnow()
    record.arrived_slack_user_id = slack_user_id
    record.arrival_confirmed = True
    db.commit()

    # Ping the wave lead with this arrival
    try:
        notify_wave_lead_driver_arrived(shift_date, driver_name, db)
    except Exception as exc:
        logger.warning("Wave lead arrival ping error: %s", exc)

    # #nday-mgt visibility comes from the periodic (every 30 min)
    # consolidated response summary (refresh_all_dm_response_summaries in
    # main.py's loop), not a message per arrival — avoids a new post for
    # every one of dozens of drivers checking in around the same time.

    return True


# ─── Day-of DMs (route + van + staging + packages) ──────────────────────────

def _parse_wave_dt(wave_str: str) -> Optional[datetime]:
    """Parse a wave time string into a datetime (date portion ignored)."""
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p", "%-I:%M %p"):
        try:
            return datetime.strptime(wave_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _calc_return_time(wave_str: str, duration_minutes: Optional[int]) -> Optional[str]:
    """Expected return = wave + route_duration - 30 min."""
    if not wave_str or not duration_minutes:
        return None
    dt = _parse_wave_dt(wave_str)
    if not dt:
        return None
    return (dt + timedelta(minutes=int(duration_minutes) - 30)).strftime("%-I:%M %p")


def _build_driver_dm(a: DailyRouteAssignment, wave_lead_name: str, date_str: str, wave_lead_slack_id: Optional[str] = None, db: Optional[Session] = None) -> tuple[str, list]:
    """Build the (fallback_text, blocks) for one driver's day-of assignment
    DM. Shared by send_day_of_dms() (the real, gated send) and the
    /day-of-dms/test endpoint (an explicit preview send to a reviewer,
    bypassing DRIVER_DM_ACTIVE) so the two can never drift apart in content.
    Content spec: Governance/DRIVER_DM_CONTENT_RULES.md.

    wave_lead_slack_id is only passed once LEAD_ROUTING_ACTIVE is on (see
    driver_lead_schedule.py) — when present, a "Talk to My Lead" button
    replaces the "contact your wave lead on Zello" text, per
    Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md §7.

    Showtime is resolved via _resolve_showtime(): the night-before value
    from DriverScheduleEntry (what the driver was already told) wins over
    the day-of DOP-wave-derived value whenever both exist — see
    _resolve_showtime()'s docstring for why. db is optional only so callers
    that truly have no session can still get the day-of-only fallback.
    """
    first_name = a.driver_name.split(",")[1].strip().split()[0] if "," in (a.driver_name or "") else (a.driver_name or "Driver").split()[0]

    night_prior_showtime = None
    if db is not None:
        schedule_entry = (
            db.query(DriverScheduleEntry)
            .filter(
                DriverScheduleEntry.schedule_date == a.assignment_date,
                DriverScheduleEntry.driver_name == a.driver_name,
            )
            .first()
        )
        if not schedule_entry:
            # Same middle-name mismatch confirmed for _get_driver_slack_id()
            # (e.g. "Ross Michael Reinberg" in the schedule file vs. "Ross
            # Reinberg" in the DOP-derived assignment) — exact match alone
            # misses most drivers here too. Token-overlap fallback, same
            # as everywhere else this mismatch shows up.
            a_tokens = frozenset((a.driver_name or "").lower().replace(",", "").split())
            if a_tokens:
                candidates = (
                    db.query(DriverScheduleEntry)
                    .filter(DriverScheduleEntry.schedule_date == a.assignment_date)
                    .all()
                )
                for c in candidates:
                    c_tokens = frozenset((c.driver_name or "").lower().replace(",", "").split())
                    if len(a_tokens & c_tokens) >= 2:
                        schedule_entry = c
                        break
        if schedule_entry:
            night_prior_showtime = _calc_showtime(schedule_entry.wave_time) or schedule_entry.show_time

    showtime = _resolve_showtime(night_prior_showtime, _calc_showtime(a.wave))
    return_time = _calc_return_time(a.wave or "", a.route_duration)

    fields = []
    if a.route_code:
        fields.append({"type": "mrkdwn", "text": f"*Route:*\n{a.route_code}"})
    if a.van_number:
        fields.append({"type": "mrkdwn", "text": f"*Van:*\n{a.van_number}"})
    if a.stage_location:
        fields.append({"type": "mrkdwn", "text": f"*Staging:*\n{a.stage_location}"})
    if showtime:
        fields.append({"type": "mrkdwn", "text": f"*Showtime:*\n{showtime}"})
    if a.wave:
        fields.append({"type": "mrkdwn", "text": f"*Wave:*\n{a.wave}"})
    if return_time:
        fields.append({"type": "mrkdwn", "text": f"*Est. Return:*\n{return_time}"})
    fields.append({"type": "mrkdwn", "text": f"*Wave Lead:*\n{wave_lead_name}"})
    # ACE Eligibility criteria aren't defined yet — reserved for a future
    # coaching/eligibility module (see Governance/DRIVER_DM_CONTENT_RULES.md).
    # Static "TBD" placeholder is intentional, not a bug.
    fields.append({"type": "mrkdwn", "text": "*ACE Eligibility:*\nTBD"})

    arrival_value = json.dumps({
        "shift_date": a.assignment_date.isoformat(),
        "driver_name": a.driver_name,
    })

    # Same one-click treatment as _build_shift_dm()'s "Can't Make It" —
    # token baked in at send-time (long TTL) so "Call Out" opens the
    # callout page directly in the same click that fires the
    # interaction payload to our handler.
    from api.src.routes.slack_interactions import _issue_callout_token
    callout_token = _issue_callout_token(a.driver_name, a.assignment_date.isoformat(), ttl_hours=48)
    callout_url = f"{FRONTEND_URL}/callout?token={callout_token}"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚐 Your Assignment — {date_str}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Good morning, {first_name}! Here's everything you need for today's shift:",
            },
        },
        {
            "type": "section",
            "fields": fields,
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "_Drive safe and have a great shift! 💪 Need your wave lead? Tap below._"
                    if wave_lead_slack_id else
                    "_Drive safe and have a great shift! 💪 For questions before your wave, contact your wave lead on Zello._"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✅  I Have Arrived for My Shift",
                        "emoji": True,
                    },
                    "style": "primary",
                    "action_id": "driver_arrived_shift",
                    "value": arrival_value,
                },
                *([{
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📻  Talk to My Lead",
                        "emoji": True,
                    },
                    "action_id": "talk_to_lead",
                    "value": arrival_value,
                }] if wave_lead_slack_id else []),
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨  Call Out",
                        "emoji": True,
                    },
                    "style": "danger",
                    "action_id": "driver_callout_from_dm",
                    "value": arrival_value,
                    "url": callout_url,
                },
            ],
        },
    ]

    fallback_text = (
        f"Good morning {first_name}! Your assignment for {date_str}: "
        f"Route {a.route_code or '?'} | Van {a.van_number or '?'} | "
        f"Staging {a.stage_location or '?'} | Showtime {showtime or '?'} | Wave {a.wave or '?'} | "
        f"Wave Lead {wave_lead_name}"
    )
    return fallback_text, blocks


def send_day_of_dms(shift_date: date, db: Session) -> dict:
    """
    Send morning-of route assignment DMs to all drivers with a confirmed assignment.

    Queries DailyRouteAssignment for shift_date where dm_sent=False.
    Each DM is Block Kit with route, van, staging, wave, showtime, expected
    return, wave lead, ACE Eligibility (static "TBD" until that module
    exists), and the arrival confirmation button. Full content spec/
    rationale: Governance/DRIVER_DM_CONTENT_RULES.md.

    Marks dm_sent=True on each record so daily_notify.send_all_dms() won't double-send.
    Gated by DRIVER_DM_ACTIVE=true (independent of ROSTERING_ACTIVE, which
    gates the assignment matrix and stays live on its own schedule).
    """
    if not _DM_ACTIVE:
        return {"status": "inactive", "note": "Set DRIVER_DM_ACTIVE=true on Render to enable driver DMs"}

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.dm_sent == False,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .order_by(DailyRouteAssignment.driver_name)
        .all()
    )

    if not assignments:
        return {"status": "no_assignments", "date": shift_date.isoformat()}

    from api.src.routes.driver_lead_schedule import get_current_lead, LEAD_ROUTING_ACTIVE
    wave_lead_name, wave_lead_slack_id, _lead_source = get_current_lead(shift_date, db)
    if not LEAD_ROUTING_ACTIVE:
        wave_lead_slack_id = None  # keep the legacy Zello-text DM until the flag is flipped on
    client = _slack_client()
    date_str = shift_date.strftime("%A, %B %-d")

    sent = skipped = no_slack = 0

    for a in assignments:
        slack_id = _get_driver_slack_id(a.driver_name, db)
        if not slack_id:
            no_slack += 1
            # Still mark dm_sent so daily_notify plain-text fallback can pick it up
            continue

        fallback_text, blocks = _build_driver_dm(a, wave_lead_name, date_str, wave_lead_slack_id, db)

        dm_ts = None
        if client:
            try:
                resp = client.chat_postMessage(
                    channel=slack_id,
                    text=fallback_text,
                    blocks=blocks,
                )
                dm_ts = resp.get("ts")
                sent += 1
            except Exception as exc:
                logger.warning("Day-of DM failed for %s: %s", a.driver_name, exc)
                no_slack += 1
                continue

        # Mark sent on DailyRouteAssignment to prevent daily_notify double-send
        a.dm_sent = True
        a.dm_sent_at = datetime.utcnow()
        if dm_ts:
            a.dm_message_ts = dm_ts
            a.dm_channel = slack_id

    db.commit()

    # Team-room heads-up that individual assignments have gone out — per
    # explicit 2026-07-22 request, the day DRIVER_DM_ACTIVE went live.
    # Respects the same TEAM_ROOM_MESSAGES_ACTIVE hard-off-switch as
    # post_showtime_summary()'s team-room copy (see CLAUDE.md) — if that's
    # still off from the 2026-07-20 incident, this stays off too until
    # explicitly re-enabled.
    if sent > 0 and TEAM_ROOM_MESSAGES_ACTIVE:
        try:
            team_client = _slack_client()
            if team_client:
                team_client.chat_postMessage(
                    channel=TEAM_CHANNEL,
                    text=(
                        f"📬 Individual route assignments for {date_str} have gone out. "
                        f"Check your Slack DMs for your route, van, and staging details."
                    ),
                )
        except Exception as exc:
            logger.warning("Team-room assignments-sent announcement failed: %s", exc)

    return {
        "status": "done",
        "date": shift_date.isoformat(),
        "sent": sent,
        "skipped": skipped,
        "no_slack_id": no_slack,
        "total": len(assignments),
    }


def send_single_day_of_dm(assignment_id: int, db: Session) -> dict:
    """Resend the real, button-equipped day-of DM to one driver by
    assignment id — e.g. a driver reports not receiving the first one.
    Same content builder as send_day_of_dms() (_build_driver_dm), so this
    can never drift from the batch send's format. Gated by
    DRIVER_DM_ACTIVE like every other driver-facing send here."""
    if not _DM_ACTIVE:
        return {"status": "inactive", "note": "Set DRIVER_DM_ACTIVE=true on Render to enable driver DMs"}

    a = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.id == assignment_id).first()
    if not a:
        return {"status": "not_found", "assignment_id": assignment_id}

    slack_id = _get_driver_slack_id(a.driver_name, db)
    if not slack_id:
        return {"status": "no_slack_id", "driver": a.driver_name}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_client"}

    from api.src.routes.driver_lead_schedule import get_current_lead, LEAD_ROUTING_ACTIVE
    wave_lead_name, wave_lead_slack_id, _lead_source = get_current_lead(a.assignment_date, db)
    if not LEAD_ROUTING_ACTIVE:
        wave_lead_slack_id = None
    date_str = a.assignment_date.strftime("%A, %B %-d")
    fallback_text, blocks = _build_driver_dm(a, wave_lead_name, date_str, wave_lead_slack_id, db)

    try:
        resp = client.chat_postMessage(channel=slack_id, text=fallback_text, blocks=blocks)
        a.dm_sent = True
        a.dm_sent_at = datetime.utcnow()
        a.dm_message_ts = resp.get("ts")
        a.dm_channel = slack_id
        db.commit()
        return {"status": "sent", "driver": a.driver_name}
    except Exception as exc:
        logger.warning("Single day-of DM resend failed for %s: %s", a.driver_name, exc)
        return {"status": "error", "detail": str(exc)}


def send_test_driver_dm(shift_date: date, sample_driver_name: str, target_slack_id: str, db: Session) -> dict:
    """Send ONE real driver's assignment DM content to an arbitrary reviewer
    (e.g. a manager doing a pre-launch check) for visual/content review.

    Deliberately bypasses DRIVER_DM_ACTIVE — the whole point is to preview
    before that gate is ever flipped on. Does NOT mark dm_sent on the real
    assignment, so the sampled driver still gets their real DM once DMs go
    live for real. Still subject to SLACK_NOTIFICATIONS_ACTIVE (the
    system-wide send gate) like every other Slack send in the app.
    """
    a = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name == sample_driver_name,
        )
        .first()
    )
    if not a:
        return {"status": "no_assignment", "date": shift_date.isoformat(), "sample_driver_name": sample_driver_name}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    from api.src.routes.driver_lead_schedule import get_current_lead, LEAD_ROUTING_ACTIVE
    wave_lead_name, wave_lead_slack_id, _lead_source = get_current_lead(shift_date, db)
    if not LEAD_ROUTING_ACTIVE:
        wave_lead_slack_id = None
    date_str = shift_date.strftime("%A, %B %-d")
    fallback_text, blocks = _build_driver_dm(a, wave_lead_name, date_str, wave_lead_slack_id, db)

    banner = {
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"🧪 *TEST SEND* — preview of {sample_driver_name}'s DM content, sent to you for review. "
                "Not a real assignment notification; the real driver has not been notified."
            ),
        }],
    }

    try:
        client.chat_postMessage(
            channel=target_slack_id,
            text=f"[TEST] {fallback_text}",
            blocks=[banner] + blocks,
        )
        return {"status": "sent", "target_slack_id": target_slack_id, "sample_driver_name": sample_driver_name, "date": shift_date.isoformat()}
    except Exception as exc:
        logger.warning("Test driver DM send failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ─── API endpoints ───────────────────────────────────────────────────────────

@router.get("/wave-status")
def get_wave_status(shift_date: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Return wave-by-wave attendance status for shift_date (defaults to today).
    Used by the Wave Status dashboard page.
    """
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(PACIFIC)

    if shift_date:
        try:
            target = date.fromisoformat(shift_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    else:
        target = now_pt.date()

    wave_lead_name, _ = _wave_lead_name(target)

    # All assignments for the date
    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == target,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .order_by(DailyRouteAssignment.wave, DailyRouteAssignment.driver_name)
        .all()
    )

    # ADP punch status — fetched once per request, cached 2 min inside adp module
    # {normalized_name: {"clocked_in": bool, "clocked_out": bool, "in_at": str|None, "out_at": str|None}}
    adp_punch_map: Optional[dict] = None
    try:
        from api.src.routes.adp import get_adp_punch_status as _adp_status, normalize_name as _adp_norm
        adp_punch_map = _adp_status()
    except Exception:
        pass

    # Latest Cortex snapshot per route for today → ETA calculation
    # Subquery: max snapshot_at per route_code on target date
    from sqlalchemy import func as _func
    cortex_subq = (
        db.query(
            CortexSnapshot.route_code,
            _func.max(CortexSnapshot.snapshot_at).label("latest_at"),
        )
        .filter(CortexSnapshot.route_date == target)
        .group_by(CortexSnapshot.route_code)
        .subquery()
    )
    latest_cortex = (
        db.query(CortexSnapshot)
        .join(
            cortex_subq,
            (CortexSnapshot.route_code == cortex_subq.c.route_code)
            & (CortexSnapshot.snapshot_at == cortex_subq.c.latest_at),
        )
        .all()
    )
    cortex_map: dict[str, CortexSnapshot] = {s.route_code: s for s in latest_cortex}

    # Shift DM records — arrival + checklist items
    shift_dm_map: dict[str, DriverShiftDM] = {}
    for r in db.query(DriverShiftDM).filter(DriverShiftDM.shift_date == target).all():
        shift_dm_map[r.driver_name] = r

    arrived_map: dict[str, Optional[datetime]] = {
        name: r.arrived_at
        for name, r in shift_dm_map.items()
        if r.arrival_confirmed
    }

    # RTS (Return to Station) debriefs — most recent per driver for this shift
    rts_map: dict[str, RtsDebrief] = {}
    for r in (
        db.query(RtsDebrief)
        .filter(RtsDebrief.shift_date == target)
        .order_by(RtsDebrief.started_at)
        .all()
    ):
        rts_map[r.driver_name] = r

    # Group by wave
    from collections import defaultdict
    waves_map: dict[str, list] = defaultdict(list)
    for a in assignments:
        waves_map[a.wave or "Unscheduled"].append(a)

    def _parse_wave_minutes(wt: str) -> float:
        """Return wave time as minutes since midnight for sorting."""
        for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
            try:
                t = datetime.strptime(wt.strip(), fmt)
                return t.hour * 60 + t.minute
            except ValueError:
                continue
        return 9999.0

    def _minutes_to_wave(wt: str) -> Optional[float]:
        for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
            try:
                parsed = datetime.strptime(wt.strip(), fmt)
                wave_dt = now_pt.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                return (wave_dt - now_pt).total_seconds() / 60
            except ValueError:
                continue
        return None

    waves_out = []
    total_all = arrived_all = missing_all = pending_all = 0

    for wave_time_str in sorted(waves_map.keys(), key=_parse_wave_minutes):
        wave_drivers = waves_map[wave_time_str]
        mins = _minutes_to_wave(wave_time_str)
        wave_past = mins is not None and mins < 0

        drivers_out = []
        w_arrived = w_missing = w_pending = 0

        for a in wave_drivers:
            if a.driver_name in arrived_map:
                status = "arrived"
                w_arrived += 1
            elif wave_past:
                status = "missing"
                w_missing += 1
            else:
                status = "pending"
                w_pending += 1

            # ── Per-driver checklist ───────────────────────────────────────
            dm_rec = shift_dm_map.get(a.driver_name)

            adp_ps = None
            if adp_punch_map is not None:
                try:
                    adp_ps = adp_punch_map.get(_adp_norm(a.driver_name))
                except Exception:
                    pass

            def _ci_bool(flag: Optional[bool], ts=None) -> dict:
                return {"done": flag, "at": ts.isoformat() if ts and hasattr(ts, "isoformat") else ts}

            sch_acked_at = getattr(dm_rec, "schedule_acked_at", None) if dm_rec else None
            eod_at       = getattr(dm_rec, "eod_checklist_at", None) if dm_rec else None
            arr_at       = arrived_map.get(a.driver_name)

            checklist = {
                "schedule_acked":  _ci_bool(bool(sch_acked_at), sch_acked_at),
                "adp_clocked_in":  {"done": adp_ps["clocked_in"],  "at": adp_ps.get("in_at")}  if adp_ps else {"done": None, "at": None},
                "arrived":         _ci_bool(a.driver_name in arrived_map, arr_at),
                "eod_checklist":   _ci_bool(bool(eod_at), eod_at),
                "adp_clocked_out": {"done": adp_ps["clocked_out"], "at": adp_ps.get("out_at")} if adp_ps else {"done": None, "at": None},
            }

            # ── Cortex ETA ─────────────────────────────────────────────────
            snap = cortex_map.get(a.route_code or "")
            eta_return = _calc_eta(snap, a.wave, target) if snap else None
            eta_dt = _calc_eta_dt(snap, a.wave, target) if snap and eta_return not in (None, "Done") else None
            pct_complete = float(snap.pct_complete) if snap and snap.pct_complete is not None else None
            pkgs_remaining = snap.packages_remaining if snap else None

            # ── RTS (Return to Station) ─────────────────────────────────────
            rts_rec = rts_map.get(a.driver_name)
            if rts_rec is None:
                rts_status = "not_started"
            elif rts_rec.completed_at is None:
                rts_status = "in_progress"
            else:
                rts_status = "completed"

            rts_out = {
                "status": rts_status,
                "started_at": rts_rec.started_at.isoformat() if rts_rec and rts_rec.started_at else None,
                "completed_at": rts_rec.completed_at.isoformat() if rts_rec and rts_rec.completed_at else None,
                "expected_return_time": rts_rec.expected_return_time if rts_rec else None,
                "routed_to_rescue": bool(rts_rec.routed_to_rescue) if rts_rec else False,
                "reattempt_assigned_count": rts_rec.reattempt_assigned_count if rts_rec else None,
            }

            drivers_out.append({
                "driver_name": a.driver_name,
                "route_code": a.route_code,
                "van_number": a.van_number,
                "stage_location": a.stage_location,
                "stops": a.stops or a.packages,
                "service_type": a.service_type,
                "status": status,
                "arrived_at": arr_at.isoformat() if arr_at else None,
                "checklist": checklist,
                "eta_return": eta_return,
                "eta_return_at": eta_dt.isoformat() if eta_dt else None,
                "pct_complete": pct_complete,
                "packages_remaining": pkgs_remaining,
                "rts": rts_out,
            })

        total_all += len(wave_drivers)
        arrived_all += w_arrived
        missing_all += w_missing
        pending_all += w_pending

        waves_out.append({
            "wave_time": wave_time_str,
            "wave_past": wave_past,
            "minutes_to_wave": round(mins, 1) if mins is not None else None,
            "total": len(wave_drivers),
            "arrived": w_arrived,
            "missing": w_missing,
            "pending": w_pending,
            "drivers": drivers_out,
        })

    return {
        "date": target.isoformat(),
        "wave_lead": wave_lead_name,
        "as_of": now_pt.isoformat(),
        "summary": {
            "total": total_all,
            "arrived": arrived_all,
            "missing": missing_all,
            "pending": pending_all,
        },
        "waves": waves_out,
    }


@router.post("/nightly-reminder")
def trigger_nightly_reminder(shift_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Manually trigger the nightly roster reminder for a given date (defaults to tomorrow)."""
    if shift_date:
        try:
            target = date.fromisoformat(shift_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    else:
        from zoneinfo import ZoneInfo
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        target = now_pt.date() + timedelta(days=1)

    return send_nightly_roster_reminder(target, db)


@router.post("/driver-dms/{shift_date}")
def trigger_driver_dms(shift_date: str, db: Session = Depends(get_db)):
    """Send pre-shift DMs to all drivers scheduled for shift_date (YYYY-MM-DD)."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return send_driver_shift_dms(target, db)


@router.post("/mgt-summary/{shift_date}")
def trigger_mgt_summary(shift_date: str, force: bool = False, db: Session = Depends(get_db)):
    """Post (or refresh) the #nday-mgt roster matrix for shift_date.
    Pass ?force=true for a genuinely new post instead of a silent
    chat_update of an old message (see post_mgt_summary's docstring)."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return post_mgt_summary(target, db, force=force)


@router.post("/assignment-matrix/{shift_date}")
def trigger_assignment_matrix(shift_date: str, force: bool = False, db: Session = Depends(get_db)):
    """Post the #nday-mgt day-of assignment matrix (driver/route/van/est. return) for shift_date.
    Pass ?force=true to re-post even if already posted for this date (e.g. after a format change)."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return post_assignment_matrix(target, db, force=force)


@router.post("/showtime-summary/{shift_date}")
def trigger_showtime_summary(shift_date: str, force: bool = False, db: Session = Depends(get_db)):
    """Post (or refresh) the #nday-mgt Showtime-grouped breakdown for
    shift_date. Pass ?force=true to post a fresh message instead of
    silently editing an existing one in place."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return post_showtime_summary(target, db, force=force)


@router.post("/driver-summary-matrix/{shift_date}")
def trigger_driver_summary_matrix(shift_date: str, force: bool = False, db: Session = Depends(get_db)):
    """Post the #nday-mgt driver summary (every field each driver's DM would
    show) for shift_date. Meant to run concurrently with the route summary
    matrix. Pass ?force=true to re-post."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return post_driver_summary_matrix(target, db, force=force)


@router.get("/suggested/{shift_date}")
def get_suggested_roster(shift_date: str, db: Session = Depends(get_db)):
    """Return the suggested roster ranking for shift_date — no Slack messages sent."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    suggestions = _build_roster_suggestion(target, db)
    return {
        "date": shift_date,
        "count": len(suggestions),
        "roster": suggestions,
        "wave_lead": _wave_lead_name(target)[0],
    }


@router.post("/day-of-dms/{shift_date}")
def trigger_day_of_dms(shift_date: str, db: Session = Depends(get_db)):
    """Send morning-of route/van/staging DMs for all drivers with assignments on shift_date."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return send_day_of_dms(target, db)


@router.post("/day-of-dms/{shift_date}/test")
def trigger_test_driver_dm(
    shift_date: str, sample_driver_name: str, target_slack_id: str,
    db: Session = Depends(get_db),
):
    """Preview one real driver's DM content by sending it to a reviewer
    (e.g. a manager) instead of the real driver. Bypasses DRIVER_DM_ACTIVE
    on purpose — for pre-launch review before that gate is ever flipped on.
    Does not mark the sampled assignment's dm_sent."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return send_test_driver_dm(target, sample_driver_name, target_slack_id, db)


@router.post("/driver-dms/{shift_date}/test")
def trigger_test_shift_dm(
    shift_date: str, sample_driver_name: str, target_slack_id: str,
    db: Session = Depends(get_db),
):
    """Preview/test one real driver's Showtime DM content and button
    interactivity. Bypasses DRIVER_DM_ACTIVE on purpose — for pre-launch
    testing before that gate is ever flipped on for everyone."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return send_test_shift_dm(target, sample_driver_name, target_slack_id, db)


@router.post("/ack-schedule")
def ack_schedule(
    shift_date: str, driver_name: str, db: Session = Depends(get_db)
):
    """
    Mark a driver as having acknowledged their schedule.
    Called by the Slack interaction handler when the driver taps 'I've Got My Schedule'.
    Also callable directly by dispatch if needed.
    """
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")

    rec = db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == target,
        DriverShiftDM.driver_name == driver_name,
    ).first()
    if not rec:
        rec = DriverShiftDM(shift_date=target, driver_name=driver_name)
        db.add(rec)

    if not rec.schedule_acked_at:
        rec.schedule_acked_at = datetime.utcnow()
    db.commit()

    # #nday-mgt visibility comes from the periodic (every 30 min)
    # consolidated response summary, not a message per acknowledgment.

    return {"status": "ok", "driver_name": driver_name, "shift_date": shift_date}


def decline_shift(shift_date_str: str, driver_name: str, db: Session) -> bool:
    """Driver tapped 'Can't Make It' on their night-before Showtime DM.
    Only logs the DM-response timestamp (parallel to schedule_acked_at/
    arrived_at) — the real write-up/compliance record and #nday-mgt
    notification come from the actual callout submission
    (attendance.py's /callout, via the tokenized link the Slack handler
    sends immediately after calling this), not from here."""
    try:
        shift_date = date.fromisoformat(shift_date_str)
    except ValueError:
        return False

    rec = db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == shift_date,
        DriverShiftDM.driver_name == driver_name,
    ).first()
    if not rec:
        rec = DriverShiftDM(shift_date=shift_date, driver_name=driver_name)
        db.add(rec)

    rec.declined_at = datetime.utcnow()
    db.commit()
    return True


def mark_callout_tapped(shift_date_str: str, driver_name: str, db: Session) -> bool:
    """Driver tapped 'Call Out' on their day-of Route Assignment DM.
    Only logs the DM-response timestamp — same rationale as
    decline_shift(): the real write-up/compliance record comes from the
    actual /callout submission, this just lets the consolidated
    #nday-mgt summary show red immediately rather than waiting for the
    driver to finish the web form."""
    try:
        shift_date = date.fromisoformat(shift_date_str)
    except ValueError:
        return False

    rec = db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == shift_date,
        DriverShiftDM.driver_name == driver_name,
    ).first()
    if not rec:
        rec = DriverShiftDM(shift_date=shift_date, driver_name=driver_name)
        db.add(rec)

    rec.callout_tapped_at = datetime.utcnow()
    db.commit()
    return True


@router.post("/eod-complete")
def eod_complete(
    shift_date: str, driver_name: str, db: Session = Depends(get_db)
):
    """
    Mark a driver's end-of-day checklist as complete.
    Called by the Slack interaction handler when the driver taps 'EOD Complete',
    or by dispatch on the driver's behalf.
    """
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")

    rec = db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == target,
        DriverShiftDM.driver_name == driver_name,
    ).first()
    if not rec:
        rec = DriverShiftDM(shift_date=target, driver_name=driver_name)
        db.add(rec)

    if not rec.eod_checklist_at:
        rec.eod_checklist_at = datetime.utcnow()
    db.commit()
    return {"status": "ok", "driver_name": driver_name, "shift_date": shift_date}


def send_eod_checklist_dms(shift_date: date, db: Session) -> dict:
    """
    Send EOD completion DMs to all drivers who finished their shift today.
    Includes an 'EOD Complete' button that drivers tap when they've done the
    end-of-day van checklist and submitted their delivery report.
    Gated by DRIVER_DM_ACTIVE=true (independent of ROSTERING_ACTIVE, which
    gates the assignment matrix and stays live on its own schedule).
    """
    if not _DM_ACTIVE:
        return {"status": "inactive"}

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name != None,
            DailyRouteAssignment.driver_name != "",
        )
        .all()
    )
    if not assignments:
        return {"status": "no_assignments", "date": shift_date.isoformat()}

    client = _slack_client()
    date_str = shift_date.strftime("%A, %B %-d")
    sent = skipped = 0

    for a in assignments:
        # Skip if EOD already recorded
        rec = db.query(DriverShiftDM).filter(
            DriverShiftDM.shift_date == shift_date,
            DriverShiftDM.driver_name == a.driver_name,
        ).first()
        if rec and getattr(rec, "eod_checklist_at", None):
            skipped += 1
            continue

        slack_id = _get_driver_slack_id(a.driver_name, db)
        if not slack_id or not client:
            continue

        first_name = a.driver_name.split(",")[1].strip().split()[0] if "," in (a.driver_name or "") else (a.driver_name or "Driver").split()[0]

        eod_value = json.dumps({"shift_date": shift_date.isoformat(), "driver_name": a.driver_name})
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📋 End of Day Checklist — {date_str}", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Great work today, {first_name}! Before you go, please confirm the following:\n\n"
                        f"• Packages delivered or returned to station\n"
                        f"• Van returned, locked, and plugged in (if electric)\n"
                        f"• Delivery report submitted in Cortex\n"
                        f"• Any incidents or damages reported to dispatch"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅  EOD Complete", "emoji": True},
                        "style": "primary",
                        "action_id": "driver_eod_complete",
                        "value": eod_value,
                    }
                ],
            },
        ]

        try:
            client.chat_postMessage(
                channel=slack_id,
                text=f"End of day checklist for {date_str} — please confirm before leaving.",
                blocks=blocks,
            )
            sent += 1
        except Exception as exc:
            logger.warning("EOD DM failed for %s: %s", a.driver_name, exc)

    return {"status": "done", "sent": sent, "skipped": skipped}


@router.post("/eod-dms/{shift_date}")
def trigger_eod_dms(shift_date: str, db: Session = Depends(get_db)):
    """Send EOD checklist DMs to all drivers for shift_date. Typically called ~5pm."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return send_eod_checklist_dms(target, db)


@router.get("/shift-dms/{shift_date}")
def get_shift_dm_status(shift_date: str, db: Session = Depends(get_db)):
    """List DM send status and arrival confirmations for all drivers on shift_date."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")

    records = (
        db.query(DriverShiftDM)
        .filter(DriverShiftDM.shift_date == target)
        .order_by(DriverShiftDM.driver_name)
        .all()
    )
    return {
        "date": shift_date,
        "total": len(records),
        "arrived": sum(1 for r in records if r.arrival_confirmed),
        "dms": [
            {
                "driver_name": r.driver_name,
                "dm_sent": r.dm_sent_at is not None,
                "dm_sent_at": r.dm_sent_at.isoformat() if r.dm_sent_at else None,
                "wave_time": r.wave_time,
                "showtime": r.showtime,
                "arrived": r.arrival_confirmed,
                "arrived_at": r.arrived_at.isoformat() if r.arrived_at else None,
            }
            for r in records
        ],
    }


# ─── Test scaffolding (end-to-end button-interactivity testing) ─────────────
# Added 2026-07-21 to test the full driver DM pipeline (Showtime DM +
# Route Assignment DM, all four buttons) against a real Slack account
# before flipping DRIVER_DM_ACTIVE on for everyone. Creates/deletes mock
# DriverScheduleEntry + DailyRouteAssignment rows scoped to one
# driver_name + shift_date at a time — always use a shift_date far
# outside any real scheduling horizon so it can never collide with real
# ops data or show up in a real matrix/reconciliation.

@router.post("/test-setup")
def test_setup_mock_shift(
    shift_date: str, driver_name: str,
    db: Session = Depends(get_db),
):
    """Create (or refresh) a mock DriverScheduleEntry + DailyRouteAssignment
    for one driver on a test date. Clean up afterward with /test-teardown."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")

    entry = db.query(DriverScheduleEntry).filter(
        DriverScheduleEntry.schedule_date == target,
        DriverScheduleEntry.driver_name == driver_name,
    ).first()
    if not entry:
        entry = DriverScheduleEntry(schedule_date=target, driver_name=driver_name)
        db.add(entry)
    entry.wave_time = "8:00 AM"
    entry.show_time = "7:35 AM"
    entry.service_type = "TEST — Standard Parcel"
    entry.is_sweeper = False
    entry.source_file = "manual_test_setup"

    assignment = db.query(DailyRouteAssignment).filter(
        DailyRouteAssignment.assignment_date == target,
        DailyRouteAssignment.driver_name == driver_name,
    ).first()
    if not assignment:
        assignment = DailyRouteAssignment(
            assignment_date=target, driver_name=driver_name,
            ack_token=str(uuid.uuid4()).replace("-", ""),
        )
        db.add(assignment)
    assignment.route_code = "TEST-001"
    assignment.van_number = "TEST-VAN"
    assignment.stage_location = "TEST STAGE"
    assignment.wave = "8:00 AM"
    assignment.packages = 0
    assignment.route_duration = 180
    assignment.service_type = "TEST — Standard Parcel"
    assignment.assignment_status = "pending"
    assignment.dm_sent = False

    db.commit()
    return {"status": "ok", "shift_date": shift_date, "driver_name": driver_name}


@router.post("/test-teardown")
def test_teardown_mock_shift(shift_date: str, driver_name: str, db: Session = Depends(get_db)):
    """Delete the mock schedule entry, assignment, and DriverShiftDM
    tracking row created by /test-setup for the given test date + driver."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")

    deleted = {
        "schedule_entries": db.query(DriverScheduleEntry).filter(
            DriverScheduleEntry.schedule_date == target,
            DriverScheduleEntry.driver_name == driver_name,
        ).delete(),
        "assignments": db.query(DailyRouteAssignment).filter(
            DailyRouteAssignment.assignment_date == target,
            DailyRouteAssignment.driver_name == driver_name,
        ).delete(),
        "shift_dms": db.query(DriverShiftDM).filter(
            DriverShiftDM.shift_date == target,
            DriverShiftDM.driver_name == driver_name,
        ).delete(),
    }
    db.commit()
    return {"status": "ok", "deleted": deleted}


# ─── Consolidated #nday-mgt response summaries ──────────────────────────────
# Added 2026-07-21 per explicit request: individual per-driver posts for
# every Acknowledge/Arrived/Decline/Call-Out event buried #nday-mgt in
# dozens of messages once more than a handful of drivers responded.
# These build ONE message per shift_date per DM type, refreshed on a
# 30-min periodic loop (see main.py's _dm_response_summary_loop()) —
# not on every individual click — showing a red/yellow/green rollup
# instead. Manual trigger endpoints below exist for on-demand refresh
# (e.g. testing, or a date outside the loop's today/tomorrow scope).

def _shift_response_status(rec: Optional[DriverShiftDM]) -> str:
    if rec and rec.declined_at:
        return "red"
    if rec and rec.schedule_acked_at:
        return "green"
    return "yellow"


def _arrival_response_status(rec: Optional[DriverShiftDM]) -> str:
    if rec and rec.callout_tapped_at:
        return "red"
    if rec and rec.arrival_confirmed:
        return "green"
    return "yellow"


def refresh_shift_response_summary(shift_date: date, db: Session) -> dict:
    """Consolidated #nday-mgt summary of Showtime DM responses: 🟢
    acknowledged / 🟡 no reply / 🔴 declined. One message per
    shift_date, updated in place."""
    if not _ACTIVE:
        return {"status": "inactive"}

    scheduled = (
        db.query(DriverScheduleEntry)
        .filter(DriverScheduleEntry.schedule_date == shift_date)
        .order_by(DriverScheduleEntry.driver_name)
        .all()
    )
    if not scheduled:
        return {"status": "no_schedule"}

    dm_records = {
        r.driver_name: r
        for r in db.query(DriverShiftDM).filter(DriverShiftDM.shift_date == shift_date).all()
    }

    green, yellow, red = [], [], []
    for entry in scheduled:
        status = _shift_response_status(dm_records.get(entry.driver_name))
        (green if status == "green" else yellow if status == "yellow" else red).append(entry.driver_name)

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    lines = [f"🟢 {len(green)} acknowledged  ·  🟡 {len(yellow)} no reply  ·  🔴 {len(red)} declined"]
    if red:
        lines.append("\n*🔴 Declined:*\n" + "\n".join(f"• {n}" for n in red))
    if yellow:
        lines.append("\n*🟡 No reply yet:*\n" + "\n".join(f"• {n}" for n in yellow))

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 Schedule Responses — {date_str}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Updated {datetime.utcnow().strftime('%H:%M UTC')}_"}]},
    ]

    state_key = f"shift_response_summary_{shift_date.isoformat()}"
    state = get_reminder_state(db, state_key)
    existing_ts = state.get("mgt_slack_ts")

    try:
        if existing_ts:
            client.chat_update(channel=MGT_CHANNEL, ts=existing_ts, text=f"Schedule Responses — {date_str}", blocks=blocks)
            ts = existing_ts
        else:
            resp = client.chat_postMessage(channel=MGT_CHANNEL, text=f"Schedule Responses — {date_str}", blocks=blocks)
            ts = resp.get("ts")
        set_reminder_state(db, state_key, {"mgt_slack_ts": ts})
        return {"status": "ok", "green": len(green), "yellow": len(yellow), "red": len(red)}
    except Exception as exc:
        logger.warning("Shift response summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def refresh_arrival_response_summary(shift_date: date, db: Session) -> dict:
    """Consolidated #nday-mgt summary of Route Assignment DM responses:
    🟢 arrived / 🟡 not yet arrived / 🔴 called out. One message per
    shift_date, updated in place."""
    if not _ACTIVE:
        return {"status": "inactive"}

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name.isnot(None),
            DailyRouteAssignment.driver_name != "",
        )
        .order_by(DailyRouteAssignment.driver_name)
        .all()
    )
    if not assignments:
        return {"status": "no_assignments"}

    dm_records = {
        r.driver_name: r
        for r in db.query(DriverShiftDM).filter(DriverShiftDM.shift_date == shift_date).all()
    }

    green, yellow, red = [], [], []
    seen = set()
    for a in assignments:
        if a.driver_name in seen:
            continue
        seen.add(a.driver_name)
        status = _arrival_response_status(dm_records.get(a.driver_name))
        (green if status == "green" else yellow if status == "yellow" else red).append(a.driver_name)

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = shift_date.strftime("%A, %B %-d")
    lines = [f"🟢 {len(green)} arrived  ·  🟡 {len(yellow)} not yet  ·  🔴 {len(red)} called out"]
    if red:
        lines.append("\n*🔴 Called Out:*\n" + "\n".join(f"• {n}" for n in red))

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚐 Route Assignment Responses — {date_str}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Updated {datetime.utcnow().strftime('%H:%M UTC')}_"}]},
    ]

    state_key = f"arrival_response_summary_{shift_date.isoformat()}"
    state = get_reminder_state(db, state_key)
    existing_ts = state.get("mgt_slack_ts")

    try:
        if existing_ts:
            client.chat_update(channel=MGT_CHANNEL, ts=existing_ts, text=f"Route Assignment Responses — {date_str}", blocks=blocks)
            ts = existing_ts
        else:
            resp = client.chat_postMessage(channel=MGT_CHANNEL, text=f"Route Assignment Responses — {date_str}", blocks=blocks)
            ts = resp.get("ts")
        set_reminder_state(db, state_key, {"mgt_slack_ts": ts})
        return {"status": "ok", "green": len(green), "yellow": len(yellow), "red": len(red)}
    except Exception as exc:
        logger.warning("Arrival response summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def refresh_all_dm_response_summaries(db: Session) -> dict:
    """Called every 30 min by main.py's _dm_response_summary_loop().
    Refreshes tomorrow's Showtime response summary and today's Route
    Assignment response summary — the two dates each DM type is
    realistically active for."""
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    tomorrow = today + timedelta(days=1)
    results = {}
    try:
        results["shift_tomorrow"] = refresh_shift_response_summary(tomorrow, db)
    except Exception as exc:
        logger.warning("Shift response summary refresh failed: %s", exc)
        results["shift_tomorrow"] = {"status": "error", "detail": str(exc)}
    try:
        results["arrival_today"] = refresh_arrival_response_summary(today, db)
    except Exception as exc:
        logger.warning("Arrival response summary refresh failed: %s", exc)
        results["arrival_today"] = {"status": "error", "detail": str(exc)}
    return results


@router.post("/shift-response-summary/{shift_date}")
def trigger_shift_response_summary(shift_date: str, db: Session = Depends(get_db)):
    """Manual/on-demand refresh of the Showtime response summary for
    shift_date — the periodic loop only covers tomorrow automatically."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return refresh_shift_response_summary(target, db)


@router.post("/arrival-response-summary/{shift_date}")
def trigger_arrival_response_summary(shift_date: str, db: Session = Depends(get_db)):
    """Manual/on-demand refresh of the Route Assignment response summary
    for shift_date — the periodic loop only covers today automatically."""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_date must be YYYY-MM-DD")
    return refresh_arrival_response_summary(target, db)

