"""
Ingest module for Amazon DSP Overview Dashboard — Trailing Six Week CSV.

Parses the per-driver performance export and populates quality_metric_snapshots
and quality_metric_drivers. Safe to call repeatedly — deduplicates on slack_file_id.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dec(value: str) -> Optional[Decimal]:
    if not value or value.strip() in ("", "—", "N/A"):
        return None
    # Strip trailing % if present
    v = value.strip().rstrip("%")
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _int(value: str) -> Optional[int]:
    d = _dec(value)
    return int(d) if d is not None else None


def _pod_pct(value: str) -> Optional[Decimal]:
    """'99.1%' → Decimal('0.991')"""
    if not value or not value.strip():
        return None
    v = value.strip().rstrip("%")
    try:
        return Decimal(v) / Decimal("100")
    except InvalidOperation:
        return None


def _infer_week(filename: str) -> str:
    """Extract '2026-W26' from filenames like '...2026-W26.csv'."""
    m = re.search(r"(20\d{2}-W\d{1,2})", filename or "")
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# Column map (header → attribute name)
# ─────────────────────────────────────────────────────────────────────────────

_COL = {
    "Week":                                      "week",
    "Delivery Associate ":                       "driver_name",   # trailing space in header
    "Delivery Associate":                        "driver_name",
    "Transporter ID":                            "transporter_id",
    "Overall Standing":                          "overall_standing",
    "Overall Score":                             "overall_score",
    "Speeding Event Rate (per trip)":            "speeding_rate",
    "Speeding Event Rate Score":                 "speeding_score",
    "Seatbelt-Off Rate (per trip)":              "seatbelt_rate",
    "Seatbelt-Off Rate Score":                   "seatbelt_score",
    "Distractions Rate (per trip)":              "distraction_rate",
    "Distractions Rate Score":                   "distraction_score",
    "Sign/ Signal Violations Rate (per trip)":   "sign_violation_rate",
    "Sign/ Signal Violations Rate Score":        "sign_violation_score",
    "Following Distance Rate (per trip)":        "following_distance_rate",
    "Following Distance Rate Score":             "following_distance_score",
    "CDF DPMO":                                  "cdf_dpmo",
    "CDF DPMO Score":                            "cdf_dpmo_score",
    "Delivery Completion DPMO":                  "dc_dpmo",
    "Delivery Completion DPMO Score":            "dc_dpmo_score",
    "DSB":                                       "dsb_count",
    "DSB DPMO Score":                            "dsb_score",
    "POD":                                       "pod_pct",
    "POD Score":                                 "pod_score",
    "PSB":                                       "psb_rate",
    "PSB Score":                                 "psb_score",
    "Packages Delivered":                        "packages_delivered",
}

_DEC_FIELDS = {
    "overall_score", "speeding_rate", "speeding_score",
    "seatbelt_rate", "seatbelt_score", "distraction_rate", "distraction_score",
    "sign_violation_rate", "sign_violation_score", "following_distance_rate",
    "following_distance_score", "cdf_dpmo", "cdf_dpmo_score",
    "dc_dpmo", "dc_dpmo_score", "dsb_score", "pod_score",
    "psb_rate", "psb_score",
}
_INT_FIELDS = {"dsb_count", "packages_delivered"}
_POD_FIELDS = {"pod_pct"}


# ─────────────────────────────────────────────────────────────────────────────
# Public parse function
# ─────────────────────────────────────────────────────────────────────────────

def parse_quality_metrics_csv(
    content: bytes,
    filename: str = "",
) -> Tuple[dict, list]:
    """
    Parse the DSP Overview Dashboard trailing six-week CSV.

    Returns:
        (summary_dict, driver_list)
        summary_dict: {"week": str, "driver_count": int}
        driver_list:  list of dicts with field names matching QualityMetricDriver columns
    """
    errors: list = []
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    # Build a normalised header → attribute map for this file's actual headers
    headers = reader.fieldnames or []
    col_map: dict[str, str] = {}
    for h in headers:
        key = h.strip()
        if key in _COL:
            col_map[h] = _COL[key]

    drivers = []
    week_seen = ""

    for i, row in enumerate(reader, start=2):
        driver_name = (row.get("Delivery Associate ") or row.get("Delivery Associate") or "").strip()
        if not driver_name:
            continue

        rec: dict = {}
        for raw_col, attr in col_map.items():
            raw = (row.get(raw_col) or "").strip()

            if attr == "week":
                rec["week"] = raw
                if raw and not week_seen:
                    week_seen = raw
            elif attr == "driver_name":
                rec["driver_name"] = raw
            elif attr == "transporter_id":
                rec["transporter_id"] = raw or None
            elif attr == "overall_standing":
                rec["overall_standing"] = raw or None
            elif attr in _DEC_FIELDS:
                rec[attr] = _dec(raw)
            elif attr in _INT_FIELDS:
                rec[attr] = _int(raw)
            elif attr in _POD_FIELDS:
                rec[attr] = _pod_pct(raw)

        if "driver_name" not in rec or not rec["driver_name"]:
            continue

        drivers.append(rec)

    inferred_week = week_seen or _infer_week(filename)
    summary = {"week": inferred_week, "driver_count": len(drivers)}
    return summary, drivers
