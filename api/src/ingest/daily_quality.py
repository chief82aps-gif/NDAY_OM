"""
Ingest module for Amazon's "Quality Overview" daily CSV -- added
2026-07-31. Released ~30-48 hours after delivery completion (per
explicit user note), a narrower daily counterpart to the weekly DSP
Scorecard: just packages/routes volume, RTS-controllable count, POD%,
and DSB count, one row per driver per day.

Real columns (Quality_Overview_NDAY_DLV3_2026-07-29.csv):
  Date, Delivery Associate , Transporter ID, Packages Delivered,
  Routes Completed, Packages Returned to Station - DA Controllable,
  POD, DSB Count
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple


def _int(value: str) -> Optional[int]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return int(Decimal(v))
    except InvalidOperation:
        return None


def _pod_pct(value: str) -> Optional[Decimal]:
    """'99.17%' -> Decimal('0.9917')"""
    v = (value or "").strip()
    if not v:
        return None
    v = v.rstrip("%")
    try:
        return Decimal(v) / Decimal("100")
    except InvalidOperation:
        return None


def _infer_date(filename: str) -> str:
    """Extract '2026-07-29' from filenames like '...2026-07-29.csv'."""
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", filename or "")
    return m.group(1) if m else ""


_COL = {
    "Date": "report_date",
    "Delivery Associate ": "driver_name",   # trailing space in header, matches weekly convention
    "Delivery Associate": "driver_name",
    "Transporter ID": "transporter_id",
    "Packages Delivered": "packages_delivered",
    "Routes Completed": "routes_completed",
    "Packages Returned to Station - DA Controllable": "packages_rts_da_controllable",
    "POD": "pod_pct",
    "DSB Count": "dsb_count",
}

_INT_FIELDS = {"packages_delivered", "routes_completed", "packages_rts_da_controllable", "dsb_count"}


def parse_daily_quality_csv(content: bytes, filename: str = "") -> Tuple[dict, list]:
    """
    Parse the Quality Overview daily CSV.

    Returns:
        (summary_dict, driver_list)
        summary_dict: {"report_date": "YYYY-MM-DD", "driver_count": int}
        driver_list:  list of dicts with field names matching DailyQualityRecord columns
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    headers = reader.fieldnames or []
    col_map: dict[str, str] = {}
    for h in headers:
        key = h.strip()
        if key in _COL:
            col_map[h] = _COL[key]

    drivers = []
    date_seen = ""

    for row in reader:
        driver_name = (row.get("Delivery Associate ") or row.get("Delivery Associate") or "").strip()
        if not driver_name:
            continue

        rec: dict = {"driver_name": driver_name}
        for raw_col, attr in col_map.items():
            raw = (row.get(raw_col) or "").strip()

            if attr == "report_date":
                if raw and not date_seen:
                    date_seen = raw
            elif attr == "transporter_id":
                rec["transporter_id"] = raw or None
            elif attr in _INT_FIELDS:
                rec[attr] = _int(raw)
            elif attr == "pod_pct":
                rec["pod_pct"] = _pod_pct(raw)

        drivers.append(rec)

    inferred_date = date_seen or _infer_date(filename)
    summary = {"report_date": inferred_date, "driver_count": len(drivers)}
    return summary, drivers
