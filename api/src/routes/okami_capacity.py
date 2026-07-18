"""
Okami Capacity — daily next-day capacity planning numbers.

Amazon's Okami tool produces these as a short free-text summary ops
posts in Slack (not a file), e.g.:

    61 DAs
    Okami 44
    Capacity at 50 plus 4x4 = 51
    Vans - 55

There's no file to ingest, so this is captured directly via a dashboard
form (see frontend/pages/okami-capacity.tsx) rather than a Slack-message
parser. Owns the `okami_capacity_logs` and `okami_settings` tables
exclusively — other modules (mgt_reminders.py) call has_submission_today()
here rather than querying OkamiCapacityLog directly, per the hub-and-spoke
rule in CLAUDE.md.

Two-step flow, per explicit 2026-07-14 decision:
  1. Draft entry/correction — POST /okami-capacity, any number of times a
     day. Append-only, consistent with this project's ingest philosophy
     elsewhere (never overwrite, always read "most recent for the day").
  2. Finalize — POST /okami-capacity/finalize. Locks in the latest
     submission for the day, computes the coverage checks below against
     the tunable settings, snapshots the result onto that row, posts the
     #nday-mgt summary, and fires any threshold DMs. Re-finalizing after
     a correction re-runs checks and re-sends notifications — that's
     intentional, not a dedup bug.

Business rules (as specified 2026-07-14/15):

  - FRT (Flex Up Route Target) — Amazon's own daily ask, read off their
    scheduling page as a "Flex up target" row that only appears some
    weeks. Entered per-day on the form (nullable — most weeks there is
    none). When present and capacity_total < frt, that's an FRT miss:
    remediation is (a) flag for a human to review the schedule for
    drivers who can pick up extra shifts [phase 1: alert only, the
    auto-suggested driver list is deferred], (b) DM every #nday-mgt
    member (mostly drivers — a flex-up resource pool), (c) DM Jayson
    and Tamra specifically (RoleDirectory "owner"/"hr" roles).

  - DA coverage — target is capacity_total * (1 + driver_buffer_pct/100),
    default 110% (driver_buffer_pct=10). Informational status only, no
    specified notification target — surfaced in the finalize summary.

  - Van coverage — target is capacity_total * (1 + van_buffer_pct/100).
    Effective available vans = van_count + available_non_okami_vehicles
    - 1 (a standing conservative assumption: one more van will end up
    grounded on return to station). If that's short of target, DM every
    #nday-mgt member AND every #nday-fleet member with the current
    grounded-van list (live from the Vehicle table — status='grounded')
    and the shortfall count.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db, OkamiCapacityLog, OkamiSettings, Vehicle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/okami-capacity", tags=["okami-capacity"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")     # #nday-mgt
FLEET_CHANNEL = os.getenv("SLACK_FLEET_CHANNEL", "C0BJ8J5LGAU")  # #nday-fleet
PT = ZoneInfo("America/Los_Angeles")


def _fmt_time(dt: Optional[datetime]) -> str:
    """UTC-naive DB timestamp -> Pacific 'h:mm AM/PM', matching the
    dashboard's display format (see frontend/pages/okami-capacity.tsx)."""
    if not dt:
        return "—"
    local = dt.replace(tzinfo=timezone.utc).astimezone(PT)
    return local.strftime("%I:%M %p").lstrip("0")


class OkamiCapacitySubmission(BaseModel):
    log_date: Optional[str] = None   # defaults to today (server date) if omitted
    da_count: Optional[int] = None
    okami_count: Optional[int] = None
    capacity_base: Optional[int] = None
    capacity_4x4: Optional[int] = None
    van_count: Optional[int] = None
    frt: Optional[int] = None
    submitted_by: Optional[str] = None


class OkamiSettingsUpdate(BaseModel):
    driver_buffer_pct: Optional[int] = None
    van_buffer_pct: Optional[int] = None
    available_non_okami_vehicles: Optional[int] = None
    updated_by: Optional[str] = None


class FinalizeRequest(BaseModel):
    log_id: Optional[int] = None   # defaults to today's latest submission
    finalized_by: Optional[str] = None


def _serialize(r: OkamiCapacityLog) -> dict:
    return {
        "id": r.id,
        "log_date": r.log_date.isoformat(),
        "da_count": r.da_count,
        "okami_count": r.okami_count,
        "capacity_base": r.capacity_base,
        "capacity_4x4": r.capacity_4x4,
        "capacity_total": r.capacity_total,
        "van_count": r.van_count,
        "frt": r.frt,
        "submitted_by": r.submitted_by,
        "created_at": r.created_at.isoformat(),
        "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
        "finalized_by": r.finalized_by,
        "required_da_count": r.required_da_count,
        "da_status": r.da_status,
        "required_van_count": r.required_van_count,
        "effective_available_vans": r.effective_available_vans,
        "van_status": r.van_status,
        "van_deficit": r.van_deficit,
        "grounded_vans_snapshot": r.grounded_vans_snapshot,
        "frt_breached": r.frt_breached,
    }


def _get_or_create_settings(db: Session) -> OkamiSettings:
    row = db.query(OkamiSettings).filter(OkamiSettings.id == 1).first()
    if not row:
        row = OkamiSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def has_submission_today(db: Session, day: date) -> bool:
    """Used by mgt_reminders.py in place of file-detection for the
    'okami' reminder key — this requirement is satisfied by a dashboard
    form submission, not an uploaded file. A draft (unfinalized) entry
    still counts — the reminder is about "did ops engage with Okami
    today", not "was it finalized"."""
    return db.query(OkamiCapacityLog).filter(OkamiCapacityLog.log_date == day).first() is not None


def get_latest_for_date(db: Session, day: date) -> Optional[OkamiCapacityLog]:
    return (
        db.query(OkamiCapacityLog)
        .filter(OkamiCapacityLog.log_date == day)
        .order_by(OkamiCapacityLog.created_at.desc())
        .first()
    )


def _grounded_vans(db: Session) -> list[dict]:
    rows = db.query(Vehicle).filter(Vehicle.status.ilike("grounded")).all()
    return [{"vin": v.vin, "vehicle_name": v.vehicle_name} for v in rows]


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _channel_member_ids(client, channel_id: str) -> list[str]:
    try:
        bot_id = client.auth_test().get("user_id")
    except Exception:
        bot_id = None
    try:
        resp = client.conversations_members(channel=channel_id)
        members = resp.get("members", [])
    except Exception as exc:
        logger.warning("okami_capacity: conversations_members(%s) failed: %s", channel_id, exc)
        return []
    return [m for m in members if m != bot_id]


def _dm_many(client, slack_ids: list[str], text: str) -> int:
    sent = 0
    for uid in slack_ids:
        try:
            client.chat_postMessage(channel=uid, text=text)
            sent += 1
        except Exception as exc:
            logger.warning("okami_capacity: DM to %s failed: %s", uid, exc)
    return sent


@router.post("")
def submit_okami_capacity(payload: OkamiCapacitySubmission, db: Session = Depends(get_db)):
    try:
        # date.today() is the server's system clock (Render runs UTC) —
        # Okami is submitted 3:30-9PM Pacific, which is already the next
        # UTC calendar day for most of that window. A submission with no
        # explicit log_date used to get silently stamped "tomorrow", so
        # has_submission_today() (checked against Pacific "today") never
        # found it and the reminder kept firing despite a real submission.
        log_date = date.fromisoformat(payload.log_date) if payload.log_date else datetime.now(PT).date()
    except ValueError:
        raise HTTPException(400, "log_date must be YYYY-MM-DD")

    capacity_total = None
    if payload.capacity_base is not None and payload.capacity_4x4 is not None:
        capacity_total = payload.capacity_base + payload.capacity_4x4

    row = OkamiCapacityLog(
        log_date=log_date,
        da_count=payload.da_count,
        okami_count=payload.okami_count,
        capacity_base=payload.capacity_base,
        capacity_4x4=payload.capacity_4x4,
        capacity_total=capacity_total,
        van_count=payload.van_count,
        frt=payload.frt,
        submitted_by=payload.submitted_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.get("/today")
def get_today(db: Session = Depends(get_db)):
    pt_today = datetime.now(PT).date()
    row = get_latest_for_date(db, pt_today)
    return {"log_date": pt_today.isoformat(), "submission": _serialize(row) if row else None}


@router.get("")
def list_recent(days: int = 14, db: Session = Depends(get_db)):
    rows = (
        db.query(OkamiCapacityLog)
        .order_by(OkamiCapacityLog.log_date.desc(), OkamiCapacityLog.created_at.desc())
        .limit(days * 3)  # allow for same-day re-submissions
        .all()
    )
    return {"total": len(rows), "submissions": [_serialize(r) for r in rows]}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    s = _get_or_create_settings(db)
    return {
        "driver_buffer_pct": s.driver_buffer_pct,
        "van_buffer_pct": s.van_buffer_pct,
        "available_non_okami_vehicles": s.available_non_okami_vehicles,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "updated_by": s.updated_by,
    }


@router.put("/settings")
def update_settings(payload: OkamiSettingsUpdate, db: Session = Depends(get_db)):
    s = _get_or_create_settings(db)
    if payload.driver_buffer_pct is not None:
        s.driver_buffer_pct = payload.driver_buffer_pct
    if payload.van_buffer_pct is not None:
        s.van_buffer_pct = payload.van_buffer_pct
    if payload.available_non_okami_vehicles is not None:
        s.available_non_okami_vehicles = payload.available_non_okami_vehicles
    s.updated_by = payload.updated_by
    db.commit()
    return get_settings(db)


@router.post("/finalize")
def finalize(payload: FinalizeRequest, db: Session = Depends(get_db)):
    row = (
        db.query(OkamiCapacityLog).filter(OkamiCapacityLog.id == payload.log_id).first()
        if payload.log_id else get_latest_for_date(db, datetime.now(PT).date())
    )
    if not row:
        raise HTTPException(404, "No Okami capacity submission to finalize — submit numbers first.")
    if row.capacity_total is None:
        raise HTTPException(400, "Capacity (base + 4x4) must be entered before finalizing.")

    settings = _get_or_create_settings(db)

    # ── DA coverage (informational — no specified notification target) ──
    required_da = math.ceil(row.capacity_total * (1 + settings.driver_buffer_pct / 100))
    da_status = "ok" if (row.da_count is not None and row.da_count >= required_da) else "short"

    # ── Van coverage ──────────────────────────────────────────────────────
    required_van = math.ceil(row.capacity_total * (1 + settings.van_buffer_pct / 100))
    effective_available = (row.van_count or 0) + settings.available_non_okami_vehicles - 1
    van_status = "ok" if effective_available >= required_van else "short"
    van_deficit = max(0, required_van - effective_available)
    grounded = _grounded_vans(db)

    # ── FRT (flex-up) — only when Amazon actually asked for one this week ──
    frt_breached = bool(row.frt is not None and row.capacity_total < row.frt)

    row.finalized_at = datetime.utcnow()
    row.finalized_by = payload.finalized_by
    row.required_da_count = required_da
    row.da_status = da_status
    row.required_van_count = required_van
    row.effective_available_vans = effective_available
    row.van_status = van_status
    row.van_deficit = van_deficit
    row.grounded_vans_snapshot = grounded
    row.frt_breached = frt_breached
    db.commit()
    db.refresh(row)

    notifications = {"mgt_summary_sent": False, "van_alert_sent": 0, "frt_alert_sent": 0}
    client = _client()
    if client:
        # da_status "short" just means below the buffered target — that's
        # the normal/expected state most days, not a coverage emergency.
        # Only da_count < capacity_total means today's routes themselves
        # are short a driver.
        real_da_shortfall = bool(row.da_count is not None and row.da_count < row.capacity_total)

        # ── "Logged today" card — green bar, mirrors the dashboard's own
        # post-submit confirmation box (frontend/pages/okami-capacity.tsx) ──
        logged_fields = [
            {"type": "mrkdwn", "text": f"*DAs:* {row.da_count}"},
            {"type": "mrkdwn", "text": f"*Okami:* {row.okami_count}"},
            {"type": "mrkdwn", "text": f"*Capacity:* {row.capacity_total}"},
            {"type": "mrkdwn", "text": f"*Vans:* {row.van_count}"},
        ]
        if row.frt is not None:
            logged_fields.append({"type": "mrkdwn", "text": f"*FRT:* {row.frt}"})
        logged_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_check_mark: *Logged today at {_fmt_time(row.created_at)} by "
                            f"{row.submitted_by or 'ops'}*",
                },
            },
            {"type": "section", "fields": logged_fields},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "Need to correct a number? Submit again — the latest entry is what counts.",
                }],
            },
        ]

        # ── "Finalized" card — neutral bar, warning banner + OK/MET badges ──
        finalized_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":lock: *Finalized at {_fmt_time(row.finalized_at)} by "
                            f"{row.finalized_by or 'ops'}*",
                },
            },
        ]
        if real_da_shortfall:
            finalized_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":rotating_light: *DA shortfall* — only {row.da_count} drivers for "
                            f"{row.capacity_total} routes today.",
                },
            })
        elif da_status == "short":
            spare = required_da - row.da_count if row.da_count is not None else None
            finalized_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":large_orange_diamond: *Driver buffer is thin* — {row.da_count} on hand "
                            f"covers today's {row.capacity_total} routes"
                            f"{f', but only {spare} spare' if spare is not None else ''}. "
                            f"If a driver calls out, office staff may need to cover a route.",
                },
            })

        van_badge = ":white_check_mark: `OK`" if van_status == "ok" else ":red_circle: `SHORT`"
        finalized_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Van coverage — need {required_van}, effectively have "
                        f"{effective_available} {van_badge}",
            },
        })
        if row.frt is not None:
            frt_badge = ":red_circle: `BREACHED`" if frt_breached else ":white_check_mark: `MET`"
            finalized_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"FRT (Flex Up Target) — {row.frt} {frt_badge}",
                },
            })
        finalized_blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "Corrected a number above? Finalizing again re-runs these checks and re-sends notifications.",
            }],
        })

        fallback_text = (
            f"Okami Capacity finalized for {row.log_date.isoformat()} (by {row.finalized_by or 'ops'}) — "
            f"DAs: {row.da_count}, Okami: {row.okami_count}, Capacity: {row.capacity_total}, "
            f"Vans: {row.van_count}, Van coverage: {van_status.upper()}"
            + (f", FRT: {'BREACHED' if frt_breached else 'met'}" if row.frt is not None else "")
        )
        try:
            client.chat_postMessage(
                channel=MGT_CHANNEL,
                text=fallback_text,
                attachments=[
                    {"color": "#2eb67d", "blocks": logged_blocks},
                    {"color": "#dddddd", "blocks": finalized_blocks},
                ],
            )
            notifications["mgt_summary_sent"] = True
        except Exception as exc:
            logger.warning("okami_capacity: mgt summary post failed: %s", exc)

        if van_status == "short":
            grounded_text = "\n".join(f"  • {g['vehicle_name'] or g['vin']}" for g in grounded) or "  (none currently flagged grounded)"
            van_msg = (
                f":warning: *Van shortfall for {row.log_date.isoformat()}* — short by {van_deficit} "
                f"(need {required_van}, effectively have {effective_available}).\nCurrently grounded:\n{grounded_text}"
            )
            mgt_ids = _channel_member_ids(client, MGT_CHANNEL)
            fleet_ids = _channel_member_ids(client, FLEET_CHANNEL) if FLEET_CHANNEL else []
            if not FLEET_CHANNEL:
                logger.warning("okami_capacity: SLACK_FLEET_CHANNEL not set — van alert only reached #nday-mgt")
            recipients = list(dict.fromkeys(mgt_ids + fleet_ids))  # dedupe, keep order
            notifications["van_alert_sent"] = _dm_many(client, recipients, van_msg)

        if frt_breached:
            from api.src.routes.document_routing import get_role_slack_ids
            frt_msg = (
                f":rotating_light: *Flex Up Target miss for {row.log_date.isoformat()}* — "
                f"FRT is {row.frt}, capacity is only {row.capacity_total}. "
                f"Please review the schedule for drivers who can pick up extra shifts."
            )
            mgt_ids = _channel_member_ids(client, MGT_CHANNEL)
            owner_hr_ids = get_role_slack_ids(db, "owner") + get_role_slack_ids(db, "hr")
            recipients = list(dict.fromkeys(mgt_ids + owner_hr_ids))
            notifications["frt_alert_sent"] = _dm_many(client, recipients, frt_msg)
    else:
        logger.warning("okami_capacity: SLACK_BOT_TOKEN not set — finalize computed checks but sent nothing")

    return {"submission": _serialize(row), "notifications": notifications}
