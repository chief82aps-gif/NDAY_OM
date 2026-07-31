"""
NDAY Points — a positive, reward-only incentive currency, added
2026-07-31. Deliberately named and coded apart from attendance.py's own
"points" concept (HRM-023.1 violation points, where more is worse and
the whole system is punitive) -- per explicit user direction to move
away from that style of program, this must never be confused with it in
code, Slack copy, or driver-facing text.

Owns nday_points_ledger / nday_points_transactions / swag_catalog_items
/ swag_redemption_requests exclusively -- other modules should call the
helpers here, never query them directly, per the hub-and-spoke rule in
CLAUDE.md.

v1 earning source: perfect safety day (zero SafetyEvent rows for a
driver who actually drove that day), auto-awarded off the existing daily
Safety Dashboard ingest (safety_events.py). More earning sources
(rescue conversion, tier achievement, perfect attendance -- see
project_incentive_points_module memory) are future work, not built yet.

Redemption is a catalog (swag/gift cards), always available once items
exist. A cash-out request type exists in the data model (is_cash_out)
but is gated OFF by NDAY_POINTS_CASH_OUT_ACTIVE pending legal review --
converting a reward-points balance to cash has real wage/tax
implications this repo has no basis to decide unilaterally. Do not flip
this flag without that review.

Fulfillment is manual -- HR sees the request, hands out/ships the item,
marks it fulfilled. Same "identify, don't execute" idiom as the Rescue
Bonus Ledger, Okami, etc.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, DriverRosterEntry, DailyRouteAssignment, SafetyEvent,
    NdayPointsLedger, NdayPointsTransaction, SwagCatalogItem, SwagRedemptionRequest,
)
from api.src.feature_flags import get_flag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nday-points", tags=["nday-points"])

# Off by default like every other new automated award this session --
# confirm perfect-day detection is correct before it silently changes
# driver balances.

# Placeholder value -- no real point economy has been defined yet
# (how much a perfect day is "worth" relative to catalog item costs).
# Adjust via NDAY_POINTS_PER_PERFECT_DAY once a real number is chosen.
POINTS_PER_PERFECT_DAY = int(os.getenv("NDAY_POINTS_PER_PERFECT_DAY", "10"))

# Hard off pending legal review -- see module docstring. Do not enable
# without confirming wage/tax treatment of a points-to-cash conversion.


# ─────────────────────────────────────────────────────────────────────────────
# Ledger core
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_ledger(roster_id: int, db: Session) -> NdayPointsLedger:
    ledger = db.query(NdayPointsLedger).filter(NdayPointsLedger.roster_id == roster_id).first()
    if not ledger:
        ledger = NdayPointsLedger(roster_id=roster_id, balance=0)
        db.add(ledger)
        db.flush()
    return ledger


def award_points(
    roster_id: int,
    points: int,
    reason: str,
    description: str,
    db: Session,
    related_date: Optional[date] = None,
    created_by: str = "system",
) -> Optional[NdayPointsTransaction]:
    """Credits (or debits, if points is negative) a driver's balance and
    logs a plain-language transaction. Idempotent for auto-awarded
    reasons that carry a related_date -- calling this twice for the same
    (roster_id, reason, related_date) is a no-op the second time, so a
    re-run of the awarding job can never double-award the same day."""
    if related_date is not None:
        existing = (
            db.query(NdayPointsTransaction)
            .filter(
                NdayPointsTransaction.roster_id == roster_id,
                NdayPointsTransaction.reason == reason,
                NdayPointsTransaction.related_date == related_date,
            )
            .first()
        )
        if existing:
            return None

    ledger = _get_or_create_ledger(roster_id, db)
    ledger.balance += points
    ledger.updated_at = datetime.utcnow()

    txn = NdayPointsTransaction(
        roster_id=roster_id,
        points=points,
        reason=reason,
        description=description,
        related_date=related_date,
        created_by=created_by,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# ─────────────────────────────────────────────────────────────────────────────
# Perfect-day awarding — called from safety_events.py after a day's
# Safety Dashboard file is ingested.
# ─────────────────────────────────────────────────────────────────────────────

def award_perfect_day_points(report_date: date, db: Session) -> dict:
    """Any driver with a confirmed route assignment for report_date and
    zero SafetyEvent rows that date earns POINTS_PER_PERFECT_DAY. No-ops
    entirely if NDAY_POINTS_ACTIVE is off."""
    if not get_flag("NDAY_POINTS_ACTIVE"):
        return {"status": "inactive", "note": "Set NDAY_POINTS_ACTIVE=true on Render to enable"}

    worked = {
        a.driver_name for a in
        db.query(DailyRouteAssignment.driver_name)
        .filter(DailyRouteAssignment.assignment_date == report_date, DailyRouteAssignment.driver_name.isnot(None))
        .all()
    }
    if not worked:
        return {"status": "no_assignments", "report_date": report_date.isoformat()}

    had_event = {
        r.driver_name for r in
        db.query(SafetyEvent.driver_name)
        .filter(SafetyEvent.report_date == report_date, SafetyEvent.driver_name.isnot(None))
        .all()
    }

    perfect_drivers = worked - had_event
    awarded = 0
    for driver_name in perfect_drivers:
        entry = (
            db.query(DriverRosterEntry)
            .filter(DriverRosterEntry.payroll_name == driver_name, DriverRosterEntry.is_active == True)  # noqa: E712
            .first()
        )
        if not entry:
            continue
        txn = award_points(
            entry.id, POINTS_PER_PERFECT_DAY, "perfect_day",
            f"+{POINTS_PER_PERFECT_DAY} pts — Perfect Safety Day ({report_date.isoformat()})",
            db, related_date=report_date,
        )
        if txn:
            awarded += 1

    return {"status": "ok", "report_date": report_date.isoformat(), "eligible": len(perfect_drivers), "awarded": awarded}


@router.post("/award-perfect-day/{report_date}")
def trigger_award_perfect_day(report_date: date, db: Session = Depends(get_db)):
    """Manual trigger for testing/backfill — same function safety_events.py calls after ingest."""
    return award_perfect_day_points(report_date, db)


# ─────────────────────────────────────────────────────────────────────────────
# Balance + transaction history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/balance/{roster_id}")
def get_balance(roster_id: int, db: Session = Depends(get_db)):
    ledger = db.query(NdayPointsLedger).filter(NdayPointsLedger.roster_id == roster_id).first()
    return {"roster_id": roster_id, "balance": ledger.balance if ledger else 0}


@router.get("/transactions/{roster_id}")
def get_transactions(roster_id: int, limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(NdayPointsTransaction)
        .filter(NdayPointsTransaction.roster_id == roster_id)
        .order_by(NdayPointsTransaction.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "roster_id": roster_id,
        "transactions": [
            {
                "id": t.id, "points": t.points, "reason": t.reason,
                "description": t.description,
                "related_date": t.related_date.isoformat() if t.related_date else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Catalog — HR/dispatch-curated, no real inventory tracking beyond "active"
# ─────────────────────────────────────────────────────────────────────────────

class CatalogItemRequest(BaseModel):
    name: str
    description: Optional[str] = None
    point_cost: int
    created_by: Optional[str] = None


@router.get("/catalog")
def list_catalog(active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(SwagCatalogItem)
    if active_only:
        q = q.filter(SwagCatalogItem.active == True)  # noqa: E712
    rows = q.order_by(SwagCatalogItem.point_cost).all()
    return {
        "items": [
            {"id": i.id, "name": i.name, "description": i.description, "point_cost": i.point_cost, "active": i.active}
            for i in rows
        ]
    }


@router.post("/catalog")
def create_catalog_item(payload: CatalogItemRequest, db: Session = Depends(get_db)):
    if payload.point_cost <= 0:
        raise HTTPException(400, "point_cost must be positive")
    item = SwagCatalogItem(
        name=payload.name, description=payload.description,
        point_cost=payload.point_cost, created_by=payload.created_by,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "name": item.name, "point_cost": item.point_cost}


@router.post("/catalog/{item_id}/deactivate")
def deactivate_catalog_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(SwagCatalogItem).filter(SwagCatalogItem.id == item_id).first()
    if not item:
        raise HTTPException(404, f"Catalog item {item_id} not found")
    item.active = False
    db.commit()
    return {"status": "deactivated", "id": item_id}


# ─────────────────────────────────────────────────────────────────────────────
# Redemption
# ─────────────────────────────────────────────────────────────────────────────

def do_redeem_catalog_item(roster_id: int, catalog_item_id: int, db: Session) -> SwagRedemptionRequest:
    """Core redemption logic — raises ValueError on invalid input/
    insufficient balance, same pattern as rescue.py's do_redeem_bonus()."""
    item = db.query(SwagCatalogItem).filter(SwagCatalogItem.id == catalog_item_id, SwagCatalogItem.active == True).first()  # noqa: E712
    if not item:
        raise ValueError("Catalog item not found or no longer offered")

    ledger = _get_or_create_ledger(roster_id, db)
    if ledger.balance < item.point_cost:
        raise ValueError("Insufficient points balance")

    ledger.balance -= item.point_cost
    ledger.updated_at = datetime.utcnow()
    db.add(NdayPointsTransaction(
        roster_id=roster_id, points=-item.point_cost, reason="catalog_redemption",
        description=f"-{item.point_cost} pts — Redeemed: {item.name}",
    ))
    request = SwagRedemptionRequest(
        roster_id=roster_id, catalog_item_id=item.id, item_name_snapshot=item.name,
        point_cost=item.point_cost,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def do_redeem_cash_out(roster_id: int, points: int, db: Session) -> SwagRedemptionRequest:
    """Gated by NDAY_POINTS_CASH_OUT_ACTIVE -- see module docstring. No
    point-to-dollar conversion rate is defined yet; this records the
    point amount only, not a dollar figure, until that's decided."""
    if not get_flag("NDAY_POINTS_CASH_OUT_ACTIVE"):
        raise ValueError("Cash-out is not currently available.")
    if points <= 0:
        raise ValueError("points must be positive")

    ledger = _get_or_create_ledger(roster_id, db)
    if ledger.balance < points:
        raise ValueError("Insufficient points balance")

    ledger.balance -= points
    ledger.updated_at = datetime.utcnow()
    db.add(NdayPointsTransaction(
        roster_id=roster_id, points=-points, reason="cash_out",
        description=f"-{points} pts — Cash-out requested",
    ))
    request = SwagRedemptionRequest(
        roster_id=roster_id, is_cash_out=True, item_name_snapshot="Cash-out", point_cost=points,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


class RedeemRequest(BaseModel):
    roster_id: int
    catalog_item_id: Optional[int] = None
    cash_out_points: Optional[int] = None


@router.post("/redeem")
def redeem(payload: RedeemRequest, db: Session = Depends(get_db)):
    try:
        if payload.cash_out_points is not None:
            request = do_redeem_cash_out(payload.roster_id, payload.cash_out_points, db)
        elif payload.catalog_item_id is not None:
            request = do_redeem_catalog_item(payload.roster_id, payload.catalog_item_id, db)
        else:
            raise ValueError("Must provide either catalog_item_id or cash_out_points")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ledger = db.query(NdayPointsLedger).filter(NdayPointsLedger.roster_id == payload.roster_id).first()
    return {"request_id": request.id, "item": request.item_name_snapshot, "remaining_balance": ledger.balance if ledger else 0}


@router.get("/pending-redemptions")
def pending_redemptions(db: Session = Depends(get_db)):
    rows = (
        db.query(SwagRedemptionRequest, DriverRosterEntry)
        .join(DriverRosterEntry, SwagRedemptionRequest.roster_id == DriverRosterEntry.id)
        .filter(SwagRedemptionRequest.status == "pending")
        .order_by(SwagRedemptionRequest.requested_at.asc())
        .all()
    )
    return {
        "requests": [
            {
                "id": r.id, "driver": entry.payroll_name, "item": r.item_name_snapshot,
                "is_cash_out": r.is_cash_out, "point_cost": r.point_cost,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            }
            for r, entry in rows
        ]
    }


class FulfillRequest(BaseModel):
    fulfilled_by: str


@router.post("/redemptions/{request_id}/fulfill")
def fulfill_redemption(request_id: int, payload: FulfillRequest, db: Session = Depends(get_db)):
    request = db.query(SwagRedemptionRequest).filter(SwagRedemptionRequest.id == request_id).first()
    if not request:
        raise HTTPException(404, f"Redemption request {request_id} not found")
    request.status = "fulfilled"
    request.fulfilled_at = datetime.utcnow()
    request.fulfilled_by = payload.fulfilled_by
    db.commit()
    return {"status": "fulfilled", "id": request_id}
