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

from api.src.database import get_db, Base, engine, DocumentRoutingRule, DocumentRequirementRule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/document-config", tags=["document-config"])


def _ensure_tables():
    Base.metadata.create_all(
        bind=engine,
        tables=[DocumentRoutingRule.__table__, DocumentRequirementRule.__table__],
    )


# Seed defaults the user specified — safe to call repeatedly (upsert by document_type).
_DEFAULT_ROUTING = {
    "crash_report": ["dispatch", "ops_manager", "owner"],
    "injury_report": ["dispatch", "ops_manager", "hr"],
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
