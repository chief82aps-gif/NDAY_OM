"""
Coaching Notifications — Amazon's weekly "Coaching Notifications Weekly
Digest" email (POD bypass, not-following-instructions, wrong-address
deliveries, etc.), added 2026-08-01.

No attachment; source data is an inline HTML table in the email body
(see api/src/ingest/coaching_notifications.py). Ingested via a Slack
channel-email forward into an already-scanned channel, not a direct
mailbox integration (see ops_ingest.py's "coaching_notifications"
detected_type).

Workflow, per explicit direction:
  1. Driver gets a low-key, positive DM ("mentoring, not discipline" --
     buried behind a plain Acknowledge button, no scary framing) with
     the specific behavior + a training-video suggestion if one exists
     for that behavior.
  2. Once the driver acknowledges, Ops Manager/Luis gets notified and
     must approve.
  3. Once Ops Manager approves, HR gets notified and must approve --
     HR IS a required gating stage here (the one deliberate difference
     from crash_report.py's pattern, where HR is notified-only).
  4. Once HR approves, the notification is "finalized" and shows up in
     the write-up tracker (manager_accountability.discipline_tracker()).

Only rows with a populated Behavior are DMed at all -- rows with a
blank Behavior/Coaching Tip (a real quirk in Amazon's own export) are
stored for completeness but there's nothing actionable to tell a driver
about, so they're never notified.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, CoachingNotification, CoachingNotificationApproval, DriverRosterEntry,
    get_reminder_state, set_reminder_state,
)
from api.src.ingest.coaching_notifications import parse_coaching_notifications_html
from api.src.driver_identity import resolve_roster_entry
from api.src.routes.document_routing import get_role_slack_ids
from api.src.feature_flags import get_flag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/coaching-notifications", tags=["coaching-notifications"])

# Deliberately a standalone scanner (like daily_notify.py's
# scan_for_ecp_message()) rather than another entry into ops_ingest.py's
# generic file-download pipeline -- that pipeline requires a real
# file_url to download (see run_pending_ingest_jobs()'s `if not
# job.file_url: continue`), and this source has no attachment most of
# the time. Safer to keep this isolated than to retrofit the shared
# pipeline's core loop under time pressure.
#
# UNTESTED against a real forwarded email as of 2026-08-01 -- Slack's
# own "send emails to this channel" feature may deliver the content as
# plain message text, a small file attachment, or something in between,
# and the exact shape wasn't available to test against. This checks a
# file attachment first, falls back to the message's own text -- it
# will very likely need one real-world adjustment once an actual weekly
# digest gets forwarded.
COACHING_NOTIFICATIONS_CHANNEL = os.getenv("COACHING_NOTIFICATIONS_CHANNEL_ID", os.getenv("OPS_CHANNEL_ID", "C0BE4ALL1EX"))
_SCAN_STATE_KEY = "coaching_notifications_scanned_message_ids"

# Best-effort mapping to the same CAP/BOC video topics wave_lead.py's Team
# Focus page already uses (Governance/06_NDL_CAP_Compliance_Monitoring_SRD.md)
# -- reuses the same library rather than starting a second one. Matched
# case-insensitively (fixed 2026-08-05 -- Amazon's real export sends
# Behavior in ALL CAPS, e.g. "DELIVERED TO INCORRECT ADDRESS", which never
# matched this dict's Title Case keys, so the video line silently never
# appeared). Add entries here as more Behavior strings show up in real
# weekly digests.
_BEHAVIOR_TO_VIDEO = {
    "delivered to incorrect address": "Delivery completion",
    "not following delivery/customer instructions": "Customer experience / professionalism",
    "package scanned far away from delivery location": "Proof-of-delivery photos",
    "low severity damage: lawn, driveway, mailbox, parked vehicle, house, garage, or other non-essential items": "Vehicle & property care",
}

_APPROVAL_STAGES = [(1, "driver"), (2, "ops_manager"), (3, "hr")]


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _store_coaching_notifications(html_content: str, source_email_id: str, db: Session) -> dict:
    """Parse + upsert (deduped by Amazon's own case_number). Called from
    ops_ingest.py's dispatcher once a forwarded digest is detected."""
    summary, records = parse_coaching_notifications_html(html_content, source_email_id)
    if not records:
        return {"status": "error", "message": "No rows parsed from email content."}

    created = 0
    dmed = 0
    for rec in records:
        existing = db.query(CoachingNotification).filter(CoachingNotification.case_number == rec["case_number"]).first()
        if existing:
            continue

        roster = resolve_roster_entry(rec["da_name"], db)
        notification = CoachingNotification(
            week=rec["week"], da_name=rec["da_name"], transporter_id=rec["transporter_id"],
            station=rec["station"], case_number=rec["case_number"],
            occurrence_info=rec["occurrence_info"], behavior=rec["behavior"],
            coaching_tip=rec["coaching_tip"], status=rec.get("status"),
            source_email_id=rec["source_email_id"],
            roster_id=roster.id if roster else None,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)
        created += 1

        if notification.behavior and roster and roster.slack_member_id:
            for stage_order, role in _APPROVAL_STAGES:
                db.add(CoachingNotificationApproval(notification_id=notification.id, stage_order=stage_order, role=role, status="pending"))
            db.commit()
            _notify_driver_stage(notification, roster, db)
            dmed += 1

    return {"status": "ingested", "week": summary["week"], "row_count": summary["row_count"], "created": created, "dmed": dmed}


def scan_for_coaching_notifications(db: Session) -> dict:
    """Checks COACHING_NOTIFICATIONS_CHANNEL for a forwarded 'Coaching
    Notifications Weekly Digest' email not yet processed. Called every
    ~10 min from main.py's background loop. Dedupes by Slack message ts
    (stored via get_reminder_state/set_reminder_state), separate from
    CoachingNotification.case_number's own per-row dedup -- this just
    stops the same message from being re-scanned every cycle."""
    if not get_flag("COACHING_NOTIFICATIONS_ACTIVE"):
        return {"status": "inactive", "note": "Set COACHING_NOTIFICATIONS_ACTIVE=true (or flip it on /feature-flags) to enable"}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    state = get_reminder_state(db, _SCAN_STATE_KEY)
    seen_ids = set(state.get("ids", []))

    try:
        resp = client.conversations_history(channel=COACHING_NOTIFICATIONS_CHANNEL, limit=50)
    except Exception as exc:
        logger.warning("Coaching notifications scan failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    processed = 0
    for msg in resp.get("messages", []):
        ts = msg.get("ts", "")
        if not ts or ts in seen_ids:
            continue
        text = msg.get("text", "") or ""
        if "coaching notification" not in text.lower() and "coaching tips" not in text.lower():
            continue

        seen_ids.add(ts)
        content = text
        for f in msg.get("files", []):
            url = f.get("url_private_download") or f.get("url_private")
            if not url:
                continue
            try:
                token = os.getenv("SLACK_BOT_TOKEN")
                import requests
                dl = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                dl.raise_for_status()
                content = dl.content.decode("utf-8", errors="replace")
                break
            except Exception as exc:
                logger.warning("Coaching notification file download failed: %s", exc)

        result = _store_coaching_notifications(content, ts, db)
        logger.info("Coaching notifications scan: message %s -> %s", ts, result)
        processed += 1

    set_reminder_state(db, _SCAN_STATE_KEY, {"ids": list(seen_ids)[-200:]})  # cap growth
    return {"status": "ok", "processed": processed}


@router.post("/scan")
def trigger_scan(db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the background loop calls."""
    return scan_for_coaching_notifications(db)


class IngestEmailRequest(BaseModel):
    html_content: str
    source_email_id: str


@router.post("/ingest-email")
def ingest_email(request: IngestEmailRequest, db: Session = Depends(get_db)):
    """Direct-email ingest path -- added 2026-08-05, alongside a daily
    scheduled check (outside this app, since the backend itself has no
    mailbox access) that searches Outlook for the latest Consolidated
    Coaching Notification email and posts its HTML body here. Calls the
    exact same _store_coaching_notifications() the Slack-scan path uses,
    so case_number dedup (Amazon re-sending the same event more than
    once) is identical either way -- no separate storage logic to keep
    in sync. Respects the same COACHING_NOTIFICATIONS_ACTIVE flag as the
    Slack-scan path -- the flag conceptually gates the whole feature,
    not just one ingest route in."""
    if not get_flag("COACHING_NOTIFICATIONS_ACTIVE"):
        return {"status": "inactive", "note": "Set COACHING_NOTIFICATIONS_ACTIVE=true (or flip it on /feature-flags) to enable"}
    return _store_coaching_notifications(request.html_content, request.source_email_id, db)


@router.get("/preview-test")
def preview_test(
    driver_name: str = "Collin Jonathan LaTour",
    behavior: str = "Delivered To Incorrect Address",
    db: Session = Depends(get_db),
):
    """See the exact mentoring DM text without sending anything or
    writing to the database -- for reviewing tone/format before using
    /send-test to actually deliver it."""
    roster = resolve_roster_entry(driver_name, db)
    if not roster:
        raise HTTPException(status_code=404, detail=f"No roster entry found for '{driver_name}'")

    notification = CoachingNotification(da_name=roster.payroll_name, behavior=behavior, coaching_tip="This is a test send -- verifying the mentoring DM format and tone.")
    return {"driver_name": roster.payroll_name, "text": build_driver_stage_message(notification, db)}


@router.post("/send-test")
def send_test(
    driver_name: str = "Collin Jonathan LaTour",
    behavior: str = "Delivered To Incorrect Address",
    db: Session = Depends(get_db),
):
    """Sends one real driver the real mentoring DM (not a fake preview --
    exercises the exact same _notify_driver_stage()/video-lookup/approval-
    chain-creation code path as a genuine Amazon-sourced notification),
    bypassing DRIVER_DM_ACTIVE/COACHING_NOTIFICATIONS_ACTIVE deliberately
    so it works even while those gates are being tested. Uses a synthetic
    case_number (TEST-<timestamp>) so it never collides with a real
    Amazon case_number and each call creates a fresh row rather than
    deduping against a prior test send."""
    roster = resolve_roster_entry(driver_name, db)
    if not roster:
        raise HTTPException(status_code=404, detail=f"No roster entry found for '{driver_name}'")
    if not roster.slack_member_id:
        raise HTTPException(status_code=400, detail=f"'{driver_name}' has no linked Slack account")

    case_number = f"TEST-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    notification = CoachingNotification(
        week="TEST", da_name=roster.payroll_name, transporter_id=roster.position_id,
        case_number=case_number, behavior=behavior,
        coaching_tip="This is a test send -- verifying the mentoring DM format and tone.",
        status="TEST", source_email_id="manual-test-send",
        roster_id=roster.id,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    for stage_order, role in _APPROVAL_STAGES:
        db.add(CoachingNotificationApproval(notification_id=notification.id, stage_order=stage_order, role=role, status="pending"))
    db.commit()

    _notify_driver_stage(notification, roster, db)

    return {"status": "sent", "driver_name": roster.payroll_name, "notification_id": notification.id, "case_number": case_number}


def build_driver_stage_message(notification: CoachingNotification, db: Session) -> str:
    """Pure text builder, no Slack calls/DB writes -- shared by the real
    send path and the /preview-test endpoint so what you preview is
    guaranteed identical to what actually sends."""
    first = (notification.da_name or "Driver").split()[0]
    video_topic = _BEHAVIOR_TO_VIDEO.get((notification.behavior or "").strip().lower())
    video_line = ""
    if video_topic:
        from api.src.database import TrainingVideoLink
        link = db.query(TrainingVideoLink).filter(TrainingVideoLink.metric_label == video_topic).first()
        if link:
            video_line = f"\n\n🎥 There's a short training video on *{video_topic}* if you want a quick refresher: {link.video_url}"
        else:
            video_line = f"\n\n🎥 There's a training topic on *{video_topic}* that covers this — worth a look when you get a chance."

    # Amazon's real export sends Behavior in ALL CAPS -- reads as shouting
    # in a message meant to be upbeat/low-key, so display it title-cased.
    behavior_display = notification.behavior.title() if notification.behavior else "a delivery behavior"
    tip_line = f"_{notification.coaching_tip}_\n\n" if notification.coaching_tip else ""

    return (
        f":wave: Hey {first} — quick heads up, nothing to stress about.\n\n"
        f"Amazon flagged one of your deliveries this week: *{behavior_display}*.\n\n"
        f"{tip_line}"
        f"{video_line}\n\n"
        "Just tap Acknowledge below so we know you saw it — that's all this needs from you."
    )


def _notify_driver_stage(notification: CoachingNotification, roster: DriverRosterEntry, db: Session) -> None:
    """Stage 1 — low-key, positive DM to the specific driver. Deliberately
    NOT framed as a write-up on its face; the acknowledgment is what
    quietly starts the real approval chain behind it."""
    approval = db.query(CoachingNotificationApproval).filter(
        CoachingNotificationApproval.notification_id == notification.id,
        CoachingNotificationApproval.stage_order == 1,
    ).first()
    client = _client()
    if not approval or not client:
        return

    text = build_driver_stage_message(notification, db)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Acknowledge", "emoji": True},
                "style": "primary",
                "action_id": "coaching_notification_ack",
                "value": str(notification.id),
            }],
        },
    ]
    try:
        resp = client.chat_postMessage(channel=roster.slack_member_id, text=f"Coaching note — {notification.behavior}", blocks=blocks)
        approval.status = "notified"
        approval.notified_at = datetime.utcnow()
        approval.slack_channel = resp.get("channel")
        approval.slack_ts = resp.get("ts")
        db.commit()
    except Exception as exc:
        logger.warning("Coaching notification driver DM failed for %s: %s", notification.da_name, exc)


def _notify_role_stage(notification: CoachingNotification, stage_order: int, role: str, db: Session) -> None:
    """Stages 2 (ops_manager/Luis) and 3 (hr) — plain internal review,
    same shape as crash_report.py's _notify_stage()."""
    approval = db.query(CoachingNotificationApproval).filter(
        CoachingNotificationApproval.notification_id == notification.id,
        CoachingNotificationApproval.stage_order == stage_order,
    ).first()
    client = _client()
    slack_ids = get_role_slack_ids(db, role)
    if not approval or not client or not slack_ids:
        if approval and not slack_ids:
            logger.warning("Coaching notification %s: role '%s' has no Slack ID on file — stage %s not notified.",
                            notification.case_number, role, stage_order)
        return

    summary = (
        f":clipboard: *Coaching Notification — {notification.da_name}* (Case {notification.case_number})\n"
        f"*Behavior:* {notification.behavior}\n"
        f"*Week:* {notification.week} | *Station:* {notification.station}\n"
        f"Driver has acknowledged. Awaiting your approval to move this forward."
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "action_id": "coaching_notification_approve",
                "text": {"type": "plain_text", "text": "✅ Approve & Route to Next Stage", "emoji": True},
                "style": "primary",
                "value": f"{notification.id}:{stage_order}",
            }],
        },
    ]
    sent_channel = sent_ts = None
    for sid in slack_ids:
        try:
            msg = client.chat_postMessage(channel=sid, text="Coaching notification needs approval:", blocks=blocks)
            if sent_channel is None:
                sent_channel, sent_ts = msg.get("channel"), msg.get("ts")
        except Exception as exc:
            logger.warning("Coaching notification %s: notify stage %s (%s) failed for %s: %s",
                            notification.case_number, stage_order, role, sid, exc)
    approval.status = "notified"
    approval.notified_at = datetime.utcnow()
    approval.slack_channel = sent_channel
    approval.slack_ts = sent_ts
    db.commit()


def _handle_coaching_notification_ack(payload: dict, db: Session) -> None:
    """Driver taps Acknowledge — advances stage 1, notifies stage 2 (ops_manager)."""
    user_id = payload.get("user", {}).get("id", "")
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "coaching_notification_ack"), None)
    if not action:
        return
    try:
        notification_id = int(action.get("value") or "")
    except ValueError:
        return

    notification = db.query(CoachingNotification).filter(CoachingNotification.id == notification_id).first()
    approval = db.query(CoachingNotificationApproval).filter(
        CoachingNotificationApproval.notification_id == notification_id,
        CoachingNotificationApproval.stage_order == 1,
    ).first()
    if not notification or not approval or approval.status == "approved":
        return

    roster = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == notification.roster_id).first()
    if not roster or roster.slack_member_id != user_id:
        logger.warning("Unauthorized coaching_notification_ack attempt by %s for notification %s", user_id, notification_id)
        return

    approval.status = "approved"
    approval.approved_at = datetime.utcnow()
    approval.approved_by = user_id
    db.commit()

    client = _client()
    if client and approval.slack_channel and approval.slack_ts:
        try:
            client.chat_update(
                channel=approval.slack_channel, ts=approval.slack_ts,
                text="Acknowledged", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Acknowledged* — thanks!"}}],
            )
        except Exception as exc:
            logger.warning("chat_update on coaching_notification_ack failed: %s", exc)

    _notify_role_stage(notification, 2, "ops_manager", db)


def _handle_coaching_notification_approve(payload: dict, db: Session) -> None:
    """Ops Manager/Luis or HR taps Approve — advances stage 2->3, or
    finalizes on stage 3."""
    user_id = payload.get("user", {}).get("id", "")
    action = next((a for a in payload.get("actions", []) if a.get("action_id") == "coaching_notification_approve"), None)
    if not action:
        return
    try:
        notification_id_str, stage_order_str = (action.get("value") or "").split(":")
        notification_id, stage_order = int(notification_id_str), int(stage_order_str)
    except Exception:
        logger.warning("Malformed coaching_notification_approve value: %s", action.get("value"))
        return

    notification = db.query(CoachingNotification).filter(CoachingNotification.id == notification_id).first()
    approval = db.query(CoachingNotificationApproval).filter(
        CoachingNotificationApproval.notification_id == notification_id,
        CoachingNotificationApproval.stage_order == stage_order,
    ).first()
    if not notification or not approval or approval.status != "notified":
        return

    role = approval.role
    if user_id not in get_role_slack_ids(db, role):
        logger.warning("Unauthorized coaching_notification_approve attempt by %s for stage '%s' (notification %s)",
                        user_id, role, notification_id)
        return

    approval.status = "approved"
    approval.approved_at = datetime.utcnow()
    approval.approved_by = user_id
    db.commit()

    client = _client()
    if client and approval.slack_channel and approval.slack_ts:
        try:
            client.chat_update(
                channel=approval.slack_channel, ts=approval.slack_ts,
                text=f"Approved by <@{user_id}>",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"✅ *Approved by* <@{user_id}>"}}],
            )
        except Exception as exc:
            logger.warning("chat_update on coaching_notification_approve failed: %s", exc)

    next_stage = next(((so, r) for so, r in _APPROVAL_STAGES if so == stage_order + 1), None)
    if next_stage:
        _notify_role_stage(notification, next_stage[0], next_stage[1], db)
        return

    # HR (final gating stage) just approved -- fully finalized.
    logger.info("Coaching notification %s fully finalized (case %s).", notification.id, notification.case_number)


@router.get("")
def list_coaching_notifications(week: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(CoachingNotification)
    if week:
        q = q.filter(CoachingNotification.week == week)
    rows = q.order_by(CoachingNotification.created_at.desc()).all()
    return {
        "notifications": [
            {
                "id": n.id, "week": n.week, "da_name": n.da_name, "transporter_id": n.transporter_id,
                "station": n.station, "case_number": n.case_number, "behavior": n.behavior,
                "coaching_tip": n.coaching_tip, "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Reliability score input — added 2026-08-02, same purpose/precedent as
# dvic.py's get_dvic_reliability_deductions(). Only reads already-recorded
# notifications; the driver-ack -> ops_manager -> HR approval chain above
# is completely unaffected.
# ─────────────────────────────────────────────────────────────────────────────

COACHING_RELIABILITY_DEDUCTION = 4.0


def get_coaching_reliability_deductions(since_date: date, db: Session) -> dict[int, float]:
    """{roster_id: deduction} for every CoachingNotification created since
    since_date. roster_id is already a native column here -- no identity
    resolution needed, unlike DVIC/Safety."""
    since_dt = datetime.combine(since_date, datetime.min.time())
    rows = (
        db.query(CoachingNotification)
        .filter(CoachingNotification.created_at >= since_dt, CoachingNotification.roster_id.isnot(None))
        .all()
    )
    deductions: dict[int, float] = {}
    for n in rows:
        deductions[n.roster_id] = deductions.get(n.roster_id, 0.0) + COACHING_RELIABILITY_DEDUCTION
    return deductions
