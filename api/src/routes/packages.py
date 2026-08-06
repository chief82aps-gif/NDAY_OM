"""
Packages — Amazon's live, company-wide non-delivered-package export
(Reattemptable / Undeliverable / Missing / Returned to station / Pickup
failed), added 2026-08-04. Pulled by hand multiple times a day
(immediately after the last wave launches, every 60-90 min after that,
and at COB) -- see ops_cadence.py for the reminder/All-In gate built on
top of these uploads.

Owns the `packages_snapshots` / `packages_records` tables exclusively —
other modules (rts.py's debrief pre-population, ops_cadence.py's COB
gate) should call the helpers here, never query them directly, per the
hub-and-spoke rule in CLAUDE.md.

Append-only — unlike daily_quality/quality_rts, a re-upload does NOT
replace the prior snapshot for the date; multiple snapshots per day are
expected and kept for progress-over-the-day visibility. Ingested via
ops_ingest.py's normal Slack-scan/auto-ingest pipeline (detected_type
"packages") or direct upload here.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from api.src.database import get_db, PackagesSnapshot, PackagesRecord
from api.src.ingest.packages import parse_packages

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/packages", tags=["packages"])

# Statuses that mean "didn't get delivered normally" -- everything this
# file tracks. Kept as a named list rather than an exclusion so a new
# Amazon status shows up as a visible gap instead of silently included.
NON_DELIVERED_STATUSES = ("Reattemptable", "Undeliverable", "Missing", "Returned to station", "Pickup failed")


def _store_packages(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    """Parse CSV/Excel and append a new snapshot. Called from
    ops_ingest.py's dispatcher (Slack-scan path) or the direct upload
    endpoint below. Never replaces a prior snapshot for the date -- see
    module docstring."""
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    records, errors = parse_packages(tmp_path)
    if errors:
        logger.warning("Packages: %d parse issue(s) for %s: %s", len(errors), filename, "; ".join(errors[:5]))
    if not records:
        return {"status": "error", "message": "; ".join(errors) if errors else "No package rows parsed from file."}

    if slack_file_id:
        existing = db.query(PackagesSnapshot).filter(
            PackagesSnapshot.slack_file_id == slack_file_id
        ).first()
        if existing:
            return {"status": "already_ingested", "package_count": existing.package_count}

    report_date = date.today()
    snap = PackagesSnapshot(
        report_date=report_date,
        source_file=filename,
        slack_file_id=slack_file_id,
        imported_at=datetime.now(timezone.utc),
        package_count=len(records),
    )
    db.add(snap)
    db.flush()

    for r in records:
        db.add(PackagesRecord(
            snapshot_id=snap.id,
            tracking_id=r.tracking_id,
            route_code=r.route_code,
            transporter_name=r.transporter_name,
            transporter_id=r.transporter_id,
            address=r.address,
            package_status=r.package_status,
            reason_code=r.reason_code,
            last_scan_at=r.last_scan_at,
        ))

    db.commit()
    return {"status": "ingested", "report_date": report_date.isoformat(), "package_count": len(records)}


def get_latest_snapshot(db: Session, report_date: Optional[date] = None) -> Optional[PackagesSnapshot]:
    """Most recently imported snapshot for a date (defaults to today) --
    used for the RTS debrief pre-population and the ops_cadence COB gate,
    both of which only care about the freshest picture, not history."""
    report_date = report_date or date.today()
    return (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date == report_date)
        .order_by(PackagesSnapshot.imported_at.desc())
        .first()
    )


def get_driver_packages(transporter_id: str, db: Session, report_date: Optional[date] = None) -> list[PackagesRecord]:
    """This driver's non-delivered packages from the latest snapshot for
    the date -- the list rts.py's debrief pre-populates from."""
    snap = get_latest_snapshot(db, report_date)
    if not snap:
        return []
    return (
        db.query(PackagesRecord)
        .filter(
            PackagesRecord.snapshot_id == snap.id,
            PackagesRecord.transporter_id == transporter_id,
        )
        .all()
    )


def _serialize_snapshot(s: PackagesSnapshot) -> dict:
    return {
        "id": s.id,
        "report_date": s.report_date.isoformat(),
        "source_file": s.source_file,
        "package_count": s.package_count,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
    }


def _serialize_record(r: PackagesRecord) -> dict:
    return {
        "tracking_id": r.tracking_id,
        "route_code": r.route_code,
        "transporter_name": r.transporter_name,
        "transporter_id": r.transporter_id,
        "address": r.address,
        "package_status": r.package_status,
        "reason_code": r.reason_code,
        "no_reason": r.reason_code is None,
        "last_scan_at": r.last_scan_at.isoformat() if r.last_scan_at else None,
    }


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack."""
    content = await file.read()
    return _store_packages(content, file.filename or "upload.csv", None, db)


@router.get("/snapshots")
def list_snapshots(days: int = 3, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    snaps = (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date >= since)
        .order_by(PackagesSnapshot.imported_at.desc())
        .all()
    )
    return {"snapshots": [_serialize_snapshot(s) for s in snaps]}


@router.get("/latest")
def get_latest(db: Session = Depends(get_db)):
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    rows = db.query(PackagesRecord).filter(PackagesRecord.snapshot_id == snap.id).all()
    return {"snapshot": _serialize_snapshot(snap), "records": [_serialize_record(r) for r in rows]}


@router.get("/no-reason")
def no_reason_worklist(db: Session = Depends(get_db)):
    """Every package in the latest snapshot Amazon hasn't recorded a
    reason for yet -- the exact coaching worklist this module exists to
    surface."""
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    rows = (
        db.query(PackagesRecord)
        .filter(PackagesRecord.snapshot_id == snap.id, PackagesRecord.reason_code.is_(None))
        .order_by(PackagesRecord.transporter_name)
        .all()
    )
    return {"snapshot": _serialize_snapshot(snap), "records": [_serialize_record(r) for r in rows]}


# ─────────────────────────────────────────────────────────────────────────────
# Non-delivered-marking offender scrub -- added 2026-08-05, per explicit
# direction: drivers are supposed to seek permission from Blake BEFORE
# marking a package Reattemptable/Undeliverable/Missing/Returned to
# station/Pickup failed, but that permission workflow doesn't exist yet
# (see Governance backlog note -- future: pre-marking permission request,
# a package detail/dispute view, and DS text-conversation capture).
# Until that exists, this scrubs the Packages export itself for anyone
# using these codes heavily -- human review of who might be marking
# packages undeliverable without actually working the problem first,
# not an automatic accusation. "Reattemptable" is excluded from the
# offender count itself (a reattempt is still a plausible delivery, not
# a claim of being unable to deliver) but still shown for context.
# ─────────────────────────────────────────────────────────────────────────────

UNABLE_TO_DELIVER_STATUSES = ("Undeliverable", "Missing", "Returned to station", "Pickup failed")
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt


def get_driver_status_counts(db: Session, snapshot: PackagesSnapshot) -> list[dict]:
    """Per-driver breakdown for one snapshot, sorted by unable-to-deliver
    count descending. "statuses" is the coarse package_status tally;
    "unable_to_deliver_reasons" is Amazon's own Column H reason code
    (e.g. "BUSINESS CLOSED", "TR CANCELLED", "CUSTOMER UNAVAILABLE"),
    tallied only for the unable-to-deliver rows -- added 2026-08-05 per
    explicit request ("can the undeliverables see the exact markings?
    We need this rather than just 'Undeliverable'")."""
    rows = db.query(PackagesRecord).filter(PackagesRecord.snapshot_id == snapshot.id).all()
    by_driver: dict[str, dict] = {}
    for r in rows:
        name = r.transporter_name or "Unknown"
        entry = by_driver.setdefault(name, {"transporter_id": r.transporter_id, "statuses": {}, "unable_to_deliver_reasons": {}, "unable_to_deliver": 0})
        entry["statuses"][r.package_status] = entry["statuses"].get(r.package_status, 0) + 1
        if r.package_status in UNABLE_TO_DELIVER_STATUSES:
            entry["unable_to_deliver"] += 1
            reason = r.reason_code or "(no reason given)"
            entry["unable_to_deliver_reasons"][reason] = entry["unable_to_deliver_reasons"].get(reason, 0) + 1
    result = [{"driver_name": name, **data} for name, data in by_driver.items()]
    result.sort(key=lambda d: d["unable_to_deliver"], reverse=True)
    return result


def get_trailing_offender_report(db: Session, days: int = 7) -> list[dict]:
    """Sums unable-to-deliver counts across the latest snapshot per
    report_date over the trailing N days -- the "systematic/habitual"
    view, as opposed to get_driver_status_counts()'s single-snapshot
    picture. Uses only the LATEST snapshot per date (not every re-pull)
    so a busy upload day doesn't inflate a driver's count."""
    since = date.today() - timedelta(days=days - 1)
    dates_with_data = (
        db.query(PackagesSnapshot.report_date)
        .filter(PackagesSnapshot.report_date >= since)
        .distinct()
        .all()
    )
    by_driver: dict[str, dict] = {}
    for (report_date,) in dates_with_data:
        snap = get_latest_snapshot(db, report_date)
        if not snap:
            continue
        for entry in get_driver_status_counts(db, snap):
            d = by_driver.setdefault(entry["driver_name"], {"transporter_id": entry["transporter_id"], "unable_to_deliver": 0, "days_flagged": 0, "reason_codes": {}})
            if entry["unable_to_deliver"] > 0:
                d["unable_to_deliver"] += entry["unable_to_deliver"]
                d["days_flagged"] += 1
                for reason, count in entry["unable_to_deliver_reasons"].items():
                    d["reason_codes"][reason] = d["reason_codes"].get(reason, 0) + count
    result = [{"driver_name": name, **data} for name, data in by_driver.items() if data["unable_to_deliver"] > 0]
    result.sort(key=lambda d: d["unable_to_deliver"], reverse=True)
    return result


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


# Direct-to-driver DM for a NEW unable-to-deliver marking -- added
# 2026-08-05 per explicit direction: drivers should be checking with
# Blake and getting permission BEFORE marking a package this way (the
# actual pre-marking permission gate is still backlog, not built --
# see Governance backlog note), so until that exists, every new marking
# gets this direct, pointed, slightly snarky ask. Replies land in the
# driver's own DM and get relayed to HR via slack_interactions.py's
# existing _relay_driver_dm_reply, no new plumbing needed for that part.
_OFFENDER_DM_TEMPLATES = [
    "👀 Hey {first} — I'm seeing you marked {count_phrase} today: {reasons}. There's a button for this, you know — "
    "did you actually contact the customer? Try a redelivery? What's going on here? Why am I seeing this instead "
    "of you checking with me first?",
    "🤨 {first}, quick question — {count_phrase} today ({reasons}). Did you reach out to the customer about it? "
    "We've got a process for confirming these before they go final, and I don't remember hearing from you. "
    "What happened?",
]


def send_offender_dm(driver_name: str, flagged: list[dict], db: Session) -> dict:
    """Direct DM to the specific driver for their own new unable-to-
    deliver marking(s) -- separate from send_offender_alert_to_mgt()'s
    #nday-mgt summary, which stays a human-review list, not a driver
    confrontation."""
    from api.src.driver_identity import resolve_roster_entry
    entry = resolve_roster_entry(driver_name, db)
    slack_id = entry.slack_member_id if entry else None
    if not slack_id:
        return {"status": "no_slack_id", "driver_name": driver_name}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    first = (driver_name or "there").split()[0]
    count_phrase = f"{len(flagged)} package(s)" if len(flagged) > 1 else "a package"
    reason_counts: dict = {}
    for p in flagged:
        key = f"{p['reason_code']} ({p['package_status']})"
        reason_counts[key] = reason_counts.get(key, 0) + 1
    reasons = _format_reasons(reason_counts)

    import random
    text = random.choice(_OFFENDER_DM_TEMPLATES).format(first=first, count_phrase=count_phrase, reasons=reasons)
    try:
        client.chat_postMessage(channel=slack_id, text=text)
    except Exception as exc:
        logger.warning("Offender DM failed for %s: %s", driver_name, exc)
        return {"status": "send_failed", "error": str(exc)}
    return {"status": "sent", "driver_name": driver_name, "text": text}


def get_new_unable_to_deliver_since_last_snapshot(db: Session, snapshot: PackagesSnapshot) -> list[dict]:
    """Unable-to-deliver PackagesRecord rows in `snapshot` whose
    tracking_id did NOT appear in the immediately prior snapshot for the
    same report_date -- i.e. newly marked since the last upload, not
    just "everything flagged today." Added 2026-08-05 per explicit
    request ("we would also like to know new marking since last
    update"). If this is the first snapshot of the day, everything
    unable-to-deliver in it counts as new."""
    prior = (
        db.query(PackagesSnapshot)
        .filter(PackagesSnapshot.report_date == snapshot.report_date, PackagesSnapshot.imported_at < snapshot.imported_at)
        .order_by(PackagesSnapshot.imported_at.desc())
        .first()
    )
    prior_tracking_ids = set()
    if prior:
        prior_tracking_ids = {
            r.tracking_id for r in db.query(PackagesRecord.tracking_id).filter(PackagesRecord.snapshot_id == prior.id).all()
        }

    rows = (
        db.query(PackagesRecord)
        .filter(PackagesRecord.snapshot_id == snapshot.id, PackagesRecord.package_status.in_(UNABLE_TO_DELIVER_STATUSES))
        .all()
    )
    return [
        {
            "driver_name": r.transporter_name or "Unknown",
            "tracking_id": r.tracking_id,
            "package_status": r.package_status,
            "reason_code": r.reason_code or "(no reason given)",
        }
        for r in rows if r.tracking_id not in prior_tracking_ids
    ]


def _format_reasons(reasons: dict) -> str:
    return ", ".join(f"{k} ({v})" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))


def send_offender_alert_to_mgt(db: Session, snapshot: Optional[PackagesSnapshot] = None, trailing_days: int = 7) -> dict:
    """Posts new-since-last-update + today's snapshot breakdown + the
    trailing-N-day habitual view to #nday-mgt for human review. Framed
    as "worth a look," never as an automatic write-up -- these drivers
    haven't been through any permission/dispute process, this is raw
    pattern-of-use data. Shows Amazon's own exact reason code (Column H)
    per driver, not just the coarse package_status bucket."""
    snapshot = snapshot or get_latest_snapshot(db)
    if not snapshot:
        return {"status": "no_snapshot"}

    new_since_last = get_new_unable_to_deliver_since_last_snapshot(db, snapshot)
    today_counts = [d for d in get_driver_status_counts(db, snapshot) if d["unable_to_deliver"] > 0]
    trailing = get_trailing_offender_report(db, days=trailing_days)

    if not today_counts and not trailing:
        return {"status": "nothing_to_report"}

    # Direct-to-driver DM for each NEW unable-to-deliver marking this
    # cycle -- own try/except per driver so one bad Slack ID never blocks
    # the rest, and never affects the #nday-mgt alert below.
    driver_dm_results = []
    if new_since_last:
        packages_by_driver: dict[str, list[dict]] = {}
        for row in new_since_last:
            packages_by_driver.setdefault(row["driver_name"], []).append(row)
        for name, flagged in packages_by_driver.items():
            try:
                driver_dm_results.append(send_offender_dm(name, flagged, db))
            except Exception as exc:
                logger.warning("Offender DM dispatch failed for %s: %s", name, exc)

    lines = ["📦 *Non-Delivered Marking Review* — for human review, not an automatic write-up.\n"]

    if new_since_last:
        new_by_driver: dict[str, dict] = {}
        for row in new_since_last:
            d = new_by_driver.setdefault(row["driver_name"], {})
            key = f"{row['reason_code']} ({row['package_status']})"
            d[key] = d.get(key, 0) + 1
        lines.append(f"*🆕 New since last upload ({len(new_since_last)} package(s)):*")
        for name, reasons in list(new_by_driver.items())[:10]:
            lines.append(f"• *{name}* — {_format_reasons(reasons)}")
        lines.append("")

    if today_counts:
        lines.append(f"*Today's snapshot ({snapshot.report_date.isoformat()}):*")
        for d in today_counts[:10]:
            lines.append(f"• *{d['driver_name']}* — {d['unable_to_deliver']} unable-to-deliver: {_format_reasons(d['unable_to_deliver_reasons'])}")
    if trailing:
        lines.append(f"\n*Trailing {trailing_days} days (habitual pattern):*")
        for d in trailing[:10]:
            flag = " ⚠️ _repeat pattern_" if d["days_flagged"] >= 3 else ""
            reason_str = f" — top reasons: {_format_reasons(d['reason_codes'])}" if d.get("reason_codes") else ""
            lines.append(f"• *{d['driver_name']}* — {d['unable_to_deliver']} total across {d['days_flagged']} day(s){flag}{reason_str}")
    lines.append("\n_Reminder: drivers should confirm with Blake before marking a package this way — this list is to help spot who may not be doing that yet, not a punishment list._")

    client = _client()
    if not client:
        return {"status": "no_slack_token", "text": "\n".join(lines), "driver_dms": driver_dm_results}
    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text="\n".join(lines))
    except Exception as exc:
        logger.warning("Non-delivered marking alert post failed: %s", exc)
        return {"status": "send_failed", "error": str(exc), "driver_dms": driver_dm_results}
    return {
        "status": "sent", "today_count": len(today_counts), "trailing_count": len(trailing),
        "new_since_last": len(new_since_last), "driver_dms": driver_dm_results,
    }


@router.get("/offender-report")
def offender_report(trailing_days: int = 7, db: Session = Depends(get_db)):
    """Read-only view of the same data send_offender_alert_to_mgt posts."""
    snap = get_latest_snapshot(db)
    if not snap:
        raise HTTPException(404, "No Packages snapshot found for today.")
    return {
        "snapshot": _serialize_snapshot(snap),
        "new_since_last": get_new_unable_to_deliver_since_last_snapshot(db, snap),
        "today": get_driver_status_counts(db, snap),
        "trailing": get_trailing_offender_report(db, days=trailing_days),
    }


@router.post("/send-offender-alert")
def send_offender_alert_endpoint(trailing_days: int = 7, db: Session = Depends(get_db)):
    return send_offender_alert_to_mgt(db, trailing_days=trailing_days)
