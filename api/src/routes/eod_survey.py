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
from datetime import datetime, date, timedelta, timezone
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
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
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


def _dm(user_id: str, text: str, button_url: Optional[str] = None, button_text: str = "Open") -> None:
    try:
        kwargs: dict = {"channel": user_id, "text": text}
        if button_url:
            # url-style button — opens the link directly, no interactivity
            # endpoint needed (unlike the callout/DVIC action_id buttons).
            kwargs["blocks"] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": button_text, "emoji": True},
                        "style": "primary",
                        "url": button_url,
                        "action_id": "eod_survey_open",
                    }],
                },
            ]
        _slack().chat_postMessage(**kwargs)
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
    # Optional — schedule_upload-sourced drivers (most of the roster) never
    # get a real Amazon transporter/position ID, only ADP/DVIC-ingested ones
    # do. This was previously required `str`, which raised an unhandled
    # Pydantic validation error (opaque 500) for any such driver.
    transporter_id: Optional[str]
    driver_name: str
    roster_id: int
    van_number: Optional[str]
    wave: Optional[str]
    role: str
    already_submitted: bool
    submitted_at: Optional[str]
    survey_date: str


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

    # Crash — its own explicit question, routed to the real CrashReport
    # engine. crash_report_id is resolved by the frontend (existing
    # report found today, or a new draft created via
    # POST /crash-report/start) before submission.
    crash_occurred: bool = False
    crash_report_id: Optional[int] = None

    # Generic catch-all — near-miss, non-crash property damage, safety
    # concern. Distinct from crash/injury above.
    incident_occurred: bool = False
    incident_report_filed: Optional[bool] = None
    incident_description: Optional[str] = None

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
    target_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return driver info + pre-fill data for target_date (defaults to
    today). target_date exists so the outstanding-items gate (added
    2026-07-26) can send a driver straight to a *missed* day's survey —
    "explain why you missed it" is just completing that overdue survey,
    not a separate form. PIN only required with neither token nor
    transporter_id — see _authenticate_driver()'s docstring."""
    entry = _authenticate_driver(transporter_id, driver_name_hint, pin, db, token=token)
    today = date.fromisoformat(target_date) if target_date else date.today()
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
        survey_date=today.isoformat(),
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
        crash_occurred=req.crash_occurred,
        crash_report_id=req.crash_report_id,
        incident_occurred=req.incident_occurred,
        incident_report_filed=req.incident_report_filed,
        incident_description=req.incident_description,
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
    if req.crash_occurred:
        flags.append("🚨 Crash reported")
    if req.incident_occurred:
        flags.append("⚠️ Incident reported")
    if req.injury_occurred:
        flags.append("🚨 Injury reported")
    if req.van_issues:
        flags.append("🔧 Van issue: " + (req.van_issue_description or "see survey"))
    if req.needs_management_contact:
        flags.append("👔 Requests management contact")

    # Real-time #nday-mgt alert for crash/injury/incident — added
    # 2026-07-22. Previously these only showed up as a flag on
    # /eod-admin, reviewed whenever someone happened to check the
    # dashboard — a real safety gap for something as urgent as a crash
    # or injury. This fires unconditionally on submission (not from the
    # frontend's own report-creation calls), so it can't be missed by a
    # dropped network call mid-flow.
    _alert_mgt_on_serious_flags(req, display_name, survey_date)

    # Offer the optional sentiment follow-up right after EOD submission —
    # added 2026-07-26, bundled here rather than as its own separate DM/
    # reminder loop. Soft-fails: a driver's EOD submission must never be
    # blocked by this being unavailable.
    sentiment_token = None
    try:
        from api.src.routes.sentiment_survey import _issue_sentiment_token, SENTIMENT_SURVEY_ACTIVE
        if SENTIMENT_SURVEY_ACTIVE:
            sentiment_token = _issue_sentiment_token(entry.id, display_name, survey_date)
    except Exception as exc:
        logger.warning("Sentiment token issuance failed: %s", exc)

    return {"status": "submitted", "driver": display_name, "flags": flags, "sentiment_token": sentiment_token}


def _alert_mgt_on_serious_flags(req: "EodSubmitRequest", driver_name: str, survey_date: date) -> None:
    if not (req.crash_occurred or req.injury_occurred or req.incident_occurred):
        return
    lines = []
    date_str = survey_date.isoformat()
    if req.crash_occurred:
        if req.crash_report_id:
            lines.append(
                f"🚨 *Crash* reported via EOD Survey — *{driver_name}* ({date_str}). "
                f"<{APP_URL}/crash-report/{req.crash_report_id}|Open Crash Report> — follow up immediately."
            )
        else:
            lines.append(
                f"🚨 *Crash* reported via EOD Survey — *{driver_name}* ({date_str}). "
                f"No crash report on file yet — <{APP_URL}/crash-report|start one> and follow up immediately."
            )
    if req.injury_occurred:
        lines.append(
            f"🚨 *Injury* reported via EOD Survey — *{driver_name}* ({date_str}). "
            f"Injury report submitted: {'Yes' if req.injury_report_submitted else 'NOT YET — ' + APP_URL + '/injury-report'}."
        )
    if req.incident_occurred:
        lines.append(
            f"⚠️ *Incident* reported via EOD Survey — *{driver_name}* ({date_str}). "
            f"Report filed: {'Yes' if req.incident_report_filed else 'Not yet — dispatch should follow up.'} "
            f"Details: {req.incident_description or '(none given)'}"
        )
    try:
        _slack().chat_postMessage(channel=MGT_CHANNEL, text="\n".join(lines))
    except Exception as exc:
        logger.warning("EOD serious-flag mgt alert failed: %s", exc)


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


@router.post("/test-send")
def send_test_eod_dm(sample_driver_name: str, target_slack_id: str, db: Session = Depends(get_db)):
    """Preview the real EOD survey DM by sending it to a reviewer instead
    of the sampled driver — same pattern as rostering.py's
    /day-of-dms/{date}/test. Added 2026-07-26 to directly verify Slack
    delivery is working at all, separate from whether drivers are
    completing it. Does not touch any real state (no ReminderThrottleState
    write), so it's safe to call repeatedly."""
    from api.src.driver_identity import resolve_roster_entry
    entry = resolve_roster_entry(sample_driver_name, db)
    if not entry:
        raise HTTPException(404, f"No active roster match for '{sample_driver_name}'.")

    today = date.today()
    eod_token = _issue_eod_token(entry.id, entry.position_id, entry.payroll_name)
    url = f"{APP_URL}/eod?token={eod_token}"
    first_name = entry.payroll_name.split(",", 1)[1].strip().split()[0] if "," in entry.payroll_name else entry.payroll_name.split()[0]
    msg = (
        f"🏁 [TEST] Hi {first_name}! Time to complete your *End of Day Survey* before you head out.\n"
        f"It only takes 2 minutes."
    )
    _dm(target_slack_id, msg, button_url=url, button_text="📋 Complete Survey")
    return {"status": "sent", "sampled_driver": entry.payroll_name, "target_slack_id": target_slack_id, "survey_date": today.isoformat()}


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


@router.post("/trigger-check")
def trigger_check(db: Session = Depends(get_db)):
    """Manual, safe-to-call-anytime trigger for the real (2026-07-24+)
    per-driver ETA-driven check — same function the 60s loop calls. Useful
    for testing/recovery without waiting for the next tick."""
    return run_eod_survey_check(db)


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
    if r.crash_occurred:
        flags.append("crash")
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
        "crash_occurred": r.crash_occurred,
        "crash_report_id": r.crash_report_id,
        "incident_occurred": r.incident_occurred,
        "incident_report_filed": r.incident_report_filed,
        "incident_description": r.incident_description,
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
                f"It only takes 2 minutes."
            )
            _dm(roster_entry.slack_member_id, msg, button_url=url, button_text="📋 Complete Survey")
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
                f"It only takes a couple of minutes."
            )
            _dm(roster_entry.slack_member_id, msg, button_url=url, button_text="📋 Complete Survey")

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


# ─── Per-driver ETA-driven timing (replaces the flat 3PM/7:30PM schedule) ────
#
# Added 2026-07-24 per explicit direction: the old fixed schedule sent every
# driver the same DM at 3 PM regardless of their actual wave/route — a driver
# with a 6:30-7:30 PM ETA got asked about "clock-out"/"van condition at end
# of day" hours before they were actually done, which is a very plausible
# contributor to the near-zero completion rate found the same day. This
# anchors each driver's timing to their own live Cortex-pace ETA instead
# (rostering.get_driver_expected_return_dt() — the same number the Wave
# Status board shows as "Return"):
#   - normal drivers: send 1 hour BEFORE their ETA
#   - drivers pulled onto a rescue today: a rescue means they're back LATER
#     than planned, not earlier, so send 1 hour AFTER their ORIGINAL
#     (pre-rescue, static wave+route_duration) expected return instead —
#     rostering.get_driver_original_return_dt(), which does not shift once
#     they're pulled off-route.
# Once the anchor time (the actual ETA/original-return, not the -1h/+1h send
# time) has passed and they still haven't submitted, repeat "ping" reminders
# fire every 30 min — until either they submit, dispatch posts an "All In"
# message in #nday-mgt for the day, or it's past 22:00 Pacific, whichever
# comes first. No feature flag: this fully replaces the old unconditional
# loop, same as it was (never gated), just smarter about timing.
_PING_INTERVAL_MINUTES = 30
_ALL_IN_KEY_PREFIX = "eod_all_in_"
_DRIVER_DM_KEY_PREFIX = "eod_dm_"


def _all_in_posted_today(db: Session) -> bool:
    """True once dispatch has posted a message containing 'all in' to
    #nday-mgt today. Cached in ReminderThrottleState for the rest of the
    day once found, so this only re-scans Slack history on ticks before
    it's been seen."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    today = datetime.now(tz).date()
    key = f"{_ALL_IN_KEY_PREFIX}{today.isoformat()}"

    state = get_reminder_state(db, key)
    if state.get("posted"):
        return True

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return False
    try:
        midnight_pt = datetime.combine(today, datetime.min.time(), tzinfo=tz)
        since_ts = str(midnight_pt.timestamp())
        client = _slack()
        resp = client.conversations_history(channel=MGT_CHANNEL, oldest=since_ts, limit=100)
        for msg in resp.get("messages", []):
            if "all in" in (msg.get("text") or "").lower():
                set_reminder_state(db, key, {"posted": True, "ts": msg.get("ts")})
                return True
    except Exception as exc:
        logger.warning("All-in check failed: %s", exc)
    return False


def run_eod_survey_check(db: Session) -> dict:
    """Called every 60s from main.py's _eod_survey_loop — see module note
    above. Per scheduled driver: send the initial survey DM once their
    anchor time arrives, then escalate with a repeat ping every
    _PING_INTERVAL_MINUTES once the real ETA/original-return has passed,
    until they submit, an 'All In' post is seen, or it's past 22:00 PT."""
    from api.src.database import RescueEvent
    from api.src.routes.rostering import get_driver_expected_return_dt, get_driver_original_return_dt
    from api.src.driver_identity import resolve_roster_entry

    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(tz)
    today = now_pt.date()
    now_utc_naive = datetime.utcnow()

    all_in = _all_in_posted_today(db)
    cutoff = now_pt.hour >= 22

    scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
    if not scheduled:
        return {"status": "no_schedule", "date": today.isoformat()}

    sent = pinged = already_submitted = no_slack = no_eta = 0

    for a in scheduled:
        roster_entry: Optional[DriverRosterEntry] = None
        if a.roster_id is not None:
            roster_entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
        if roster_entry is None:
            roster_entry = resolve_roster_entry(a.driver_name, db)

        if roster_entry and db.query(EodSurveyResponse).filter_by(
            roster_id=roster_entry.id, survey_date=today
        ).first():
            already_submitted += 1
            continue

        if not roster_entry or not roster_entry.slack_member_id:
            no_slack += 1
            continue

        rescued_today = db.query(RescueEvent).filter(
            RescueEvent.event_date == today,
            RescueEvent.rescuing_driver_name == a.driver_name,
        ).first() is not None

        # 1900 Pacific is now a hard, guaranteed send time regardless of
        # whether Cortex/pace data ever loads — confirmed live 2026-07-25
        # that CortexSnapshot (the live-pace ETA source) is only populated
        # by a separate, manual "upload every ~2 hours" endpoint
        # (cortex_tracking.py's POST /cortex-tracking/snapshot), not by the
        # daily morning Cortex file check_and_notify() processes, so on a
        # day nobody feeds that endpoint, no driver would ever get a live
        # ETA and this redesign would silently never fire. Per explicit
        # direction: fire at 1900 no matter what; the only reason to send
        # earlier is real data (live Cortex pace, or the static
        # wave+route_duration estimate) suggesting an earlier time.
        today_1900_pt = now_pt.replace(hour=19, minute=0, second=0, microsecond=0)
        today_1900_utc_naive = today_1900_pt.astimezone(timezone.utc).replace(tzinfo=None)

        if rescued_today:
            # Rescued drivers keep their own dedicated exception — they're
            # legitimately expected out later than a normal return, so they
            # are NOT capped at 1900 the way everyone else is.
            anchor = get_driver_original_return_dt(a.driver_name, today, db)
            send_at = anchor + timedelta(hours=1) if anchor else None
            if anchor is None or send_at is None:
                no_eta += 1
                continue
        else:
            anchor = (
                get_driver_expected_return_dt(a.driver_name, today, db)
                or get_driver_original_return_dt(a.driver_name, today, db)
            )
            if anchor is not None:
                # Real data (live or static) exists — cap only the send
                # time at 1900, not the anchor itself. The anchor still
                # drives when ping escalation starts, and it shouldn't be
                # dragged earlier than a driver's actual expected return
                # just because of the 1900 send-time ceiling.
                send_at = min(anchor - timedelta(hours=1), today_1900_utc_naive)
            else:
                # No data at all — 1900 is the only thing we have.
                anchor = today_1900_utc_naive
                send_at = today_1900_utc_naive

        state_key = f"{_DRIVER_DM_KEY_PREFIX}{today.isoformat()}_{roster_entry.id}"
        state = get_reminder_state(db, state_key)

        eod_token = _issue_eod_token(roster_entry.id, roster_entry.position_id, roster_entry.payroll_name)
        url = f"{APP_URL}/eod?token={eod_token}"
        first_name = a.driver_name.split()[0] if a.driver_name else "there"

        if not state.get("initial_sent_at"):
            # The initial send is NEVER blocked by all_in/cutoff — every
            # driver gets at least one real attempt no matter how late in
            # the day the loop gets a chance to run (confirmed live
            # 2026-07-25: a delayed redeploy landing after 22:00 meant this
            # check, applied blanket at the top of the loop before this
            # fix, silently suppressed every driver's first-ever send for
            # the rest of the day). all_in/cutoff only suppress the repeat
            # ping below, which is what they were actually meant to stop.
            if now_utc_naive >= send_at:
                verb = "you're needed for a rescue before it," if rescued_today else "before you head out"
                msg = (
                    f"🏁 Hi {first_name}! Time to complete your *End of Day Survey* — "
                    f"{'once your rescue wraps up' if rescued_today else verb}.\n"
                    f"It only takes 2 minutes."
                )
                _dm(roster_entry.slack_member_id, msg, button_url=url, button_text="📋 Complete Survey")
                set_reminder_state(db, state_key, {"initial_sent_at": now_utc_naive.isoformat(), "last_ping_at": None})
                sent += 1
            continue

        if all_in or cutoff:
            continue

        if now_utc_naive >= anchor:
            last_ping = state.get("last_ping_at")
            last_ping_dt = datetime.fromisoformat(last_ping) if last_ping else None
            if not last_ping_dt or (now_utc_naive - last_ping_dt) >= timedelta(minutes=_PING_INTERVAL_MINUTES):
                msg = (
                    f"🏁 Hi {first_name} — still need your *End of Day Survey* for today. "
                    f"Takes 2 minutes, please complete it now."
                )
                _dm(roster_entry.slack_member_id, msg, button_url=url, button_text="📋 Complete Survey")
                set_reminder_state(db, state_key, {**state, "last_ping_at": now_utc_naive.isoformat()})
                pinged += 1

    return {
        "status": "checked", "date": today.isoformat(),
        "sent": sent, "pinged": pinged, "already_submitted": already_submitted,
        "no_slack_id": no_slack, "no_eta_yet": no_eta,
        "all_in": all_in, "past_2200": cutoff,
    }
