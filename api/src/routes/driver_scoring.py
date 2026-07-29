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
  Attendance (20% of overall):
    100 - (trailing-60-day attendance points x 10), floored at 0 -- reuses
    attendance.py's existing HRM-023.1 points ladder (10 points is that
    system's own termination threshold, so e.g. 8 points nets a 20%
    attendance score), no new data collection.

Team & Fleet (Tenured Workforce + Fleet Execution) is dropped entirely
from the weighted score -- these are DSP-wide/vehicle-level metrics, not
something an individual driver's own behavior controls. Driver tenure is
NOT part of the weighted score at all -- it's a pass/fail eligibility
gate (the Tenured Workforce report's own "Tenure Status" field), same as
the 30-route trailing-6-week floor.

Tier cutoffs (TIER_THRESHOLDS) were recalibrated 2026-07-29 for this
20/40/40 blend, per explicit direction: Platinum >99, Gold 98-99,
Silver 97-98, Bronze <=97.

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
    "safety": 40.0,
    "quality": 40.0,
    "attendance": 20.0,
}

ROUTE_ELIGIBILITY_THRESHOLD = 30
ROUTE_ELIGIBILITY_WEEKS = 6

# Recalibrated 2026-07-29 for the 20/40/40 blend (explicit request) --
# these no longer mirror Amazon's own DA Performance page cutoffs, they're
# NDAY's own bands for the blended overall score: Platinum >99, Gold
# 98-99, Silver 97-98, Bronze <=97. Each tier's threshold is its own upper
# bound (you must exceed a tier's listed number to reach the tier above it).
TIER_THRESHOLDS = [
    ("platinum", 99.0),
    ("gold", 98.0),
    ("silver", 97.0),
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
            "lifetime_routes": tenure_rec.lifetime_routes if tenure_rec else None,
        })

    results.sort(key=lambda r: (r["overall"] is None, -(r["overall"] or 0)))
    return results


@router.get("/scores")
def get_driver_scores(db: Session = Depends(get_db)):
    """Overall/Safety/Quality/Attendance scores for every driver in the
    latest quality snapshot, with color coding and bonus eligibility."""
    return {"drivers": compute_driver_scores(db)}
