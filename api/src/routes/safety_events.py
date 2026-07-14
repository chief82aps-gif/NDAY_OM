"""
Safety Events — Netradyne driving-safety data (speeding, roadside
parking, etc.), ingested from the "Safety Dashboard" CSV export dropped
in #nday-operations-management. New module, added 2026-07-14.

Owns the `safety_events` table exclusively — other modules (e.g.
rostering.py's driver summary matrix) should call the query helpers
here, never query SafetyEvent directly, per the hub-and-spoke rule in
CLAUDE.md.

Append-only, deduped by Netradyne's own event_id (the export is a rolling
window, so the same event can reappear across multiple uploads).
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import get_db, SessionLocal, SafetyEvent
from api.src.ingest.safety_events import parse_safety_events

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/safety-events", tags=["safety-events"])


def _store_safety_events(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Called from ops_ingest.py's dispatcher. Append-only, deduped by event_id."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        records, errors = parse_safety_events(tmp_path)
        if errors:
            logger.warning("Safety events: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
        if not records:
            return {"status": "error", "message": "; ".join(errors) if errors else "No safety events parsed from file."}

        existing_ids = {
            r[0] for r in db.query(SafetyEvent.event_id)
            .filter(SafetyEvent.event_id.in_([rec.event_id for rec in records]))
            .all()
        }

        created = 0
        for rec in records:
            if rec.event_id in existing_ids:
                continue
            db.add(SafetyEvent(
                event_id=rec.event_id,
                report_date=rec.report_date or date.today(),
                driver_name=rec.driver_name,
                transporter_id=rec.transporter_id,
                event_at=rec.event_at,
                vin=rec.vin,
                program_impact=rec.program_impact,
                metric_type=rec.metric_type,
                metric_subtype=rec.metric_subtype,
                source=rec.source,
                video_link=rec.video_link,
                review_details=rec.review_details,
                source_file=filename,
            ))
            created += 1
        db.commit()

        return {"status": "ingested", "records": len(records), "created": created, "duplicates_skipped": len(records) - created}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def get_driver_safety_summary(db: Session, start_date: date, end_date: date) -> dict[str, dict]:
    """{driver_name: {"count": n, "metric_types": {...}}} for events in
    [start_date, end_date] inclusive. Shared helper — e.g. rostering.py's
    driver summary matrix should call this rather than querying
    SafetyEvent directly."""
    rows = (
        db.query(SafetyEvent)
        .filter(SafetyEvent.report_date >= start_date, SafetyEvent.report_date <= end_date)
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        if not r.driver_name:
            continue
        entry = out.setdefault(r.driver_name, {"count": 0, "metric_types": {}})
        entry["count"] += 1
        mt = r.metric_type or "Unknown"
        entry["metric_types"][mt] = entry["metric_types"].get(mt, 0) + 1
    return out


@router.get("")
def list_safety_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    driver_name: Optional[str] = None,
    metric_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(SafetyEvent)
    if start_date:
        q = q.filter(SafetyEvent.report_date >= date.fromisoformat(start_date))
    if end_date:
        q = q.filter(SafetyEvent.report_date <= date.fromisoformat(end_date))
    if driver_name:
        q = q.filter(SafetyEvent.driver_name == driver_name)
    if metric_type:
        q = q.filter(SafetyEvent.metric_type == metric_type)
    rows = q.order_by(SafetyEvent.event_at.desc()).limit(500).all()
    return {
        "total": len(rows),
        "events": [
            {
                "event_id": r.event_id,
                "report_date": r.report_date.isoformat() if r.report_date else None,
                "driver_name": r.driver_name,
                "transporter_id": r.transporter_id,
                "event_at": r.event_at.isoformat() if r.event_at else None,
                "vin": r.vin,
                "program_impact": r.program_impact,
                "metric_type": r.metric_type,
                "metric_subtype": r.metric_subtype,
                "source": r.source,
                "video_link": r.video_link,
                "review_details": r.review_details,
            }
            for r in rows
        ],
    }


@router.get("/summary")
def safety_summary(start_date: str, end_date: str, db: Session = Depends(get_db)):
    """Per-driver event counts for a date range."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "start_date/end_date must be YYYY-MM-DD")
    summary = get_driver_safety_summary(db, start, end)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "driver_count": len(summary),
        "drivers": [
            {"driver_name": name, "count": v["count"], "metric_types": v["metric_types"]}
            for name, v in sorted(summary.items(), key=lambda kv: -kv[1]["count"])
        ],
    }
