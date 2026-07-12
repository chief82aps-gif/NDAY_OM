"""
Document Routing & Requirements Config
========================================
Two small admin-editable config tables that other document-generating flows
(crash reports, injury reports, DVIC write-ups, etc.) consult:

  - DocumentRoutingRule      document_type -> which roles get notified
  - DocumentRequirementRule  document_type -> ordered list of fields/tasks
                             that must be completed before the document is
                             eligible for submission

Neither table is wired into a live trigger yet for crash/injury reports —
that lands with the crash-report wizard. DVIC's stage-4 write-up already
uses the manager-accountability queue directly (see dvic.py).
"""
from __future__ import annotations

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, Base, engine,
    DocumentRoutingRule, DocumentRequirementRule, RoleDirectory,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/document-config", tags=["document-config"])


def _ensure_tables():
    Base.metadata.create_all(
        bind=engine,
        tables=[
            DocumentRoutingRule.__table__,
            DocumentRequirementRule.__table__,
            RoleDirectory.__table__,
        ],
    )


# Seed defaults the user specified — safe to call repeatedly (upsert by document_type).
_DEFAULT_ROUTING = {
    "crash_report": ["dispatch", "ops_manager", "owner"],
    "injury_report": ["dispatch", "ops_manager", "hr"],
}

# Known Slack IDs, reused from rostering.py — same three people. "dispatch",
# "owner", and "hr" have no known Slack ID yet: left as an empty list (routing
# skips an empty role rather than erroring) until set via PUT /document-config/roles.
_DEFAULT_ROLE_DIRECTORY = {
    "ops_manager": ["U0BE493C5K9", "U0B36C9R8N4", "U0AJPQALDLL"],   # Spencer, Luis, Fabian
    "dispatch": [],
    "owner": [],
    "hr": [],
}


def seed_default_routing(db: Session) -> None:
    _ensure_tables()
    for doc_type, roles in _DEFAULT_ROUTING.items():
        existing = db.query(DocumentRoutingRule).filter(
            DocumentRoutingRule.document_type == doc_type
        ).first()
        if not existing:
            db.add(DocumentRoutingRule(document_type=doc_type, recipient_roles=roles))
    db.commit()


def seed_default_role_directory(db: Session) -> None:
    _ensure_tables()
    for role, slack_ids in _DEFAULT_ROLE_DIRECTORY.items():
        existing = db.query(RoleDirectory).filter(RoleDirectory.role_name == role).first()
        if not existing:
            db.add(RoleDirectory(role_name=role, slack_ids=slack_ids))
    db.commit()


def resolve_recipients(document_type: str, db: Session) -> dict[str, list[str]]:
    """Expand a document type's routing roles into actual Slack IDs.
    Returns {role_name: [slack_ids]} — roles with no IDs on file are
    included with an empty list so callers can report what's unset."""
    seed_default_routing(db)
    seed_default_role_directory(db)
    rule = db.query(DocumentRoutingRule).filter(
        DocumentRoutingRule.document_type == document_type
    ).first()
    roles = rule.recipient_roles if rule else []
    out: dict[str, list[str]] = {}
    for role in roles:
        entry = db.query(RoleDirectory).filter(RoleDirectory.role_name == role).first()
        out[role] = list(entry.slack_ids) if entry and entry.slack_ids else []
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Routing rules
# ─────────────────────────────────────────────────────────────────────────────

class RoutingRuleRequest(BaseModel):
    document_type: str
    recipient_roles: List[str]


@router.get("/routing")
def list_routing_rules(db: Session = Depends(get_db)):
    _ensure_tables()
    seed_default_routing(db)
    rows = db.query(DocumentRoutingRule).order_by(DocumentRoutingRule.document_type).all()
    return [
        {"document_type": r.document_type, "recipient_roles": r.recipient_roles or []}
        for r in rows
    ]


@router.get("/routing/{document_type}")
def get_routing_rule(document_type: str, db: Session = Depends(get_db)):
    _ensure_tables()
    rule = db.query(DocumentRoutingRule).filter(
        DocumentRoutingRule.document_type == document_type
    ).first()
    if not rule:
        raise HTTPException(404, f"No routing rule for document_type '{document_type}'")
    return {"document_type": rule.document_type, "recipient_roles": rule.recipient_roles or []}


@router.put("/routing")
def upsert_routing_rule(req: RoutingRuleRequest, db: Session = Depends(get_db)):
    """Create or replace the recipient list for a document type."""
    _ensure_tables()
    rule = db.query(DocumentRoutingRule).filter(
        DocumentRoutingRule.document_type == req.document_type
    ).first()
    if rule:
        rule.recipient_roles = req.recipient_roles
    else:
        rule = DocumentRoutingRule(document_type=req.document_type, recipient_roles=req.recipient_roles)
        db.add(rule)
    db.commit()
    return {"status": "saved", "document_type": req.document_type, "recipient_roles": req.recipient_roles}


# ─────────────────────────────────────────────────────────────────────────────
# Requirement (checklist) rules
# ─────────────────────────────────────────────────────────────────────────────

class RequirementFieldRequest(BaseModel):
    field_key: str
    field_label: str
    field_type: str = "text"          # text | number | photo | signature | boolean | select | date
    is_required: bool = True
    display_order: int = 0
    options: Optional[List[str]] = None


@router.get("/requirements/{document_type}")
def get_requirements(document_type: str, db: Session = Depends(get_db)):
    """Ordered checklist of fields/tasks a document type must complete before submission."""
    _ensure_tables()
    if document_type == "crash_report":
        seed_crash_report_requirements(db)
    rows = (
        db.query(DocumentRequirementRule)
        .filter(DocumentRequirementRule.document_type == document_type)
        .order_by(DocumentRequirementRule.display_order, DocumentRequirementRule.id)
        .all()
    )
    return {
        "document_type": document_type,
        "fields": [
            {
                "field_key": r.field_key,
                "field_label": r.field_label,
                "field_type": r.field_type,
                "is_required": r.is_required,
                "display_order": r.display_order,
                "options": r.options,
            }
            for r in rows
        ],
    }


@router.put("/requirements/{document_type}")
def replace_requirements(document_type: str, fields: List[RequirementFieldRequest], db: Session = Depends(get_db)):
    """Replace the entire requirement checklist for a document type."""
    _ensure_tables()
    db.query(DocumentRequirementRule).filter(
        DocumentRequirementRule.document_type == document_type
    ).delete(synchronize_session=False)
    for f in fields:
        db.add(DocumentRequirementRule(
            document_type=document_type,
            field_key=f.field_key,
            field_label=f.field_label,
            field_type=f.field_type,
            is_required=f.is_required,
            display_order=f.display_order,
            options=f.options,
        ))
    db.commit()
    return {"status": "saved", "document_type": document_type, "field_count": len(fields)}


# ─────────────────────────────────────────────────────────────────────────────
# Role directory (role name -> Slack IDs)
# ─────────────────────────────────────────────────────────────────────────────

class RoleDirectoryRequest(BaseModel):
    role_name: str
    slack_ids: List[str]


@router.get("/roles")
def list_role_directory(db: Session = Depends(get_db)):
    seed_default_role_directory(db)
    rows = db.query(RoleDirectory).order_by(RoleDirectory.role_name).all()
    return [{"role_name": r.role_name, "slack_ids": r.slack_ids or []} for r in rows]


@router.put("/roles")
def upsert_role_directory(req: RoleDirectoryRequest, db: Session = Depends(get_db)):
    """Create or replace the Slack ID list for a role (e.g. 'owner', 'hr', 'dispatch')."""
    _ensure_tables()
    entry = db.query(RoleDirectory).filter(RoleDirectory.role_name == req.role_name).first()
    if entry:
        entry.slack_ids = req.slack_ids
    else:
        entry = RoleDirectory(role_name=req.role_name, slack_ids=req.slack_ids)
        db.add(entry)
    db.commit()
    return {"status": "saved", "role_name": req.role_name, "slack_ids": req.slack_ids}


# ─────────────────────────────────────────────────────────────────────────────
# Crash report requirement checklist — seeded from Amazon's
# "DA Incident Packet v3.3" paper form. Third-party and police-report
# fields are intentionally NOT required here (they're conditional on
# "was another vehicle involved" / "were police dispatched" — the
# crash-report submission endpoint enforces those two conditionally).
# ─────────────────────────────────────────────────────────────────────────────

_CRASH_REPORT_FIELDS = [
    # Safety checklist
    ("flashers_on", "Emergency flashers turned on", "boolean", True, 10, None),
    ("vehicle_secured", "Vehicle shut down and secured", "boolean", True, 20, None),
    ("hotline_called", "Called On-Road Emergency Hotline (1-844-311-0406)", "boolean", True, 30, None),
    ("hotline_call_at", "Date/time of hotline call", "date", True, 40, None),
    ("photos_360_taken", "360° photos of scene taken", "photo", True, 50, None),
    # General information
    ("accident_date", "Accident Date", "date", True, 100, None),
    ("accident_time", "Time of Accident", "text", True, 110, None),
    ("accident_ampm", "AM/PM", "select", True, 120, ["AM", "PM"]),
    ("location_address", "Location of Accident (Address)", "text", True, 130, None),
    ("city_state_zip", "City, State, Zip", "text", True, 140, None),
    ("driver_name", "Driver's Name", "text", True, 150, None),
    ("driver_license_number", "Driver License #", "text", True, 160, None),
    ("driver_license_state", "Driver License State", "text", True, 170, None),
    ("dsp_code", "DSP Code", "text", True, 180, None),
    # Vehicle information
    ("vehicle_year", "Vehicle Year", "text", True, 200, None),
    ("vehicle_make_model", "Vehicle Make & Model", "text", True, 210, None),
    ("license_plate_state", "License Plate & State", "text", True, 220, None),
    ("equipment_number", "Equipment # (Van)", "text", True, 230, None),
    ("vin", "VIN", "text", True, 240, None),
    ("amzl_station_origin", "AMZL Station (Origin)", "text", True, 250, None),
    ("destination_type", "Destination", "select", True, 260, ["Delivery", "AMZL Station", "Vehicle Service"]),
    # Third party (conditional — not marked required here)
    ("third_party_involved", "Was another vehicle involved?", "boolean", False, 300, None),
    ("third_party_driver_name", "Third Party Driver's Name", "text", False, 310, None),
    ("third_party_driver_address", "Third Party Driver's Address", "text", False, 320, None),
    ("third_party_driver_phone", "Third Party Driver's Phone #", "text", False, 330, None),
    ("third_party_insurance", "Third Party Insurance Co. & Policy No.", "text", False, 340, None),
    ("third_party_vehicle_year", "Third Party Vehicle Year", "text", False, 350, None),
    ("third_party_vehicle_make_model", "Third Party Make & Model", "text", False, 360, None),
    ("third_party_license_plate_state", "Third Party License Plate & State", "text", False, 370, None),
    ("third_party_license_no", "Third Party Driver License No.", "text", False, 380, None),
    ("third_party_license_state", "Third Party License State", "text", False, 390, None),
    # Narrative
    ("accident_description", "Describe accident and how it happened", "text", True, 400, None),
    # Conditions/other (optional)
    ("num_lanes", "Number of Lanes (each direction)", "number", False, 500, None),
    ("road_construction", "Road Construction", "select", False, 510, ["Asphalt", "Concrete", "Gravel", "Shell", "Dirt"]),
    ("road_attitude", "Road Attitude", "select", False, 520, ["Straightaway", "Intersection", "Downhill", "Curve", "Uphill", "Circle"]),
    ("traffic_conditions", "Traffic Conditions", "select", False, 530, ["Light", "Medium", "Congested", "No Traffic"]),
    ("light_conditions", "Light Conditions", "select", False, 540, ["Daylight", "Dawn/Dusk", "Dark/Road Lighted", "Dark/No Light"]),
    ("road_conditions", "Road Conditions", "select", False, 550, ["Dry/Normal", "Wet", "Muddy", "Ice", "Snow"]),
    ("weather_conditions", "Weather Conditions", "select", False, 560, ["Clear", "Cloudy", "Foggy", "Raining", "Sleeting", "Snowing", "Hailing", "Dust Storm", "Other"]),
    # Additional information (conditional on police dispatched — not required here)
    ("police_department", "Police Department Reported", "text", False, 600, None),
    ("officer_name", "Officer's Name", "text", False, 610, None),
    ("police_phone", "Police Phone No.", "text", False, 620, None),
    ("police_report_no", "Police Report No.", "text", False, 630, None),
    ("citation_issued", "Citation Issued?", "boolean", False, 640, None),
    # Diagram
    ("diagram_url", "Diagram of accident scene (photo)", "photo", True, 700, None),
]


def seed_crash_report_requirements(db: Session) -> None:
    """Idempotent — only seeds if no crash_report requirements exist yet,
    so an admin's later edits via PUT /document-config/requirements aren't
    clobbered on every request."""
    existing = db.query(DocumentRequirementRule).filter(
        DocumentRequirementRule.document_type == "crash_report"
    ).first()
    if existing:
        return
    for key, label, ftype, required, order, options in _CRASH_REPORT_FIELDS:
        db.add(DocumentRequirementRule(
            document_type="crash_report",
            field_key=key,
            field_label=label,
            field_type=ftype,
            is_required=required,
            display_order=order,
            options=options,
        ))
    db.commit()


def validate_submission(document_type: str, submitted_data: dict, db: Session) -> List[str]:
    """Return a list of missing-field labels (empty = eligible for submission)."""
    rows = (
        db.query(DocumentRequirementRule)
        .filter(
            DocumentRequirementRule.document_type == document_type,
            DocumentRequirementRule.is_required == True,
        )
        .all()
    )
    missing = []
    for r in rows:
        value = submitted_data.get(r.field_key)
        if value is None or value == "" or value == []:
            missing.append(r.field_label)
    return missing
