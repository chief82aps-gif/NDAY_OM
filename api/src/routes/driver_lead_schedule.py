"""
Driver-Facing Schedule module — daily lead ingest & adaptive PTT routing.

Phase 1 of Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md: replaces
rostering.py's hardcoded _wave_lead_name() weekday dict with a real,
data-driven DailyLeadAssignment table, plus manual-override CRUD for the
dispatch console. No ingest source, no Android app, no MDM yet — those are
later phases.

Endpoints:
  GET    /driver-lead-schedule/{date}   resolve today's lead (writes a
                                         default_rotation fallback row if
                                         nothing else is set, so the
                                         "no real ingest yet" gap stays
                                         visible in the data)
  POST   /driver-lead-schedule/{date}   set a manual-override lead
  DELETE /driver-lead-schedule/{date}   clear manual override for a date
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, DailyLeadAssignment, DriverRosterEntry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/driver-lead-schedule", tags=["driver-lead-schedule"])
# No RBAC decorator yet — matches rostering.py, the module this extends,
# which also has none. Tracked as debt in CLAUDE.md (5/24 route files
# comply), not precedent to keep copying.

# Feature gate — the driver DM keeps its existing "contact your wave lead on
# Zello" text until this is flipped on. Set LEAD_ROUTING_ACTIVE=true on
# Render to enable the "Talk to My Lead" button in rostering.py's day-of DM.
LEAD_ROUTING_ACTIVE = os.getenv("LEAD_ROUTING_ACTIVE", "false").lower() == "true"


def _resolve_slack_id(driver_name: str, db: Session) -> Optional[str]:
    """Best-effort match against DriverRosterEntry.payroll_name, which is
    stored 'Last, First' — handles callers passing either that format or
    'First Last'. Returns None (not just unverified) if the driver isn't
    Slack-linked yet, since an unverified slack_member_id may be stale."""
    if not driver_name:
        return None
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.payroll_name == driver_name).first()
    if not entry and "," not in driver_name:
        parts = driver_name.strip().split()
        if len(parts) >= 2:
            flipped = f"{parts[-1]}, {' '.join(parts[:-1])}"
            entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.payroll_name == flipped).first()
    if entry and entry.slack_verified and entry.slack_member_id:
        return entry.slack_member_id
    return None


def get_current_lead(shift_date: date, db: Session) -> tuple[str, Optional[str], str]:
    """Resolve today's wave lead: the most recent DailyLeadAssignment row
    for (shift_date, scope_type='global') wins — a manual_override always
    ends up most recent because it's written after any fallback row logged
    earlier the same day. If no row exists at all, fall back to
    rostering.py's hardcoded weekday dict and log that fallback as its own
    row, so the "no real ingest yet" gap is visible in the data instead of
    buried in code. See SRD §6.3.

    Returns (driver_name, slack_user_id_or_None, source).
    """
    row = (
        db.query(DailyLeadAssignment)
        .filter(
            DailyLeadAssignment.schedule_date == shift_date,
            DailyLeadAssignment.scope_type == "global",
        )
        .order_by(DailyLeadAssignment.created_at.desc())
        .first()
    )
    if row:
        return row.driver_name, _resolve_slack_id(row.driver_name, db), row.source

    from api.src.routes.rostering import _wave_lead_name
    name, slack_id = _wave_lead_name(shift_date)
    db.add(DailyLeadAssignment(
        schedule_date=shift_date,
        scope_type="global",
        driver_name=name,
        source="default_rotation",
        created_by="system_fallback",
    ))
    db.commit()
    return name, slack_id, "default_rotation"


class SetLeadRequest(BaseModel):
    driver_name: str
    created_by: Optional[str] = None


@router.get("/{shift_date}")
def get_lead(shift_date: date, db: Session = Depends(get_db)):
    name, slack_id, source = get_current_lead(shift_date, db)
    return {"date": shift_date.isoformat(), "driver_name": name, "slack_user_id": slack_id, "source": source}


@router.post("/{shift_date}")
def set_lead(shift_date: date, body: SetLeadRequest, db: Session = Depends(get_db)):
    """Manual-override CRUD — the actual v1 source of truth (SRD §6.1).
    Building file/Excel ingest for a 2-person rotation would be
    over-engineering; this is what the dispatch console calls."""
    name = body.driver_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="driver_name is required")
    db.add(DailyLeadAssignment(
        schedule_date=shift_date,
        scope_type="global",
        driver_name=name,
        source="manual_override",
        created_by=body.created_by or "dispatch_console",
    ))
    db.commit()
    logger.info("Lead override set: %s -> %s (by %s)", shift_date.isoformat(), name, body.created_by or "dispatch_console")
    return {"status": "ok", "date": shift_date.isoformat(), "driver_name": name}


@router.delete("/{shift_date}")
def clear_lead_override(shift_date: date, db: Session = Depends(get_db)):
    """Deletes manual_override rows for a date so the next resolution falls
    back to schedule_ingest (none exist yet in Phase 1) or default_rotation."""
    deleted = (
        db.query(DailyLeadAssignment)
        .filter(
            DailyLeadAssignment.schedule_date == shift_date,
            DailyLeadAssignment.scope_type == "global",
            DailyLeadAssignment.source == "manual_override",
        )
        .delete()
    )
    db.commit()
    return {"status": "ok", "cleared": deleted}
