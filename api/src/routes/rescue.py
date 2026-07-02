"""Rescue Tracker API — 3-stage workflow: Open → Contribute → Close."""
import os
import logging
from datetime import datetime, date, timedelta
from math import floor
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from api.src.database import (
    get_db, RescueEvent, RescueContribution, DriverRosterEntry,
    Cortex, Assignment, Driver, Vehicle, User
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rescue", tags=["rescue"])

BONUS_RATE = 10          # dollars per full unit
BONUS_UNIT = 40          # packages per unit


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

BONUS_ELIGIBLE_TYPES = {"Full Pull", "Full Pull Assist", "Rescue"}
PAD_SWEEP_TYPE = "Pad Sweep"
# Full Pull Assist is always bonus eligible — no confirmation gate required
AUTO_BONUS_TYPES = {"Full Pull Assist"}


class Stage1Request(BaseModel):
    rescued_route_id: str
    rescuing_route_id: Optional[str] = None   # None for Pad Sweep
    event_type: str                            # Pad Sweep | Full Pull | Full Pull Assist | Rescue
    reason_code: str
    reason_notes: Optional[str] = None
    pad_sweep_package_count: Optional[int] = None
    expected_packages: Optional[int] = None   # Full Pull and Full Pull Assist only
    meeting_address: Optional[str] = None     # Address for GPS link in Slack DMs
    opened_by: str                             # dispatcher username from frontend JWT


class Stage2Request(BaseModel):
    event_id: str
    rescuing_driver_name: str
    packages_taken: int
    confirmed_all_taken: Optional[bool] = None  # Not required for Full Pull Assist
    observations: Optional[str] = None


class Stage3Request(BaseModel):
    closed_by: str
    close_notes: Optional[str] = None


class ReinstateRequest(BaseModel):
    reinstated_by: str
    reinstatement_reason: str


class RosterImportRequest(BaseModel):
    """Paste of ADP export rows — tab or comma separated."""
    raw_text: str


class SlackLinkRequest(BaseModel):
    slack_member_id: str   # e.g. U012AB3CD


class PhoneUpdateRequest(BaseModel):
    phone: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _make_contribution_id(event_id: str, suffix: int) -> str:
    return f"{event_id}-C{suffix:02d}"


def _lookup_route(route_id: str, lookup_date: date, db: Session) -> dict:
    """Return driver name, van, packages, and phone for a route assignment.
    Phone is looked up from the driver roster by name match.
    Falls back to the most recent available Cortex date if today has no data."""
    def _cortex_query(target: date):
        return (
            db.query(Cortex)
            .filter(
                func.upper(Cortex.route_code) == route_id.upper(),
                Cortex.assignment_date == target,
            )
            .order_by(Cortex.created_at.desc())
            .first()
        )

    row = _cortex_query(lookup_date)

    # Fall back to the most recent date that has this route if today's data is missing
    if not row:
        latest = (
            db.query(func.max(Cortex.assignment_date))
            .filter(func.upper(Cortex.route_code) == route_id.upper())
            .scalar()
        )
        if latest and latest != lookup_date:
            row = _cortex_query(latest)

    if row:
        driver_name = row.driver_name or ""
        phone = _lookup_phone(driver_name, db)
        return {
            "driver_name": driver_name,
            "van": "",
            "packages": row.packages or 0,
            "phone": phone,
        }

    # Fallback: try assignments table
    assign = (
        db.query(Assignment, Driver, Vehicle)
        .join(Driver, Assignment.driver_id == Driver.id, isouter=True)
        .join(Vehicle, Assignment.vehicle_id == Vehicle.id, isouter=True)
        .filter(
            func.upper(Assignment.assignment_id) == route_id.upper(),
            Assignment.assignment_date == lookup_date,
        )
        .first()
    )
    if assign:
        assignment, driver, vehicle = assign
        driver_name = ""
        if driver and driver.user_id:
            user = db.query(User).filter(User.id == driver.user_id).first()
            driver_name = user.name if user else ""
        phone = _lookup_phone(driver_name, db)
        return {
            "driver_name": driver_name,
            "van": vehicle.vehicle_name if vehicle else "",
            "packages": 0,
            "phone": phone,
        }

    return {"driver_name": "", "van": "", "packages": 0, "phone": ""}


def _lookup_phone(driver_name: str, db: Session) -> str:
    """Look up a driver's phone from the roster by payroll name (case-insensitive)."""
    if not driver_name:
        return ""
    entry = (
        db.query(DriverRosterEntry)
        .filter(func.lower(DriverRosterEntry.payroll_name) == driver_name.lower())
        .first()
    )
    return entry.phone or "" if entry else ""


def _send_slack(message: str) -> bool:
    """Send a Slack message. Silently skips if token not configured."""
    token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_RESCUE_CHANNEL", "#dispatch")
    if not token:
        logger.info("SLACK_BOT_TOKEN not set — skipping Slack notification")
        return False
    try:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        client.chat_postMessage(channel=channel, text=message)
        return True
    except Exception as exc:
        logger.warning(f"Slack notification failed: {exc}")
        return False


def _slack_client():
    """Return a configured Slack WebClient, or None if token not set."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        from slack_sdk import WebClient
        return WebClient(token=token)
    except Exception:
        return None


def _dm_driver(slack_member_id: str, message: str) -> bool:
    """Open a DM channel with a Slack user and send a message. Returns True on success."""
    client = _slack_client()
    if not client:
        logger.info("SLACK_BOT_TOKEN not set — skipping driver DM")
        return False
    try:
        client.chat_postMessage(channel=slack_member_id, text=message)
        return True
    except Exception as exc:
        logger.warning(f"Driver DM failed (uid={slack_member_id}): {exc}")
        return False


def _week_bounds(ref: date):
    """Return (sunday, saturday) for the ISO week containing ref."""
    days_since_sunday = (ref.weekday() + 1) % 7
    sunday = ref - timedelta(days=days_since_sunday)
    saturday = sunday + timedelta(days=6)
    return sunday, saturday


def _bonus_amount(total_packages: int) -> int:
    return floor(total_packages / BONUS_UNIT) * BONUS_RATE


# ---------------------------------------------------------------------------
# Stage 1 — Open Rescue
# ---------------------------------------------------------------------------

@router.post("/events")
def open_rescue(payload: Stage1Request, db: Session = Depends(get_db)):
    today = date.today()
    event_id = _make_event_id()

    # Look up rescued driver from morning assignment
    rescued_info = _lookup_route(payload.rescued_route_id, today, db)

    # Look up rescuing driver (not needed for Pad Sweep)
    rescuing_info = {"driver_name": "", "van": "", "packages": 0}
    if payload.event_type != "Pad Sweep" and payload.rescuing_route_id:
        rescuing_info = _lookup_route(payload.rescuing_route_id, today, db)

    event = RescueEvent(
        event_id=event_id,
        event_date=today,
        event_type=payload.event_type,
        rescued_route_id=payload.rescued_route_id.upper(),
        rescued_driver_name=rescued_info["driver_name"],
        rescued_van=rescued_info["van"],
        rescued_driver_tier=None,
        rescuing_route_id=(payload.rescuing_route_id.upper() if payload.rescuing_route_id else None),
        rescuing_driver_name=rescuing_info["driver_name"],
        rescuing_van=rescuing_info["van"],
        reason_code=payload.reason_code,
        reason_notes=payload.reason_notes,
        pad_sweep_package_count=payload.pad_sweep_package_count if payload.event_type == PAD_SWEEP_TYPE else None,
        expected_packages=payload.expected_packages if payload.event_type in ("Full Pull", "Full Pull Assist") else None,
        meeting_address=payload.meeting_address or None,
        rescued_driver_phone=rescued_info.get("phone", ""),
        rescuing_driver_phone=rescuing_info.get("phone", ""),
        opened_by=payload.opened_by,
        status="Open",
    )
    db.add(event)
    db.flush()

    # Slack notifications for all bonus-eligible types
    if payload.event_type in BONUS_ELIGIBLE_TYPES:
        base_url = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
        stage2_url = f"{base_url}/rescue/contribute?eventId={event_id}&routeId={payload.rescued_route_id}"

        # Build address GPS link for Slack (tap-to-navigate on mobile)
        address = payload.meeting_address or ""
        if address:
            from urllib.parse import quote
            maps_url = f"https://maps.google.com/?q={quote(address)}"
            address_link = f"<{maps_url}|{address}>"
        else:
            address_link = "_No address provided_"

        # Event-type instruction for the rescuing driver
        type_instructions = {
            "Full Pull":        "This is a *Full Pull* — take ALL remaining packages from the route.",
            "Full Pull Assist": "This is a *Full Pull Assist* — dispatch has scoped this job. Take all assigned packages.",
            "Rescue":           "This is a *Rescue* — take the packages as directed by dispatch.",
        }
        instruction = type_instructions.get(payload.event_type, "")

        def _first_name(full_name: str) -> str:
            """Extract first name from 'Last, First' or return full name."""
            if "," in full_name:
                return full_name.split(",", 1)[1].strip()
            return full_name

        rescued_first  = _first_name(rescued_info["driver_name"])  if rescued_info["driver_name"]  else "the driver"
        rescuing_first = _first_name(rescuing_info["driver_name"]) if rescuing_info["driver_name"] else "another driver"

        # Channel post — always sent to #rescue-tracking
        channel_msg = (
            f"🚨 *Rescue Opened* — Route {payload.rescued_route_id} | {payload.event_type}\n"
            f"Rescued: {rescued_info['driver_name']} | Rescuing: {rescuing_info['driver_name']}\n"
            f"Reason: {payload.reason_code}"
            + (f" | Address: {address}" if address else "")
            + f"\nEvent ID: `{event_id}` | <{stage2_url}|Log packages>"
        )
        notified = _send_slack(channel_msg)

        # DM the RESCUING driver
        rescuing_entry = (
            db.query(DriverRosterEntry)
            .filter(
                DriverRosterEntry.payroll_name == rescuing_info["driver_name"],
                DriverRosterEntry.slack_member_id.isnot(None),
                DriverRosterEntry.slack_verified == True,
            )
            .first()
        ) if rescuing_info["driver_name"] else None

        if rescuing_entry:
            rescued_phone = rescued_info.get("phone", "")
            rescuing_dm = (
                f"🚨 *Rescue Assignment — Action Required*\n\n"
                f"Hi {rescuing_first}, dispatch has assigned you to assist "
                f"*{rescued_info['driver_name']}* on route *{payload.rescued_route_id}*.\n\n"
                f"{instruction}\n\n"
                f"📍 *Meet at:* {address_link}\n"
                + (f"📞 *Driver's number:* {rescued_phone}\n" if rescued_phone else "")
                + f"\nAfter pickup, log your packages here:\n👉 <{stage2_url}|Tap to log packages>"
            )
            _dm_driver(rescuing_entry.slack_member_id, rescuing_dm)

        # DM the RESCUED driver
        rescued_entry = (
            db.query(DriverRosterEntry)
            .filter(
                DriverRosterEntry.payroll_name == rescued_info["driver_name"],
                DriverRosterEntry.slack_member_id.isnot(None),
                DriverRosterEntry.slack_verified == True,
            )
            .first()
        ) if rescued_info["driver_name"] else None

        if rescued_entry:
            rescued_dm = (
                f"🎉 *Great news — help is on the way!*\n\n"
                f"Hi {rescued_first}, *{rescuing_info['driver_name']}* has been dispatched to assist you "
                f"on route *{payload.rescued_route_id}*.\n\n"
                f"📍 *They'll meet you at:* {address_link}\n\n"
                f"Please make sure to meet them there and have your remaining packages ready! 📦"
            )
            _dm_driver(rescued_entry.slack_member_id, rescued_dm)

        event.slack_notified = notified

    db.commit()
    db.refresh(event)
    return {"event_id": event_id, "status": "Open", "event": _serialize_event(event)}


# ---------------------------------------------------------------------------
# Stage 2 — Log Contribution (public endpoint — no auth required)
# ---------------------------------------------------------------------------

@router.post("/contributions")
def log_contribution(payload: Stage2Request, db: Session = Depends(get_db)):
    event = db.query(RescueEvent).filter(RescueEvent.event_id == payload.event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status == "Closed":
        raise HTTPException(status_code=400, detail="Event is already closed")
    if event.event_type == PAD_SWEEP_TYPE:
        raise HTTPException(status_code=400, detail="Pad Sweep events do not accept contributions")

    # Determine suffix for contribution ID
    existing_count = db.query(RescueContribution).filter(
        RescueContribution.event_id == payload.event_id
    ).count()
    contribution_id = _make_contribution_id(payload.event_id, existing_count + 1)

    # Bonus eligibility rules:
    #   Full Pull Assist → always eligible (dispatch scoped the job, no gate needed)
    #   Full Pull / Rescue → eligible only if driver confirmed all taken
    if event.event_type in AUTO_BONUS_TYPES:
        bonus_eligible = True
        confirmed = True
    else:
        confirmed = bool(payload.confirmed_all_taken)
        bonus_eligible = (event.event_type in BONUS_ELIGIBLE_TYPES) and confirmed

    contribution = RescueContribution(
        contribution_id=contribution_id,
        event_id=payload.event_id,
        rescuing_driver_name=payload.rescuing_driver_name,
        packages_taken=payload.packages_taken,
        confirmed_all_taken=confirmed,
        bonus_eligible=bonus_eligible,
        observations=payload.observations,
    )
    db.add(contribution)
    db.commit()
    db.refresh(contribution)

    # Slack confirmation
    status_flag = "✅" if bonus_eligible else "⚠️ (not confirmed all taken)"
    msg = (
        f"{status_flag} *Contribution Logged* — Route {event.rescued_route_id}\n"
        f"Driver: {payload.rescuing_driver_name} | Packages: {payload.packages_taken}\n"
        f"Event ID: `{payload.event_id}`"
    )
    _send_slack(msg)

    return {
        "contribution_id": contribution_id,
        "bonus_eligible": bonus_eligible,
        "confirmed_all_taken": confirmed,
    }


# ---------------------------------------------------------------------------
# Stage 3 — Close Rescue
# ---------------------------------------------------------------------------

@router.patch("/events/{event_id}/close")
def close_rescue(event_id: str, payload: Stage3Request, db: Session = Depends(get_db)):
    event = db.query(RescueEvent).filter(RescueEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.status = "Closed"
    event.closed_by = payload.closed_by
    event.close_notes = payload.close_notes
    event.closed_at = datetime.utcnow()

    # Verify all pending contributions
    db.query(RescueContribution).filter(
        RescueContribution.event_id == event_id,
        RescueContribution.verified == "Pending",
    ).update({"verified": "Verified", "verified_at": datetime.utcnow()})

    db.commit()

    msg = (
        f"🔒 *Rescue Closed* — Route {event.rescued_route_id}\n"
        f"Event ID: `{event_id}` | Closed by: {payload.closed_by}"
    )
    _send_slack(msg)

    return {"event_id": event_id, "status": "Closed"}


# ---------------------------------------------------------------------------
# Admin — Reinstate Bonus Eligibility
# ---------------------------------------------------------------------------

@router.patch("/contributions/{contribution_id}/reinstate")
def reinstate_bonus(contribution_id: str, payload: ReinstateRequest, db: Session = Depends(get_db)):
    contrib = db.query(RescueContribution).filter(
        RescueContribution.contribution_id == contribution_id
    ).first()
    if not contrib:
        raise HTTPException(status_code=404, detail="Contribution not found")

    contrib.bonus_eligible = True
    contrib.bonus_reinstated = True
    contrib.reinstated_by = payload.reinstated_by
    contrib.reinstated_at = datetime.utcnow()
    contrib.reinstatement_reason = payload.reinstatement_reason
    db.commit()

    return {"contribution_id": contribution_id, "bonus_eligible": True, "reinstated": True}


# ---------------------------------------------------------------------------
# GET — Today's routes (for dropdowns)
# ---------------------------------------------------------------------------

@router.get("/routes")
def list_routes(
    route_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today"),
    db: Session = Depends(get_db),
):
    """Return Cortex route assignments for a given date (default today).
    If no routes exist for the requested date, falls back to the most recent date available."""
    try:
        target = date.fromisoformat(route_date) if route_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    rows = (
        db.query(Cortex)
        .filter(Cortex.assignment_date == target)
        .order_by(Cortex.wave, Cortex.route_code)
        .all()
    )

    # Fall back to most recent date if nothing found for the requested date
    if not rows and not route_date:
        latest = db.query(func.max(Cortex.assignment_date)).scalar()
        if latest:
            rows = (
                db.query(Cortex)
                .filter(Cortex.assignment_date == latest)
                .order_by(Cortex.wave, Cortex.route_code)
                .all()
            )
    return [
        {
            "route_code": r.route_code,
            "driver_name": r.driver_name or "",
            "packages": r.packages or 0,
            "wave": r.wave or "",
            "zone": r.zone or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET — List events
# ---------------------------------------------------------------------------

@router.get("/events")
def list_events(
    status: Optional[str] = Query(None, description="Open | Closed"),
    event_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    q = db.query(RescueEvent)
    if status:
        q = q.filter(RescueEvent.status == status)
    if event_date:
        try:
            d = date.fromisoformat(event_date)
            q = q.filter(RescueEvent.event_date == d)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    events = q.order_by(RescueEvent.created_at.desc()).all()
    return [_serialize_event(e) for e in events]


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(RescueEvent).filter(RescueEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    result = _serialize_event(event)
    result["contributions"] = [_serialize_contribution(c) for c in event.contributions]
    return result


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@router.get("/payroll")
def payroll_report(
    week_of: Optional[str] = Query(None, description="Any date in the target week (YYYY-MM-DD). Defaults to current week."),
    db: Session = Depends(get_db),
):
    """Weekly bonus report: sum bonus-eligible packages per driver, calculate payout."""
    ref = date.fromisoformat(week_of) if week_of else date.today()
    sunday, saturday = _week_bounds(ref)

    contribs = (
        db.query(RescueContribution)
        .join(RescueEvent, RescueContribution.event_id == RescueEvent.event_id)
        .filter(
            RescueEvent.event_date >= sunday,
            RescueEvent.event_date <= saturday,
            or_(
                RescueContribution.bonus_eligible == True,
                RescueContribution.bonus_reinstated == True,
            ),
        )
        .all()
    )

    # Group by driver
    driver_map: dict = {}
    for c in contribs:
        name = c.rescuing_driver_name
        if name not in driver_map:
            driver_map[name] = {"packages": 0, "all_paid": True, "contribution_ids": []}
        driver_map[name]["packages"] += c.packages_taken or 0
        driver_map[name]["contribution_ids"].append(c.contribution_id)
        if not c.bonus_paid:
            driver_map[name]["all_paid"] = False

    report = []
    for driver_name, data in driver_map.items():
        bonus = _bonus_amount(data["packages"])
        report.append({
            "driver": driver_name,
            "bonus_eligible_packages": data["packages"],
            "bonus_amount": bonus,
            "bonus_paid": data["all_paid"],
            "contribution_ids": data["contribution_ids"],
            "week_start": str(sunday),
            "week_end": str(saturday),
        })

    report.sort(key=lambda x: x["bonus_amount"], reverse=True)
    return {
        "week_start": str(sunday),
        "week_end": str(saturday),
        "drivers": report,
        "total_payout": sum(r["bonus_amount"] for r in report),
        "all_paid": all(r["bonus_paid"] for r in report),
    }


class PayrollConfirmRequest(BaseModel):
    driver: str
    week_of: str          # YYYY-MM-DD any date in the target week
    confirmed_by: str


@router.post("/payroll/confirm")
def confirm_payroll(payload: PayrollConfirmRequest, db: Session = Depends(get_db)):
    """Mark all bonus-eligible contributions for a driver in a given week as paid."""
    ref = date.fromisoformat(payload.week_of)
    sunday, saturday = _week_bounds(ref)

    contribs = (
        db.query(RescueContribution)
        .join(RescueEvent, RescueContribution.event_id == RescueEvent.event_id)
        .filter(
            RescueEvent.event_date >= sunday,
            RescueEvent.event_date <= saturday,
            RescueContribution.rescuing_driver_name == payload.driver,
            or_(
                RescueContribution.bonus_eligible == True,
                RescueContribution.bonus_reinstated == True,
            ),
            RescueContribution.bonus_paid == False,
        )
        .all()
    )

    if not contribs:
        raise HTTPException(status_code=404, detail="No unpaid eligible contributions found for this driver/week")

    for c in contribs:
        c.bonus_paid = True
        c.bonus_paid_by = payload.confirmed_by
        c.bonus_paid_at = datetime.utcnow()

    db.commit()
    return {"marked_paid": len(contribs), "driver": payload.driver, "week_start": str(sunday), "week_end": str(saturday)}


@router.get("/missed-pulls")
def missed_pulls_report(
    week_of: Optional[str] = Query(None, description="YYYY-MM-DD within target week. Defaults to current week."),
    db: Session = Depends(get_db),
):
    """Non-bonus events: Full Pull / Rescue where driver did not confirm all packages taken."""
    ref = date.fromisoformat(week_of) if week_of else date.today()
    sunday, saturday = _week_bounds(ref)

    rows = (
        db.query(RescueContribution, RescueEvent)
        .join(RescueEvent, RescueContribution.event_id == RescueEvent.event_id)
        .filter(
            RescueEvent.event_date >= sunday,
            RescueEvent.event_date <= saturday,
            RescueEvent.event_type.in_(["Full Pull", "Rescue"]),
            RescueContribution.confirmed_all_taken == False,
            RescueContribution.bonus_reinstated == False,
        )
        .all()
    )

    return {
        "week_start": str(sunday),
        "week_end": str(saturday),
        "missed_pulls": [
            {
                "contribution_id": c.contribution_id,
                "event_id": e.event_id,
                "event_date": str(e.event_date),
                "event_type": e.event_type,
                "rescued_route": e.rescued_route_id,
                "rescued_driver": e.rescued_driver_name,
                "rescuing_driver": c.rescuing_driver_name,
                "packages_reported": c.packages_taken,
                "bonus_reinstated": c.bonus_reinstated,
            }
            for c, e in rows
        ],
    }


# ---------------------------------------------------------------------------
# Driver Roster
# ---------------------------------------------------------------------------

@router.get("/drivers")
def get_drivers(db: Session = Depends(get_db)):
    """Return active drivers for dropdown population."""
    drivers = (
        db.query(DriverRosterEntry)
        .filter(
            DriverRosterEntry.is_active == True,
            DriverRosterEntry.position_code == "000004-Driver",
        )
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    return [
        {"name": d.payroll_name, "position_id": d.position_id}
        for d in drivers
    ]


@router.get("/roster")
def get_roster(db: Session = Depends(get_db)):
    """Return all roster entries with Slack link status."""
    entries = (
        db.query(DriverRosterEntry)
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    return [_serialize_roster_entry(e) for e in entries]


@router.patch("/roster/{position_id}/phone")
def update_phone(position_id: str, payload: PhoneUpdateRequest, db: Session = Depends(get_db)):
    """Save or update a driver's phone number."""
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.position_id == position_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Driver not found")
    entry.phone = payload.phone.strip() or None
    db.commit()
    return {"position_id": position_id, "phone": entry.phone}


@router.patch("/roster/{position_id}/slack")
def link_slack(position_id: str, payload: SlackLinkRequest, db: Session = Depends(get_db)):
    """Save a Slack Member ID and validate it against the workspace immediately."""
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.position_id == position_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Driver not found")

    member_id = payload.slack_member_id.strip()
    if not member_id.startswith("U") and not member_id.startswith("W"):
        raise HTTPException(status_code=400, detail="Slack Member IDs start with U or W (e.g. U012AB3CD). Check and try again.")

    # Validate against Slack workspace
    client = _slack_client()
    if not client:
        raise HTTPException(status_code=503, detail="SLACK_BOT_TOKEN is not configured on the server.")

    try:
        from slack_sdk.errors import SlackApiError
        result = client.users_info(user=member_id)
        slack_user = result["user"]
        display_name = (
            slack_user.get("profile", {}).get("real_name")
            or slack_user.get("real_name")
            or slack_user.get("name")
            or member_id
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Slack lookup failed: {exc}. Double-check the Member ID.")

    entry.slack_member_id = member_id
    entry.slack_display_name = display_name
    entry.slack_verified = True
    entry.slack_verified_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)

    return {
        "position_id": position_id,
        "payroll_name": entry.payroll_name,
        "slack_member_id": member_id,
        "slack_display_name": display_name,
        "slack_verified": True,
    }


@router.delete("/roster/{position_id}/slack")
def unlink_slack(position_id: str, db: Session = Depends(get_db)):
    """Remove the Slack link for a driver."""
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.position_id == position_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Driver not found")

    entry.slack_member_id = None
    entry.slack_display_name = None
    entry.slack_verified = False
    entry.slack_verified_at = None
    db.commit()
    return {"position_id": position_id, "slack_member_id": None}


@router.post("/roster/{position_id}/slack/test-dm")
def test_slack_dm(position_id: str, db: Session = Depends(get_db)):
    """Send a test DM to verify the driver receives messages."""
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.position_id == position_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Driver not found")
    if not entry.slack_member_id or not entry.slack_verified:
        raise HTTPException(status_code=400, detail="No verified Slack ID on file for this driver.")

    first_name = (
        entry.payroll_name.split(",")[1].strip()
        if "," in entry.payroll_name
        else entry.payroll_name
    )
    msg = (
        f"👋 Hi {first_name}! This is a test message from *NDAY Route Manager*.\n\n"
        f"Your Slack account is now linked and you'll receive rescue assignments here "
        f"with a direct link to log your packages. No action needed — this is just a confirmation!"
    )
    sent = _dm_driver(entry.slack_member_id, msg)
    if not sent:
        raise HTTPException(status_code=502, detail="DM could not be sent. Check SLACK_BOT_TOKEN and bot permissions.")

    return {"sent": True, "to": entry.slack_member_id, "display_name": entry.slack_display_name}


@router.post("/roster/import")
def import_roster(payload: RosterImportRequest, db: Session = Depends(get_db)):
    """
    Parse pasted ADP export text and upsert into driver_roster.
    Expected columns (tab or comma separated):
      Payroll Name | Position ID | Hire Date | Home Department | Rate Type | Position Code
    """
    lines = [l.strip() for l in payload.raw_text.strip().splitlines() if l.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="No data provided")

    imported = 0
    skipped = 0
    for line in lines:
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        if len(parts) < 2:
            skipped += 1
            continue

        payroll_name = parts[0]
        position_id = parts[1] if len(parts) > 1 else ""
        hire_date_raw = parts[2] if len(parts) > 2 else ""
        home_dept = parts[3] if len(parts) > 3 else ""
        rate_type = parts[4] if len(parts) > 4 else ""
        position_code = parts[5] if len(parts) > 5 else ""

        if not payroll_name or not position_id:
            skipped += 1
            continue

        hire_date = None
        if hire_date_raw:
            try:
                hire_date = date.fromisoformat(hire_date_raw)
            except ValueError:
                pass

        existing = db.query(DriverRosterEntry).filter(
            DriverRosterEntry.position_id == position_id
        ).first()

        if existing:
            existing.payroll_name = payroll_name
            existing.hire_date = hire_date
            existing.home_department = home_dept
            existing.rate_type = rate_type
            existing.position_code = position_code
            existing.is_active = True
            existing.updated_at = datetime.utcnow()
        else:
            db.add(DriverRosterEntry(
                payroll_name=payroll_name,
                position_id=position_id,
                hire_date=hire_date,
                home_department=home_dept,
                rate_type=rate_type,
                position_code=position_code,
            ))
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _serialize_event(e: RescueEvent) -> dict:
    return {
        "event_id": e.event_id,
        "event_date": str(e.event_date),
        "event_type": e.event_type,
        "rescued_route_id": e.rescued_route_id,
        "rescued_driver_name": e.rescued_driver_name,
        "rescued_van": e.rescued_van,
        "rescued_driver_tier": e.rescued_driver_tier,
        "rescuing_route_id": e.rescuing_route_id,
        "rescuing_driver_name": e.rescuing_driver_name,
        "rescuing_van": e.rescuing_van,
        "reason_code": e.reason_code,
        "reason_notes": e.reason_notes,
        "pad_sweep_package_count": e.pad_sweep_package_count,
        "expected_packages": e.expected_packages,
        "meeting_address": e.meeting_address,
        "rescued_driver_phone": e.rescued_driver_phone,
        "rescuing_driver_phone": e.rescuing_driver_phone,
        "opened_by": e.opened_by,
        "closed_by": e.closed_by,
        "close_notes": e.close_notes,
        "closed_at": e.closed_at.isoformat() if e.closed_at else None,
        "status": e.status,
        "slack_notified": e.slack_notified,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_roster_entry(e: DriverRosterEntry) -> dict:
    return {
        "position_id": e.position_id,
        "payroll_name": e.payroll_name,
        "position_code": e.position_code,
        "hire_date": str(e.hire_date) if e.hire_date else None,
        "is_active": e.is_active,
        "phone": e.phone,
        "slack_member_id": e.slack_member_id,
        "slack_display_name": e.slack_display_name,
        "slack_verified": e.slack_verified or False,
        "slack_verified_at": e.slack_verified_at.isoformat() if e.slack_verified_at else None,
    }


def _serialize_contribution(c: RescueContribution) -> dict:
    return {
        "contribution_id": c.contribution_id,
        "event_id": c.event_id,
        "rescuing_driver_name": c.rescuing_driver_name,
        "packages_taken": c.packages_taken,
        "confirmed_all_taken": c.confirmed_all_taken,
        "bonus_eligible": c.bonus_eligible,
        "observations": c.observations,
        "bonus_reinstated": c.bonus_reinstated,
        "reinstated_by": c.reinstated_by,
        "reinstated_at": c.reinstated_at.isoformat() if c.reinstated_at else None,
        "reinstatement_reason": c.reinstatement_reason,
        "verified": c.verified,
        "verified_at": c.verified_at.isoformat() if c.verified_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
