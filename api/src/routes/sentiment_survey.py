"""
Sentiment Survey — optional driver check-in, added 2026-07-26 as a follow-up
step offered right after a driver submits their EOD survey (see eod_survey.py's
submit endpoint, which issues a sentiment_token alongside its normal response).

Deliberately "anonymous" from the driver's side: the submission form never
asks for or shows a name — identity rides along silently in the signed
token, same trust model as every other driver-facing tokenized page in this
app (link possession = identity). Reads of this data stay identity-blind
everywhere except the one explicit admin-report endpoint below, which is
gated to owner/hr only — not ops_manager — since these responses can include
claims about management and showing them to managers broadly would defeat
the point.

A daily job (gated by SENTIMENT_SURVEY_ACTIVE, default false) sends each
day's free-text responses to Claude for analysis, then posts a summary —
never including driver names — to owner/hr only. Follow-up on a specific
flagged item goes through the admin-report endpoint, not the daily summary.

HR can also trigger the survey directly (send_sentiment_survey(), added
2026-07-27) — to specific drivers, or everyone active/linked at once —
from the HR Home tab in Slack. See that function's docstring for why the
DM copy can never vary by who was targeted.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SentimentSurveyResponse, DriverRosterEntry,
    get_reminder_state, set_reminder_state,
)
from api.src.authorization import require_any_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentiment-survey", tags=["sentiment-survey"])

SENTIMENT_SURVEY_ACTIVE = os.getenv("SENTIMENT_SURVEY_ACTIVE", "false").lower() == "true"
_DAILY_REPORT_KEY = "sentiment_survey_daily_report"
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")
PACIFIC = ZoneInfo("America/Los_Angeles")


def _pacific_today() -> date:
    """Same Pacific-anchored-date fix as eod_survey.py's _pacific_today() —
    naive date.today() on Render's UTC server clock drifts a calendar day
    ahead of the real business day for roughly a third of every day."""
    return datetime.now(PACIFIC).date()


def _issue_sentiment_token(roster_id: int, driver_name: str, survey_date: date) -> str:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    payload = {
        "purpose": "sentiment_survey",
        "roster_id": roster_id,
        "driver_name": driver_name,
        "survey_date": survey_date.isoformat(),
        "exp": int(time.time()) + 30 * 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _verify_sentiment_token(token: str) -> Optional[dict]:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("purpose") != "sentiment_survey":
        return None
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Outbound send — added 2026-07-27. Until now the only way a driver ever saw
# this survey was as a soft follow-up offer right after submitting their EOD
# (eod_survey.py's submit endpoint). This adds a standalone send HR can
# trigger any time, for one/several specific drivers or everyone at once.
#
# The DM copy below must never give a driver reason to think they were
# personally singled out — that would undercut the "appears anonymous"
# promise this whole module is built around, even though the *submission*
# itself was always identity-blind regardless of who initiated the send.
# Explicit direction (2026-07-27): the message reads exactly the same
# whether HR sent it to one driver or all of them — generic, routine framing,
# no "you were selected" language, no reference to who requested it.
# ─────────────────────────────────────────────────────────────────────────────

def _dm(user_id: str, text: str, button_url: str, button_text: str = "Check In") -> None:
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        client.chat_postMessage(
            channel=user_id,
            text=text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": button_text, "emoji": True},
                        "style": "primary",
                        "url": button_url,
                        "action_id": "sentiment_survey_open",
                    }],
                },
            ],
        )
    except Exception as exc:
        logger.warning("Sentiment survey DM failed to %s: %s", user_id, exc)


def send_sentiment_survey(roster_ids: Optional[list[int]], send_to_all: bool, db: Session) -> dict:
    """Core send logic, shared by the REST endpoint and the Slack HR Home
    tab modal (which calls this directly, no HTTP round-trip). Skips anyone
    without a Slack link and anyone who already checked in today — same
    dedup as the daily follow-up offer, so a driver never gets asked twice
    in one day regardless of how many times/ways they were reached."""
    today = _pacific_today()

    if send_to_all:
        candidates = (
            db.query(DriverRosterEntry)
            .filter(DriverRosterEntry.is_active == True, DriverRosterEntry.slack_member_id.isnot(None))  # noqa: E712
            .all()
        )
    else:
        candidates = (
            db.query(DriverRosterEntry)
            .filter(DriverRosterEntry.id.in_(roster_ids or []))
            .all()
        )

    sent = already_submitted = no_slack = 0
    # Same generic wording every time — see module note above on why this
    # can never vary by who was selected or how many were sent to.
    msg = (
        "🗣️ Quick, anonymous daily check-in — how's everything going? "
        "Your response isn't tied to your name. Takes under a minute."
    )

    for entry in candidates:
        if not entry.slack_member_id:
            no_slack += 1
            continue
        existing = db.query(SentimentSurveyResponse).filter_by(roster_id=entry.id, survey_date=today).first()
        if existing:
            already_submitted += 1
            continue

        token = _issue_sentiment_token(entry.id, entry.payroll_name, today)
        url = f"{APP_URL}/sentiment-survey?token={token}"
        _dm(entry.slack_member_id, msg, url)
        sent += 1

    return {
        "sent": sent, "already_submitted": already_submitted, "no_slack_id": no_slack,
        "total_candidates": len(candidates),
    }


class SendSentimentSurveyRequest(BaseModel):
    roster_ids: Optional[list[int]] = None
    send_to_all: bool = False


@router.post("/send")
def send_sentiment_survey_endpoint(
    payload: SendSentimentSurveyRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    if not payload.send_to_all and not payload.roster_ids:
        raise HTTPException(status_code=400, detail="Provide roster_ids or set send_to_all=true")
    return send_sentiment_survey(payload.roster_ids, payload.send_to_all, db)


@router.get("/status-by-token")
def status_by_token(token: str, db: Session = Depends(get_db)):
    """Public — resolves a signed token to just enough to render the form
    (whether it's already been submitted for that date). Never returns the
    driver's name to the frontend — the page shouldn't display it."""
    claims = _verify_sentiment_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="This link has expired or is invalid.")
    roster_id = claims["roster_id"]
    survey_date = date.fromisoformat(claims["survey_date"])
    existing = db.query(SentimentSurveyResponse).filter_by(
        roster_id=roster_id, survey_date=survey_date
    ).first()
    return {"already_submitted": existing is not None}


class SentimentSubmitRequest(BaseModel):
    token: str
    feeling: Optional[str] = None
    van_equipment_issues: Optional[str] = None
    suggestions: Optional[str] = None
    treatment_concerns: Optional[str] = None


@router.post("/submit")
def submit_sentiment_survey(req: SentimentSubmitRequest, db: Session = Depends(get_db)):
    claims = _verify_sentiment_token(req.token)
    if not claims:
        raise HTTPException(status_code=401, detail="This link has expired or is invalid.")
    roster_id = claims["roster_id"]
    survey_date = date.fromisoformat(claims["survey_date"])

    existing = db.query(SentimentSurveyResponse).filter_by(
        roster_id=roster_id, survey_date=survey_date
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Already submitted for this date.")

    if not any([req.feeling, req.van_equipment_issues, req.suggestions, req.treatment_concerns]):
        raise HTTPException(status_code=400, detail="Nothing to submit — leave blank to skip instead.")

    db.add(SentimentSurveyResponse(
        survey_date=survey_date,
        roster_id=roster_id,
        feeling=(req.feeling or None),
        van_equipment_issues=(req.van_equipment_issues or None),
        suggestions=(req.suggestions or None),
        treatment_concerns=(req.treatment_concerns or None),
    ))
    db.commit()
    return {"status": "submitted"}


# ─────────────────────────────────────────────────────────────────────────────
# Admin report — the one place identity is deliberately revealed. owner/hr
# only, not ops_manager, since a response can include a claim about a manager.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin-report")
def admin_report(
    survey_date: Optional[str] = None,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    target = date.fromisoformat(survey_date) if survey_date else date.today()
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(SentimentSurveyResponse.survey_date == target)
        .order_by(SentimentSurveyResponse.submitted_at)
        .all()
    )
    roster_ids = {r.roster_id for r in rows if r.roster_id}
    roster_map = {
        r.id: r.payroll_name
        for r in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}

    return {
        "survey_date": target.isoformat(),
        "responses": [
            {
                "id": r.id,
                "driver_name": roster_map.get(r.roster_id, "Unknown"),
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "feeling": r.feeling,
                "van_equipment_issues": r.van_equipment_issues,
                "suggestions": r.suggestions,
                "treatment_concerns": r.treatment_concerns,
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily AI analysis + report — gated, never includes driver names.
# ─────────────────────────────────────────────────────────────────────────────

def _anthropic_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


_ANALYSIS_SYSTEM_PROMPT = """You are reviewing anonymous end-of-day sentiment survey responses from delivery
drivers at a small delivery services company. You will be given a list of raw free-text responses (feeling,
van/equipment issues, suggestions, treatment concerns) for one day. Identify and summarize, in plain
management-facing language:

1. URGENT — any signs of significant distress, anger, or language suggesting a driver may become violent or
   is a safety risk to themselves or others. Be sensitive to this — quote the relevant text.
2. RECURRING SUGGESTIONS — actionable ideas mentioned by more than one response, or a single strong one worth
   acting on.
3. VAN/EQUIPMENT ISSUES — any claims that vehicles or equipment are damaged, unsafe, or malfunctioning.
4. TREATMENT CONCERNS — any claims of mistreatment, unfair treatment, or concerns about how management has
   treated them.

Do NOT include any names, even if a response happens to mention one — refer to responses generically
("one driver said...", "another response noted..."). If a category has nothing to report, say so briefly.
Keep the whole summary concise — this is read daily, not an essay."""


def _run_ai_analysis(rows: list[SentimentSurveyResponse]) -> Optional[str]:
    client = _anthropic_client()
    if not client:
        logger.warning("Sentiment survey: ANTHROPIC_API_KEY not configured, skipping AI analysis.")
        return None

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"Response {i}:")
        if r.feeling:
            lines.append(f"  Feeling: {r.feeling}")
        if r.van_equipment_issues:
            lines.append(f"  Van/equipment issues: {r.van_equipment_issues}")
        if r.suggestions:
            lines.append(f"  Suggestions: {r.suggestions}")
        if r.treatment_concerns:
            lines.append(f"  Treatment concerns: {r.treatment_concerns}")
    raw_text = "\n".join(lines)

    try:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2048,
            system=_ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_text}],
        )
        return next((b.text for b in response.content if b.type == "text"), None)
    except Exception as exc:
        logger.warning("Sentiment survey AI analysis failed: %s", exc)
        return None


def send_daily_sentiment_report(db: Session, force: bool = False) -> dict:
    """Once per day: AI-summarize yesterday's responses (the day that just
    fully closed out) and DM the summary to owner/hr only. force=True bypasses
    the once-per-day guard for manual testing/recovery."""
    if not SENTIMENT_SURVEY_ACTIVE:
        return {"status": "inactive"}

    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    today = datetime.now(tz).date()

    if not force:
        state = get_reminder_state(db, _DAILY_REPORT_KEY)
        if state.get("last_sent_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if datetime.now(tz).hour < 21:
            return {"status": "outside_window"}

    from datetime import timedelta
    target = today - timedelta(days=1)
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(SentimentSurveyResponse.survey_date == target)
        .all()
    )
    if not rows:
        set_reminder_state(db, _DAILY_REPORT_KEY, {"last_sent_date": today.isoformat()})
        return {"status": "no_responses", "date": target.isoformat()}

    summary = _run_ai_analysis(rows)
    if not summary:
        return {"status": "analysis_failed", "date": target.isoformat()}

    from api.src.routes.document_routing import get_role_slack_ids
    recipient_ids = set(get_role_slack_ids(db, "owner")) | set(get_role_slack_ids(db, "hr"))
    if not recipient_ids:
        logger.info("Sentiment survey: no owner/hr Slack ID configured, skipping send.")
        return {"status": "no_recipient", "date": target.isoformat()}

    text = f"🗣️ *Sentiment Survey Summary — {target.strftime('%A, %B %-d')}* ({len(rows)} response(s))\n\n{summary}"
    token = os.getenv("SLACK_BOT_TOKEN")
    if token:
        try:
            from slack_sdk import WebClient
            client = WebClient(token=token)
            for sid in recipient_ids:
                client.chat_postMessage(channel=sid, text=text)
        except Exception as exc:
            logger.warning("Sentiment survey report send failed: %s", exc)
            return {"status": "error", "detail": str(exc)}

    for r in rows:
        r.reviewed_at = datetime.utcnow()
    set_reminder_state(db, _DAILY_REPORT_KEY, {"last_sent_date": today.isoformat()})
    db.commit()
    return {"status": "sent", "date": target.isoformat(), "responses": len(rows)}


@router.post("/trigger-daily-report")
def trigger_daily_report(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery."""
    return send_daily_sentiment_report(db, force=force)
