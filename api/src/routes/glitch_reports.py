"""
App Glitch Reports — a lightweight, trackable bug-report inbox fed by a
"Report an App Glitch" button on every Slack Home tab (driver, dispatch,
HR) -- added 2026-07-31, per explicit request for "an actionable list
that feeds you directly as we develop this system."

Distinct from slack_home.py's generic injury/incident quick-report modal
(_quick_report_modal / _handle_home_report_submit), which only DMs
configured recipients and never persists anywhere -- glitch reports are
meant to build a real, trackable (open/resolved) list over time, not a
Slack message that scrolls out of history.

On submit: persists a row here AND DMs the "owner" role (document_routing
.get_role_slack_ids) directly, so it reaches a human immediately too.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, AppGlitchReport

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/glitch-reports", tags=["glitch-reports"])


def _serialize(r: AppGlitchReport) -> dict:
    return {
        "id": r.id,
        "reporter_name": r.reporter_name,
        "source_page": r.source_page,
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


def submit_glitch_report(
    reporter_name: str,
    reporter_slack_id: Optional[str],
    source_page: str,
    description: str,
    db: Session,
) -> AppGlitchReport:
    """Persist the report, then DM the "owner" role directly so it reaches
    a human right away, not just the tracked list."""
    report = AppGlitchReport(
        reporter_name=reporter_name,
        reporter_slack_id=reporter_slack_id,
        source_page=source_page,
        description=description,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        if token:
            from api.src.routes.document_routing import get_role_slack_ids
            owner_ids = get_role_slack_ids(db, "owner")
            if owner_ids:
                from slack_sdk import WebClient
                client = WebClient(token=token)
                text = (
                    f"🐛 *App glitch reported* — from *{reporter_name}* "
                    f"({_SOURCE_LABELS.get(source_page, source_page)})\n> {description}"
                )
                for uid in owner_ids:
                    client.chat_postMessage(channel=uid, text=text)
    except Exception as exc:
        logger.warning("Glitch report notify failed: %s", exc)

    return report


@router.get("")
def list_glitch_reports(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(AppGlitchReport)
    if status:
        q = q.filter(AppGlitchReport.status == status)
    rows = q.order_by(AppGlitchReport.reported_at.desc()).all()
    return {"reports": [_serialize(r) for r in rows]}


class ResolveRequest(BaseModel):
    resolved_by: Optional[str] = None


@router.post("/{report_id}/resolve")
def resolve_glitch_report(report_id: int, payload: ResolveRequest, db: Session = Depends(get_db)):
    report = db.query(AppGlitchReport).filter(AppGlitchReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Glitch report {report_id} not found")
    report.status = "resolved"
    report.resolved_at = datetime.utcnow()
    report.resolved_by = payload.resolved_by
    db.commit()
    return _serialize(report)


@router.post("/{report_id}/reopen")
def reopen_glitch_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AppGlitchReport).filter(AppGlitchReport.id == report_id).first()
    if not report:
        raise HTTPException(404, f"Glitch report {report_id} not found")
    report.status = "open"
    report.resolved_at = None
    report.resolved_by = None
    db.commit()
    return _serialize(report)
