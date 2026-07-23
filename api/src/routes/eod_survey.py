"""
End of Day Survey module.

Flow:
  1. Daily 3 PM Pacific: DM every scheduled driver their personal, signed
     survey link → /eod?token=XXXX (or just /eod, generic/no-auth-token path).
  2. Driver taps link — the token alone authenticates them (see
     _issue_eod_token()'s docstring for the trust model) — no PIN needed.
  3. Answers ~14 questions, submits.
  4. 7:30 PM Pacific: gentle DM reminder to any scheduled driver who hasn't submitted.

Admin view: /eod-admin — daily response grid with flags for outstanding items.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    SessionLocal, get_db,
    EodSurveyResponse, DriverRosterEntry, DailyRouteAssignment,
    get_reminder_state, set_reminder_state,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/eod-survey", tags=["eod-survey"])

DRIVER_DASHBOARD_CHANNEL = os.getenv("DRIVER_DASHBOARD_CHANNEL_ID", "C0BEDCXNQNT")
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

# 2026-07-22: replaced the bare ?tid=<transporter_id> link with a signed,
# expiring token (same JWT-signing approach as slack_interactions.py's
# _issue_callout_token) — a bare transporter_id is guessable/enumerable,
# so anyone who learned another driver's ID could construct their link
# without ever seeing the real DM. The token can't be forged (signature
# verified against JWT_SECRET) and expires, while still carrying the
# driver's identity as a signed claim — so we can always determine
# exactly who submitted, same as before, just no longer via a value
# that doubles as a guessable URL parameter.
EOD_TOKEN_TTL_HOURS = 30   # covers a full shift + evening; sent as early as 3 PM the same day


def _issue_eod_token(roster_id: int, transporter_id: Optional[str], driver_name: str) -> str:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    payload = {
        "purpose": "eod_survey",
        "roster_id": roster_id,
        "transporter_id": transporter_id,
        "driver_name": driver_name,
        "exp": int(time.time()) + EOD_TOKEN_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _verify_eod_token(token: str) -> Optional[dict]:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("purpose") != "eod_survey":
        return None
    return payload

# DB-backed (ReminderThrottleState) as of 2026-07-22 — these were plain
# module-level dicts before, which reset to empty on every process
# restart/redeploy, silently forgetting "already sent today" and risking
# a duplicate send on the next deploy during the same window. Same root
# cause ReminderThrottleState itself was built to fix for other loops
# (see its docstring in database.py) — this module just hadn't been
# migrated onto it yet.
_DAILY_POST_KEY = "eod_survey_daily_post"
_REMINDER_KEY = "eod_survey_reminder"


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
    pin: Optional[str],
    db: Session,
    token: Optional[str] = None,
) -> DriverRosterEntry:
    """Resolve driver by signed token, transporter_id, or name.

    token (preferred): a signed, expiring JWT (_issue_eod_token()) — can't
    be forged or guessed, unlike a bare transporter_id, while still
    carrying the driver's identity as a verified claim. This is what
    post_daily_survey_message()/send_eod_reminders() put in the real DM
    link as of 2026-07-22.

    transporter_id (legacy/manual): still accepted directly, same trust
    model as before (link possession = identity) — kept for any old
    links or manual testing, but no longer generated for new DMs.

    Both skip the PIN check entirely. PIN is only required as a fallback
    on the generic /eod link (no token/transporter_id) — a bare typed
    name alone isn't proof of identity, unlike a link only that one
    person ever received.
    """
    entry: Optional[DriverRosterEntry] = None

    if token:
        claims = _verify_eod_token(token)
        if not claims:
            raise HTTPException(status_code=401, detail="This link has expired or is invalid.")
        entry = db.query(DriverRosterEntry).filter_by(
            id=claims.get("roster_id"), is_active=True
        ).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Driver not found.")
        return entry

    if transporter_id:
        entry = db.query(DriverRosterEntry).filter_by(
            position_id=transporter_id, is_active=True
        ).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Driver not found.")
        return entry

    if driver_name_hint:
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

    if not pin:
        raise HTTPException(status_code=401, detail="PIN required.")
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
    # Auth — token (preferred) or transporter_id skip the PIN check; pin
    # is only required with neither (see _authenticate_driver()'s docstring)
    token: Optional[str] = None
    transporter_id: Optional[str] = None
    driver_name_hint: Optional[str] = None
    pin: Optional[str] = None

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
    pin: Optional[str] = None,
    transporter_id: Optional[str] = None,
    driver_name_hint: Optional[str] = None,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return driver info + today's pre-fill data. PIN only required
    with neither token nor transporter_id — see _authenticate_driver()'s
    docstring."""
    entry = _authenticate_driver(transporter_id, driver_name_hint, pin, db, token=token)
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
    entry = _authenticate_driver(req.transporter_id, req.driver_name_hint, req.pin, db, token=req.token)

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


@router.post("/trigger-daily-post")
def trigger_daily_post(force: bool = False):
    """Manual trigger for the 3 PM daily survey-link DM — for recovery if
    the automatic 3-4 PM window was missed (e.g. a redeploy landed
    mid-window, which also resets the old in-memory guard this used to
    rely on). Safe to call any time; force=True bypasses the hour check
    but still respects "already sent today"."""
    return post_daily_survey_message(force=force)


@router.post("/trigger-reminders")
def trigger_reminders(force: bool = False):
    """Manual trigger for the 7:30 PM not-yet-submitted reminder DM. Same
    recovery/safety rationale as /trigger-daily-post."""
    return send_eod_reminders(force=force)


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

def post_daily_survey_message(force: bool = False) -> dict:
    """3 PM Pacific: DM every driver on today's schedule with their personal
    survey link. Pass force=True to send regardless of the 3-4 PM window
    (manual recovery if the automatic window was missed, e.g. a redeploy
    landed mid-window) — still respects the "already sent today" guard
    either way, so this is always safe to call."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    today = now.date()

    db = SessionLocal()
    try:
        state = get_reminder_state(db, _DAILY_POST_KEY)
        if state.get("last_posted_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if not force and (now.hour < 15 or now.hour >= 16):
            return {"status": "outside_window", "date": today.isoformat()}

        scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
        if not scheduled:
            logger.info("EOD daily DM: no drivers scheduled today, skipping.")
            set_reminder_state(db, _DAILY_POST_KEY, {"last_posted_date": today.isoformat()})
            return {"status": "no_schedule", "date": today.isoformat()}

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

            eod_token = _issue_eod_token(roster_entry.id, roster_entry.position_id, roster_entry.payroll_name)
            url = f"{APP_URL}/eod?token={eod_token}"
            first_name = assignment.driver_name.split()[0]
            msg = (
                f"🏁 Hi {first_name}! Time to complete your *End of Day Survey* before you head out.\n"
                f"It only takes 2 minutes.\n"
                f"👉 *<{url}|Complete Your Survey>*"
            )
            _dm(roster_entry.slack_member_id, msg)
            sent += 1

        set_reminder_state(db, _DAILY_POST_KEY, {"last_posted_date": today.isoformat()})
        logger.info("EOD daily DMs sent to %d driver(s) (%d had no Slack ID)", sent, no_slack)
        return {"status": "sent", "date": today.isoformat(), "sent": sent, "no_slack_id": no_slack, "total": len(scheduled)}
    except Exception as exc:
        logger.warning("EOD daily DM loop failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
    finally:
        db.close()


def send_eod_reminders(force: bool = False) -> dict:
    """7:30 PM Pacific: DM any scheduled driver who hasn't submitted yet.
    Pass force=True for manual recovery outside the normal window — still
    respects the "already ran today" guard, safe to call any time."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    today = now.date()

    db = SessionLocal()
    try:
        state = get_reminder_state(db, _REMINDER_KEY)
        if state.get("last_run_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if not force:
            if now.hour < 19 or (now.hour == 19 and now.minute < 30):
                return {"status": "outside_window", "date": today.isoformat()}
            if now.hour >= 22:
                return {"status": "outside_window", "date": today.isoformat()}

        set_reminder_state(db, _REMINDER_KEY, {"last_run_date": today.isoformat()})

        scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
        if not scheduled:
            return {"status": "no_schedule", "date": today.isoformat()}

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

            eod_token = _issue_eod_token(roster_entry.id, roster_entry.position_id, roster_entry.payroll_name)
            url = f"{APP_URL}/eod?token={eod_token}"
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
        return {"status": "sent", "date": today.isoformat(), "sent": sent, "total": len(scheduled)}
    finally:
        db.close()
