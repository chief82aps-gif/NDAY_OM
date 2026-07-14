"""Hiring pipeline intake — receives candidate data scraped by the Chrome
extension from Indeed, syncs it into the live "New Day Hiring" Asana board,
tags keywords/tenure for later performance correlation, and (when contact
info is available) upserts a Google Contact in the shared recruiting account.

See Governance/03_NDL_Hiring_Onboarding_Automation.md for the full design.

Auth: the extension is not a logged-in NDAY_OM user, so this route is
protected by a shared-secret header (X-Extension-Key / CANDIDATE_SYNC_KEY)
rather than the JWT-based RBAC used elsewhere in the app. Revisit if the
extension ever needs per-user attribution.
"""
from __future__ import annotations

import os
import re
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, Candidate, CandidateKeywordTag, KeywordRule
from api.src.asana_integration import AsanaClient
from api.src.google_contacts import GoogleContactsClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/candidates", tags=["candidates"])

ASANA_PROJECT_NAME = os.getenv("ASANA_HIRING_PROJECT_NAME", "New Day Hiring")
ASANA_PROJECT_GID = os.getenv("ASANA_HIRING_PROJECT_GID")  # e.g. 1202834412268957 — preferred over name lookup
GOOGLE_CONTACTS_GROUP = os.getenv("GOOGLE_CONTACTS_GROUP", "Candidates")

SECTION_BY_DECISION = {
    "accept": "1st Contact/Interview",
    "undecided": "Undecided in Indeed",
    # "reject" intentionally has no Asana section — do nothing in Asana per spec
}

PHONE_RE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _verify_extension_key(x_extension_key: Optional[str] = Header(None)) -> None:
    expected = os.getenv("CANDIDATE_SYNC_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="CANDIDATE_SYNC_KEY not configured on server")
    if x_extension_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing extension key")


class ScreenerAnswer(BaseModel):
    question: str = ""
    answer: str = ""


class WorkExperienceEntry(BaseModel):
    title: str = ""
    employer: str = ""
    date_range: str = ""


class CandidateSyncPayload(BaseModel):
    indeed_candidate_id: str
    decision: str  # "accept" | "undecided" | "reject"
    raw_name: str
    location: Optional[str] = None
    indeed_profile_url: Optional[str] = None
    indeed_match_score: Optional[int] = None
    recruiting_summary: Optional[str] = None
    resume_url: Optional[str] = None
    screener_answers: List[ScreenerAnswer] = []
    work_experience: List[WorkExperienceEntry] = []


def _normalize_name(raw_name: str) -> tuple[str, str]:
    """Best-effort "First Last" normalization, handling "Last, First" order."""
    raw_name = (raw_name or "").strip()
    if "," in raw_name:
        last, first = raw_name.split(",", 1)
        first, last = first.strip(), last.strip()
    else:
        parts = raw_name.split()
        if not parts:
            return "", ""
        first = parts[0]
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return first.title(), last.title()


def _extract_contact_info(screener_answers: List[ScreenerAnswer]) -> tuple[Optional[str], Optional[str]]:
    """Phone/email live inside free-text screener-question answers on Indeed,
    not a fixed contact field, and question wording can change between job
    postings — so match on the answer VALUE, not the question label."""
    phone, email = None, None
    for qa in screener_answers:
        answer = qa.answer or ""
        if not email:
            match = EMAIL_RE.search(answer)
            if match:
                email = match.group(0)
        if not phone:
            match = PHONE_RE.search(answer)
            if match:
                phone = match.group(0)
    return phone, email


def _parse_tenure_months(date_range: str) -> Optional[float]:
    """Parse strings like 'March 2026 - Present' or 'June 2023 - February 2026'."""
    if not date_range:
        return None
    parts = re.split(r"[–—-]", date_range)
    if len(parts) != 2:
        return None
    start_str, end_str = parts[0].strip(), parts[1].strip()

    def _parse_month_year(s: str) -> Optional[datetime]:
        if s.lower() == "present":
            return datetime.utcnow()
        for fmt in ("%B %Y", "%b %Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    start = _parse_month_year(start_str)
    end = _parse_month_year(end_str)
    if not start or not end or end < start:
        return None
    return round((end - start).days / 30.44, 1)


def _tag_keywords(db: Session, candidate: Candidate, work_experience: List[WorkExperienceEntry]) -> None:
    text_blob = " ".join(f"{w.title} {w.employer}" for w in work_experience).lower()
    if not text_blob.strip():
        return

    db.query(CandidateKeywordTag).filter(CandidateKeywordTag.candidate_id == candidate.id).delete()

    active_rules = db.query(KeywordRule).filter(KeywordRule.active == True).all()  # noqa: E712
    for rule in active_rules:
        if rule.keyword.lower() in text_blob:
            db.add(CandidateKeywordTag(
                candidate_id=candidate.id,
                keyword=rule.keyword,
                category=rule.category,
                matched_text=text_blob[:500],
            ))


def _sync_to_asana(candidate: Candidate, decision: str) -> None:
    target_section_name = SECTION_BY_DECISION.get(decision)
    if not target_section_name:
        return  # reject -> do nothing in Asana

    asana = AsanaClient()
    if ASANA_PROJECT_GID:
        project_gid = ASANA_PROJECT_GID
    else:
        # Fallback for portability, but GET /projects appears to page/filter
        # in a way that doesn't reliably surface every project by name —
        # prefer setting ASANA_HIRING_PROJECT_GID to skip this lookup.
        project = asana.get_project_by_name(ASANA_PROJECT_NAME)
        if not project:
            raise HTTPException(status_code=500, detail=f"Asana project '{ASANA_PROJECT_NAME}' not found")
        project_gid = project["gid"]

    section = asana.get_section_by_name(project_gid, target_section_name)
    if not section:
        raise HTTPException(status_code=500, detail=f"Asana section '{target_section_name}' not found")

    full_name = f"{candidate.first_name} {candidate.last_name}".strip()
    notes_lines = []
    if candidate.email:
        notes_lines.append(candidate.email)
    if candidate.phone:
        notes_lines.append(candidate.phone)
    if candidate.recruiting_summary_text:
        notes_lines.append("")
        notes_lines.append(candidate.recruiting_summary_text)
    notes = "\n".join(notes_lines)

    existing_task = asana.find_task_by_gid(candidate.asana_task_gid) if candidate.asana_task_gid else None

    if existing_task:
        asana.update_task(candidate.asana_task_gid, {"name": full_name, "notes": notes})
        asana.move_task_to_section(candidate.asana_task_gid, section["gid"])
    else:
        task = asana.create_task(project_gid, {
            "name": full_name,
            "notes": notes,
            "memberships": [{"project": project_gid, "section": section["gid"]}],
        })
        candidate.asana_task_gid = task["gid"]


def _sync_to_google_contacts(candidate: Candidate) -> None:
    if not (candidate.phone or candidate.email):
        return
    try:
        client = GoogleContactsClient()
        contact = client.find_or_upsert_contact(
            first_name=candidate.first_name,
            last_name=candidate.last_name,
            phone=candidate.phone,
            email=candidate.email,
            group_name=GOOGLE_CONTACTS_GROUP,
        )
        candidate.google_contact_resource_name = contact.get("resourceName")
    except Exception as e:
        # Google Contacts is a nice-to-have layered on top of the Asana sync,
        # which is the source of truth for the pipeline — don't fail the
        # whole request if only this piece breaks (e.g. 7-day Testing-mode
        # refresh token expired).
        logger.warning("Google Contacts sync failed for candidate %s: %s", candidate.id, e)


@router.post("/sync")
def sync_candidate(
    payload: CandidateSyncPayload,
    db: Session = Depends(get_db),
    _auth: None = Depends(_verify_extension_key),
):
    if payload.decision not in ("accept", "undecided", "reject"):
        raise HTTPException(status_code=400, detail="decision must be accept, undecided, or reject")

    candidate = db.query(Candidate).filter(
        Candidate.indeed_candidate_id == payload.indeed_candidate_id
    ).first()
    if not candidate:
        candidate = Candidate(indeed_candidate_id=payload.indeed_candidate_id)
        db.add(candidate)

    first_name, last_name = _normalize_name(payload.raw_name)
    phone, email = _extract_contact_info(payload.screener_answers)

    candidate.first_name = first_name
    candidate.last_name = last_name
    candidate.location = payload.location or candidate.location
    candidate.indeed_profile_url = payload.indeed_profile_url or candidate.indeed_profile_url
    candidate.indeed_match_score = payload.indeed_match_score or candidate.indeed_match_score
    candidate.recruiting_summary_text = payload.recruiting_summary or candidate.recruiting_summary_text
    candidate.resume_url = payload.resume_url or candidate.resume_url
    candidate.phone = phone or candidate.phone
    candidate.email = email or candidate.email
    candidate.status = payload.decision

    tenures = [
        m for m in (_parse_tenure_months(w.date_range) for w in payload.work_experience) if m is not None
    ]
    if tenures:
        candidate.avg_tenure_months = round(sum(tenures) / len(tenures), 1)

    db.flush()  # ensure candidate.id is populated for keyword tagging / FK
    _tag_keywords(db, candidate, payload.work_experience)

    _sync_to_asana(candidate, payload.decision)
    if payload.decision in ("accept", "undecided"):
        _sync_to_google_contacts(candidate)

    db.commit()
    db.refresh(candidate)

    return {
        "candidate_id": candidate.id,
        "status": candidate.status,
        "asana_task_gid": candidate.asana_task_gid,
        "google_contact_resource_name": candidate.google_contact_resource_name,
        "avg_tenure_months": candidate.avg_tenure_months,
    }
