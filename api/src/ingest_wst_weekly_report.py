"""WST Weekly Report ingest parser."""
from typing import Dict, List, Tuple
import pandas as pd

REQUIRED_COLUMNS = [
    "date",
    "station",
    "dsp short code",
    "service type",
    "planned duration",
    "total distance planned",
    "total distance allowance",
    "planned distance unit",
    "amzl late cancel",
    "dsp late cancel",
    "completed routes",
]


def parse_weekly_report_csv(file_path: str) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return [], errors

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["amzl late cancel"] = pd.to_numeric(df["amzl late cancel"], errors="coerce")
    df["dsp late cancel"] = pd.to_numeric(df["dsp late cancel"], errors="coerce")
    df["completed routes"] = pd.to_numeric(df["completed routes"], errors="coerce")

    records = df.to_dict(orient="records")
    return records, errors
