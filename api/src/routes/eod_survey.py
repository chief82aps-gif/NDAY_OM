"""
End of Day Survey module.

Flow:
  1. Daily 7 PM Pacific: one mass DM to every driver scheduled today who
     hasn't already submitted — their personal, signed survey link →
     /eod?token=XXXX. Simplified 2026-07-27 from an earlier per-driver
     ETA-anchored/escalating-ping design that proved too complicated to
     reason about day to day — see run_eod_survey_check()'s docstring.
  2. Driver taps link — the token alone authenticates them (see
     _issue_eod_token()'s docstring for the trust model) — no PIN needed.
  3. Answers ~14 questions, submits.

Admin view: /eod-admin — daily response grid with flags for outstanding items.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    SessionLocal, get_db,
    EodSurveyResponse, DriverRosterEntry, DailyRouteAssignment,
    get_reminder_state, set_reminder_state,
)
from api.src.feature_flags import get_flag

logger = logging.getLogger(__name__)
PACIFIC = ZoneInfo("America/Los_Angeles")


def _pacific_today() -> date:
    """Shifts are Pacific-anchored, but Render's server clock runs UTC —
    naive date.today() drifts a calendar day ahead of the real business
    day for roughly a third of every day (evening Pacific = already
    tomorrow UTC). Confirmed live 2026-07-27: a submission taken in
    Pacific evening stored survey_date one day ahead of what the
    Pacific-aware category digest computed as "today", so the digest
    found nothing. Every date.today() in this module must go through
    this instead."""
    return datetime.now(PACIFIC).date()
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
    vin: Optional[str]
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
    today = date.fromisoformat(target_date) if target_date else _pacific_today()
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
        vin=assignment.vin if assignment else None,
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
    _alert_mgt_on_serious_flags(req, display_name, survey_date, resp.submitted_at, db)

    # Offer the optional sentiment follow-up right after EOD submission —
    # added 2026-07-26, bundled here rather than as its own separate DM/
    # reminder loop. Soft-fails: a driver's EOD submission must never be
    # blocked by this being unavailable.
    sentiment_token = None
    try:
        from api.src.routes.sentiment_survey import _issue_sentiment_token
        if get_flag("SENTIMENT_SURVEY_ACTIVE"):
            sentiment_token = _issue_sentiment_token(entry.id, display_name, survey_date)
    except Exception as exc:
        logger.warning("Sentiment token issuance failed: %s", exc)

    return {"status": "submitted", "driver": display_name, "flags": flags, "sentiment_token": sentiment_token}


def _alert_mgt_on_serious_flags(req: "EodSubmitRequest", driver_name: str, survey_date: date, submitted_at: Optional[datetime] = None, db: Optional[Session] = None) -> None:
    # Timestamp shown on every alert below, added 2026-08-01 per explicit
    # request ("reviewers can see what time it was completed") -- these
    # alerts previously only showed the survey_date (day), no time.
    # submitted_at is naive UTC (EodSurveyResponse's own default), so
    # convert to Pacific for display, matching this file's other
    # Pacific-local-time formatting.
    time_str = ""
    if submitted_at:
        local = submitted_at.replace(tzinfo=timezone.utc).astimezone(PACIFIC)
        time_str = f" at {local.strftime('%-I:%M %p')}"

    # Van issues get their own immediate, unconditional (no feature flag)
    # alert straight to #nday-fleet -- added 2026-07-30 per explicit
    # request ("make sure all van reports are reaching #nday-fleet").
    # Previously the ONLY delivery path for a van issue was the once-daily
    # digest (send_daily_eod_category_digests(), gated behind
    # EOD_CATEGORY_DIGEST_ACTIVE, default false) -- if that flag was ever
    # left off, a reported van issue never reached Slack at all. This
    # fires on every submission regardless of that flag, same
    # unconditional pattern as crash/injury/incident below. The digest
    # stays as-is too, as an end-of-day rollup for whoever has it enabled.
    if req.van_issues:
        try:
            _slack().chat_postMessage(
                channel=FLEET_CHANNEL,
                text=(
                    f"🔧 *Van Issue* reported via EOD Survey — *{driver_name}*"
                    + (f" (Van {req.van_number})" if req.van_number else "")
                    + f" ({survey_date.isoformat()}{time_str}).\n{req.van_issue_description or 'See survey for details.'}"
                ),
            )
        except Exception as exc:
            logger.warning("EOD van-issue fleet alert failed: %s", exc)

    # Management-contact requests get an immediate DM straight to HR --
    # added 2026-08-01 per explicit request. HR is the only destination
    # (confirmed: "hr only they will notify the appropriate parties"),
    # not #nday-mgt. Previously this flag only surfaced in the once-daily
    # digest (send_daily_eod_category_digests(), gated behind
    # EOD_CATEGORY_DIGEST_ACTIVE) -- unconditional here, same pattern as
    # the van-issue alert above, so a request to speak with management
    # can't sit unseen until the next digest run.
    if req.needs_management_contact:
        try:
            from api.src.routes.document_routing import get_role_slack_ids
            client = _slack()
            for sid in get_role_slack_ids(db, "hr"):
                try:
                    client.chat_postMessage(
                        channel=sid,
                        text=(
                            f"👔 *Management Contact Requested* via EOD Survey — *{driver_name}* "
                            f"({survey_date.isoformat()}{time_str}). Please follow up and notify the "
                            f"appropriate parties."
                        ),
                    )
                except Exception as exc:
                    logger.warning("EOD management-contact HR DM failed for %s: %s", sid, exc)
        except Exception as exc:
            logger.warning("EOD management-contact HR alert failed: %s", exc)

    if not (req.crash_occurred or req.injury_occurred or req.incident_occurred):
        return
    lines = []
    date_str = f"{survey_date.isoformat()}{time_str}"
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


FLEET_CHANNEL = os.getenv("SLACK_FLEET_CHANNEL", "C0BJ8J5LGAU")   # #nday-fleet
_EOD_DIGEST_KEY = "eod_category_digest"


def send_daily_eod_category_digests(db: Session, force: bool = False) -> dict:
    """Once daily (~22:15 Pacific, after the survey's own 22:00 cutoff):
    posts three SEPARATE summarized digests for that day's responses —
    van issues to #nday-fleet, injury/management-contact flags to #nday-hr,
    everything else flagged (crash/incident/route issues/missing equipment)
    to #nday-mgt. Per explicit user direction: rolled up once a day, not an
    individual post per driver/submission.

    This is additive to _alert_mgt_on_serious_flags(), not a replacement —
    that fires immediately per-submission for crash/injury/incident as a
    safety measure (added 2026-07-22) and stays as-is; this digest exists
    so fleet/HR/management each get one end-of-day rollup relevant to
    their own follow-up, without every category flooding #nday-mgt
    individually all day."""
    if not get_flag("EOD_CATEGORY_DIGEST_ACTIVE"):
        return {"status": "inactive"}

    now = datetime.now(PACIFIC)
    today = now.date()

    if not force:
        state = get_reminder_state(db, _EOD_DIGEST_KEY)
        if state.get("last_sent_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if now.hour < 22 or (now.hour == 22 and now.minute < 15):
            return {"status": "outside_window"}

    rows = db.query(EodSurveyResponse).filter_by(survey_date=today).all()
    date_str = today.strftime("%A, %B %-d")
    try:
        client = _slack()
    except Exception:
        client = None

    fleet_lines = [
        f"• *{r.driver_name}*" + (f" (Van {r.van_number})" if r.van_number else "")
        + f": {r.van_issue_description or 'see survey'}"
        for r in rows if r.van_issues
    ]
    hr_lines = []
    for r in rows:
        if r.injury_occurred:
            hr_lines.append(f"• *{r.driver_name}* — injury reported")
        if r.needs_management_contact:
            hr_lines.append(f"• *{r.driver_name}* — requested management contact")
    mgt_lines = []
    for r in rows:
        if r.crash_occurred:
            mgt_lines.append(f"• *{r.driver_name}* — crash reported")
        if r.incident_occurred:
            mgt_lines.append(f"• *{r.driver_name}* — incident: {r.incident_description or 'see survey'}")
        if r.route_issues:
            mgt_lines.append(f"• *{r.driver_name}* — route issue: {r.route_issue_description or 'see survey'}")
        if not r.all_equipment_present:
            mgt_lines.append(f"• *{r.driver_name}* — missing equipment: {r.missing_equipment or 'see survey'}")

    sent_any = False
    if client:
        if fleet_lines and FLEET_CHANNEL:
            try:
                client.chat_postMessage(
                    channel=FLEET_CHANNEL,
                    text=f"🔧 *Van Issues — {date_str}* ({len(fleet_lines)})\n" + "\n".join(fleet_lines),
                )
                sent_any = True
            except Exception as exc:
                logger.warning("EOD fleet digest post failed: %s", exc)
        if hr_lines:
            from api.src.routes.document_routing import get_role_slack_ids
            for sid in get_role_slack_ids(db, "hr"):
                try:
                    client.chat_postMessage(
                        channel=sid,
                        text=f"👔 *EOD Flags for HR — {date_str}* ({len(hr_lines)})\n" + "\n".join(hr_lines),
                    )
                    sent_any = True
                except Exception as exc:
                    logger.warning("EOD HR digest post failed: %s", exc)
        if mgt_lines:
            try:
                client.chat_postMessage(
                    channel=MGT_CHANNEL,
                    text=f"⚠️ *EOD Operational Flags — {date_str}* ({len(mgt_lines)})\n" + "\n".join(mgt_lines),
                )
                sent_any = True
            except Exception as exc:
                logger.warning("EOD mgt digest post failed: %s", exc)

    set_reminder_state(db, _EOD_DIGEST_KEY, {"last_sent_date": today.isoformat()})
    return {
        "status": "sent" if sent_any else "no_flags",
        "date": today.isoformat(),
        "fleet": len(fleet_lines), "hr": len(hr_lines), "mgt": len(mgt_lines),
    }


@router.post("/trigger-category-digest")
def trigger_category_digest(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery."""
    return send_daily_eod_category_digests(db, force=force)


EOD_COMPLETION_REPORT_HOUR = int(os.getenv("EOD_COMPLETION_REPORT_HOUR", "9"))
_EOD_COMPLETION_REPORT_KEY_PREFIX = "eod_completion_time_report_"


def send_eod_completion_time_report(db: Session, force: bool = False) -> dict:
    """By 9 AM Pacific: posts every driver's EOD survey completion time for
    the PRIOR day to #nday-hr, so reviewers can see who submitted on time
    vs. late -- added 2026-08-01 per explicit request. Distinct from
    send_daily_eod_category_digests() above, which only surfaces flagged
    items, same-day, at ~22:15. This lists every submission, not just
    flagged ones. Gated by EOD_COMPLETION_REPORT_ACTIVE (default false)."""
    if not get_flag("EOD_COMPLETION_REPORT_ACTIVE"):
        return {"status": "inactive"}

    now = datetime.now(PACIFIC)
    report_date = now.date() - timedelta(days=1)

    if not force and now.hour != EOD_COMPLETION_REPORT_HOUR:
        return {"status": "not_send_hour", "date": report_date.isoformat()}

    state_key = f"{_EOD_COMPLETION_REPORT_KEY_PREFIX}{report_date.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": report_date.isoformat()}

    rows = (
        db.query(EodSurveyResponse)
        .filter(EodSurveyResponse.survey_date == report_date)
        .order_by(EodSurveyResponse.submitted_at)
        .all()
    )
    date_str = report_date.strftime("%A, %B %-d")

    lines = []
    for r in rows:
        if r.submitted_at:
            local = r.submitted_at.replace(tzinfo=timezone.utc).astimezone(PACIFIC)
            time_str = local.strftime("%-I:%M %p")
        else:
            time_str = "no timestamp on file"
        lines.append(f"• *{r.driver_name}* — {time_str}")

    text = (
        f"🕐 *EOD Completion Times — {date_str}* ({len(rows)} submitted)\n"
        + ("\n".join(lines) if lines else "No EOD surveys submitted.")
    )

    sent = False
    try:
        client = _slack()
        from api.src.routes.document_routing import get_role_slack_ids
        for sid in get_role_slack_ids(db, "hr"):
            try:
                client.chat_postMessage(channel=sid, text=text)
                sent = True
            except Exception as exc:
                logger.warning("EOD completion-time report post failed: %s", exc)
    except Exception as exc:
        logger.warning("EOD completion-time report failed: %s", exc)

    if not sent:
        return {"status": "no_slack_token", "date": report_date.isoformat(), "count": len(rows)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "date": report_date.isoformat(), "count": len(rows)}


@router.post("/trigger-completion-report")
def trigger_completion_report(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery."""
    return send_eod_completion_time_report(db, force=force)


@router.delete("/responses/{response_id}")
def delete_response(response_id: int, db: Session = Depends(get_db)):
    """Admin cleanup — e.g. removing a real click-through test submission
    that accidentally landed on a real driver's record before phantom test
    employees existed (2026-07-26)."""
    row = db.query(EodSurveyResponse).filter_by(id=response_id).first()
    if not row:
        raise HTTPException(404, f"Response {response_id} not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted", "id": response_id}


@router.get("/responses")
def list_responses(survey_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Admin: list all responses for a date (defaults to today)."""
    target = date.fromisoformat(survey_date) if survey_date else _pacific_today()
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

    today = _pacific_today()
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
def trigger_check(force: bool = False, db: Session = Depends(get_db)):
    """Manual, safe-to-call-anytime trigger for the mass-send check — same
    function the 60s loop calls. force=True bypasses the 1900-hour gate and
    the already-sent guard, for an ad-hoc "DM everyone who hasn't submitted
    yet" send at any time of day."""
    return run_eod_survey_check(db, force=force)


@router.get("/missing")
def missing_drivers(survey_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Admin: drivers scheduled today who haven't submitted."""
    target = date.fromisoformat(survey_date) if survey_date else _pacific_today()
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


# ─── Mass send at 1900 Pacific ────────────────────────────────────────────────
#
# Added 2026-07-24, simplified 2026-07-27 per explicit direction: the
# original fixed 3 PM schedule sent every driver the same DM regardless of
# their actual wave/route (a driver with a 6:30-7:30 PM ETA got asked about
# "clock-out"/"van condition at end of day" hours early — a plausible
# contributor to the near-zero completion rate found the same day). That
# was replaced with per-driver ETA-anchored timing plus escalating pings,
# which then proved too complicated to reason about day to day. Now: one
# flat mass send at 1900 Pacific to every driver scheduled today who hasn't
# already submitted. No per-driver anchor math, no rescue-specific offset,
# no repeat pings — a driver who misses the window just doesn't get a
# second nudge today. Still skips the send entirely if dispatch has already
# posted an "All In" message to #nday-mgt for the day, since that's a
# single cheap global check, not per-driver complexity. No feature flag —
# always on, same as before.
_ALL_IN_KEY_PREFIX = "eod_all_in_"
_MASS_SEND_KEY_PREFIX = "eod_mass_send_"


def _all_in_posted_today(db: Session) -> bool:
    """True once dispatch has posted a message containing 'all in' to
    #nday-mgt today. Cached in ReminderThrottleState for the rest of the
    day once found, so this only re-scans Slack history on ticks before
    it's been seen."""
    today = _pacific_today()
    key = f"{_ALL_IN_KEY_PREFIX}{today.isoformat()}"

    state = get_reminder_state(db, key)
    if state.get("posted"):
        return True

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return False
    try:
        midnight_pt = datetime.combine(today, datetime.min.time(), tzinfo=PACIFIC)
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


def run_eod_survey_check(db: Session, force: bool = False) -> dict:
    """Called every 60s from main.py's _eod_survey_loop — see module note
    above. Fires exactly once, at 1900 Pacific: mass-DMs the survey link to
    every driver scheduled today who hasn't already submitted. Dedup'd via
    ReminderThrottleState so repeat ticks within the 1900 hour don't resend.
    force=True (manual /trigger-check only) bypasses both the hour gate and
    the already-sent-today guard, for an ad-hoc "catch up the stragglers
    right now" send at any time of day."""
    from api.src.driver_identity import resolve_roster_entry

    now_pt = datetime.now(PACIFIC)
    today = now_pt.date()
    now_utc_naive = datetime.utcnow()

    if not force and now_pt.hour != 19:
        return {"status": "not_send_hour", "date": today.isoformat()}

    if not force and _all_in_posted_today(db):
        return {"status": "all_in_posted", "date": today.isoformat()}

    state_key = f"{_MASS_SEND_KEY_PREFIX}{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
    if not scheduled:
        return {"status": "no_schedule", "date": today.isoformat()}

    sent = already_submitted = no_slack = 0

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

        eod_token = _issue_eod_token(roster_entry.id, roster_entry.position_id, roster_entry.payroll_name)
        url = f"{APP_URL}/eod?token={eod_token}"
        first_name = a.driver_name.split()[0] if a.driver_name else "there"
        msg = (
            f"🏁 Hi {first_name}! Time to complete your *End of Day Survey*.\n"
            f"It only takes 2 minutes."
        )
        _dm(roster_entry.slack_member_id, msg, button_url=url, button_text="📋 Complete Survey")
        sent += 1

    set_reminder_state(db, state_key, {"sent_at": now_utc_naive.isoformat(), "sent": sent})
    return {
        "status": "sent", "date": today.isoformat(),
        "sent": sent, "already_submitted": already_submitted, "no_slack_id": no_slack,
    }


@router.get("/debug-today")
def debug_today(db: Session = Depends(get_db)) -> dict:
    """Read-only — per-driver view of exactly what run_eod_survey_check()
    sees right now (already submitted? has a Slack ID? has the 1900 mass
    send already gone out today?). Never sends anything itself."""
    from api.src.driver_identity import resolve_roster_entry

    now_pt = datetime.now(PACIFIC)
    today = now_pt.date()
    now_utc_naive = datetime.utcnow()

    mass_send_state = get_reminder_state(db, f"{_MASS_SEND_KEY_PREFIX}{today.isoformat()}")

    scheduled = db.query(DailyRouteAssignment).filter_by(assignment_date=today).all()
    out = []
    for a in scheduled:
        roster_entry = None
        if a.roster_id is not None:
            roster_entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
        if roster_entry is None:
            roster_entry = resolve_roster_entry(a.driver_name, db)

        row = {"driver_name": a.driver_name, "wave": a.wave}
        submitted = roster_entry and db.query(EodSurveyResponse).filter_by(roster_id=roster_entry.id, survey_date=today).first()
        row["already_submitted"] = bool(submitted)
        row["has_slack_id"] = bool(roster_entry and roster_entry.slack_member_id)
        out.append(row)

    return {
        "date": today.isoformat(), "now_utc": now_utc_naive.isoformat(),
        "mass_send_hour": 19, "mass_send_sent_at": mass_send_state.get("sent_at"),
        "all_in_posted": _all_in_posted_today(db),
        "drivers": out,
    }
