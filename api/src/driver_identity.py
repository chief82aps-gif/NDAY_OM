"""
Driver identity resolution — the one shared place that turns a free-text
driver-name string (from ADP, DOP, Cortex, a schedule file, a Slack DM, a
manager-typed form — every source spells names slightly differently) into
a canonical DriverRosterEntry.

Added 2026-07-23 after a run of production bugs (missed Slack IDs,
double-sent sweeper DMs, sweeper DMs to off-schedule drivers, a Slack
summary undercounting real callouts) that all traced back to the same
root cause: driver identity was being re-derived by fuzzy name matching
independently at 12+ call sites across the codebase, each with slightly
different normalization/thresholds. This module consolidates that into
one algorithm; callers should prefer a stored roster_id where one exists
and only fall back to resolve_roster_id() for rows that don't have one
yet (see the driver-identity refactor plan).

Matching is intentionally against payroll_name only (ADP's "Last, First"
spelling) — that's the one name every other system's driver_name string
is ultimately trying to refer to. preferred_name (below) is display-only
and never enters matching, since external systems have no way to send it
back to us.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from api.src.database import DriverRosterEntry

TOKEN_MATCH_THRESHOLD = 2


def _tokens(name: Optional[str]) -> frozenset:
    return frozenset((name or "").lower().replace(",", "").split())


def resolve_roster_entry(name: str, db: Session) -> Optional[DriverRosterEntry]:
    """Resolve a free-text driver name to its DriverRosterEntry, or None.

    Exact (case-sensitive) payroll_name match first, then a best-match
    token-overlap fallback (>=2 shared name tokens, comma-stripped,
    lowercased) among active roster entries. Picks the highest-scoring
    candidate rather than the first one crossing the threshold — a
    deliberate improvement over the original call sites this consolidates
    (rostering.py:_get_driver_slack_id and others), which returned on the
    first hit and were nondeterministic under ties.
    """
    if not name:
        return None

    exact = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.payroll_name == name, DriverRosterEntry.is_active == True)
        .first()
    )
    if exact:
        return exact

    name_tokens = _tokens(name)
    if not name_tokens:
        return None

    best_entry = None
    best_score = 0
    for candidate in db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all():
        score = len(name_tokens & _tokens(candidate.payroll_name))
        if score >= TOKEN_MATCH_THRESHOLD and score > best_score:
            best_entry = candidate
            best_score = score
    return best_entry


def resolve_roster_id(name: str, db: Session) -> Optional[int]:
    entry = resolve_roster_entry(name, db)
    return entry.id if entry else None


def display_name(entry: DriverRosterEntry) -> str:
    """Name to show a driver-facing message — their preferred name if
    they've set one, otherwise their payroll name. Never use this for
    matching; match against entry.payroll_name."""
    return entry.preferred_name or entry.payroll_name


def backfill_roster_ids(db: Session, start_date, end_date) -> dict:
    """One-time/rerunnable backfill: populate roster_id on existing rows
    (in the date range) that don't have one yet. Safe to call repeatedly —
    only touches rows where roster_id IS NULL. Returns counts per table."""
    from api.src.database import DailyRouteAssignment, DriverScheduleEntry, DriverShiftDM

    counts = {"daily_route_assignments": 0, "driver_schedule_entries": 0, "driver_shift_dms": 0}

    assignments = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date >= start_date,
            DailyRouteAssignment.assignment_date <= end_date,
            DailyRouteAssignment.roster_id.is_(None),
            DailyRouteAssignment.driver_name.isnot(None),
        )
        .all()
    )
    for a in assignments:
        rid = resolve_roster_id(a.driver_name, db)
        if rid:
            a.roster_id = rid
            counts["daily_route_assignments"] += 1

    schedule_entries = (
        db.query(DriverScheduleEntry)
        .filter(
            DriverScheduleEntry.schedule_date >= start_date,
            DriverScheduleEntry.schedule_date <= end_date,
            DriverScheduleEntry.roster_id.is_(None),
        )
        .all()
    )
    for s in schedule_entries:
        rid = resolve_roster_id(s.driver_name, db)
        if rid:
            s.roster_id = rid
            counts["driver_schedule_entries"] += 1

    shift_dms = (
        db.query(DriverShiftDM)
        .filter(
            DriverShiftDM.shift_date >= start_date,
            DriverShiftDM.shift_date <= end_date,
            DriverShiftDM.roster_id.is_(None),
        )
        .all()
    )
    for d in shift_dms:
        rid = resolve_roster_id(d.driver_name, db)
        if rid:
            d.roster_id = rid
            counts["driver_shift_dms"] += 1

    db.commit()
    return counts
