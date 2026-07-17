"""
Driver Individual Scoring — a weighted percentage score per active driver,
mirroring the DSP Scorecard's own category weights (Appendix A of the
weekly scorecard PDF), with two deliberate departures per explicit
2026-07-17 decision:

  - Team & Fleet (Tenured Workforce 0% + Fleet Execution 5%) is dropped
    entirely from the weighted score -- these are DSP-wide/vehicle-level
    metrics, not something an individual driver's own behavior controls.
    That 5% is reassigned to a new Attendance component instead.
  - Driver tenure is NOT part of the weighted score at all -- it's a
    pass/fail eligibility gate (the Tenured Workforce report's own
    "Tenure Status" field), same as the 30-route trailing-6-week floor.

Category weights (sum to what's actually available at the per-driver
level -- Amazon's per-driver CSV gives one combined "CDF DPMO" score, not
split into Customer Delivery Feedback (5.7%) vs. Customer Escalation
Defect (11.3%) separately, so CDF here stands in for the full 17.0%
Customer Delivery Experience category):

  Safety (47.6% of Overall):
    Speeding 11.7 | Seatbelt 11.7 | Sign/Signal 11.7 | Distractions 7.5 | Following Distance 5.0
  Quality (47.4% of Overall):
    DC DPMO 11.3 | DSB 11.3 | POD 2.8 | CDF DPMO 17.0 | PSB 5.0
  Attendance (5.0% of Overall, reassigned from Fleet Execution):
    100 - (trailing-60-day attendance points x 10), floored at 0 -- reuses
    attendance.py's existing HRM-023.1 points ladder (10 points is that
    system's own termination threshold), no new data collection.

A missing/None component score is dropped and the remaining weights in
that category renormalized -- the same "Coming Soon" handling the real
scorecard documents in Appendix A, so one missing metric doesn't unfairly
zero out a driver's category score.

Eligibility (ranking + high-performer bonus) requires BOTH:
  - Tenure Status == "Tenured" (TenuredWorkforceRecord, latest week)
  - >= 30 routes in the trailing 6 weeks (routes_in_week summed)
A driver failing either still gets a score shown (useful for coaching),
just flagged ineligible rather than silently hidden.
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
    "speeding_score": 11.7,
    "seatbelt_score": 11.7,
    "sign_violation_score": 11.7,
    "distraction_score": 7.5,
    "following_distance_score": 5.0,
}
QUALITY_WEIGHTS = {
    "dc_dpmo_score": 11.3,
    "dsb_score": 11.3,
    "pod_score": 2.8,
    "cdf_dpmo_score": 17.0,
    "psb_score": 5.0,
}
CATEGORY_WEIGHTS = {
    "safety": sum(SAFETY_WEIGHTS.values()),      # 47.6
    "quality": sum(QUALITY_WEIGHTS.values()),    # 47.4
    "attendance": 5.0,
}

ROUTE_ELIGIBILITY_THRESHOLD = 30
ROUTE_ELIGIBILITY_WEEKS = 6
HIGH_PERFORMER_THRESHOLD = 92.0   # green
CAUTION_THRESHOLD = 90.0          # yellow; below this is red


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


def color_for(score: Optional[float]) -> str:
    if score is None:
        return "gray"
    if score >= HIGH_PERFORMER_THRESHOLD:
        return "green"
    if score >= CAUTION_THRESHOLD:
        return "yellow"
    return "red"


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
            "overall_color": color_for(overall),
            "safety_color": color_for(safety),
            "quality_color": color_for(quality),
            "attendance_color": color_for(attendance),
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
