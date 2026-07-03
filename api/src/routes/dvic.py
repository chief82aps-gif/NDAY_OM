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
    DvicSnapshot, DvicViolation, DvicAcknowledgment,
    DriverRosterEntry,
)
from api.src.ingest.dvic import parse_dvic_xlsx, extract_week

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dvic", tags=["dvic"])

NDAY_MGT_CHANNEL = os.getenv("NDAY_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
OPS_CHANNEL      = os.getenv("OPS_CHANNEL_ID",  "C0BE4ALL1EX")    # #nday-operations-management
APP_URL          = os.getenv("APP_URL", "https://nday-om.vercel.app")


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
# DM escalation messages
# ─────────────────────────────────────────────────────────────────────────────

def _dm_message(name: str, count: int, week: str, ack_url: str) -> str:
    first = name.split()[0] if name else "Driver"
    week_label = week.replace("-W", " Week ").replace("2026 Week ", "Week ")

    if count <= 2:
        return (
            f":wave: Hi {first}, this is a safety notice from NDAY Management.\n\n"
            f"Our records show *{count} pre-trip vehicle inspection(s)* completed in under 90 seconds "
            f"during {week_label}.\n\n"
            "Amazon requires a *minimum of 90 seconds* to properly inspect your vehicle before every shift. "
            "A rushed inspection puts *you*, your packages, and others on the road at risk. "
            "Your safety is our top priority.\n\n"
            f"Please review and acknowledge this notice here:\n{ack_url}"
        )
    elif count <= 4:
        return (
            f":warning: *Safety Notice — {first}*\n\n"
            f"Our records show *{count} pre-trip inspections* completed in under 90 seconds during {week_label}.\n\n"
            "This is a serious safety concern that requires your immediate attention. "
            "A vehicle inspection that is rushed may miss critical safety issues. "
            "Continued pattern may result in a formal coaching session.\n\n"
            "Please acknowledge this notice immediately:\n"
            f"{ack_url}"
        )
    else:
        return (
            f":rotating_light: *Urgent Safety Notice — {first}*\n\n"
            f"You have *{count} recorded instances* of completing your pre-trip vehicle inspection "
            f"in under 90 seconds during {week_label}.\n\n"
            "This level of pattern requires a *formal written acknowledgment* and will result in a coaching session. "
            "Failure to acknowledge may result in progressive disciplinary action.\n\n"
            "*Please acknowledge this notice immediately:*\n"
            f"{ack_url}"
        )


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

# Module-level state so we don't spam per loop iteration
_reminder_state: dict = {"last_reminded_date": None, "reminder_count": 0, "resolved_date": None}


def run_dvic_upload_reminder() -> None:
    """
    Called every minute from the background loop.
    At 3:00–18:00 Pacific, if no DVIC file was uploaded today, remind #nday-mgt.
    Fires initial reminder at 15:00, then every 5 min until upload or 18:00.
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    today = now.date()

    # Only active 3 PM–6 PM Pacific
    if not (15 <= now.hour < 18):
        if now.hour >= 18 and _reminder_state["last_reminded_date"] == today:
            # Hit 6 PM with no upload — send final notice once
            if _reminder_state["reminder_count"] > 0 and _reminder_state.get("sent_final") != today:
                _reminder_state["sent_final"] = today
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
        return

    # Throttle to every 5 minutes
    last_remind = _reminder_state.get("last_reminded_at")
    if last_remind and (now - last_remind).total_seconds() < 300:
        return

    count = _reminder_state.get("reminder_count", 0) + 1
    _reminder_state["reminder_count"] = count
    _reminder_state["last_reminded_date"] = today
    _reminder_state["last_reminded_at"] = now

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
    """Send a safety DM to one flagged driver."""
    # Get latest week if not specified
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.transporter_id == transporter_id, DvicViolation.week == week)
        .all()
    )
    if not violations:
        raise HTTPException(404, f"No violations for {transporter_id} in week {week}")

    name = violations[0].transporter_name or transporter_id
    roster = _find_roster_entry(name, db)

    if not roster or not roster.slack_member_id:
        return {"status": "no_slack_id", "driver": name, "message": "Driver has no Slack member ID in roster."}

    ack_url = f"{APP_URL}/dvic-ack?tid={transporter_id}&week={week}"
    msg = _dm_message(name, len(violations), week, ack_url)
    sent = _dm(roster.slack_member_id, msg)

    return {
        "status": "sent" if sent else "failed",
        "driver": name,
        "transporter_id": transporter_id,
        "violation_count": len(violations),
        "week": week,
        "slack_member_id": roster.slack_member_id,
    }


@router.post("/send-all-dms")
def send_all_dms(req: DmRequest, db: Session = Depends(get_db)):
    """DM all flagged drivers for the latest (or specified) week."""
    week = req.week
    if not week:
        snap = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
        if not snap:
            raise HTTPException(404, "No DVIC data ingested.")
        week = snap.week

    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.week == week)
        .all()
    )

    by_driver: dict = defaultdict(list)
    for v in violations:
        by_driver[v.transporter_id].append(v)

    results = []
    sent_count = 0
    for tid, vrows in by_driver.items():
        name = vrows[0].transporter_name or tid
        roster = _find_roster_entry(name, db)
        if not roster or not roster.slack_member_id:
            results.append({"driver": name, "status": "no_slack_id"})
            continue

        ack_url = f"{APP_URL}/dvic-ack?tid={tid}&week={week}"
        msg = _dm_message(name, len(vrows), week, ack_url)
        ok = _dm(roster.slack_member_id, msg)
        results.append({"driver": name, "status": "sent" if ok else "failed", "violations": len(vrows)})
        if ok:
            sent_count += 1

    return {
        "week": week,
        "total_drivers": len(by_driver),
        "sent": sent_count,
        "results": results,
    }


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


@router.post("/acknowledge")
def acknowledge(req: AcknowledgeRequest, db: Session = Depends(get_db)):
    """Driver submits digital acknowledgment of their DVIC violations."""
    existing = (
        db.query(DvicAcknowledgment)
        .filter(
            DvicAcknowledgment.transporter_id == req.transporter_id,
            DvicAcknowledgment.week == req.week,
        )
        .first()
    )
    if existing:
        return {
            "status": "already_acknowledged",
            "acknowledged_at": existing.acknowledged_at.isoformat(),
        }

    violations = (
        db.query(DvicViolation)
        .filter(DvicViolation.transporter_id == req.transporter_id, DvicViolation.week == req.week)
        .all()
    )
    name = violations[0].transporter_name if violations else req.transporter_id

    ack = DvicAcknowledgment(
        transporter_id=req.transporter_id,
        transporter_name=name,
        week=req.week,
        violation_count=len(violations),
        signature_name=req.signature_name.strip(),
        acknowledged_at=datetime.now(timezone.utc),
    )
    db.add(ack)
    db.commit()

    # Notify ops channel
    _post(
        OPS_CHANNEL,
        f":pencil: *DVIC Acknowledgment* — {name} signed for {req.week} "
        f"({len(violations)} violation{'s' if len(violations) != 1 else ''}). "
        f"Signature: \"{req.signature_name}\"",
    )

    return {
        "status": "acknowledged",
        "transporter_id": req.transporter_id,
        "transporter_name": name,
        "week": req.week,
        "violation_count": len(violations),
        "signature_name": req.signature_name,
        "acknowledged_at": ack.acknowledged_at.isoformat(),
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
    latest = db.query(DvicSnapshot).order_by(DvicSnapshot.week.desc()).first()
    return {
        "uploaded_today": snap is not None,
        "latest_week": latest.week if latest else None,
        "latest_imported_at": latest.imported_at.isoformat() if latest else None,
    }
