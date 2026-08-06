"""
Owner Meeting — "office hours" for drivers who rated low on the sentiment
survey, added 2026-08-05 per explicit request: give drivers who've flagged
problems on the (anonymous) sentiment survey a real chance to sit down with
just the owners and directly affect how the company is run.

Deliberately NOT automatic end-to-end. Per explicit direction, eligibility
is surfaced (candidates() below ranks drivers by their own lowest average
sentiment rating) but the actual invite list is a MANUAL owner decision
every single cycle — this module never auto-selects who gets invited.

One active OwnerMeeting row at a time (draft -> confirmed -> sent ->
completed, or cancelled any time before sent). The owner is reminded every
Monday and Wednesday night (see run_owner_meeting_reminder) to confirm the
scheduled meeting is still on, via Slack Confirm/Cancel buttons -- a
recurring check-in, not a one-time gate, since the meeting can be pushed or
called off at any point before invites actually go out.

The broadcast invite (send_meeting_invites) is a deliberate, narrow
exception to SentimentSurveyResponse's anonymity (see that model's own
docstring) -- the owner sees identity to build the candidate list, but the
invite text itself never tells a driver *why* they were invited, and
explicitly reassures them attending isn't tied back to their specific
answers.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, OwnerMeeting, OwnerMeetingRSVP, SentimentSurveyResponse, DriverRosterEntry,
    get_reminder_state, set_reminder_state,
)
from api.src.routes.sentiment_survey import SENTIMENT_QUESTIONS
from api.src.authorization import require_any_role
from api.src.feature_flags import get_flag
from api.src.timezone import PACIFIC

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/owner-meeting", tags=["owner-meeting"])

_OWNER_SLACK_ID = os.getenv("DOC_ROUTING_OWNER_SLACK_ID")
_CANDIDATE_LOOKBACK_DAYS = 90
_ACTIVE_STATUSES = ("draft", "confirmed")

_REMINDER_KEY_PREFIX = "owner_meeting_reminder_"
_REMINDER_WEEKDAYS = (0, 2)   # Monday, Wednesday (date.weekday(): Monday=0)
_REMINDER_HOUR = 20           # 8 PM Pacific


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _pacific_today() -> date:
    return datetime.now(PACIFIC).date()


def _get_active_meeting(db: Session) -> Optional[OwnerMeeting]:
    return (
        db.query(OwnerMeeting)
        .filter(OwnerMeeting.status.in_(_ACTIVE_STATUSES))
        .order_by(OwnerMeeting.created_at.desc())
        .first()
    )


def _rating_keys() -> list[str]:
    return [f"rating_{q['key']}" for q in SENTIMENT_QUESTIONS]


def get_candidates(db: Session, limit: int = 25) -> list[dict]:
    """Drivers ranked by their own most recent sentiment-survey average
    rating, lowest first -- surfaced for the owner to manually pick from,
    never auto-invited. Only considers each driver's single most recent
    response within the lookback window, so someone who rated low months
    ago but has since responded more positively doesn't stay flagged."""
    since = _pacific_today() - timedelta(days=_CANDIDATE_LOOKBACK_DAYS)
    rows = (
        db.query(SentimentSurveyResponse)
        .filter(SentimentSurveyResponse.survey_date >= since, SentimentSurveyResponse.roster_id.isnot(None))
        .order_by(SentimentSurveyResponse.roster_id, SentimentSurveyResponse.submitted_at.desc())
        .all()
    )

    latest_by_roster: dict[int, SentimentSurveyResponse] = {}
    for r in rows:
        if r.roster_id not in latest_by_roster:
            latest_by_roster[r.roster_id] = r  # first hit per roster_id is the most recent, given the ordering above

    roster_ids = list(latest_by_roster.keys())
    roster_map = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}

    keys = _rating_keys()
    candidates = []
    for roster_id, r in latest_by_roster.items():
        ratings = [v for v in (getattr(r, k) for k in keys) if v is not None]
        if not ratings:
            continue
        entry = roster_map.get(roster_id)
        candidates.append({
            "roster_id": roster_id,
            "driver_name": entry.payroll_name if entry else "Unknown",
            "has_slack_link": bool(entry and entry.slack_member_id),
            "average_rating": round(sum(ratings) / len(ratings), 2),
            "rating_count": len(ratings),
            "last_survey_date": r.survey_date.isoformat() if r.survey_date else None,
        })

    candidates.sort(key=lambda c: c["average_rating"])
    return candidates[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleMeetingRequest(BaseModel):
    meeting_date: str   # YYYY-MM-DD
    meeting_time: str
    location: str


@router.post("/schedule")
def schedule_meeting(
    payload: ScheduleMeetingRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner")),
):
    existing = _get_active_meeting(db)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A meeting is already {existing.status} for {existing.meeting_date.isoformat()} — cancel it first.",
        )
    try:
        meeting_date = date.fromisoformat(payload.meeting_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="meeting_date must be YYYY-MM-DD")

    meeting = OwnerMeeting(
        status="draft",
        meeting_date=meeting_date,
        meeting_time=payload.meeting_time,
        location=payload.location,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return {"status": "scheduled", "id": meeting.id}


@router.get("/current")
def current_meeting(db: Session = Depends(get_db), caller_role: str = Depends(require_any_role("owner"))):
    meeting = (
        db.query(OwnerMeeting).order_by(OwnerMeeting.created_at.desc()).first()
    )
    if not meeting:
        return {"status": "none"}
    rsvps = db.query(OwnerMeetingRSVP).filter(OwnerMeetingRSVP.meeting_id == meeting.id).all()
    return {
        "id": meeting.id,
        "status": meeting.status,
        "meeting_date": meeting.meeting_date.isoformat(),
        "meeting_time": meeting.meeting_time,
        "location": meeting.location,
        "confirmed_at": meeting.confirmed_at.isoformat() if meeting.confirmed_at else None,
        "invited_at": meeting.invited_at.isoformat() if meeting.invited_at else None,
        "invited_count": len(meeting.invited_roster_ids or []),
        "rsvp_yes": sum(1 for r in rsvps if r.response == "yes"),
        "rsvp_no": sum(1 for r in rsvps if r.response == "no"),
    }


@router.post("/cancel")
def cancel_meeting(db: Session = Depends(get_db), caller_role: str = Depends(require_any_role("owner"))):
    meeting = _get_active_meeting(db)
    if not meeting:
        return {"status": "no_active_meeting"}
    meeting.status = "cancelled"
    meeting.cancelled_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled", "id": meeting.id}


@router.get("/candidates")
def candidates_endpoint(
    limit: int = 25,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner")),
):
    return {"candidates": get_candidates(db, limit=limit)}


# ─────────────────────────────────────────────────────────────────────────────
# Invite broadcast
# ─────────────────────────────────────────────────────────────────────────────

_ANONYMITY_LINE = (
    "This invite isn't tied to or shared as being about any specific survey answer — "
    "nobody else will know why you were asked."
)


def _build_invite_blocks(meeting: OwnerMeeting, meeting_id: int) -> list:
    date_str = meeting.meeting_date.strftime("%A, %B %-d")
    text = (
        f"🍞 *You're invited to sit down with the owners*\n\n"
        f"*When:* {date_str} at {meeting.meeting_time}\n"
        f"*Where:* {meeting.location}\n\n"
        f"This is a real, direct chance to tell us what's working and what isn't — "
        f"no agenda beyond hearing from you.\n\n"
        f"_{_ANONYMITY_LINE}_"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ I'll Be There", "emoji": True},
                    "style": "primary",
                    "action_id": "owner_meeting_rsvp_yes",
                    "value": json.dumps({"meeting_id": meeting_id}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Can't Make It", "emoji": True},
                    "action_id": "owner_meeting_rsvp_no",
                    "value": json.dumps({"meeting_id": meeting_id}),
                },
            ],
        },
    ]


class InviteRequest(BaseModel):
    roster_ids: list[int]


@router.post("/invite")
def invite_drivers(
    payload: InviteRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner")),
):
    meeting = _get_active_meeting(db)
    if not meeting:
        raise HTTPException(status_code=400, detail="No active (draft/confirmed) meeting to invite drivers to.")
    if not payload.roster_ids:
        raise HTTPException(status_code=400, detail="roster_ids is required and cannot be empty.")

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    entries = db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(payload.roster_ids)).all()
    blocks = _build_invite_blocks(meeting, meeting.id)

    sent = no_slack = 0
    for entry in entries:
        if not entry.slack_member_id:
            no_slack += 1
            continue
        try:
            client.chat_postMessage(channel=entry.slack_member_id, text="You're invited to sit down with the owners.", blocks=blocks)
            sent += 1
        except Exception as exc:
            logger.warning("Owner meeting invite DM failed for roster_id=%s: %s", entry.id, exc)

    meeting.status = "sent"
    meeting.invited_at = datetime.utcnow()
    meeting.invited_roster_ids = payload.roster_ids
    db.commit()

    return {"status": "sent", "invited": sent, "no_slack_id": no_slack, "total": len(entries)}


# ─────────────────────────────────────────────────────────────────────────────
# Slack button handlers — called from slack_interactions.py
# ─────────────────────────────────────────────────────────────────────────────

def handle_owner_meeting_confirm(payload: dict, db: Session) -> None:
    """Owner tapped 'Confirm' on the Mon/Wed reminder DM."""
    try:
        meeting = _get_active_meeting(db)
        if meeting and meeting.status == "draft":
            meeting.status = "confirmed"
            meeting.confirmed_at = datetime.utcnow()
            db.commit()
        _update_reminder_message(payload, "✅ Confirmed — still on. You'll be reminded again Monday/Wednesday until invites go out.")
    except Exception as exc:
        logger.warning("owner_meeting_confirm handler error: %s", exc)


def handle_owner_meeting_cancel(payload: dict, db: Session) -> None:
    """Owner tapped 'Cancel' on the Mon/Wed reminder DM."""
    try:
        meeting = _get_active_meeting(db)
        if meeting:
            meeting.status = "cancelled"
            meeting.cancelled_at = datetime.utcnow()
            db.commit()
        _update_reminder_message(payload, "❌ Cancelled — schedule a new one anytime via the owner meeting tool.")
    except Exception as exc:
        logger.warning("owner_meeting_cancel handler error: %s", exc)


def _update_reminder_message(payload: dict, text: str) -> None:
    channel_id = payload.get("channel", {}).get("id", "")
    msg_ts = payload.get("message", {}).get("ts", "")
    client = _client()
    if client and channel_id and msg_ts:
        try:
            client.chat_update(channel=channel_id, ts=msg_ts, text=text, blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}])
        except Exception as exc:
            logger.warning("owner_meeting reminder message update failed: %s", exc)


def handle_owner_meeting_rsvp(payload: dict, db: Session, response: str) -> None:
    """Driver tapped 'I'll Be There' / 'Can't Make It' on their invite DM."""
    try:
        action = (payload.get("actions") or [{}])[0]
        value = json.loads(action.get("value", "{}"))
        meeting_id = value.get("meeting_id")
        slack_user_id = payload.get("user", {}).get("id", "")

        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.slack_member_id == slack_user_id).first()
        if not entry or not meeting_id:
            return

        existing = db.query(OwnerMeetingRSVP).filter_by(meeting_id=meeting_id, roster_id=entry.id).first()
        if existing:
            existing.response = response
            existing.responded_at = datetime.utcnow()
        else:
            db.add(OwnerMeetingRSVP(meeting_id=meeting_id, roster_id=entry.id, response=response))
        db.commit()

        confirm_text = "✅ Thanks — you're on the list!" if response == "yes" else "Got it — thanks for letting us know."
        _update_reminder_message(payload, confirm_text)
    except Exception as exc:
        logger.warning("owner_meeting_rsvp handler error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Monday/Wednesday confirm reminder — called every 60s from main.py's
# background loop, same pattern as every other evening reminder in this app.
# ─────────────────────────────────────────────────────────────────────────────

def run_owner_meeting_reminder(db: Session, force: bool = False) -> dict:
    if not get_flag("OWNER_MEETING_ACTIVE"):
        return {"status": "inactive"}

    now = datetime.now(PACIFIC)
    today = now.date()

    meeting = _get_active_meeting(db)
    if not meeting or meeting.meeting_date < today:
        return {"status": "no_active_meeting"}

    if not force:
        if today.weekday() not in _REMINDER_WEEKDAYS or now.hour < _REMINDER_HOUR:
            return {"status": "not_due"}

    state_key = f"{_REMINDER_KEY_PREFIX}{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent_today"}

    if not _OWNER_SLACK_ID:
        return {"status": "no_owner_slack_id"}
    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    date_str = meeting.meeting_date.strftime("%A, %B %-d")
    text = (
        f"🍞 *Owner meeting check-in* — is the office-hours meeting on "
        f"*{date_str}* at *{meeting.meeting_time}* ({meeting.location}) still happening?"
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Confirm", "emoji": True}, "style": "primary", "action_id": "owner_meeting_confirm", "value": str(meeting.id)},
                {"type": "button", "text": {"type": "plain_text", "text": "❌ Cancel", "emoji": True}, "style": "danger", "action_id": "owner_meeting_cancel", "value": str(meeting.id)},
            ],
        },
    ]
    try:
        client.chat_postMessage(channel=_OWNER_SLACK_ID, text=text, blocks=blocks)
    except Exception as exc:
        logger.warning("Owner meeting reminder DM failed: %s", exc)
        return {"status": "send_failed"}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "meeting_id": meeting.id}


@router.post("/reminder/trigger")
def trigger_reminder(db: Session = Depends(get_db), caller_role: str = Depends(require_any_role("owner"))):
    """Manual/forced trigger for testing — bypasses the day/hour gate and already-sent guard."""
    return run_owner_meeting_reminder(db, force=True)
