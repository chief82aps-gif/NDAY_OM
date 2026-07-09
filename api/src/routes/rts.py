"""Return to Station (RTS) — driver debrief before heading back.

Flow:
  1. Driver taps "Return to Station" in Slack.
  2. If dispatch has already assigned them a rescue, they're routed straight into
     the existing rescue Stage 2 flow instead of the debrief (no separate prompt).
  3. Otherwise they get a personal link to a short (~3 min) debrief: what's coming
     back, split into Damaged / Reverse / Re-Attemptable, per the RTS Standards.
  4. Any Re-Attemptable packages the driver can reach within a 10-15 min drive get
     assigned as reattempts; everything else (plus Business Closed / Damaged /
     Refused / Rescheduled, which are never reattemptable) heads back with them.
  5. Driver gets their expected return time and a go-ahead to head to the station.

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

from api.src.database import (
    get_db, RtsDebrief, RescueEvent, RescueContribution,
    DailyRouteAssignment, CortexSnapshot,
)

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

def start_rts(driver_name: str, slack_user_id: str, db: Session) -> dict:
    """Driver tapped the RTS button. Returns either a rescue handoff or a debrief link."""
    today = date.today()

    rescue = _find_open_rescue(driver_name, db)
    if rescue:
        contribute_url = (
            f"{FRONTEND_URL}/rescue/contribute"
            f"?eventId={rescue.event_id}&routeId={rescue.rescued_route_id}"
        )
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

@router.get("/debrief")
def get_debrief(token: str, db: Session = Depends(get_db)):
    debrief = db.query(RtsDebrief).filter(RtsDebrief.token == token).first()
    if not debrief:
        raise HTTPException(status_code=404, detail="This RTS link is invalid. Use the button in Slack.")
    if debrief.completed_at:
        raise HTTPException(status_code=400, detail="This debrief has already been submitted.")
    return {
        "driver_name": debrief.driver_name,
        "route_id": debrief.route_id,
        "shift_date": str(debrief.shift_date),
    }


class SubmitRequest(BaseModel):
    token: str
    damaged_count: int = 0
    reverse_count: int = 0
    excluded_count: int = 0            # Business Closed / Refused / Rescheduled
    reattempt_eligible_count: int = 0  # candidates that could still be delivered
    reattempt_within_drive_time: int = 0  # of those, how many are a 10-15 min drive or less


@router.post("/submit")
def submit_debrief(payload: SubmitRequest, db: Session = Depends(get_db)):
    debrief = db.query(RtsDebrief).filter(RtsDebrief.token == payload.token).first()
    if not debrief:
        raise HTTPException(status_code=404, detail="This RTS link is invalid.")
    if debrief.completed_at:
        raise HTTPException(status_code=400, detail="This debrief has already been submitted.")

    assigned = min(payload.reattempt_within_drive_time, payload.reattempt_eligible_count)
    skipped = payload.reattempt_eligible_count - assigned

    debrief.damaged_count = payload.damaged_count
    debrief.reverse_count = payload.reverse_count
    debrief.excluded_count = payload.excluded_count
    debrief.reattempt_eligible_count = payload.reattempt_eligible_count
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
            client.chat_postMessage(channel=debrief.slack_user_id, text=text)
    except Exception as exc:
        logger.warning("RTS confirmation DM failed: %s", exc)

    return {
        "reattempt_assigned_count": assigned,
        "reattempt_skipped_count": skipped,
        "expected_return_time": eta,
    }
