"""
Safety Events — Netradyne driving-safety data (speeding, roadside
parking, etc.), ingested from the "Safety Dashboard" CSV export dropped
in #nday-operations-management. New module, added 2026-07-14.

Owns the `safety_events` table exclusively — other modules (e.g.
rostering.py's driver summary matrix) should call the query helpers
here, never query SafetyEvent directly, per the hub-and-spoke rule in
CLAUDE.md.

Append-only, deduped by Netradyne's own event_id (the export is a rolling
window, so the same event can reappear across multiple uploads).
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import get_db, SessionLocal, SafetyEvent, get_reminder_state, set_reminder_state
from api.src.ingest.safety_events import parse_safety_events
from api.src.driver_identity import resolve_roster_entry
from api.src.routes.document_routing import is_dispatch_staff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/safety-events", tags=["safety-events"])

NDAY_MGT_CHANNEL = os.getenv("NDAY_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt

# Safety Violation Review/Dispute workflow — added 2026-07-23
# (BUILD_QUEUE.md #6). Hard off-switch, same pattern as DRIVER_DM_ACTIVE/
# DVIC_TRAINING_VIDEO_ACTIVE: fully built, zero effect until turned on.
SAFETY_VIOLATION_REVIEW_ACTIVE = os.getenv("SAFETY_VIOLATION_REVIEW_ACTIVE", "false").lower() == "true"
# Stub for a future forced training video on confirmed violations — no
# video exists yet, column/gate shape mirrors DVIC's but isn't wired to
# anything beyond existing. Do not flip on without a real video.
SAFETY_VIOLATION_VIDEO_ACTIVE = os.getenv("SAFETY_VIOLATION_VIDEO_ACTIVE", "false").lower() == "true"


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _dm_with_blocks(slack_member_id: str, fallback_text: str, blocks: list):
    """Send a Block Kit DM. Returns (ok, channel_id, message_ts) for a
    later chat_update — same shape as dvic.py's _dm_with_blocks()."""
    c = _client()
    if not c:
        return False, None, None
    try:
        resp = c.chat_postMessage(channel=slack_member_id, text=fallback_text, blocks=blocks)
        return True, resp.get("channel"), resp.get("ts")
    except Exception as exc:
        logger.warning("Safety violation DM failed (%s): %s", slack_member_id, exc)
        return False, None, None


def _store_safety_events(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Called from ops_ingest.py's dispatcher. Append-only, deduped by event_id."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        records, errors = parse_safety_events(tmp_path)
        if errors:
            logger.warning("Safety events: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
        if not records:
            return {"status": "error", "message": "; ".join(errors) if errors else "No safety events parsed from file."}

        existing_ids = {
            r[0] for r in db.query(SafetyEvent.event_id)
            .filter(SafetyEvent.event_id.in_([rec.event_id for rec in records]))
            .all()
        }

        created = 0
        for rec in records:
            if rec.event_id in existing_ids:
                continue
            db.add(SafetyEvent(
                event_id=rec.event_id,
                report_date=rec.report_date or date.today(),
                driver_name=rec.driver_name,
                transporter_id=rec.transporter_id,
                event_at=rec.event_at,
                vin=rec.vin,
                program_impact=rec.program_impact,
                metric_type=rec.metric_type,
                metric_subtype=rec.metric_subtype,
                source=rec.source,
                video_link=rec.video_link,
                review_details=rec.review_details,
                source_file=filename,
            ))
            created += 1
        db.commit()

        return {"status": "ingested", "records": len(records), "created": created, "duplicates_skipped": len(records) - created}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Review/dispute workflow — added 2026-07-23. See CLAUDE.md /
# BUILD_QUEUE.md #6. Gated by SAFETY_VIOLATION_REVIEW_ACTIVE.
# ─────────────────────────────────────────────────────────────────────────────

def post_pending_safety_violations(db: Session) -> dict:
    """Post a Confirm/False-Flag review message to #nday-mgt for every
    SafetyEvent not yet posted. Deliberately NOT scoped to "just ingested
    this call" — the query matches any already-ingested, not-yet-posted
    row, so this also picks up historical rows (e.g. this morning's
    violations) for on-demand testing/backfill, not just future ingests."""
    if not SAFETY_VIOLATION_REVIEW_ACTIVE:
        return {"status": "inactive", "note": "Set SAFETY_VIOLATION_REVIEW_ACTIVE=true on Render to enable"}

    events = (
        db.query(SafetyEvent)
        .filter(
            SafetyEvent.review_slack_ts.is_(None),
            (SafetyEvent.review_status.is_(None)) | (SafetyEvent.review_status == "pending"),
        )
        .order_by(SafetyEvent.event_at)
        .all()
    )
    if not events:
        return {"status": "no_pending", "posted": 0}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    posted = 0
    for event in events:
        date_str = event.event_at.strftime("%A, %B %-d, %-I:%M %p") if event.event_at else (event.report_date.isoformat() if event.report_date else "unknown date")
        lines = [
            f"*Driver:* {event.driver_name or 'Unknown'}",
            f"*Violation:* {event.metric_type or 'Unknown'}" + (f" — {event.metric_subtype}" if event.metric_subtype else ""),
            f"*When:* {date_str}",
        ]
        if event.video_link:
            lines.append(f"<{event.video_link}|View Video>")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "🚨 Safety Violation — Review Needed", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "safety_event_confirm",
                        "text": {"type": "plain_text", "text": "✅ Confirm Violation", "emoji": True},
                        "style": "primary",
                        "value": str(event.id),
                    },
                    {
                        "type": "button",
                        "action_id": "safety_event_false_flag",
                        "text": {"type": "plain_text", "text": "❌ False Flag", "emoji": True},
                        "style": "danger",
                        "value": str(event.id),
                    },
                ],
            },
        ]
        try:
            resp = client.chat_postMessage(
                channel=NDAY_MGT_CHANNEL,
                text=f"Safety Violation — {event.driver_name} — {event.metric_type}",
                blocks=blocks,
            )
            event.review_slack_channel = resp.get("channel")
            event.review_slack_ts = resp.get("ts")
            event.review_status = event.review_status or "pending"
            posted += 1
        except Exception as exc:
            logger.warning("Safety violation review post failed for event %s: %s", event.event_id, exc)
    db.commit()
    return {"status": "ok", "posted": posted, "total_pending": len(events)}


def _build_safety_violation_dm(event: SafetyEvent) -> tuple[str, list]:
    """Write-up DM sent to the driver once dispatch confirms a violation.
    No video button yet (SAFETY_VIOLATION_VIDEO_ACTIVE is off) — mirrors
    dvic.py's _dm_blocks() shape so a future video gate slots in the same
    way DVIC's did."""
    first = (event.driver_name or "Driver").split()[0]
    detail = event.metric_type or "a safety violation"
    if event.metric_subtype:
        detail += f" ({event.metric_subtype})"
    text = (
        f":rotating_light: Hi {first}, dispatch has confirmed a safety event on your record: "
        f"*{detail}*, logged {event.event_at.strftime('%A, %B %-d') if event.event_at else 'recently'}.\n\n"
        "This is being recorded as a write-up. Please drive safely and be mindful of this going forward — "
        "these events matter for your safety and everyone else's on the road.\n\n"
        "Please tap *Acknowledge* below to confirm you've seen this."
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Acknowledge", "emoji": True},
                "style": "primary",
                "action_id": "safety_violation_ack",
                "value": str(event.id),
            }],
        },
    ]
    return text, blocks


def record_safety_violation_ack(event_id: int, signature_name: str, db: Session) -> dict:
    """Shared by the Slack safety_violation_ack button handler (no
    driver-facing web page yet — no video to gate on, so a Slack ack is
    sufficient for now)."""
    event = db.query(SafetyEvent).filter(SafetyEvent.id == event_id).first()
    if not event:
        return {"status": "not_found"}
    if event.ack_status == "acknowledged":
        return {"status": "already_acknowledged", "acknowledged_at": event.acknowledged_at.isoformat() if event.acknowledged_at else None}

    # Belt-and-suspenders stub for the future video gate — inert today
    # since SAFETY_VIOLATION_VIDEO_ACTIVE is off, same shape as DVIC's
    # record_acknowledgment() check.
    if SAFETY_VIOLATION_VIDEO_ACTIVE and event.video_watched_at is None:
        return {"status": "video_not_watched"}

    event.ack_status = "acknowledged"
    event.acknowledged_at = datetime.utcnow()
    db.commit()

    refresh_safety_ack_summary(db)

    return {"status": "acknowledged", "event_id": event_id, "acknowledged_at": event.acknowledged_at.isoformat()}


def refresh_safety_ack_summary(db: Session) -> dict:
    """Consolidated #nday-mgt summary of confirmed-violation acknowledgment
    status: ✅ acknowledged / ⏳ pending. One message, updated in place —
    not a post per driver (same lesson as dvic.py's refresh_dvic_ack_summary())."""
    confirmed = db.query(SafetyEvent).filter(SafetyEvent.review_status == "confirmed").all()
    if not confirmed:
        return {"status": "no_confirmed"}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    acknowledged = [e for e in confirmed if e.ack_status == "acknowledged"]
    pending = [e for e in confirmed if e.ack_status != "acknowledged"]

    lines = [f"✅ {len(acknowledged)} acknowledged  ·  ⏳ {len(pending)} pending"]
    if pending:
        lines.append("\n*⏳ Pending:*\n" + "\n".join(f"• {e.driver_name} — {e.metric_type}" for e in pending))

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📋 Safety Violation Acknowledgments", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Updated {datetime.utcnow().strftime('%H:%M UTC')}_"}]},
    ]

    state_key = "safety_violation_ack_summary"
    state = get_reminder_state(db, state_key)
    existing_ts = state.get("ts")
    try:
        if existing_ts:
            client.chat_update(channel=NDAY_MGT_CHANNEL, ts=existing_ts, text="Safety Violation Acknowledgments", blocks=blocks)
            ts = existing_ts
        else:
            resp = client.chat_postMessage(channel=NDAY_MGT_CHANNEL, text="Safety Violation Acknowledgments", blocks=blocks)
            ts = resp.get("ts")
        set_reminder_state(db, state_key, {"ts": ts})
        return {"status": "ok", "acknowledged": len(acknowledged), "pending": len(pending)}
    except Exception as exc:
        logger.warning("Safety violation ack summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _handle_safety_event_confirm(payload: dict, db: Session) -> None:
    """Dispatch confirms a violation is real — routes the driver a
    write-up DM. Routed from slack_interactions.py."""
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        return
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "safety_event_confirm"), None)
    if not action:
        return
    try:
        event_id = int(action.get("value") or "")
    except ValueError:
        return

    event = db.query(SafetyEvent).filter(SafetyEvent.id == event_id).first()
    if not event or event.review_status != "pending":
        return  # already actioned — guard against double-click

    reviewer = payload.get("user", {}).get("username") or user_id
    event.review_status = "confirmed"
    event.reviewed_by = reviewer
    event.reviewed_at = datetime.utcnow()

    dm_note = "No Slack ID on file — follow up with the driver directly."
    roster = resolve_roster_entry(event.driver_name, db)
    if roster and roster.slack_member_id:
        fallback_text, blocks = _build_safety_violation_dm(event)
        ok, dm_channel, dm_ts = _dm_with_blocks(roster.slack_member_id, fallback_text, blocks)
        if ok:
            event.dm_channel = dm_channel
            event.dm_ts = dm_ts
            event.ack_status = "pending"
            dm_note = "Driver DM sent."
        else:
            dm_note = "Driver DM failed to send — follow up directly."
    db.commit()

    refresh_safety_ack_summary(db)

    client = _client()
    if client and event.review_slack_channel and event.review_slack_ts:
        try:
            client.chat_update(
                channel=event.review_slack_channel,
                ts=event.review_slack_ts,
                text="Safety violation confirmed",
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": (
                        f"✅ *Confirmed* by <@{user_id}> — {event.driver_name} — {event.metric_type}\n{dm_note}"
                    )},
                }],
            )
        except Exception as exc:
            logger.warning("chat_update on safety_event_confirm failed: %s", exc)


def _handle_safety_event_false_flag(payload: dict, db: Session) -> None:
    """Dispatch marks a violation as a false positive — no driver DM."""
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        return
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "safety_event_false_flag"), None)
    if not action:
        return
    try:
        event_id = int(action.get("value") or "")
    except ValueError:
        return

    event = db.query(SafetyEvent).filter(SafetyEvent.id == event_id).first()
    if not event or event.review_status != "pending":
        return

    event.review_status = "false_flagged"
    event.reviewed_by = payload.get("user", {}).get("username") or user_id
    event.reviewed_at = datetime.utcnow()
    db.commit()

    client = _client()
    if client and event.review_slack_channel and event.review_slack_ts:
        try:
            client.chat_update(
                channel=event.review_slack_channel,
                ts=event.review_slack_ts,
                text="Safety violation false-flagged",
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": (
                        f"❌ *False-flagged* by <@{user_id}> — {event.driver_name} — {event.metric_type}"
                    )},
                }],
            )
        except Exception as exc:
            logger.warning("chat_update on safety_event_false_flag failed: %s", exc)


def _handle_safety_violation_ack(payload: dict, db: Session) -> None:
    """Driver tapped 'Acknowledge' on their safety-violation write-up DM."""
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "safety_violation_ack"), None)
    if not action:
        return
    try:
        event_id = int(action.get("value") or "")
    except ValueError:
        return

    result = record_safety_violation_ack(event_id, "Acknowledged via Slack", db)
    if result.get("status") not in ("acknowledged", "already_acknowledged"):
        return

    event = db.query(SafetyEvent).filter(SafetyEvent.id == event_id).first()
    client = _client()
    if client and event and event.dm_channel and event.dm_ts:
        try:
            client.chat_update(
                channel=event.dm_channel,
                ts=event.dm_ts,
                text="Acknowledged",
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "✅ *Acknowledged* — thank you."},
                }],
            )
        except Exception as exc:
            logger.warning("chat_update on safety_violation_ack failed: %s", exc)


def get_driver_safety_summary(db: Session, start_date: date, end_date: date) -> dict[str, dict]:
    """{driver_name: {"count": n, "metric_types": {...}}} for events in
    [start_date, end_date] inclusive. Shared helper — e.g. rostering.py's
    driver summary matrix should call this rather than querying
    SafetyEvent directly."""
    rows = (
        db.query(SafetyEvent)
        .filter(SafetyEvent.report_date >= start_date, SafetyEvent.report_date <= end_date)
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        if not r.driver_name:
            continue
        entry = out.setdefault(r.driver_name, {"count": 0, "metric_types": {}})
        entry["count"] += 1
        mt = r.metric_type or "Unknown"
        entry["metric_types"][mt] = entry["metric_types"].get(mt, 0) + 1
    return out


@router.get("")
def list_safety_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    driver_name: Optional[str] = None,
    metric_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(SafetyEvent)
    if start_date:
        q = q.filter(SafetyEvent.report_date >= date.fromisoformat(start_date))
    if end_date:
        q = q.filter(SafetyEvent.report_date <= date.fromisoformat(end_date))
    if driver_name:
        q = q.filter(SafetyEvent.driver_name == driver_name)
    if metric_type:
        q = q.filter(SafetyEvent.metric_type == metric_type)
    rows = q.order_by(SafetyEvent.event_at.desc()).limit(500).all()
    return {
        "total": len(rows),
        "events": [
            {
                "event_id": r.event_id,
                "report_date": r.report_date.isoformat() if r.report_date else None,
                "driver_name": r.driver_name,
                "transporter_id": r.transporter_id,
                "event_at": r.event_at.isoformat() if r.event_at else None,
                "vin": r.vin,
                "program_impact": r.program_impact,
                "metric_type": r.metric_type,
                "metric_subtype": r.metric_subtype,
                "source": r.source,
                "video_link": r.video_link,
                "review_details": r.review_details,
            }
            for r in rows
        ],
    }


@router.get("/summary")
def safety_summary(start_date: str, end_date: str, db: Session = Depends(get_db)):
    """Per-driver event counts for a date range."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "start_date/end_date must be YYYY-MM-DD")
    summary = get_driver_safety_summary(db, start, end)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "driver_count": len(summary),
        "drivers": [
            {"driver_name": name, "count": v["count"], "metric_types": v["metric_types"]}
            for name, v in sorted(summary.items(), key=lambda kv: -kv[1]["count"])
        ],
    }


@router.post("/post-pending-reviews")
def post_pending_reviews_endpoint(db: Session = Depends(get_db)):
    """Manual/on-demand trigger — posts a Confirm/False-Flag review
    message for every not-yet-posted SafetyEvent, including already-
    ingested historical rows (e.g. this morning's violations). Gated by
    SAFETY_VIOLATION_REVIEW_ACTIVE."""
    return post_pending_safety_violations(db)
