"""WST Service Details Report ingest parser."""
from typing import Dict, List, Tuple
import pandas as pd

REQUIRED_COLUMNS = [
    "date",
    "station",
    "dsp short code",
    "delivery associate",
    "route",
    "service type",
    "planned duration",
    "log in",
    "log out",
    "shipments delivered",
    "shipments returned",
    "pickup packages",
    "excluded?",
]


def parse_service_details_csv(file_path: str) -> Tuple[List[Dict], List[str]]:
    errors: List[str] = []
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return [], errors

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["log in"] = pd.to_datetime(df["log in"], errors="coerce")
    df["log out"] = pd.to_datetime(df["log out"], errors="coerce")
    df["shipments delivered"] = pd.to_numeric(df["shipments delivered"], errors="coerce")
    df["shipments returned"] = pd.to_numeric(df["shipments returned"], errors="coerce")
    df["pickup packages"] = pd.to_numeric(df["pickup packages"], errors="coerce")

    records = df.to_dict(orient="records")
    return records, errors
