"""
Tenured Workforce ingest — Amazon's weekly "Tenured Workforce DAs Report"
(logistics.amazon.com -> Performance -> Interactive Report -> Supplementary
Reports -> TWF Dashboard; exported via the three-stacked-dots menu ->
Export to CSV). Backs the driver-score tenure gate and the trailing-6-week
route-count ranking/bonus eligibility gate.

The same file re-exports Amazon's full history every week (53+ weeks in
the first real export, 2026-07-17), so ingestion upserts by
(transporter_id, year, week) rather than replacing an entire source_file's
rows the way DOP/Cortex do — past weeks are immutable, only the newest
week is actually new data on a given Friday.

Called from ops_ingest.py's dispatcher, same pattern as dvic.py/
safety_events.py/quality.py's own _store_* functions.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from api.src.column_mapping import read_tabular_file
from api.src.database import get_db, TenuredWorkforceRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenured-workforce", tags=["tenured-workforce"])


def _str_or_none(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value) -> Optional[int]:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _store_tenured_workforce(content: bytes, filename: str, slack_file_id: Optional[str], db: Session) -> dict:
    ext = os.path.splitext(filename)[1].lower() or ".csv"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    required = {"Trabsporter ID", "Year", "Week"}
    df = None
    # header=0 works for a plain CSV export. A real .xlsx export (confirmed
    # 2026-08-05) instead carries a title row ("Tenured Workforce DAs
    # Report") PLUS a blank row before the real header row (row index 2),
    # which header=0 would read as column names -- try a small range
    # before concluding it's the wrong tab.
    for header_row in (0, 1, 2, 3):
        try:
            candidate = read_tabular_file(tmp_path, header=header_row)
        except Exception as exc:
            if df is None:
                last_exc = exc
            continue
        candidate.columns = [str(c).strip() for c in candidate.columns]
        if required.issubset(set(candidate.columns)):
            df = candidate
            break
        if df is None:
            df = candidate  # keep the last attempt around for the error message below

    if df is None:
        return {"status": "error", "message": f"Could not parse file: {last_exc}"}

    missing = required - set(df.columns)
    if missing:
        # By far the most common remaining cause (confirmed 2026-07-18):
        # Amazon's TWF Dashboard portal has two tabs -- "Tenured Workforce
        # Calculation Report" and "Tenured Workforce DAs Report" -- and
        # exporting from the wrong one silently produces a differently-
        # shaped file. Name the likely fix, don't just report columns.
        return {
            "status": "error",
            "message": (
                f"Missing expected columns {sorted(missing)} — this looks like the wrong "
                "export from Amazon's TWF Dashboard. The portal has two tabs: "
                "'Tenured Workforce Calculation Report' and 'Tenured Workforce DAs Report'. "
                "Make sure the 'Tenured Workforce DAs Report' tab is selected (bold/highlighted) "
                "before using Export to CSV, then re-upload."
            ),
        }

    try:

        existing_keys = {
            (r.transporter_id, r.year, r.week)
            for r in db.query(
                TenuredWorkforceRecord.transporter_id,
                TenuredWorkforceRecord.year,
                TenuredWorkforceRecord.week,
            ).all()
        }

        inserted = 0
        for _, row in df.iterrows():
            tid = _str_or_none(row.get("Trabsporter ID"))
            year = _int_or_none(row.get("Year"))
            week = _int_or_none(row.get("Week"))
            if not tid or year is None or week is None:
                continue

            key = (tid, year, week)
            if key in existing_keys:
                continue  # historical week already stored, immutable

            db.add(TenuredWorkforceRecord(
                dsp=_str_or_none(row.get("Dsp")),
                station=_str_or_none(row.get("Station")),
                year=year,
                week=week,
                employee_id=_str_or_none(row.get("Employee ID")),
                transporter_id=tid,
                da_name=_str_or_none(row.get("DA Name")),
                days_since_last_delivery=_int_or_none(row.get("Days Since Last Delivery")),
                delivery_status=_str_or_none(row.get("Delivery Status")),
                driver_status=_str_or_none(row.get("Driver Status")),
                tenure_status=_str_or_none(row.get("Tenure Status")),
                lifetime_routes=_int_or_none(row.get("Lifetime Routes")),
                routes_in_week=_int_or_none(row.get("Routes in Week")),
                source_file=filename,
            ))
            existing_keys.add(key)
            inserted += 1

        db.commit()
        return {"status": "ingested", "records": inserted, "total_rows": len(df)}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Direct upload, for when the file was downloaded by hand rather
    than shared in Slack -- same pattern as packages.py's endpoint of
    the same name."""
    content = await file.read()
    return _store_tenured_workforce(content, file.filename or "upload.xlsx", None, db)
