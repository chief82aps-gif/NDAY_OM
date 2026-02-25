"""WST Training Weekly Report ingest parser."""
from typing import Dict, List, Tuple
import pandas as pd

REQUIRED_COLUMNS = [
    "assignment date",
    "payment date",
    "station",
    "dsp short code",
    "delivery associate",
    "service type",
    "course name",
    "dsp payment eligible",
]


def parse_training_weekly_csv(file_path: str) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return [], errors

    df["assignment date"] = pd.to_datetime(df["assignment date"], errors="coerce").dt.date
    df["payment date"] = pd.to_datetime(df["payment date"], errors="coerce").dt.date

    records = df.to_dict(orient="records")
    return records, errors
