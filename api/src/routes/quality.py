"""
Quality Metrics module — ingest and ranking for DSP Overview Dashboard data.

Endpoints:
  POST /quality/ingest-slack        Scan #nday-ops-mgmt for new quality CSV and ingest it
  POST /quality/ingest-upload       Accept a direct file upload
  GET  /quality/rankings            Ranked driver list (latest snapshot)
  GET  /quality/driver/{tid}        Single driver history by Transporter ID
  GET  /quality/snapshots           List all ingested snapshots
"""
from __future__ import annotations

import io
import os
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func

from api.src.database import (
    get_db,
    QualityMetricSnapshot,
    QualityMetricDriver,
    SlackIngestLog,
)
from api.src.ingest.quality_metrics import parse_quality_metrics_csv

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quality", tags=["quality"])

CORTEX_CHANNEL = os.getenv("CORTEX_NOTIFY_CHANNEL", "C0BE4ALL1EX")

# Tier ordering for rostering priority (higher = better)
_STANDING_RANK = {"Platinum": 4, "Gold": 3, "Silver": 2, "Bronze": 1}

# Human-readable labels for bottom-metric callouts
_METRIC_LABELS: Dict[str, str] = {
    "speeding_score":          "Speeding Event Rate",
    "seatbelt_score":          "Seatbelt-Off Rate",
    "distraction_score":       "Distractions Rate",
    "sign_violation_score":    "Sign/Signal Violations",
    "following_distance_score":"Following Distance Rate",
    "cdf_dpmo_score":          "Customer Delivery Feedback (DPMO)",
    "dc_dpmo_score":           "Delivery Completion (DPMO)",
    "dsb_score":               "Delivery Success Behaviors",
    "pod_score":               "Photo on Delivery",
    "psb_score":               "Pickup Success Behaviors",
}


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _download_slack_file(url: str) -> Optional[bytes]:
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Quality CSV download failed: %s", exc)
        return None


def _scan_for_quality_csv(db: Session) -> Optional[dict]:
    """
    Scan #nday-operations-management for a quality metrics CSV not yet ingested.
    Returns {id, name, url} or None.
    """
    client = _slack_client()
    if not client:
        return None
    try:
        resp = client.files_list(channel=CORTEX_CHANNEL, count=30)
    except Exception as exc:
        logger.warning("Quality CSV scan failed: %s", exc)
        return None

    already_ids = {
        r.slack_file_id
        for r in db.query(SlackIngestLog.slack_file_id)
        .filter(SlackIngestLog.file_type == "quality_csv")
        .all()
    }

    for f in resp.get("files", []):
        name: str = f.get("name", "")
        nl = name.lower()
        if nl.endswith(".csv") and ("trailing" in nl or "overview" in nl or "quality" in nl):
            fid = f.get("id")
            if fid not in already_ids:
                return {
                    "id": fid,
                    "name": name,
                    "url": f.get("url_private_download") or f.get("url_private"),
                }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core ingest function (shared by Slack scan + direct upload)
# ─────────────────────────────────────────────────────────────────────────────

def _store_quality_metrics(
    content: bytes,
    filename: str,
    slack_file_id: Optional[str],
    db: Session,
) -> dict:
    """Parse CSV and upsert into the DB. Returns result dict."""
    summary, drivers = parse_quality_metrics_csv(content, filename)

    if not drivers:
        return {"status": "error", "message": "No driver rows parsed from file."}

    week = summary["week"]

    # Upsert snapshot: if same slack_file_id already stored, skip
    if slack_file_id:
        existing = db.query(QualityMetricSnapshot).filter(
            QualityMetricSnapshot.slack_file_id == slack_file_id
        ).first()
        if existing:
            return {"status": "already_ingested", "week": week, "driver_count": existing.driver_count}

    # Delete any previous snapshot for the same week (re-upload replaces)
    db.query(QualityMetricSnapshot).filter(
        QualityMetricSnapshot.week == week,
        QualityMetricSnapshot.source_file == filename,
    ).delete(synchronize_session=False)

    snap = QualityMetricSnapshot(
        week=week,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        driver_count=len(drivers),
    )
    db.add(snap)
    db.flush()

    for d in drivers:
        row = QualityMetricDriver(snapshot_id=snap.id, **d)
        db.add(row)

    # Log to SlackIngestLog if from Slack
    if slack_file_id:
        db.add(SlackIngestLog(
            ingest_date=datetime.now(timezone.utc).date(),
            file_type="quality_csv",
            slack_file_id=slack_file_id,
            filename=filename,
            processed_at=datetime.now(timezone.utc),
            status="success",
            records_processed=len(drivers),
        ))

    db.commit()
    return {"status": "ingested", "week": week, "driver_count": len(drivers)}


# ─────────────────────────────────────────────────────────────────────────────
# Ranking helpers
# ─────────────────────────────────────────────────────────────────────────────

def _focus_areas(driver: QualityMetricDriver, top_n: int = 3) -> List[str]:
    """Return the top_n lowest-scoring metric labels for a driver."""
    scores = {}
    for attr, label in _METRIC_LABELS.items():
        val = getattr(driver, attr, None)
        if val is not None:
            scores[label] = float(val)
    sorted_asc = sorted(scores.items(), key=lambda x: x[1])
    return [label for label, _ in sorted_asc[:top_n] if _ < 100]


def _driver_to_dict(driver: QualityMetricDriver, rank: int) -> dict:
    return {
        "rank": rank,
        "driver_name": driver.driver_name,
        "transporter_id": driver.transporter_id,
        "overall_standing": driver.overall_standing,
        "overall_score": float(driver.overall_score) if driver.overall_score is not None else None,
        "standing_rank": _STANDING_RANK.get(driver.overall_standing or "", 0),
        "focus_areas": _focus_areas(driver),
        "metrics": {
            "speeding_rate": float(driver.speeding_rate) if driver.speeding_rate is not None else None,
            "speeding_score": float(driver.speeding_score) if driver.speeding_score is not None else None,
            "seatbelt_rate": float(driver.seatbelt_rate) if driver.seatbelt_rate is not None else None,
            "seatbelt_score": float(driver.seatbelt_score) if driver.seatbelt_score is not None else None,
            "distraction_rate": float(driver.distraction_rate) if driver.distraction_rate is not None else None,
            "distraction_score": float(driver.distraction_score) if driver.distraction_score is not None else None,
            "sign_violation_rate": float(driver.sign_violation_rate) if driver.sign_violation_rate is not None else None,
            "sign_violation_score": float(driver.sign_violation_score) if driver.sign_violation_score is not None else None,
            "following_distance_rate": float(driver.following_distance_rate) if driver.following_distance_rate is not None else None,
            "following_distance_score": float(driver.following_distance_score) if driver.following_distance_score is not None else None,
            "cdf_dpmo": float(driver.cdf_dpmo) if driver.cdf_dpmo is not None else None,
            "cdf_dpmo_score": float(driver.cdf_dpmo_score) if driver.cdf_dpmo_score is not None else None,
            "dc_dpmo": float(driver.dc_dpmo) if driver.dc_dpmo is not None else None,
            "dc_dpmo_score": float(driver.dc_dpmo_score) if driver.dc_dpmo_score is not None else None,
            "dsb_count": driver.dsb_count,
            "dsb_score": float(driver.dsb_score) if driver.dsb_score is not None else None,
            "pod_pct": float(driver.pod_pct) if driver.pod_pct is not None else None,
            "pod_score": float(driver.pod_score) if driver.pod_score is not None else None,
            "psb_rate": float(driver.psb_rate) if driver.psb_rate is not None else None,
            "psb_score": float(driver.psb_score) if driver.psb_score is not None else None,
            "packages_delivered": driver.packages_delivered,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/ingest-slack")
def ingest_from_slack(db: Session = Depends(get_db)):
    """Scan #nday-operations-management for a new quality CSV and ingest it."""
    csv_file = _scan_for_quality_csv(db)
    if not csv_file:
        return {"status": "no_new_file", "message": "No new quality CSV found in channel."}

    content = _download_slack_file(csv_file["url"])
    if not content:
        raise HTTPException(502, "Could not download file from Slack.")

    return _store_quality_metrics(content, csv_file["name"], csv_file["id"], db)


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accept a direct CSV upload (multipart/form-data)."""
    content = await file.read()
    return _store_quality_metrics(content, file.filename or "upload.csv", None, db)


@router.get("/snapshots")
def list_snapshots(db: Session = Depends(get_db)):
    snaps = db.query(QualityMetricSnapshot).order_by(QualityMetricSnapshot.week.desc()).all()
    return [
        {
            "id": s.id,
            "week": s.week,
            "source_file": s.source_file,
            "driver_count": s.driver_count,
            "imported_at": s.imported_at.isoformat() if s.imported_at else None,
        }
        for s in snaps
    ]


@router.get("/rankings")
def get_rankings(week: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Return all drivers ranked for rostering: Platinum first, then by Overall Score desc.
    Defaults to the most recent snapshot week.
    """
    if not week:
        latest = db.query(func.max(QualityMetricSnapshot.week)).scalar()
        if not latest:
            return {"week": None, "drivers": [], "message": "No quality data ingested yet."}
        week = latest

    snap = db.query(QualityMetricSnapshot).filter(QualityMetricSnapshot.week == week).first()
    if not snap:
        raise HTTPException(404, f"No snapshot found for week {week}")

    drivers = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.snapshot_id == snap.id)
        .all()
    )

    ranked = sorted(
        drivers,
        key=lambda d: (
            _STANDING_RANK.get(d.overall_standing or "", 0),
            float(d.overall_score) if d.overall_score is not None else 0,
        ),
        reverse=True,
    )

    return {
        "week": week,
        "snapshot_id": snap.id,
        "driver_count": len(ranked),
        "drivers": [_driver_to_dict(d, i + 1) for i, d in enumerate(ranked)],
    }


@router.get("/driver/{transporter_id}")
def get_driver(transporter_id: str, db: Session = Depends(get_db)):
    """Return all snapshots for a single driver by Transporter ID."""
    rows = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.transporter_id == transporter_id)
        .order_by(QualityMetricDriver.week.desc())
        .all()
    )
    if not rows:
        raise HTTPException(404, f"No data found for transporter_id {transporter_id}")

    return {
        "transporter_id": transporter_id,
        "driver_name": rows[0].driver_name,
        "history": [_driver_to_dict(r, 0) for r in rows],
    }
