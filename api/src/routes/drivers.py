"""
Driver Profiles
================
Interim driver-profile module: for now, DriverRosterEntry (fed by ADP
import + schedule-upload auto-creation in ops_ingest.py) is the source of
truth. This module only reads/reviews it — it never creates, deactivates,
or deletes a driver. A future HR module will own create/terminate and
become the real source of truth; this module is designed to hand off to
it without disruption (see the `source` column: "adp_import" |
"schedule_upload" | "hr_module").
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, DriverRosterEntry, flag_stale_driver_profiles,
    get_reminder_state, set_reminder_state,
)
from api.src.driver_matching import (
    load_ssn, load_slack, load_associates,
    best_ssn_match, best_slack_match, best_slack_match_via_associates,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drivers", tags=["drivers"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")     # #nday-mgt


def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


# Weekly nag to HR/#nday-mgt to re-upload the Amazon associate roster
# export ("AssociateData (N).csv") so the Slack-linking bridge
# (best_slack_match_via_associates) keeps working for new hires — added
# 2026-07-21 per explicit request. Gated off by default like the other
# reminder loops (Okami finalize) until confirmed working.
ASSOCIATE_DATA_REMINDER_ACTIVE = os.getenv("ASSOCIATE_DATA_REMINDER_ACTIVE", "false").lower() == "true"
ASSOCIATE_DATA_STALE_DAYS = 7
LAST_ASSOCIATE_UPLOAD_KEY = "associate_data_last_upload"
ASSOCIATE_DATA_REMINDER_KEY = "associate_data_weekly_reminder"

# Proactive "this driver isn't in Slack yet" notice, fired the moment a
# new DriverRosterEntry is auto-created from a schedule upload (see
# ops_ingest.py) — a brand-new driver can never already have a Slack
# link at creation time (linking is a separate backfill process), so
# "new driver created" and "new unlinked driver" are the same event.
UNLINKED_DRIVER_ALERT_ACTIVE = os.getenv("UNLINKED_DRIVER_ALERT_ACTIVE", "false").lower() == "true"


def notify_new_unlinked_drivers(names: list[str]) -> None:
    """Post a one-time #nday-mgt notice for drivers newly auto-created
    from a schedule upload — they won't be Slack-linked until the next
    associate-data import. Best-effort: never raises into the caller's
    ingest path."""
    if not UNLINKED_DRIVER_ALERT_ACTIVE or not names:
        return
    try:
        client = _slack_client()
        if not client:
            return
        names_list = "\n".join(f"• {n}" for n in names)
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=(
                f"👋 *New driver{'s' if len(names) > 1 else ''} not yet in Slack*\n"
                f"{names_list}\n"
                f"Just picked up off today's schedule upload — not linked to a Slack "
                f"account yet. They'll link automatically on the next Associate Data "
                f"import, or can be added manually via /drivers/import-ssn-slack."
            ),
        )
    except Exception as e:
        logger.warning("Unlinked-driver Slack notice failed: %s", e)


def run_associate_data_reminder(db: Session) -> dict:
    """Nags #nday-mgt weekly if the Amazon associate roster export
    hasn't been re-uploaded via /drivers/import-ssn-slack in
    ASSOCIATE_DATA_STALE_DAYS+ days. Reminder-only — never re-runs the
    import itself. Call on a periodic loop (see main.py)."""
    if not ASSOCIATE_DATA_REMINDER_ACTIVE:
        return {"status": "inactive"}

    upload_state = get_reminder_state(db, LAST_ASSOCIATE_UPLOAD_KEY)
    last_uploaded_at = upload_state.get("last_uploaded_at")
    now = datetime.utcnow()

    if last_uploaded_at:
        last_dt = datetime.fromisoformat(last_uploaded_at)
        stale = (now - last_dt).days >= ASSOCIATE_DATA_STALE_DAYS
    else:
        stale = True  # never uploaded at all

    if not stale:
        return {"status": "not_due", "last_uploaded_at": last_uploaded_at}

    nag_state = get_reminder_state(db, ASSOCIATE_DATA_REMINDER_KEY)
    last_nagged_at = nag_state.get("last_nagged_at")
    if last_nagged_at:
        last_nag_dt = datetime.fromisoformat(last_nagged_at)
        if (now - last_nag_dt).days < ASSOCIATE_DATA_STALE_DAYS:
            return {"status": "already_nagged_this_week", "last_nagged_at": last_nagged_at}

    try:
        client = _slack_client()
        if client:
            when = f"since {last_dt.date().isoformat()}" if last_uploaded_at else "no upload on record"
            client.chat_postMessage(
                channel=MGT_CHANNEL,
                text=(
                    f"📋 *Weekly reminder: Associate Data upload*\n"
                    f"The Amazon associate/Transporter roster export hasn't been "
                    f"re-uploaded in {ASSOCIATE_DATA_STALE_DAYS}+ days ({when}). "
                    f"Re-upload the latest `AssociateData (N).csv` via "
                    f"/drivers/import-ssn-slack so new hires keep linking to Slack "
                    f"correctly."
                ),
            )
    except Exception as e:
        logger.warning("Associate Data reminder Slack post failed: %s", e)

    set_reminder_state(db, ASSOCIATE_DATA_REMINDER_KEY, {"last_nagged_at": now.isoformat()})
    return {"status": "nagged", "last_uploaded_at": last_uploaded_at}


def _serialize(r: DriverRosterEntry) -> dict:
    return {
        "id": r.id,
        "payroll_name": r.payroll_name,
        "is_active": r.is_active,
        "source": r.source,
        "last_seen_on_schedule": r.last_seen_on_schedule.isoformat() if r.last_seen_on_schedule else None,
        "flagged_inactive": r.flagged_inactive,
        "flagged_inactive_at": r.flagged_inactive_at.isoformat() if r.flagged_inactive_at else None,
        "slack_member_id": r.slack_member_id,
        "slack_display_name": r.slack_display_name,
        "slack_verified": r.slack_verified,
        "phone": r.phone,
        "ssn_last4": r.ssn_last4,
        "hire_date": r.hire_date.isoformat() if r.hire_date else None,
        "position_code": r.position_code,
    }


@router.get("")
def list_drivers(
    source: Optional[str] = None,
    flagged_only: bool = False,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    """List driver profiles. Defaults to active drivers only."""
    q = db.query(DriverRosterEntry)
    if not include_inactive:
        q = q.filter(DriverRosterEntry.is_active == True)
    if source:
        q = q.filter(DriverRosterEntry.source == source)
    if flagged_only:
        q = q.filter(DriverRosterEntry.flagged_inactive == True)
    rows = q.order_by(DriverRosterEntry.payroll_name).all()
    return {
        "total": len(rows),
        "flagged_count": sum(1 for r in rows if r.flagged_inactive),
        "drivers": [_serialize(r) for r in rows],
    }


@router.get("/{driver_id}")
def get_driver(driver_id: int, db: Session = Depends(get_db)):
    r = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == driver_id).first()
    if not r:
        raise HTTPException(404, f"Driver {driver_id} not found")
    return _serialize(r)


class DriverUpdateRequest(BaseModel):
    phone: Optional[str] = None
    ssn_last4: Optional[str] = None
    slack_member_id: Optional[str] = None
    slack_display_name: Optional[str] = None
    is_active: Optional[bool] = None
    updated_by: Optional[str] = None


@router.patch("/{driver_id}")
def update_driver(driver_id: int, req: DriverUpdateRequest, db: Session = Depends(get_db)):
    """Manual single-driver correction — for fixing one bad Slack link,
    phone number, or PIN (or reactivating a mistakenly-terminated driver)
    without a full CSV re-import. Any field left null in the request is
    left untouched; pass an empty string to clear a text field.

    Deliberately does NOT allow editing payroll_name — it's used as a
    plain-string join key against DailyRouteAssignment.driver_name and
    DriverScheduleEntry.driver_name elsewhere, so renaming it here would
    silently orphan those rows rather than update them.
    """
    r = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == driver_id).first()
    if not r:
        raise HTTPException(404, f"Driver {driver_id} not found")

    if req.phone is not None:
        r.phone = req.phone
    if req.ssn_last4 is not None:
        r.ssn_last4 = req.ssn_last4
    if req.slack_member_id is not None:
        r.slack_member_id = req.slack_member_id
        r.slack_display_name = req.slack_display_name
        r.slack_verified = bool(req.slack_member_id)
        r.slack_verified_at = datetime.utcnow() if req.slack_member_id else None
    if req.is_active is not None:
        r.is_active = req.is_active

    db.commit()
    db.refresh(r)
    logger.info("Driver %s (id=%s) manually updated by %s", r.payroll_name, r.id, req.updated_by or "unknown")
    return _serialize(r)


class TerminateRequest(BaseModel):
    terminated_by: Optional[str] = None


@router.post("/{driver_id}/terminate")
def terminate_driver(driver_id: int, req: TerminateRequest, db: Session = Depends(get_db)):
    """Mark a driver terminated (is_active=False) — the one write this
    interim module allows, per its docstring (a future HR module owns
    the rest of create/terminate). Nothing previously set is_active to
    False anywhere in the codebase, which meant the 'Remove Terminated
    Employees' Dispatch Home button (slack_dispatch_home.py) — which
    finds candidates via is_active==False + a linked Slack account — had
    no way to ever find anyone. This is what feeds it."""
    r = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == driver_id).first()
    if not r:
        raise HTTPException(404, f"Driver {driver_id} not found")
    if not r.is_active:
        return {"status": "already_inactive", "driver": _serialize(r)}
    r.is_active = False
    db.commit()
    db.refresh(r)
    logger.info("Driver %s (id=%s) marked terminated by %s", r.payroll_name, r.id, req.terminated_by or "unknown")
    return {"status": "terminated", "driver": _serialize(r)}


@router.post("/recompute-stale")
def recompute_stale(days: int = 30, db: Session = Depends(get_db)):
    """Manually re-run the 30-day (or custom) staleness flagging pass —
    normally this runs automatically on every schedule ingest."""
    flagged = flag_stale_driver_profiles(db, days=days)
    return {"status": "ok", "days": days, "newly_flagged": flagged}


@router.post("/import-ssn-slack")
def import_ssn_slack(
    dry_run: bool = True,
    ssn_file: Optional[UploadFile] = File(None),
    slack_file: Optional[UploadFile] = File(None),
    associate_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """
    Fuzzy-match the driver roster against an SSN export (real callout PINs)
    and/or a Slack workspace member export (Slack IDs for driver DMs), and
    populate ssn_last4 / slack_member_id / slack_verified accordingly.

    Matching logic: api/src/driver_matching.py (shared with
    scripts/import_ssn_slack.py, the local-CLI equivalent — this endpoint
    is the preferred way to run it against production since it doesn't
    require handing raw DB credentials to a local script).

    associate_file (optional, added 2026-07-16): an Amazon associate/
    Transporter roster export ("AssociateData (N).csv"), used only
    alongside slack_file. For each driver, first tries bridging through
    this file's legal name -> email local-part -> Slack username (a
    strict/deterministic match); only falls back to fuzzy-matching the
    roster name directly against Slack's display/real name if that
    bridge doesn't resolve. The direct fuzzy match alone regularly misses
    real matches because Slack's "Real Name" field is often an
    auto-generated placeholder derived from the username (e.g.
    "A Laporte Ndl"), not something with the driver's actual middle name
    or full legal name in it.

    Pass at least one of ssn_file / slack_file. Defaults to dry_run=true —
    pass ?dry_run=false to actually write changes.
    """
    if not ssn_file and not slack_file:
        raise HTTPException(400, "Provide at least one of ssn_file or slack_file.")

    ssn_path = slack_path = associate_path = None
    try:
        if ssn_file:
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(ssn_file.filename or "")[1] or ".xlsx", delete=False) as tmp:
                tmp.write(ssn_file.file.read())
                ssn_path = tmp.name
        if slack_file:
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(slack_file.filename or "")[1] or ".xlsx", delete=False) as tmp:
                tmp.write(slack_file.file.read())
                slack_path = tmp.name
        if associate_file:
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(associate_file.filename or "")[1] or ".csv", delete=False) as tmp:
                tmp.write(associate_file.file.read())
                associate_path = tmp.name

        ssn_rows = load_ssn(ssn_path) if ssn_path else []
        slack_rows = load_slack(slack_path) if slack_path else []
        associate_rows = load_associates(associate_path) if associate_path else []

        roster = db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all()

        ssn_hits: list[dict] = []
        ssn_misses = 0
        slack_hits: list[dict] = []
        slack_misses = 0

        for entry in roster:
            name = entry.payroll_name

            if ssn_rows:
                last4, score = best_ssn_match(name, ssn_rows)
                if last4:
                    ssn_hits.append({"driver": name, "score": round(score, 2), "pin": last4})
                    if not dry_run:
                        entry.ssn_last4 = last4
                else:
                    ssn_misses += 1

            if slack_rows:
                uid = display = None
                score = 0.0
                method = None
                if associate_rows:
                    uid, display, score = best_slack_match_via_associates(name, associate_rows, slack_rows)
                    if uid:
                        method = "associate_bridge"
                if not uid:
                    uid, display, score = best_slack_match(name, slack_rows)
                    if uid:
                        method = "direct_fuzzy"

                if uid:
                    slack_hits.append({"driver": name, "score": round(score, 2), "slack_id": uid, "slack_display": display, "method": method})
                    if not dry_run:
                        entry.slack_member_id = uid
                        entry.slack_display_name = display
                        entry.slack_verified = True
                        entry.slack_verified_at = datetime.utcnow()
                else:
                    slack_misses += 1

        if not dry_run:
            db.commit()
            if associate_rows:
                set_reminder_state(db, LAST_ASSOCIATE_UPLOAD_KEY, {
                    "last_uploaded_at": datetime.utcnow().isoformat(),
                })

        return {
            "status": "applied" if not dry_run else "dry_run",
            "roster_size": len(roster),
            "ssn": {"matched": len(ssn_hits), "unmatched": ssn_misses, "sample": ssn_hits[:10]} if ssn_rows else None,
            "slack": {"matched": len(slack_hits), "unmatched": slack_misses, "sample": slack_hits[:20]} if slack_rows else None,
        }
    finally:
        for p in (ssn_path, slack_path, associate_path):
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass
