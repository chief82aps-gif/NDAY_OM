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
from sqlalchemy.orm import Session

from api.src.database import get_db, DriverRosterEntry, flag_stale_driver_profiles
from api.src.driver_matching import (
    load_ssn, load_slack, load_associates,
    best_ssn_match, best_slack_match, best_slack_match_via_associates,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drivers", tags=["drivers"])


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
        "slack_verified": r.slack_verified,
        "phone": r.phone,
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
