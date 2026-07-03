"""
End of Day Survey module.

Flow:
  1. Daily 3 PM Pacific: post a survey button to #driver-dashboard.
  2. Driver taps link → /eod?tid=XXXX (or just /eod).
  3. Driver enters SSN last 4 PIN → authenticated, sees pre-filled form.
  4. Answers ~10 questions, submits.
  5. 7:30 PM Pacific: gentle DM reminder to any scheduled driver who hasn't submitted.

Admin view: /eod-admin — daily response grid with flags for outstanding items.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    SessionLocal, get_db,
    EodSurveyResponse, DriverRosterEntry, DailyRouteAssignment,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/eod-survey", tags=["eod-survey"])

DRIVER_DASHBOARD_CHANNEL = os.getenv("DRIVER_DASHBOARD_CHANNEL_ID", "C0BEDCXNQNT")
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

# Module-level state — tracks whether today's channel post went out
_daily_post_state: dict = {"last_posted_date": None}
# Tracks last reminder run to avoid double-firing
_reminder_state: dict = {"last_run_date": None, "runs_today": 0}


# ─── Slack helpers ────────────────────────────────────────────────────────────

def _slack():
    from slack_sdk import WebClient
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def _dm(user_id: str, text: str) -> None:
    try:
        _slack().chat_postMessage(channel=user_id, text=text)
    except Exception as exc:
        logger.warning("EOD DM failed to %s: %s", user_id, exc)


# ─── Auth helper ─────────────────────────────────────────────────────────────

def _authenticate_driver(
    transporter_id: Optional[str],
    driver_name_hint: Optional[str],
    pin: str,
    db: Session,
) -> DriverRosterEntry:
    """Resolve driver by transporter_id or name, verify SSN last 4 PIN."""
    entry: Optional[DriverRosterEntry] = None

    if transporter_id:
        entry = db.query(DriverRosterEntry).filter_by(
            position_id=transporter_id, is_active=True
        ).first()
    elif driver_name_hint:
        # Token-overlap match (Last, First vs First Last)
        hint_tokens = frozenset(driver_name_hint.lower().split())
        candidates = db.query(DriverRosterEntry).filter_by(is_active=True).all()
        for c in candidates:
            name_tokens = frozenset(c.payroll_name.lower().replace(",", "").split())
            if len(hint_tokens & name_tokens) >= 2:
                entry = c
                break

    if not entry:
        raise HTTPException(status_code=404, detail="Driver not found.")

    stored_pin = entry.ssn_last4 or "1234"
    if pin.strip() != stored_pin:
        raise HTTPException(status_code=401, detail="Incorrect PIN.")

    return entry


def _load_today_assignment(roster_id: int, today: date, db: Session) -> Optional[DailyRouteAssignment]:
    entry = db.query(DriverRosterEntry).get(roster_id)
    if not entry:
        return None
    # Match by driver name (payroll_name is "Last, First"; assignment stores "First Last")
    name_tokens = frozenset(entry.payroll_name.lower().replace(",", "").split())
    rows = db.query(DailyRouteAssignment).filter(
        DailyRouteAssignment.assignment_date == today
    ).all()
    for row in rows:
        row_tokens = frozenset(row.driver_name.lower().split())
        if len(name_tokens & row_tokens) >= 2:
            return row
    return None


# ─── Pydantic models ─────────────────────────────────────────────────────────

class DriverLookupResponse(BaseModel):
    transporter_id: str
    driver_name: str
    roster_id: int
    van_number: Optional[str]
    wave: Optional[str]
    role: str
    already_submitted: bool
    submitted_at: Optional[str]


class EodSubmitRequest(BaseModel):
    # Auth
    transporter_id: Optional[str] = None
    driver_name_hint: Optional[str] = None
    pin: str

    # Survey fields
    survey_date: str                          # YYYY-MM-DD
    van_number: Optional[str] = None          # driver can correct pre-fill
    wave: Optional[str] = None
    role: Optional[str] = None

    clocked_in_on_time: bool = True
    actual_clock_in_time: Optional[str] = None
    clock_in_reason: Optional[str] = None

    van_issues: bool = False
    van_issue_description: Optional[str] = None

    incident_occurred: bool = False
    incident_report_filed: Optional[bool] = None

    injury_occurred: bool = False
    injury_report_submitted: Optional[bool] = None
    medical_review_completed: Optional[bool] = None

    post_trip_dvic_completed: bool = True
    gas_level: Optional[str] = None
    packages_rts: int = 0

    route_issues: bool = False
    route_issue_description: Optional[str] = None

    performed_sweep: bool = False
    sweep_details: Optional[str] = None

    took_lunch: bool = False
    lunch_clock_out: Optional[str] = None
    lunch_clock_in: Optional[str] = None

    clock_out_time: Optional[str] = None
    pockets_checked: bool = True
    needs_management_contact: bool = False

    all_equipment_present: bool = True
    missing_equipment: Optional[str] = None


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/driver-lookup")
def driver_lookup(
    pin: str,
    transporter_id: Optional[str] = None,
    driver_name_hint: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Validate PIN and return driver info + today's pre-fill data."""
    entry = _authenticate_driver(transporter_id, driver_name_hint, pin, db)
    today = date.today()
    assignment = _load_today_assignment(entry.id, today, db)

    # Check if already submitted
    existing = db.query(EodSurveyResponse).filter_by(
        roster_id=entry.id, survey_date=today
    ).first()

    # Derive role from position_code
    code = (entry.position_code or "").lower()
    if "helper" in code:
        role = "Helper"
    elif "shift" in code or "lead" in code:
        role = "Shift Lead"
    elif "train" in code:
        role = "Trainer"
    else:
        role = "Driver"

    parts = entry.payroll_name.split(",", 1)
    display_name = f"{parts[1].strip()} {parts[0].strip()}" if len(parts) == 2 else entry.payroll_name

    return DriverLookupResponse(
        transporter_id=entry.position_id,
        driver_name=display_name,
        roster_id=entry.id,
        van_number=assignment.van_number if assignment else None,
        wave=assignment.wave if assignment else None,
        role=role,
        already_submitted=existing is not None,
        submitted_at=existing.submitted_at.isoformat() if existing else None,
    )


@router.post("/submit")
def submit_survey(req: EodSubmitRequest, db: Session = Depends(get_db)):
    """Authenticate driver and save EOD survey response."""
    entry = _authenticate_driver(req.transporter_id, req.driver_name_hint, req.pin, db)

    try:
        survey_date = date.fromisoformat(req.survey_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid survey_date format.")

    # Prevent duplicate for same day
    existing = db.query(EodSurveyResponse).filter_by(
        roster_id=entry.id, survey_date=survey_date
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Survey already submitted for today.")

    parts = entry.payroll_name.split(",", 1)
    display_name = f"{parts[1].strip()} {parts[0].strip()}" if len(parts) == 2 else entry.payroll_name

    resp = EodSurveyResponse(
        survey_date=survey_date,
        driver_name=display_name,
        transporter_id=entry.position_id,
        roster_id=entry.id,
        van_number=req.van_number,
        wave=req.wave,
        role=req.role,
        clocked_in_on_time=req.clocked_in_on_time,
        actual_clock_in_time=req.actual_clock_in_time,
        clock_in_reason=req.clock_in_reason,
        van_issues=req.van_issues,
        van_issue_description=req.van_issue_description,
        incident_occurred=req.incident_occurred,
        incident_report_filed=req.incident_report_filed,
        injury_occurred=req.injury_occurred,
        injury_report_submitted=req.injury_report_submitted,
        medical_review_completed=req.medical_review_completed,
        post_trip_dvic_completed=req.post_trip_dvic_completed,
        gas_level=req.gas_level,
        packages_rts=req.packages_rts,
        route_issues=req.route_issues,
        route_issue_description=req.route_issue_description,
        performed_sweep=req.performed_sweep,
        sweep_details=req.sweep_details,
        took_lunch=req.took_lunch,
        lunch_clock_out=req.lunch_clock_out,
        lunch_clock_in=req.lunch_clock_in,
        clock_out_time=req.clock_out_time,
        pockets_checked=req.pockets_checked,
        needs_management_contact=req.needs_management_contact,
        all_equipment_present=req.all_equipment_present,
        missing_equipment=req.missing_equipment,
    )
    db.add(resp)
    db.commit()
    logger.info("EOD survey submitted by %s for %s", display_name, survey_date)

    # Flag items needing attention
    flags: list[str] = []
    if req.incident_occurred:
        flags.append("⚠️ Incident reported")
    if req.injury_occurred:
        flags.append("🚨 Injury reported")
    if req.van_issues:
        flags.append("🔧 Van issue: " + (req.van_issue_description or "see survey"))
    if req.needs_management_contact:
        flags.append("👔 Requests management contact")

    return {"status": "submitted", "driver": display_name, "flags": flags}


@router.get("/responses")
def list_responses(survey_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Admin: list all responses for a date (defaults to today)."""
    target = date.fromisoformat(survey_date) if survey_date else date.today()
    rows = (
        db.query(EodSurveyResponse)
        .filter_by(survey_date=target)
        .order_by(EodSurveyResponse.submitted_at)
        .all()
    )
    return [_row_to_dict(r) for r in rows]


@router.get("/missing")
def missing_drivers(survey_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Admin: drivers scheduled today who haven't submitted."""
    target = date.fromisoformat(survey_date) if survey_date else date.today()
    scheduled = (
        db.query(DailyRouteAssignment)
        .filter_by(assignment_date=target)
        .all()
    )
    submitted_names = {
        r.driver_name.lower()
        for r in db.query(EodSurveyResponse).filter_by(survey_date=target).all()
    }
    missing = []
    for a in scheduled:
        if a.driver_name.lower() not in submitted_names:
            missing.append({"driver_name": a.driver_name, "van": a.van_number, "wave": a.wave})
    return missing


def _row_to_dict(r: EodSurveyResponse) -> dict:
    flags: list[str] = []
    if r.incident_occurred:
        flags.append("incident")
    if r.injury_occurred:
        flags.append("injury")
    if r.van_issues:
        flags.append("van_issue")
    if r.needs_management_contact:
        flags.append("mgmt_contact")
    if r.post_trip_dvic_completed is False:
        flags.append("dvic_missed")

    return {
        "id": r.id,
        "survey_date": r.survey_date.isoformat() if r.survey_date else None,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "driver_name": r.driver_name,
        "transporter_id": r.transporter_id,
        "van_number": r.van_number,
        "wave": r.wave,
        "role": r.role,
        "clocked_in_on_time": r.clocked_in_on_time,
        "actual_clock_in_time": r.actual_clock_in_time,
        "clock_in_reason": r.clock_in_reason,
        "van_issues": r.van_issues,
        "van_issue_description": r.van_issue_description,
        "incident_occurred": r.incident_occurred,
        "incident_report_filed": r.incident_report_filed,
        "injury_occurred": r.injury_occurred,
        "injury_report_submitted": r.injury_report_submitted,
        "medical_review_completed": r.medical_review_completed,
        "post_trip_dvic_completed": r.post_trip_dvic_completed,
        "gas_level": r.gas_level,
        "packages_rts": r.packages_rts,
        "route_issues": r.route_issues,
        "route_issue_description": r.route_issue_description,
        "performed_sweep": r.performed_sweep,
        "sweep_details": r.sweep_details,
        "took_lunch": r.took_lunch,
        "lunch_clock_out": r.lunch_clock_out,
        "lunch_clock_in": r.lunch_clock_in,
        "clock_out_time": r.clock_out_time,
        "pockets_checked": r.pockets_checked,
        "needs_management_contact": r.needs_management_contact,
        "all_equipment_present": r.all_equipment_present,
        "missing_equipment": r.missing_equipment,
        "flags": flags,
    }


# ─── Background tasks ─────────────────────────────────────────────────────────

def post_daily_survey_message() -> None:
    """3 PM Pacific: DM every driver on today's schedule with their personal survey link."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    today = now.date()

    if _daily_post_state["last_posted_date"] == today:
        return
    if now.hour < 15 or now.hour >= 16:
        return

    db = SessionLocal()
    try:
        scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
        if not scheduled:
            logger.info("EOD daily DM: no drivers scheduled today, skipping.")
            _daily_post_state["last_posted_date"] = today
            return

        # Build name→roster lookup
        roster_entries = db.query(DriverRosterEntry).filter_by(is_active=True).all()

        sent = 0
        no_slack = 0
        for assignment in scheduled:
            name_tokens = frozenset(assignment.driver_name.lower().split())
            roster_entry: Optional[DriverRosterEntry] = None
            for rc in roster_entries:
                rc_tokens = frozenset(rc.payroll_name.lower().replace(",", "").split())
                if len(name_tokens & rc_tokens) >= 2:
                    roster_entry = rc
                    break

            if not roster_entry or not roster_entry.slack_member_id:
                no_slack += 1
                continue

            tid = roster_entry.position_id
            url = f"{APP_URL}/eod?tid={tid}"
            first_name = assignment.driver_name.split()[0]
            msg = (
                f"🏁 Hi {first_name}! Time to complete your *End of Day Survey* before you head out.\n"
                f"It only takes 2 minutes.\n"
                f"👉 *<{url}|Complete Your Survey>*"
            )
            _dm(roster_entry.slack_member_id, msg)
            sent += 1

        _daily_post_state["last_posted_date"] = today
        logger.info("EOD daily DMs sent to %d driver(s) (%d had no Slack ID)", sent, no_slack)
    except Exception as exc:
        logger.warning("EOD daily DM loop failed: %s", exc)
    finally:
        db.close()


def send_eod_reminders() -> None:
    """7:30 PM Pacific: DM any scheduled driver who hasn't submitted yet."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    today = now.date()

    if _reminder_state["last_run_date"] == today:
        return
    if now.hour < 19 or (now.hour == 19 and now.minute < 30):
        return
    if now.hour >= 22:
        return

    _reminder_state["last_run_date"] = today

    db = SessionLocal()
    try:
        scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
        if not scheduled:
            return

        submitted_names = {
            r.driver_name.lower()
            for r in db.query(EodSurveyResponse).filter_by(survey_date=today).all()
        }

        sent = 0
        for assignment in scheduled:
            if assignment.driver_name.lower() in submitted_names:
                continue

            # Find roster entry for Slack ID
            name_tokens = frozenset(assignment.driver_name.lower().split())
            roster_candidates = db.query(DriverRosterEntry).filter_by(is_active=True).all()
            roster_entry: Optional[DriverRosterEntry] = None
            for rc in roster_candidates:
                rc_tokens = frozenset(rc.payroll_name.lower().replace(",", "").split())
                if len(name_tokens & rc_tokens) >= 2:
                    roster_entry = rc
                    break

            if not roster_entry or not roster_entry.slack_member_id:
                continue

            tid = roster_entry.position_id
            url = f"{APP_URL}/eod?tid={tid}"
            first_name = assignment.driver_name.split()[0]
            msg = (
                f"Hi {first_name} 👋 Just a reminder to complete your *End of Day Survey* before heading out!\n"
                f"It only takes a couple of minutes.\n"
                f"👉 {url}"
            )
            _dm(roster_entry.slack_member_id, msg)

            # Mark reminder sent on any existing partial response
            eod = db.query(EodSurveyResponse).filter_by(
                roster_id=roster_entry.id, survey_date=today
            ).first()
            if eod:
                eod.reminder_sent = True
                eod.reminder_sent_at = datetime.utcnow()

            sent += 1

        if sent:
            db.commit()
            logger.info("EOD reminders sent to %d driver(s)", sent)
    finally:
        db.close()
