"""
App Suggestions — a lightweight, trackable inbox for suggestions and
database/system upgrade ideas, fed by a "Submit a Suggestion" button on
every Slack Home tab (driver, dispatch, HR) -- added 2026-08-05, mirrors
glitch_reports.py's shape exactly but for ideas rather than bugs.

On submit: persists a row here AND DMs the "owner" role
(document_routing.get_role_slack_ids) directly, so it reaches a human
immediately too.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, AppSuggestion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/suggestions", tags=["suggestions"])


def _serialize(r: AppSuggestion) -> dict:
    return {
        "id": r.id,
        "reporter_name": r.reporter_name,
        "source_page": r.source_page,
        "category": r.category,
        "description": r.description,
        "status": r.status,
        "reported_at": r.reported_at.isoformat() if r.reported_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "resolved_by": r.resolved_by,
    }


_SOURCE_LABELS = {
    "driver_home": "Driver Home",
    "dispatch_home": "Dispatch Home",
    "hr_home": "HR Home",
}
_CATEGORY_LABELS = {
    "suggestion": "Suggestion",
    "database_upgrade": "Database / System Upgrade Idea",
}


def submit_suggestion(
    reporter_name: str,
    reporter_slack_id: Optional[str],
    source_page: str,
    category: str,
    description: str,
    db: Session,
) -> AppSuggestion:
    """Persist the suggestion, then DM the "owner" role directly so it
    reaches a human right away, not just the tracked list."""
    row = AppSuggestion(
        reporter_name=reporter_name,
        reporter_slack_id=reporter_slack_id,
        source_page=source_page,
        category=category if category in _CATEGORY_LABELS else "suggestion",
        description=description,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        if token:
            from api.src.routes.document_routing import get_role_slack_ids
            owner_ids = get_role_slack_ids(db, "owner")
            if owner_ids:
                from slack_sdk import WebClient
                client = WebClient(token=token)
                text = (
                    f"💡 *{_CATEGORY_LABELS.get(row.category, 'Suggestion')}* — from *{reporter_name}* "
                    f"({_SOURCE_LABELS.get(source_page, source_page)})\n> {description}"
                )
                for uid in owner_ids:
                    client.chat_postMessage(channel=uid, text=text)
    except Exception as exc:
        logger.warning("Suggestion notify failed: %s", exc)

    return row


@router.get("")
def list_suggestions(status: Optional[str] = None, category: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(AppSuggestion)
    if status:
        q = q.filter(AppSuggestion.status == status)
    if category:
        q = q.filter(AppSuggestion.category == category)
    rows = q.order_by(AppSuggestion.reported_at.desc()).all()
    return {"suggestions": [_serialize(r) for r in rows]}


class ResolveRequest(BaseModel):
    resolved_by: Optional[str] = None


@router.post("/{suggestion_id}/resolve")
def resolve_suggestion(suggestion_id: int, payload: ResolveRequest, db: Session = Depends(get_db)):
    row = db.query(AppSuggestion).filter(AppSuggestion.id == suggestion_id).first()
    if not row:
        raise HTTPException(404, f"Suggestion {suggestion_id} not found")
    row.status = "resolved"
    row.resolved_at = datetime.utcnow()
    row.resolved_by = payload.resolved_by
    db.commit()
    return _serialize(row)


@router.post("/{suggestion_id}/reopen")
def reopen_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    row = db.query(AppSuggestion).filter(AppSuggestion.id == suggestion_id).first()
    if not row:
        raise HTTPException(404, f"Suggestion {suggestion_id} not found")
    row.status = "open"
    row.resolved_at = None
    row.resolved_by = None
    db.commit()
    return _serialize(row)
