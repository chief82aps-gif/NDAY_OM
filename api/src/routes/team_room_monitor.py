"""
Team Room AI Monitor — added 2026-08-05. See Governance/TEAM_ROOM_AI_MONITOR.md
for the full design writeup; this module is the implementation.

Watches #nday-team-room for driver messages worth flagging: missing/broken
van equipment, injuries, incidents, dog bites, customer complaints. Uses
Claude to classify each message and draft a short reply, resolves which
van (VIN) is involved for equipment issues via that day's
DailyRouteAssignment (van_number is NOT a stable VIN alias -- the same
van_number can be a different physical vehicle day to day, so resolution
always goes through today's assignment row, never a fixed lookup table),
and looks up the most recent OTHER driver assigned that same VIN before
today so a follow-up ("did you notice this during your inspection?") can
be drafted too.

DRAFT-FIRST BY DESIGN (explicit instruction 2026-08-05: "draft first for
now, we can fully automate once we have a good feeling on how it is
working"): nothing ever posts back into #nday-team-room automatically.
Every detection posts a review card to #nday-mgt with Approve/Dismiss
buttons; only a human click actually sends the reply. Do not wire up
automatic posting without that instruction changing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, TeamRoomFlag, DailyRouteAssignment
from api.src.routes.rostering import TEAM_CHANNEL
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/team-room-monitor", tags=["team-room-monitor"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt

_CATEGORY_LABELS = {
    "equipment_issue": "🔧 Equipment Issue",
    "injury": "🩹 Injury Mention",
    "incident": "⚠️ Incident Mention",
    "dog_bite": "🐕 Dog Bite",
    "customer_complaint": "😠 Customer Complaint",
    "progress_dm_feedback": "💬 Progress DM Feedback",
}

# Categories that are pure logging -- no reply drafted, no #nday-mgt
# review card posted. Added 2026-08-05 for progress_dm_feedback: driver
# critique of the new automatic progress-check-in DMs should be
# cataloged, not responded to or reviewed one-by-one -- explicit
# direction: "we will not act on those, but we will catalog them...
# give them two to three days... I'll make the decisions."
_LOG_ONLY_CATEGORIES = {"progress_dm_feedback"}


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _anthropic_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


_CLASSIFY_SYSTEM_PROMPT = """You are monitoring a small delivery company's internal Slack "team room" \
channel where drivers casually chat during their shift. You'll be given ONE message from a driver. \
Decide if it reports one of these categories worth flagging to management:

- equipment_issue: missing/broken/malfunctioning van equipment (charger, phone mount, dolly, hand truck, etc.)
- injury: driver mentions being hurt, in pain, or an injury
- incident: a safety incident, near-miss, property damage, or unsafe situation (NOT a crash -- that has its own report)
- dog_bite: a dog bite or aggressive-dog encounter
- customer_complaint: a customer was rude, threatening, or caused a problem
- progress_dm_feedback: driver is complaining about, praising, questioning, or otherwise reacting to the new \
automatic "progress check-in" DMs they've started receiving during their shift (e.g. "why is the app texting \
me", "this is annoying", "stop messaging me", "I actually like this", "who keeps texting me updates")

If the message doesn't clearly fit one of these, respond with category "none" and everything else null.

If it fits "equipment_issue", also try to extract:
- any van/unit number mentioned in the message itself (digits only, e.g. "2107", "34")
- what specific equipment is being described (e.g. "charger", "phone mount", "dolly")

For any flagged category EXCEPT progress_dm_feedback, draft ONE short, warm, non-corporate reply Blake (a real \
dispatcher persona) could send back in the channel. For equipment_issue, ask a clarifying question -- if a van \
number was given, reference it back to them (e.g. "Hey Rogers, I see you're asking about van 2107 -- are you \
needing the charger cable itself, or the mount?"). If no van number was mentioned, ask for it. For injury/\
incident/dog_bite/customer_complaint, be warm and steer them toward filing the formal report so they're taken \
care of properly -- never diagnose, never promise an outcome. For progress_dm_feedback, draft_reply MUST be \
null -- this category is logged for later human review only, never auto-replied to.

Respond with ONLY JSON (no markdown fences, no commentary):
{"category": "equipment_issue|injury|incident|dog_bite|customer_complaint|progress_dm_feedback|none", "van_number": "<string or null>", "equipment": "<string or null>", "draft_reply": "<string or null -- null for progress_dm_feedback and none>"}"""


def classify_team_room_message(text: str, reporter_display_name: str) -> Optional[dict]:
    """Read-only Claude call. Returns None if AI isn't configured or the
    call fails -- caller just skips flagging in that case, never crashes
    the message-handling path."""
    client = _anthropic_client()
    if not client:
        return None
    try:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            system=_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Message from {reporter_display_name}: {text}"}],
        )
        raw = next((b.text for b in response.content if b.type == "text"), "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        if parsed.get("category") == "none":
            return None
        return parsed
    except Exception as exc:
        logger.warning("Team room message classification failed: %s", exc)
        return None


def _resolve_van_for_driver(driver_name: str, extracted_van_number: Optional[str], target_date: date, db: Session) -> tuple[Optional[str], Optional[str]]:
    """Returns (van_number, vin) -- prefers today's actual assignment for
    this driver (authoritative), falling back to whatever van number the
    message itself mentioned if no assignment is on file yet."""
    assignment = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target_date, DailyRouteAssignment.driver_name == driver_name)
        .first()
    )
    if assignment and assignment.van_number:
        return assignment.van_number, assignment.vin
    return extracted_van_number, None


def _find_prior_driver(vin: Optional[str], van_number: Optional[str], target_date: date, db: Session) -> Optional[str]:
    """Most recent OTHER driver assigned this same VIN before today. VIN is
    preferred (van_number alone is not a stable vehicle alias -- the same
    number can be a different physical van day to day); falls back to
    van_number only if no VIN was resolved."""
    q = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date < target_date)
    if vin:
        q = q.filter(DailyRouteAssignment.vin == vin)
    elif van_number:
        q = q.filter(DailyRouteAssignment.van_number == van_number)
    else:
        return None
    prior = q.order_by(DailyRouteAssignment.assignment_date.desc()).first()
    return prior.driver_name if prior else None


def _review_card_blocks(flag: TeamRoomFlag) -> list:
    label = _CATEGORY_LABELS.get(flag.category, flag.category)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}* — from *{flag.reporter_name}*\n> {flag.raw_text}"}},
    ]
    if flag.category == "equipment_issue" and (flag.van_number or flag.equipment_description):
        detail = f"Van: {flag.van_number or 'unknown'}" + (f" · Equipment: {flag.equipment_description}" if flag.equipment_description else "")
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": detail}]})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Draft reply:*\n{flag.draft_reply_text}"},
    })
    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button", "action_id": "team_room_flag_approve", "text": {"type": "plain_text", "text": "✅ Send Reply"}, "style": "primary", "value": str(flag.id)},
            {"type": "button", "action_id": "team_room_flag_dismiss", "text": {"type": "plain_text", "text": "Dismiss"}, "value": str(flag.id)},
        ],
    })
    if flag.prior_driver_name and flag.prior_driver_draft_text:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Also flag with {flag.prior_driver_name}* (had this van most recently before today):\n{flag.prior_driver_draft_text}"},
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {"type": "button", "action_id": "team_room_flag_prior_approve", "text": {"type": "plain_text", "text": "✅ Send to " + flag.prior_driver_name.split()[0]}, "value": str(flag.id)},
                {"type": "button", "action_id": "team_room_flag_prior_dismiss", "text": {"type": "plain_text", "text": "Dismiss"}, "value": str(flag.id)},
            ],
        })
    return blocks


def handle_team_room_message(event: dict, db: Session) -> Optional[dict]:
    """Called from slack_interactions.py's /events handler as a background
    task for every real (non-bot) message in #nday-team-room."""
    text = (event.get("text") or "").strip()
    slack_user_id = event.get("user", "")
    if not text or not slack_user_id:
        return None

    from api.src.routes.slack_interactions import _resolve_driver
    driver = _resolve_driver(slack_user_id, db)
    reporter_name = driver.payroll_name if driver else slack_user_id

    parsed = classify_team_room_message(text, reporter_name)
    if not parsed:
        return None

    today = datetime.now(PT).date()
    van_number, vin = (None, None)
    if driver:
        van_number, vin = _resolve_van_for_driver(driver.payroll_name, parsed.get("van_number"), today, db)
    else:
        van_number = parsed.get("van_number")

    prior_driver_name = None
    prior_draft = None
    if parsed["category"] == "equipment_issue" and (vin or van_number):
        prior_driver_name = _find_prior_driver(vin, van_number, today, db)
        if prior_driver_name and prior_driver_name != reporter_name:
            equip = parsed.get("equipment") or "an equipment issue"
            prior_draft = (
                f"Hey {prior_driver_name.split()[0]}, we had {equip} reported missing/broken on van "
                f"{van_number or 'this one'} today — did you happen to notice that during your last "
                f"inspection? Just trying to catch these earlier next time, no worries either way. Thanks!"
            )

    flag = TeamRoomFlag(
        message_ts=event.get("ts"),
        channel_id=event.get("channel"),
        reporter_slack_id=slack_user_id,
        reporter_name=reporter_name,
        roster_id=driver.id if driver else None,
        category=parsed["category"],
        raw_text=text,
        van_number=van_number,
        vin=vin,
        equipment_description=parsed.get("equipment"),
        draft_reply_text=parsed.get("draft_reply"),
        prior_driver_name=prior_driver_name,
        prior_driver_draft_text=prior_draft,
    )
    if flag.category in _LOG_ONLY_CATEGORIES:
        # No draft reply, no #nday-mgt review card -- catalog only. See
        # module note: "we will not act on those... catalog them."
        flag.reply_status = "logged"
    db.add(flag)
    db.commit()
    db.refresh(flag)

    if flag.category not in _LOG_ONLY_CATEGORIES:
        client = _client()
        if client:
            try:
                resp = client.chat_postMessage(channel=MGT_CHANNEL, blocks=_review_card_blocks(flag), text=f"Team room flag: {flag.category}")
                flag.review_message_ts = resp.get("ts")
                db.commit()
            except Exception as exc:
                logger.warning("Team room review-card post failed: %s", exc)

    return {"status": "flagged", "flag_id": flag.id, "category": flag.category}


def _handle_approve(flag_id: int, db: Session) -> None:
    flag = db.query(TeamRoomFlag).filter(TeamRoomFlag.id == flag_id).first()
    if not flag or flag.reply_status != "pending":
        return
    client = _client()
    if client and flag.draft_reply_text:
        try:
            client.chat_postMessage(channel=flag.channel_id, text=flag.draft_reply_text, thread_ts=flag.message_ts)
            flag.reply_status = "approved"
            flag.reply_sent_at = datetime.utcnow()
        except Exception as exc:
            logger.warning("Team room approved reply send failed: %s", exc)
    db.commit()


def _handle_dismiss(flag_id: int, db: Session) -> None:
    flag = db.query(TeamRoomFlag).filter(TeamRoomFlag.id == flag_id).first()
    if flag and flag.reply_status == "pending":
        flag.reply_status = "dismissed"
        db.commit()


def _handle_prior_approve(flag_id: int, db: Session) -> None:
    flag = db.query(TeamRoomFlag).filter(TeamRoomFlag.id == flag_id).first()
    if not flag or flag.prior_driver_reply_status != "pending":
        return
    client = _client()
    if client and flag.prior_driver_draft_text:
        try:
            client.chat_postMessage(channel=flag.channel_id, text=flag.prior_driver_draft_text)
            flag.prior_driver_reply_status = "approved"
            flag.prior_driver_reply_sent_at = datetime.utcnow()
        except Exception as exc:
            logger.warning("Team room prior-driver reply send failed: %s", exc)
    db.commit()


def _handle_prior_dismiss(flag_id: int, db: Session) -> None:
    flag = db.query(TeamRoomFlag).filter(TeamRoomFlag.id == flag_id).first()
    if flag and flag.prior_driver_reply_status == "pending":
        flag.prior_driver_reply_status = "dismissed"
        db.commit()


def chat_flagged_equipment_lines(db: Session, target_date: date) -> list[str]:
    """For ops_daily_digest.py's Fleet section."""
    rows = (
        db.query(TeamRoomFlag)
        .filter(TeamRoomFlag.category == "equipment_issue", TeamRoomFlag.detected_at >= datetime.combine(target_date, datetime.min.time()))
        .all()
    )
    return [
        f"• *{r.reporter_name}* (Van {r.van_number or 'unknown'}): {r.equipment_description or 'equipment issue'} — flagged in team room"
        for r in rows
    ]


def chat_flagged_hr_lines(db: Session, target_date: date) -> list[str]:
    """For ops_daily_digest.py's HR section."""
    rows = (
        db.query(TeamRoomFlag)
        .filter(TeamRoomFlag.category.in_(["injury", "incident", "dog_bite", "customer_complaint"]),
                TeamRoomFlag.detected_at >= datetime.combine(target_date, datetime.min.time()))
        .all()
    )
    return [f"• {_CATEGORY_LABELS.get(r.category, r.category)} — {r.reporter_name}: {r.raw_text[:120]}" for r in rows]


@router.get("/pending")
def list_pending(db: Session = Depends(get_db)):
    rows = db.query(TeamRoomFlag).filter(TeamRoomFlag.reply_status == "pending").order_by(TeamRoomFlag.detected_at.desc()).all()
    return {"flags": [{"id": r.id, "category": r.category, "reporter_name": r.reporter_name, "raw_text": r.raw_text, "draft_reply_text": r.draft_reply_text} for r in rows]}


@router.get("/progress-dm-feedback")
def list_progress_dm_feedback(db: Session = Depends(get_db)):
    """The catalog of driver critique/reaction to the new progress-DM
    feature, for manual review after the "two to three days of
    complaining" window -- never auto-replied to, never posted as a
    review card, per explicit direction."""
    rows = (
        db.query(TeamRoomFlag)
        .filter(TeamRoomFlag.category == "progress_dm_feedback")
        .order_by(TeamRoomFlag.detected_at.desc())
        .all()
    )
    return {"count": len(rows), "feedback": [{"id": r.id, "detected_at": r.detected_at.isoformat() if r.detected_at else None, "reporter_name": r.reporter_name, "raw_text": r.raw_text} for r in rows]}
