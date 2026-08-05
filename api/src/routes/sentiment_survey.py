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
from datetime import datetime, date, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SentimentSurveyResponse, DriverRosterEntry,
    get_reminder_state, set_reminder_state,
)
from api.src.authorization import require_any_role
from api.src.feature_flags import get_flag
from api.src.timezone import PACIFIC

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentiment-survey", tags=["sentiment-survey"])

_DAILY_REPORT_KEY = "sentiment_survey_daily_report"
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

# Weekly #nday-hr summary — added 2026-07-30 per explicit request ("we need
# to make sure these are being reviewed by mgt"). Separate from the daily
# DM report above: posts once a week, to the #nday-hr channel specifically
# (not owner/hr DMs), and includes the quantitative rating stats alongside
# the AI-flagged qualitative themes -- the daily report predates the 6
# rating questions and never mentions them.
_WEEKLY_SUMMARY_KEY_PREFIX = "sentiment_survey_weekly_summary_"
_WEEKLY_SUMMARY_SEND_WEEKDAY = 0   # Monday (Python weekday(): Monday=0)
_WEEKLY_SUMMARY_SEND_HOUR = 8      # 8 AM Pacific

# Morning shift-DM hints — added 2026-07-29, gated separately from the
# survey send itself since it touches rostering.py's DM, not this module's
# own send path.
_NUDGE_THRESHOLD_DAYS = 3  # shortened from 5 (2026-07-29) -- "I really want them to know we want their input"

# Monthly proactive push — added 2026-07-29. Sent ahead of Amazon's own
# survey window (first two weeks of the month) so drivers' sentiment is
# already positively primed by the time Amazon asks.
_MONTHLY_PUSH_KEY_PREFIX = "sentiment_survey_monthly_push_"


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
# The 6 real Amazon DSP sentiment-survey categories -- part of the original
# spec (see the DSP's own weekly Sentiment Report export), each a 1-5 rating
# (5 = most positive, matching Amazon's own scale) plus its own optional
# free-text elaboration. Added 2026-07-29 after these were found omitted
# from the first build. `key` names the pair of DB columns
# (rating_{key}/note_{key} on SentimentSurveyResponse) and doubles as the
# question_stats key in the monthly admin report.
# ─────────────────────────────────────────────────────────────────────────────

SENTIMENT_QUESTIONS = [
    {"key": "recognition", "text": "My DSP regularly recognizes the hard work I do to meet customer expectations."},
    {"key": "practical_solutions", "text": "My DSP provides practical solutions when I face challenges in my work."},
    {"key": "leadership_info", "text": "My DSP leadership provides useful information to help me succeed."},
    {"key": "clear_expectations", "text": "My DSP communicates clear expectations for my role."},
    {"key": "feel_valued", "text": "I feel valued as a member of my DSP team."},
    {"key": "easy_reach", "text": "I can easily reach out to my DSP when needed."},
]


@router.get("/questions")
def get_sentiment_questions():
    """Public — the driver-facing form and the admin report both read the
    question list from here so the two can never drift apart."""
    return {"questions": SENTIMENT_QUESTIONS}


def _compute_question_stats(rows: list) -> list[dict]:
    """Per-question response count / average (1-5) / % favorable (rated
    3-5, matching Amazon's own report shape) across a set of responses.
    Shared by the monthly admin report and the weekly #nday-hr summary
    so the two can never disagree on the math."""
    question_stats = []
    for q in SENTIMENT_QUESTIONS:
        ratings = [v for r in rows if (v := getattr(r, f"rating_{q['key']}")) is not None]
        favorable = [v for v in ratings if v >= 3]
        question_stats.append({
            "key": q["key"],
            "text": q["text"],
            "responses": len(ratings),
            "average": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "favorable_rate": round(100 * len(favorable) / len(ratings), 1) if ratings else None,
        })
    return question_stats


# ─────────────────────────────────────────────────────────────────────────────
# Morning shift-DM hints — added 2026-07-29, per explicit request: rather
# than waiting for the survey to reveal a problem, the daily DM proactively
# addresses each category's substance directly, ahead of Amazon's own
# survey window (their first two weeks of the month). One hint per
# SENTIMENT_QUESTIONS key, cycling by day-of-year so all 6 get even
# coverage across a month regardless of which day rostering.py calls in.
# ─────────────────────────────────────────────────────────────────────────────

SENTIMENT_HINTS = {
    "recognition": "💡 Crushed it out there? Tell your lead — we want to hear about it and recognize it.",
    "practical_solutions": "💡 Hit a snag today? Ping dispatch — we're here to help problem-solve in real time.",
    "leadership_info": "💡 Not sure about a policy or process? Ask your wave lead or dispatch — we'd rather you ask than guess.",
    "clear_expectations": "💡 Unclear on what's expected for a route or task? Reach out before you start and we'll clear it up.",
    "feel_valued": "💡 You're a valued part of this team — thank you for showing up and getting it done.",
    "easy_reach": "💡 Need us? Message Dispatch right from your Home tab — we're just a tap away.",
}
_HINT_KEYS_ORDER = [q["key"] for q in SENTIMENT_QUESTIONS]


def get_driver_dm_hint_block(roster_id: Optional[int], driver_name: str, today: date, db: Session) -> Optional[list]:
    """Public helper for rostering.py's morning shift DM — returns a list
    of blocks (a text section + a "Share Feedback" button) to splice into
    the DM, or None if gated off. Every variant always includes the
    button, not just the overdue case — added 2026-07-29 per explicit
    "I really want them to know we want their input" direction. Two text
    variants, gated by SENTIMENT_SURVEY_DM_HINTS_ACTIVE:
      - A driver who hasn't submitted a sentiment-survey response in
        _NUDGE_THRESHOLD_DAYS+ days (or ever) gets a personalized
        "haven't heard from you" nudge.
      - Everyone else gets a rotating category hint instead, cycling by
        day-of-year, each one directly addressing what that survey
        question asks about."""
    if not get_flag("SENTIMENT_SURVEY_DM_HINTS_ACTIVE") or not roster_id:
        return None

    token = _issue_sentiment_token(roster_id, driver_name, today)
    url = f"{APP_URL}/sentiment-survey?token={token}"
    feedback_button = {
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "📝 Share Feedback", "emoji": True},
            "url": url,
            "action_id": "sentiment_hint_share_feedback",
        }],
    }

    last = (
        db.query(func.max(SentimentSurveyResponse.survey_date))
        .filter(SentimentSurveyResponse.roster_id == roster_id)
        .scalar()
    )
    if not last or (today - last).days >= _NUDGE_THRESHOLD_DAYS:
        first_name = driver_name.split(",")[1].strip().split()[0] if "," in driver_name else driver_name.split()[0]
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"👋 *{first_name}, we haven't heard from you in a few days* — we really want your "
                        f"input on how things are going and what would make your days easier."
                    ),
                },
            },
            feedback_button,
        ]

    key = _HINT_KEYS_ORDER[today.timetuple().tm_yday % len(_HINT_KEYS_ORDER)]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": SENTIMENT_HINTS[key]}},
        feedback_button,
    ]


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


# ─────────────────────────────────────────────────────────────────────────────
# Monthly proactive push — added 2026-07-29. Amazon runs its own DSP
# sentiment survey in the first two weeks of the month; sending ours on
# the Sunday of the last full week of the *previous* month puts a fresh,
# positively-primed check-in in front of drivers right before Amazon asks,
# instead of relying on scattered organic responses.
# ─────────────────────────────────────────────────────────────────────────────

def _last_full_week_sunday(year: int, month: int) -> date:
    """The Sunday of the last Sunday-Saturday week fully contained within
    (year, month) -- e.g. if the month ends mid-week, that partial trailing
    week doesn't count; it backs up to the prior full week instead."""
    import calendar
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    # date.weekday(): Monday=0 ... Saturday=5, Sunday=6
    days_back_to_saturday = (last_day.weekday() - 5) % 7
    saturday = last_day - timedelta(days=days_back_to_saturday)
    sunday = saturday - timedelta(days=6)
    if sunday.month != month:
        # That week actually started in the previous month -- not "fully
        # contained" -- so use the full week before it instead.
        sunday -= timedelta(days=7)
    return sunday


def run_monthly_sentiment_survey_push(db: Session, force: bool = False) -> dict:
    """Once a month: auto-send the full survey to every active, linked
    driver on the Sunday of the last full week of the month. force=True
    bypasses the day-gate and already-sent guard for manual testing."""
    if not get_flag("SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE"):
        return {"status": "inactive", "note": "Set SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE=true on Render to enable"}

    today = _pacific_today()
    target_sunday = _last_full_week_sunday(today.year, today.month)

    if not force and today != target_sunday:
        return {"status": "not_send_day", "date": today.isoformat(), "target_date": target_sunday.isoformat()}

    state_key = f"{_MONTHLY_PUSH_KEY_PREFIX}{today.year}-{today.month:02d}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    result = send_sentiment_survey(None, True, db)
    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})

    try:
        from api.src.routes.document_routing import get_role_slack_ids
        hr_channel_ids = get_role_slack_ids(db, "hr")
        if hr_channel_ids:
            from slack_sdk import WebClient
            token = os.getenv("SLACK_BOT_TOKEN")
            if token:
                summary = (
                    f"🗣️ *Monthly sentiment survey push sent* — {result['sent']} sent"
                    f", {result['already_submitted']} already checked in today"
                    f", {result['no_slack_id']} skipped (no Slack link)"
                    f" (of {result['total_candidates']} considered)"
                )
                WebClient(token=token).chat_postMessage(channel=hr_channel_ids[0], text=summary)
    except Exception as exc:
        logger.warning("Monthly sentiment survey push audit log post failed: %s", exc)

    return {"status": "sent", "date": today.isoformat(), **result}


@router.post("/trigger-monthly-push")
def trigger_monthly_push(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return run_monthly_sentiment_survey_push(db, force=force)


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

    # One optional 1-5 rating + free-text note per SENTIMENT_QUESTIONS key —
    # added 2026-07-29, part of the original spec.
    rating_recognition: Optional[int] = None
    note_recognition: Optional[str] = None
    rating_practical_solutions: Optional[int] = None
    note_practical_solutions: Optional[str] = None
    rating_leadership_info: Optional[int] = None
    note_leadership_info: Optional[str] = None
    rating_clear_expectations: Optional[int] = None
    note_clear_expectations: Optional[str] = None
    rating_feel_valued: Optional[int] = None
    note_feel_valued: Optional[str] = None
    rating_easy_reach: Optional[int] = None
    note_easy_reach: Optional[str] = None


_RATING_FIELDS = [f"rating_{q['key']}" for q in SENTIMENT_QUESTIONS]
_NOTE_FIELDS = [f"note_{q['key']}" for q in SENTIMENT_QUESTIONS]

# A rating this low or lower requires its note field -- added 2026-08-04
# per explicit request: a bad rating with zero context ("ghost
# complaint") was invisible in the daily AI report, since _run_ai_analysis
# only ever included a question's rating in its input when a note was
# also present. Enforced here (not just the frontend) since this is the
# one place every submission path funnels through.
LOW_RATING_NOTE_THRESHOLD = 2


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

    for field in _RATING_FIELDS:
        value = getattr(req, field)
        if value is not None and not (1 <= value <= 5):
            raise HTTPException(status_code=400, detail=f"{field} must be 1-5.")

    for q in SENTIMENT_QUESTIONS:
        rating = getattr(req, f"rating_{q['key']}")
        note = getattr(req, f"note_{q['key']}")
        if rating is not None and rating <= LOW_RATING_NOTE_THRESHOLD and not (note or "").strip():
            raise HTTPException(status_code=400, detail=f"Please add a note explaining the low rating for: {q['text']}")

    if not any(
        [req.feeling, req.van_equipment_issues, req.suggestions, req.treatment_concerns]
        + [getattr(req, f) for f in _RATING_FIELDS]
        + [getattr(req, f) for f in _NOTE_FIELDS]
    ):
        raise HTTPException(status_code=400, detail="Nothing to submit — leave blank to skip instead.")

    db.add(SentimentSurveyResponse(
        survey_date=survey_date,
        roster_id=roster_id,
        feeling=(req.feeling or None),
        van_equipment_issues=(req.van_equipment_issues or None),
        suggestions=(req.suggestions or None),
        treatment_concerns=(req.treatment_concerns or None),
        **{f: getattr(req, f) for f in _RATING_FIELDS},
        **{f: getattr(req, f) for f in _NOTE_FIELDS},
    ))
    db.commit()
    return {"status": "submitted"}


# ─────────────────────────────────────────────────────────────────────────────
# Admin report — the one place identity is deliberately revealed. owner/hr
# only, not ops_manager, since a response can include a claim about a manager.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin-report")
def admin_report(
    month: Optional[str] = None,   # "YYYY-MM" — defaults to the current month
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    """Monthly, not daily (changed 2026-07-29) — it takes drivers several
    days to get around to answering, so a single-day view was usually
    near-empty. Aggregates the 6 rating questions (response count, average,
    % favorable i.e. rated 3-5, matching Amazon's own report shape) across
    the month, plus the full list of individual responses (identity
    revealed here only, as before)."""
    if month:
        year_i, month_i = (int(p) for p in month.split("-"))
    else:
        today = date.today()
        year_i, month_i = today.year, today.month
    range_start = date(year_i, month_i, 1)
    range_end = date(year_i + 1, 1, 1) if month_i == 12 else date(year_i, month_i + 1, 1)

    rows = (
        db.query(SentimentSurveyResponse)
        .filter(
            SentimentSurveyResponse.survey_date >= range_start,
            SentimentSurveyResponse.survey_date < range_end,
        )
        .order_by(SentimentSurveyResponse.submitted_at)
        .all()
    )
    roster_ids = {r.roster_id for r in rows if r.roster_id}
    roster_entries = (
        db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    ) if roster_ids else []
    roster_map = {r.id: r.payroll_name for r in roster_entries}
    roster_id_slack_map = {r.id: r.slack_member_id for r in roster_entries}

    question_stats = _compute_question_stats(rows)

    return {
        "month": f"{year_i:04d}-{month_i:02d}",
        "response_count": len(rows),
        "question_stats": question_stats,
        "responses": [
            {
                "id": r.id,
                "driver_name": roster_map.get(r.roster_id, "Unknown"),
                "survey_date": r.survey_date.isoformat(),
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "feeling": r.feeling,
                "van_equipment_issues": r.van_equipment_issues,
                "suggestions": r.suggestions,
                "treatment_concerns": r.treatment_concerns,
                "ratings": [
                    {
                        "key": q["key"],
                        "text": q["text"],
                        "rating": getattr(r, f"rating_{q['key']}"),
                        "note": getattr(r, f"note_{q['key']}"),
                    }
                    for q in SENTIMENT_QUESTIONS
                    if getattr(r, f"rating_{q['key']}") is not None or getattr(r, f"note_{q['key']}")
                ],
                "responded_at": r.responded_at.isoformat() if r.responded_at else None,
                "response_mode": r.response_mode,
                "response_text": r.response_text,
                "has_slack_link": bool(r.roster_id and roster_id_slack_map.get(r.roster_id)),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# "Respond as Blake" — added 2026-07-30, in memory of Chief Blake Cooke. A
# deliberate, human-supervised exception to this module's anonymity: HR
# can choose to reply directly to the specific driver about their
# specific suggestion (their identity was already visible to HR via the
# admin report above -- this just lets HR act on it for the ones worth a
# real answer). Three modes, matching Blake's own real signature phrases:
#   - "noted": bare acknowledgment, no elaboration -- for something that
#     doesn't warrant a real response.
#   - "noted_with_reason": "Noted. {reason}" -- for feedback that needs a
#     real answer but isn't a genuine constructive suggestion (e.g. a
#     blunt complaint), paired with a constructive redirect where possible.
#   - "decline_with_reason": "Thank you for the suggestion, I see where
#     you're coming from -- unfortunately we cannot do this, and here's
#     why: {reason}" -- for genuine suggestions being declined.
#   - "free_text" (added 2026-08-04): sends `reason` verbatim, no template
#     wrapper -- for the responses where none of Blake's three signature
#     phrasings actually fit what needs to be said.
# ─────────────────────────────────────────────────────────────────────────────

BLAKE_RESPONSE_TEMPLATES = {
    "noted": lambda reason: "Noted.",
    "noted_with_reason": lambda reason: f"Noted. {reason}",
    "decline_with_reason": lambda reason: (
        "Thank you for the suggestion, I see where you're coming from — "
        f"unfortunately we cannot do this, and here's why: {reason}"
    ),
    "free_text": lambda reason: reason,
}


class BlakeResponseRequest(BaseModel):
    mode: str   # "noted" | "noted_with_reason" | "decline_with_reason" | "free_text"
    reason: Optional[str] = None
    responded_by: Optional[str] = None


@router.post("/respond/{response_id}")
def respond_as_blake(
    response_id: int,
    payload: BlakeResponseRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    """Sends a direct DM to the specific driver who submitted this
    response, using Blake's voice. See module note above for why this is
    a deliberate, one-off exception to the anonymity design, not a
    general leak of who-said-what."""
    if payload.mode not in BLAKE_RESPONSE_TEMPLATES:
        raise HTTPException(status_code=400, detail="mode must be one of: " + ", ".join(BLAKE_RESPONSE_TEMPLATES))
    if payload.mode != "noted" and not (payload.reason or "").strip():
        raise HTTPException(status_code=400, detail="reason is required for this mode")

    response_row = db.query(SentimentSurveyResponse).filter(SentimentSurveyResponse.id == response_id).first()
    if not response_row:
        raise HTTPException(status_code=404, detail=f"Response {response_id} not found")
    if not response_row.roster_id:
        raise HTTPException(status_code=400, detail="This response has no linked driver to reply to")

    driver = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == response_row.roster_id).first()
    if not driver or not driver.slack_member_id:
        raise HTTPException(status_code=400, detail="Driver is not Slack-linked")

    text = BLAKE_RESPONSE_TEMPLATES[payload.mode]((payload.reason or "").strip())

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="SLACK_BOT_TOKEN is not configured")
    try:
        from slack_sdk import WebClient
        WebClient(token=token).chat_postMessage(channel=driver.slack_member_id, text=text)
    except Exception as exc:
        logger.warning("Blake response send failed for response %s: %s", response_id, exc)
        raise HTTPException(status_code=502, detail=f"Send failed: {exc}")

    response_row.responded_at = datetime.utcnow()
    response_row.responded_by = payload.responded_by or caller_role
    response_row.response_mode = payload.mode
    response_row.response_text = text
    db.commit()

    return {"status": "sent", "response_id": response_id, "text": text}


# ─────────────────────────────────────────────────────────────────────────────
# Help candidates — added 2026-08-05 per explicit request ("find the
# candidate drivers that would like to help fix some of the issues they
# are identifying... can Blake ask them?"). Deliberately two steps, same
# human-supervised shape as "Respond as Blake" above: this only reads
# responses and drafts a suggested ask -- nothing is sent to a driver
# until a human reviews the draft and sends it via the existing
# POST /respond/{response_id} (mode="free_text"). AI never volunteers a
# driver to anyone; it only helps HR find who might want to be asked, and
# drafts the actual asking -- the driver still has to say yes.
# ─────────────────────────────────────────────────────────────────────────────

_HELP_CANDIDATE_SYSTEM_PROMPT = """You are helping HR at a small delivery company find drivers worth asking
a specific question: "would you like to help us fix this?"

You'll get a numbered list of sentiment-survey responses (id + any suggestions, van/equipment issues,
treatment concerns, or low-rating notes they wrote). For EACH response, decide:

- Is there a specific, concrete, fixable issue here -- not a vague complaint, not just venting, not
  something outside the company's control -- that this driver seems genuinely invested in?
- If yes, draft a short message ASKING (never assuming) if they'd like to help work on it. Voice: direct,
  warm, no corporate language, in the style of a real person named Blake. Reference their specific issue in
  a sentence, then ask plainly whether they'd want to help. Never claim this is AI-generated. Never guarantee
  a fix will happen -- just ask if they want to be involved in figuring one out.

Respond with ONLY a JSON array (no markdown fences, no commentary), one object per response that qualifies
(skip ones that don't): [{"id": <int>, "issue_summary": "<one sentence, for the HR reviewer only, never
sent to the driver>", "draft_message": "<the actual message to send the driver>"}]

If nothing qualifies, respond with exactly: []"""


def find_help_candidates(db: Session, days: int = 30) -> list[dict]:
    """AI-assisted first pass at drivers worth ASKING if they'd like to
    help fix an issue they raised. Identity is resolved here (already
    owner/hr-gated below, same as admin_report) but nothing is sent --
    see module note above."""
    client = _anthropic_client()
    if not client:
        return []

    since = date.today() - timedelta(days=days)
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(
            SentimentSurveyResponse.survey_date >= since,
            SentimentSurveyResponse.responded_at.is_(None),
            SentimentSurveyResponse.roster_id.isnot(None),
        )
        .all()
    )
    actionable = [
        r for r in rows
        if r.suggestions or r.van_equipment_issues or r.treatment_concerns
        or any(getattr(r, f"note_{q['key']}") for q in SENTIMENT_QUESTIONS)
    ]
    if not actionable:
        return []

    lines = []
    for r in actionable:
        lines.append(f"Response id={r.id}:")
        if r.suggestions:
            lines.append(f"  Suggestion: {r.suggestions}")
        if r.van_equipment_issues:
            lines.append(f"  Van/equipment issue: {r.van_equipment_issues}")
        if r.treatment_concerns:
            lines.append(f"  Treatment concern: {r.treatment_concerns}")
        for q in SENTIMENT_QUESTIONS:
            note = getattr(r, f"note_{q['key']}")
            if note:
                lines.append(f"  [{q['text']}]: {note}")
    raw_text = "\n".join(lines)

    import json
    try:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=_HELP_CANDIDATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_text}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "[]").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
    except Exception as exc:
        logger.warning("Sentiment survey help-candidate analysis failed: %s", exc)
        return []

    by_id = {r.id: r for r in actionable}
    roster_ids = {r.roster_id for r in actionable}
    roster_map = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    }

    candidates = []
    for item in parsed:
        row = by_id.get(item.get("id"))
        if not row:
            continue
        entry = roster_map.get(row.roster_id)
        candidates.append({
            "response_id": row.id,
            "driver_name": entry.payroll_name if entry else "Unknown",
            "has_slack_link": bool(entry and entry.slack_member_id),
            "survey_date": row.survey_date.isoformat(),
            "issue_summary": item.get("issue_summary"),
            "draft_message": item.get("draft_message"),
        })
    return candidates


@router.get("/help-candidates")
def get_help_candidates(
    days: int = 30,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    """Owner/HR only. Nothing is sent by calling this -- it only surfaces
    candidates + a draft ask for a human to review and send via
    POST /respond/{response_id} (mode="free_text")."""
    return {"candidates": find_help_candidates(db, days=days)}


# ─────────────────────────────────────────────────────────────────────────────
# Testing-channel builder — added 2026-08-05 per explicit request: pull
# together drivers who rated "clear expectations" or "feel valued" low
# (all-time -- this survey only started 2026-07-26, so there's no long
# history to window), plus every #nday-mgt member and current wave leads,
# into one dedicated Slack channel for the RTS/feature testing rollout.
# Preview-then-create, same human-in-the-loop shape as the rest of this
# module -- the actual invite list is shown before anyone is added.
# ─────────────────────────────────────────────────────────────────────────────

TESTING_CHANNEL_NAME = "nday-rts-testing"
# Published artifact walkthrough for the RTS feature -- private until
# shared from its own page's share menu (Claude Artifacts default to
# private); the link only works for recipients once that's done.
RTS_HOWTO_URL = "https://claude.ai/code/artifact/fa4dd2cd-56be-46ac-bb2f-e7b20124de8c"


def _low_rating_drivers(db: Session) -> list[dict]:
    """One entry per driver who rated clear_expectations or feel_valued
    <= LOW_RATING_NOTE_THRESHOLD in any response, most recent qualifying
    response per driver."""
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(
            SentimentSurveyResponse.roster_id.isnot(None),
            (
                (SentimentSurveyResponse.rating_clear_expectations <= LOW_RATING_NOTE_THRESHOLD)
                | (SentimentSurveyResponse.rating_feel_valued <= LOW_RATING_NOTE_THRESHOLD)
            ),
        )
        .order_by(SentimentSurveyResponse.submitted_at.desc())
        .all()
    )
    by_roster: dict[int, SentimentSurveyResponse] = {}
    for r in rows:
        by_roster.setdefault(r.roster_id, r)   # first hit wins = most recent, given the ordering above

    if not by_roster:
        return []
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(list(by_roster.keys()))).all()
    }

    results = []
    for rid, r in by_roster.items():
        entry = entries.get(rid)
        if not entry:
            continue
        reasons = []
        if r.rating_clear_expectations is not None and r.rating_clear_expectations <= LOW_RATING_NOTE_THRESHOLD:
            reasons.append("low clear-expectations rating")
        if r.rating_feel_valued is not None and r.rating_feel_valued <= LOW_RATING_NOTE_THRESHOLD:
            reasons.append("low feel-valued rating")
        results.append({
            "roster_id": rid,
            "driver_name": entry.payroll_name,
            "slack_member_id": entry.slack_member_id,
            "has_slack_link": bool(entry.slack_member_id),
            "reasons": reasons,
        })
    results.sort(key=lambda d: d["driver_name"])
    return results


def _testing_channel_invite_preview(db: Session) -> dict:
    """Read-only -- computes exactly who would be invited, without
    creating anything or inviting anyone."""
    drivers = _low_rating_drivers(db)

    mgt_ids: list[str] = []
    token = os.getenv("SLACK_BOT_TOKEN")
    if token:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        try:
            bot_id = client.auth_test().get("user_id")
        except Exception:
            bot_id = None
        try:
            resp = client.conversations_members(channel=os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3"))
            mgt_ids = [m for m in resp.get("members", []) if m != bot_id]
        except Exception as exc:
            logger.warning("Testing-channel preview: #nday-mgt member lookup failed: %s", exc)

    from api.src.routes.wave_lead import get_senior_wave_lead, get_active_wave_leads, WAVE_5, HALVES
    wave_leads = []
    for half in HALVES:
        entry = get_senior_wave_lead(half, db)
        if entry:
            wave_leads.append({
                "driver_name": entry.payroll_name,
                "slack_member_id": entry.slack_member_id,
                "role": f"Senior Wave Lead ({half})",
            })
    for entry in get_active_wave_leads(WAVE_5, db):
        wave_leads.append({"driver_name": entry.payroll_name, "slack_member_id": entry.slack_member_id, "role": "Wave 5 Lead"})

    invite_ids = {d["slack_member_id"] for d in drivers if d["has_slack_link"]}
    invite_ids |= set(mgt_ids)
    invite_ids |= {w["slack_member_id"] for w in wave_leads if w["slack_member_id"]}

    return {
        "channel_name": TESTING_CHANNEL_NAME,
        "low_rating_drivers": drivers,
        "mgt_member_count": len(mgt_ids),
        "wave_leads": wave_leads,
        "total_invite_count": len(invite_ids),
    }


@router.get("/testing-channel-preview")
def testing_channel_preview(
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    """Owner/HR only. Read-only -- shows exactly who would be invited to
    the #nday-rts-testing channel before anything is created."""
    return _testing_channel_invite_preview(db)


@router.post("/testing-channel-create")
def testing_channel_create(
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr")),
):
    """Owner/HR only. Creates (or reuses, if it already exists) #nday-rts-testing
    and invites every driver flagged by _low_rating_drivers(), every
    #nday-mgt member, and current wave leads -- the exact same set
    testing_channel_preview() shows."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="SLACK_BOT_TOKEN is not configured")
    from slack_sdk import WebClient
    client = WebClient(token=token)

    preview = _testing_channel_invite_preview(db)

    try:
        resp = client.conversations_create(name=TESTING_CHANNEL_NAME, is_private=False)
        channel_id = resp["channel"]["id"]
    except Exception as exc:
        if "name_taken" in str(exc):
            channel_id = None
            cursor = None
            while True:
                listing = client.conversations_list(types="public_channel", limit=200, cursor=cursor)
                channel_id = next((c["id"] for c in listing["channels"] if c["name"] == TESTING_CHANNEL_NAME), None)
                if channel_id:
                    break
                cursor = listing.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break
            if not channel_id:
                raise HTTPException(status_code=502, detail=f"Channel exists but could not be found: {exc}")
        else:
            raise HTTPException(status_code=502, detail=f"Channel creation failed: {exc}")

    invite_ids: set[str] = set()
    for d in preview["low_rating_drivers"]:
        if d["has_slack_link"]:
            invite_ids.add(d["slack_member_id"])
    for w in preview["wave_leads"]:
        if w["slack_member_id"]:
            invite_ids.add(w["slack_member_id"])

    mgt_ids: list[str] = []
    try:
        bot_id = client.auth_test().get("user_id")
    except Exception:
        bot_id = None
    try:
        mresp = client.conversations_members(channel=os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3"))
        mgt_ids = [m for m in mresp.get("members", []) if m != bot_id]
    except Exception as exc:
        logger.warning("Testing-channel create: #nday-mgt member lookup failed: %s", exc)
    invite_ids |= set(mgt_ids)

    invited = 0
    invite_errors: list[str] = []
    for uid in invite_ids:
        try:
            client.conversations_invite(channel=channel_id, users=uid)
            invited += 1
        except Exception as exc:
            if "already_in_channel" not in str(exc):
                invite_errors.append(f"{uid}: {exc}")

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=(
                "👋 *Welcome to the team testing round.*\n\n"
                "We're rolling out some new dispatch/driver tools — starting with the RTS (Return to Station) "
                "debrief — and we want *your* eyes on it before it goes wide. You're in this channel because "
                "your input matters and we think you're exactly the kind of person who can help make this "
                "actually good, not just functional.\n\n"
                "Straight talk: it's not going to be perfect. That's *why* we need you — to poke at it, "
                "break it, tell us what's confusing or annoying before it's everyone's problem. No feedback is "
                "too small.\n\n"
                f"📖 How-to guide: {RTS_HOWTO_URL}\n"
                "👉 Try it, then just say what you found in here."
            ),
        )
    except Exception as exc:
        logger.warning("Testing-channel create: welcome message failed: %s", exc)

    return {
        "status": "created",
        "channel_name": TESTING_CHANNEL_NAME,
        "channel_id": channel_id,
        "invited": invited,
        "invite_errors": invite_errors,
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
5. UNEXPLAINED LOW RATINGS — some question ratings are marked "no explanation given" in the input. List
   which question(s) these are and how many responses, so management knows to follow up even though there's
   no free text to go on. Do not guess at a reason.

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
        for q in SENTIMENT_QUESTIONS:
            note = getattr(r, f"note_{q['key']}")
            rating = getattr(r, f"rating_{q['key']}")
            if note:
                lines.append(f"  [{q['text']}] (rated {rating}/5 if given): {note}")
            elif rating is not None and rating <= LOW_RATING_NOTE_THRESHOLD:
                # No note (pre-2026-08-04 submission, before notes were
                # required for a low rating) -- still surface the bare
                # rating so it isn't a silent "ghost complaint."
                lines.append(f"  [{q['text']}] rated {rating}/5, no explanation given.")
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
    if not get_flag("SENTIMENT_SURVEY_ACTIVE"):
        return {"status": "inactive"}

    today = datetime.now(PACIFIC).date()

    if not force:
        state = get_reminder_state(db, _DAILY_REPORT_KEY)
        if state.get("last_sent_date") == today.isoformat():
            return {"status": "already_sent", "date": today.isoformat()}
        if datetime.now(PACIFIC).hour < 21:
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

    # Raw counts of low ratings (<=LOW_RATING_NOTE_THRESHOLD), independent
    # of the AI call below -- so a bad rating is never silently lost even
    # if ANTHROPIC_API_KEY is missing or the API call fails. Notes are
    # required for new low ratings (see submit_sentiment_survey()), but
    # this also still catches any pre-2026-08-04 no-note ones.
    low_counts: dict[str, int] = {}
    for r in rows:
        for q in SENTIMENT_QUESTIONS:
            rating = getattr(r, f"rating_{q['key']}")
            if rating is not None and rating <= LOW_RATING_NOTE_THRESHOLD:
                low_counts[q["text"]] = low_counts.get(q["text"], 0) + 1
    low_rating_line = (
        "⚠️ *Low ratings (≤2/5):* " + "; ".join(f"{txt} ×{ct}" for txt, ct in low_counts.items())
        if low_counts else None
    )

    summary = _run_ai_analysis(rows)
    if not summary:
        if not low_rating_line:
            return {"status": "analysis_failed", "date": target.isoformat()}
        # AI summary failed, but there are low ratings to flag -- don't
        # silently drop them just because the narrative half failed.
        summary = "_AI analysis unavailable today — raw low-rating counts below._"

    from api.src.routes.document_routing import get_role_slack_ids
    recipient_ids = set(get_role_slack_ids(db, "owner")) | set(get_role_slack_ids(db, "hr"))
    if not recipient_ids:
        logger.info("Sentiment survey: no owner/hr Slack ID configured, skipping send.")
        return {"status": "no_recipient", "date": target.isoformat()}

    text = f"🗣️ *Sentiment Survey Summary — {target.strftime('%A, %B %-d')}* ({len(rows)} response(s))\n\n{summary}"
    if low_rating_line:
        text += f"\n\n{low_rating_line}"
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


def send_weekly_sentiment_summary(db: Session, force: bool = False) -> dict:
    """Once a week (Monday mornings): post a summary of the trailing 7
    days' responses to #nday-hr -- both the quantitative rating stats
    (average/% favorable per question) and an AI-flagged qualitative
    rollup, so management has a standing weekly checkpoint to actually
    review sentiment data rather than it only surfacing via the daily DM
    (which several people may not open) or the on-demand monthly admin
    report (which nobody's prompted to go check). force=True bypasses
    the day/hour gate and the already-sent-this-week guard for manual
    testing/recovery."""
    if not get_flag("SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE"):
        return {"status": "inactive", "note": "Set SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE=true on Render to enable"}

    now_pt = datetime.now(PACIFIC)
    today = now_pt.date()

    if not force and (now_pt.weekday() != _WEEKLY_SUMMARY_SEND_WEEKDAY or now_pt.hour != _WEEKLY_SUMMARY_SEND_HOUR):
        return {"status": "not_send_time", "date": today.isoformat()}

    # ISO week number keys the dedup guard, not the date -- avoids a
    # double-send if the loop ticks more than once inside the send hour.
    iso_year, iso_week, _ = today.isocalendar()
    state_key = f"{_WEEKLY_SUMMARY_KEY_PREFIX}{iso_year}-W{iso_week:02d}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "week": f"{iso_year}-W{iso_week:02d}"}

    range_end = today  # exclusive -- trailing 7 days ending yesterday
    range_start = range_end - timedelta(days=7)
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(SentimentSurveyResponse.survey_date >= range_start, SentimentSurveyResponse.survey_date < range_end)
        .all()
    )

    if not rows:
        set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
        return {"status": "no_responses", "range": f"{range_start.isoformat()} to {range_end.isoformat()}"}

    question_stats = _compute_question_stats(rows)
    stats_lines = []
    for q in question_stats:
        if q["responses"] == 0:
            continue
        stats_lines.append(f"• {q['text']} — avg *{q['average']}/5*, {q['favorable_rate']}% favorable ({q['responses']} responses)")
    stats_text = "\n".join(stats_lines) if stats_lines else "_No ratings submitted this week._"

    ai_summary = _run_ai_analysis(rows)

    text = (
        f"🗣️ *Weekly Sentiment Survey Summary — {range_start.strftime('%b %-d')} to "
        f"{(range_end - timedelta(days=1)).strftime('%b %-d')}* ({len(rows)} response(s))\n\n"
        f"📊 *Ratings*\n{stats_text}\n\n"
        f"🤖 *AI-Flagged Themes*\n{ai_summary or '_Analysis unavailable — see the full report._'}\n\n"
        f"👉 Full detail: {APP_URL}/sentiment-survey-admin"
    )

    from api.src.routes.document_routing import get_role_slack_ids
    hr_channel_ids = get_role_slack_ids(db, "hr")
    if not hr_channel_ids:
        logger.info("Sentiment survey: no hr channel configured, skipping weekly summary.")
        return {"status": "no_recipient"}

    token = os.getenv("SLACK_BOT_TOKEN")
    if token:
        try:
            from slack_sdk import WebClient
            WebClient(token=token).chat_postMessage(channel=hr_channel_ids[0], text=text)
        except Exception as exc:
            logger.warning("Weekly sentiment summary post failed: %s", exc)
            return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "range": f"{range_start.isoformat()} to {range_end.isoformat()}", "responses": len(rows)}


@router.post("/trigger-weekly-summary")
def trigger_weekly_summary(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return send_weekly_sentiment_summary(db, force=force)
