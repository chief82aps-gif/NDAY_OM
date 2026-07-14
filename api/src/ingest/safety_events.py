"""Parse the Netradyne "Safety Dashboard" CSV export.

One row per real-world driving-safety event (speeding, roadside parking,
etc.). Columns confirmed against a real export
(Safety_Dashboard_NDAY_DLV3_2026-07-13.csv, 2026-07-14):

  Date, "Delivery Associate " (trailing space), Transporter ID, Event ID,
  "Date (Station Local Time)", VIN, Program Impact, Metric Type,
  Metric Subtype, Source, Video Link, Review Details

Never trust the filename/extension for format — see
api/src/column_mapping.py's read_tabular_file(), used here too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

import pandas as pd

from api.src.column_mapping import read_tabular_file


@dataclass
class SafetyEventRecord:
    event_id: str
    report_date: Optional[date]
    driver_name: Optional[str]
    transporter_id: Optional[str]
    event_at: Optional[datetime]
    vin: Optional[str]
    program_impact: Optional[str]
    metric_type: Optional[str]
    metric_subtype: Optional[str]
    source: Optional[str]
    video_link: Optional[str]
    review_details: Optional[str]


def _s(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() != "nan" else None


def _parse_date(val) -> Optional[date]:
    s = _s(val)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(val) -> Optional[datetime]:
    s = _s(val)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_safety_events(file_path: str) -> Tuple[List[SafetyEventRecord], List[str]]:
    """Parse a Safety Dashboard CSV/Excel file and return event records + errors."""
    errors: List[str] = []
    records: List[SafetyEventRecord] = []

    try:
        df = read_tabular_file(file_path, header=0)
    except Exception as e:
        return records, [f"Failed to read Safety Dashboard file: {e}"]

    # Header cells may carry stray whitespace (e.g. "Delivery Associate ").
    df.columns = [str(c).strip() for c in df.columns]

    required = {"Event ID"}
    missing = required - set(df.columns)
    if missing:
        errors.append(f"Safety Dashboard file missing required column(s): {sorted(missing)}")
        return records, errors

    for idx, row in df.iterrows():
        event_id = _s(row.get("Event ID"))
        if not event_id:
            errors.append(f"Row {idx + 2}: missing Event ID, skipped.")
            continue

        records.append(SafetyEventRecord(
            event_id=event_id,
            report_date=_parse_date(row.get("Date")),
            driver_name=_s(row.get("Delivery Associate")),
            transporter_id=_s(row.get("Transporter ID")),
            event_at=_parse_datetime(row.get("Date (Station Local Time)")),
            vin=_s(row.get("VIN")),
            program_impact=_s(row.get("Program Impact")),
            metric_type=_s(row.get("Metric Type")),
            metric_subtype=_s(row.get("Metric Subtype")),
            source=_s(row.get("Source")),
            video_link=_s(row.get("Video Link")),
            review_details=_s(row.get("Review Details")),
        ))

    return records, errors
