"""
Ad-hoc Survey/Quiz module — added 2026-08-06 per explicit request: let an
admin author a set of questions (optionally graded, with a pass/fail
threshold), assign it to a specific ad-hoc set of drivers, and keep
nudging anyone who hasn't completed it until they do.

Deliberately does NOT gate routing/DRIVER_DM_ACTIVE in this version --
per explicit direction, v1 only surfaces who's incomplete (GET
/surveys/{id}/status) so dispatch can hold a driver back from routing
manually. An automated routing gate plus an escalation-to-termination
consequence is backlogged, not built here — see
Governance/FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md and
UPGRADE_BACKLOG.md. Research done before building this confirmed there
is no existing precedent anywhere in this codebase for an internal
compliance mechanism automatically terminating a driver (attendance.py's
points ladder only ever computes/displays a "termination" label; a
human always has to act on it) — so this module doesn't invent one.

Flow:
  1. Admin creates a survey with questions (POST /surveys), assigns an
     ad-hoc set of drivers (POST /surveys/{id}/assign), and sends it
     (POST /surveys/{id}/send) -- DMs each assigned, not-yet-completed
     driver a signed link to /survey?token=XXXX (same JWT-signed-link
     trust model as eod_survey.py's _issue_eod_token, single-purpose
     claim + expiry, no PIN needed).
  2. Driver taps the link, answers the questions, submits
     (POST /surveys/submit). Multiple-choice/true-false questions are
     auto-graded against the question's correct_answer; free_text
     questions are never auto-graded (informational only, excluded from
     scoring) -- an admin can review free-text answers via
     GET /surveys/{id}/responses.
  3. If nothing has changed and the survey is still "active" with an
     incomplete assignment, run_survey_nudges() (60s loop, gated by
     SURVEY_NUDGE_ACTIVE) re-sends the same link once every
     _NUDGE_INTERVAL_HOURS, indefinitely, until the driver submits or an
     admin closes the survey. No auto-escalation beyond re-sending --
     see the module docstring above for why.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, Survey, SurveyQuestion, SurveyAssignment, SurveyResponse, DriverRosterEntry,
)
from api.src.authorization import require_any_role
from api.src.feature_flags import get_flag
from api.src.slack_notification_gate import sends_paused, was_suppressed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/surveys", tags=["surveys"])

APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")
SURVEY_TOKEN_TTL_HOURS = 24 * 14   # 2 weeks -- an ad-hoc survey can sit open for a while, unlike EOD's same-day link
_NUDGE_INTERVAL_HOURS = 24


def _issue_survey_token(survey_id: int, roster_id: int, driver_name: str) -> str:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    payload = {
        "purpose": "survey_response",
        "survey_id": survey_id,
        "roster_id": roster_id,
        "driver_name": driver_name,
        "exp": int(time.time()) + SURVEY_TOKEN_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _verify_survey_token(token: str) -> Optional[dict]:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception as exc:
        logger.warning("Survey token failed to decode (%s: %s) — token preview %s…", type(exc).__name__, exc, token[:16])
        return None
    if payload.get("purpose") != "survey_response":
        logger.warning("Survey token had wrong purpose=%r — token preview %s…", payload.get("purpose"), token[:16])
        return None
    return payload


def _slack():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _dm_survey_link(slack_user_id: str, survey: Survey, token: str, is_nudge: bool) -> bool:
    client = _slack()
    if not client:
        return False
    url = f"{APP_URL}/survey?token={token}"
    kind = "quiz" if survey.is_quiz else "survey"
    text = (
        (f"⏰ Reminder — please complete this {kind}: *{survey.title}*" if is_nudge
         else f"📋 You have a new {kind} to complete: *{survey.title}*")
        + "\nThis needs your acknowledgment — please don't put it off."
    )
    try:
        resp = client.chat_postMessage(
            channel=slack_user_id, text=text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {"type": "actions", "elements": [{
                    "type": "button", "text": {"type": "plain_text", "text": f"Open {kind.capitalize()}", "emoji": True},
                    "style": "primary", "url": url, "action_id": "survey_open",
                }]},
            ],
        )
        # The pause gate returns a fake-success dict rather than raising, so a
        # bare try/except would record this as delivered. See
        # slack_notification_gate.was_suppressed().
        if was_suppressed(resp):
            logger.warning(
                "Survey DM to %s suppressed by SLACK_NOTIFICATIONS_ACTIVE=false "
                "— not recording it as sent.", slack_user_id,
            )
            return False
        return True
    except Exception as exc:
        logger.warning("Survey DM failed for %s: %s", slack_user_id, exc)
        return False


def _grade(survey: Survey, questions: list[SurveyQuestion], answers: dict) -> tuple[Optional[float], Optional[bool]]:
    """free_text questions are never auto-graded -- excluded entirely from
    the score. Returns (score_pct, passed), both None for a non-quiz survey."""
    if not survey.is_quiz:
        return None, None

    graded = [q for q in questions if q.question_type != "free_text" and q.correct_answer is not None]
    if not graded:
        return None, None

    earned = total = 0
    for q in graded:
        total += q.points
        given = str(answers.get(str(q.id), "")).strip().lower()
        correct = str(q.correct_answer).strip().lower()
        if given == correct:
            earned += q.points

    score_pct = round(100 * earned / total, 2) if total else None
    passed = None
    if score_pct is not None and survey.passing_score_pct is not None:
        passed = score_pct >= float(survey.passing_score_pct)
    return score_pct, passed


# ─────────────────────────────────────────────────────────────────────────────
# Admin: create / list / detail
# ─────────────────────────────────────────────────────────────────────────────

class QuestionIn(BaseModel):
    question_text: str
    question_type: str   # "multiple_choice" | "true_false" | "free_text"
    options: Optional[list[str]] = None
    correct_answer: Optional[str] = None
    points: int = 1


class SurveyCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    is_quiz: bool = False
    passing_score_pct: Optional[float] = None
    questions: list[QuestionIn]


_VALID_TYPES = {"multiple_choice", "true_false", "free_text"}


@router.post("")
def create_survey(
    payload: SurveyCreateRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    if not payload.questions:
        raise HTTPException(400, "A survey needs at least one question.")
    for q in payload.questions:
        if q.question_type not in _VALID_TYPES:
            raise HTTPException(400, f"Invalid question_type {q.question_type!r} — must be one of {sorted(_VALID_TYPES)}")
        if payload.is_quiz and q.question_type != "free_text" and not q.correct_answer:
            raise HTTPException(400, f"Question {q.question_text!r} needs a correct_answer for a graded quiz.")

    survey = Survey(
        title=payload.title, description=payload.description,
        is_quiz=payload.is_quiz, passing_score_pct=payload.passing_score_pct,
        status="draft", created_by=caller_role,
    )
    db.add(survey)
    db.flush()
    for i, q in enumerate(payload.questions):
        db.add(SurveyQuestion(
            survey_id=survey.id, order_index=i, question_text=q.question_text,
            question_type=q.question_type, options=q.options,
            correct_answer=q.correct_answer, points=q.points,
        ))
    db.commit()
    db.refresh(survey)
    return {"id": survey.id, "status": survey.status}


@router.get("")
def list_surveys(db: Session = Depends(get_db)):
    surveys = db.query(Survey).order_by(Survey.created_at.desc()).all()
    return {"surveys": [
        {
            "id": s.id, "title": s.title, "is_quiz": s.is_quiz, "status": s.status,
            "question_count": len(s.questions), "assignment_count": len(s.assignments),
            "completed_count": sum(1 for a in s.assignments if a.completed_at),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in surveys
    ]}


# `:int` converter is load-bearing, do not drop it. Without it this route is
# declared before GET /surveys/lookup and swallows it -- FastAPI matches in
# declaration order, so /surveys/lookup?token=... resolved to survey_id="lookup"
# and returned a 422 int_parsing error. That broke EVERY driver-facing survey
# link (the "Open Quiz" button in the DM) while the admin side looked healthy,
# which is why completion sat at 0 across all surveys. Found 2026-08-06.
# Same class of bug as the Slack Home tab routing collision (2026-08-05).
@router.get("/{survey_id:int}")
def get_survey(survey_id: int, db: Session = Depends(get_db)):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")
    return {
        "id": survey.id, "title": survey.title, "description": survey.description,
        "is_quiz": survey.is_quiz, "passing_score_pct": float(survey.passing_score_pct) if survey.passing_score_pct is not None else None,
        "status": survey.status,
        "questions": [
            {
                "id": q.id, "order_index": q.order_index, "question_text": q.question_text,
                "question_type": q.question_type, "options": q.options,
                "correct_answer": q.correct_answer, "points": q.points,
            }
            for q in survey.questions
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Admin: assign / send / status / close
# ─────────────────────────────────────────────────────────────────────────────

class AssignRequest(BaseModel):
    roster_ids: Optional[list[int]] = None
    all_active: bool = False


@router.post("/{survey_id}/assign")
def assign_survey(
    survey_id: int, payload: AssignRequest, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")

    if payload.all_active:
        roster_ids = [r.id for r in db.query(DriverRosterEntry.id).filter(DriverRosterEntry.is_active == True).all()]  # noqa: E712
    else:
        roster_ids = payload.roster_ids or []
    if not roster_ids:
        raise HTTPException(400, "Provide roster_ids or set all_active=true")

    existing = {a.roster_id for a in survey.assignments}
    added = 0
    for rid in roster_ids:
        if rid in existing:
            continue
        db.add(SurveyAssignment(survey_id=survey.id, roster_id=rid))
        added += 1
    db.commit()
    return {"status": "assigned", "added": added, "total_assigned": len(existing) + added}


@router.post("/{survey_id}/send")
def send_survey(
    survey_id: int, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    """Sends (or re-sends, for anyone not yet completed) the survey link.
    Same function used by the manual admin trigger and the nudge loop --
    see run_survey_nudges()."""
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")
    if survey.status == "closed":
        return {"status": "closed", "sent": 0}
    if survey.status == "draft":
        survey.status = "active"

    sent = already_done = no_slack = 0
    now = datetime.utcnow()
    for a in survey.assignments:
        if a.completed_at:
            already_done += 1
            continue
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
        if not entry or not entry.slack_member_id:
            no_slack += 1
            continue

        token = _issue_survey_token(survey.id, entry.id, entry.payroll_name)
        is_nudge = a.first_sent_at is not None
        if _dm_survey_link(entry.slack_member_id, survey, token, is_nudge=is_nudge):
            if not a.first_sent_at:
                a.first_sent_at = now
            else:
                a.nudge_count += 1
            a.last_nudge_at = now
            sent += 1
    db.commit()
    return {
        "status": "sent", "sent": sent, "already_completed": already_done,
        "no_slack_id": no_slack,
        # When true, every send was swallowed by the system-wide pause and
        # nothing reached a driver -- surfaced so the admin page can say so
        # outright instead of reporting a silent "sent 0".
        "paused": sends_paused(),
    }


@router.get("/{survey_id}/status")
def survey_status(survey_id: int, db: Session = Depends(get_db)):
    """Who's completed vs. still outstanding, with nudge counts and
    scores -- the manual-gate visibility tool: dispatch reads this and
    decides who to hold back from routing themselves. This module does
    not enforce that gate automatically (see module docstring)."""
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")

    responses = {r.roster_id: r for r in db.query(SurveyResponse).filter(SurveyResponse.survey_id == survey_id).all()}
    rows = []
    for a in survey.assignments:
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
        resp = responses.get(a.roster_id)
        rows.append({
            "roster_id": a.roster_id,
            "driver_name": entry.payroll_name if entry else "Unknown",
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
            "first_sent_at": a.first_sent_at.isoformat() if a.first_sent_at else None,
            "last_nudge_at": a.last_nudge_at.isoformat() if a.last_nudge_at else None,
            "nudge_count": a.nudge_count,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "score_pct": float(resp.score_pct) if resp and resp.score_pct is not None else None,
            "passed": resp.passed if resp else None,
        })
    incomplete = [r for r in rows if not r["completed_at"]]
    return {
        "survey_id": survey_id, "title": survey.title, "status": survey.status,
        "total_assigned": len(rows), "completed": len(rows) - len(incomplete),
        "incomplete": incomplete, "all": rows,
    }


@router.post("/{survey_id}/close")
def close_survey(
    survey_id: int, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")
    survey.status = "closed"
    survey.closed_at = datetime.utcnow()
    db.commit()
    return {"status": "closed"}


@router.delete("/{survey_id:int}")
def delete_survey(
    survey_id: int, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    """Hard-delete a survey and everything attached to it. IRREVERSIBLE --
    responses and scores go with it, so prefer /close for anything whose
    results still matter. Added 2026-08-07 to clear out the v1 test
    surveys; closing alone leaves dead rows in the admin list forever
    since there is no reopen path.

    Children are removed explicitly rather than leaning on the
    ondelete="CASCADE" FKs: Postgres enforces those in production, but
    SQLite ignores them unless PRAGMA foreign_keys=ON, so doing it by hand
    keeps local and prod behaviour identical.

    Uses the `:int` converter for the same reason as GET /{survey_id:int}
    -- so it can never swallow a future static path like /surveys/purge.
    """
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")

    title = survey.title
    responses = db.query(SurveyResponse).filter(SurveyResponse.survey_id == survey_id).delete(synchronize_session=False)
    assignments = db.query(SurveyAssignment).filter(SurveyAssignment.survey_id == survey_id).delete(synchronize_session=False)
    questions = db.query(SurveyQuestion).filter(SurveyQuestion.survey_id == survey_id).delete(synchronize_session=False)
    db.delete(survey)
    db.commit()

    logger.warning(
        "Survey %s (%r) hard-deleted by %s — %d responses, %d assignments, %d questions removed",
        survey_id, title, caller_role, responses, assignments, questions,
    )
    return {
        "status": "deleted", "survey_id": survey_id, "title": title,
        "deleted_responses": responses, "deleted_assignments": assignments,
        "deleted_questions": questions,
    }


@router.get("/{survey_id}/responses")
def survey_responses(survey_id: int, db: Session = Depends(get_db)):
    """Full answer detail per response -- for reviewing free_text answers
    (never auto-graded) and auditing graded ones."""
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, f"Survey {survey_id} not found")
    responses = db.query(SurveyResponse).filter(SurveyResponse.survey_id == survey_id).all()
    q_by_id = {q.id: q for q in survey.questions}
    return {"responses": [
        {
            "roster_id": r.roster_id, "driver_name": r.driver_name,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "score_pct": float(r.score_pct) if r.score_pct is not None else None,
            "passed": r.passed,
            "answers": [
                {
                    "question_id": qid, "question_text": q_by_id[int(qid)].question_text if int(qid) in q_by_id else "(deleted question)",
                    "answer": ans,
                }
                for qid, ans in (r.answers or {}).items()
            ],
        }
        for r in responses
    ]}


# ─────────────────────────────────────────────────────────────────────────────
# Driver-facing: lookup + submit
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/lookup")
def survey_lookup(token: str, db: Session = Depends(get_db)):
    payload = _verify_survey_token(token)
    if not payload:
        raise HTTPException(401, "This link is invalid or has expired.")
    survey = db.query(Survey).filter(Survey.id == payload["survey_id"]).first()
    if not survey:
        raise HTTPException(404, "This survey no longer exists.")

    existing = db.query(SurveyResponse).filter_by(survey_id=survey.id, roster_id=payload["roster_id"]).first()
    return {
        "survey_id": survey.id, "title": survey.title, "description": survey.description,
        "is_quiz": survey.is_quiz, "driver_name": payload["driver_name"],
        "already_submitted": existing is not None,
        "score_pct": float(existing.score_pct) if existing and existing.score_pct is not None else None,
        "passed": existing.passed if existing else None,
        "questions": [
            {"id": q.id, "question_text": q.question_text, "question_type": q.question_type, "options": q.options}
            for q in survey.questions
        ],
    }


class SubmitRequest(BaseModel):
    token: str
    answers: dict[str, str]


@router.post("/submit")
def submit_survey(payload: SubmitRequest, db: Session = Depends(get_db)):
    claims = _verify_survey_token(payload.token)
    if not claims:
        raise HTTPException(401, "This link is invalid or has expired.")

    survey = db.query(Survey).filter(Survey.id == claims["survey_id"]).first()
    if not survey:
        raise HTTPException(404, "This survey no longer exists.")

    existing = db.query(SurveyResponse).filter_by(survey_id=survey.id, roster_id=claims["roster_id"]).first()
    if existing:
        return {"status": "already_submitted", "score_pct": float(existing.score_pct) if existing.score_pct is not None else None, "passed": existing.passed}

    score_pct, passed = _grade(survey, survey.questions, payload.answers)

    db.add(SurveyResponse(
        survey_id=survey.id, roster_id=claims["roster_id"], driver_name=claims["driver_name"],
        answers=payload.answers, score_pct=score_pct, passed=passed,
    ))
    assignment = db.query(SurveyAssignment).filter_by(survey_id=survey.id, roster_id=claims["roster_id"]).first()
    if assignment:
        assignment.completed_at = datetime.utcnow()
    db.commit()
    return {"status": "submitted", "score_pct": score_pct, "passed": passed}


# ─────────────────────────────────────────────────────────────────────────────
# Nudge loop — every 60s from main.py's background loop
# ─────────────────────────────────────────────────────────────────────────────

def run_survey_nudges(db: Session) -> dict:
    """Re-sends any active survey's link to anyone still incomplete, no
    more than once every _NUDGE_INTERVAL_HOURS. Keeps going indefinitely
    -- there is no auto-escalation to a gate or a consequence built into
    this loop; see module docstring."""
    if not get_flag("SURVEY_NUDGE_ACTIVE"):
        return {"status": "inactive"}

    now = datetime.utcnow()
    active_surveys = db.query(Survey).filter(Survey.status == "active").all()
    if not active_surveys:
        return {"status": "no_active_surveys"}

    total_nudged = 0
    for survey in active_surveys:
        due = [
            a for a in survey.assignments
            if not a.completed_at and (
                not a.last_nudge_at or (now - a.last_nudge_at) >= timedelta(hours=_NUDGE_INTERVAL_HOURS)
            )
        ]
        if not due:
            continue
        for a in due:
            entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
            if not entry or not entry.slack_member_id:
                continue
            token = _issue_survey_token(survey.id, entry.id, entry.payroll_name)
            if _dm_survey_link(entry.slack_member_id, survey, token, is_nudge=True):
                a.nudge_count += 1
                a.last_nudge_at = now
                total_nudged += 1
    db.commit()
    return {"status": "checked", "nudged": total_nudged}


@router.post("/nudge-now")
def trigger_survey_nudges(db: Session = Depends(get_db)):
    """Manual trigger for testing — still respects SURVEY_NUDGE_ACTIVE."""
    return run_survey_nudges(db)
