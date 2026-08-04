"""
Packages — Amazon's live, company-wide non-delivered-package export
(Reattemptable / Undeliverable / Missing / Returned to station / Pickup
failed), added 2026-08-04. Pulled by hand multiple times a day
(immediately after the last wave launches, every 60-90 min after that,
and at COB) -- see ops_cadence.py for the reminder/All-In gate built on
top of these uploads.

Owns the `packages_snapshots` / `packages_records` tables exclusively —
other modules (rts.py's debrief pre-population, ops_cadence.py's COB
gate) should call the helpers here, never query them directly, per the
hub-and-spoke rule in CLAUDE.md.

Append-only — unlike daily_quality/quality_rts, a re-upload does NOT
replace the prior snapshot for the date; multiple snapshots per day are
expected and kept for progress-over-the-day visibility. Ingested via
ops_ingest.py's normal Slack-scan/auto-ingest pipeline (detected_type
"packages") or direct upload here.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from api.src.database import get_db, PackagesSnapshot, PackagesRecord
from api.src.ingest.packages import parse_packages

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/packages", tags=["packages"])

# Statuses that mean "didn't get delivered normally" -- everything this
# file tracks. Kept as a named list rather than an exclusion so a new
# Amazon status shows up as a visible gap instead of silently included.
NON_DELIVERED_STATUSES = ("Reattemptable", "Undeliverable", "Missing", "Returned to station", "Pickup failed")


def _store_packages(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Parse CSV/Excel and append a new snapshot. Called from
    ops_ingest.py's dispatcher (Slack-scan path) or the direct upload
    endpoint below. Never replaces a prior snapshot for the date -- see
    module docstring."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    records, errors = parse_packages(tmp_path)
    if errors:
        logger.warning("Packages: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
    if not records:
        return {"status": "error", "message": "; ".join(errors) if errors else "No package rows parsed from file."}

    if slack_file_id:
        existing = db.query(PackagesSnapshot).filter(
            PackagesSnapshot.slack_file_id == slack_file_id
        ).first()
        if existing:
            return {"status": "already_ingested", "package_count": existing.package_count}

    report_date = date.today()
    snap = PackagesSnapshot(
        report_date=report_date,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        package_count=len(records),
    )
    db.add(snap)
    db.flush()

    for r in records:
        db.add(PackagesRecord(
            snapshot_id=snap.id,
            tracking_id=r.tracking_id,
            route_code=r.route_code,
            transporter_name=r.transporter_name,
            transporter_id=r.transporter_id,
            address=r.address,
            package_status=r.package_status,
            reason_code=r.reason_code,
            last_scan_at=r.last_scan_at,
        ))

    db.commit()
    return {"status": "ingested", "report_date": report_date.isoformat(), "package_count": len(records)}


def get_latest_snapshot(db: Session, report_date: Optional[date] = None) -> Optional[PackagesSnapshot]:
    """Most recently imported snapshot for a date (defaults to today) --
    used for the RTS debrief pre-population and the ops_cadence COB gate,
    both of which only care about the freshest picture, not history."""
    report_date = report_date or date.today()
    return (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date == report_date)
        .order_by(PackagesSnapshot.imported_at.desc())
        .first()
    )


def get_driver_packages(transporter_id: str, db: Session, report_date: Optional[date] = None) -> list[PackagesRecord]:
    """This driver's non-delivered packages from the latest snapshot for
    the date -- the list rts.py's debrief pre-populates from."""
    snap = get_latest_snapshot(db, report_date)
    if not snap:
        return []
    return (
        db.query(PackagesRecord)
        .filter(
            PackagesRecord.snapshot_id == snap.id,
            PackagesRecord.transporter_id == transporter_id,
        )
        .all()
    )


def _serialize_snapshot(s: PackagesSnapshot) -> dict:
    return {
        "id": s.id,
        "report_date": s.report_date.isoformat(),
        "source_file": s.source_file,
        "package_count": s.package_count,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
    }


def _serialize_record(r: PackagesRecord) -> dict:
    return {
        "tracking_id": r.tracking_id,
        "route_code": r.route_code,
        "transporter_name": r.transporter_name,
        "transporter_id": r.transporter_id,
        "address": r.address,
        "package_status": r.package_status,
        "reason_code": r.reason_code,
        "no_reason": r.reason_code is None,
        "last_scan_at": r.last_scan_at.isoformat() if r.last_scan_at else None,
    }


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack."""
    content = await file.read()
    return _store_packages(content, file.filename or "upload.csv", None, db)


@router.get("/snapshots")
def list_snapshots(days: int = 3, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    snaps = (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date >= since)
        .order_by(PackagesSnapshot.imported_at.desc())
        .all()
    )
    return {"snapshots": [_serialize_snapshot(s) for s in snaps]}


@router.get("/latest")
def get_latest(db: Session = Depends(get_db)):
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    rows = db.query(PackagesRecord).filter(PackagesRecord.snapshot_id == snap.id).all()
    return {"snapshot": _serialize_snapshot(snap), "records": [_serialize_record(r) for r in rows]}


@router.get("/no-reason")
def no_reason_worklist(db: Session = Depends(get_db)):
    """Every package in the latest snapshot Amazon hasn't recorded a
    reason for yet -- the exact coaching worklist this module exists to
    surface."""
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    rows = (
        db.query(PackagesRecord)
        .filter(PackagesRecord.snapshot_id == snap.id, PackagesRecord.reason_code.is_(None))
        .order_by(PackagesRecord.transporter_name)
        .all()
    )
    return {"snapshot": _serialize_snapshot(snap), "records": [_serialize_record(r) for r in rows]}
