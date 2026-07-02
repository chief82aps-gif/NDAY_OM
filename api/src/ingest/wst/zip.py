"""WST ZIP ingest parser that dispatches to each CSV parser."""
from typing import Dict, List, Tuple
from io import BytesIO
import zipfile
import pandas as pd

from api.src.ingest.wst.delivered_packages import parse_delivered_packages_csv
from api.src.ingest.wst.service_details import parse_service_details_csv
from api.src.ingest.wst.training_weekly import parse_training_weekly_csv
from api.src.ingest.wst.unplanned_delay import parse_unplanned_delay_csv
from api.src.ingest.wst.weekly_report import parse_weekly_report_csv


FILE_MAP = {
    "delivered packages report": parse_delivered_packages_csv,
    "service details report": parse_service_details_csv,
    "training weekly report": parse_training_weekly_csv,
    "unplanned delay weekly report": parse_unplanned_delay_csv,
    "weekly report": parse_weekly_report_csv,
}


def ingest_wst_zip(zip_path: str) -> Tuple[Dict[str, List[Dict]], List[str]]:
    results: Dict[str, List[Dict]] = {}
    errors: List[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename.lower()
            if not name.endswith(".csv"):
                continue

            matched = None
            for key in FILE_MAP:
                if key in name:
                    matched = key
                    break

            if not matched:
                errors.append(f"Unrecognized file in zip: {info.filename}")
                continue

            data = zf.read(info.filename)
            df = pd.read_csv(BytesIO(data))

            records, parse_errors = FILE_MAP[matched](BytesIO(data)) if False else (df.to_dict(orient="records"), [])
            records, parse_errors = FILE_MAP[matched](BytesIO(data)) if hasattr(BytesIO(data), "read") else (df.to_dict(orient="records"), [])

            if not records:
                records = df.to_dict(orient="records")

            if parse_errors:
                errors.extend([f"{info.filename}: {e}" for e in parse_errors])

            results[matched] = records

    return results, errors
