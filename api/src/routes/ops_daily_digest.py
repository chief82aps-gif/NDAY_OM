"""
Daily Ops Digest — added 2026-08-04, per explicit request for a single
end-of-day rollup of "every event required to act on": crashes,
injuries, callouts, packages delivered/route progress, vans grounded.
Formatted by type (HR / Operational / Fleet) as ONE report, sent
identically to #nday-mgt, #nday-fleet, and #nday-hr at 10:00 PM Pacific.

Distinct from eod_survey.py's existing send_daily_eod_category_digests()
(gated off by EOD_CATEGORY_DIGEST_ACTIVE, fires ~22:15 PT): that digest
is sourced ONLY from driver EOD-survey self-reports and sends three
SEPARATE filtered messages (fleet gets van issues only, HR gets injury/
mgmt-contact only, mgt gets crash/incident/route/equipment only). This
digest instead reads from the actual systems of record (CrashReport,
InjuryReport, AttendanceEvent, Vehicle, CortexSnapshot) and sends the
SAME unified report to all three channels -- kept as a separate module
rather than folded into that one since the data sources, format, and
timing are all different; both can run without conflicting.

Called every 60s from main.py's background loop, same pattern as
mgt_reminders.py / eod_survey.py / ops_cadence.py.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, get_reminder_state, set_reminder_state,
    CrashReport, InjuryReport, AttendanceEvent, Vehicle, CortexSnapshot, EodSurveyResponse,
)
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ops-daily-digest", tags=["ops-daily-digest"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")     # #nday-mgt
FLEET_CHANNEL = os.getenv("SLACK_FLEET_CHANNEL", "C0BJ8J5LGAU")  # #nday-fleet
HR_CHANNEL = os.getenv("SLACK_HR_CHANNEL", "C0BLRE793L0")        # #nday-hr

DIGEST_HOUR = 22   # 10:00 PM Pacific
_STATE_KEY = "ops_daily_digest"


def _crash_lines(db: Session, today: date) -> list[str]:
    rows = (
        db.query(CrashReport)
        .filter(func.date(CrashReport.submitted_at) == today, CrashReport.status == "submitted")
        .all()
    )
    return [f"• Report {r.report_number} — submitted by {r.submitted_by or 'unknown'}" for r in rows]


def _injury_lines(db: Session, today: date) -> list[str]:
    rows = db.query(InjuryReport).filter(InjuryReport.incident_date == today).all()
    lines = []
    for r in rows:
        tag = " _(needs HR sign-off)_" if not r.hr_signed_at else ""
        lines.append(f"• {r.employee_name} — {r.body_parts_injured or 'injury reported'}{tag}")
    return lines


def _callout_lines(db: Session, today: date) -> list[str]:
    rows = (
        db.query(AttendanceEvent)
        .filter(AttendanceEvent.event_date == today, AttendanceEvent.is_missed == True)
        .all()
    )
    return [f"• {r.driver_name} — {r.event_type} ({r.reason_code or 'no reason given'})" for r in rows]


def _grounded_van_lines(db: Session) -> list[str]:
    rows = db.query(Vehicle).filter(func.lower(Vehicle.status) == "grounded").all()
    return [f"• {r.vehicle_name} ({r.vin}) — {r.service_type}" for r in rows]


def _van_issue_lines(db: Session, today: date) -> list[str]:
    """Driver-reported van problems from today's EOD survey -- added
    2026-08-05. This was the actual gap behind "the daily summary showed
    no van issues" even though several EOD submissions had flagged one:
    this digest's Fleet section only ever queried grounded vans (Fleet
    ingest data), never EodSurveyResponse.van_issues at all."""
    rows = (
        db.query(EodSurveyResponse)
        .filter(EodSurveyResponse.survey_date == today, EodSurveyResponse.van_issues == True)  # noqa: E712
        .all()
    )
    return [
        f"• *{r.driver_name}*" + (f" (Van {r.van_number})" if r.van_number else "")
        + f": {r.van_issue_description or 'see survey'}"
        for r in rows
    ]


def _route_progress(db: Session, today: date) -> Optional[dict]:
    """Latest-snapshot-per-route totals for today, from CortexSnapshot's
    intraday progress data -- not daily_quality.py's file, which releases
    30-48h after delivery and won't have today's figures yet at 10 PM."""
    latest_subq = (
        db.query(CortexSnapshot.route_code, func.max(CortexSnapshot.snapshot_at).label("latest_at"))
        .filter(CortexSnapshot.route_date == today)
        .group_by(CortexSnapshot.route_code)
        .subquery()
    )
    rows = (
        db.query(CortexSnapshot)
        .join(
            latest_subq,
            (CortexSnapshot.route_code == latest_subq.c.route_code)
            & (CortexSnapshot.snapshot_at == latest_subq.c.latest_at),
        )
        .filter(CortexSnapshot.route_date == today)
        .all()
    )
    if not rows:
        return None
    return {
        "routes": len(rows),
        "routes_complete": sum(1 for r in rows if r.pct_complete is not None and r.pct_complete >= 100),
        "packages_delivered": sum(r.packages_delivered or 0 for r in rows),
        "packages_remaining": sum(r.packages_remaining or 0 for r in rows),
    }


def build_digest_text(db: Session, today: date) -> str:
    date_str = today.strftime("%A, %B ") + str(today.day)

    hr_lines = _injury_lines(db, today) + _callout_lines(db, today) + _crash_lines(db, today)
    hr_section = "\n".join(hr_lines) or "_Nothing to report._"

    progress = _route_progress(db, today)
    if progress:
        ops_section = (
            f"• Routes: *{progress['routes_complete']}/{progress['routes']}* complete\n"
            f"• Packages delivered: *{progress['packages_delivered']}*\n"
            f"• Packages remaining: *{progress['packages_remaining']}*"
        )
    else:
        ops_section = "_No Cortex progress data today._"

    grounded_lines = _grounded_van_lines(db)
    van_issue_lines = _van_issue_lines(db, today)
    fleet_lines = grounded_lines + van_issue_lines
    fleet_section = "\n".join(fleet_lines) or "_No grounded vans or reported van issues._"
    fleet_header = f"*Fleet* ({len(grounded_lines)} grounded, {len(van_issue_lines)} reported issue(s))" if fleet_lines else "*Fleet*"

    return (
        f"📋 *Daily Ops Digest — {date_str}*\n\n"
        f"*HR* ({len(hr_lines)} item(s))\n{hr_section}\n\n"
        f"*Operational (Packages / Routes)*\n{ops_section}\n\n"
        f"{fleet_header}\n{fleet_section}"
    )


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _send_digest(db: Session, today: date) -> None:
    client = _client()
    if not client:
        logger.info("ops_daily_digest: SLACK_BOT_TOKEN not set — skipping send")
        return
    text = build_digest_text(db, today)
    for channel in (MGT_CHANNEL, FLEET_CHANNEL, HR_CHANNEL):
        try:
            client.chat_postMessage(channel=channel, text=text)
        except Exception as exc:
            logger.warning("Daily ops digest post failed (%s): %s", channel, exc)


def run_daily_ops_digest(db: Session, force: bool = False) -> dict:
    """Called every 60s from main.py's background loop. Fires once daily
    at/after 10:00 PM Pacific, dedup'd per day via ReminderThrottleState."""
    now = datetime.now(PT)
    today = now.date()

    if not force:
        state = get_reminder_state(db, _STATE_KEY)
        if state.get("last_sent_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if now.hour < DIGEST_HOUR:
            return {"status": "outside_window"}

    _send_digest(db, today)
    set_reminder_state(db, _STATE_KEY, {"last_sent_date": today.isoformat()})
    return {"status": "sent", "date": today.isoformat()}


@router.post("/check")
def manual_check(force: bool = False, db: Session = Depends(get_db)):
    """Manual trigger — same call the background loop makes."""
    return run_daily_ops_digest(db, force=force)


@router.get("/preview")
def preview(db: Session = Depends(get_db)):
    """See today's digest text without sending it."""
    today = datetime.now(PT).date()
    return {"date": today.isoformat(), "text": build_digest_text(db, today)}


@router.post("/resend-fleet")
def resend_fleet(db: Session = Depends(get_db)):
    """One-off catch-up send -- added 2026-08-05 after discovering
    #nday-fleet was private and the bot had never been invited, so every
    van-issue alert had been failing silently. Posts just today's Fleet
    section (grounded vans + reported van issues) to #nday-fleet, without
    touching the full digest's own once-daily state (run_daily_ops_digest
    still fires normally tonight/tomorrow)."""
    client = _client()
    if not client:
        return {"status": "no_slack_token"}
    today = datetime.now(PT).date()
    grounded_lines = _grounded_van_lines(db)
    van_issue_lines = _van_issue_lines(db, today)
    fleet_lines = grounded_lines + van_issue_lines
    if not fleet_lines:
        return {"status": "nothing_to_send", "date": today.isoformat()}
    date_str = today.strftime("%A, %B ") + str(today.day)
    text = (
        f"📋 *Fleet Update — {date_str}* ({len(grounded_lines)} grounded, {len(van_issue_lines)} reported issue(s))\n\n"
        + "\n".join(fleet_lines)
    )
    try:
        client.chat_postMessage(channel=FLEET_CHANNEL, text=text)
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    return {"status": "sent", "date": today.isoformat(), "grounded": len(grounded_lines), "van_issues": len(van_issue_lines)}
