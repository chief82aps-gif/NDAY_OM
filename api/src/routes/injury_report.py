"""
Injury Report — digital version of the two-part paper incident/injury form
=============================================================================
A logged-in dispatch/ops user fills out both sections in one submission
when an incident occurs (unlike a callout, a supervisor is present at/soon
after an injury, so this isn't a driver self-service Slack-token flow like
attendance callouts or crash reports):

  1. Employee self-report — what happened, from the injured driver's account.
  2. Supervisor's Accident Investigation — the supervisor's own findings.

Sign-off is two flat fields (ops_manager, then hr) rather than a multi-stage
approval chain like CrashReport's — see manager_accountability.py's
discipline_tracker(), which surfaces unsigned InjuryReport rows alongside
unsigned attendance callouts, DVIC write-ups, and crash reports.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, InjuryReport
from api.src.authorization import require_any_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/injury-reports", tags=["injury-reports"])


class InjuryReportCreate(BaseModel):
    submitted_by: Optional[str] = None

    # Employee self-report
    employee_name: str
    supervisor_notified_at: Optional[str] = None
    incident_date: Optional[str] = None   # YYYY-MM-DD
    incident_time: Optional[str] = None
    location: Optional[str] = None
    activity_at_time: Optional[str] = None
    incident_description: Optional[str] = None
    prevention_suggestion: Optional[str] = None
    body_parts_injured: Optional[str] = None
    wants_medical_care: Optional[bool] = None
    medical_provider_name: Optional[str] = None
    medical_provider_phone: Optional[str] = None
    prior_injury: Optional[bool] = None
    prior_injury_when: Optional[str] = None
    supervisor_name: Optional[str] = None
    employee_signature_name: Optional[str] = None

    # Supervisor's Accident Investigation
    investigation_body_part_detail: Optional[str] = None
    incident_nature: Optional[str] = None
    investigation_description: Optional[str] = None
    event_date: Optional[str] = None
    event_time: Optional[str] = None
    event_location_detail: Optional[str] = None
    weather: Optional[str] = None
    cause_and_preventable: Optional[str] = None
    safety_regs_followed: Optional[bool] = None
    safety_regs_detail: Optional[str] = None
    recommended_preventive_action: Optional[str] = None
    medical_care_offered: Optional[bool] = None
    approved_provider_list_given: Optional[bool] = None
    actual_doctor_name: Optional[str] = None
    actual_hospital_name: Optional[str] = None
    supervisor_signature_name: Optional[str] = None


class InjuryReportSign(BaseModel):
    role: str   # "ops_manager" | "hr"
    signed_by: str


def _serialize(r: InjuryReport) -> dict:
    return {
        "id": r.id,
        "submitted_by": r.submitted_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "employee_name": r.employee_name,
        "supervisor_notified_at": r.supervisor_notified_at,
        "incident_date": r.incident_date.isoformat() if r.incident_date else None,
        "incident_time": r.incident_time,
        "location": r.location,
        "activity_at_time": r.activity_at_time,
        "incident_description": r.incident_description,
        "prevention_suggestion": r.prevention_suggestion,
        "body_parts_injured": r.body_parts_injured,
        "wants_medical_care": r.wants_medical_care,
        "medical_provider_name": r.medical_provider_name,
        "medical_provider_phone": r.medical_provider_phone,
        "prior_injury": r.prior_injury,
        "prior_injury_when": r.prior_injury_when,
        "supervisor_name": r.supervisor_name,
        "employee_signature_name": r.employee_signature_name,
        "employee_signature_at": r.employee_signature_at.isoformat() if r.employee_signature_at else None,
        "investigation_body_part_detail": r.investigation_body_part_detail,
        "incident_nature": r.incident_nature,
        "investigation_description": r.investigation_description,
        "event_date": r.event_date.isoformat() if r.event_date else None,
        "event_time": r.event_time,
        "event_location_detail": r.event_location_detail,
        "weather": r.weather,
        "cause_and_preventable": r.cause_and_preventable,
        "safety_regs_followed": r.safety_regs_followed,
        "safety_regs_detail": r.safety_regs_detail,
        "recommended_preventive_action": r.recommended_preventive_action,
        "medical_care_offered": r.medical_care_offered,
        "approved_provider_list_given": r.approved_provider_list_given,
        "actual_doctor_name": r.actual_doctor_name,
        "actual_hospital_name": r.actual_hospital_name,
        "supervisor_signature_name": r.supervisor_signature_name,
        "ops_manager_signed_by": r.ops_manager_signed_by,
        "ops_manager_signed_at": r.ops_manager_signed_at.isoformat() if r.ops_manager_signed_at else None,
        "hr_signed_by": r.hr_signed_by,
        "hr_signed_at": r.hr_signed_at.isoformat() if r.hr_signed_at else None,
    }


@router.post("")
def create_injury_report(req: InjuryReportCreate, db: Session = Depends(get_db)):
    """Submit a new injury report (both form sections at once)."""
    if not req.employee_name.strip():
        raise HTTPException(400, "employee_name is required.")

    def _parse_date(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    report = InjuryReport(
        submitted_by=req.submitted_by,
        employee_name=req.employee_name.strip(),
        supervisor_notified_at=req.supervisor_notified_at,
        incident_date=_parse_date(req.incident_date),
        incident_time=req.incident_time,
        location=req.location,
        activity_at_time=req.activity_at_time,
        incident_description=req.incident_description,
        prevention_suggestion=req.prevention_suggestion,
        body_parts_injured=req.body_parts_injured,
        wants_medical_care=req.wants_medical_care,
        medical_provider_name=req.medical_provider_name,
        medical_provider_phone=req.medical_provider_phone,
        prior_injury=req.prior_injury,
        prior_injury_when=req.prior_injury_when,
        supervisor_name=req.supervisor_name,
        employee_signature_name=req.employee_signature_name,
        employee_signature_at=datetime.utcnow() if req.employee_signature_name else None,
        investigation_body_part_detail=req.investigation_body_part_detail,
        incident_nature=req.incident_nature,
        investigation_description=req.investigation_description,
        event_date=_parse_date(req.event_date),
        event_time=req.event_time,
        event_location_detail=req.event_location_detail,
        weather=req.weather,
        cause_and_preventable=req.cause_and_preventable,
        safety_regs_followed=req.safety_regs_followed,
        safety_regs_detail=req.safety_regs_detail,
        recommended_preventive_action=req.recommended_preventive_action,
        medical_care_offered=req.medical_care_offered,
        approved_provider_list_given=req.approved_provider_list_given,
        actual_doctor_name=req.actual_doctor_name,
        actual_hospital_name=req.actual_hospital_name,
        supervisor_signature_name=req.supervisor_signature_name,
        supervisor_signature_at=datetime.utcnow() if req.supervisor_signature_name else None,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _serialize(report)


@router.get("/today-for-driver")
def injury_report_today_for_driver(employee_name: str, db: Session = Depends(get_db)):
    """Check whether an injury report already exists for this driver
    today — used by the EOD survey so answering "yes, I got hurt"
    doesn't prompt a second/duplicate report when one was already filed
    earlier the same day."""
    today = date.today()
    report = (
        db.query(InjuryReport)
        .filter(InjuryReport.employee_name == employee_name, InjuryReport.incident_date == today)
        .order_by(InjuryReport.created_at.desc())
        .first()
    )
    if not report:
        return {"exists": False}
    return {"exists": True, "report_id": report.id}


@router.get("/{report_id}")
def get_injury_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(InjuryReport).filter(InjuryReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Injury report not found.")
    return _serialize(report)


@router.post("/{report_id}/sign")
def sign_injury_report(
    report_id: int, req: InjuryReportSign, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("ops_manager", "hr")),
):
    """Sign as ops_manager or hr — the caller's actual JWT role must match
    req.role (an hr-role caller can't sign as ops_manager and vice versa;
    admin may sign as either, matching require_any_role's admin override)."""
    if caller_role != "admin" and caller_role != req.role:
        raise HTTPException(403, f"Your role ({caller_role}) cannot sign as {req.role}.")
    if req.role not in ("ops_manager", "hr"):
        raise HTTPException(400, "role must be 'ops_manager' or 'hr'.")

    report = db.query(InjuryReport).filter(InjuryReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Injury report not found.")

    now = datetime.utcnow()
    if req.role == "ops_manager":
        report.ops_manager_signed_by = req.signed_by
        report.ops_manager_signed_at = now
    else:
        report.hr_signed_by = req.signed_by
        report.hr_signed_at = now

    db.commit()
    db.refresh(report)
    return _serialize(report)
