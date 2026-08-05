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


# ─────────────────────────────────────────────────────────────────────────────
# Non-delivered-marking offender scrub -- added 2026-08-05, per explicit
# direction: drivers are supposed to seek permission from Blake BEFORE
# marking a package Reattemptable/Undeliverable/Missing/Returned to
# station/Pickup failed, but that permission workflow doesn't exist yet
# (see Governance backlog note -- future: pre-marking permission request,
# a package detail/dispute view, and DS text-conversation capture).
# Until that exists, this scrubs the Packages export itself for anyone
# using these codes heavily -- human review of who might be marking
# packages undeliverable without actually working the problem first,
# not an automatic accusation. "Reattemptable" is excluded from the
# offender count itself (a reattempt is still a plausible delivery, not
# a claim of being unable to deliver) but still shown for context.
# ─────────────────────────────────────────────────────────────────────────────

UNABLE_TO_DELIVER_STATUSES = ("Undeliverable", "Missing", "Returned to station", "Pickup failed")
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt


def get_driver_status_counts(db: Session, snapshot: PackagesSnapshot) -> list[dict]:
    """Per-driver breakdown of package_status counts for one snapshot,
    sorted by unable-to-deliver count descending."""
    rows = db.query(PackagesRecord).filter(PackagesRecord.snapshot_id == snapshot.id).all()
    by_driver: dict[str, dict] = {}
    for r in rows:
        name = r.transporter_name or "Unknown"
        entry = by_driver.setdefault(name, {"transporter_id": r.transporter_id, "statuses": {}, "unable_to_deliver": 0})
        entry["statuses"][r.package_status] = entry["statuses"].get(r.package_status, 0) + 1
        if r.package_status in UNABLE_TO_DELIVER_STATUSES:
            entry["unable_to_deliver"] += 1
    result = [{"driver_name": name, **data} for name, data in by_driver.items()]
    result.sort(key=lambda d: d["unable_to_deliver"], reverse=True)
    return result


def get_trailing_offender_report(db: Session, days: int = 7) -> list[dict]:
    """Sums unable-to-deliver counts across the latest snapshot per
    report_date over the trailing N days -- the "systematic/habitual"
    view, as opposed to get_driver_status_counts()'s single-snapshot
    picture. Uses only the LATEST snapshot per date (not every re-pull)
    so a busy upload day doesn't inflate a driver's count."""
    since = date.today() - timedelta(days=days - 1)
    dates_with_data = (
        db.query(PackagesSnapshot.report_date)
        .filter(PackagesSnapshot.report_date >= since)
        .distinct()
        .all()
    )
    by_driver: dict[str, dict] = {}
    for (report_date,) in dates_with_data:
        snap = get_latest_snapshot(db, report_date)
        if not snap:
            continue
        for entry in get_driver_status_counts(db, snap):
            d = by_driver.setdefault(entry["driver_name"], {"transporter_id": entry["transporter_id"], "unable_to_deliver": 0, "days_flagged": 0})
            if entry["unable_to_deliver"] > 0:
                d["unable_to_deliver"] += entry["unable_to_deliver"]
                d["days_flagged"] += 1
    result = [{"driver_name": name, **data} for name, data in by_driver.items() if data["unable_to_deliver"] > 0]
    result.sort(key=lambda d: d["unable_to_deliver"], reverse=True)
    return result


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def send_offender_alert_to_mgt(db: Session, snapshot: Optional[PackagesSnapshot] = None, trailing_days: int = 7) -> dict:
    """Posts today's snapshot breakdown + the trailing-N-day habitual
    view to #nday-mgt for human review. Framed as "worth a look," never
    as an automatic write-up -- these drivers haven't been through any
    permission/dispute process, this is raw pattern-of-use data."""
    snapshot = snapshot or get_latest_snapshot(db)
    if not snapshot:
        return {"status": "no_snapshot"}

    today_counts = [d for d in get_driver_status_counts(db, snapshot) if d["unable_to_deliver"] > 0]
    trailing = get_trailing_offender_report(db, days=trailing_days)

    if not today_counts and not trailing:
        return {"status": "nothing_to_report"}

    lines = ["📦 *Non-Delivered Marking Review* — for human review, not an automatic write-up.\n"]
    if today_counts:
        lines.append(f"*Today's snapshot ({snapshot.report_date.isoformat()}):*")
        for d in today_counts[:10]:
            status_str = ", ".join(f"{k}: {v}" for k, v in d["statuses"].items() if k in UNABLE_TO_DELIVER_STATUSES)
            lines.append(f"• *{d['driver_name']}* — {d['unable_to_deliver']} unable-to-deliver ({status_str})")
    if trailing:
        lines.append(f"\n*Trailing {trailing_days} days (habitual pattern):*")
        for d in trailing[:10]:
            flag = " ⚠️ _repeat pattern_" if d["days_flagged"] >= 3 else ""
            lines.append(f"• *{d['driver_name']}* — {d['unable_to_deliver']} total across {d['days_flagged']} day(s){flag}")
    lines.append("\n_Reminder: drivers should confirm with Blake before marking a package this way — this list is to help spot who may not be doing that yet, not a punishment list._")

    client = _client()
    if not client:
        return {"status": "no_slack_token", "text": "\n".join(lines)}
    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text="\n".join(lines))
    except Exception as exc:
        logger.warning("Non-delivered marking alert post failed: %s", exc)
        return {"status": "send_failed", "error": str(exc)}
    return {"status": "sent", "today_count": len(today_counts), "trailing_count": len(trailing)}


@router.get("/offender-report")
def offender_report(trailing_days: int = 7, db: Session = Depends(get_db)):
    """Read-only view of the same data send_offender_alert_to_mgt posts."""
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    return {
        "snapshot": _serialize_snapshot(snap),
        "today": get_driver_status_counts(db, snap),
        "trailing": get_trailing_offender_report(db, days=trailing_days),
    }


@router.post("/send-offender-alert")
def send_offender_alert_endpoint(trailing_days: int = 7, db: Session = Depends(get_db)):
    return send_offender_alert_to_mgt(db, trailing_days=trailing_days)
