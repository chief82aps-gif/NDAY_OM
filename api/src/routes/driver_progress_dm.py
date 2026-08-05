"""
Driver Daily Progress DM — added 2026-08-05, per explicit request for an
upbeat mid-day check-in DM showing a driver their delivery progress and
estimated time remaining.

Data sources, confirmed deliberately:
  - Packages export (packages.py's PackagesSnapshot/PackagesRecord) --
    one row per currently NON-delivered package, re-pulled multiple times
    a day. Gives a live "still remaining" count per driver.
  - DailyRouteAssignment -- planned total packages + planned route
    duration for the day (populated from DOP/Route Sheet, NOT Cortex).
  - DriverShiftDM.arrived_at -- "I've Arrived" tap time, for elapsed-vs-
    planned-duration framing.
Cortex is deliberately NOT used here -- confirmed elsewhere this same
session (ops_daily_digest.py) that CortexSnapshot has no real ingest path
in practice and stays empty; Cortex the historical route table is planned/
assignment data, not a live delivered/remaining signal.

TESTING PHASE: hard-restricted to _TESTING_DRIVER_NAMES (Collin LaTour
only) per explicit instruction ("I would like to use Collin Latour as the
only recipient for the first several rounds of testing"). This allowlist
gates every send path (manual trigger AND the automatic per-ingest hook in
ops_ingest.py) -- do not widen it without asking first.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, DailyRouteAssignment, DriverShiftDM, PackagesRecord
from api.src.routes.packages import get_latest_snapshot
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/driver-progress", tags=["driver-progress"])

_TESTING_DRIVER_NAMES = {"Collin Jonathan LaTour"}


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def build_progress_stats(driver_name: str, target_date: date, db: Session) -> Optional[dict]:
    """Read-only. Returns None if there's no route assignment for this
    driver today at all (nothing to report on yet)."""
    assignment = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target_date, DailyRouteAssignment.driver_name == driver_name)
        .first()
    )
    if not assignment:
        return None

    snapshot = get_latest_snapshot(db, target_date)
    remaining = None
    if snapshot:
        remaining = (
            db.query(PackagesRecord)
            .filter(PackagesRecord.snapshot_id == snapshot.id, PackagesRecord.transporter_name == driver_name)
            .count()
        )

    total = assignment.packages
    delivered = max(0, total - remaining) if (total and remaining is not None) else None
    pct = round(100 * delivered / total, 1) if (delivered is not None and total) else None

    dm = (
        db.query(DriverShiftDM)
        .filter(DriverShiftDM.shift_date == target_date, DriverShiftDM.driver_name == driver_name)
        .first()
    )
    arrived_at = dm.arrived_at if dm else None
    elapsed_minutes = (datetime.utcnow() - arrived_at).total_seconds() / 60 if arrived_at else None
    planned_duration = assignment.route_duration
    time_remaining_minutes = (
        (planned_duration - elapsed_minutes) if (planned_duration and elapsed_minutes is not None) else None
    )

    return {
        "driver_name": driver_name,
        "date": target_date.isoformat(),
        "total_packages": total,
        "remaining_packages": remaining,
        "delivered_so_far": delivered,
        "pct_complete": pct,
        "elapsed_minutes": round(elapsed_minutes) if elapsed_minutes is not None else None,
        "planned_duration_minutes": planned_duration,
        "time_remaining_minutes": round(time_remaining_minutes) if time_remaining_minutes is not None else None,
        "van_number": assignment.van_number,
        "wave": assignment.wave,
    }


_OPENERS_STRONG = [
    "🔥 You're crushing it out there today!",
    "🌟 Look at you go — this is a great run!",
    "🚀 Absolutely flying through the route today!",
]
_OPENERS_MID = [
    "👍 Solid progress today — nice steady pace!",
    "💪 Making good moves out there, keep it rolling!",
    "🙂 Right on track — good work so far!",
]
_OPENERS_EARLY_OR_BEHIND = [
    "☀️ Just checking in — every stop counts, you've got plenty of route left!",
    "🚐 Still plenty of daylight — no stress, just keep knocking out stops!",
    "🎯 Early days on this one still — steady and sure wins it!",
]

_CLOSERS = [
    "You've got this! 💪",
    "Keep it up — we're rooting for you! 🙌",
    "Drive safe and keep truckin'! 🚚",
]


def _fmt_minutes(m: Optional[int]) -> str:
    if m is None:
        return "?"
    h, mm = divmod(abs(m), 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


def build_progress_message_text(stats: dict) -> str:
    pct = stats["pct_complete"]
    if pct is not None and pct >= 85:
        opener = random.choice(_OPENERS_STRONG)
    elif pct is not None and pct >= 50:
        opener = random.choice(_OPENERS_MID)
    else:
        opener = random.choice(_OPENERS_EARLY_OR_BEHIND)

    lines = [opener, ""]
    if stats["total_packages"] and stats["delivered_so_far"] is not None:
        pct_str = f" ({pct}%)" if pct is not None else ""
        lines.append(f"📦 {stats['delivered_so_far']}/{stats['total_packages']} delivered{pct_str}")
    if stats["remaining_packages"] is not None:
        lines.append(f"🎯 {stats['remaining_packages']} stop(s) to go")

    trm = stats["time_remaining_minutes"]
    if trm is not None:
        if trm >= 0:
            lines.append(f"⏱️ About {_fmt_minutes(trm)} left on today's planned route time")
        else:
            lines.append(f"⏱️ You're {_fmt_minutes(-trm)} past our usual estimate — no worries, every route's different!")

    lines.append("")
    lines.append(random.choice(_CLOSERS))
    return "\n".join(lines)


def send_progress_dm(driver_name: str, db: Session, target_date: Optional[date] = None) -> dict:
    if driver_name not in _TESTING_DRIVER_NAMES:
        return {"status": "not_in_testing_allowlist", "driver_name": driver_name}

    target_date = target_date or datetime.now(PT).date()
    stats = build_progress_stats(driver_name, target_date, db)
    if not stats:
        return {"status": "no_assignment", "driver_name": driver_name, "date": target_date.isoformat()}

    from api.src.driver_identity import resolve_roster_entry
    entry = resolve_roster_entry(driver_name, db)
    slack_id = entry.slack_member_id if entry else None
    if not slack_id:
        return {"status": "no_slack_id", "driver_name": driver_name}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    text = build_progress_message_text(stats)
    try:
        client.chat_postMessage(channel=slack_id, text=text)
    except Exception as exc:
        logger.warning("Driver progress DM failed for %s: %s", driver_name, exc)
        return {"status": "send_failed", "error": str(exc)}

    return {"status": "sent", "driver_name": driver_name, "text": text, "stats": stats}


@router.get("/preview")
def preview_progress(driver_name: str = "Collin Jonathan LaTour", target_date: Optional[str] = None, db: Session = Depends(get_db)):
    """See the message text without sending it."""
    d = date.fromisoformat(target_date) if target_date else datetime.now(PT).date()
    stats = build_progress_stats(driver_name, d, db)
    if not stats:
        return {"status": "no_assignment", "driver_name": driver_name, "date": d.isoformat()}
    return {"stats": stats, "text": build_progress_message_text(stats)}


@router.post("/send-test")
def send_test(driver_name: str = "Collin Jonathan LaTour", db: Session = Depends(get_db)):
    """Manual on-demand trigger for testing rounds. Restricted to
    _TESTING_DRIVER_NAMES regardless of what's passed here."""
    return send_progress_dm(driver_name, db)
