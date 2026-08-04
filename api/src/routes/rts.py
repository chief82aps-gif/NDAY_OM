"""Return to Station (RTS) — driver debrief before heading back.

Flow:
  1. Driver taps "Return to Station" in Slack.
  2. If dispatch has already assigned them a rescue, they're routed straight into
     the existing rescue Stage 2 flow instead of the debrief (no separate prompt).
  3. Otherwise they get a personal link to a short debrief: for every package
     they're bringing back, a deliberate reason -- pre-populated (with tracking
     ID + Amazon's own reason code, if any) from the latest Packages export
     (packages.py) for their transporter ID. Redesigned 2026-08-04: any package
     Amazon hasn't recorded a reason for yet forces the driver to pick one of
     Amazon's own RTS codes before they can submit -- this is the actual fix for
     the "NO RTS CODE SELECTED" scorecard defect (quality_rts.py), which Amazon's
     own docs confirm defaults to a DC DPMO defect. The old count-only Damaged/
     Reverse/Excluded/Re-Attemptable buckets are gone in favor of this per-
     package list (RtsDebriefPackage).
  4. Any package marked Still-Deliverable-Today that the driver can reach within
     a 10-15 min drive gets assigned as a reattempt; everything else heads back.
  5. Driver gets their expected return time and a go-ahead to head to the station.

Submission is blocked (400) until every package in the driver's pre-populated
list has an answer -- see submit_debrief()'s reconciliation against
packages.get_driver_packages(). Drivers can also add packages that became a
problem after the last Packages pull (source="manual").

No mapping/API dependency: reattempt drive-time eligibility is self-reported by
the driver, not computed from a live routing service.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import func

from api.src.database import (
    get_db, RtsDebrief, RtsDebriefPackage, RescueEvent, RescueContribution,
    DailyRouteAssignment, CortexSnapshot, DriverRosterEntry, CrashReport,
)
from api.src.routes.packages import get_driver_packages

# Amazon's own RTS reason codes (confirmed against real exports -- see
# api/src/ingest/quality_rts.py and api/src/ingest/packages.py) plus two
# NDAY-internal classifications that aren't delivery failures at all
# (reverse_pickup, reattemptable) and a free-text escape hatch (other).
# "amazon_code" is what the driver should also select in their own Amazon
# app -- shown to them as a reminder, since this tool can't set it there.
RTS_REASON_CODES = [
    {"code": "business_closed",      "label": "Business Closed",                    "amazon_code": "BUSINESS CLOSED"},
    {"code": "address_not_found",    "label": "Address Not Found",                  "amazon_code": "ADDRESS NOT FOUND"},
    {"code": "object_missing",       "label": "Package Missing From Van",           "amazon_code": "OBJECT MISSING"},
    {"code": "damaged",              "label": "Damaged",                            "amazon_code": "DAMAGED"},
    {"code": "otp_not_available",    "label": "OTP / ID Verification Not Available","amazon_code": "OTP NOT AVAILABLE"},
    {"code": "no_locker_available",  "label": "No Locker Available",                "amazon_code": "NO LOCKER AVAILABLE"},
    {"code": "inaccessible_location","label": "Inaccessible Delivery Location",      "amazon_code": "INACCESSIBLE DELIVERY LOCATION"},
    {"code": "refused",              "label": "Refused by Customer",                "amazon_code": "REFUSED"},
    {"code": "tr_cancelled",         "label": "Cancelled by Amazon (TR Cancelled)", "amazon_code": "TR CANCELLED"},
    {"code": "reverse_pickup",       "label": "Customer Return / SWA Pickup",       "amazon_code": None},
    {"code": "reattemptable",        "label": "Still Deliverable Today",            "amazon_code": None},
    {"code": "other",                "label": "Other (explain)",                    "amazon_code": None},
]
_VALID_REASON_CODES = {c["code"] for c in RTS_REASON_CODES}

# Amazon's raw reason-code text -> our code, for suggesting a default
# selection when Amazon already recorded one (driver just confirms).
_AMAZON_CODE_TO_OURS = {c["amazon_code"]: c["code"] for c in RTS_REASON_CODES if c["amazon_code"]}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rts", tags=["rts"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")


def _first_name(full_name: str) -> str:
    if "," in full_name:
        return full_name.split(",", 1)[1].strip()
    return full_name.split()[0] if full_name else full_name


def _find_open_rescue(driver_name: str, db: Session) -> Optional[RescueEvent]:
    """Return the driver's open, not-yet-contributed rescue assignment, if any."""
    event = (
        db.query(RescueEvent)
        .filter(
            RescueEvent.rescuing_driver_name == driver_name,
            RescueEvent.status == "Open",
        )
        .order_by(RescueEvent.created_at.desc())
        .first()
    )
    if not event:
        return None
    already_contributed = (
        db.query(RescueContribution)
        .filter(
            RescueContribution.event_id == event.event_id,
            RescueContribution.rescuing_driver_name == driver_name,
        )
        .first()
    )
    return None if already_contributed else event


def _driver_route_today(driver_name: str, shift_date: date, db: Session) -> Optional[DailyRouteAssignment]:
    return (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name == driver_name,
        )
        .first()
    )


def _expected_return_time(assignment: Optional[DailyRouteAssignment], shift_date: date, db: Session) -> Optional[str]:
    if not assignment or not assignment.route_code:
        return None
    from api.src.routes.rostering import _calc_eta
    snap = (
        db.query(CortexSnapshot)
        .filter(
            CortexSnapshot.route_code == assignment.route_code,
            CortexSnapshot.route_date == shift_date,
        )
        .order_by(CortexSnapshot.snapshot_at.desc())
        .first()
    )
    return _calc_eta(snap, assignment.wave, shift_date) if snap else None


# ─────────────────────────────────────────────────────────────────────────────
# Called directly from the Slack action handler (not over HTTP)
# ─────────────────────────────────────────────────────────────────────────────

def _drug_screen_alert(driver_name: str, db: Session) -> None:
    """Nudges dispatch when a driver with an open post-accident drug-screen
    requirement (set on crash_report.py's submit_crash_report) hits Return
    to Station — this is the 'when the driver returns' trigger the
    drug-screen reminder was waiting on, reusing the RTS tap rather than
    building separate return-detection logic."""
    pending = (
        db.query(CrashReport)
        .filter(
            CrashReport.driver_name == driver_name,
            CrashReport.drug_screen_status.in_(["pending", "scheduled"]),
            CrashReport.status.in_(["submitted", "routed_complete"]),
        )
        .order_by(CrashReport.created_at.desc())
        .first()
    )
    if not pending:
        return
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return
    from api.src.routes.document_routing import get_role_slack_ids
    dispatch_ids = get_role_slack_ids(db, "dispatch")
    if not dispatch_ids:
        return
    from slack_sdk import WebClient
    client = WebClient(token=token)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"🚨 *{driver_name}* is heading back — post-accident drug screen required "
            f"(Report {pending.report_number})."
        )}},
        {"type": "actions", "elements": [{
            "type": "button",
            "action_id": "crash_report_drug_screen_done",
            "text": {"type": "plain_text", "text": "✅ Mark Drug Screen Complete", "emoji": True},
            "style": "primary",
            "value": str(pending.id),
        }]},
    ]
    for sid in dispatch_ids:
        try:
            client.chat_postMessage(channel=sid, text="Post-accident drug screen required", blocks=blocks)
        except Exception as exc:
            logger.warning("Drug-screen RTS alert failed for %s: %s", sid, exc)


def start_rts(driver_name: str, slack_user_id: Optional[str], db: Session) -> dict:
    """Driver is heading back — either self-tapped (legacy) or pushed by
    dispatch via generate_rts() below. Returns either a rescue handoff or
    a debrief link."""
    _drug_screen_alert(driver_name, db)
    today = date.today()

    rescue = _find_open_rescue(driver_name, db)
    if rescue:
        contribute_url = (
            f"{FRONTEND_URL}/rescue/contribute"
            f"?eventId={rescue.event_id}&routeId={rescue.rescued_route_id}"
        )
        # Record this on the wave-status board same as a debrief would be —
        # otherwise a rescue-routed driver shows as "not_started" forever
        # (the columns for this exist on RtsDebrief but were never
        # populated). Nothing to debrief here, so it's resolved immediately.
        now = datetime.utcnow()
        db.add(RtsDebrief(
            token=secrets.token_urlsafe(24),
            shift_date=today,
            driver_name=driver_name,
            slack_user_id=slack_user_id,
            started_at=now,
            completed_at=now,
            routed_to_rescue=True,
            rescue_event_id=rescue.event_id,
        ))
        db.commit()
        return {
            "routed_to_rescue": True,
            "event_id": rescue.event_id,
            "contribute_url": contribute_url,
            "rescued_driver_name": rescue.rescued_driver_name,
        }

    assignment = _driver_route_today(driver_name, today, db)
    token = secrets.token_urlsafe(24)
    debrief = RtsDebrief(
        token=token,
        shift_date=today,
        driver_name=driver_name,
        slack_user_id=slack_user_id,
        route_id=assignment.route_code if assignment else None,
    )
    db.add(debrief)
    db.commit()

    return {
        "routed_to_rescue": False,
        "debrief_url": f"{FRONTEND_URL}/rts?token={token}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoints — used by the frontend debrief page
# ─────────────────────────────────────────────────────────────────────────────

class IdentifyRequest(BaseModel):
    driver_name: str
    ssn_last4: str


@router.post("/identify")
def identify(req: IdentifyRequest, db: Session = Depends(get_db)):
    """Public — name + PIN identification (same PIN as ADP kiosk / callout page),
    used when a driver opens the Return to Station link directly from the
    driver-dashboard channel instead of a personal Slack DM."""
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == req.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()
    if not roster_entry or not roster_entry.ssn_last4 or roster_entry.ssn_last4 != req.ssn_last4.strip():
        raise HTTPException(status_code=401, detail="Name or PIN is incorrect.")

    return start_rts(roster_entry.payroll_name, None, db)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch-triggered generation — replaces the driver-facing self-tap button
# (removed 2026-07-23 from slack_home.py and slack_interactions.py's
# driver-dashboard hub). Dispatch decides a driver is wrapping up their route
# and pushes the next step directly, same shape as opening a rescue
# (rescue.py's POST /rescue/events): dispatch picks the driver, the system
# resolves rescue-vs-debrief via the existing start_rts() branching, and DMs
# the driver the right link. Everything downstream (Stage 2 contribute, or
# the debrief form) is unchanged.
# ─────────────────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        from slack_sdk import WebClient
        return WebClient(token=token)
    except Exception:
        return None


def _dm_driver(slack_member_id: str, message: str) -> bool:
    client = _slack_client()
    if not client:
        logger.info("SLACK_BOT_TOKEN not set — skipping RTS driver DM")
        return False
    try:
        client.chat_postMessage(channel=slack_member_id, text=message)
        return True
    except Exception as exc:
        logger.warning("RTS driver DM failed (uid=%s): %s", slack_member_id, exc)
        return False


class GenerateRequest(BaseModel):
    driver_name: str
    generated_by: str


@router.post("/generate")
def generate_rts(payload: GenerateRequest, db: Session = Depends(get_db)):
    """Dispatch pushes the RTS/rescue decision to a driver — see module docstring."""
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == payload.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()
    if not roster_entry:
        raise HTTPException(status_code=404, detail="Driver not found on active roster.")

    result = start_rts(roster_entry.payroll_name, roster_entry.slack_member_id, db)

    dm_sent = False
    if roster_entry.slack_member_id and roster_entry.slack_verified:
        first = _first_name(roster_entry.payroll_name)
        if result["routed_to_rescue"]:
            message = (
                f"🚨 *Rescue Assignment* — Hi {first}, dispatch needs you for a rescue "
                f"({result.get('rescued_driver_name') or 'another driver'}'s route) before you head back.\n\n"
                f"👉 {result['contribute_url']}"
            )
        else:
            message = (
                f"🔄 *Return to Station* — Hi {first}, dispatch has you wrapping up for the day.\n\n"
                f"👉 {result['debrief_url']}"
            )
        dm_sent = _dm_driver(roster_entry.slack_member_id, message)

    return {**result, "driver_name": roster_entry.payroll_name, "dm_sent": dm_sent}


def _driver_transporter_id(driver_name: str, shift_date: date, db: Session) -> Optional[str]:
    assignment = _driver_route_today(driver_name, shift_date, db)
    return assignment.transporter_id if assignment else None


@router.get("/debrief")
def get_debrief(token: str, db: Session = Depends(get_db)):
    debrief = db.query(RtsDebrief).filter(RtsDebrief.token == token).first()
    if not debrief:
        raise HTTPException(status_code=404, detail="This RTS link is invalid. Use the button in Slack.")
    if debrief.completed_at:
        raise HTTPException(status_code=400, detail="This debrief has already been submitted.")

    transporter_id = _driver_transporter_id(debrief.driver_name, debrief.shift_date, db)
    packages = []
    if transporter_id:
        for p in get_driver_packages(transporter_id, db):
            packages.append({
                "tracking_id": p.tracking_id,
                "package_status": p.package_status,
                "amazon_reason_code": p.reason_code,
                "suggested_code": _AMAZON_CODE_TO_OURS.get((p.reason_code or "").upper()),
                "needs_answer": p.reason_code is None,
            })

    return {
        "driver_name": debrief.driver_name,
        "route_id": debrief.route_id,
        "shift_date": str(debrief.shift_date),
        "packages": packages,
        "reason_codes": RTS_REASON_CODES,
    }


class PackageAnswer(BaseModel):
    tracking_id: str
    reason_code: str
    other_detail: Optional[str] = None
    within_drive_time: Optional[bool] = None   # only meaningful when reason_code == "reattemptable"
    source: str = "packages_file"              # packages_file | manual
    amazon_reason_code: Optional[str] = None   # what Amazon already had recorded, if anything


class SubmitRequest(BaseModel):
    token: str
    packages: list[PackageAnswer] = []


@router.post("/submit")
def submit_debrief(payload: SubmitRequest, db: Session = Depends(get_db)):
    debrief = db.query(RtsDebrief).filter(RtsDebrief.token == payload.token).first()
    if not debrief:
        raise HTTPException(status_code=404, detail="This RTS link is invalid.")
    if debrief.completed_at:
        raise HTTPException(status_code=400, detail="This debrief has already been submitted.")

    seen_tracking_ids: set[str] = set()
    for p in payload.packages:
        if p.reason_code not in _VALID_REASON_CODES:
            raise HTTPException(status_code=400, detail=f"Unrecognized reason code: {p.reason_code}")
        if p.reason_code == "other" and not (p.other_detail or "").strip():
            raise HTTPException(status_code=400, detail=f"Package {p.tracking_id} needs a description for 'Other'.")
        if p.reason_code == "reattemptable" and p.within_drive_time is None:
            raise HTTPException(status_code=400, detail=f"Package {p.tracking_id}: answer whether it's a quick drive.")
        seen_tracking_ids.add(p.tracking_id)

    # Hard enforcement: every package Amazon already flagged for this driver
    # today must be accounted for before they can submit -- closes any
    # client-side bypass of the pre-populated list.
    transporter_id = _driver_transporter_id(debrief.driver_name, debrief.shift_date, db)
    if transporter_id:
        required_ids = {p.tracking_id for p in get_driver_packages(transporter_id, db)}
        missing = required_ids - seen_tracking_ids
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(missing)} package(s) still need a reason before you can submit: "
                    f"{', '.join(sorted(missing)[:5])}"
                ),
            )

    for p in payload.packages:
        db.add(RtsDebriefPackage(
            debrief_id=debrief.id,
            tracking_id=p.tracking_id,
            reason_code=p.reason_code,
            other_detail=p.other_detail,
            within_drive_time=p.within_drive_time,
            source=p.source,
            amazon_reason_code=p.amazon_reason_code,
        ))

    reattemptable = [p for p in payload.packages if p.reason_code == "reattemptable"]
    assigned = sum(1 for p in reattemptable if p.within_drive_time)
    skipped = sum(1 for p in reattemptable if not p.within_drive_time)

    debrief.reattempt_assigned_count = assigned
    debrief.reattempt_skipped_count = skipped
    debrief.completed_at = datetime.utcnow()

    assignment = _driver_route_today(debrief.driver_name, debrief.shift_date, db)
    eta = _expected_return_time(assignment, debrief.shift_date, db)
    debrief.expected_return_time = eta
    db.commit()

    # Slack confirmation
    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        if token and debrief.slack_user_id:
            from slack_sdk import WebClient
            client = WebClient(token=token)
            first = _first_name(debrief.driver_name)
            if assigned > 0:
                text = (
                    f"🔄 *RTS Debrief Complete* — Thanks {first}!\n\n"
                    f"You've got *{assigned}* re-attempt(s) that are a quick drive — "
                    f"go make those attempts, then head back to the station.\n"
                    + (f"Expected return: *{eta}*" if eta else "")
                )
            else:
                text = (
                    f"✅ *RTS Debrief Complete* — Thanks {first}!\n\n"
                    f"Head back to the station now."
                    + (f" Expected arrival: *{eta}*" if eta else "")
                )
            # This tool can't select the code in Amazon's own app for the
            # driver -- the actual defect only clears once they do it there
            # too, so any package we just forced an answer for (Amazon had
            # no code recorded yet) gets an explicit reminder.
            needed_coaching = [p for p in payload.packages if p.amazon_reason_code is None and p.reason_code not in ("reattemptable", "reverse_pickup")]
            if needed_coaching:
                text += (
                    f"\n\n⚠️ *{len(needed_coaching)} package(s)* didn't have a reason code in your "
                    f"delivery app yet — make sure you select one there too before you clock out."
                )
            client.chat_postMessage(channel=debrief.slack_user_id, text=text)
    except Exception as exc:
        logger.warning("RTS confirmation DM failed: %s", exc)

    return {
        "reattempt_assigned_count": assigned,
        "reattempt_skipped_count": skipped,
        "expected_return_time": eta,
    }
