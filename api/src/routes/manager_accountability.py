"""
Manager Accountability Module
==============================
Tracks which manager is on duty each day and issues morning accountability
notices (via Slack DM) when required reviews were not completed by EOD.

Schedule:
  Spencer Colby  — Sun / Mon / Tue / Wed  (front half)  U0BE493C5K9
  Galo (Fabian)  — Wed / Thu / Fri / Sat  (back half)   U0AJPQALDLL

Wednesday is shared — both managers receive notices on overlap days.

Adding new writeup catalysts:
  Call POST /manager-accountability/eod-scan with writeup_type and a list
  of source_event_ids. The module handles all DM scheduling. No new loop needed.

See Governance/MANAGER_ACCOUNTABILITY_RULES.md for full rule set.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, date, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Date, DateTime, Text
from sqlalchemy.orm import Session

from api.src.database import Base, engine, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/manager-accountability", tags=["manager-accountability"])

PACIFIC = ZoneInfo("America/Los_Angeles")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")

# ─────────────────────────────────────────────────────────────────────────────
# Manager schedule  (day_of_week: 0=Mon … 6=Sun, matching Python isoweekday-1)
# ─────────────────────────────────────────────────────────────────────────────

MANAGERS: list[dict] = [
    {
        "name": "Spencer Colby",
        "slack_id": "U0BE493C5K9",
        # isoweekday: Mon=1 Tue=2 Wed=3 Thu=4 Fri=5 Sat=6 Sun=7
        "days": {7, 1, 2, 3},   # Sun Mon Tue Wed
        "half": "front",
    },
    {
        "name": "Galo (Fabian Marcillo)",
        "slack_id": "U0AJPQALDLL",
        "days": {3, 4, 5, 6},   # Wed Thu Fri Sat
        "half": "back",
    },
]


def _on_duty_managers(for_date: date) -> list[dict]:
    """Return the manager(s) scheduled on a given date."""
    dow = for_date.isoweekday()   # Mon=1 … Sun=7
    return [m for m in MANAGERS if dow in m["days"]]


# ─────────────────────────────────────────────────────────────────────────────
# Database model
# ─────────────────────────────────────────────────────────────────────────────

class ManagerAccountabilityEvent(Base):
    """One row per accountability notice issued to a manager."""
    __tablename__ = "manager_accountability_events"

    id = Column(Integer, primary_key=True)
    shift_date     = Column(Date, nullable=False, index=True)
    manager_name   = Column(String(150), nullable=False)
    manager_slack_id = Column(String(50))
    writeup_type   = Column(String(80), nullable=False)   # e.g. "unsigned_callout"
    source_event_id = Column(Integer, index=True)         # FK to the original unreviewed event
    source_detail  = Column(Text)                         # human-readable description
    dm_sent_at     = Column(DateTime)
    acknowledged_at = Column(DateTime)
    created_at     = Column(DateTime, default=datetime.utcnow)


def _ensure_tables():
    Base.metadata.create_all(bind=engine, tables=[ManagerAccountabilityEvent.__table__])


# ─────────────────────────────────────────────────────────────────────────────
# Slack helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_dm(slack_id: str, text: str, blocks: list) -> bool:
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            logger.warning("SLACK_BOT_TOKEN not set — skipping manager DM")
            return False
        client = WebClient(token=token)
        client.chat_postMessage(channel=slack_id, text=text, blocks=blocks)
        return True
    except Exception as exc:
        logger.warning("Manager DM failed to %s: %s", slack_id, exc)
        return False


def _build_dm_blocks(manager_name: str, shift_date: date, notices: list[dict]) -> list:
    date_str = shift_date.strftime("%A, %B %-d, %Y")
    lines = "\n".join(
        f"• <{FRONTEND_URL}/admin/callout-review/{n['source_event_id']}|{n['source_detail']}>"
        if n.get("source_event_id") else f"• {n['source_detail']}"
        for n in notices
    )
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📋 Manager Action Required", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Hi *{manager_name}*, the following items from your shift on "
                    f"*{date_str}* were not reviewed by end of shift and require your action:"
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": lines},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Per NDL policy, managers are required to review and countersign "
                            "employee writeups within the same shift. Please complete these today.",
                }
            ],
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Core scan logic (called by the EOD loop or manually via API)
# ─────────────────────────────────────────────────────────────────────────────

def _run_eod_scan(scan_date: date, writeup_type: str,
                  items: list[dict], db: Session) -> dict:
    """
    For each unreviewed item, create a ManagerAccountabilityEvent for every
    on-duty manager that day. The morning DM loop picks them up at 6 AM.

    items: list of {source_event_id, source_detail}
    """
    managers = _on_duty_managers(scan_date)
    if not managers:
        return {"status": "no_manager_scheduled", "date": scan_date.isoformat()}

    created = 0
    for manager in managers:
        for item in items:
            existing = db.query(ManagerAccountabilityEvent).filter(
                ManagerAccountabilityEvent.shift_date == scan_date,
                ManagerAccountabilityEvent.manager_name == manager["name"],
                ManagerAccountabilityEvent.writeup_type == writeup_type,
                ManagerAccountabilityEvent.source_event_id == item.get("source_event_id"),
            ).first()
            if existing:
                continue
            db.add(ManagerAccountabilityEvent(
                shift_date=scan_date,
                manager_name=manager["name"],
                manager_slack_id=manager["slack_id"],
                writeup_type=writeup_type,
                source_event_id=item.get("source_event_id"),
                source_detail=item.get("source_detail", ""),
            ))
            created += 1
    db.commit()
    return {
        "status": "queued",
        "date": scan_date.isoformat(),
        "managers": [m["name"] for m in managers],
        "items_queued": created,
    }


def _send_pending_dms(db: Session) -> int:
    """Send morning DMs for all unsent accountability notices. Called at 6 AM."""
    yesterday = datetime.now(PACIFIC).date() - timedelta(days=1)
    pending = (
        db.query(ManagerAccountabilityEvent)
        .filter(
            ManagerAccountabilityEvent.shift_date == yesterday,
            ManagerAccountabilityEvent.dm_sent_at == None,
        )
        .all()
    )
    if not pending:
        return 0

    # Group by manager
    by_manager: dict[str, list] = {}
    for evt in pending:
        by_manager.setdefault(evt.manager_name, []).append(evt)

    sent_count = 0
    for manager_name, evts in by_manager.items():
        slack_id = evts[0].manager_slack_id
        if not slack_id:
            continue
        notices = [{"source_event_id": e.source_event_id, "source_detail": e.source_detail} for e in evts]
        blocks = _build_dm_blocks(manager_name, yesterday, notices)
        text = f"Manager Action Required — {len(notices)} unreviewed item(s) from {yesterday}"
        ok = _send_dm(slack_id, text, blocks)
        if ok:
            now = datetime.utcnow()
            for e in evts:
                e.dm_sent_at = now
            db.commit()
            sent_count += len(evts)

    return sent_count


# ─────────────────────────────────────────────────────────────────────────────
# Background loop — runs nightly
# ─────────────────────────────────────────────────────────────────────────────

async def manager_accountability_loop():
    """
    11 PM Pacific — scan for unsigned callout writeups → queue notices
    6 AM Pacific  — send morning DMs to on-duty managers
    """
    _ensure_tables()
    logger.info("Manager accountability loop started.")
    await asyncio.sleep(10)   # let server warm up

    while True:
        try:
            now = datetime.now(PACIFIC)
            hour = now.hour

            if hour == 23:
                # EOD scan — unsigned callout writeups
                db_gen = get_db()
                db = next(db_gen)
                try:
                    from api.src.database import AttendanceEvent
                    today = now.date()
                    unsigned = db.query(AttendanceEvent).filter(
                        AttendanceEvent.event_date == today,
                        AttendanceEvent.signature_name != None,
                        AttendanceEvent.manager_signature_name == None,
                    ).all()
                    if unsigned:
                        items = [
                            {
                                "source_event_id": e.id,
                                "source_detail": f"{e.driver_name} — {e.reason_code} callout",
                            }
                            for e in unsigned
                        ]
                        result = _run_eod_scan(today, "unsigned_callout", items, db)
                        logger.info("Manager accountability EOD scan: %s", result)
                finally:
                    db.close()
                await asyncio.sleep(3600)   # sleep 1 hour to avoid double-firing

            elif hour == 6:
                # Morning DM dispatch
                db_gen = get_db()
                db = next(db_gen)
                try:
                    sent = _send_pending_dms(db)
                    logger.info("Manager accountability morning DMs sent: %d", sent)
                finally:
                    db.close()
                await asyncio.sleep(3600)

            else:
                # Sleep until next relevant hour
                next_target = 23 if hour < 23 else 6
                if next_target <= hour:
                    next_target += 24
                sleep_secs = (next_target - hour) * 3600 - now.minute * 60 - now.second
                await asyncio.sleep(max(sleep_secs, 60))

        except Exception as exc:
            logger.error("Manager accountability loop error: %s", exc)
            await asyncio.sleep(300)


# ─────────────────────────────────────────────────────────────────────────────
# API endpoints
# ─────────────────────────────────────────────────────────────────────────────

class EodScanRequest(BaseModel):
    writeup_type: str
    shift_date: Optional[str] = None   # YYYY-MM-DD; defaults to today
    items: List[dict]                  # [{source_event_id, source_detail}]


@router.post("/eod-scan")
def eod_scan(req: EodScanRequest, db: Session = Depends(get_db)):
    """Manually trigger an EOD scan for any writeup type."""
    scan_date = date.today()
    if req.shift_date:
        try:
            scan_date = date.fromisoformat(req.shift_date)
        except ValueError:
            raise HTTPException(400, "Invalid shift_date format.")
    return _run_eod_scan(scan_date, req.writeup_type, req.items, db)


@router.post("/send-morning-dms")
def send_morning_dms(db: Session = Depends(get_db)):
    """Manually trigger morning DM dispatch (for testing or manual recovery)."""
    sent = _send_pending_dms(db)
    return {"status": "ok", "dms_sent": sent}


@router.get("/pending-notices")
def pending_notices(db: Session = Depends(get_db)):
    """Admin — list all accountability notices not yet sent."""
    rows = (
        db.query(ManagerAccountabilityEvent)
        .filter(ManagerAccountabilityEvent.dm_sent_at == None)
        .order_by(ManagerAccountabilityEvent.shift_date.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "shift_date": r.shift_date.isoformat(),
            "manager_name": r.manager_name,
            "writeup_type": r.writeup_type,
            "source_event_id": r.source_event_id,
            "source_detail": r.source_detail,
        }
        for r in rows
    ]


@router.get("/schedule")
def get_schedule():
    """Return the current manager on-duty schedule."""
    day_names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    return [
        {
            "name": m["name"],
            "half": m["half"],
            "slack_id": m["slack_id"],
            "days": sorted([day_names[d] for d in m["days"]]),
        }
        for m in MANAGERS
    ]


@router.get("/on-duty")
def on_duty_today():
    """Who is on duty today."""
    today = datetime.now(PACIFIC).date()
    managers = _on_duty_managers(today)
    return {
        "date": today.isoformat(),
        "day_of_week": today.strftime("%A"),
        "on_duty": [{"name": m["name"], "half": m["half"]} for m in managers],
    }
