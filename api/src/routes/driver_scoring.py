"""
Driver Individual Scoring — a weighted percentage score per active driver.

Category weights are Attendance 20% / Safety 40% / Quality 40% (explicit
2026-07-27 decision, sums to a clean 100). Within Safety and Quality, the
per-metric proportions still mirror Amazon's current DA Performance
Scoring config (screenshot, 2026-07-22) -- _weighted_avg() normalizes by
whatever weight is actually available inside each category, so these
relative proportions determine the category's own 0-100 score
independent of how much that category counts toward the overall blend.
Amazon's per-driver CSV still gives one combined "CDF DPMO" score rather
than Customer Delivery Feedback and Customer Escalation Defect (CED)
separately, so CDF here continues to stand in for both combined
(5.9 + 11.9 = 17.8):

  Safety metric proportions:
    Speeding 12.5 | Seatbelt 12.5 | Sign/Signal 12.5 | Distractions 7.5 | Following Distance 5.0
  Quality metric proportions:
    DC DPMO 11.9 | DSB 11.9 | POD 2.9 | CDF DPMO (+CED) 17.8 | PSB 5.5
  Attendance (20% of overall) -- reframed as a "reliability score" 2026-08-02:
    100 - total deductions, floored at 0. Deductions combine:
      - trailing-60-day attendance points x 10 (unchanged from the
        original formula -- attendance.py's HRM-023.1 points ladder still
        drives its own separate Written Warning/Final Warning/Termination
        consequences; this just reuses the same point total as one input)
      - DVIC violations in the same 60-day window (dvic.py's
        get_dvic_reliability_deductions()): 3 per stage-1, 6 per stage-2,
        12 for a weekly-frequency escalation
      - Coaching Notifications in the window (coaching_notifications.py's
        get_coaching_reliability_deductions()): 4 each
      - CONFIRMED safety violations in the window (safety_events.py's
        get_safety_reliability_deductions()): 8 each -- unconfirmed/
        false-flagged events never count
    Crash reports and injury reports are deliberately excluded -- both are
    frequently no-fault or safety-positive-to-report, so docking
    reliability for filing one would be a bad incentive. This is purely a
    ranking input (route/schedule priority) -- it changes NO existing
    write-up workflow, notification, video gate, or sign-off chain; those
    all keep working exactly as before, this only reads their already-
    recorded outcomes.

Team & Fleet (Tenured Workforce + Fleet Execution) is dropped entirely
from the weighted score -- these are DSP-wide/vehicle-level metrics, not
something an individual driver's own behavior controls. Driver tenure is
NOT part of the weighted score at all -- it's a pass/fail eligibility
gate (the Tenured Workforce report's own "Tenure Status" field), same as
the 30-route trailing-6-week floor.

Tier cutoffs (TIER_THRESHOLDS) were recalibrated 2026-07-29 for this
20/40/40 blend: Platinum >99, Gold 98-99, Silver 97-98, Bronze 92-97,
Does Not Meet <=92. (Briefly split the bottom band into Tin/Lead/Sawdust
the same day, reverted 2026-08-04 -- see TIER_THRESHOLDS's own comment.)

  Note: Amazon's own scoring page also lists a Safe Driving Metric
  (FICO) row, currently weighted 0% -- intentionally excluded here since
  it carries no weight on Amazon's side either.

A missing/None component score is dropped and the remaining weights in
that category renormalized -- the same "Coming Soon" handling the real
scorecard documents in Appendix A, so one missing metric doesn't unfairly
zero out a driver's category score.

Eligibility (ranking + high-performer bonus) requires BOTH:
  - Tenure Status == "Tenured" (TenuredWorkforceRecord, latest week)
  - >= 30 routes in the trailing 6 weeks (routes_in_week summed)
A driver failing either still gets a score shown (useful for coaching),
just flagged ineligible rather than silently hidden.

Tier thresholds (tier_for()) originally mirrored Amazon's own Platinum/
Gold/Silver/Bronze cutoffs (screenshot, 2026-07-22), then were replaced
entirely by NDAY's own bands (see TIER_THRESHOLDS above) for the 20/40/40
blend -- applied here to OUR custom-blended overall score, not Amazon's
own overall_score, per explicit 2026-07-22 decision. high_performer_eligible
deliberately keeps the exact same 92.0 floor the old green/yellow/red
system used (i.e. any named tier, not just Platinum) -- switching what
counts as "high performer" would be a real bonus-eligibility policy
change nobody asked for here, so it's preserved as-is even though the
tier *names* changed.

Driver-facing display (2026-08-04): when a driver sees their OWN
standing (slack_home.py's Home tab), the tier renders through
DRIVER_FACING_TIER_DISPLAY's baseball-ladder names (All-Star/Major
League/Triple-A/Double-A/Spring Training) instead of the staff-facing
Platinum/Gold/Silver/Bronze/Does Not Meet Minimum names in TIER_DISPLAY
-- reframes the bottom tier as a development stage rather than a report
card. Staff/HR-facing surfaces (admin dashboards, #nday-mgt matrices)
keep the staff-facing names; this is additive, not a replacement.
"""
from __future__ import annotations

from datetime import timedelta, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.timezone import PACIFIC

from api.src.database import (
    get_db,
    QualityMetricDriver,
    QualityMetricSnapshot,
    DriverRosterEntry,
    get_trailing_route_count,
    get_latest_tenure_record,
)
from api.src.driver_identity import resolve_roster_id

router = APIRouter(prefix="/driver-scoring", tags=["driver-scoring"])

SAFETY_WEIGHTS = {
    "speeding_score": 12.5,
    "seatbelt_score": 12.5,
    "sign_violation_score": 12.5,
    "distraction_score": 7.5,
    "following_distance_score": 5.0,
}
QUALITY_WEIGHTS = {
    "dc_dpmo_score": 11.9,
    "dsb_score": 11.9,
    "pod_score": 2.9,
    "cdf_dpmo_score": 17.8,   # stands in for CDF (5.9) + CED (11.9) combined
    "psb_score": 5.5,
}
CATEGORY_WEIGHTS = {
    "safety": 40.0,
    "quality": 40.0,
    "attendance": 20.0,
}

ROUTE_ELIGIBILITY_THRESHOLD = 30
ROUTE_ELIGIBILITY_WEEKS = 6

# Recalibrated 2026-07-29 for the 20/40/40 blend (explicit request) --
# these no longer mirror Amazon's own DA Performance page cutoffs, they're
# NDAY's own bands for the blended overall score: Platinum >99, Gold
# 98-99, Silver 97-98, Bronze 92-97, Does Not Meet <=92.
#
# 2026-07-29 briefly split the bottom band into Tin/Lead/Sawdust, but
# every consumer (quality.py, rostering.py, route_assignment.py) ended up
# independently re-collapsing those three back into one "Does Not Meet
# Minimum" display anyway via their own separate _TIER_DISPLAY dicts --
# the same reinvented-duplication pattern flagged elsewhere in this
# codebase. Reverted 2026-08-04 (explicit direction) to make "Does Not
# Meet" the one real tier below Bronze, removing the need for any
# display-layer collapsing at all.
# Each tier's threshold is its own upper bound (you must exceed a tier's
# listed number to reach the tier above it).
TIER_THRESHOLDS = [
    ("platinum", 99.0),
    ("gold", 98.0),
    ("silver", 97.0),
    ("bronze", 92.0),
]
HIGH_PERFORMER_THRESHOLD = 92.0   # unchanged floor -- see module docstring


def _weighted_avg(row: QualityMetricDriver, weights: dict) -> Optional[float]:
    total_weight = 0.0
    total = 0.0
    for field, weight in weights.items():
        value = getattr(row, field, None)
        if value is None:
            continue
        total += float(value) * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return total / total_weight


RELIABILITY_WINDOW_DAYS = 60


def _reliability_score(
    roster_id: Optional[int],
    driver_name: str,
    db: Session,
    dvic_deductions: dict[int, float],
    coaching_deductions: dict[int, float],
    safety_deductions: dict[int, float],
) -> float:
    """Replaces the old pure-points _attendance_score() -- see the module
    docstring's "Attendance (20% of overall)" section for the full
    breakdown of what feeds this. attendance.py's own points ladder and
    Written Warning/Final Warning/Termination consequences are entirely
    separate and unaffected by this function."""
    from api.src.routes.attendance import _driver_points_summary
    points = _driver_points_summary(driver_name, db)["current_points"]
    points_deduction = min(100.0, points * 10.0)

    total_deduction = points_deduction
    if roster_id is not None:
        total_deduction += dvic_deductions.get(roster_id, 0.0)
        total_deduction += coaching_deductions.get(roster_id, 0.0)
        total_deduction += safety_deductions.get(roster_id, 0.0)

    return max(0.0, 100.0 - total_deduction)


# Staff-facing display names -- the one shared source of truth. Added
# 2026-08-04: quality.py, rostering.py, and route_assignment.py had each
# independently defined their own copy of this exact same collapse-tin/
# lead/sawdust-to-one-string dict; now that "does_not_meet" is the one
# real tier below Bronze (see TIER_THRESHOLDS above), there's nothing
# left to collapse -- this is just the canonical tier -> label mapping,
# imported by every module that needs it instead of redefined per-file.
TIER_DISPLAY = {
    "does_not_meet": "Does Not Meet Minimum",
}

# Driver-facing names ONLY -- shown when a driver sees their own
# standing (e.g. slack_home.py's Home tab), never on staff/HR-facing
# dashboards. Added 2026-08-04 per explicit direction: reframes the
# tiers as a development ladder (real minor-league progression) rather
# than a report card, since "Does Not Meet Minimum" reads harshly for
# something a driver sees about themselves. Staff-facing surfaces keep
# TIER_DISPLAY/the raw Platinum-Bronze names -- this mapping is additive,
# not a replacement.
DRIVER_FACING_TIER_DISPLAY = {
    "platinum": "All-Star",
    "gold": "Major League",
    "silver": "Triple-A",
    "bronze": "Double-A",
    "does_not_meet": "Spring Training",
}


def tier_for(score: Optional[float]) -> str:
    """Platinum down through Bronze per TIER_THRESHOLDS above, applied to
    our own blended overall/category scores. "gray" for a score we
    couldn't compute at all (missing data); "does_not_meet" is the bottom
    catch-all for anything at or below the Bronze cutoff."""
    if score is None:
        return "gray"
    for tier_name, cutoff in TIER_THRESHOLDS:
        if score > cutoff:
            return tier_name
    return "does_not_meet"


def compute_driver_scores(db: Session) -> list[dict]:
    """Overall/Safety/Quality/Attendance percentages + color + eligibility
    for every driver in the most recently ingested quality snapshot."""
    latest_snap = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.week.desc())
        .first()
    )
    if not latest_snap:
        return []

    rows = (
        db.query(QualityMetricDriver)
        .filter(QualityMetricDriver.snapshot_id == latest_snap.id)
        .all()
    )

    # Reliability deduction maps -- built ONCE per call, not per-row (same
    # "resolve identity once, key by roster_id" pattern as rostering.py's
    # _latest_quality_map()). Imported lazily to avoid a route-module
    # import cycle at package load time.
    since_date = datetime.now(PACIFIC).date() - timedelta(days=RELIABILITY_WINDOW_DAYS)
    from api.src.routes.dvic import get_dvic_reliability_deductions
    from api.src.routes.coaching_notifications import get_coaching_reliability_deductions
    from api.src.routes.safety_events import get_safety_reliability_deductions
    dvic_deductions = get_dvic_reliability_deductions(since_date, db)
    coaching_deductions = get_coaching_reliability_deductions(since_date, db)
    safety_deductions = get_safety_reliability_deductions(since_date, db)

    # roster_id per QualityMetricDriver row -- exact transporter_id ==
    # position_id match first, falling back to name resolution, same
    # precedent as the deduction-map builders above.
    position_ids = {row.transporter_id for row in rows if row.transporter_id}
    by_position_id = {
        e.position_id: e.id
        for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.position_id.in_(position_ids)).all()
    } if position_ids else {}

    results = []
    for row in rows:
        safety = _weighted_avg(row, SAFETY_WEIGHTS)
        quality = _weighted_avg(row, QUALITY_WEIGHTS)

        roster_id = by_position_id.get(row.transporter_id) or resolve_roster_id(row.driver_name, db)
        attendance = _reliability_score(roster_id, row.driver_name, db, dvic_deductions, coaching_deductions, safety_deductions)

        parts = [
            (safety, CATEGORY_WEIGHTS["safety"]),
            (quality, CATEGORY_WEIGHTS["quality"]),
            (attendance, CATEGORY_WEIGHTS["attendance"]),
        ]
        available = [(v, w) for v, w in parts if v is not None]
        overall = (
            sum(v * w for v, w in available) / sum(w for _, w in available)
            if available else None
        )

        tenure_rec = get_latest_tenure_record(db, row.transporter_id) if row.transporter_id else None
        trailing_routes = (
            get_trailing_route_count(db, row.transporter_id, weeks=ROUTE_ELIGIBILITY_WEEKS)
            if row.transporter_id else 0
        )

        tenure_ok = bool(tenure_rec and tenure_rec.tenure_status == "Tenured")
        routes_ok = trailing_routes >= ROUTE_ELIGIBILITY_THRESHOLD
        ranking_eligible = tenure_ok and routes_ok

        results.append({
            "driver_name": row.driver_name,
            "transporter_id": row.transporter_id,
            "overall": round(overall, 1) if overall is not None else None,
            "safety": round(safety, 1) if safety is not None else None,
            "quality": round(quality, 1) if quality is not None else None,
            "attendance": round(attendance, 1),
            "overall_tier": tier_for(overall),
            "safety_tier": tier_for(safety),
            "quality_tier": tier_for(quality),
            "attendance_tier": tier_for(attendance),
            "ranking_eligible": ranking_eligible,
            "high_performer_eligible": ranking_eligible and overall is not None and overall >= HIGH_PERFORMER_THRESHOLD,
            "tenure_status": tenure_rec.tenure_status if tenure_rec else "Unknown",
            "trailing_routes": trailing_routes,
            "lifetime_routes": tenure_rec.lifetime_routes if tenure_rec else None,
        })

    results.sort(key=lambda r: (r["overall"] is None, -(r["overall"] or 0)))
    return results


def get_driver_metric_highlights(driver_name: str, db: Session, top_n: int = 2) -> dict:
    """Best- and worst-scoring safety/quality sub-metrics for one driver,
    for driver-facing coaching content (the morning assignment DM) --
    added 2026-07-31 per explicit request to surface both "doing great"
    and "room to grow" in the same place, not just weaknesses. Returns
    {"strengths": [...], "focus_areas": [...]}, each a list of
    {"label", "score"} dicts, newest snapshot only. Reuses quality.py's
    _METRIC_LABELS so the same metric names show consistently everywhere
    they're surfaced (this DM, the Wave Lead Team Focus page, etc.).

    Caps at len(scored)//2 per side so a driver with very few scored
    metrics never sees the same metric labeled both a strength and a
    focus area."""
    latest_snap = (
        db.query(QualityMetricSnapshot)
        .order_by(QualityMetricSnapshot.week.desc())
        .first()
    )
    if not latest_snap:
        return {"strengths": [], "focus_areas": []}

    row = (
        db.query(QualityMetricDriver)
        .filter(
            QualityMetricDriver.snapshot_id == latest_snap.id,
            func.lower(QualityMetricDriver.driver_name) == driver_name.lower(),
        )
        .first()
    )
    if not row:
        return {"strengths": [], "focus_areas": []}

    from api.src.routes.quality import _METRIC_LABELS

    all_metrics = {**SAFETY_WEIGHTS, **QUALITY_WEIGHTS}
    scored = [
        (label, float(getattr(row, attr)))
        for attr, label in _METRIC_LABELS.items()
        if attr in all_metrics and getattr(row, attr, None) is not None
    ]
    if not scored:
        return {"strengths": [], "focus_areas": []}

    scored.sort(key=lambda m: m[1], reverse=True)
    n = min(top_n, len(scored) // 2)
    if n == 0:
        return {"strengths": [], "focus_areas": []}

    strengths = [{"label": label, "score": round(score, 1)} for label, score in scored[:n]]
    focus_areas = [{"label": label, "score": round(score, 1)} for label, score in reversed(scored[-n:])]
    return {"strengths": strengths, "focus_areas": focus_areas}


@router.get("/scores")
def get_driver_scores(db: Session = Depends(get_db)):
    """Overall/Safety/Quality/Attendance scores for every driver in the
    latest quality snapshot, with color coding and bonus eligibility."""
    return {"drivers": compute_driver_scores(db)}
