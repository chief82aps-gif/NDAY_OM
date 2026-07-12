"""
Driver Profiles
================
Interim driver-profile module: for now, DriverRosterEntry (fed by ADP
import + schedule-upload auto-creation in ops_ingest.py) is the source of
truth. This module only reads/reviews it — it never creates, deactivates,
or deletes a driver. A future HR module will own create/terminate and
become the real source of truth; this module is designed to hand off to
it without disruption (see the `source` column: "adp_import" |
"schedule_upload" | "hr_module").
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.src.database import get_db, DriverRosterEntry, flag_stale_driver_profiles

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drivers", tags=["drivers"])


def _serialize(r: DriverRosterEntry) -> dict:
    return {
        "id": r.id,
        "payroll_name": r.payroll_name,
        "is_active": r.is_active,
        "source": r.source,
        "last_seen_on_schedule": r.last_seen_on_schedule.isoformat() if r.last_seen_on_schedule else None,
        "flagged_inactive": r.flagged_inactive,
        "flagged_inactive_at": r.flagged_inactive_at.isoformat() if r.flagged_inactive_at else None,
        "slack_member_id": r.slack_member_id,
        "slack_verified": r.slack_verified,
        "phone": r.phone,
        "hire_date": r.hire_date.isoformat() if r.hire_date else None,
        "position_code": r.position_code,
    }


@router.get("")
def list_drivers(
    source: Optional[str] = None,
    flagged_only: bool = False,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    """List driver profiles. Defaults to active drivers only."""
    q = db.query(DriverRosterEntry)
    if not include_inactive:
        q = q.filter(DriverRosterEntry.is_active == True)
    if source:
        q = q.filter(DriverRosterEntry.source == source)
    if flagged_only:
        q = q.filter(DriverRosterEntry.flagged_inactive == True)
    rows = q.order_by(DriverRosterEntry.payroll_name).all()
    return {
        "total": len(rows),
        "flagged_count": sum(1 for r in rows if r.flagged_inactive),
        "drivers": [_serialize(r) for r in rows],
    }


@router.get("/{driver_id}")
def get_driver(driver_id: int, db: Session = Depends(get_db)):
    r = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == driver_id).first()
    if not r:
        raise HTTPException(404, f"Driver {driver_id} not found")
    return _serialize(r)


@router.post("/recompute-stale")
def recompute_stale(days: int = 30, db: Session = Depends(get_db)):
    """Manually re-run the 30-day (or custom) staleness flagging pass —
    normally this runs automatically on every schedule ingest."""
    flagged = flag_stale_driver_profiles(db, days=days)
    return {"status": "ok", "days": days, "newly_flagged": flagged}
