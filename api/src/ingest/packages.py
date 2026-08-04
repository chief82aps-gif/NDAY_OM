"""Parse Amazon's "Packages" export — a live, company-wide dump of every
non-delivered package (Reattemptable, Undeliverable, Missing, Returned to
station, Pickup failed), with a per-package reason code. Pulled by hand
multiple times a day (immediately after the last wave launches, every
60-90 min after that, and at COB) -- added 2026-08-04.

Columns confirmed against a real export (Packages (3).csv, 2026-08-04):

  Scannable Id, Route Code, Company Short Code, Transporter Name,
  Transporter Id, Address, Package Status, Reason Code, Time of last scan

A blank/"NONE" Reason Code at this stage is the earliest possible signal
that a package is headed toward Amazon's "NO RTS CODE SELECTED" scorecard
defect (see quality_rts.py) -- this file lets the RTS debrief (rts.py)
force that question on the driver before they even reach the station.

Never trust the filename/extension for format -- see
api/src/column_mapping.py's read_tabular_file(), used here too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from api.src.column_mapping import read_tabular_file


@dataclass
class PackageRecord:
    tracking_id: str
    route_code: Optional[str]
    transporter_name: Optional[str]
    transporter_id: Optional[str]
    address: Optional[str]
    package_status: Optional[str]
    reason_code: Optional[str]
    last_scan_at: Optional[datetime]


def _s(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() != "nan" else None


def _reason(val) -> Optional[str]:
    """"NONE" is Amazon's own literal placeholder for "no reason yet" --
    normalized to None so callers can treat it the same as a blank cell."""
    s = _s(val)
    if not s or s.upper() == "NONE":
        return None
    return s


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


def parse_packages(file_path: str) -> Tuple[List[PackageRecord], List[str]]:
    """Parse a Packages CSV/Excel export and return records + errors."""
    errors: List[str] = []
    records: List[PackageRecord] = []

    try:
        df = read_tabular_file(file_path, header=0)
    except Exception as e:
        return records, [f"Failed to read Packages file: {e}"]

    df.columns = [str(c).strip() for c in df.columns]

    required = {"Scannable Id"}
    missing = required - set(df.columns)
    if missing:
        errors.append(f"Packages file missing required column(s): {sorted(missing)}")
        return records, errors

    for idx, row in df.iterrows():
        tracking_id = _s(row.get("Scannable Id"))
        if not tracking_id:
            errors.append(f"Row {idx + 2}: missing Scannable Id, skipped.")
            continue

        records.append(PackageRecord(
            tracking_id=tracking_id,
            route_code=_s(row.get("Route Code")),
            transporter_name=_s(row.get("Transporter Name")),
            transporter_id=_s(row.get("Transporter Id")),
            address=_s(row.get("Address")),
            package_status=_s(row.get("Package Status")),
            reason_code=_reason(row.get("Reason Code")),
            last_scan_at=_parse_datetime(row.get("Time of last scan")),
        ))

    return records, errors
