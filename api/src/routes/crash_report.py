"""
Crash Report — digital "DA Incident Packet v3.3"
==================================================
A driver calls dispatch to report a crash; dispatch clicks "Generate Crash
Report" from the Dispatch Home tab / dashboard for that driver (see
slack_dispatch_home.py). That creates a draft, prepopulated with everything
already known about the driver's route today (van, VIN, DSP code, station)
so the driver only has to answer what actually happened, upload the
required evidence photos, and give a statement. Submission is blocked until
every required field (see document_routing.seed_crash_report_requirements),
photo category, and statement-quality check (_looks_sloppy) is satisfied.

On submit, the report enters a sequential Slack approval chain — dispatch
approves, then ops_manager, then owner (_APPROVAL_STAGES / _notify_stage /
_handle_crash_report_approve below) — rather than a flat broadcast. HR is
notified and the report is archived to the driver's employee-document
record automatically once the owner approves.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src import storage
from api.src.crash_report_pdf import generate_crash_report_pdf_bytes
from api.src.database import (
    get_db, CrashReport, CrashReportApproval, DailyRouteAssignment, EmployeeDocument, Vehicle,
)
from api.src.routes.document_routing import get_role_slack_ids, is_dispatch_staff, validate_submission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/crash-report", tags=["crash-report"])

DSP_CODE = os.getenv("DSP_CODE", "DLV3")
AMZL_STATION = os.getenv("AMZL_STATION", "")   # station origin code, if set on Render

# Evidence photos are real incident/legal records (DL photos, insurance,
# damage) — they live in S3 (see storage.py), not local disk. Each JSON
# column below stores S3 keys, never bare filenames or local paths.
_LIST_PHOTO_COLUMNS = {
    "scene": "photo_urls",
    "vehicle_damage": "photo_vehicle_damage",
    "other_vehicle": "photo_other_vehicle",
    "dl_driver": "photo_dl_driver",
    "dl_other": "photo_dl_other",
    "insurance_other": "photo_insurance_other",
    "license_plate_other": "photo_license_plate_other",
}
MIN_SCENE_PHOTOS = 6

# Anti-sloppiness locks on free-text statements — see _looks_sloppy().
_JUNK_PHRASES = {"n/a", "na", "idk", "asdf", "none", "nothing", "not sure", "unknown", "no comment"}
MIN_STATEMENT_CHARS = 120
MIN_STATEMENT_WORDS = 25


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


def _looks_sloppy(text: Optional[str]) -> Optional[str]:
    """Returns a rejection reason if a statement is too short or looks like a
    placeholder non-answer, else None. This is the anti-sloppiness lock —
    server-side, so it can't be bypassed by calling the API directly even if
    the wizard's client-side counter is skipped."""
    if not text or not text.strip():
        return "This field is empty."
    cleaned = text.strip()
    if cleaned.lower() in _JUNK_PHRASES:
        return "This doesn't look like a real answer — please describe what happened."
    if len(set(cleaned.lower().replace(" ", ""))) <= 2 and len(cleaned) > 3:
        return "This doesn't look like a real answer — please describe what happened."
    if len(cleaned) < MIN_STATEMENT_CHARS or len(cleaned.split()) < MIN_STATEMENT_WORDS:
        return f"Please add more detail — at least {MIN_STATEMENT_WORDS} words describing what happened."
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Statement sanitization — rewords fault-admission language, preserves facts.
# Verbatim originals are kept forever in the *_raw columns; this never
# discards what the driver actually said, only what appears on the routed
# report. Uses a haiku-tier model deliberately — this is a short, narrow
# rewrite task run once per statement, not open-ended reasoning.
# ─────────────────────────────────────────────────────────────────────────────

_SANITIZABLE_FIELDS = {"accident_description": "accident_description_raw",
                       "third_party_statement": "third_party_statement_raw"}

_SANITIZE_SYSTEM_PROMPT = (
    "You rewrite incident-report statements from a delivery driver's crash report. "
    "Reword only subjective admissions of fault or blame (e.g. 'it was my fault', "
    "'I ran the red light', 'I wasn't paying attention', 'I should have stopped') "
    "into neutral, factual descriptions of what happened (e.g. 'the vehicle "
    "proceeded through the intersection'). Preserve every factual detail exactly: "
    "times, locations, actions, sequence of events, what was said by anyone "
    "involved. Never add, remove, or invent facts. Never draw legal conclusions "
    "about who was at fault. If the statement contains no fault-admitting "
    "language, return it unchanged. Return only the rewritten statement text — "
    "no preamble, no commentary, no quotation marks."
)


def _anthropic_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _sanitize_statement(text: str) -> str:
    client = _anthropic_client()
    if not client:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not configured — sanitization unavailable.")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=_SANITIZE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        logger.warning("Statement sanitization failed: %s", exc)
        raise HTTPException(502, "Sanitization service failed — try again.")
    return next((b.text for b in response.content if b.type == "text"), text).strip()


class SanitizeRequest(BaseModel):
    field: str   # "accident_description" | "third_party_statement"


@router.post("/{report_id}/sanitize-statement")
def sanitize_statement(report_id: int, req: SanitizeRequest, db: Session = Depends(get_db)):
    """Rewords fault-admitting language in a statement, preserving facts. The
    verbatim original is captured into the matching *_raw column on the FIRST
    call only (never overwritten again) — the permanent record of what the
    driver/third party actually said. Returns both so the wizard can show a
    before/after preview before the driver moves on."""
    if req.field not in _SANITIZABLE_FIELDS:
        raise HTTPException(400, f"field must be one of {sorted(_SANITIZABLE_FIELDS)}")
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")

    current_text = getattr(report, req.field)
    sloppy = _looks_sloppy(current_text)
    if sloppy:
        raise HTTPException(422, f"Can't sanitize yet — {sloppy}")

    raw_column = _SANITIZABLE_FIELDS[req.field]
    if not getattr(report, raw_column):
        setattr(report, raw_column, current_text)

    sanitized = _sanitize_statement(current_text)
    setattr(report, req.field, sanitized)
    db.commit()
    db.refresh(report)
    return {
        "status": "sanitized",
        "field": req.field,
        "raw": getattr(report, raw_column),
        "sanitized": sanitized,
    }


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


@router.get("/{report_id}/pdf")
def get_crash_report_pdf(report_id: int, db: Session = Depends(get_db)):
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    pdf_bytes = generate_crash_report_pdf_bytes(report)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{report.report_number}.pdf"'},
    )


class UpdateRequest(BaseModel):
    fields: dict


_EDITABLE_FIELDS = {c.name for c in CrashReport.__table__.columns} - {
    "id", "report_number", "created_at", "status", "submitted_at", "routed_at", "routed_to",
    "drug_screen_status", "accident_description_raw", "third_party_statement_raw",
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
    """Validate against the required-field checklist, finalize, and kick off
    the sequential approval chain (dispatch -> ops_manager -> owner; see
    _notify_stage / _handle_crash_report_approve below)."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if report.status in ("submitted", "routed_complete"):
        return {"status": "already_submitted", "report_number": report.report_number}

    data = _report_dict(report)
    missing = validate_submission("crash_report", data, db)

    # Conditional requirements the generic checklist can't express:
    if report.third_party_involved and not report.third_party_driver_name:
        missing.append("Third Party Driver's Name (required — another vehicle was involved)")
    if report.police_called and not report.police_report_no:
        missing.append("Police Report No. (required — police were called)")

    # Anti-sloppiness locks — a report can't be submitted with a one-line
    # description no matter what the client sends (see _looks_sloppy).
    sloppy = _looks_sloppy(report.accident_description)
    if sloppy:
        missing.append(f"Driver's statement — {sloppy}")

    if report.third_party_involved:
        if not report.third_party_statement_declined:
            sloppy_tp = _looks_sloppy(report.third_party_statement)
            if sloppy_tp:
                missing.append(
                    f"Third party statement — {sloppy_tp} "
                    f"(or mark 'third party declined to provide a statement')"
                )
        # Conditional evidence — only required once another vehicle is involved.
        if not report.photo_other_vehicle:
            missing.append("Photo of third party's vehicle (required — another vehicle was involved)")
        if not report.photo_dl_other:
            missing.append("Photo of third party's driver's license (required — another vehicle was involved)")
        if not report.photo_insurance_other:
            missing.append("Photo of third party's insurance (required — another vehicle was involved)")
        if not report.photo_license_plate_other:
            missing.append("Photo of third party's license plate (required — another vehicle was involved)")

    # Minimum scene-photo count — the generic checklist only checks presence,
    # not count, so this is enforced manually.
    scene_count = len(report.photo_urls or [])
    if scene_count < MIN_SCENE_PHOTOS:
        missing.append(f"360° scene photos — need at least {MIN_SCENE_PHOTOS} (have {scene_count})")

    if missing:
        raise HTTPException(422, detail={"status": "incomplete", "missing_fields": missing})

    report.status = "submitted"
    report.submitted_at = datetime.utcnow()
    report.drug_screen_status = "pending"

    for stage_order, role in _APPROVAL_STAGES:
        db.add(CrashReportApproval(report_id=report.id, stage_order=stage_order, role=role, status="pending"))
    db.commit()
    db.refresh(report)

    _notify_stage(report, 1, "dispatch", db)

    # Drug-screen heads-up rides alongside the first approval request — this
    # is the "route to dispatch" moment; see rts.py for the follow-up nudge
    # when the driver actually returns.
    client = _client()
    dispatch_ids = get_role_slack_ids(db, "dispatch")
    if client and dispatch_ids:
        for sid in dispatch_ids:
            try:
                client.chat_postMessage(
                    channel=sid,
                    text=(f"⚠️ Schedule a post-accident drug screen for *{report.driver_name}* "
                          f"when they return (Report {report.report_number})."),
                )
            except Exception as exc:
                logger.warning("Drug-screen heads-up failed for %s: %s", sid, exc)

    return {"status": "submitted", "report_number": report.report_number}


# ─────────────────────────────────────────────────────────────────────────────
# Sequential approval chain — dispatch -> ops_manager -> owner. Each stage
# must click "Approve" before the next is notified (unlike the old flat
# broadcast, this makes routing an actual accountability chain, not just an
# FYI blast). HR is notified automatically once the owner approves — HR
# isn't a gating stage, just informed — and the report is archived to the
# employee file at the same point. See document_routing.py for the
# get_role_slack_ids / is_dispatch_staff helpers this reuses.
# ─────────────────────────────────────────────────────────────────────────────

_APPROVAL_STAGES = [(1, "dispatch"), (2, "ops_manager"), (3, "owner")]


def _notify_stage(report: CrashReport, stage_order: int, role: str, db: Session) -> None:
    approval = db.query(CrashReportApproval).filter(
        CrashReportApproval.report_id == report.id,
        CrashReportApproval.stage_order == stage_order,
    ).first()
    if not approval:
        return

    slack_ids = get_role_slack_ids(db, role)
    client = _client()
    if not slack_ids:
        logger.warning("Crash report %s: role '%s' has no Slack ID on file — stage %s not notified.",
                        report.report_number, role, stage_order)
        return
    if not client:
        return

    summary = (
        f":rotating_light: *Crash Report {report.report_number} — awaiting your approval*\n"
        f"Driver: *{report.driver_name}* | Van: *{report.equipment_number or '—'}* | "
        f"Date: {report.accident_date.isoformat() if report.accident_date else '—'}\n"
        f"Location: {report.location_address or '—'}, {report.city_state_zip or '—'}"
    )
    approve_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "action_id": "crash_report_approve",
                "text": {"type": "plain_text", "text": "✅ Approve & Route to Next Stage", "emoji": True},
                "style": "primary",
                "value": f"{report.id}:{stage_order}",
            }],
        },
    ]

    sent_channel = None
    sent_ts = None
    try:
        pdf_bytes = generate_crash_report_pdf_bytes(report)
    except Exception as exc:
        logger.warning("Crash report %s: PDF generation failed, notifying without attachment: %s",
                        report.report_number, exc)
        pdf_bytes = None

    for sid in slack_ids:
        try:
            if pdf_bytes:
                client.files_upload_v2(
                    channel=sid, file=pdf_bytes, filename=f"{report.report_number}.pdf",
                    initial_comment=summary,
                )
            msg = client.chat_postMessage(channel=sid, text="Approve this crash report:", blocks=approve_blocks)
            if sent_channel is None:
                sent_channel = msg.get("channel")
                sent_ts = msg.get("ts")
        except Exception as exc:
            logger.warning("Crash report %s: notify stage %s (%s) failed for %s: %s",
                            report.report_number, stage_order, role, sid, exc)

    approval.status = "notified"
    approval.notified_at = datetime.utcnow()
    approval.slack_channel = sent_channel
    approval.slack_ts = sent_ts
    db.commit()


def _handle_crash_report_approve(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "crash_report_approve"), None)
    if not action:
        return
    try:
        report_id_str, stage_order_str = (action.get("value") or "").split(":")
        report_id, stage_order = int(report_id_str), int(stage_order_str)
    except Exception:
        logger.warning("Malformed crash_report_approve value: %s", action.get("value"))
        return

    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    approval = db.query(CrashReportApproval).filter(
        CrashReportApproval.report_id == report_id,
        CrashReportApproval.stage_order == stage_order,
    ).first()
    if not report or not approval or approval.status == "approved":
        return  # unknown report/stage, or already handled (avoid double-advance on a double-click)

    role = approval.role
    authorized = is_dispatch_staff(user_id, db) if role == "dispatch" else user_id in get_role_slack_ids(db, role)
    if not authorized:
        logger.warning("Unauthorized crash_report_approve attempt by %s for stage '%s' (report %s)",
                        user_id, role, report.report_number)
        return

    approval.status = "approved"
    approval.approved_at = datetime.utcnow()
    approval.approved_by = user_id
    db.commit()

    client = _client()
    if client and approval.slack_channel and approval.slack_ts:
        try:
            client.chat_update(
                channel=approval.slack_channel, ts=approval.slack_ts,
                text=f"✅ Approved by <@{user_id}>",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"✅ *Approved by* <@{user_id}>"}}],
            )
        except Exception as exc:
            logger.warning("chat_update on crash report approval failed: %s", exc)

    next_stage = next(((so, r) for so, r in _APPROVAL_STAGES if so == stage_order + 1), None)
    if next_stage:
        _notify_stage(report, next_stage[0], next_stage[1], db)
        return

    # Owner (final gating stage) just approved — notify HR (informational
    # only, no approval action) and archive to the employee file.
    report.status = "routed_complete"
    report.routed_at = datetime.utcnow()
    db.commit()

    hr_ids = get_role_slack_ids(db, "hr")
    if client and hr_ids:
        try:
            pdf_bytes = generate_crash_report_pdf_bytes(report)
        except Exception as exc:
            logger.warning("Crash report %s: PDF generation failed for HR notify: %s", report.report_number, exc)
            pdf_bytes = None
        for sid in hr_ids:
            try:
                if pdf_bytes:
                    client.files_upload_v2(
                        channel=sid, file=pdf_bytes, filename=f"{report.report_number}.pdf",
                        initial_comment=(f":open_file_folder: Crash Report {report.report_number} fully approved — "
                                          f"driver *{report.driver_name}*, filed for HR records."),
                    )
            except Exception as exc:
                logger.warning("HR notify failed for %s: %s", sid, exc)

    db.add(EmployeeDocument(
        driver_name=report.driver_name,
        document_type="crash_report",
        related_record_id=report.id,
        file_url=f"/crash-report/{report.id}/pdf",
    ))
    db.commit()


def _handle_crash_report_drug_screen_done(payload: dict, db: Session) -> None:
    """Dispatch marks the post-accident drug screen complete — button
    fired either from the initial submit notification or from rts.py's
    driver-return nudge."""
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        return
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "crash_report_drug_screen_done"), None)
    if not action:
        return
    try:
        report_id = int(action.get("value") or "")
    except Exception:
        return
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        return
    report.drug_screen_status = "completed"
    db.commit()

    client = _client()
    channel = payload.get("channel", {}).get("id")
    ts = payload.get("message", {}).get("ts")
    if client and channel and ts:
        try:
            client.chat_update(
                channel=channel, ts=ts,
                text=f"✅ Drug screen marked complete for {report.driver_name} by <@{user_id}>",
                blocks=[{"type": "section", "text": {"type": "mrkdwn",
                          "text": f"✅ *Drug screen marked complete* for {report.driver_name} by <@{user_id}>"}}],
            )
        except Exception as exc:
            logger.warning("chat_update on drug-screen-done failed: %s", exc)


@router.post("/{report_id}/upload-photo")
def upload_photo(report_id: int, kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """kind: one of 'diagram' (single photo, sets diagram_url) or the keys in
    _LIST_PHOTO_COLUMNS ('scene', 'vehicle_damage', 'other_vehicle',
    'dl_driver', 'dl_other', 'insurance_other', 'license_plate_other') —
    each appended to its own JSON list column. Stored in S3 (storage.py),
    never local disk — these are real incident/legal evidence."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if kind != "diagram" and kind not in _LIST_PHOTO_COLUMNS:
        raise HTTPException(400, f"kind must be 'diagram' or one of {sorted(_LIST_PHOTO_COLUMNS)}")

    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    key = storage.build_key(
        "crash_reports", report.report_number,
        f"{kind}_{datetime.utcnow().strftime('%H%M%S%f')}{ext}",
    )
    data = file.file.read()
    storage.upload_bytes(data, key, content_type=file.content_type)

    if kind == "diagram":
        report.diagram_url = key
    else:
        column = _LIST_PHOTO_COLUMNS[kind]
        keys = list(getattr(report, column) or [])
        keys.append(key)
        setattr(report, column, keys)
        if kind == "scene":
            report.photos_360_taken = True
    db.commit()

    return {"status": "uploaded", "kind": kind, "key": key, "url": f"/crash-report/{report_id}/photo-url?key={key}"}


def _key_belongs_to_report(report: CrashReport, key: str) -> bool:
    if report.diagram_url == key:
        return True
    for column in _LIST_PHOTO_COLUMNS.values():
        if key in (getattr(report, column) or []):
            return True
    return False


@router.get("/{report_id}/photo-url")
def get_photo_url(report_id: int, key: str, db: Session = Depends(get_db)):
    """Redirects to a short-lived presigned S3 URL. Requires the key to
    actually belong to this report (photos are PII — DL/insurance — so this
    isn't a bare pass-through to S3)."""
    report = db.query(CrashReport).filter(CrashReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Crash report {report_id} not found")
    if not _key_belongs_to_report(report, key):
        raise HTTPException(404, "Photo not found on this report")
    return RedirectResponse(storage.presigned_url(key))
