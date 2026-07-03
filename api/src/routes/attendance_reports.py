"""
Attendance Reports — admin-only deep-reporting endpoints.

Separate module so existing attendance.py is never modified.

Endpoints:
  GET    /attendance/all-points       Point summary for all active drivers
  GET    /attendance/pending-review   RC auto-logged events needing a reason code
  PATCH  /attendance/events/{id}      Edit an attendance event
  DELETE /attendance/events/{id}      Void (permanently delete) an attendance event
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, AttendanceEvent, DriverRosterEntry, QualityMetricSnapshot, QualityMetricDriver
from api.src.routes.attendance import (
    _event_to_dict,
    _driver_points_summary,
    VALID_EVENT_TYPES,
    VALID_REASON_CODES,
    _calc_compliance,
    MISSED_TYPES,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/attendance", tags=["attendance-reports"])


class UpdateEventRequest(BaseModel):
    event_type: Optional[str] = None
    reason_code: Optional[str] = None
    notes: Optional[str] = None
    logged_by: Optional[str] = None
    scheduled_wave: Optional[str] = None
    call_time: Optional[str] = None


@router.get("/all-points")
def all_driver_points(db: Session = Depends(get_db)):
    """Admin — attendance point summary for all active drivers, sorted highest to lowest."""
    drivers = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.is_active == True)
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    results = []
    for d in drivers:
        summary = _driver_points_summary(d.payroll_name, db)
        results.append({"driver_name": d.payroll_name, **summary})
    results.sort(key=lambda x: x["current_points"], reverse=True)
    return {"total": len(results), "drivers": results}


@router.get("/pending-review")
def pending_review(db: Session = Depends(get_db)):
    """Admin — events auto-logged by RingCentral that still need a reason code."""
    events = (
        db.query(AttendanceEvent)
        .filter(
            AttendanceEvent.logged_by.like("RingCentral%"),
            AttendanceEvent.reason_code == None,
        )
        .order_by(AttendanceEvent.event_date.desc(), AttendanceEvent.created_at.desc())
        .all()
    )
    return {"total": len(events), "events": [_event_to_dict(e) for e in events]}


@router.patch("/events/{event_id}")
def update_event(event_id: int, req: UpdateEventRequest, db: Session = Depends(get_db)):
    """Admin — edit an existing attendance event."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")

    if req.event_type is not None:
        if req.event_type not in VALID_EVENT_TYPES:
            raise HTTPException(400, "Invalid event_type.")
        event.event_type = req.event_type
        event.is_missed = req.event_type in MISSED_TYPES

    if req.reason_code is not None:
        if req.reason_code and req.reason_code not in VALID_REASON_CODES:
            raise HTTPException(400, "Invalid reason_code.")
        event.reason_code = req.reason_code or None

    if req.notes is not None:
        event.notes = req.notes or None

    if req.logged_by is not None:
        event.logged_by = req.logged_by or None

    if req.scheduled_wave is not None:
        event.scheduled_wave = req.scheduled_wave or None

    if req.call_time is not None:
        try:
            ct = datetime.fromisoformat(req.call_time.replace("Z", "+00:00")).replace(tzinfo=None)
            event.call_time = ct
        except ValueError:
            raise HTTPException(400, "Invalid call_time format. Use ISO 8601.")

    if event.call_time and event.scheduled_wave:
        hours_before, compliant = _calc_compliance(event.call_time, event.scheduled_wave, event.event_date)
        event.hours_before_shift = Decimal(str(hours_before)) if hours_before is not None else None
        event.compliant = compliant

    db.commit()
    db.refresh(event)
    return _event_to_dict(event)


@router.get("/composite-ranking")
def composite_ranking(db: Session = Depends(get_db)):
    """
    Admin — composite driver ranking.
    Attendance 20% | Safety 40% | Quality 40%

    Attendance score: max(0, 100 - points * 10)  — 0 pts = 100, 10 pts = 0
    Safety score:     average of Amazon safety sub-scores (0–100 each)
    Quality score:    Amazon overall_score (0–100)
    """
    # Latest quality metric snapshot
    latest = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.imported_at.desc())
        .first()
    )
    quality_map: dict[str, QualityMetricDriver] = {}
    if latest:
        rows = db.query(QualityMetricDriver).filter(
            QualityMetricDriver.snapshot_id == latest.id
        ).all()
        for row in rows:
            quality_map[row.driver_name.lower()] = row

    roster = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.is_active == True)
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )

    results = []
    for entry in roster:
        att = _driver_points_summary(entry.payroll_name, db)
        att_pts = float(att["current_points"])
        att_score = round(max(0.0, 100.0 - att_pts * 10.0), 1)

        qrow = quality_map.get(entry.payroll_name.lower())

        safety_score: Optional[float] = None
        quality_score: Optional[float] = None

        if qrow:
            safety_vals = [
                float(v) for v in [
                    qrow.speeding_score, qrow.seatbelt_score, qrow.distraction_score,
                    qrow.sign_violation_score, qrow.following_distance_score,
                ] if v is not None
            ]
            if safety_vals:
                safety_score = round(sum(safety_vals) / len(safety_vals), 1)

            if qrow.overall_score is not None:
                quality_score = round(float(qrow.overall_score), 1)

        composite: Optional[float] = None
        if safety_score is not None and quality_score is not None:
            composite = round(att_score * 0.20 + safety_score * 0.40 + quality_score * 0.40, 1)

        results.append({
            "driver_name": entry.payroll_name,
            "attendance_score": att_score,
            "attendance_points": att_pts,
            "attendance_status": att["status"],
            "safety_score": safety_score,
            "quality_score": quality_score,
            "overall_standing": qrow.overall_standing if qrow else None,
            "composite_score": composite,
            "quality_week": latest.week if latest else None,
        })

    results.sort(key=lambda x: (x["composite_score"] is None, -(x["composite_score"] or 0)))

    return {
        "quality_week": latest.week if latest else None,
        "driver_count": len(results),
        "drivers": results,
    }


@router.delete("/events/{event_id}")
def void_event(event_id: int, db: Session = Depends(get_db)):
    """Admin — permanently void (delete) an attendance event."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")
    driver_name = event.driver_name
    db.delete(event)
    db.commit()
    return {"status": "voided", "id": event_id, "driver_name": driver_name}
