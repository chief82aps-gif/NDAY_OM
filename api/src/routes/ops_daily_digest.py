"""
Daily Ops Digest — added 2026-08-04, per explicit request for a single
end-of-day rollup of "every event required to act on": crashes,
injuries, callouts, packages delivered/route progress, vans grounded.
Formatted as an executive summary (HR / Operational / Fleet) as ONE
report, sent identically to #nday-mgt, #nday-fleet, and #nday-hr.

Timing redesigned 2026-08-05 (was: fixed 10:00 PM Pacific) -- now fires
once the same day's final Cortex data (delivered + RTS) has posted,
reusing ops_cadence.py's "both uploads landed since last wave" signal
(the same one that gates the All In post), since the summary's own
numbers (total delivered, total RTS packages) aren't meaningful before
that data exists.

Distinct from eod_survey.py's existing send_daily_eod_category_digests()
(gated off by EOD_CATEGORY_DIGEST_ACTIVE, fires ~22:15 PT): that digest
is sourced ONLY from driver EOD-survey self-reports and sends three
SEPARATE filtered messages (fleet gets van issues only, HR gets injury/
mgmt-contact only, mgt gets crash/incident/route/equipment only). This
digest instead reads from the actual systems of record (CrashReport,
InjuryReport, AttendanceEvent, Vehicle, PackagesSnapshot) and sends the
SAME unified report to all three channels -- kept as a separate module
rather than folded into that one since the data sources, format, and
timing are all different; both can run without conflicting.

Called every 60s from main.py's background loop, same pattern as
mgt_reminders.py / eod_survey.py / ops_cadence.py.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, get_reminder_state, set_reminder_state,
    CrashReport, InjuryReport, AttendanceEvent, Vehicle, EodSurveyResponse,
    DailyRouteAssignment, DriverShiftDM,
)
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ops-daily-digest", tags=["ops-daily-digest"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")     # #nday-mgt
FLEET_CHANNEL = os.getenv("SLACK_FLEET_CHANNEL", "C0BJ8J5LGAU")  # #nday-fleet
HR_CHANNEL = os.getenv("SLACK_HR_CHANNEL", "C0BLRE793L0")        # #nday-hr

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
    ingest data), never EodSurveyResponse.van_issues at all.

    van_number is pre-populated onto the survey row from that day's
    DailyRouteAssignment at creation time, but falls through null when
    the assignment wasn't recorded yet (or changed after) -- fall back
    to a direct DailyRouteAssignment lookup by driver name so the van
    name is never dropped from the memo, per explicit request "add van
    names to the fleet memos"."""
    rows = (
        db.query(EodSurveyResponse)
        .filter(EodSurveyResponse.survey_date == today, EodSurveyResponse.van_issues == True)  # noqa: E712
        .all()
    )
    van_by_driver = {
        a.driver_name: a.van_number
        for a in db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date == today).all()
        if a.van_number
    }
    lines = []
    for r in rows:
        van_number = r.van_number or van_by_driver.get(r.driver_name)
        van_tag = f" (Van {van_number})" if van_number else " (van unknown)"
        lines.append(f"• *{r.driver_name}*{van_tag}: {r.van_issue_description or 'see survey'}")
    return lines


def _incident_lines(db: Session, today: date) -> list[str]:
    """EOD-survey self-reported incidents -- added 2026-08-05 per explicit
    direction that HR's section should cover "callouts, injuries,
    incidents." Separate from _crash_lines() (the real CrashReport table)
    -- an "incident" here is whatever a driver flagged in their own EOD
    survey, which may or may not have become a formal report."""
    rows = (
        db.query(EodSurveyResponse)
        .filter(EodSurveyResponse.survey_date == today, EodSurveyResponse.incident_occurred == True)  # noqa: E712
        .all()
    )
    return [f"• {r.driver_name} — {r.incident_description or 'see survey'}" for r in rows]


def _mgmt_contact_lines(db: Session, today: date) -> list[str]:
    """EOD-survey requests to speak with HR/management -- added 2026-08-05."""
    rows = (
        db.query(EodSurveyResponse)
        .filter(EodSurveyResponse.survey_date == today, EodSurveyResponse.needs_management_contact == True)  # noqa: E712
        .all()
    )
    return [f"• {r.driver_name} — {r.management_contact_reason or 'see survey'}" for r in rows]


def _pt_time_str(dt: Optional[datetime]) -> Optional[str]:
    """Every DateTime default in this codebase is naive UTC
    (datetime.utcnow()) -- convert to Pacific for display."""
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(PT).strftime("%-I:%M %p")


def _mgt_stats(db: Session, today: date) -> dict:
    """Executive-summary numbers for the MGT section -- added 2026-08-05,
    replacing the earlier per-item raw dump per explicit direction
    ("I want the executive summary").

    Total RTS packages comes from today's Packages export (packages.py)
    -- not quality_rts.py's file, which reports the PRIOR day and
    wouldn't have today's number yet. Total delivered is DERIVED
    (2026-08-05, confirmed) as today's total planned packages
    (DailyRouteAssignment.packages) minus that same RTS count --
    CortexSnapshot (the original source) turned out to have no real
    upload path in practice, always empty.

    Efficiency = total time between "I've Arrived" (DriverShiftDM.arrived_at)
    and EOD submission, summed across every driver with both, divided by
    the summed planned route duration for ONLY those same matched
    drivers (not every route today) -- comparing a handful of matched
    drivers' worked time against the whole day's planned time produced a
    nonsense ~15% figure the first time this ran; fixed to compare
    like-for-like."""
    from api.src.database import PackagesSnapshot

    latest_pkg_snap = (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date == today)
        .order_by(PackagesSnapshot.imported_at.desc())
        .first()
    )
    total_rts_packages = latest_pkg_snap.package_count if latest_pkg_snap else None

    assignments = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date == today).all()
    total_planned_packages = sum(a.packages or 0 for a in assignments)
    total_delivered = (
        max(0, total_planned_packages - total_rts_packages)
        if total_rts_packages is not None and total_planned_packages
        else None
    )

    route_count = len(assignments)
    eod_count = db.query(EodSurveyResponse).filter(EodSurveyResponse.survey_date == today).count()
    eod_pct = round(100 * eod_count / route_count, 1) if route_count else None

    shift_rows = db.query(DriverShiftDM).filter(DriverShiftDM.shift_date == today, DriverShiftDM.arrived_at.isnot(None)).all()
    eod_time_by_name = {
        r.driver_name: r.submitted_at
        for r in db.query(EodSurveyResponse).filter(EodSurveyResponse.survey_date == today).all()
    }
    route_duration_by_name = {a.driver_name: a.route_duration for a in assignments if a.route_duration}

    total_worked_minutes = 0.0
    total_planned_minutes_for_matched = 0.0
    matched = 0
    for s in shift_rows:
        eod_time = eod_time_by_name.get(s.driver_name)
        planned_duration = route_duration_by_name.get(s.driver_name)
        if eod_time and s.arrived_at and planned_duration:
            delta_minutes = (eod_time - s.arrived_at).total_seconds() / 60
            if delta_minutes > 0:
                total_worked_minutes += delta_minutes
                total_planned_minutes_for_matched += planned_duration
                matched += 1

    efficiency_pct = (
        round(100 * total_worked_minutes / total_planned_minutes_for_matched, 1)
        if total_planned_minutes_for_matched else None
    )

    return {
        "total_rts_packages": total_rts_packages,
        "total_delivered": total_delivered,
        "route_count": route_count,
        "eod_count": eod_count,
        "eod_pct": eod_pct,
        "efficiency_pct": efficiency_pct,
        "matched_shift_count": matched,
    }


def build_digest_text(db: Session, today: date) -> str:
    """Executive-summary format -- redesigned 2026-08-05 per explicit
    direction, replacing the earlier raw per-item/per-driver dump. HR
    still lists actual occurrences (incidents/callouts/injuries/mgmt
    requests); MGT is aggregate stats only; Fleet stays van issues."""
    date_str = today.strftime("%A, %B ") + str(today.day)

    from api.src.routes.team_room_monitor import chat_flagged_hr_lines, chat_flagged_equipment_lines

    hr_lines = (
        _incident_lines(db, today)
        + _callout_lines(db, today)
        + _injury_lines(db, today)
        + _mgmt_contact_lines(db, today)
        + _crash_lines(db, today)   # not in the explicit list but a real write-up needing HR eyes -- flagged, remove if not wanted
        + chat_flagged_hr_lines(db, today)   # AI-detected injury/incident/dog-bite/customer-complaint mentions in #nday-team-room
    )
    hr_section = "\n".join(hr_lines) or "_Nothing to report._"

    stats = _mgt_stats(db, today)
    mgt_section = (
        f"• Total RTS packages: *{stats['total_rts_packages'] if stats['total_rts_packages'] is not None else '—'}*\n"
        f"• Total delivered packages: *{stats['total_delivered'] if stats['total_delivered'] is not None else '—'}*\n"
        f"• EOD complete vs. routes: *{stats['eod_count']}/{stats['route_count']}*"
        + (f" ({stats['eod_pct']}%)" if stats['eod_pct'] is not None else "") + "\n"
        f"• Arrived-to-EOD time vs. planned route time: "
        + (f"*{stats['efficiency_pct']}%*" if stats['efficiency_pct'] is not None else "_no matched arrival/EOD pairs yet_")
        + (f" (from {stats['matched_shift_count']} driver(s))" if stats['matched_shift_count'] else "")
    )

    grounded_lines = _grounded_van_lines(db)
    van_issue_lines = _van_issue_lines(db, today)
    chat_equipment_lines = chat_flagged_equipment_lines(db, today)   # AI-detected equipment mentions in #nday-team-room
    fleet_lines = grounded_lines + van_issue_lines + chat_equipment_lines
    fleet_section = "\n".join(fleet_lines) or "_No van issues reported._"

    return (
        f"📋 *Daily Ops Digest — {date_str}*\n\n"
        f"*HR* ({len(hr_lines)} item(s))\n{hr_section}\n\n"
        f"*Operational*\n{mgt_section}\n\n"
        f"*Fleet*\n{fleet_section}"
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
    once the same day's final Cortex data (delivered + RTS) has posted --
    reuses ops_cadence.py's "both Packages+Cortex uploaded since last
    wave launched" signal, the same one that gates the All In post --
    rather than a fixed clock time, since the executive summary's own
    numbers aren't meaningful before that data exists. Dedup'd per day
    via ReminderThrottleState."""
    from api.src.routes.ops_cadence import (
        _last_wave_launch_dt, _to_naive_utc, _packages_uploaded_since, _cortex_uploaded_since,
    )

    now = datetime.now(PT)
    today = now.date()

    if not force:
        state = get_reminder_state(db, _STATE_KEY)
        if state.get("last_sent_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}

        last_wave_dt = _last_wave_launch_dt(db, today)
        if not last_wave_dt or now < last_wave_dt:
            return {"status": "no_wave_data_yet", "date": today.isoformat()}

        since_utc = _to_naive_utc(last_wave_dt)
        if not (_packages_uploaded_since(db, since_utc) and _cortex_uploaded_since(db, since_utc, today)):
            return {"status": "waiting_for_final_data", "date": today.isoformat()}

    _send_digest(db, today)
    set_reminder_state(db, _STATE_KEY, {"last_sent_date": today.isoformat()})
    return {"status": "sent", "date": today.isoformat()}


@router.post("/check")
def manual_check(force: bool = False, db: Session = Depends(get_db)):
    """Manual trigger — same call the background loop makes."""
    return run_daily_ops_digest(db, force=force)


@router.get("/preview")
def preview(for_date: Optional[str] = None, db: Session = Depends(get_db)):
    """See a given date's digest text without sending it. Defaults to
    today; pass for_date=YYYY-MM-DD to preview a past day's data."""
    today = date.fromisoformat(for_date) if for_date else datetime.now(PT).date()
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
