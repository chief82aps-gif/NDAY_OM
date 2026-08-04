"""Parse Amazon's "Quality RTS" daily CSV export.

One row per package returned to station (RTS). Columns confirmed
against a real export (Quality_RTS_NDAY_DLV3_2026-08-03.csv, 2026-08-04):

  "Delivery Associate ", "Impacts Scorecard", "Tracking ID",
  "Transporter ID", "DA Selected RTS Code", "Additional Information",
  "Exemption Reason", "Planned Delivery Date", "Service Area"

A blank/"NO RTS CODE SELECTED" reason code defaults to a DC DPMO
scorecard defect (per Amazon's own RTS Dashboard documentation) unless
the package was reprocessed by the station while the driver was still
on the road -- this is the file used to identify which drivers need
coaching on always selecting a code before returning a package.

Never trust the filename/extension for format -- see
api/src/column_mapping.py's read_tabular_file(), used here too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

import pandas as pd

from api.src.column_mapping import read_tabular_file


@dataclass
class QualityRtsRecord:
    tracking_id: str
    driver_name: Optional[str]
    transporter_id: Optional[str]
    impacts_scorecard: bool
    rts_code: Optional[str]
    additional_information: Optional[str]
    exemption_reason: Optional[str]
    planned_delivery_date: Optional[date]
    service_area: Optional[str]


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


def parse_quality_rts(file_path: str) -> Tuple[List[QualityRtsRecord], List[str]]:
    """Parse a Quality RTS CSV/Excel file and return records + errors."""
    errors: List[str] = []
    records: List[QualityRtsRecord] = []

    try:
        df = read_tabular_file(file_path, header=0)
    except Exception as e:
        return records, [f"Failed to read Quality RTS file: {e}"]

    df.columns = [str(c).strip() for c in df.columns]

    required = {"Tracking ID"}
    missing = required - set(df.columns)
    if missing:
        errors.append(f"Quality RTS file missing required column(s): {sorted(missing)}")
        return records, errors

    for idx, row in df.iterrows():
        tracking_id = _s(row.get("Tracking ID"))
        if not tracking_id:
            errors.append(f"Row {idx + 2}: missing Tracking ID, skipped.")
            continue

        records.append(QualityRtsRecord(
            tracking_id=tracking_id,
            driver_name=_s(row.get("Delivery Associate")),
            transporter_id=_s(row.get("Transporter ID")),
            impacts_scorecard=(_s(row.get("Impacts Scorecard")) or "").upper() == "Y",
            rts_code=_s(row.get("DA Selected RTS Code")),
            additional_information=_s(row.get("Additional Information")),
            exemption_reason=_s(row.get("Exemption Reason")),
            planned_delivery_date=_parse_date(row.get("Planned Delivery Date")),
            service_area=_s(row.get("Service Area")),
        ))

    return records, errors
