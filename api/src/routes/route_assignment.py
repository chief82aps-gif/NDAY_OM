"""
Route Assignment Board — daily assignment management combining Cortex + DOP + Fleet + Quality Rankings.

Workflow:
  1. Load Cortex routes for the date (Amazon's initial driver assignments)
  2. Overlay DOP data for staging location, wave, and planned packages
  3. Rank drivers by quality score (Platinum > Gold > Silver > Bronze, then by score)
  4. Apply callout rule: called-out drivers drop to priority tier 3 (last resort)
  5. Auto-assign vans using service-type fallback chain and 7-day driver affinity
  6. Finalize → write to daily_route_assignments → trigger DM notifications

Callout rule:
  - Any driver on driver_callouts for the date is de-prioritized below ALL non-callout drivers.
  - Auto-assign still fills their vacated routes from the non-callout pool.
  - If no non-callout driver is available, a callout driver CAN cover the route but the
    assignment is flagged is_callout_coverage=True and shown in amber on the board.

Van assignment rules (from Governance/VAN_INGEST_RULES.md):
  - Skip GROUNDED vehicles
  - Service-type fallback: CDV14→CDV16→XL, CDV16→XL, Electric→Electric only (no fallback)
  - 7-day driver-van affinity takes priority over fallback chain
  - Warn at 85% capacity, block at 100%
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal,
    Cortex, DOP, Vehicle, DriverRosterEntry, DailyRouteAssignment,
    QualityMetricDriver, QualityMetricSnapshot, DriverCallout,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/route-assignment", tags=["route-assignment"])

APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

# ─────────────────────────────────────────────────────────────────────────────
# Standing rank (higher = better)
# ─────────────────────────────────────────────────────────────────────────────

_STANDING_RANK = {"Platinum": 4, "Gold": 3, "Silver": 2, "Bronze": 1}

# ─────────────────────────────────────────────────────────────────────────────
# Van service-type fallback chains
# ─────────────────────────────────────────────────────────────────────────────

def _is_electric_service(service_type: str) -> bool:
    s = (service_type or "").lower()
    return "electric" in s or "rivian" in s or "nursery" in s

def _van_fallback_chain(service_type: str) -> list[str]:
    """Return acceptable Vehicle.service_type values in preference order."""
    s = (service_type or "").lower()
    if "electric" in s or "rivian" in s or "nursery" in s:
        return [service_type]          # electric routes: NO fallback
    if "14ft" in s or "cdv14" in s or "custom delivery van 14" in s:
        return ["CDV14", "CDV16", "Extra Large Van", "XL"]
    if "16ft" in s or "cdv16" in s or "custom delivery van 16" in s:
        return ["CDV16", "Extra Large Van", "XL"]
    if "extra large" in s or "xl van" in s:
        return ["Extra Large Van", "XL", "CDV16"]
    if "4wd" in s or "p31" in s:
        return ["4WD P31"]
    return ["Extra Large Van", "XL", "CDV16", "CDV14"]   # default

def _van_matches(van: Vehicle, service_type: str) -> bool:
    """Return True if van.service_type is in the acceptable chain for this route."""
    chain = _van_fallback_chain(service_type)
    vst = (van.service_type or "").strip()
    return any(c.lower() in vst.lower() or vst.lower() in c.lower() for c in chain)

# ─────────────────────────────────────────────────────────────────────────────
# Name-token matching
# ─────────────────────────────────────────────────────────────────────────────

def _tokens(name: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z\s]", "", (name or "").lower()).split())

def _name_overlap(a: str, b: str) -> int:
    return len(_tokens(a) & _tokens(b))

def _match_roster(name: str, roster: list[DriverRosterEntry]) -> Optional[DriverRosterEntry]:
    best, best_score = None, 0
    for r in roster:
        score = _name_overlap(name, r.payroll_name)
        if score > best_score:
            best_score, best = score, r
    return best if best_score >= 1 else None

# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_cortex_for_date(target: date, db: Session) -> list[Cortex]:
    rows = (
        db.query(Cortex)
        .filter(Cortex.assignment_date == target)
        .order_by(Cortex.route_code)
        .all()
    )
    if not rows:
        # Try the latest available date as fallback (useful for testing)
        latest = db.query(func.max(Cortex.assignment_date)).scalar()
        if latest:
            rows = (
                db.query(Cortex)
                .filter(Cortex.assignment_date == latest)
                .order_by(Cortex.route_code)
                .all()
            )
    return rows

def _load_dop_map(target: date, db: Session) -> dict[str, DOP]:
    """Return {route_code: DOP} for the date (or nearest earlier date)."""
    rows = db.query(DOP).filter(DOP.schedule_date == target).all()
    if not rows:
        latest = (
            db.query(func.max(DOP.schedule_date))
            .filter(DOP.schedule_date <= target)
            .scalar()
        )
        if latest:
            rows = db.query(DOP).filter(DOP.schedule_date == latest).all()
    return {r.route_code: r for r in rows}

def _load_quality_map(db: Session) -> dict[str, dict]:
    """Return {transporter_id: {rank, standing, score}} from the latest quality snapshot."""
    latest_snap = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.week.desc())
        .first()
    )
    if not latest_snap:
        return {}
    drivers = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.snapshot_id == latest_snap.id)
        .all()
    )
    # Sort: standing rank desc, then score desc → assign rank 1..N
    sorted_drivers = sorted(
        drivers,
        key=lambda d: (
            _STANDING_RANK.get(d.overall_standing or "", 0),
            float(d.overall_score or 0),
        ),
        reverse=True,
    )
    return {
        d.transporter_id: {
            "rank": i + 1,
            "standing": d.overall_standing or "Unknown",
            "score": float(d.overall_score or 0),
            "week": latest_snap.week,
        }
        for i, d in enumerate(sorted_drivers)
        if d.transporter_id
    }

def _load_van_affinity(target: date, db: Session) -> dict[str, str]:
    """Return {transporter_id: van_number} from last-7-days DailyRouteAssignment."""
    cutoff = target - timedelta(days=7)
    rows = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date >= cutoff,
            DailyRouteAssignment.assignment_date < target,
            DailyRouteAssignment.van_number != None,
        )
        .order_by(DailyRouteAssignment.assignment_date.desc())
        .all()
    )
    affinity: dict[str, str] = {}
    for r in rows:
        tid = getattr(r, "transporter_id", None) or r.driver_name
        if tid and tid not in affinity and r.van_number:
            affinity[tid] = r.van_number
    return affinity

def _load_fleet(db: Session) -> list[Vehicle]:
    """Return all non-grounded vehicles."""
    return (
        db.query(Vehicle)
        .filter(
            Vehicle.status != "grounded",
            Vehicle.status != "GROUNDED",
        )
        .all()
    )

def _load_callout_set(target: date, db: Session) -> set[str]:
    """Return set of transporter_ids called out for target date."""
    rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()
    return {r.transporter_id for r in rows}

# ─────────────────────────────────────────────────────────────────────────────
# Auto-assign van for a route
# ─────────────────────────────────────────────────────────────────────────────

def _assign_van(
    service_type: str,
    transporter_id: Optional[str],
    affinity: dict[str, str],
    fleet: list[Vehicle],
    used_vans: set[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (van_number, vin, warning_flag).
    warning_flag: None | 'electric_violation' | 'no_van_available'
    """
    is_electric = _is_electric_service(service_type)

    # 1. Try affinity van first
    affinity_van_name = affinity.get(transporter_id) if transporter_id else None
    if affinity_van_name:
        for v in fleet:
            if v.vehicle_name == affinity_van_name and v.vin not in used_vans:
                # Affinity van is available — check electric constraint
                if is_electric and not v.is_electric:
                    pass  # affinity van is gas, skip it for electric route
                elif not is_electric and v.is_electric:
                    pass  # gas route, skip electric van
                else:
                    return v.vehicle_name, v.vin, None

    # 2. Match by service-type fallback chain
    chain = _van_fallback_chain(service_type)
    for target_type in chain:
        for v in fleet:
            if v.vin in used_vans:
                continue
            vst = (v.service_type or "").lower()
            if target_type.lower() not in vst and vst not in target_type.lower():
                continue
            # Electric constraint: electric route → must be electric van
            if is_electric and not v.is_electric:
                return None, None, "electric_violation"
            return v.vehicle_name, v.vin, None

    return None, None, "no_van_available"

# ─────────────────────────────────────────────────────────────────────────────
# Core board builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_board(target: date, db: Session) -> dict:
    cortex_rows = _load_cortex_for_date(target, db)
    dop_map = _load_dop_map(target, db)
    quality_map = _load_quality_map(db)
    affinity = _load_van_affinity(target, db)
    fleet = _load_fleet(db)
    callout_set = _load_callout_set(target, db)
    roster = db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all()
    callout_rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()

    # Check if any saved assignments exist for this date
    saved_assignments: dict[str, DailyRouteAssignment] = {
        a.route_code: a
        for a in db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target)
        .all()
    }

    used_vans: set[str] = set()
    routes = []

    for cr in cortex_rows:
        dop = dop_map.get(cr.route_code)
        tid = getattr(cr, "transporter_id", None) or None
        q = quality_map.get(tid, {}) if tid else {}
        saved = saved_assignments.get(cr.route_code)

        # Determine van assignment (saved > auto)
        if saved and saved.van_number:
            van_name, van_vin, van_warn = saved.van_number, None, None
            if van_name:
                used_vans.add(van_name)
        else:
            van_name, van_vin, van_warn = _assign_van(
                cr.service_type or "", tid, affinity, fleet, used_vans
            )
            if van_vin:
                used_vans.add(van_vin)

        is_callout = tid in callout_set if tid else False
        status = (saved.assignment_status if saved and saved.assignment_status else
                  ("callout" if is_callout else "pending"))

        routes.append({
            "route_code": cr.route_code,
            "service_type": cr.service_type,
            "is_electric": _is_electric_service(cr.service_type or ""),
            "driver_name": cr.driver_name,
            "transporter_id": tid,
            "quality_rank": q.get("rank"),
            "quality_standing": q.get("standing", "Unknown"),
            "quality_score": q.get("score"),
            "quality_week": q.get("week"),
            "packages": cr.packages,
            "stops": saved.stops if saved else None,
            "departure_time": saved.departure_time if saved else None,
            "wave": dop.wave if dop else cr.wave,
            "staging_location": dop.station if dop else None,
            "planned_packages": dop.planned_packages if dop else None,
            "route_duration_min": dop.route_duration if dop else None,
            "van_number": van_name,
            "van_vin": van_vin,
            "van_warning": van_warn,
            "is_callout": is_callout,
            "is_callout_coverage": bool(saved and saved.is_callout_coverage) if saved else False,
            "dm_sent": saved.dm_sent if saved else False,
            "assignment_status": status,
            "source_file": cr.source_file,
        })

    # Driver pool: all drivers in quality map, annotated with callout flag and route
    route_by_tid: dict[str, str] = {
        r["transporter_id"]: r["route_code"]
        for r in routes if r["transporter_id"]
    }
    driver_pool = []
    for tid, q in quality_map.items():
        is_callout = tid in callout_set
        driver_pool.append({
            "transporter_id": tid,
            "quality_rank": q["rank"],
            "quality_standing": q["standing"],
            "quality_score": q["score"],
            "is_callout": is_callout,
            "assigned_route": route_by_tid.get(tid),
            # Sort key: (0=normal,1=no_quality_data,2=callout), then rank
            "_sort": (2 if is_callout else 0, q["rank"]),
        })

    # Include any roster drivers not in quality map
    quality_tids = set(quality_map.keys())
    for r in roster:
        # Try to find transporter_id via any matching cortex row
        matching_tid = next(
            (cr.transporter_id for cr in cortex_rows
             if getattr(cr, "transporter_id", None) and
             _name_overlap(cr.driver_name or "", r.payroll_name) >= 1),
            None
        )
        if matching_tid and matching_tid not in quality_tids:
            is_callout = matching_tid in callout_set
            driver_pool.append({
                "transporter_id": matching_tid,
                "quality_rank": 9999,
                "quality_standing": "Unknown",
                "quality_score": None,
                "is_callout": is_callout,
                "assigned_route": route_by_tid.get(matching_tid),
                "_sort": (2 if is_callout else 1, 9999),
            })

    driver_pool.sort(key=lambda d: d["_sort"])
    for d in driver_pool:
        del d["_sort"]

    needs_coverage = [r for r in routes if r["is_callout"]]

    return {
        "date": target.isoformat(),
        "route_count": len(routes),
        "callout_count": len(callout_set),
        "needs_coverage_count": len(needs_coverage),
        "quality_week": (list(quality_map.values())[0].get("week") if quality_map else None),
        "routes": routes,
        "driver_pool": driver_pool,
        "callouts": [
            {
                "id": c.id,
                "transporter_id": c.transporter_id,
                "driver_name": c.driver_name,
                "callout_type": c.callout_type,
                "notes": c.notes,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in callout_rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-assign algorithm
# ─────────────────────────────────────────────────────────────────────────────

def _auto_assign(target: date, db: Session) -> dict:
    """
    For each Cortex route whose driver called out, find the best replacement
    from the non-callout driver pool (ranked by quality). Falls back to callout
    drivers only if no other option exists.

    Writes DailyRouteAssignment rows and returns a summary.
    """
    cortex_rows = _load_cortex_for_date(target, db)
    dop_map = _load_dop_map(target, db)
    quality_map = _load_quality_map(db)
    affinity = _load_van_affinity(target, db)
    fleet = _load_fleet(db)
    callout_set = _load_callout_set(target, db)

    # Build sorted driver list: non-callout first (by rank), then callout (by rank)
    non_callout = sorted(
        [(tid, q) for tid, q in quality_map.items() if tid not in callout_set],
        key=lambda x: x[1]["rank"],
    )
    callout_drivers = sorted(
        [(tid, q) for tid, q in quality_map.items() if tid in callout_set],
        key=lambda x: x[1]["rank"],
    )
    available_pool = non_callout + callout_drivers   # callouts at bottom

    assigned_tids: set[str] = set()
    used_vans: set[str] = set()

    # First pass: assign already-confirmed non-callout routes
    for cr in cortex_rows:
        tid = getattr(cr, "transporter_id", None)
        if tid and tid not in callout_set:
            assigned_tids.add(tid)
            q = quality_map.get(tid, {})
            dop = dop_map.get(cr.route_code)
            van_name, van_vin, van_warn = _assign_van(
                cr.service_type or "", tid, affinity, fleet, used_vans
            )
            if van_vin:
                used_vans.add(van_vin)
            elif van_name:
                used_vans.add(van_name)
            _upsert_assignment(
                db, target, cr, dop, tid, van_name, q,
                is_callout_coverage=False,
                status="confirmed",
            )

    # Second pass: fill callout vacancies
    callout_routes = [cr for cr in cortex_rows
                      if getattr(cr, "transporter_id", None) in callout_set]
    replacements = []
    for cr in callout_routes:
        replacement_tid = None
        is_coverage = False
        for tid, q in available_pool:
            if tid in assigned_tids:
                continue
            is_coverage = tid in callout_set
            replacement_tid = tid
            assigned_tids.add(tid)
            break

        dop = dop_map.get(cr.route_code)
        q = quality_map.get(replacement_tid, {}) if replacement_tid else {}
        van_name, van_vin, van_warn = _assign_van(
            cr.service_type or "", replacement_tid, affinity, fleet, used_vans
        )
        if van_vin:
            used_vans.add(van_vin)
        elif van_name:
            used_vans.add(van_name)

        _upsert_assignment(
            db, target, cr, dop, replacement_tid, van_name, q,
            is_callout_coverage=is_coverage,
            status="confirmed" if replacement_tid else "unassigned",
        )
        replacements.append({
            "route_code": cr.route_code,
            "original_driver": cr.driver_name,
            "replacement_tid": replacement_tid,
            "is_callout_coverage": is_coverage,
            "van": van_name,
            "van_warning": van_warn,
        })

    db.commit()
    return {
        "status": "auto_assigned",
        "date": target.isoformat(),
        "total_routes": len(cortex_rows),
        "callout_routes": len(callout_routes),
        "replacements": replacements,
    }


def _upsert_assignment(
    db: Session,
    target: date,
    cr: Cortex,
    dop: Optional[DOP],
    transporter_id: Optional[str],
    van_name: Optional[str],
    quality: dict,
    is_callout_coverage: bool,
    status: str,
) -> DailyRouteAssignment:
    existing = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == target,
            DailyRouteAssignment.route_code == cr.route_code,
        )
        .first()
    )
    kwargs = dict(
        assignment_date=target,
        route_code=cr.route_code,
        driver_name=cr.driver_name,
        van_number=van_name,
        stage_location=dop.station if dop else None,
        wave=dop.wave if dop else cr.wave,
        packages=cr.packages or (dop.planned_packages if dop else None),
        route_duration=dop.route_duration if dop else None,
        service_type=cr.service_type,
        transporter_id=transporter_id,
        quality_rank=quality.get("rank"),
        quality_standing=quality.get("standing"),
        is_callout_coverage=is_callout_coverage,
        assignment_status=status,
    )
    if existing:
        for k, v in kwargs.items():
            try:
                setattr(existing, k, v)
            except Exception:
                pass
        return existing
    row = DailyRouteAssignment(**kwargs)
    db.add(row)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/board")
def get_board(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Full assignment board for a date (defaults to today)."""
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    else:
        target = date.today()
    return _build_board(target, db)


@router.post("/auto-assign")
def auto_assign(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Run the auto-assignment algorithm for a date."""
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(400, "Invalid date format.")
    else:
        target = date.today()
    return _auto_assign(target, db)


class ManualAssignRequest(BaseModel):
    route_code: str
    transporter_id: str
    van_number: Optional[str] = None
    date_str: Optional[str] = None
    notes: Optional[str] = None


@router.post("/assign")
def manual_assign(req: ManualAssignRequest, db: Session = Depends(get_db)):
    """Manually assign a driver (by transporter_id) to a route, with optional van override."""
    target = date.fromisoformat(req.date_str) if req.date_str else date.today()
    callout_set = _load_callout_set(target, db)
    quality_map = _load_quality_map(db)
    q = quality_map.get(req.transporter_id, {})
    is_coverage = req.transporter_id in callout_set

    cr = (
        db.query(Cortex)
        .filter(
            Cortex.assignment_date == target,
            Cortex.route_code == req.route_code,
        )
        .first()
    )
    if not cr:
        # Accept assignment even without cortex record
        cr_data = type("Fake", (), {
            "route_code": req.route_code,
            "driver_name": None,
            "service_type": None,
            "packages": None,
            "wave": None,
        })()
    else:
        cr_data = cr

    dop = (
        db.query(DOP)
        .filter(DOP.schedule_date == target, DOP.route_code == req.route_code)
        .first()
    )
    row = _upsert_assignment(
        db, target, cr_data, dop, req.transporter_id, req.van_number, q,
        is_callout_coverage=is_coverage,
        status="confirmed",
    )
    db.commit()
    return {
        "status": "assigned",
        "route_code": req.route_code,
        "transporter_id": req.transporter_id,
        "van_number": req.van_number,
        "is_callout_coverage": is_coverage,
        "quality_standing": q.get("standing"),
    }


class CalloutRequest(BaseModel):
    transporter_id: str
    driver_name: str
    callout_date: Optional[str] = None
    callout_type: str = "sick"
    notes: Optional[str] = None
    recorded_by: Optional[str] = None


@router.post("/callout")
def mark_callout(req: CalloutRequest, db: Session = Depends(get_db)):
    """Mark a driver as called out for a date (default: today)."""
    target = date.fromisoformat(req.callout_date) if req.callout_date else date.today()
    existing = (
        db.query(DriverCallout)
        .filter(
            DriverCallout.callout_date == target,
            DriverCallout.transporter_id == req.transporter_id,
        )
        .first()
    )
    if existing:
        return {"status": "already_recorded", "id": existing.id, "date": target.isoformat()}

    callout = DriverCallout(
        callout_date=target,
        transporter_id=req.transporter_id,
        driver_name=req.driver_name,
        callout_type=req.callout_type,
        notes=req.notes,
        recorded_by=req.recorded_by,
        created_at=datetime.now(timezone.utc),
    )
    db.add(callout)
    db.commit()
    logger.info(
        "Callout recorded: %s (%s) on %s — type: %s",
        req.driver_name, req.transporter_id, target, req.callout_type,
    )
    return {
        "status": "recorded",
        "id": callout.id,
        "driver_name": req.driver_name,
        "transporter_id": req.transporter_id,
        "date": target.isoformat(),
        "callout_type": req.callout_type,
        "rule": "Driver moved to bottom of assignment priority. Route flagged for coverage.",
    }


@router.delete("/callout/{callout_id}")
def remove_callout(callout_id: int, db: Session = Depends(get_db)):
    """Remove a callout record (driver is available again)."""
    row = db.query(DriverCallout).filter(DriverCallout.id == callout_id).first()
    if not row:
        raise HTTPException(404, "Callout record not found.")
    db.delete(row)
    db.commit()
    return {"status": "removed", "id": callout_id}


@router.get("/callouts")
def list_callouts(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """List all callouts for a date."""
    target = date.fromisoformat(date_str) if date_str else date.today()
    rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()
    return {
        "date": target.isoformat(),
        "count": len(rows),
        "callouts": [
            {
                "id": r.id,
                "transporter_id": r.transporter_id,
                "driver_name": r.driver_name,
                "callout_type": r.callout_type,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/driver-ranking")
def get_driver_ranking(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Quality-ranked driver list with callout flags for a date."""
    target = date.fromisoformat(date_str) if date_str else date.today()
    quality_map = _load_quality_map(db)
    callout_set = _load_callout_set(target, db)
    cortex_rows = _load_cortex_for_date(target, db)
    route_by_tid = {
        getattr(cr, "transporter_id", None): cr.route_code
        for cr in cortex_rows if getattr(cr, "transporter_id", None)
    }
    ranking = []
    for tid, q in quality_map.items():
        is_callout = tid in callout_set
        ranking.append({
            "transporter_id": tid,
            "quality_rank": q["rank"],
            "quality_standing": q["standing"],
            "quality_score": q["score"],
            "is_callout": is_callout,
            "assigned_route": route_by_tid.get(tid),
            "priority_tier": 3 if is_callout else 1,
            "priority_label": "Last resort (callout)" if is_callout else "Available",
        })
    ranking.sort(key=lambda d: (3 if d["is_callout"] else 1, d["quality_rank"]))
    return {
        "date": target.isoformat(),
        "total_drivers": len(ranking),
        "callout_count": len(callout_set),
        "ranking": ranking,
    }


@router.post("/finalize")
def finalize_assignments(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Lock all confirmed assignments for the date and return a summary.
    The daily_notify module handles DM delivery separately.
    """
    target = date.fromisoformat(date_str) if date_str else date.today()
    rows = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target)
        .all()
    )
    for r in rows:
        try:
            r.assignment_status = "finalized"
        except Exception:
            pass
    db.commit()
    unassigned = [r for r in rows if not r.driver_name]
    return {
        "status": "finalized",
        "date": target.isoformat(),
        "total_routes": len(rows),
        "unassigned_count": len(unassigned),
        "message": (
            f"{len(rows)} routes finalized."
            + (f" {len(unassigned)} route(s) still unassigned." if unassigned else "")
        ),
    }
