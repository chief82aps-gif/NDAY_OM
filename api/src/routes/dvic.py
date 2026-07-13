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
from collections import defaultdict
from datetime import datetime, date, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal,
    DvicSnapshot, DvicViolation, DvicAcknowledgment, DvicCounselingRecord,
    DriverRosterEntry,
)
from api.src.ingest.dvic import parse_dvic_xlsx, extract_week

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
    if not transporter_name:
        return None
    target = _name_tokens(transporter_name)
    best: Optional[DriverRosterEntry] = None
    best_score = 0
    for entry in db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all():
        score = len(target & _name_tokens(entry.payroll_name))
        if score > best_score:
            best_score = score
            best = entry
    return best if best_score >= 1 else None


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
            f":wave: Hi {first}, this is a friendly safety check-in from NDAY Management.\n\n"
            f"In the past week you completed *{count} pre-trip vehicle inspection{plural}* in under 90 seconds "
            f"({week_label}).\n\n"
            "Amazon requires a *minimum of 90 seconds* to properly inspect your vehicle before every shift — "
            "it's there to protect *you*. A rushed inspection can miss a real safety issue before you're out on the road. "
            "We know you can do this, and we just want to make sure you're taking the full time every shift.\n\n"
            "Please tap *Acknowledge* below to confirm you've seen this."
        )
    elif stage == 2:
        return (
            f":memo: *Written Safety Notice — {first}*\n\n"
            f"This is your second safety notice. In the past week you completed *{count} pre-trip inspection{plural}* "
            f"in under 90 seconds ({week_label}).\n\n"
            "This is now being documented as a formal coaching notice. A rushed pre-trip inspection is a genuine "
            "safety risk — to you, your vehicle, and everyone around you on the road. We need to see this change.\n\n"
            "Please tap *Acknowledge* below — your acknowledgment is recorded in your driver file."
        )
    elif stage == 3:
        return (
            f":warning: *Final Warning — {first}*\n\n"
            f"This is your *final warning* regarding pre-trip inspection times. In the past week you completed "
            f"*{count} pre-trip inspection{plural}* in under 90 seconds ({week_label}).\n\n"
            "This pattern has continued despite prior notices and is a serious safety concern. Your manager has "
            "been copied on this notice. Continued violations will result in formal disciplinary action.\n\n"
            "Please tap *Acknowledge* below — your acknowledgment is recorded in your driver file."
        )
    else:
        return (
            f":rotating_light: *Formal Write-Up — {first}*\n\n"
            f"In the past week you completed *{count} pre-trip inspection{plural}* in under 90 seconds ({week_label}), "
            "continuing a pattern despite prior safety notices and a final warning.\n\n"
            "This is a *formal written disciplinary action*, routed to NDAY Management for review. "
            "Continued violations may result in further disciplinary action up to and including termination.\n\n"
            "Please tap *Acknowledge* below to confirm you've received this notice."
        )


def _dm_blocks(stage: int, name: str, count: int, week: str, transporter_id: str) -> list:
    text = _counseling_message(stage, name, count, week)
    value = json.dumps({"transporter_id": transporter_id, "week": week, "stage": stage})
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledge", "emoji": True},
                    "style": "primary",
                    "action_id": "dvic_ack",
                    "value": value,
                }
            ],
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
    """Advance counseling stage + send the Slack DM for each flagged driver
    in `week`'s report.

    Gated by DRIVER_DM_ACTIVE — a no-op otherwise, so the counseling ladder
    can never silently advance while driver DMs are switched off.
    """
    if not _DM_ACTIVE:
        return {"status": "inactive", "note": "Set DRIVER_DM_ACTIVE=true on Render to enable driver DMs"}

    violations = db.query(DvicViolation).filter(DvicViolation.week == week).all()
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

        blocks = _dm_blocks(record.stage, name, len(vrows), week, tid)
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
                    "No further reminders will be sent. Upload manually via `/ops-ingest` if it becomes available.",
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

    if count == 1:
        msg = (
            ":bell: *DVIC Pre-Trip Under-90 Report* — Please upload this week's file to "
            f"<#{OPS_CHANNEL}|nday-operations-management>.\n"
            "This report tracks drivers who completed their vehicle inspection in under 90 seconds. "
            "Reminders will continue every 5 minutes until uploaded."
        )
    else:
        msg = (
            f":bell: *(Reminder #{count})* DVIC Pre-Trip Under-90 Report not yet uploaded. "
            f"Please drop it in <#{OPS_CHANNEL}|nday-operations-management>."
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


def _mgt_summary_rows(week: str, db: Session) -> list[dict]:
    """Per-driver name / avg inspection time / instance count for a week."""
    violations = db.query(DvicViolation).filter(DvicViolation.week == week).all()
    by_driver: dict = defaultdict(list)
    for v in violations:
        by_driver[v.transporter_id].append(v)

    rows = []
    for tid, vrows in by_driver.items():
        durations = [v.duration_seconds for v in vrows if v.duration_seconds is not None]
        rows.append({
            "transporter_id": tid,
            "name": vrows[0].transporter_name or tid,
            "avg_seconds": round(sum(durations) / len(durations), 1) if durations else None,
            "instances": len(vrows),
        })
    rows.sort(key=lambda r: r["instances"], reverse=True)
    return rows


@router.post("/post-mgt-summary")
def post_mgt_summary(req: DmRequest, db: Session = Depends(get_db)):
    """Post a driver-name / avg-time / instance-count summary table to
    #nday-mgt for a week. Explicit action — never fired automatically on
    ingest (see column_mapping/rostering fix history: ingest must only
    update data, posting is a separate, deliberate step)."""
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    rows = _mgt_summary_rows(week, db)
    if not rows:
        return {"status": "no_data", "week": week}

    week_label = _week_label(week)
    lines = [f"{'Driver':<24} {'Avg Time':>10} {'Instances':>10}"]
    for r in rows:
        avg = f"{r['avg_seconds']:.0f}s" if r["avg_seconds"] is not None else "—"
        lines.append(f"{r['name']:<24} {avg:>10} {str(r['instances']):>10}")
    table = "```\n" + "\n".join(lines) + "\n```"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"DVIC Under-90 Summary — {week_label}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": table}},
    ]
    _post(NDAY_MGT_CHANNEL, f"DVIC Under-90 Summary — {week_label}", blocks=blocks)

    return {"status": "posted", "week": week, "driver_count": len(rows)}


@router.get("/weeks")
def list_weeks(db: Session = Depends(get_db)):
    snaps = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).all()
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
    """Return violations grouped by driver for a given week (default: latest)."""
    if not week:
        latest = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
        if not latest:
            return {"week": None, "drivers": [], "message": "No DVIC data ingested yet."}
        week = latest.week

    snap = db.query(DvicSnapshot).filter(DvicSnapshot.week == week).first()
    if not snap:
        raise HTTPException(404, f"No DVIC snapshot for week {week}")

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
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
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
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    return _process_week(week, db)


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

    _post(
        OPS_CHANNEL,
        f":pencil: *DVIC Acknowledgment* — {name} signed for {week} "
        f"({len(violations)} violation{'s' if len(violations) != 1 else ''}). "
        f"Signature: \"{signature_name}\"",
    )

    return {
        "status": "acknowledged",
        "transporter_id": transporter_id,
        "transporter_name": name,
        "week": week,
        "violation_count": len(violations),
        "signature_name": signature_name,
        "acknowledged_at": now.isoformat(),
    }


@router.post("/acknowledge")
def acknowledge(req: AcknowledgeRequest, db: Session = Depends(get_db)):
    """Driver submits digital acknowledgment of their DVIC violations (web form path)."""
    return record_acknowledgment(req.transporter_id, req.week, req.signature_name, db)


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
    latest = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
    return {
        "uploaded_today": snap is not None,
        "latest_week": latest.week if latest else None,
        "latest_imported_at": latest.imported_at.isoformat() if latest else None,
    }
