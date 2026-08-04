"""
Quality RTS — Amazon's per-package "Return to Station" reason-code
export, added 2026-08-04. A blank/"NO RTS CODE SELECTED" reason code
defaults to a DC DPMO scorecard defect, so this file exists to identify
which drivers need coaching on always selecting a code before returning
a package.

Owns the `quality_rts_snapshots` / `quality_rts_records` tables
exclusively — other modules should call the helpers here, never query
them directly, per the hub-and-spoke rule in CLAUDE.md.

Ingested via ops_ingest.py's normal Slack-scan/auto-ingest pipeline
(detected_type "quality_rts") or direct upload here.
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

from api.src.database import get_db, QualityRtsSnapshot, QualityRtsRecord
from api.src.ingest.quality_rts import parse_quality_rts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quality-rts", tags=["quality-rts"])

NO_CODE_LABEL = "NO RTS CODE SELECTED"


def _store_quality_rts(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Parse CSV/Excel and upsert into the DB. Called from ops_ingest.py's
    dispatcher (Slack-scan path) or the direct upload endpoint below."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    records, errors = parse_quality_rts(tmp_path)
    if errors:
        logger.warning("Quality RTS: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
    if not records:
        return {"status": "error", "message": "; ".join(errors) if errors else "No RTS rows parsed from file."}

    report_date = next((r.planned_delivery_date for r in records if r.planned_delivery_date), None)
    if not report_date:
        return {"status": "error", "message": "Could not determine report date from file contents."}

    if slack_file_id:
        existing = db.query(QualityRtsSnapshot).filter(
            QualityRtsSnapshot.slack_file_id == slack_file_id
        ).first()
        if existing:
            return {"status": "already_ingested", "report_date": report_date.isoformat(), "package_count": existing.package_count}

    # Re-upload for the same date replaces the prior snapshot wholesale.
    existing_snap = db.query(QualityRtsSnapshot).filter(QualityRtsSnapshot.report_date == report_date).first()
    if existing_snap:
        db.query(QualityRtsRecord).filter(QualityRtsRecord.snapshot_id == existing_snap.id).delete(synchronize_session=False)
        db.delete(existing_snap)
        db.flush()

    snap = QualityRtsSnapshot(
        report_date=report_date,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        package_count=len(records),
    )
    db.add(snap)
    db.flush()

    for r in records:
        db.add(QualityRtsRecord(
            snapshot_id=snap.id,
            driver_name=r.driver_name,
            transporter_id=r.transporter_id,
            tracking_id=r.tracking_id,
            impacts_scorecard=r.impacts_scorecard,
            rts_code=r.rts_code,
            additional_information=r.additional_information,
            exemption_reason=r.exemption_reason,
            service_area=r.service_area,
        ))

    db.commit()
    return {"status": "ingested", "report_date": report_date.isoformat(), "package_count": len(records)}


def _serialize_snapshot(s: QualityRtsSnapshot) -> dict:
    return {
        "id": s.id,
        "report_date": s.report_date.isoformat(),
        "source_file": s.source_file,
        "package_count": s.package_count,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
    }


def _serialize_record(r: QualityRtsRecord) -> dict:
    return {
        "driver_name": r.driver_name,
        "transporter_id": r.transporter_id,
        "tracking_id": r.tracking_id,
        "impacts_scorecard": r.impacts_scorecard,
        "rts_code": r.rts_code,
        "no_code_selected": (r.rts_code or "").strip().upper() == NO_CODE_LABEL,
        "additional_information": r.additional_information,
        "exemption_reason": r.exemption_reason,
        "service_area": r.service_area,
    }


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack."""
    content = await file.read()
    return _store_quality_rts(content, file.filename or "upload.csv", None, db)


@router.get("/snapshots")
def list_snapshots(days: int = 30, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    snaps = (
        db.query(QualityRtsSnapshot)
        .filter(QualityRtsSnapshot.report_date >= since)
        .order_by(QualityRtsSnapshot.report_date.desc())
        .all()
    )
    return {"snapshots": [_serialize_snapshot(s) for s in snaps]}


@router.get("/day/{report_date}")
def get_day(report_date: date, db: Session = Depends(get_db)):
    snap = db.query(QualityRtsSnapshot).filter(QualityRtsSnapshot.report_date == report_date).first()
    if not snap:
        raise HTTPException(404, f"No Quality RTS snapshot found for {report_date}")
    rows = db.query(QualityRtsRecord).filter(QualityRtsRecord.snapshot_id == snap.id).order_by(QualityRtsRecord.driver_name).all()
    return {"snapshot": _serialize_snapshot(snap), "records": [_serialize_record(r) for r in rows]}


@router.get("/no-code-leaderboard")
def no_code_leaderboard(days: int = 7, db: Session = Depends(get_db)):
    """Trailing-N-day count of scorecard-impacting RTS with no reason
    code selected, grouped by driver -- the coaching worklist this
    module exists for."""
    since = date.today() - timedelta(days=days)
    rows = (
        db.query(
            QualityRtsRecord.driver_name,
            QualityRtsRecord.transporter_id,
            func.count(QualityRtsRecord.id).label("no_code_count"),
        )
        .join(QualityRtsSnapshot, QualityRtsRecord.snapshot_id == QualityRtsSnapshot.id)
        .filter(
            QualityRtsSnapshot.report_date >= since,
            QualityRtsRecord.impacts_scorecard == True,
            func.upper(func.trim(QualityRtsRecord.rts_code)) == NO_CODE_LABEL,
        )
        .group_by(QualityRtsRecord.driver_name, QualityRtsRecord.transporter_id)
        .order_by(func.count(QualityRtsRecord.id).desc())
        .all()
    )
    return {
        "since": since.isoformat(),
        "drivers": [
            {"driver_name": r.driver_name, "transporter_id": r.transporter_id, "no_code_count": r.no_code_count}
            for r in rows
        ],
    }
