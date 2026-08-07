"""
Offboarding / Remove Terminated Employees — added 2026-08-07.

Replaces the associate-file auto-termination that lived inside
`drivers.py::import_ssn_slack()`. That path treated any Associate Data status
other than the literal string "ACTIVE" as terminated and the UI labelled the
whole bucket "Terminated". Measured against the real 2026-08-07 exports, that
would have mislabelled **44 drivers who are on leave of absence as fired**.

This is a separate module rather than an edit to `drivers.py` for two reasons:
`import_ssn_slack()` / `update_driver()` / `terminate_driver()` and all of
`driver_matching.py` are locked (2026-07-21, see CLAUDE.md), and the house rule
is that a new feature gets its own route file. Nothing here modifies those --
it only reads the roster and writes status columns this module owns.

## The two files

Both download from Amazon as "AssociateData (N).csv" with identical columns, so
**they cannot be told apart by filename**. `file_kind` is decided by content:
if every row's Status is OFFBOARDED it's the offboarded export, otherwise it's
the associates export. (Same lesson as the Cortex/DVIC filename-routing bug
that corrupted a day of route data.)

- **Offboarded export** — authoritative for termination. Every row is gone.
- **Associate Data export** — Status is only ever ACTIVE or INACTIVE. There is
  **no leave-of-absence value**, so LOA has to be derived, never read off.

## The four rules (each one exists because the real data broke a simpler rule)

1. **ACTIVE in Associate Data is the sole proof of current employment, and it
   always wins.** Ten offboarded rows match a currently-active employee by name
   at 1.000 similarity; four of them are re-hires who are working today under a
   new account (Loren Ledrew, Jacob Randall Sotelo, Dexter Ray Gleaton, Dana
   Lee Corley). Processing the offboarded file without this rule terminates
   four working drivers.
2. **Offboarded retires an ACCOUNT, never a person who is ACTIVE in associates.**
3. **INACTIVE and not offboarded => leave_of_absence**, surfaced for human
   confirmation. Never "terminated". 44 of the 49 INACTIVE rows land here.
4. **Join on transporter_id, never on email.** Of 199 same-email-base families
   in the offboarded export only 49 are the same person -- 150 are *different
   people* sharing a first initial and surname (a.romero / a.romero1 /
   a.romero3 / a.romero4 are four distinct humans). Amazon indexes the
   local-part because it is already taken by someone else. An email collision
   only indicates a duplicate when the NAME also matches. "The numbered one is
   the dead account" is false too -- Dexter Gleaton's offboarded address is
   d.gleaton1 while his live one is d.gleaton.

Name matching is a fallback only, and goes through `driver_identity`'s existing
resolver rather than a new implementation (CLAUDE.md forbids a third one).
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.authorization import require_any_role
from api.src.database import (
    get_db, DriverRosterEntry, OffboardingFileSnapshot,
)
from api.src.feature_flags import get_flag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/offboarding", tags=["offboarding"])

# Per explicit direction 2026-08-07: the Remove action must prompt for fresh
# uploads when the stored files are older than this.
FILE_MAX_AGE_HOURS = 24


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def _norm_name(s: Optional[str]) -> str:
    out = []
    for ch in (s or "").lower():
        out.append(ch if ch.isalpha() or ch == " " else " ")
    return " ".join("".join(out).split())


def parse_associate_export(raw: bytes, filename: str = "") -> tuple[str, list[dict]]:
    """Parse either export. Returns (file_kind, rows).

    file_kind is decided by CONTENT, never by filename -- both files arrive
    named "AssociateData (N).csv".
    """
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for r in reader:
        name = (r.get("Name and ID") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "norm_name": _norm_name(name),
            "transporter_id": (r.get("TransporterID") or "").strip(),
            "email": (r.get("Email") or "").strip().lower(),
            "status": (r.get("Status") or "").strip().upper(),
        })
    if not rows:
        raise HTTPException(400, f"{filename or 'File'} has no readable rows — expected a 'Name and ID' column.")

    statuses = {r["status"] for r in rows}
    kind = "offboarded" if statuses == {"OFFBOARDED"} else "associates"
    return kind, rows


def classify(associate_status: Optional[str], in_offboarded: bool) -> tuple[str, str]:
    """The four rules, as a pure function so they can be tested without a DB.

    Returns (bucket, note). bucket is one of:
      terminated | leave_of_absence | still_active | protected_rehire | no_data
    """
    if associate_status == "ACTIVE":
        if in_offboarded:
            # RULES 1+2 — ACTIVE in Associate Data always wins. An old account
            # of theirs being offboarded does not terminate the person.
            return "protected_rehire", (
                "Appears in the offboarded export but is ACTIVE in Associate Data — "
                "a retired old account, not this person. Left employed."
            )
        return "still_active", "ACTIVE in Associate Data."
    if associate_status == "INACTIVE":
        if in_offboarded:
            return "terminated", "INACTIVE in Associate Data and present in the offboarded export."
        # RULE 3 — inactive but not offboarded is NOT a termination.
        return "leave_of_absence", (
            "INACTIVE but not in the offboarded export — treat as leave of absence "
            "pending confirmation."
        )
    if associate_status is None and in_offboarded:
        return "terminated", "Absent from Associate Data and present in the offboarded export."
    return "no_data", "No row in either export — no status data to act on. Left untouched."


def _store_snapshot(db: Session, kind: str, rows: list[dict], filename: str, who: str) -> OffboardingFileSnapshot:
    """One current snapshot per kind — replaces any previous one."""
    db.query(OffboardingFileSnapshot).filter(OffboardingFileSnapshot.file_kind == kind).delete(synchronize_session=False)
    snap = OffboardingFileSnapshot(
        file_kind=kind, source_file_name=filename, uploaded_by=who,
        row_count=len(rows), payload=json.dumps(rows),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _load_snapshot(db: Session, kind: str) -> Optional[OffboardingFileSnapshot]:
    return (
        db.query(OffboardingFileSnapshot)
        .filter(OffboardingFileSnapshot.file_kind == kind)
        .order_by(OffboardingFileSnapshot.uploaded_at.desc())
        .first()
    )


def _freshness(db: Session) -> dict:
    now = datetime.utcnow()
    out: dict = {"max_age_hours": FILE_MAX_AGE_HOURS, "files": {}, "stale": False, "missing": []}
    for kind in ("associates", "offboarded"):
        snap = _load_snapshot(db, kind)
        if not snap:
            out["missing"].append(kind)
            out["stale"] = True
            out["files"][kind] = None
            continue
        age = (now - snap.uploaded_at).total_seconds() / 3600.0
        stale = age > FILE_MAX_AGE_HOURS
        out["stale"] = out["stale"] or stale
        out["files"][kind] = {
            "uploaded_at": snap.uploaded_at.isoformat(),
            "uploaded_by": snap.uploaded_by,
            "source_file_name": snap.source_file_name,
            "row_count": snap.row_count,
            "age_hours": round(age, 1),
            "stale": stale,
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_files(
    file_a: UploadFile = File(...),
    file_b: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager")),
):
    """Upload the Associate Data and Offboarded exports, in either order.

    Which is which is worked out from content, so the caller doesn't have to
    know or care -- and can't get it wrong by mislabelling the upload fields.
    """
    stored = {}
    for uf in [f for f in (file_a, file_b) if f is not None]:
        kind, rows = parse_associate_export(await uf.read(), uf.filename or "")
        snap = _store_snapshot(db, kind, rows, uf.filename or "", caller_role)
        stored[kind] = {"rows": len(rows), "source_file_name": snap.source_file_name}
        logger.info("Offboarding: stored %s snapshot (%d rows) from %r", kind, len(rows), uf.filename)

    if "associates" in stored and "offboarded" in stored and stored["associates"]["source_file_name"] == stored["offboarded"]["source_file_name"]:
        logger.warning("Offboarding: both uploads had the same filename — content routing distinguished them.")

    return {"status": "stored", "stored": stored, "freshness": _freshness(db)}


@router.get("/freshness")
def freshness(db: Session = Depends(get_db)):
    """Age of each stored file. The UI calls this before showing the Remove
    button and prompts for re-upload when `stale` is true."""
    return _freshness(db)


@router.get("/scan")
def scan(db: Session = Depends(get_db)):
    """Dry run. Classifies the roster against both files and returns the plan
    without writing anything. Never call apply without reading this first."""
    fresh = _freshness(db)
    if fresh["missing"]:
        raise HTTPException(400, f"Missing file(s): {', '.join(fresh['missing'])}. Upload both exports first.")

    assoc = json.loads(_load_snapshot(db, "associates").payload)
    off = json.loads(_load_snapshot(db, "offboarded").payload)

    assoc_by_tid = {r["transporter_id"]: r for r in assoc if r["transporter_id"]}
    assoc_by_name = {r["norm_name"]: r for r in assoc if r["norm_name"]}
    off_tids = {r["transporter_id"] for r in off if r["transporter_id"]}
    off_names = {r["norm_name"] for r in off if r["norm_name"]}

    terminated, loa, still_active, unknown, protected = [], [], [], [], []

    for entry in db.query(DriverRosterEntry).all():
        nn = _norm_name(entry.payroll_name)
        tid = (entry.transporter_id or "").strip()
        arow = assoc_by_tid.get(tid) if tid else None
        if arow is None:
            arow = assoc_by_name.get(nn)

        in_off = (tid in off_tids) if tid else (nn in off_names)
        rec = {
            "roster_id": entry.id, "payroll_name": entry.payroll_name,
            "transporter_id": tid or (arow or {}).get("transporter_id") or None,
            "is_active_now": entry.is_active,
            "associate_status": (arow or {}).get("status"),
            "in_offboarded_file": in_off,
        }

        bucket, note = classify((arow or {}).get("status"), in_off)
        rec["note"] = note
        {"terminated": terminated, "leave_of_absence": loa, "still_active": still_active,
         "protected_rehire": protected, "no_data": unknown}[bucket].append(rec)

    return {
        "freshness": fresh,
        "counts": {
            "terminated": len(terminated), "leave_of_absence": len(loa),
            "still_active": len(still_active), "protected_rehires": len(protected),
            "no_data": len(unknown),
        },
        "terminated": terminated,
        "leave_of_absence": loa,
        "protected_rehires": protected,
        "no_data": unknown,
    }


class ApplyRequest(BaseModel):
    confirm: bool = False
    apply_loa: bool = True          # also record leave_of_absence status
    remove_from_slack: bool = False  # kick terminated drivers from Slack channels
    actor: Optional[str] = None


@router.post("/apply")
def apply(payload: ApplyRequest, db: Session = Depends(get_db),
          caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager"))):
    """Apply the /scan plan. Requires confirm=true and non-stale files.

    Terminated drivers get employment_status='terminated' + is_active=False,
    which is what every dashboard/report filter keys off. Records are never
    deleted -- routes, DVIC, quality and attendance history stay intact, which
    payroll and any later dispute depend on.
    """
    if not payload.confirm:
        raise HTTPException(400, "Pass confirm=true to apply. Call GET /offboarding/scan first.")

    fresh = _freshness(db)
    if fresh["stale"]:
        raise HTTPException(
            409,
            "The Associate Data / Offboarded files are missing or older than "
            f"{FILE_MAX_AGE_HOURS}h. Upload fresh exports before removing anyone.",
        )

    plan = scan(db)
    now = datetime.utcnow()
    changed_term, changed_loa = [], []

    for rec in plan["terminated"]:
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == rec["roster_id"]).first()
        if not entry:
            continue
        if rec["transporter_id"] and not entry.transporter_id:
            entry.transporter_id = rec["transporter_id"]
        entry.is_active = False
        entry.employment_status = "terminated"
        entry.employment_status_source = "offboarded_export"
        entry.employment_status_at = now
        changed_term.append(entry.payroll_name)

    if payload.apply_loa:
        for rec in plan["leave_of_absence"]:
            entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == rec["roster_id"]).first()
            if not entry:
                continue
            if rec["transporter_id"] and not entry.transporter_id:
                entry.transporter_id = rec["transporter_id"]
            # NOT is_active=False by decree -- LOA means "not routing now,
            # coming back". Leave is_active as-is and record the reason so the
            # UI stops calling them terminated.
            entry.employment_status = "leave_of_absence"
            entry.employment_status_source = "associate_export_inactive"
            entry.employment_status_at = now
            changed_loa.append(entry.payroll_name)

    # Backfill transporter_id for everyone else we could resolve — makes every
    # future run deterministic instead of fuzzy.
    for rec in plan["still_active"] + plan["protected_rehires"]:
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == rec["roster_id"]).first()
        if entry and rec["transporter_id"] and not entry.transporter_id:
            entry.transporter_id = rec["transporter_id"]
        if entry and entry.employment_status != "active":
            entry.employment_status = "active"
            entry.employment_status_source = "associate_export_active"
            entry.employment_status_at = now

    db.commit()
    logger.warning(
        "Offboarding apply by %s: %d terminated, %d marked leave_of_absence",
        payload.actor or caller_role, len(changed_term), len(changed_loa),
    )

    slack_result = {"status": "not_requested"}
    if payload.remove_from_slack:
        slack_result = remove_terminated_from_slack(db, confirm=True)

    return {
        "status": "applied",
        "terminated": {"count": len(changed_term), "drivers": changed_term},
        "leave_of_absence": {"count": len(changed_loa), "drivers": changed_loa},
        "protected_rehires": plan["counts"]["protected_rehires"],
        "slack_removal": slack_result,
    }


def remove_terminated_from_slack(db: Session, confirm: bool = False) -> dict:
    """Remove terminated drivers from every Slack channel the bot can see.

    Gated by OFFBOARDING_SLACK_REMOVAL_ACTIVE (default off) because this is
    irreversible from the app's side -- re-adding someone means a human
    re-inviting them to each channel. Never touches a driver whose
    employment_status is anything other than 'terminated', so an LOA driver
    can't be swept out by it.
    """
    if not get_flag("OFFBOARDING_SLACK_REMOVAL_ACTIVE"):
        return {"status": "inactive",
                "note": "OFFBOARDING_SLACK_REMOVAL_ACTIVE is off — nobody was removed from Slack."}
    if not confirm:
        return {"status": "not_confirmed"}

    import os
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return {"status": "no_token"}
    from slack_sdk import WebClient
    client = WebClient(token=token)

    targets = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.employment_status == "terminated")
        .filter(DriverRosterEntry.slack_member_id.isnot(None))
        .all()
    )
    if not targets:
        return {"status": "nothing_to_do", "removed": 0}

    channels: list[dict] = []
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel", limit=200, cursor=cursor, exclude_archived=True,
        )
        channels.extend(resp.get("channels") or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    removed, failures = [], []
    for entry in targets:
        for ch in channels:
            try:
                client.conversations_kick(channel=ch["id"], user=entry.slack_member_id)
                removed.append({"driver": entry.payroll_name, "channel": ch.get("name")})
            except Exception as exc:
                msg = str(exc)
                # not_in_channel / cant_kick_self etc. are expected and boring
                if "not_in_channel" in msg or "user_not_found" in msg or "cant_kick" in msg:
                    continue
                failures.append({"driver": entry.payroll_name, "channel": ch.get("name"), "error": msg[:120]})

    logger.warning("Offboarding Slack removal: %d channel-removals, %d failures", len(removed), len(failures))
    return {"status": "done", "drivers": len(targets), "removed": len(removed),
            "failures": failures[:20], "failure_count": len(failures)}


@router.post("/slack-removal")
def slack_removal_endpoint(db: Session = Depends(get_db),
                           caller_role: str = Depends(require_any_role("owner", "hr"))):
    """Run the Slack channel removal on its own, for drivers already marked
    terminated. Owner/HR only — this one takes people out of rooms."""
    return remove_terminated_from_slack(db, confirm=True)
