"""Parse Amazon's "DSP Customer Delivery Feedback - negative" CSV export.

One row per negative customer feedback event, deduped by Amazon's own
Delivery Group ID -- the export is a rolling window (a single file can
span several delivery dates), same pattern as safety_events.py's
SafetyEvent. Columns confirmed against a real export
(DSP_Customer_Delivery_Feedback_negative_DLV3_2026-08-03.csv, 2026-08-04):

  "Delivery Group ID", "Delivery Associate", "Delivery Associate Name",
  "Tracking ID", "DA Mishandled Package", "DA was Unprofessional",
  "DA did not follow my delivery instructions", "Delivered to Wrong
  Address", "Never Received Delivery", "Received Wrong Item",
  "Feedback Details", "Delivery Date", "Scorecard Reporting week"

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
class CustomerFeedbackRecord:
    delivery_group_id: str
    driver_name: Optional[str]
    transporter_id: Optional[str]
    tracking_id: Optional[str]
    mishandled_package: bool
    unprofessional: bool
    did_not_follow_instructions: bool
    wrong_address: bool
    never_received: bool
    wrong_item: bool
    feedback_details: Optional[str]
    delivery_date: Optional[date]
    reporting_week: Optional[int]


def _s(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() != "nan" else None


def _bool(val) -> bool:
    s = _s(val)
    return s == "1"


def _int(val) -> Optional[int]:
    s = _s(val)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_date(val) -> Optional[date]:
    s = _s(val)
    if not s:
        return None
    s = s.split(" ")[0]   # "2026-08-02 15:03:16" -> "2026-08-02"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_customer_feedback(file_path: str) -> Tuple[List[CustomerFeedbackRecord], List[str]]:
    """Parse a Customer Delivery Feedback (negative) CSV/Excel file and
    return records + errors."""
    errors: List[str] = []
    records: List[CustomerFeedbackRecord] = []

    try:
        df = read_tabular_file(file_path, header=0)
    except Exception as e:
        return records, [f"Failed to read Customer Delivery Feedback file: {e}"]

    df.columns = [str(c).strip() for c in df.columns]

    required = {"Delivery Group ID"}
    missing = required - set(df.columns)
    if missing:
        errors.append(f"Customer Delivery Feedback file missing required column(s): {sorted(missing)}")
        return records, errors

    for idx, row in df.iterrows():
        delivery_group_id = _s(row.get("Delivery Group ID"))
        if not delivery_group_id:
            errors.append(f"Row {idx + 2}: missing Delivery Group ID, skipped.")
            continue

        records.append(CustomerFeedbackRecord(
            delivery_group_id=delivery_group_id,
            driver_name=_s(row.get("Delivery Associate Name")),
            transporter_id=_s(row.get("Delivery Associate")),
            tracking_id=_s(row.get("Tracking ID")),
            mishandled_package=_bool(row.get("DA Mishandled Package")),
            unprofessional=_bool(row.get("DA was Unprofessional")),
            did_not_follow_instructions=_bool(row.get("DA did not follow my delivery instructions")),
            wrong_address=_bool(row.get("Delivered to Wrong Address")),
            never_received=_bool(row.get("Never Received Delivery")),
            wrong_item=_bool(row.get("Received Wrong Item")),
            feedback_details=_s(row.get("Feedback Details")),
            delivery_date=_parse_date(row.get("Delivery Date")),
            reporting_week=_int(row.get("Scorecard Reporting week")),
        ))

    return records, errors
