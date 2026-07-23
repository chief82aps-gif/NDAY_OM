"""
DVIC Pre-Trip Inspection (Under-90-Second) module.

Tracks drivers who complete their vehicle inspection too quickly.
Actions:
  - Ingest weekly Amazon Excel report
  - DM flagged drivers with escalating safety notices
  - Driver digital acknowledgment (signed via /dvic-ack page)
  - Daily 3 PM reminder to #nday-mgt if file not yet uploaded

Endpoints:
  POST /dvic/ingest-upload          Direct file upload
  POST /dvic/ingest-slack           Ingest from Slack (ops_ingest dispatcher)
  GET  /dvic/weeks                  Available weeks
  GET  /dvic/violations             Driver summary for a week
  GET  /dvic/violations/{tid}       Full history for one driver
  POST /dvic/send-dm/{tid}          DM one driver
  POST /dvic/send-all-dms           DM all flagged drivers for latest week
  GET  /dvic/acknowledgments        List digital acknowledgments
  POST /dvic/acknowledge            Driver submits acknowledgment
  GET  /dvic/upload-status          Has today's file been uploaded?
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, date, timezone
from typing import Optional

import jwt
import requests
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal,
    DvicSnapshot, DvicViolation, DvicAcknowledgment, DvicCounselingRecord,
    DriverRosterEntry,
    get_reminder_state, set_reminder_state,
)
from api.src.ingest.dvic import parse_dvic_xlsx, extract_week
from api.src.driver_identity import resolve_roster_entry
from api.src.authorization import require_any_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dvic", tags=["dvic"])

NDAY_MGT_CHANNEL = os.getenv("NDAY_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
OPS_CHANNEL      = os.getenv("OPS_CHANNEL_ID",  "C0BE4ALL1EX")    # #nday-operations-management
APP_URL          = os.getenv("APP_URL", "https://nday-om.vercel.app")

# Driver-facing DVIC DMs stay off until the flow is fully tested — same gate
# used for driver DMs in rostering.py (api/src/routes/rostering.py). Advancing
# a driver's counseling stage is coupled to actually sending the DM (see
# _process_week below), so the ladder can't silently advance while this is off.
_DM_ACTIVE = os.getenv("DRIVER_DM_ACTIVE", "false").lower() == "true"

# Forced-training-video gate for repeat (Stage 2+) DVIC violations — added
# 2026-07-23. Hard off-switch, same pattern as DRIVER_DM_ACTIVE/
# TEAM_ROOM_MESSAGES_ACTIVE: fully built and testable, but has zero effect
# on the real DM/acknowledgment flow until explicitly turned on. Do not
# flip on until a real training video has been uploaded via
# POST /dvic/training-video — see CLAUDE.md.
DVIC_TRAINING_VIDEO_ACTIVE = os.getenv("DVIC_TRAINING_VIDEO_ACTIVE", "false").lower() == "true"
DVIC_VIDEO_GATE_MIN_STAGE = 2
DVIC_VIDEO_TOKEN_TTL_HOURS = 72
DVIC_TRAINING_VIDEO_STATE_KEY = "dvic_training_video"
# Minimum real elapsed wall-clock time between opening the training page
# and being allowed to mark it watched — enforced server-side against
# video_started_at, not just trusting the <video> 'ended' event (which
# fires immediately if someone scrubs straight to the end). Update this
# to match the real video's length once uploaded.
DVIC_VIDEO_MIN_WATCH_SECONDS = int(os.getenv("DVIC_VIDEO_MIN_WATCH_SECONDS", "360"))


def _issue_dvic_video_token(transporter_id: str, week: str) -> str:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    payload = {
        "purpose": "dvic_video",
        "transporter_id": transporter_id,
        "week": week,
        "exp": int(time.time()) + DVIC_VIDEO_TOKEN_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _verify_dvic_video_token(token: str) -> Optional[dict]:
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None
    if payload.get("purpose") != "dvic_video":
        return None
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _post(channel: str, text: str, blocks=None) -> None:
    c = _client()
    if not c:
        return
    try:
        kwargs = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        c.chat_postMessage(**kwargs)
    except Exception as exc:
        logger.warning("DVIC Slack post failed: %s", exc)


def _dm(slack_member_id: str, text: str) -> bool:
    c = _client()
    if not c:
        return False
    try:
        c.chat_postMessage(channel=slack_member_id, text=text)
        return True
    except Exception as exc:
        logger.warning("DVIC DM failed (%s): %s", slack_member_id, exc)
        return False


def _dm_with_blocks(slack_member_id: str, fallback_text: str, blocks: list):
    """Send a Block Kit DM. Returns (ok, channel_id, message_ts) for chat_update on ack."""
    c = _client()
    if not c:
        return False, None, None
    try:
        resp = c.chat_postMessage(channel=slack_member_id, text=fallback_text, blocks=blocks)
        return True, resp.get("channel"), resp.get("ts")
    except Exception as exc:
        logger.warning("DVIC DM (blocks) failed (%s): %s", slack_member_id, exc)
        return False, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Name matching — DVIC "First Last" vs roster "Last, First"
# ─────────────────────────────────────────────────────────────────────────────

def _name_tokens(name: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z\s]", "", name.lower()).split())


def _find_roster_entry(transporter_name: str, db: Session) -> Optional[DriverRosterEntry]:
    """Delegates to the shared driver-identity resolver (2026-07-23) —
    this used to accept a match on just a single shared name token
    (score >= 1), noticeably weaker than the >=2 threshold used
    everywhere else this same DOP/Cortex-vs-ADP mismatch is handled.
    Tightened deliberately; a driver who previously matched only on a
    common first name will now correctly fail to match."""
    return resolve_roster_entry(transporter_name, db)


# ─────────────────────────────────────────────────────────────────────────────
# DM escalation messages — 4-stage progressive discipline.
#
# The stage is driven by DvicCounselingRecord.stage (how many times this
# driver has been counseled across weeks), NOT by how many instances appear
# on a single report — a driver who's already been counseled keeps
# reappearing on the rolling 7-day report, and that must not by itself
# trigger a repeat write-up. `count` (this week's instance count) is only
# used to word the message.
# ─────────────────────────────────────────────────────────────────────────────

def _week_label(week: str) -> str:
    return week.replace("-W", " Week ").replace("2026 Week ", "Week ")


def _counseling_message(stage: int, name: str, count: int, week: str) -> str:
    first = name.split()[0] if name else "Driver"
    week_label = _week_label(week)
    plural = "s" if count != 1 else ""

    if stage == 1:
        return (
            f":wave: Hey {first}, this is Dispatch/Safety checking in — not a write-up, just us looking out for you.\n\n"
            f"This week you had *{count} pre-trip inspection{plural}* logged under 90 seconds. We get it — you're "
            "trying to get moving fast. But that walk-around is one of the only things standing between you and a "
            "flat tire, a bad mirror, or a brake issue nobody caught before you were 20 miles into your route.\n\n"
            "*You're too important to us to risk getting hurt over a rushed checklist.* Take the full 90 seconds — "
            "every shift, no exceptions. It's not about the paperwork, it's about you going home the same way you "
            "came in.\n\n"
            "No action needed here beyond tapping *Acknowledge* — just wanted you to know we noticed, and we've got "
            "your back."
        )
    elif stage == 2:
        return (
            f":memo: Hey {first}, we flagged you again this week — *{count} pre-trip inspection{plural}* under 90 "
            f"seconds ({week_label}).\n\n"
            "We meant what we said last time: this isn't about catching you, it's about making sure you're "
            "actually checking the van before you drive it. A second flag means we're logging this one as a real "
            "coaching note in your file — not to punish you, but because a pattern here is a real safety risk, and "
            "we have to start treating it like one.\n\n"
            "Take the extra 60-90 seconds. Walk all the way around. Check your tires, mirrors, lights. It matters "
            "more than the couple minutes it costs you.\n\n"
            "Please tap *Acknowledge* — this confirms you've seen it and it's noted in your file."
        )
    elif stage == 3:
        return (
            f":warning: {first}, this is a final warning on pre-trip inspection times — *{count} pre-trip "
            f"inspection{plural}* under 90 seconds this week ({week_label}).\n\n"
            "We've talked to you about this twice now. At this point we have to be direct: this is a real safety "
            "violation, not a habit we can keep coaching around informally. Your manager has been looped in. One "
            "more flag after this moves to formal disciplinary action.\n\n"
            "We still want this to end with you safe and driving, not with a write-up. Slow down at the start of "
            "your shift — it's the cheapest 90 seconds in your whole day.\n\n"
            "Please tap *Acknowledge*."
        )
    else:
        return (
            f":rotating_light: {first}, this is a formal written disciplinary notice — *{count} pre-trip "
            f"inspection{plural}* under 90 seconds this week ({week_label}), continuing after a final warning.\n\n"
            "This is now routed to NDAY Management and placed in your file. Continued violations put your "
            "position at risk. We genuinely don't want it to get there — please take this seriously starting with "
            "your very next shift.\n\n"
            "Please tap *Acknowledge* to confirm receipt."
        )


def _dm_blocks(stage: int, name: str, count: int, week: str, transporter_id: str, video_watched: bool = False) -> list:
    text = _counseling_message(stage, name, count, week)
    value = json.dumps({"transporter_id": transporter_id, "week": week, "stage": stage})

    needs_video = DVIC_TRAINING_VIDEO_ACTIVE and stage >= DVIC_VIDEO_GATE_MIN_STAGE and not video_watched
    if needs_video:
        # Baked in at send-time (not issued fresh on click) so this is a
        # genuine one-click button straight to the video page — same
        # pattern as rostering.py's "Can't Make It"/"Call Out" buttons.
        # No Acknowledge button here: record_acknowledgment() would reject
        # it anyway, and showing a button that's guaranteed to fail is
        # worse than not showing it.
        video_url = f"{APP_URL}/dvic-training?token={_issue_dvic_video_token(transporter_id, week)}"
        actions = [{
            "type": "button",
            "text": {"type": "plain_text", "text": "▶️  Watch Training Video", "emoji": True},
            "style": "primary",
            "action_id": "dvic_watch_video",
            "value": value,
            "url": video_url,
        }]
    else:
        actions = [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Acknowledge", "emoji": True},
            "style": "primary",
            "action_id": "dvic_ack",
            "value": value,
        }]

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": actions,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Counseling stage — persistent, per-driver progressive discipline ladder
# ─────────────────────────────────────────────────────────────────────────────

def _advance_counseling(tid: str, name: str, week: str, instance_count: int, db: Session):
    """Advance (or create) this driver's counseling record for `week`.

    Returns (record, is_new_action). is_new_action=False means this week was
    already actioned for this driver — the same name reappearing on a later
    rolling 7-day report must never trigger a second write-up for a week
    already handled. Caller should skip sending a DM when False.
    """
    record = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == tid
    ).first()

    if record and record.last_week == week:
        return record, False

    if not record:
        record = DvicCounselingRecord(transporter_id=tid, transporter_name=name, stage=0)
        db.add(record)

    record.transporter_name = name or record.transporter_name
    record.stage = min((record.stage or 0) + 1, 4)
    record.last_week = week
    record.last_instance_count = instance_count
    record.last_actioned_at = datetime.utcnow()
    record.ack_status = "pending"
    record.acknowledged_at = None
    record.video_watched_at = None
    record.video_started_at = None
    db.flush()
    return record, True


def _queue_discipline_writeup(record: "DvicCounselingRecord", week: str, db: Session) -> None:
    """Stage-4 formal write-ups route into the same pending-review queue as
    other management write-ups (see manager_accountability.py's
    ManagerAccountabilityEvent / discipline-tracker endpoint)."""
    try:
        from api.src.routes.manager_accountability import ManagerAccountabilityEvent, _on_duty_managers
        managers = _on_duty_managers(date.today())
        for manager in managers:
            existing = db.query(ManagerAccountabilityEvent).filter(
                ManagerAccountabilityEvent.shift_date == date.today(),
                ManagerAccountabilityEvent.manager_name == manager["name"],
                ManagerAccountabilityEvent.writeup_type == "dvic_repeat_violation",
                ManagerAccountabilityEvent.source_event_id == record.id,
            ).first()
            if existing:
                continue
            db.add(ManagerAccountabilityEvent(
                shift_date=date.today(),
                manager_name=manager["name"],
                manager_slack_id=manager["slack_id"],
                writeup_type="dvic_repeat_violation",
                source_event_id=record.id,
                source_detail=(
                    f"{record.transporter_name} — {record.last_instance_count} DVIC under-90s in "
                    f"{week}, stage 4 formal write-up"
                ),
            ))
        db.commit()
    except Exception as exc:
        logger.warning("Failed to queue DVIC discipline write-up: %s", exc)


def _process_week(week: str, db: Session, only_tid: Optional[str] = None) -> dict:
    """Advance counseling stage + send the Slack DM for each flagged driver,
    using the most-recently-imported snapshot for `week`.

    Gated by DRIVER_DM_ACTIVE — a no-op otherwise, so the counseling ladder
    can never silently advance while driver DMs are switched off.

    Violations are scoped to that ONE snapshot, not every row tagged with
    this week string — DVIC is a rolling trailing-7-day report re-uploaded
    daily under the same week label, so summing by week alone would count
    the same real-world instance multiple times (once per overlapping
    day's snapshot) and inflate both the DM's stated count and the stage
    advancement. _advance_counseling()'s own "already actioned this week"
    check (keyed by week, not snapshot) still ensures only one DM/stage
    bump happens per driver per week even though this runs once per daily
    upload.
    """
    if not _DM_ACTIVE:
        return {"status": "inactive", "note": "Set DRIVER_DM_ACTIVE=true on Render to enable driver DMs"}

    snap = (
        db.query(DvicSnapshot)
        .filter(DvicSnapshot.week == week)
        .order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc())
        .first()
    )
    if not snap:
        return {"status": "no_violations", "week": week}
    violations = db.query(DvicViolation).filter(DvicViolation.snapshot_id == snap.id).all()
    if only_tid:
        violations = [v for v in violations if v.transporter_id == only_tid]
    if not violations:
        return {"status": "no_violations", "week": week}

    by_driver: dict = defaultdict(list)
    for v in violations:
        by_driver[v.transporter_id].append(v)

    results = []
    sent_count = 0
    for tid, vrows in by_driver.items():
        name = vrows[0].transporter_name or tid
        roster = _find_roster_entry(name, db)

        record, is_new = _advance_counseling(tid, name, week, len(vrows), db)
        if not is_new:
            db.commit()
            results.append({"driver": name, "status": "already_actioned_this_week", "stage": record.stage})
            continue

        if not roster or not roster.slack_member_id:
            db.commit()
            results.append({"driver": name, "status": "no_slack_id", "stage": record.stage})
            continue

        blocks = _dm_blocks(record.stage, name, len(vrows), week, tid, video_watched=bool(record.video_watched_at))
        fallback = f"DVIC Safety Notice — {name} — stage {record.stage}"
        ok, channel, ts = _dm_with_blocks(roster.slack_member_id, fallback, blocks)
        if ok:
            record.dm_channel = channel
            record.dm_ts = ts
            sent_count += 1
        db.commit()

        results.append({
            "driver": name, "status": "sent" if ok else "failed",
            "stage": record.stage, "violations": len(vrows),
        })

        if record.stage >= 4:
            _queue_discipline_writeup(record, week, db)

    if sent_count > 0:
        refresh_dvic_ack_summary(week, db)

    return {
        "week": week,
        "total_drivers": len(by_driver),
        "sent": sent_count,
        "results": results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core ingest
# ─────────────────────────────────────────────────────────────────────────────

def _store_dvic(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    summary, violations = parse_dvic_xlsx(content, filename)
    week = summary["week"]

    if not violations:
        return {"status": "error", "message": "No violation rows parsed from file."}

    # Deduplicate by slack_file_id
    if slack_file_id:
        existing = db.query(DvicSnapshot).filter(DvicSnapshot.slack_file_id == slack_file_id).first()
        if existing:
            return {"status": "already_ingested", "week": week, "total_violations": existing.total_violations}

    # Replace existing snapshot for same week + filename
    old = db.query(DvicSnapshot).filter(
        DvicSnapshot.week == week, DvicSnapshot.source_file == filename
    ).first()
    if old:
        db.delete(old)
        db.flush()

    snap = DvicSnapshot(
        week=week,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        total_violations=summary["total_violations"],
        unique_drivers=summary["unique_drivers"],
        date_range_start=summary["date_range_start"],
        date_range_end=summary["date_range_end"],
    )
    db.add(snap)
    db.flush()

    for v in violations:
        db.add(DvicViolation(snapshot_id=snap.id, week=week, **v))

    db.commit()
    return {
        "status": "ingested",
        "week": week,
        "snapshot_id": snap.id,
        "total_violations": summary["total_violations"],
        "unique_drivers": summary["unique_drivers"],
        "date_range_start": summary["date_range_start"],
        "date_range_end": summary["date_range_end"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3 PM Daily Upload Reminder (called from main.py background loop)
# ─────────────────────────────────────────────────────────────────────────────

# Persisted in the database (ReminderThrottleState), not in-memory — see
# mgt_reminders.py's identical fix for why: an in-memory dict here resets on
# every process restart, wiping the "already sent"/"resolved today" memory
# and causing a spam loop on the next restart (found 2026-07-13).
_REMINDER_KEY = "dvic_upload_reminder"


def _load_reminder_state() -> dict:
    from api.src.database import get_reminder_state
    db = SessionLocal()
    try:
        raw = get_reminder_state(db, _REMINDER_KEY)
    finally:
        db.close()
    return {
        "last_reminded_date": date.fromisoformat(raw["last_reminded_date"]) if raw.get("last_reminded_date") else None,
        "last_reminded_at": datetime.fromisoformat(raw["last_reminded_at"]) if raw.get("last_reminded_at") else None,
        "reminder_count": raw.get("reminder_count", 0),
        "resolved_date": date.fromisoformat(raw["resolved_date"]) if raw.get("resolved_date") else None,
        "sent_final": date.fromisoformat(raw["sent_final"]) if raw.get("sent_final") else None,
    }


def _save_reminder_state(state: dict) -> None:
    from api.src.database import set_reminder_state
    db = SessionLocal()
    try:
        set_reminder_state(db, _REMINDER_KEY, {
            "last_reminded_date": state["last_reminded_date"].isoformat() if state.get("last_reminded_date") else None,
            "last_reminded_at": state["last_reminded_at"].isoformat() if state.get("last_reminded_at") else None,
            "reminder_count": state.get("reminder_count", 0),
            "resolved_date": state["resolved_date"].isoformat() if state.get("resolved_date") else None,
            "sent_final": state["sent_final"].isoformat() if state.get("sent_final") else None,
        })
    finally:
        db.close()


def run_dvic_upload_reminder() -> None:
    """
    Called every minute from the background loop.
    At 3:00–18:00 Pacific, if no DVIC file was uploaded today, remind #nday-mgt.
    Fires initial reminder at 15:00, then every 5 min until upload or 18:00.
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    today = now.date()
    _reminder_state = _load_reminder_state()

    # Only active 3 PM–6 PM Pacific
    if not (15 <= now.hour < 18):
        if now.hour >= 18 and _reminder_state["last_reminded_date"] == today:
            # Hit 6 PM with no upload — send final notice once
            if _reminder_state["reminder_count"] > 0 and _reminder_state.get("sent_final") != today:
                _reminder_state["sent_final"] = today
                _save_reminder_state(_reminder_state)
                _post(
                    NDAY_MGT_CHANNEL,
                    ":information_source: *DVIC Under-90 Report* — No file received from Amazon by 6:00 PM today. "
                    "No further reminders will be sent. Upload manually via "
                    f"<{APP_URL}/ops-ingest|Ops Ingest Monitor> if it becomes available.",
                )
        return

    # Already resolved today?
    if _reminder_state.get("resolved_date") == today:
        return

    # Check if file was uploaded today
    db = SessionLocal()
    try:
        snap = (
            db.query(DvicSnapshot)
            .filter(DvicSnapshot.imported_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
            .first()
        )
    finally:
        db.close()

    if snap:
        if _reminder_state["last_reminded_date"] == today and _reminder_state["reminder_count"] > 0:
            _post(
                NDAY_MGT_CHANNEL,
                f":white_check_mark: *DVIC Under-90 Report uploaded* — {snap.source_file} "
                f"({snap.unique_drivers} drivers, {snap.total_violations} violations). Reminders stopped.",
            )
        _reminder_state["resolved_date"] = today
        _reminder_state["reminder_count"] = 0
        _save_reminder_state(_reminder_state)
        return

    # Throttle to every 5 minutes
    last_remind = _reminder_state.get("last_reminded_at")
    if last_remind and (now - last_remind).total_seconds() < 300:
        return

    count = _reminder_state.get("reminder_count", 0) + 1
    _reminder_state["reminder_count"] = count
    _reminder_state["last_reminded_date"] = today
    _reminder_state["last_reminded_at"] = now
    _save_reminder_state(_reminder_state)

    page_link = f"👉 *<{APP_URL}/ops-ingest|Open Ops Ingest Monitor>*"
    if count == 1:
        msg = (
            ":bell: *DVIC Pre-Trip Under-90 Report* — Please upload this week's file to "
            f"<#{OPS_CHANNEL}|nday-operations-management>.\n"
            "This report tracks drivers who completed their vehicle inspection in under 90 seconds. "
            "Reminders will continue every 5 minutes until uploaded.\n"
            f"{page_link}"
        )
    else:
        msg = (
            f":bell: *(Reminder #{count})* DVIC Pre-Trip Under-90 Report not yet uploaded. "
            f"Please drop it in <#{OPS_CHANNEL}|nday-operations-management>.\n"
            f"{page_link}"
        )
    _post(NDAY_MGT_CHANNEL, msg)
    logger.info("DVIC upload reminder #%d sent to #nday-mgt", count)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accept a direct DVIC Excel upload."""
    content = await file.read()
    return _store_dvic(content, file.filename or "dvic.xlsx", None, db)


@router.post("/ingest-slack")
def ingest_slack(slack_file_id: str, file_url: str, filename: str, db: Session = Depends(get_db)):
    """Called by ops_ingest dispatcher — download and ingest from Slack."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise HTTPException(503, "SLACK_BOT_TOKEN not configured.")
    try:
        resp = requests.get(file_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        resp.raise_for_status()
        content = resp.content
    except Exception as exc:
        raise HTTPException(502, f"Could not download from Slack: {exc}")
    return _store_dvic(content, filename, slack_file_id, db)


def _mgt_summary_rows(snapshot_id: int, db: Session) -> list[dict]:
    """Per-driver name / avg inspection time / instance count / most recent
    inspection time (the "Naughty List") for one specific snapshot.

    Scoped by snapshot_id, not week — DVIC is a rolling trailing-7-day
    report re-uploaded daily under the SAME week label (e.g. four separate
    "2026-W29" snapshots on 07-14/15/16/19), so filtering by week alone
    would silently sum violation rows across every snapshot that shares
    that label, wildly over-counting the same real-world instances that
    reappear in each day's overlapping 7-day window."""
    violations = db.query(DvicViolation).filter(DvicViolation.snapshot_id == snapshot_id).all()
    by_driver: dict = defaultdict(list)
    for v in violations:
        by_driver[v.transporter_id].append(v)

    rows = []
    for tid, vrows in by_driver.items():
        durations = [v.duration_seconds for v in vrows if v.duration_seconds is not None]
        # "Most current" = the driver's latest inspection by start_time,
        # falling back to start_date if start_time is missing — lets
        # management see whether someone's trending better or worse, not
        # just their flat average.
        most_recent = max(
            vrows,
            key=lambda v: (v.start_time or datetime.min, v.start_date or date.min),
        )
        rows.append({
            "transporter_id": tid,
            "name": vrows[0].transporter_name or tid,
            "avg_seconds": round(sum(durations) / len(durations), 1) if durations else None,
            "instances": len(vrows),
            "most_recent_seconds": most_recent.duration_seconds,
        })
    rows.sort(key=lambda r: r["instances"], reverse=True)
    return rows


def post_dvic_naughty_list(snapshot_id: int, db: Session) -> dict:
    """Build and post the driver-name / avg-time / instance-count 'Naughty
    List' table to #nday-mgt for one specific snapshot. Shared by the
    manual /post-mgt-summary endpoint and the automatic post-ingest hook
    (ops_ingest.py's dvic dispatch) — per explicit 2026-07-20 decision,
    this is now a daily summary that fires automatically once per real new
    DVIC upload (driver DMs are a separate, not-yet-built, deliberately
    deferred future step)."""
    snap = db.query(DvicSnapshot).filter(DvicSnapshot.id == snapshot_id).first()
    if not snap:
        return {"status": "no_data"}

    rows = _mgt_summary_rows(snap.id, db)
    if not rows:
        return {"status": "no_data", "week": snap.week}

    week_label = _week_label(snap.week)
    lines = [f"{'Driver':<24} {'Instances':>10} {'Avg Time':>10} {'Most Recent':>12}"]
    for r in rows:
        avg = f"{r['avg_seconds']:.0f}s" if r["avg_seconds"] is not None else "—"
        recent = f"{r['most_recent_seconds']}s" if r["most_recent_seconds"] is not None else "—"
        lines.append(f"{r['name']:<24} {str(r['instances']):>10} {avg:>10} {recent:>12}")
    table = "```\n" + "\n".join(lines) + "\n```"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 DVIC Naughty List — {week_label}", "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Drivers with pre-trip inspections completed in under 90 seconds this week, sorted by instance count."}],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": table}},
    ]
    _post(NDAY_MGT_CHANNEL, f"DVIC Naughty List — {week_label}", blocks=blocks)

    return {"status": "posted", "week": snap.week, "driver_count": len(rows)}


@router.post("/post-mgt-summary")
def post_mgt_summary(req: DmRequest, db: Session = Depends(get_db)):
    """Manual trigger — post the Naughty List for a given week (or the
    latest snapshot if no week given). See post_dvic_naughty_list() for
    the shared logic; this also runs automatically on ingest now."""
    query = db.query(DvicSnapshot)
    if req.week:
        query = query.filter(DvicSnapshot.week == req.week)
    snap = query.order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
    if not snap:
        raise HTTPException(404, "No DVIC data ingested.")
    return post_dvic_naughty_list(snap.id, db)


@router.get("/weeks")
def list_weeks(db: Session = Depends(get_db)):
    snaps = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc(), DvicSnapshot.imported_at.desc()).all()
    return [
        {
            "week": s.week,
            "source_file": s.source_file,
            "total_violations": s.total_violations,
            "unique_drivers": s.unique_drivers,
            "imported_at": s.imported_at.isoformat() if s.imported_at else None,
        }
        for s in snaps
    ]


@router.get("/violations")
def get_violations(week: Optional[str] = None, db: Session = Depends(get_db)):
    """Return violations grouped by driver for a given week (default: the
    single most-recently-imported snapshot overall — NOT a fresh
    unordered lookup by week string, which would silently pick an
    arbitrary one of several same-week snapshots re-uploaded on
    different days)."""
    query = db.query(DvicSnapshot)
    if week:
        query = query.filter(DvicSnapshot.week == week)
    snap = query.order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
    if not snap:
        if week:
            raise HTTPException(404, f"No DVIC snapshot for week {week}")
        return {"week": None, "drivers": [], "message": "No DVIC data ingested yet."}
    week = snap.week

    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.snapshot_id == snap.id)
        .order_by(DvicViolation.transporter_name, DvicViolation.start_date)
        .all()
    )

    # Group by driver
    by_driver: dict = defaultdict(list)
    for v in violations:
        by_driver[v.transporter_id].append(v)

    # Fetch acknowledgments for this week
    ack_ids = {
        a.transporter_id
        for a in db.query(DvicAcknowledgment)
        .filter(DvicAcknowledgment.week == week)
        .all()
    }

    drivers = []
    for tid, vrows in by_driver.items():
        name = vrows[0].transporter_name or tid
        roster = _find_roster_entry(name, db)
        durations = [v.duration_seconds for v in vrows if v.duration_seconds is not None]
        drivers.append({
            "transporter_id": tid,
            "transporter_name": name,
            "violation_count": len(vrows),
            "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
            "min_duration_seconds": min(durations) if durations else None,
            "fleet_types": list(set(v.fleet_type for v in vrows if v.fleet_type)),
            "dates": sorted(set(str(v.start_date) for v in vrows if v.start_date)),
            "dm_sent": vrows[0].snapshot_id is not None and roster is not None,  # placeholder
            "acknowledged": tid in ack_ids,
            "slack_member_id": roster.slack_member_id if roster else None,
        })

    drivers.sort(key=lambda d: d["violation_count"], reverse=True)

    return {
        "week": week,
        "snapshot_id": snap.id,
        "total_violations": snap.total_violations,
        "unique_drivers": snap.unique_drivers,
        "date_range_start": str(snap.date_range_start) if snap.date_range_start else None,
        "date_range_end": str(snap.date_range_end) if snap.date_range_end else None,
        "imported_at": snap.imported_at.isoformat() if snap.imported_at else None,
        "drivers": drivers,
    }


@router.get("/violations/{transporter_id}")
def driver_history(transporter_id: str, db: Session = Depends(get_db)):
    """All DVIC violations for a single driver across all weeks."""
    rows = (
        db.query(DvicViolation)
        .filter(DvicViolation.transporter_id == transporter_id)
        .order_by(DvicViolation.week.desc(), DvicViolation.start_date.desc())
        .all()
    )
    if not rows:
        raise HTTPException(404, f"No DVIC records for transporter {transporter_id}")

    by_week: dict = defaultdict(list)
    for v in rows:
        by_week[v.week or "unknown"].append({
            "id": v.id,
            "start_date": str(v.start_date) if v.start_date else None,
            "vin": v.vin,
            "fleet_type": v.fleet_type,
            "duration_seconds": v.duration_seconds,
            "start_time": v.start_time.isoformat() if v.start_time else None,
        })

    acks = {
        a.week: a.acknowledged_at.isoformat()
        for a in db.query(DvicAcknowledgment)
        .filter(DvicAcknowledgment.transporter_id == transporter_id)
        .all()
    }

    return {
        "transporter_id": transporter_id,
        "transporter_name": rows[0].transporter_name,
        "weeks": [
            {
                "week": w,
                "violation_count": len(vs),
                "acknowledged": w in acks,
                "acknowledged_at": acks.get(w),
                "violations": vs,
            }
            for w, vs in sorted(by_week.items(), reverse=True)
        ],
    }


class DmRequest(BaseModel):
    week: Optional[str] = None


@router.post("/send-dm/{transporter_id}")
def send_dm(transporter_id: str, req: DmRequest, db: Session = Depends(get_db)):
    """Advance counseling stage + send the safety DM to one flagged driver.
    Gated by DRIVER_DM_ACTIVE (see _process_week)."""
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    return _process_week(week, db, only_tid=transporter_id)


@router.post("/send-all-dms")
def send_all_dms(req: DmRequest, db: Session = Depends(get_db)):
    """Advance counseling stage + DM every flagged driver for a week.
    Gated by DRIVER_DM_ACTIVE (see _process_week)."""
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    return _process_week(week, db)


@router.post("/refresh-ack-summary")
def refresh_ack_summary_endpoint(req: DmRequest, db: Session = Depends(get_db)):
    """Manual/on-demand refresh of the consolidated DVIC acknowledgment
    summary for a week (defaults to the latest ingested week)."""
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week
    return refresh_dvic_ack_summary(week, db)


@router.get("/acknowledgments")
def list_acknowledgments(week: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(DvicAcknowledgment)
    if week:
        q = q.filter(DvicAcknowledgment.week == week)
    acks = q.order_by(DvicAcknowledgment.acknowledged_at.desc()).all()
    return [
        {
            "id": a.id,
            "transporter_id": a.transporter_id,
            "transporter_name": a.transporter_name,
            "week": a.week,
            "violation_count": a.violation_count,
            "signature_name": a.signature_name,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        }
        for a in acks
    ]


class AcknowledgeRequest(BaseModel):
    transporter_id: str
    week: str
    signature_name: str


def record_acknowledgment(transporter_id: str, week: str, signature_name: str, db: Session) -> dict:
    """Shared by the /acknowledge web endpoint and the Slack `dvic_ack` button
    handler (slack_interactions.py) — keeps DvicAcknowledgment (historical
    record) and DvicCounselingRecord.ack_status (current stage state) in sync."""
    existing = (
        db.query(DvicAcknowledgment)
        .filter(
            DvicAcknowledgment.transporter_id == transporter_id,
            DvicAcknowledgment.week == week,
        )
        .first()
    )
    if existing:
        return {
            "status": "already_acknowledged",
            "transporter_name": existing.transporter_name,
            "acknowledged_at": existing.acknowledged_at.isoformat(),
        }

    record_check = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == transporter_id
    ).first()
    if (
        DVIC_TRAINING_VIDEO_ACTIVE
        and record_check
        and record_check.stage >= DVIC_VIDEO_GATE_MIN_STAGE
        and record_check.video_watched_at is None
    ):
        # Belt-and-suspenders: _dm_blocks() shouldn't even show an
        # Acknowledge button in this state, but this endpoint must not
        # trust the client either.
        return {"status": "video_not_watched", "transporter_name": record_check.transporter_name}

    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.transporter_id == transporter_id, DvicViolation.week == week)
        .all()
    )
    name = violations[0].transporter_name if violations else transporter_id
    now = datetime.now(timezone.utc)

    ack = DvicAcknowledgment(
        transporter_id=transporter_id,
        transporter_name=name,
        week=week,
        violation_count=len(violations),
        signature_name=signature_name.strip(),
        acknowledged_at=now,
    )
    db.add(ack)

    record = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == transporter_id
    ).first()
    if record:
        record.ack_status = "acknowledged"
        record.acknowledged_at = now

    db.commit()

    # Consolidated summary, updated in place — NOT a per-driver post.
    # With 75+ drivers acknowledging over the same day or two, a message
    # per ack would bury #nday-operations-management (flagged explicitly
    # 2026-07-23). Same pattern as rostering.py's DM-response summaries.
    refresh_dvic_ack_summary(week, db)

    return {
        "status": "acknowledged",
        "transporter_id": transporter_id,
        "transporter_name": name,
        "week": week,
        "violation_count": len(violations),
        "signature_name": signature_name,
        "acknowledged_at": now.isoformat(),
    }


def refresh_dvic_ack_summary(week: str, db: Session) -> dict:
    """Consolidated #nday-operations-management summary of DVIC
    acknowledgment status for `week`: ✅ acknowledged / ⏳ pending. One
    message per week, updated in place (chat_update) rather than a new
    post per driver — added 2026-07-23 so 75+ drivers acknowledging over
    a day or two doesn't bury the channel."""
    records = db.query(DvicCounselingRecord).filter(DvicCounselingRecord.last_week == week).all()
    if not records:
        return {"status": "no_records", "week": week}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    acknowledged = [r for r in records if r.ack_status == "acknowledged"]
    pending = [r for r in records if r.ack_status != "acknowledged"]

    week_label = _week_label(week)
    lines = [f"✅ {len(acknowledged)} acknowledged  ·  ⏳ {len(pending)} pending"]
    if pending:
        pending_sorted = sorted(pending, key=lambda r: (-(r.stage or 0), r.transporter_name or ""))
        lines.append("\n*⏳ Pending:*\n" + "\n".join(
            f"• {r.transporter_name} (Stage {r.stage})" for r in pending_sorted
        ))

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 DVIC Acknowledgments — {week_label}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Updated {datetime.utcnow().strftime('%H:%M UTC')}_"}]},
    ]

    state_key = f"dvic_ack_summary_{week}"
    state = get_reminder_state(db, state_key)
    existing_ts = state.get("ts")

    try:
        if existing_ts:
            client.chat_update(channel=OPS_CHANNEL, ts=existing_ts, text=f"DVIC Acknowledgments — {week_label}", blocks=blocks)
            ts = existing_ts
        else:
            resp = client.chat_postMessage(channel=OPS_CHANNEL, text=f"DVIC Acknowledgments — {week_label}", blocks=blocks)
            ts = resp.get("ts")
        set_reminder_state(db, state_key, {"ts": ts})
        return {"status": "ok", "acknowledged": len(acknowledged), "pending": len(pending)}
    except Exception as exc:
        logger.warning("DVIC ack summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


@router.post("/acknowledge")
def acknowledge(req: AcknowledgeRequest, db: Session = Depends(get_db)):
    """Driver submits digital acknowledgment of their DVIC violations (web form path)."""
    result = record_acknowledgment(req.transporter_id, req.week, req.signature_name, db)
    if result.get("status") == "video_not_watched":
        raise HTTPException(403, "Please watch the training video before acknowledging.")
    return result


class CounselingSignRequest(BaseModel):
    signed_by: str


@router.post("/counseling/{record_id}/sign")
def sign_counseling_record(
    record_id: int, req: CounselingSignRequest, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("ops_manager")),
):
    """Ops-manager sign-off on a formal DVIC write-up — separate from the
    driver's own ack_status. Part of the write-up review dashboard (see
    manager_accountability.py's discipline_tracker())."""
    record = db.query(DvicCounselingRecord).filter(DvicCounselingRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, "Counseling record not found.")
    record.manager_signature_name = req.signed_by
    record.manager_signature_at = datetime.utcnow()
    db.commit()
    return {
        "status": "signed",
        "id": record.id,
        "manager_signature_name": record.manager_signature_name,
        "manager_signature_at": record.manager_signature_at.isoformat(),
    }


@router.get("/violations-for-ack/{transporter_id}")
def violations_for_ack(transporter_id: str, week: str, db: Session = Depends(get_db)):
    """Public endpoint — returns violation summary for the acknowledgment page."""
    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.transporter_id == transporter_id, DvicViolation.week == week)
        .order_by(DvicViolation.start_date)
        .all()
    )
    if not violations:
        raise HTTPException(404, "No violations found. The link may be expired or incorrect.")

    existing_ack = (
        db.query(DvicAcknowledgment)
        .filter(
            DvicAcknowledgment.transporter_id == transporter_id,
            DvicAcknowledgment.week == week,
        )
        .first()
    )

    return {
        "transporter_id": transporter_id,
        "transporter_name": violations[0].transporter_name,
        "week": week,
        "violation_count": len(violations),
        "violations": [
            {
                "start_date": str(v.start_date) if v.start_date else None,
                "duration_seconds": v.duration_seconds,
                "fleet_type": v.fleet_type,
                "vin": v.vin,
            }
            for v in violations
        ],
        "already_acknowledged": existing_ack is not None,
        "acknowledged_at": existing_ack.acknowledged_at.isoformat() if existing_ack else None,
    }


@router.get("/upload-status")
def upload_status(db: Session = Depends(get_db)):
    """Check whether any DVIC file has been uploaded today."""
    from datetime import datetime, date
    today = date.today()
    snap = (
        db.query(DvicSnapshot)
        .filter(DvicSnapshot.imported_at >= datetime(today.year, today.month, today.day))
        .first()
    )
    latest = db.query(DvicSnapshot).order_by(DvicSnapshot.imported_at.desc(), DvicSnapshot.id.desc()).first()
    return {
        "uploaded_today": snap is not None,
        "latest_week": latest.week if latest else None,
        "latest_imported_at": latest.imported_at.isoformat() if latest else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Forced training video (Stage 2+) — added 2026-07-23, behind
# DVIC_TRAINING_VIDEO_ACTIVE (off until a real video exists, see CLAUDE.md).
# One video at a time, stored in S3 (api/src/storage.py) with its key kept
# in ReminderThrottleState under DVIC_TRAINING_VIDEO_STATE_KEY rather than
# a dedicated table — there's only ever one active video.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/training-video")
def upload_training_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("ops_manager", "manager")),
):
    """Upload (or replace) the single active DVIC training video. Requires
    AWS_S3_BUCKET to be configured (api/src/storage.py) — this endpoint
    exists ahead of DVIC_TRAINING_VIDEO_ACTIVE being turned on, so the
    video can be staged and reviewed before the gate goes live."""
    from api.src import storage
    if not storage.is_configured():
        raise HTTPException(503, "Video storage is not configured (AWS_S3_BUCKET missing).")

    data = file.file.read()
    ext = os.path.splitext(file.filename or "")[1] or ".mp4"
    key = storage.build_key("dvic_training", f"training{ext}")
    storage.upload_bytes(data, key, content_type=file.content_type or "video/mp4")
    set_reminder_state(db, DVIC_TRAINING_VIDEO_STATE_KEY, {"key": key, "uploaded_at": datetime.utcnow().isoformat()})
    return {"status": "uploaded", "key": key}


@router.get("/training-video-url")
def training_video_url(db: Session = Depends(get_db)):
    """Redirect to a short-lived presigned S3 URL for the active training
    video — same pattern as crash_report.py's get_photo_url(). S3 honors
    Range/Content-Range natively on presigned GETs, so this gives a
    <video> tag correct seek support with no streaming code here."""
    from api.src import storage
    from fastapi.responses import RedirectResponse
    state = get_reminder_state(db, DVIC_TRAINING_VIDEO_STATE_KEY)
    key = state.get("key")
    if not key:
        raise HTTPException(404, "No training video uploaded yet.")
    return RedirectResponse(storage.presigned_url(key))


@router.get("/training-status-by-token")
def training_status_by_token(token: str, db: Session = Depends(get_db)):
    """Resolve a signed dvic_video token (baked into the DM's 'Watch
    Training Video' button) into the driver's current watch status, for
    frontend/pages/dvic-training.tsx to render on load — no PIN, link
    possession is identity, same trust model as eod_survey.py's
    _authenticate_driver()."""
    claims = _verify_dvic_video_token(token)
    if not claims:
        raise HTTPException(401, "This link has expired or is invalid.")
    transporter_id = claims["transporter_id"]
    record = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == transporter_id
    ).first()
    if not record:
        raise HTTPException(404, "No counseling record found.")
    return {
        "transporter_id": transporter_id,
        "transporter_name": record.transporter_name,
        "week": claims.get("week"),
        "stage": record.stage,
        "video_watched_at": record.video_watched_at.isoformat() if record.video_watched_at else None,
        "video_started_at": record.video_started_at.isoformat() if record.video_started_at else None,
        "min_watch_seconds": DVIC_VIDEO_MIN_WATCH_SECONDS,
    }


class TrainingVideoTokenRequest(BaseModel):
    token: str


@router.post("/training-video-started")
def training_video_started(req: TrainingVideoTokenRequest, db: Session = Depends(get_db)):
    """Frontend calls this once, when the driver actually opens the
    training page and the video is ready to play — starts the minimum-
    elapsed-time clock. Idempotent: does not reset an already-running
    timer (a reload/re-open shouldn't let someone restart the clock)."""
    claims = _verify_dvic_video_token(req.token)
    if not claims:
        raise HTTPException(401, "This link has expired or is invalid.")
    transporter_id = claims["transporter_id"]
    record = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == transporter_id
    ).first()
    if not record:
        raise HTTPException(404, "No counseling record found.")
    if not record.video_started_at:
        record.video_started_at = datetime.utcnow()
        db.commit()
    return {"status": "started", "video_started_at": record.video_started_at.isoformat(), "min_watch_seconds": DVIC_VIDEO_MIN_WATCH_SECONDS}


@router.post("/training-video-watched")
def training_video_watched(req: TrainingVideoTokenRequest, db: Session = Depends(get_db)):
    """Frontend calls this once the video has played through AND the
    driver has confirmed they understand the consequence of a repeat.
    Server-side enforces a real minimum elapsed time since
    training-video-started was called — never trusts the client alone,
    since scrubbing straight to the end would otherwise fire the
    <video> 'ended' event immediately."""
    claims = _verify_dvic_video_token(req.token)
    if not claims:
        raise HTTPException(401, "This link has expired or is invalid.")
    transporter_id = claims["transporter_id"]
    record = db.query(DvicCounselingRecord).filter(
        DvicCounselingRecord.transporter_id == transporter_id
    ).first()
    if not record:
        raise HTTPException(404, "No counseling record found.")
    if not record.video_started_at:
        raise HTTPException(400, "Video hasn't been started yet.")
    elapsed = (datetime.utcnow() - record.video_started_at).total_seconds()
    if elapsed < DVIC_VIDEO_MIN_WATCH_SECONDS:
        remaining = int(DVIC_VIDEO_MIN_WATCH_SECONDS - elapsed)
        raise HTTPException(400, f"Please watch the full video — {remaining} more second(s) required.")
    record.video_watched_at = datetime.utcnow()
    db.commit()
    return {"status": "watched", "video_watched_at": record.video_watched_at.isoformat()}
