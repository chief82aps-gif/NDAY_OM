"""
Route Bands — geographic-proximity clustering of route codes, inferred from
gaps in the route *numbers* actually run, since nearby route numbers tend to
be close together on this DSP and no real lat/long data exists anywhere in
this system (Cortex.zone is always None — never populated by any ingest
parser). Per explicit 2026-07-19 decision: learn the band boundaries from
real historical data rather than guessing a fixed width (e.g. a jump from
150 to 156 is a likely real geographic break; a run of 121, 122, 123... is
likely one contiguous area).

This is the foundation for two not-yet-built features (also discussed
2026-07-19, deliberately out of scope here):
  - Roster priority lists (recommending which performance band a driver
    belongs in)
  - Auto-assignment refinement using area-level performance history

v1 scope, deliberately narrow:
  - calibrate_bands(): one-shot/re-runnable band-boundary detection off
    Cortex's historical route codes (weeks of real data already exist —
    no need to wait and collect fresh data first).
  - A read report joining each band's routes to the drivers who ran them
    and each driver's most recent quality score — a first correlation
    between "area" and "performance," not a per-week attribution (that
    needs reconciling Amazon's own week-label boundaries against calendar
    dates, deferred until this coarser view is validated against real
    geography).

No performance metric exists per-route in this system (Amazon's own
quality CSV is a per-driver weekly aggregate, never broken out by route or
area) — this report necessarily uses each driver's overall quality score
as a proxy for "how the areas they work tend to perform," not a true
per-route defect rate.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, Cortex, QualityMetricDriver, RouteBandDefinition

router = APIRouter(prefix="/route-bands", tags=["route-bands"])

DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_GAP_MULTIPLIER = 2.0
MIN_GAP_THRESHOLD = 5  # floor so normal, small numbering variance never fragments a band


def _route_number(route_code: str) -> Optional[int]:
    """Extract the numeric portion of a route code (e.g. 'CX121' -> 121).
    Returns None for anything that doesn't contain digits."""
    if not route_code:
        return None
    match = re.search(r"\d+", route_code)
    return int(match.group()) if match else None


def _detect_boundaries(numbers: list[int], gap_multiplier: float) -> list[int]:
    """Given sorted, unique route numbers, return the gap sizes larger than
    the calibration threshold — each one marks a likely geographic break."""
    if len(numbers) < 2:
        return []
    gaps = [numbers[i + 1] - numbers[i] for i in range(len(numbers) - 1)]
    median_gap = statistics.median(gaps)
    threshold = max(gap_multiplier * median_gap, MIN_GAP_THRESHOLD)
    return [i for i, gap in enumerate(gaps) if gap > threshold]


def calibrate_bands(
    db: Session,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
) -> list[dict]:
    """Re-derive route-number bands from every distinct route number seen
    in Cortex over the lookback window, replacing the prior calibration
    wholesale (same pattern as OkamiSettings — a periodically-refreshed
    config, not per-day data)."""
    cutoff = datetime.utcnow().date() - timedelta(days=lookback_days)
    raw_codes = (
        db.query(Cortex.route_code)
        .filter(Cortex.assignment_date >= cutoff)
        .distinct()
        .all()
    )
    numbers = sorted({n for (code,) in raw_codes if (n := _route_number(code)) is not None})

    db.query(RouteBandDefinition).delete()

    if not numbers:
        db.commit()
        return []

    boundary_indices = set(_detect_boundaries(numbers, gap_multiplier))
    bands: list[dict] = []
    band_start = numbers[0]
    for i, n in enumerate(numbers):
        is_last = i == len(numbers) - 1
        if i in boundary_indices or is_last:
            band_end = n
            bands.append({"range_start": band_start, "range_end": band_end})
            if not is_last:
                band_start = numbers[i + 1]

    now = datetime.utcnow()
    for b in bands:
        db.add(RouteBandDefinition(
            band_label=f"{b['range_start']}-{b['range_end']}",
            range_start=b["range_start"],
            range_end=b["range_end"],
            calibrated_at=now,
            distinct_routes_used=len(numbers),
        ))
    db.commit()
    return bands


def get_band_label(route_code: str, bands: list[RouteBandDefinition]) -> Optional[str]:
    n = _route_number(route_code)
    if n is None:
        return None
    for b in bands:
        if b.range_start <= n <= b.range_end:
            return b.band_label
    return None


@router.post("/calibrate")
def calibrate(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    gap_multiplier: float = DEFAULT_GAP_MULTIPLIER,
    db: Session = Depends(get_db),
):
    """Re-derive band boundaries from Cortex history. Safe to re-run as
    more data accumulates — replaces the prior calibration wholesale."""
    bands = calibrate_bands(db, lookback_days, gap_multiplier)
    return {
        "status": "calibrated",
        "band_count": len(bands),
        "bands": [f"{b['range_start']}-{b['range_end']}" for b in bands],
        "lookback_days": lookback_days,
        "gap_multiplier": gap_multiplier,
    }


@router.get("")
def list_bands(db: Session = Depends(get_db)):
    bands = db.query(RouteBandDefinition).order_by(RouteBandDefinition.range_start).all()
    return {
        "band_count": len(bands),
        "bands": [
            {
                "label": b.band_label,
                "range_start": b.range_start,
                "range_end": b.range_end,
                "calibrated_at": b.calibrated_at.isoformat() if b.calibrated_at else None,
            }
            for b in bands
        ],
    }


@router.get("/report")
def band_report(lookback_days: int = DEFAULT_LOOKBACK_DAYS, db: Session = Depends(get_db)):
    """For each calibrated band: which drivers have run routes in it (over
    the lookback window) and their most recent overall quality score —
    a first correlation between area and performance, not a per-route
    defect rate (none exists in this system)."""
    bands = db.query(RouteBandDefinition).order_by(RouteBandDefinition.range_start).all()
    if not bands:
        return {"status": "not_calibrated", "message": "Run POST /route-bands/calibrate first."}

    cutoff = datetime.utcnow().date() - timedelta(days=lookback_days)
    rows = (
        db.query(Cortex.route_code, Cortex.driver_name)
        .filter(Cortex.assignment_date >= cutoff, Cortex.driver_name.isnot(None))
        .distinct()
        .all()
    )

    band_drivers: dict[str, set[str]] = {b.band_label: set() for b in bands}
    for route_code, driver_name in rows:
        label = get_band_label(route_code, bands)
        if label:
            band_drivers[label].add(driver_name)

    # Latest quality score per driver, regardless of week — a coarse
    # snapshot, not a per-week attribution (see module docstring).
    latest_scores: dict[str, Optional[float]] = {}
    for driver_name in {d for drivers in band_drivers.values() for d in drivers}:
        row = (
            db.query(QualityMetricDriver)
            .filter(QualityMetricDriver.driver_name == driver_name)
            .order_by(QualityMetricDriver.week.desc())
            .first()
        )
        latest_scores[driver_name] = float(row.overall_score) if row and row.overall_score is not None else None

    report = []
    for b in bands:
        driver_names = sorted(band_drivers.get(b.band_label, []))
        scores = [latest_scores[d] for d in driver_names if latest_scores.get(d) is not None]
        report.append({
            "band": b.band_label,
            "driver_count": len(driver_names),
            "drivers": driver_names,
            "avg_overall_score": round(sum(scores) / len(scores), 1) if scores else None,
        })

    return {"lookback_days": lookback_days, "bands": report}
