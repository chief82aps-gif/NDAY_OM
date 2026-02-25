"""WST Unplanned Delay Weekly Report ingest parser."""
from typing import Dict, List, Tuple
import pandas as pd

REQUIRED_COLUMNS = [
    "date",
    "station",
    "dsp short code",
    "unplanned delay",
    "total delay in minutes",
    "impacted routes",
]


def parse_unplanned_delay_csv(file_path: str) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return [], errors

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["total delay in minutes"] = pd.to_numeric(df["total delay in minutes"], errors="coerce")
    df["impacted routes"] = pd.to_numeric(df["impacted routes"], errors="coerce")

    records = df.to_dict(orient="records")
    return records, errors
