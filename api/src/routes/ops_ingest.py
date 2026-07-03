"""
Ops Channel Ingest — watch #nday-operations-management for file uploads.

Workflow:
  1. Background loop (every 60 s) scans the channel via conversations.history.
  2. Every new file share creates an OpsIngestJob row (status=pending).
  3. Admin opens /ops-ingest, sees the queue, reads the description, clicks Ingest.
  4. The ingest endpoint downloads the file from Slack and dispatches to the
     correct handler based on detected_type.
  5. After success, a Slack confirmation is posted back to the channel.

File-type handlers are added one at a time as new file types are introduced.
"""
from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.src.database import get_db, OpsIngestJob

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ops-ingest", tags=["ops-ingest"])

OPS_CHANNEL = os.getenv("OPS_CHANNEL_ID", "C0BE4ALL1EX")  # #nday-operations-management


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _download_file(url: str) -> Optional[bytes]:
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Ops ingest download failed: %s", exc)
        return None


def _post_confirmation(text: str) -> None:
    client = _slack_client()
    if not client:
        return
    try:
        client.chat_postMessage(channel=OPS_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Ops ingest Slack confirm failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Classification — filename + user description → detected_type string
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_LABELS = {
    "quality_csv":       "Quality Metrics CSV",
    "dvic":              "DVIC Pre-Trip Under-90s (Excel)",
    "cortex":            "Cortex Routes (Excel)",
    "dop":               "DOP / Dispatch (Excel)",
    "driver_schedule":   "Driver Schedule (Excel)",
    "route_sheets":      "Route Sheets (PDF)",
    "wst_zip":           "WST Data (ZIP)",
    "variable_invoice":  "Variable Invoice (PDF)",
    "fleet_invoice":     "Fleet Invoice (PDF)",
    "weekly_incentive":  "Weekly Incentive (PDF)",
    "dsp_scorecard":     "DSP Scorecard (PDF)",
    "pod_report":        "POD Report (PDF)",
    "unknown":           "Unknown — needs manual review",
}


def _classify(filename: str, message: str) -> str:
    name = (filename or "").lower()
    msg = (message or "").lower()
    combined = name + " " + msg
    ext = os.path.splitext(name)[1]

    if ext == ".zip":
        return "wst_zip"

    if ext == ".csv":
        if any(k in combined for k in ("quality", "trailing", "overview", "scorecard")):
            return "quality_csv"
        if re.search(r"\bw\d{2}\b", combined):   # W27, W03, etc.
            return "quality_csv"
        return "unknown"

    if ext in (".xlsx", ".xls"):
        if any(k in combined for k in ("dvic", "pre_trip", "pre-trip", "under90", "under 90", "pretrip")):
            return "dvic"
        if any(k in combined for k in ("cortex", "routes_dlv", "dlv3", "dlv2")):
            return "cortex"
        if any(k in combined for k in ("dop", "dispatch ops", "dispatch_ops")):
            return "dop"
        if any(k in combined for k in ("schedule", "shift", "availability", "rostered work")):
            return "driver_schedule"
        return "unknown"

    if ext == ".pdf":
        if any(k in combined for k in ("variable invoice", "variable_invoice")):
            return "variable_invoice"
        if any(k in combined for k in ("fleet invoice", "fleet_invoice")):
            return "fleet_invoice"
        if any(k in combined for k in ("incentive",)):
            return "weekly_incentive"
        if any(k in combined for k in ("scorecard", "dsp score")):
            return "dsp_scorecard"
        if any(k in combined for k in ("pod report", "pod_report")):
            return "pod_report"
        if any(k in combined for k in ("route sheet", "route_sheet")):
            return "route_sheets"
        return "unknown"

    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Channel scan (called by background loop + manual Scan Now button)
# ─────────────────────────────────────────────────────────────────────────────

def scan_ops_channel(db: Session) -> list[str]:
    """
    Scan #nday-operations-management for new file shares.
    Creates OpsIngestJob rows for anything not yet seen.
    Returns list of new filenames detected.
    """
    client = _slack_client()
    if not client:
        logger.warning("Ops ingest: SLACK_BOT_TOKEN not set — scan skipped.")
        return []

    try:
        resp = client.conversations_history(channel=OPS_CHANNEL, limit=100)
    except Exception as exc:
        logger.warning("Ops channel history failed: %s", exc)
        return []

    known_ids: set[str] = {
        row[0] for row in db.query(OpsIngestJob.slack_file_id).all()
    }

    new_filenames: list[str] = []
    for msg in resp.get("messages", []):
        files = msg.get("files", [])
        if not files:
            continue

        msg_text: str = msg.get("text", "") or ""
        msg_ts: str = msg.get("ts", "") or ""

        for f in files:
            fid: str = f.get("id", "")
            if not fid or fid in known_ids:
                continue

            fname: str = f.get("name", "") or "unknown"
            url: str = f.get("url_private_download") or f.get("url_private") or ""
            dtype = _classify(fname, msg_text)

            job = OpsIngestJob(
                slack_file_id=fid,
                slack_message_ts=msg_ts,
                slack_message_text=msg_text[:1000] if msg_text else None,
                file_name=fname,
                file_url=url,
                detected_type=dtype,
                status="pending",
                detected_at=datetime.now(timezone.utc),
            )
            db.add(job)
            known_ids.add(fid)
            new_filenames.append(fname)
            logger.info("Ops ingest: queued %s as %s", fname, dtype)

    if new_filenames:
        db.commit()

    return new_filenames


# ─────────────────────────────────────────────────────────────────────────────
# Ingest dispatch — handlers added here as each file type is introduced
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch(job: OpsIngestJob, content: bytes, db: Session) -> dict:
    t = job.detected_type

    # ── Quality Metrics CSV ──────────────────────────────────────────────────
    if t == "quality_csv":
        from api.src.routes.quality import _store_quality_metrics
        return _store_quality_metrics(content, job.file_name, job.slack_file_id, db)

    # ── Cortex Routes Excel ──────────────────────────────────────────────────
    if t == "cortex":
        ext = os.path.splitext(job.file_name)[1].lower() or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from api.src.orchestrator import orchestrator
            from api.src.database import Cortex, ensure_cortex_driver_name_column
            from api.src.routes.uploads import _infer_file_date
            orchestrator.ingest_cortex(tmp_path)
            ensure_cortex_driver_name_column()
            upload_date = _infer_file_date(job.file_name) or datetime.utcnow().date()
            db.query(Cortex).filter(Cortex.source_file == job.file_name).delete(synchronize_session=False)
            for rec in orchestrator.status.cortex_records:
                db.add(Cortex(
                    assignment_date=upload_date,
                    dsp_code=rec.dsp,
                    route_code=rec.route_code,
                    service_type=rec.delivery_service_type,
                    driver_name=getattr(rec, "driver_name", None),
                    source_file=job.file_name,
                ))
            db.commit()
            return {"status": "ingested", "records": len(orchestrator.status.cortex_records)}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ── DOP Excel ────────────────────────────────────────────────────────────
    if t == "dop":
        ext = os.path.splitext(job.file_name)[1].lower() or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from api.src.orchestrator import orchestrator
            from api.src.database import DOP
            from api.src.routes.uploads import _infer_file_date
            orchestrator.ingest_dop(tmp_path)
            upload_date = _infer_file_date(job.file_name) or datetime.utcnow().date()
            db.query(DOP).filter(DOP.source_file == job.file_name).delete(synchronize_session=False)
            for rec in orchestrator.status.dop_records:
                db.add(DOP(
                    schedule_date=upload_date,
                    station=getattr(rec, "staging_location", None),
                    dsp_code=rec.dsp,
                    route_code=rec.route_code,
                    wave=rec.wave,
                    planned_packages=getattr(rec, "num_packages", None),
                    service_type=getattr(rec, "service_type", None),
                    source_file=job.file_name,
                ))
            db.commit()
            return {"status": "ingested", "records": len(orchestrator.status.dop_records)}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ── Driver Schedule Excel ────────────────────────────────────────────────
    if t == "driver_schedule":
        ext = os.path.splitext(job.file_name)[1].lower() or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from api.src.orchestrator import orchestrator
            ok = orchestrator.ingest_driver_schedule(tmp_path)
            if not ok:
                errs = orchestrator.status.validation_errors[-3:]
                return {"status": "error", "message": "; ".join(errs) or "Parse failed"}
            orchestrator.generate_driver_schedule_report(compact=True)
            return {
                "status": "ingested",
                "report_path": orchestrator.status.driver_schedule_report_path,
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ── DVIC Pre-Trip Under-90s Excel ────────────────────────────────────────
    if t == "dvic":
        from api.src.routes.dvic import _store_dvic
        return _store_dvic(content, job.file_name, job.slack_file_id, db)

    # ── DSP Scorecard PDF ────────────────────────────────────────────────────
    if t == "dsp_scorecard":
        from api.src.routes.dsp_scorecard_weekly import _store_scorecard, _post_summary_to_slack
        snap = _store_scorecard(content, job.file_name, job.slack_file_id, db)
        _post_summary_to_slack(snap)
        return {"status": "ingested", "week": snap.week, "overall": snap.overall_standing}

    # ── More handlers added here as file types are introduced ────────────────

    return {"status": "unsupported", "message": f"No ingest handler yet for type '{t}'. Will be added when this file type is introduced."}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _job_to_dict(j: OpsIngestJob) -> dict:
    return {
        "id": j.id,
        "slack_file_id": j.slack_file_id,
        "file_name": j.file_name,
        "detected_type": j.detected_type,
        "type_label": _TYPE_LABELS.get(j.detected_type, j.detected_type),
        "description": j.slack_message_text,
        "status": j.status,
        "result": j.result_json,
        "error_message": j.error_message,
        "detected_at": j.detected_at.isoformat() if j.detected_at else None,
        "ingested_at": j.ingested_at.isoformat() if j.ingested_at else None,
    }


@router.get("/jobs")
def list_jobs(status: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    """Return ops ingest jobs. Pass ?status=pending|complete|error|skipped to filter."""
    q = db.query(OpsIngestJob).order_by(OpsIngestJob.detected_at.desc())
    if status:
        q = q.filter(OpsIngestJob.status == status)
    jobs = q.limit(limit).all()
    pending = sum(1 for j in jobs if j.status == "pending")
    return {"total": len(jobs), "pending": pending, "jobs": [_job_to_dict(j) for j in jobs]}


@router.post("/scan")
def manual_scan(db: Session = Depends(get_db)):
    """Trigger an immediate scan of #nday-operations-management for new files."""
    new_files = scan_ops_channel(db)
    return {
        "status": "scanned",
        "new_files_detected": len(new_files),
        "filenames": new_files,
    }


@router.post("/jobs/{job_id}/ingest")
def ingest_job(job_id: int, db: Session = Depends(get_db)):
    """Download the Slack file and run it through the appropriate ingest handler."""
    job = db.query(OpsIngestJob).filter(OpsIngestJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status not in ("pending", "error"):
        raise HTTPException(400, f"Job status is '{job.status}' — only pending or error jobs can be ingested.")
    if not job.file_url:
        raise HTTPException(400, "No download URL stored for this job.")

    job.status = "ingesting"
    db.commit()

    content = _download_file(job.file_url)
    if not content:
        job.status = "error"
        job.error_message = "Could not download file from Slack. The link may have expired."
        db.commit()
        raise HTTPException(502, job.error_message)

    try:
        result = _dispatch(job, content, db)
    except Exception as exc:
        logger.exception("Ops ingest dispatch error for job %s", job_id)
        job.status = "error"
        job.error_message = str(exc)[:500]
        job.result_json = None
        db.commit()
        raise HTTPException(500, f"Ingest failed: {exc}")

    job.status = "complete" if result.get("status") not in ("error", "unsupported") else result["status"]
    job.result_json = result
    job.ingested_at = datetime.now(timezone.utc)
    db.commit()

    # Post Slack confirmation
    label = _TYPE_LABELS.get(job.detected_type, job.detected_type)
    if result.get("status") == "ingested":
        records = result.get("records") or result.get("driver_count") or result.get("records_parsed", "")
        confirm_text = f":white_check_mark: *{label} ingested* — `{job.file_name}`"
        if records:
            confirm_text += f"\nRecords loaded: {records}"
        if result.get("week"):
            confirm_text += f" | Week: {result['week']}"
        _post_confirmation(confirm_text)
    elif result.get("status") == "already_ingested":
        _post_confirmation(f":repeat: `{job.file_name}` was already ingested (skipped duplicate).")
    elif result.get("status") == "unsupported":
        _post_confirmation(f":warning: `{job.file_name}` queued but no handler built yet for type `{job.detected_type}`.")

    return _job_to_dict(job)


@router.post("/jobs/{job_id}/skip")
def skip_job(job_id: int, db: Session = Depends(get_db)):
    """Mark a job as skipped (won't be ingested)."""
    job = db.query(OpsIngestJob).filter(OpsIngestJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    job.status = "skipped"
    db.commit()
    return _job_to_dict(job)


@router.patch("/jobs/{job_id}/type")
def reclassify_job(job_id: int, detected_type: str, db: Session = Depends(get_db)):
    """Manually override the detected_type for a job before ingesting."""
    if detected_type not in _TYPE_LABELS:
        raise HTTPException(400, f"Unknown type. Valid: {list(_TYPE_LABELS.keys())}")
    job = db.query(OpsIngestJob).filter(OpsIngestJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    job.detected_type = detected_type
    job.status = "pending"
    db.commit()
    return _job_to_dict(job)
