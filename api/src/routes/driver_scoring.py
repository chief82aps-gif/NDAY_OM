"""
Driver Individual Scoring — a weighted percentage score per active driver,
mirroring the DSP Scorecard's own category weights (Appendix A of the
weekly scorecard PDF), with two deliberate departures per explicit
2026-07-17 decision:

  - Team & Fleet (Tenured Workforce 0% + Fleet Execution 5%) is dropped
    entirely from the weighted score -- these are DSP-wide/vehicle-level
    metrics, not something an individual driver's own behavior controls.
    A separate 5.0 Attendance component is added instead (see below) --
    the two aren't meant to sum to a fixed 100 with everything else;
    compute_driver_scores() normalizes by whatever weight is actually
    available, not a hardcoded total.
  - Driver tenure is NOT part of the weighted score at all -- it's a
    pass/fail eligibility gate (the Tenured Workforce report's own
    "Tenure Status" field), same as the 30-route trailing-6-week floor.

Category weights below match Amazon's current DA Performance Scoring
config (screenshot, 2026-07-22) -- their own weighting page no longer
carves out a separate Team & Fleet slice at all (Safety + Quality sum to
100% on their side), so these updated numbers are simply the 12 metric
weights straight off that page. Amazon's per-driver CSV still gives one
combined "CDF DPMO" score rather than Customer Delivery Feedback and
Customer Escalation Defect (CED) separately, so CDF here continues to
stand in for both combined (5.9 + 11.9 = 17.8):

  Safety (50.0 total):
    Speeding 12.5 | Seatbelt 12.5 | Sign/Signal 12.5 | Distractions 7.5 | Following Distance 5.0
  Quality (50.0 total):
    DC DPMO 11.9 | DSB 11.9 | POD 2.9 | CDF DPMO (+CED) 17.8 | PSB 5.5
  Attendance (5.0, same as before -- Amazon's weighting page has no
  equivalent category, this stays our own addition):
    100 - (trailing-60-day attendance points x 10), floored at 0 -- reuses
    attendance.py's existing HRM-023.1 points ladder (10 points is that
    system's own termination threshold), no new data collection.

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

Tier thresholds (tier_for()) mirror Amazon's own Platinum/Gold/Silver/
Bronze cutoffs (screenshot, 2026-07-22: Platinum >99.48, Gold >94,
Silver >92, Bronze <=92) -- applied here to OUR custom-blended overall
score, not Amazon's own overall_score, per explicit 2026-07-22 decision.
high_performer_eligible deliberately keeps the exact same 92.0 floor the
old green/yellow/red system used (i.e. any named tier, not just
Platinum) -- switching what counts as "high performer" would be a real
bonus-eligibility policy change nobody asked for here, so it's
preserved as-is even though the tier *names* changed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    QualityMetricDriver,
    QualityMetricSnapshot,
    get_trailing_route_count,
    get_latest_tenure_record,
)

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
    "safety": sum(SAFETY_WEIGHTS.values()),      # 50.0
    "quality": sum(QUALITY_WEIGHTS.values()),    # 50.0
    "attendance": 5.0,
}

ROUTE_ELIGIBILITY_THRESHOLD = 30
ROUTE_ELIGIBILITY_WEEKS = 6

# Amazon's current DA Performance tier cutoffs (screenshot, 2026-07-22).
# Each tier's threshold is its own upper bound per Amazon's UI ("Gold
# threshold represents the upper bound for Platinum") -- i.e. you need to
# exceed a tier's own listed number to reach the tier above it.
TIER_THRESHOLDS = [
    ("platinum", 99.48),
    ("gold", 94.0),
    ("silver", 92.0),
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


def _attendance_score(driver_name: str, db: Session) -> float:
    from api.src.routes.attendance import _driver_points_summary
    summary = _driver_points_summary(driver_name, db)
    points = summary["current_points"]
    return max(0.0, 100.0 - points * 10.0)


def tier_for(score: Optional[float]) -> str:
    """Platinum/Gold/Silver/Bronze per Amazon's current cutoffs (see
    TIER_THRESHOLDS above), applied to our own blended overall/category
    scores. "gray" for a score we couldn't compute at all (missing data),
    "bronze" for anything at or below the Silver cutoff -- Amazon's own
    UI doesn't define a tier below Bronze."""
    if score is None:
        return "gray"
    for tier_name, cutoff in TIER_THRESHOLDS:
        if score > cutoff:
            return tier_name
    return "bronze"


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

    results = []
    for row in rows:
        safety = _weighted_avg(row, SAFETY_WEIGHTS)
        quality = _weighted_avg(row, QUALITY_WEIGHTS)
        attendance = _attendance_score(row.driver_name, db)

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
        })

    results.sort(key=lambda r: (r["overall"] is None, -(r["overall"] or 0)))
    return results


@router.get("/scores")
def get_driver_scores(db: Session = Depends(get_db)):
    """Overall/Safety/Quality/Attendance scores for every driver in the
    latest quality snapshot, with color coding and bonus eligibility."""
    return {"drivers": compute_driver_scores(db)}
