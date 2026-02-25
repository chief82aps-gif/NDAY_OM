"""WST Delivered Packages Report ingest parser."""
from typing import Dict, List, Tuple
import pandas as pd

REQUIRED_COLUMNS = [
    "date",
    "station",
    "dsp short code",
    "package count",
    "package type",
]


def parse_delivered_packages_csv(file_path: str) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return [], errors

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["package count"] = pd.to_numeric(df["package count"], errors="coerce")

    records = df.to_dict(orient="records")
    return records, errors
