"""
Cortex Tracking — 2-hour Cortex snapshots for pace tracking and performance prediction.

Every 2 hours during delivery, dispatch uploads the current Cortex file.
We parse it and store a snapshot per route code. Over time this builds a
per-driver performance history used to predict on-time completion.

Endpoints:
  POST /cortex-tracking/snapshot          ingest a Cortex file upload (2-hr snapshot)
  GET  /cortex-tracking/today             current-day snapshots with pace indicators
  GET  /cortex-tracking/performance/{driver_name}   historical performance by driver
  GET  /cortex-tracking/route/{route_code}           all snapshots for a route code
"""
from __future__ import annotations

import io
import logging
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    CortexSnapshot,
    DriverRoutePerformance,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cortex-tracking", tags=["cortex-tracking"])

# Column aliases for flexible Cortex file detection
_ROUTE_ALIASES    = ("route", "route code", "routecode", "route_code")
_DRIVER_ALIASES   = ("driver", "driver name", "driver_name", "da name")
_SVC_ALIASES      = ("service type", "service_type", "servicetype")
_WAVE_ALIASES     = ("wave time", "wave", "wave_time")
_TOTAL_ALIASES    = ("packages", "total packages", "total_packages", "pkg total", "planned packages")
_DELIVERED_ALIASES= ("delivered", "packages delivered", "pkgs delivered", "del")
_REMAINING_ALIASES= ("remaining", "packages remaining", "pkgs remaining", "undelivered")


def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", " ", str(name).lower()).strip()


def _find_col(df_cols: list[str], aliases: tuple) -> Optional[str]:
    normalized = {_normalize_col(c): c for c in df_cols}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _parse_cortex_snapshot(content: bytes, filename: str) -> list[dict]:
    """Parse a Cortex xlsx file into a list of route snapshot dicts."""
    try:
        df = pd.read_excel(io.BytesIO(content), header=None)
    except Exception as exc:
        raise ValueError(f"Cannot read Excel file: {exc}")

    # Detect header row (first row where 'route' appears)
    header_row = None
    for i, row in df.iterrows():
        combined = " ".join(str(v).lower() for v in row.values if pd.notna(v))
        if "route" in combined:
            header_row = i
            break

    if header_row is None:
        raise ValueError("Could not detect header row in Cortex file")

    df.columns = df.iloc[header_row].tolist()
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df.columns = [str(c) for c in df.columns]

    col_names = df.columns.tolist()
    route_col    = _find_col(col_names, _ROUTE_ALIASES)
    driver_col   = _find_col(col_names, _DRIVER_ALIASES)
    svc_col      = _find_col(col_names, _SVC_ALIASES)
    wave_col     = _find_col(col_names, _WAVE_ALIASES)
    total_col    = _find_col(col_names, _TOTAL_ALIASES)
    delivered_col= _find_col(col_names, _DELIVERED_ALIASES)
    remaining_col= _find_col(col_names, _REMAINING_ALIASES)

    if not route_col:
        raise ValueError("Route Code column not found in Cortex file")

    rows = []
    for _, row in df.iterrows():
        route_code = str(row.get(route_col, "") or "").strip().upper()
        if not route_code or route_code in ("NAN", "ROUTE CODE", "ROUTE"):
            continue

        total     = None
        delivered = None
        remaining = None

        if total_col:
            try:
                total = int(float(row[total_col]))
            except (ValueError, TypeError):
                pass
        if delivered_col:
            try:
                delivered = int(float(row[delivered_col]))
            except (ValueError, TypeError):
                pass
        if remaining_col:
            try:
                remaining = int(float(row[remaining_col]))
            except (ValueError, TypeError):
                pass

        # Derive remaining from total - delivered if not explicit
        if remaining is None and total is not None and delivered is not None:
            remaining = max(0, total - delivered)

        pct = None
        if total and total > 0 and delivered is not None:
            pct = round((delivered / total) * 100, 2)

        rows.append({
            "route_code": route_code,
            "driver_name": str(row.get(driver_col, "") or "").strip() if driver_col else None,
            "service_type": str(row.get(svc_col, "") or "").strip() if svc_col else None,
            "wave_time": str(row.get(wave_col, "") or "").strip() if wave_col else None,
            "packages_total": total,
            "packages_delivered": delivered,
            "packages_remaining": remaining,
            "pct_complete": pct,
        })

    return rows


def _update_performance_history(route_date: date, db: Session):
    """Rebuild DriverRoutePerformance from today's snapshots."""
    snapshots = (
        db.query(CortexSnapshot)
        .filter(CortexSnapshot.route_date == route_date)
        .order_by(CortexSnapshot.snapshot_at)
        .all()
    )

    # Group by route_code
    by_route: dict[str, list[CortexSnapshot]] = {}
    for s in snapshots:
        by_route.setdefault(s.route_code, []).append(s)

    for route_code, snaps in by_route.items():
        if not snaps:
            continue
        driver_name = next((s.driver_name for s in snaps if s.driver_name), None)
        wave_time   = next((s.wave_time for s in snaps if s.wave_time), None)
        svc_type    = next((s.service_type for s in snaps if s.service_type), None)

        # Find pct at ~2hr and ~4hr mark (closest snapshot)
        pct_2hr = None
        pct_4hr = None
        final_pct = None

        for i, snap in enumerate(snaps):
            pct = float(snap.pct_complete or 0)
            if i == 0:
                pct_2hr = pct
            elif i == 1:
                pct_4hr = pct
            final_pct = pct

        finished_on_time = final_pct is not None and final_pct >= 99.0

        rec = db.query(DriverRoutePerformance).filter(
            DriverRoutePerformance.route_date == route_date,
            DriverRoutePerformance.route_code == route_code,
        ).first()

        if not rec:
            rec = DriverRoutePerformance(route_date=route_date, route_code=route_code)
            db.add(rec)

        rec.driver_name = driver_name
        rec.service_type = svc_type
        rec.wave_time = wave_time
        rec.pct_at_2hr = Decimal(str(pct_2hr)) if pct_2hr is not None else None
        rec.pct_at_4hr = Decimal(str(pct_4hr)) if pct_4hr is not None else None
        rec.final_pct = Decimal(str(final_pct)) if final_pct is not None else None
        rec.finished_on_time = finished_on_time
        rec.snapshot_count = len(snaps)

    db.commit()


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/snapshot")
async def ingest_cortex_snapshot(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Accept a Cortex xlsx upload and store a progress snapshot for every route.
    Call this every ~2 hours during delivery to track pace.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xls files accepted")

    content = await file.read()
    snapshot_at = datetime.utcnow()
    route_date = snapshot_at.date()

    try:
        rows = _parse_cortex_snapshot(content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    saved = 0
    for row in rows:
        snap = CortexSnapshot(
            snapshot_at=snapshot_at,
            route_date=route_date,
            route_code=row["route_code"],
            driver_name=row["driver_name"] or None,
            service_type=row["service_type"] or None,
            wave_time=row["wave_time"] or None,
            packages_total=row["packages_total"],
            packages_delivered=row["packages_delivered"],
            packages_remaining=row["packages_remaining"],
            pct_complete=Decimal(str(row["pct_complete"])) if row["pct_complete"] is not None else None,
            source_file=file.filename,
        )
        db.add(snap)
        saved += 1

    db.commit()

    # Update rolling performance history
    try:
        _update_performance_history(route_date, db)
    except Exception as exc:
        logger.warning("Performance history update failed: %s", exc)

    return {
        "status": "ingested",
        "snapshot_at": snapshot_at.isoformat(),
        "routes_captured": saved,
        "filename": file.filename,
    }


@router.get("/today")
def get_today_snapshots(db: Session = Depends(get_db)):
    """Return the most recent snapshot per route for today, with pace indicators."""
    today = datetime.utcnow().date()

    # Latest snapshot_at per route for today
    latest_subq = (
        db.query(
            CortexSnapshot.route_code,
            func.max(CortexSnapshot.snapshot_at).label("latest_at"),
        )
        .filter(CortexSnapshot.route_date == today)
        .group_by(CortexSnapshot.route_code)
        .subquery()
    )

    snaps = (
        db.query(CortexSnapshot)
        .join(latest_subq, (CortexSnapshot.route_code == latest_subq.c.route_code) &
              (CortexSnapshot.snapshot_at == latest_subq.c.latest_at))
        .filter(CortexSnapshot.route_date == today)
        .order_by(CortexSnapshot.route_code)
        .all()
    )

    def _pace(snap: CortexSnapshot) -> str:
        pct = float(snap.pct_complete or 0)
        if pct >= 90:
            return "on_track"
        if pct >= 60:
            return "moderate"
        return "behind"

    return {
        "date": today.isoformat(),
        "route_count": len(snaps),
        "routes": [
            {
                "route_code": s.route_code,
                "driver_name": s.driver_name,
                "wave_time": s.wave_time,
                "service_type": s.service_type,
                "packages_total": s.packages_total,
                "packages_delivered": s.packages_delivered,
                "packages_remaining": s.packages_remaining,
                "pct_complete": float(s.pct_complete) if s.pct_complete else None,
                "pace": _pace(s),
                "snapshot_at": s.snapshot_at.isoformat(),
            }
            for s in snaps
        ],
    }


@router.get("/performance/{driver_name}")
def get_driver_performance(driver_name: str, days: int = 30, db: Session = Depends(get_db)):
    """Historical pace performance for a driver over the last N days."""
    since = datetime.utcnow().date() - timedelta(days=days)
    records = (
        db.query(DriverRoutePerformance)
        .filter(
            DriverRoutePerformance.driver_name == driver_name,
            DriverRoutePerformance.route_date >= since,
        )
        .order_by(DriverRoutePerformance.route_date.desc())
        .all()
    )

    on_time_count = sum(1 for r in records if r.finished_on_time)
    avg_2hr = None
    if records:
        valid = [float(r.pct_at_2hr) for r in records if r.pct_at_2hr is not None]
        avg_2hr = round(sum(valid) / len(valid), 1) if valid else None

    return {
        "driver_name": driver_name,
        "days_back": days,
        "shifts_tracked": len(records),
        "on_time_rate": round(on_time_count / len(records) * 100, 1) if records else None,
        "avg_pct_at_2hr": avg_2hr,
        "history": [
            {
                "date": r.route_date.isoformat(),
                "route_code": r.route_code,
                "service_type": r.service_type,
                "pct_at_2hr": float(r.pct_at_2hr) if r.pct_at_2hr else None,
                "pct_at_4hr": float(r.pct_at_4hr) if r.pct_at_4hr else None,
                "final_pct": float(r.final_pct) if r.final_pct else None,
                "finished_on_time": r.finished_on_time,
            }
            for r in records
        ],
    }


@router.get("/route/{route_code}")
def get_route_snapshots(route_code: str, days: int = 7, db: Session = Depends(get_db)):
    """All snapshots for a route code over the last N days."""
    since = datetime.utcnow().date() - timedelta(days=days)
    snaps = (
        db.query(CortexSnapshot)
        .filter(
            CortexSnapshot.route_code == route_code.upper(),
            CortexSnapshot.route_date >= since,
        )
        .order_by(CortexSnapshot.snapshot_at.desc())
        .all()
    )
    return {
        "route_code": route_code.upper(),
        "snapshots": [
            {
                "snapshot_at": s.snapshot_at.isoformat(),
                "route_date": s.route_date.isoformat(),
                "driver_name": s.driver_name,
                "pct_complete": float(s.pct_complete) if s.pct_complete else None,
                "delivered": s.packages_delivered,
                "remaining": s.packages_remaining,
                "total": s.packages_total,
            }
            for s in snaps
        ],
    }
