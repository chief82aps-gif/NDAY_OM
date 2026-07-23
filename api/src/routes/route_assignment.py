"""
Route Assignment Board — daily assignment management combining Cortex + DOP + Fleet + Quality Rankings.

Workflow:
  1. Load Cortex routes for the date (Amazon's initial driver assignments)
  2. Overlay DOP data for staging location, wave, and planned packages
  3. Rank drivers by quality score (Platinum > Gold > Silver > Bronze, then by score)
  4. Apply callout rule: called-out drivers drop to priority tier 3 (last resort)
  5. Auto-assign vans using service-type fallback chain and 7-day driver affinity
  6. Finalize → write to daily_route_assignments → trigger DM notifications

Callout rule:
  - Any driver on driver_callouts for the date is de-prioritized below ALL non-callout drivers.
  - Auto-assign still fills their vacated routes from the non-callout pool.
  - If no non-callout driver is available, a callout driver CAN cover the route but the
    assignment is flagged is_callout_coverage=True and shown in amber on the board.

Van assignment rules (from Governance/VAN_INGEST_RULES.md):
  - Skip GROUNDED vehicles
  - Service-type fallback: CDV14→CDV16→XL, CDV16→XL, Electric→Electric only (no fallback)
  - 7-day driver-van affinity takes priority over fallback chain
  - Warn at 85% capacity, block at 100%
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, SessionLocal,
    Cortex, DOP, Vehicle, DriverRosterEntry, DailyRouteAssignment,
    QualityMetricDriver, QualityMetricSnapshot, DriverCallout,
    RouteSheetEntry, get_latest_dop_rows, get_latest_cortex_rows,
    get_latest_route_sheet_rows,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/route-assignment", tags=["route-assignment"])

APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt


def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)

# ─────────────────────────────────────────────────────────────────────────────
# Standing rank (higher = better)
# ─────────────────────────────────────────────────────────────────────────────

_STANDING_RANK = {"Platinum": 4, "Gold": 3, "Silver": 2, "Bronze": 1}

# ─────────────────────────────────────────────────────────────────────────────
# Van service-type fallback chains
# ─────────────────────────────────────────────────────────────────────────────

def _is_electric_service(service_type: str) -> bool:
    s = (service_type or "").lower()
    return "electric" in s or "rivian" in s or "nursery" in s

def _van_fallback_chain(service_type: str) -> list[str]:
    """Return acceptable Vehicle.service_type values in preference order."""
    s = (service_type or "").lower()
    if "electric" in s or "rivian" in s or "nursery" in s:
        return [service_type]          # electric routes: NO fallback
    if "14ft" in s or "cdv14" in s or "custom delivery van 14" in s:
        return ["CDV14", "CDV16", "Extra Large Van", "XL"]
    if "16ft" in s or "cdv16" in s or "custom delivery van 16" in s:
        return ["CDV16", "Extra Large Van", "XL"]
    if "extra large" in s or "xl van" in s:
        return ["Extra Large Van", "XL", "CDV16"]
    if "4wd" in s or "p31" in s:
        return ["4WD P31"]
    return ["Extra Large Van", "XL", "CDV16", "CDV14"]   # default

def _van_matches(van: Vehicle, service_type: str) -> bool:
    """Return True if van.service_type is in the acceptable chain for this route."""
    chain = _van_fallback_chain(service_type)
    vst = (van.service_type or "").strip()
    return any(c.lower() in vst.lower() or vst.lower() in c.lower() for c in chain)

# ─────────────────────────────────────────────────────────────────────────────
# Name-token matching
# ─────────────────────────────────────────────────────────────────────────────

def _tokens(name: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z\s]", "", (name or "").lower()).split())

def _name_overlap(a: str, b: str) -> int:
    return len(_tokens(a) & _tokens(b))

def _match_roster(name: str, roster: list[DriverRosterEntry]) -> Optional[DriverRosterEntry]:
    """Best-match among an already-loaded roster list. Tightened
    2026-07-23 from a >=1-shared-token threshold to the same >=2 used by
    the shared driver-identity resolver elsewhere — a single shared token
    (e.g. only a common first name) was noticeably weaker than every
    other matcher in the codebase and could mismatch two different
    drivers who happen to share a first name."""
    best, best_score = None, 0
    for r in roster:
        score = _name_overlap(name, r.payroll_name)
        if score > best_score:
            best_score, best = score, r
    return best if best_score >= 2 else None

# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────

def _load_cortex_for_date(target: date, db: Session) -> list[Cortex]:
    """One row per route_code for target (most recently ingested wins —
    see get_latest_cortex_rows()), falling back to the latest available
    date if nothing was ingested for target yet."""
    rows = sorted(get_latest_cortex_rows(db, target), key=lambda c: c.route_code or "")
    if not rows:
        # Try the latest available date as fallback (useful for testing)
        latest = db.query(func.max(Cortex.assignment_date)).scalar()
        if latest:
            rows = sorted(get_latest_cortex_rows(db, latest), key=lambda c: c.route_code or "")
    return rows

def _load_dop_map(target: date, db: Session) -> dict[str, DOP]:
    """Return {route_code: DOP} for the date (or nearest earlier date).
    One row per route_code — most recently ingested wins."""
    rows = get_latest_dop_rows(db, target)
    if not rows:
        latest = (
            db.query(func.max(DOP.schedule_date))
            .filter(DOP.schedule_date <= target)
            .scalar()
        )
        if latest:
            rows = get_latest_dop_rows(db, latest)
    return {r.route_code: r for r in rows}

def _load_quality_map(db: Session) -> dict[str, dict]:
    """Return {transporter_id: {rank, standing, score}} from the latest quality snapshot."""
    latest_snap = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.week.desc())
        .first()
    )
    if not latest_snap:
        return {}
    drivers = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.snapshot_id == latest_snap.id)
        .all()
    )
    # Sort: standing rank desc, then score desc → assign rank 1..N
    sorted_drivers = sorted(
        drivers,
        key=lambda d: (
            _STANDING_RANK.get(d.overall_standing or "", 0),
            float(d.overall_score or 0),
        ),
        reverse=True,
    )
    return {
        d.transporter_id: {
            "rank": i + 1,
            "standing": d.overall_standing or "Unknown",
            "score": float(d.overall_score or 0),
            "week": latest_snap.week,
        }
        for i, d in enumerate(sorted_drivers)
        if d.transporter_id
    }

def _load_van_affinity(target: date, db: Session) -> dict[str, str]:
    """Return {transporter_id: van_number} from last-7-days DailyRouteAssignment."""
    cutoff = target - timedelta(days=7)
    rows = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date >= cutoff,
            DailyRouteAssignment.assignment_date < target,
            DailyRouteAssignment.van_number != None,
        )
        .order_by(DailyRouteAssignment.assignment_date.desc())
        .all()
    )
    affinity: dict[str, str] = {}
    for r in rows:
        tid = getattr(r, "transporter_id", None) or r.driver_name
        if tid and tid not in affinity and r.van_number:
            affinity[tid] = r.van_number
    return affinity

def _load_fleet(db: Session) -> list[Vehicle]:
    """Return all non-grounded vehicles."""
    return (
        db.query(Vehicle)
        .filter(
            Vehicle.status != "grounded",
            Vehicle.status != "GROUNDED",
        )
        .all()
    )

def _load_callout_set(target: date, db: Session) -> set[str]:
    """Return set of transporter_ids called out for target date."""
    rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()
    return {r.transporter_id for r in rows}

# ─────────────────────────────────────────────────────────────────────────────
# Auto-assign van for a route
# ─────────────────────────────────────────────────────────────────────────────

def _assign_van(
    service_type: str,
    transporter_id: Optional[str],
    affinity: dict[str, str],
    fleet: list[Vehicle],
    used_vans: set[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (van_number, vin, warning_flag).
    warning_flag: None | 'electric_violation' | 'no_van_available'
    """
    is_electric = _is_electric_service(service_type)

    # 1. Try affinity van first
    affinity_van_name = affinity.get(transporter_id) if transporter_id else None
    if affinity_van_name:
        for v in fleet:
            if v.vehicle_name == affinity_van_name and v.vin not in used_vans:
                # Affinity van is available — check electric constraint
                if is_electric and not v.is_electric:
                    pass  # affinity van is gas, skip it for electric route
                elif not is_electric and v.is_electric:
                    pass  # gas route, skip electric van
                else:
                    return v.vehicle_name, v.vin, None

    # 2. Match by service-type fallback chain
    #
    # BUG (found live 2026-07-20): this used to `return ... "electric_violation"`
    # on the FIRST type-matching vehicle that happened to be non-electric,
    # instead of skipping it and continuing to look for one of the real
    # EDVs elsewhere in the fleet list. Since gas vans commonly sort before
    # electric ones, this failed nearly every electric route even when
    # plenty of eligible EDVs existed (confirmed live: 27 real EDVs in
    # Fleet, 0/28 electric routes got a van). Also added the missing
    # reverse guard -- a gas route must not consume an electric van either
    # (Governance/VAN_INGEST_RULES.md §4.2), which only the affinity-van
    # branch above previously checked.
    chain = _van_fallback_chain(service_type)
    saw_wrong_power_type = False
    for target_type in chain:
        for v in fleet:
            if v.vin in used_vans:
                continue
            vst = (v.service_type or "").lower()
            if target_type.lower() not in vst and vst not in target_type.lower():
                continue
            # Electric constraint: electric route needs an electric van,
            # gas route must not take an electric van. Skip and keep
            # looking rather than bailing on the first mismatch.
            if is_electric and not v.is_electric:
                saw_wrong_power_type = True
                continue
            if not is_electric and v.is_electric:
                saw_wrong_power_type = True
                continue
            return v.vehicle_name, v.vin, None

    if saw_wrong_power_type:
        return None, None, "electric_violation"
    return None, None, "no_van_available"


_ICE_SUBSTITUTE_SERVICE_TYPE = "Standard Parcel - Extra Large Van - US"


def _load_route_sheet_load_sizes(target: date, db: Session) -> dict[str, tuple[int, int]]:
    """{route_code: (total_bags, oversized_count)} from today's Route Sheet
    — the load-size signal used to decide which electric routes keep a
    real EDV vs. get an ICE/XL substitute when EDVs run short (see
    assign_vans_for_routes()). Missing/unparsed values default to 0."""
    rows = get_latest_route_sheet_rows(db, target)
    return {
        r.route_code.upper(): (r.total_bags or 0, r.oversized_count or 0)
        for r in rows if r.route_code
    }


def _adjacent_run_length(route_code: str, all_route_numbers: set[int]) -> int:
    """Count how many OTHER routes in today's route list have a directly
    consecutive route number next to this one (walking outward in both
    directions until the run breaks) — e.g. if today's routes include
    120-125, CX122's run length is 5 (the other 5 routes in that
    contiguous block). Used to judge whether a route's load could
    realistically be redistributed to its geographic neighbors."""
    from api.src.routes.route_bands import _route_number
    n = _route_number(route_code)
    if n is None:
        return 0
    run = {n}
    step = n - 1
    while step in all_route_numbers:
        run.add(step)
        step -= 1
    step = n + 1
    while step in all_route_numbers:
        run.add(step)
        step += 1
    return len(run) - 1


def _rank_unassigned_for_redistribution(
    unassigned: list[tuple[str, Optional[str], str]],
    all_route_codes: list[str],
    load_sizes: dict[str, tuple[int, int]],
) -> list[tuple[str, str, int, int]]:
    """For routes that got NO vehicle at all (real EDV or ICE/XL
    substitute both exhausted) — rank which one(s) are the best candidate
    to formally leave unassigned so its packages/totes can be manually
    redistributed to geographically-adjacent routes at loadout. Prefers a
    route with >=4 directly-adjacent route numbers in today's list
    (smallest tote+oversized count among those); falls back to whichever
    unassigned route has the MOST adjacent routes if none reaches 4.
    Returns [(route_code, driver_identity, load_size, adjacent_count), ...]
    sorted best-candidate-first — does not change any van assignment,
    purely a reporting/decision-support signal."""
    from api.src.routes.route_bands import _route_number
    all_numbers = {n for code in all_route_codes if (n := _route_number(code)) is not None}

    scored = []
    for route_code, driver_identity, _service_type in unassigned:
        bags, oversized = load_sizes.get(route_code.upper(), (0, 0))
        adjacent = _adjacent_run_length(route_code, all_numbers)
        scored.append((route_code, driver_identity or "", bags + oversized, adjacent))

    with_four_plus = [s for s in scored if s[3] >= 4]
    if with_four_plus:
        with_four_plus.sort(key=lambda s: s[2])  # smallest load first
        return with_four_plus
    scored.sort(key=lambda s: (-s[3], s[2]))  # most adjacent first, then smallest load
    return scored


def _post_electric_van_shortage_warning(
    substitutions: list[tuple[str, str, str, int, int]],
    unassigned_ranked: Optional[list[tuple[str, str, int, int]]] = None,
) -> None:
    """#nday-mgt heads-up for every electric route that had to take an
    ICE/XL van instead of a real EDV, per explicit 2026-07-20 decision:
    fully automatic substitution, notify after the fact rather than gate
    on approval, so dispatch can watch these specific routes at loadout.

    unassigned_ranked (also 2026-07-20): if the fleet is fully out —
    no real EDV and no ICE/XL substitute left — this names the routes
    with nothing at all, ranked by how redistributable their load is
    (route-number adjacency + tote/oversized count), so dispatch has a
    starting recommendation instead of an unordered list of problems."""
    client = _slack_client()
    if not client:
        return
    lines = [
        f"• *{route_code}* ({driver_name or 'unassigned'}) → *{van_name}* "
        f"({bags} totes, {oversized} oversized)"
        for route_code, driver_name, van_name, bags, oversized in substitutions
    ]
    text = (
        f":warning: *Electric van shortage — {len(substitutions)} route(s) got an ICE/XL substitute*\n"
        + "\n".join(lines)
        + "\nKeep an eye on these at loadout."
    )
    if unassigned_ranked:
        text += f"\n\n:rotating_light: *Fully out of vans — {len(unassigned_ranked)} route(s) have NO vehicle at all*\n"
        for route_code, driver_name, load_size, adjacent in unassigned_ranked:
            note = f"{adjacent} adjacent route(s) — good redistribution candidate" if adjacent >= 4 else (
                f"only {adjacent} adjacent route(s)" if adjacent else "no adjacent routes — isolated, needs manual coverage"
            )
            text += f"• *{route_code}* ({driver_name or 'unassigned'}) — {load_size} totes+oversized, {note}\n"
        text += "Top of this list is the best candidate to redistribute at loadout; the rest need direct dispatcher attention."
    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Electric van shortage warning post failed: %s", exc)


def assign_vans_for_routes(
    target: date,
    db: Session,
    routes: list[tuple[str, Optional[str], str]],
) -> dict[str, str]:
    """Public entrypoint for other modules (e.g. daily_notify.py) that need
    van auto-assignment — driver-van affinity + service-type fallback chain
    + electric constraint, per Governance/VAN_INGEST_RULES.md — without
    pulling in this module's Cortex/DOP/quality-ranking machinery.

    `routes` is [(route_code, driver_identity, service_type), ...].
    `driver_identity` should be whatever identity the caller's own
    DailyRouteAssignment rows use (transporter_id if available, else
    driver_name) — _load_van_affinity() falls back to driver_name the same
    way, so as long as both sides are consistent the 7-day lookup lines up.

    Electric-van-shortage substitution (added 2026-07-20, explicit
    go-ahead): electric routes are processed first, largest load (totes +
    oversized packages from the Route Sheet) first, so the biggest routes
    get first claim on the limited EDV pool. Any electric route that still
    can't get a real EDV takes an ICE/XL substitute instead of going
    unassigned, and every substitution is reported to #nday-mgt in one
    batch message so dispatch can watch those routes at loadout.

    Returns {route_code: van_number} for every route a van was found for;
    routes with no eligible van (or no driver_identity) are simply absent
    from the result, same as a normal "no match" — callers should leave
    van_number empty/unassigned in that case rather than raise.
    """
    affinity = _load_van_affinity(target, db)
    fleet = _load_fleet(db)
    load_sizes = _load_route_sheet_load_sizes(target, db)
    used_vans: set[str] = set()
    result: dict[str, str] = {}
    substitutions: list[tuple[str, str, str, int, int]] = []

    def _load_size(route_code: str) -> int:
        bags, oversized = load_sizes.get(route_code.upper(), (0, 0))
        return bags + oversized

    electric_routes = [r for r in routes if _is_electric_service(r[2])]
    other_routes = [r for r in routes if not _is_electric_service(r[2])]

    # Pass 1: real EDVs go to the biggest electric routes first.
    electric_routes.sort(key=lambda r: _load_size(r[0]), reverse=True)
    shortfall: list[tuple[str, Optional[str], str]] = []
    for route_code, driver_identity, service_type in electric_routes:
        van_name, van_vin, _warning = _assign_van(service_type, driver_identity, affinity, fleet, used_vans)
        if van_name:
            result[route_code] = van_name
            used_vans.add(van_vin or van_name)
        else:
            # Electric routes' fallback chain is [exact service_type] only
            # (no gas fallback — see _van_fallback_chain), so any failure
            # here always means "no matching EDV was available" — electric
            # and gas service_type strings essentially never text-overlap,
            # so _assign_van's "electric_violation" branch rarely fires in
            # practice; a bare failure IS the shortage signal here.
            shortfall.append((route_code, driver_identity, service_type))

    # Pass 2: ICE/XL substitutes go to the SMALLEST of the leftover
    # electric routes first — if ICE availability is also tight, the
    # BIGGEST unassigned route is the one left surfacing as needing real
    # dispatcher attention, rather than losing out arbitrarily.
    shortfall.sort(key=lambda r: _load_size(r[0]))
    fully_unassigned: list[tuple[str, Optional[str], str]] = []
    for route_code, driver_identity, service_type in shortfall:
        sub_name, sub_vin, _sub_warning = _assign_van(
            _ICE_SUBSTITUTE_SERVICE_TYPE, driver_identity, affinity, fleet, used_vans,
        )
        if sub_name:
            result[route_code] = sub_name
            used_vans.add(sub_vin or sub_name)
            bags, oversized = load_sizes.get(route_code.upper(), (0, 0))
            substitutions.append((route_code, driver_identity or "", sub_name, bags, oversized))
        else:
            # Fully out of vans for this route — no EDV, no ICE/XL left
            # either. Ranked below by adjacency + load size so dispatch
            # gets a redistribution recommendation, not just a bare list.
            fully_unassigned.append((route_code, driver_identity, service_type))

    for route_code, driver_identity, service_type in other_routes:
        van_name, van_vin, _warning = _assign_van(service_type, driver_identity, affinity, fleet, used_vans)
        if van_name:
            result[route_code] = van_name
            used_vans.add(van_vin or van_name)

    if substitutions or fully_unassigned:
        unassigned_ranked = None
        if fully_unassigned:
            all_route_codes = [r[0] for r in routes]
            unassigned_ranked = _rank_unassigned_for_redistribution(fully_unassigned, all_route_codes, load_sizes)
        _post_electric_van_shortage_warning(substitutions, unassigned_ranked)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Core board builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_board(target: date, db: Session) -> dict:
    cortex_rows = _load_cortex_for_date(target, db)
    dop_map = _load_dop_map(target, db)
    quality_map = _load_quality_map(db)
    affinity = _load_van_affinity(target, db)
    fleet = _load_fleet(db)
    callout_set = _load_callout_set(target, db)
    roster = db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all()
    callout_rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()

    # Check if any saved assignments exist for this date
    saved_assignments: dict[str, DailyRouteAssignment] = {
        a.route_code: a
        for a in db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target)
        .all()
    }

    used_vans: set[str] = set()
    routes = []

    for cr in cortex_rows:
        dop = dop_map.get(cr.route_code)
        tid = getattr(cr, "transporter_id", None) or None
        q = quality_map.get(tid, {}) if tid else {}
        saved = saved_assignments.get(cr.route_code)

        # Determine van assignment (saved > auto)
        if saved and saved.van_number:
            van_name, van_vin, van_warn = saved.van_number, None, None
            if van_name:
                used_vans.add(van_name)
        else:
            van_name, van_vin, van_warn = _assign_van(
                cr.service_type or "", tid, affinity, fleet, used_vans
            )
            if van_vin:
                used_vans.add(van_vin)

        is_callout = tid in callout_set if tid else False
        status = (saved.assignment_status if saved and saved.assignment_status else
                  ("callout" if is_callout else "pending"))

        routes.append({
            "route_code": cr.route_code,
            "service_type": cr.service_type,
            "is_electric": _is_electric_service(cr.service_type or ""),
            "driver_name": cr.driver_name,
            "transporter_id": tid,
            "quality_rank": q.get("rank"),
            "quality_standing": q.get("standing", "Unknown"),
            "quality_score": q.get("score"),
            "quality_week": q.get("week"),
            "packages": cr.packages,
            "stops": saved.stops if saved else None,
            "departure_time": saved.departure_time if saved else None,
            "wave": dop.wave if dop else cr.wave,
            "staging_location": dop.station if dop else None,
            "planned_packages": dop.planned_packages if dop else None,
            "route_duration_min": dop.route_duration if dop else None,
            "van_number": van_name,
            "van_vin": van_vin,
            "van_warning": van_warn,
            "is_callout": is_callout,
            "is_callout_coverage": bool(saved and saved.is_callout_coverage) if saved else False,
            "dm_sent": saved.dm_sent if saved else False,
            "assignment_status": status,
            "source_file": cr.source_file,
        })

    # Driver pool: all drivers in quality map, annotated with callout flag and route
    route_by_tid: dict[str, str] = {
        r["transporter_id"]: r["route_code"]
        for r in routes if r["transporter_id"]
    }
    driver_pool = []
    for tid, q in quality_map.items():
        is_callout = tid in callout_set
        driver_pool.append({
            "transporter_id": tid,
            "quality_rank": q["rank"],
            "quality_standing": q["standing"],
            "quality_score": q["score"],
            "is_callout": is_callout,
            "assigned_route": route_by_tid.get(tid),
            # Sort key: (0=normal,1=no_quality_data,2=callout), then rank
            "_sort": (2 if is_callout else 0, q["rank"]),
        })

    # Include any roster drivers not in quality map
    quality_tids = set(quality_map.keys())
    for r in roster:
        # Try to find transporter_id via any matching cortex row
        matching_tid = next(
            (cr.transporter_id for cr in cortex_rows
             if getattr(cr, "transporter_id", None) and
             _name_overlap(cr.driver_name or "", r.payroll_name) >= 2),
            None
        )
        if matching_tid and matching_tid not in quality_tids:
            is_callout = matching_tid in callout_set
            driver_pool.append({
                "transporter_id": matching_tid,
                "quality_rank": 9999,
                "quality_standing": "Unknown",
                "quality_score": None,
                "is_callout": is_callout,
                "assigned_route": route_by_tid.get(matching_tid),
                "_sort": (2 if is_callout else 1, 9999),
            })

    driver_pool.sort(key=lambda d: d["_sort"])
    for d in driver_pool:
        del d["_sort"]

    needs_coverage = [r for r in routes if r["is_callout"]]

    return {
        "date": target.isoformat(),
        "route_count": len(routes),
        "callout_count": len(callout_set),
        "needs_coverage_count": len(needs_coverage),
        "quality_week": (list(quality_map.values())[0].get("week") if quality_map else None),
        "routes": routes,
        "driver_pool": driver_pool,
        "callouts": [
            {
                "id": c.id,
                "transporter_id": c.transporter_id,
                "driver_name": c.driver_name,
                "callout_type": c.callout_type,
                "notes": c.notes,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in callout_rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-assign algorithm
# ─────────────────────────────────────────────────────────────────────────────

def _auto_assign(target: date, db: Session) -> dict:
    """
    For each Cortex route whose driver called out, find the best replacement
    from the non-callout driver pool (ranked by quality). Falls back to callout
    drivers only if no other option exists.

    Writes DailyRouteAssignment rows and returns a summary.
    """
    cortex_rows = _load_cortex_for_date(target, db)
    dop_map = _load_dop_map(target, db)
    quality_map = _load_quality_map(db)
    affinity = _load_van_affinity(target, db)
    fleet = _load_fleet(db)
    callout_set = _load_callout_set(target, db)

    # Build sorted driver list: non-callout first (by rank), then callout (by rank)
    non_callout = sorted(
        [(tid, q) for tid, q in quality_map.items() if tid not in callout_set],
        key=lambda x: x[1]["rank"],
    )
    callout_drivers = sorted(
        [(tid, q) for tid, q in quality_map.items() if tid in callout_set],
        key=lambda x: x[1]["rank"],
    )
    available_pool = non_callout + callout_drivers   # callouts at bottom

    assigned_tids: set[str] = set()
    used_vans: set[str] = set()

    # First pass: assign already-confirmed non-callout routes
    for cr in cortex_rows:
        tid = getattr(cr, "transporter_id", None)
        if tid and tid not in callout_set:
            assigned_tids.add(tid)
            q = quality_map.get(tid, {})
            dop = dop_map.get(cr.route_code)
            van_name, van_vin, van_warn = _assign_van(
                cr.service_type or "", tid, affinity, fleet, used_vans
            )
            if van_vin:
                used_vans.add(van_vin)
            elif van_name:
                used_vans.add(van_name)
            _upsert_assignment(
                db, target, cr, dop, tid, van_name, q,
                is_callout_coverage=False,
                status="confirmed",
            )

    # Second pass: fill callout vacancies
    callout_routes = [cr for cr in cortex_rows
                      if getattr(cr, "transporter_id", None) in callout_set]
    replacements = []
    for cr in callout_routes:
        replacement_tid = None
        is_coverage = False
        for tid, q in available_pool:
            if tid in assigned_tids:
                continue
            is_coverage = tid in callout_set
            replacement_tid = tid
            assigned_tids.add(tid)
            break

        dop = dop_map.get(cr.route_code)
        q = quality_map.get(replacement_tid, {}) if replacement_tid else {}
        van_name, van_vin, van_warn = _assign_van(
            cr.service_type or "", replacement_tid, affinity, fleet, used_vans
        )
        if van_vin:
            used_vans.add(van_vin)
        elif van_name:
            used_vans.add(van_name)

        _upsert_assignment(
            db, target, cr, dop, replacement_tid, van_name, q,
            is_callout_coverage=is_coverage,
            status="confirmed" if replacement_tid else "unassigned",
        )
        replacements.append({
            "route_code": cr.route_code,
            "original_driver": cr.driver_name,
            "replacement_tid": replacement_tid,
            "is_callout_coverage": is_coverage,
            "van": van_name,
            "van_warning": van_warn,
        })

    db.commit()
    return {
        "status": "auto_assigned",
        "date": target.isoformat(),
        "total_routes": len(cortex_rows),
        "callout_routes": len(callout_routes),
        "replacements": replacements,
    }


def _upsert_assignment(
    db: Session,
    target: date,
    cr: Cortex,
    dop: Optional[DOP],
    transporter_id: Optional[str],
    van_name: Optional[str],
    quality: dict,
    is_callout_coverage: bool,
    status: str,
) -> DailyRouteAssignment:
    existing = (
        db.query(DailyRouteAssignment)
        .filter(
            DailyRouteAssignment.assignment_date == target,
            DailyRouteAssignment.route_code == cr.route_code,
        )
        .first()
    )
    kwargs = dict(
        assignment_date=target,
        route_code=cr.route_code,
        driver_name=cr.driver_name,
        van_number=van_name,
        stage_location=dop.station if dop else None,
        wave=dop.wave if dop else cr.wave,
        packages=cr.packages or (dop.planned_packages if dop else None),
        route_duration=dop.route_duration if dop else None,
        service_type=cr.service_type,
        transporter_id=transporter_id,
        quality_rank=quality.get("rank"),
        quality_standing=quality.get("standing"),
        is_callout_coverage=is_callout_coverage,
        assignment_status=status,
    )
    if existing:
        for k, v in kwargs.items():
            try:
                setattr(existing, k, v)
            except Exception:
                pass
        return existing
    row = DailyRouteAssignment(**kwargs)
    db.add(row)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/board")
def get_board(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Full assignment board for a date (defaults to today)."""
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    else:
        target = date.today()
    return _build_board(target, db)


@router.post("/auto-assign")
def auto_assign(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Run the auto-assignment algorithm for a date."""
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(400, "Invalid date format.")
    else:
        target = date.today()
    return _auto_assign(target, db)


class ManualAssignRequest(BaseModel):
    route_code: str
    transporter_id: str
    van_number: Optional[str] = None
    date_str: Optional[str] = None
    notes: Optional[str] = None


@router.post("/assign")
def manual_assign(req: ManualAssignRequest, db: Session = Depends(get_db)):
    """Manually assign a driver (by transporter_id) to a route, with optional van override."""
    target = date.fromisoformat(req.date_str) if req.date_str else date.today()
    callout_set = _load_callout_set(target, db)
    quality_map = _load_quality_map(db)
    q = quality_map.get(req.transporter_id, {})
    is_coverage = req.transporter_id in callout_set

    cr = next(
        (c for c in get_latest_cortex_rows(db, target) if c.route_code == req.route_code),
        None,
    )
    if not cr:
        # Accept assignment even without cortex record
        cr_data = type("Fake", (), {
            "route_code": req.route_code,
            "driver_name": None,
            "service_type": None,
            "packages": None,
            "wave": None,
        })()
    else:
        cr_data = cr

    dop = next(
        (d for d in get_latest_dop_rows(db, target) if d.route_code == req.route_code),
        None,
    )
    row = _upsert_assignment(
        db, target, cr_data, dop, req.transporter_id, req.van_number, q,
        is_callout_coverage=is_coverage,
        status="confirmed",
    )
    db.commit()
    return {
        "status": "assigned",
        "route_code": req.route_code,
        "transporter_id": req.transporter_id,
        "van_number": req.van_number,
        "is_callout_coverage": is_coverage,
        "quality_standing": q.get("standing"),
    }


class CalloutRequest(BaseModel):
    transporter_id: str
    driver_name: str
    callout_date: Optional[str] = None
    callout_type: str = "sick"
    notes: Optional[str] = None
    recorded_by: Optional[str] = None


@router.post("/callout")
def mark_callout(req: CalloutRequest, db: Session = Depends(get_db)):
    """Mark a driver as called out for a date (default: today)."""
    target = date.fromisoformat(req.callout_date) if req.callout_date else date.today()
    existing = (
        db.query(DriverCallout)
        .filter(
            DriverCallout.callout_date == target,
            DriverCallout.transporter_id == req.transporter_id,
        )
        .first()
    )
    if existing:
        return {"status": "already_recorded", "id": existing.id, "date": target.isoformat()}

    callout = DriverCallout(
        callout_date=target,
        transporter_id=req.transporter_id,
        driver_name=req.driver_name,
        callout_type=req.callout_type,
        notes=req.notes,
        recorded_by=req.recorded_by,
        created_at=datetime.now(timezone.utc),
    )
    db.add(callout)
    db.commit()
    logger.info(
        "Callout recorded: %s (%s) on %s — type: %s",
        req.driver_name, req.transporter_id, target, req.callout_type,
    )
    return {
        "status": "recorded",
        "id": callout.id,
        "driver_name": req.driver_name,
        "transporter_id": req.transporter_id,
        "date": target.isoformat(),
        "callout_type": req.callout_type,
        "rule": "Driver moved to bottom of assignment priority. Route flagged for coverage.",
    }


@router.delete("/callout/{callout_id}")
def remove_callout(callout_id: int, db: Session = Depends(get_db)):
    """Remove a callout record (driver is available again)."""
    row = db.query(DriverCallout).filter(DriverCallout.id == callout_id).first()
    if not row:
        raise HTTPException(404, "Callout record not found.")
    db.delete(row)
    db.commit()
    return {"status": "removed", "id": callout_id}


@router.get("/callouts")
def list_callouts(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """List all callouts for a date."""
    target = date.fromisoformat(date_str) if date_str else date.today()
    rows = db.query(DriverCallout).filter(DriverCallout.callout_date == target).all()
    return {
        "date": target.isoformat(),
        "count": len(rows),
        "callouts": [
            {
                "id": r.id,
                "transporter_id": r.transporter_id,
                "driver_name": r.driver_name,
                "callout_type": r.callout_type,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/driver-ranking")
def get_driver_ranking(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """Quality-ranked driver list with callout flags for a date."""
    target = date.fromisoformat(date_str) if date_str else date.today()
    quality_map = _load_quality_map(db)
    callout_set = _load_callout_set(target, db)
    cortex_rows = _load_cortex_for_date(target, db)
    route_by_tid = {
        getattr(cr, "transporter_id", None): cr.route_code
        for cr in cortex_rows if getattr(cr, "transporter_id", None)
    }
    ranking = []
    for tid, q in quality_map.items():
        is_callout = tid in callout_set
        ranking.append({
            "transporter_id": tid,
            "quality_rank": q["rank"],
            "quality_standing": q["standing"],
            "quality_score": q["score"],
            "is_callout": is_callout,
            "assigned_route": route_by_tid.get(tid),
            "priority_tier": 3 if is_callout else 1,
            "priority_label": "Last resort (callout)" if is_callout else "Available",
        })
    ranking.sort(key=lambda d: (3 if d["is_callout"] else 1, d["quality_rank"]))
    return {
        "date": target.isoformat(),
        "total_drivers": len(ranking),
        "callout_count": len(callout_set),
        "ranking": ranking,
    }


@router.post("/finalize")
def finalize_assignments(date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Lock all confirmed assignments for the date and return a summary.
    The daily_notify module handles DM delivery separately.
    """
    target = date.fromisoformat(date_str) if date_str else date.today()
    rows = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target)
        .all()
    )
    for r in rows:
        try:
            r.assignment_status = "finalized"
        except Exception:
            pass
    db.commit()

    try:
        from api.src.routes.rostering import post_assignment_matrix
        post_assignment_matrix(target, db)
    except Exception as e:
        logger.warning("Assignment-matrix post after finalize failed: %s", e)

    unassigned = [r for r in rows if not r.driver_name]
    return {
        "status": "finalized",
        "date": target.isoformat(),
        "total_routes": len(rows),
        "unassigned_count": len(unassigned),
        "message": (
            f"{len(rows)} routes finalized."
            + (f" {len(unassigned)} route(s) still unassigned." if unassigned else "")
        ),
    }
