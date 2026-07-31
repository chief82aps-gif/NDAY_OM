"""
Daily Quality Overview — Amazon's per-day performance export, released
~30-48 hours after delivery completion (per explicit user note) --
added 2026-07-31. Narrower than the weekly DSP Scorecard: packages/
routes volume, RTS-controllable count, POD%, and DSB count only -- no
safety sub-metrics, no overall score/tier. Does NOT feed
driver_scoring.py's blended score; this is a separate, supplementary
daily signal.

Owns the `daily_quality_snapshots` / `daily_quality_records` tables
exclusively — other modules should call the helpers here, never query
them directly, per the hub-and-spoke rule in CLAUDE.md.

Ingested via ops_ingest.py's normal Slack-scan/auto-ingest pipeline
(detected_type "daily_quality") or direct upload here.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import get_db, DailyQualitySnapshot, DailyQualityRecord
from api.src.ingest.daily_quality import parse_daily_quality_csv

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/daily-quality", tags=["daily-quality"])


def _store_daily_quality(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Parse CSV and upsert into the DB. Called from ops_ingest.py's
    dispatcher (Slack-scan path) or the direct upload endpoint below."""
    summary, drivers = parse_daily_quality_csv(content, filename)

    if not drivers:
        return {"status": "error", "message": "No driver rows parsed from file."}
    if not summary["report_date"]:
        return {"status": "error", "message": "Could not determine report date from file contents or filename."}

    report_date = date.fromisoformat(summary["report_date"])

    if slack_file_id:
        existing = db.query(DailyQualitySnapshot).filter(
            DailyQualitySnapshot.slack_file_id == slack_file_id
        ).first()
        if existing:
            return {"status": "already_ingested", "report_date": summary["report_date"], "driver_count": existing.driver_count}

    # Re-upload for the same date replaces the prior snapshot wholesale.
    existing_snap = db.query(DailyQualitySnapshot).filter(DailyQualitySnapshot.report_date == report_date).first()
    if existing_snap:
        db.query(DailyQualityRecord).filter(DailyQualityRecord.snapshot_id == existing_snap.id).delete(synchronize_session=False)
        db.delete(existing_snap)
        db.flush()

    snap = DailyQualitySnapshot(
        report_date=report_date,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        driver_count=len(drivers),
    )
    db.add(snap)
    db.flush()

    for d in drivers:
        db.add(DailyQualityRecord(snapshot_id=snap.id, **d))

    db.commit()
    return {"status": "ingested", "report_date": summary["report_date"], "driver_count": len(drivers)}


def get_trailing_rts_and_dsb(driver_name: str, db: Session, days: int = 60) -> dict:
    """Trailing-N-day totals from this daily data — for other modules
    (e.g. driver_scoring.py) that want a more current RTS/DSB signal than
    the weekly snapshot without depending on this table's internals."""
    since = date.today() - timedelta(days=days)
    rows = (
        db.query(DailyQualityRecord)
        .join(DailyQualitySnapshot, DailyQualityRecord.snapshot_id == DailyQualitySnapshot.id)
        .filter(
            func.lower(DailyQualityRecord.driver_name) == driver_name.lower(),
            DailyQualitySnapshot.report_date >= since,
        )
        .all()
    )
    return {
        "days_with_data": len(rows),
        "total_packages_delivered": sum(r.packages_delivered or 0 for r in rows),
        "total_routes_completed": sum(r.routes_completed or 0 for r in rows),
        "total_rts_da_controllable": sum(r.packages_rts_da_controllable or 0 for r in rows),
        "total_dsb_count": sum(r.dsb_count or 0 for r in rows),
    }


def _serialize_snapshot(s: DailyQualitySnapshot) -> dict:
    return {
        "id": s.id,
        "report_date": s.report_date.isoformat(),
        "source_file": s.source_file,
        "driver_count": s.driver_count,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
    }


def _serialize_record(r: DailyQualityRecord) -> dict:
    return {
        "driver_name": r.driver_name,
        "transporter_id": r.transporter_id,
        "packages_delivered": r.packages_delivered,
        "routes_completed": r.routes_completed,
        "packages_rts_da_controllable": r.packages_rts_da_controllable,
        "pod_pct": float(r.pod_pct) if r.pod_pct is not None else None,
        "dsb_count": r.dsb_count,
    }


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack."""
    content = await file.read()
    return _store_daily_quality(content, file.filename or "upload.csv", None, db)


@router.get("/snapshots")
def list_snapshots(days: int = 30, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    snaps = (
        db.query(DailyQualitySnapshot)
        .filter(DailyQualitySnapshot.report_date >= since)
        .order_by(DailyQualitySnapshot.report_date.desc())
        .all()
    )
    return {"snapshots": [_serialize_snapshot(s) for s in snaps]}


@router.get("/day/{report_date}")
def get_day(report_date: date, db: Session = Depends(get_db)):
    snap = db.query(DailyQualitySnapshot).filter(DailyQualitySnapshot.report_date == report_date).first()
    if not snap:
        raise HTTPException(404, f"No daily quality snapshot found for {report_date}")
    rows = db.query(DailyQualityRecord).filter(DailyQualityRecord.snapshot_id == snap.id).order_by(DailyQualityRecord.driver_name).all()
    return {"snapshot": _serialize_snapshot(snap), "drivers": [_serialize_record(r) for r in rows]}


@router.get("/driver/{transporter_id}")
def get_driver_history(transporter_id: str, days: int = 60, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = (
        db.query(DailyQualityRecord, DailyQualitySnapshot.report_date)
        .join(DailyQualitySnapshot, DailyQualityRecord.snapshot_id == DailyQualitySnapshot.id)
        .filter(
            DailyQualityRecord.transporter_id == transporter_id,
            DailyQualitySnapshot.report_date >= since,
        )
        .order_by(DailyQualitySnapshot.report_date.desc())
        .all()
    )
    if not rows:
        raise HTTPException(404, f"No daily quality history found for transporter {transporter_id}")
    return {
        "transporter_id": transporter_id,
        "days": [
            {**_serialize_record(r), "report_date": rd.isoformat()}
            for r, rd in rows
        ],
    }
