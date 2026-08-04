"""
Customer Delivery Feedback (negative) — Amazon's per-package negative
customer feedback export (wrong address, DA unprofessional, mishandled
package, etc.), added 2026-08-04.

Owns the `customer_feedback_events` table exclusively — other modules
should call the helpers here, never query it directly, per the
hub-and-spoke rule in CLAUDE.md.

Append-only, deduped by Amazon's own Delivery Group ID (the export is a
rolling window, so the same event can reappear across multiple
uploads) -- same pattern as safety_events.py's SafetyEvent.

Ingested via ops_ingest.py's normal Slack-scan/auto-ingest pipeline
(detected_type "customer_feedback") or direct upload here.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import get_db, CustomerFeedbackEvent
from api.src.ingest.customer_feedback import parse_customer_feedback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customer-feedback", tags=["customer-feedback"])

_EVENT_TYPE_FIELDS = [
    "mishandled_package", "unprofessional", "did_not_follow_instructions",
    "wrong_address", "never_received", "wrong_item",
]


def _store_customer_feedback(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Called from ops_ingest.py's dispatcher. Append-only, deduped by
    delivery_group_id."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    records, errors = parse_customer_feedback(tmp_path)
    if errors:
        logger.warning("Customer feedback: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
    if not records:
        return {"status": "error", "message": "; ".join(errors) if errors else "No feedback events parsed from file."}

    existing_ids = {
        r[0] for r in db.query(CustomerFeedbackEvent.delivery_group_id)
        .filter(CustomerFeedbackEvent.delivery_group_id.in_([rec.delivery_group_id for rec in records]))
        .all()
    }

    created = 0
    for rec in records:
        if rec.delivery_group_id in existing_ids:
            continue
        db.add(CustomerFeedbackEvent(
            delivery_group_id=rec.delivery_group_id,
            driver_name=rec.driver_name,
            transporter_id=rec.transporter_id,
            tracking_id=rec.tracking_id,
            mishandled_package=rec.mishandled_package,
            unprofessional=rec.unprofessional,
            did_not_follow_instructions=rec.did_not_follow_instructions,
            wrong_address=rec.wrong_address,
            never_received=rec.never_received,
            wrong_item=rec.wrong_item,
            feedback_details=rec.feedback_details,
            delivery_date=rec.delivery_date,
            reporting_week=rec.reporting_week,
            source_file=filename,
        ))
        created += 1

    db.commit()
    return {"status": "ingested", "event_count": len(records), "new_count": created, "duplicate_count": len(records) - created}


def _serialize(e: CustomerFeedbackEvent) -> dict:
    return {
        "driver_name": e.driver_name,
        "transporter_id": e.transporter_id,
        "tracking_id": e.tracking_id,
        "event_types": [f for f in _EVENT_TYPE_FIELDS if getattr(e, f)],
        "feedback_details": e.feedback_details,
        "delivery_date": e.delivery_date.isoformat() if e.delivery_date else None,
        "reporting_week": e.reporting_week,
    }


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack."""
    content = await file.read()
    return _store_customer_feedback(content, file.filename or "upload.csv", None, db)


@router.get("/events")
def list_events(days: int = 14, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = (
        db.query(CustomerFeedbackEvent)
        .filter(CustomerFeedbackEvent.delivery_date >= since)
        .order_by(CustomerFeedbackEvent.delivery_date.desc())
        .all()
    )
    return {"since": since.isoformat(), "events": [_serialize(e) for e in rows]}


@router.get("/leaderboard")
def leaderboard(days: int = 14, db: Session = Depends(get_db)):
    """Trailing-N-day negative feedback count by driver + event type."""
    since = date.today() - timedelta(days=days)
    rows = (
        db.query(CustomerFeedbackEvent)
        .filter(CustomerFeedbackEvent.delivery_date >= since)
        .all()
    )
    by_driver: dict[str, dict] = {}
    for e in rows:
        key = e.driver_name or "Unknown"
        entry = by_driver.setdefault(key, {"driver_name": key, "transporter_id": e.transporter_id, "total": 0})
        entry["total"] += 1
        for f in _EVENT_TYPE_FIELDS:
            if getattr(e, f):
                entry[f] = entry.get(f, 0) + 1

    drivers = sorted(by_driver.values(), key=lambda d: d["total"], reverse=True)
    return {"since": since.isoformat(), "drivers": drivers}
