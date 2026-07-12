"""
Crash Report — digital "DA Incident Packet v3.3"
==================================================
A manager clicks "Generate Crash Report" for a driver from a management
dashboard (similar to the rescue tracker). That creates a draft,
prepopulated with everything already known about the driver's route today
(van, VIN, DSP code, station) so the driver only has to answer what
actually happened. Submission is blocked until every required field (see
document_routing.seed_crash_report_requirements) is filled, then routes
to the configured recipients (document_routing -> DocumentRoutingRule
for "crash_report").
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, CrashReport, DailyRouteAssignment, Vehicle
from api.src.routes.document_routing import validate_submission, resolve_recipients

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/crash-report", tags=["crash-report"])

DSP_CODE = os.getenv("DSP_CODE", "DLV3")
AMZL_STATION = os.getenv("AMZL_STATION", "")   # station origin code, if set on Render

# NOTE: photos are stored on local disk, same as driver_handouts.pdf elsewhere
# in this app. Render's filesystem is not guaranteed to persist across
# deploys — see UPGRADE_BACKLOG.md's "AWS S3 or GCS bucket" item. Move these
# to durable cloud storage before relying on this for real incident evidence.
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '../../uploads/crash_reports')
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _next_report_number(db: Session) -> str:
    today_str = datetime.utcnow().strftime("%Y%m%d")
    count_today = db.query(CrashReport).filter(
        CrashReport.report_number.like(f"CR-{today_str}-%")
    ).count()
    return f"CR-{today_str}-{count_today + 1:04d}"


def _report_dict(r: CrashReport) -> dict:
    return {c.name: getattr(r, c.name) for c in CrashReport.__table__.columns}


def _serialize(r: CrashReport) -> dict:
    d = _report_dict(r)
    for k, v in d.items():
        if isinstance(v, (date, datetime)):
            d[k] = v.isoformat()
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Start — prepopulate from known assignment data
# ─────────────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    driver_name: str
    shift_date: Optional[str] = None    # defaults to today
    submitted_by: Optional[str] = None  # manager who generated the report


@router.post("/start")
def start_crash_report(req: StartRequest, db: Session = Depends(get_db)):
    """Create a draft crash report prepopulated from today's route assignment."""
    shift_date = date.fromisoformat(req.shift_date) if req.shift_date else date.today()

    assignment = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == shift_date,
            DailyRouteAssignment.driver_name == req.driver_name,
        )
        .first()
    )

    van_number = assignment.van_number if assignment else None
    vin = None
    if van_number:
        vehicle = db.query(Vehicle).filter(Vehicle.vehicle_name == van_number).first()
        vin = vehicle.vin if vehicle else None

    report = CrashReport(
        report_number=_next_report_number(db),
        submitted_by=req.submitted_by,
        status="draft",
        driver_name=req.driver_name,
        dsp_code=DSP_CODE,
        equipment_number=van_number,
        vin=vin,
        amzl_station_origin=AMZL_STATION or None,
        accident_date=shift_date,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return {"status": "draft_created", "report": _serialize(report)}


@router.get("/{report_id}")
def get_crash_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    return _serialize(report)


class UpdateRequest(BaseModel):
    fields: dict


_EDITABLE_FIELDS = {c.name for c in CrashReport.__table__.columns} - {
    "id", "report_number", "created_at", "status", "submitted_at", "routed_at", "routed_to",
}


@router.patch("/{report_id}")
def update_crash_report(report_id: int, req: UpdateRequest, db: Session = Depends(get_db)):
    """Autosave endpoint — the wizard PATCHes whichever fields the driver
    just filled in on the current step."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if report.status == "submitted":
        raise HTTPException(400, "This report has already been submitted and can't be edited.")

    unknown = set(req.fields) - _EDITABLE_FIELDS
    if unknown:
        raise HTTPException(400, f"Unknown field(s): {sorted(unknown)}")

    for key, value in req.fields.items():
        if key in ("accident_date", "hotline_call_at") and isinstance(value, str) and value:
            value = date.fromisoformat(value[:10]) if key == "accident_date" else datetime.fromisoformat(value)
        setattr(report, key, value)
    db.commit()
    db.refresh(report)
    return {"status": "saved", "report": _serialize(report)}


@router.post("/{report_id}/submit")
def submit_crash_report(report_id: int, db: Session = Depends(get_db)):
    """Validate against the required-field checklist, finalize, and notify
    every recipient configured in document_routing for 'crash_report'."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if report.status == "submitted":
        return {"status": "already_submitted", "report_number": report.report_number}

    data = _report_dict(report)
    missing = validate_submission("crash_report", data, db)

    # Conditional requirements the generic checklist can't express:
    if report.third_party_involved and not report.third_party_driver_name:
        missing.append("Third Party Driver's Name (required — another vehicle was involved)")
    if report.police_called and not report.police_report_no:
        missing.append("Police Report No. (required — police were called)")

    if missing:
        raise HTTPException(422, detail={"status": "incomplete", "missing_fields": missing})

    report.status = "submitted"
    report.submitted_at = datetime.utcnow()
    db.commit()

    recipients = resolve_recipients("crash_report", db)
    notified_roles = []
    client = _client()
    text = (
        f":rotating_light: *Crash Report Submitted — {report.report_number}*\n"
        f"Driver: *{report.driver_name}* | Van: *{report.equipment_number or '—'}* | "
        f"Date: {report.accident_date.isoformat() if report.accident_date else '—'}\n"
        f"Location: {report.location_address or '—'}, {report.city_state_zip or '—'}\n"
        f"View full report: report #{report.report_number}"
    )
    for role, slack_ids in recipients.items():
        if not slack_ids:
            logger.warning("Crash report %s: role '%s' has no Slack ID on file — skipped.", report.report_number, role)
            continue
        if client:
            for sid in slack_ids:
                try:
                    client.chat_postMessage(channel=sid, text=text)
                except Exception as exc:
                    logger.warning("Crash report notify failed (%s, %s): %s", role, sid, exc)
        notified_roles.append(role)

    report.routed_at = datetime.utcnow()
    report.routed_to = notified_roles
    db.commit()

    unset_roles = [role for role, ids in recipients.items() if not ids]
    return {
        "status": "submitted",
        "report_number": report.report_number,
        "notified_roles": notified_roles,
        "unset_roles": unset_roles or None,
    }


@router.post("/{report_id}/upload-photo")
def upload_photo(report_id: int, kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """kind: 'scene' (360 photos, appended to photo_urls) or 'diagram' (single, sets diagram_url)."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if kind not in ("scene", "diagram"):
        raise HTTPException(400, "kind must be 'scene' or 'diagram'")

    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    fname = f"{report.report_number}_{kind}_{datetime.utcnow().strftime('%H%M%S%f')}{ext}"
    dest = os.path.join(UPLOAD_DIR, fname)
    with open(dest, "wb") as f:
        f.write(file.file.read())

    url = f"/crash-report/photo/{fname}"
    if kind == "diagram":
        report.diagram_url = url
    else:
        urls = list(report.photo_urls or [])
        urls.append(url)
        report.photo_urls = urls
        report.photos_360_taken = True
    db.commit()

    return {"status": "uploaded", "kind": kind, "url": url}


@router.get("/photo/{filename}")
def get_photo(filename: str):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Photo not found")
    return FileResponse(path)
