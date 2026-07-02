import re
from datetime import datetime
from typing import Iterable, Optional


SERVICE_TYPE_MATRIX = [
    ("RIVIAN", [r"\belectric\b", r"\brivian\b"]),
    ("DELIVERY", [r"delivery\s+complete", r"delivered\s+packages", r"\bdelivery\b"]),
    ("PICKUP", [r"pickup\s+complete", r"pickup\s+packages", r"locker[_\s-]*return", r"\bpickup\b", r"\breturn\b"]),
    ("AMZL_LATE_CANCEL", [r"amzl\s+late\s+cancel", r"amazon\s+late\s+cancel"]),
    ("DSP_LATE_CANCEL", [r"dsp\s+late\s+cancel"]),
    ("CANCELLATION", [r"\bcancel", r"late\s+cancel"]),
    ("TRAINING", [r"\btraining\b", r"on[\s-]*road\s+experience"]),
    ("NURSERY", [r"\bnursery\b"]),
    ("STANDARD_PARCEL", [r"standard\s+parcel"]),
    ("ROUTE", [r"block\s+of\s+\d+\s+hours", r"\broute\b", r"4wd\s+p\d+"]),
]


def _normalize(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().split())


def map_service_type(*values: Optional[str]) -> str:
    text = " ".join(_normalize(v) for v in values if v)
    if not text:
        return "UNKNOWN"

    # Deterministic business rule: any Electric mention is treated as Rivian service type.
    if re.search(r"\belectric\b|\brivian\b", text, re.IGNORECASE):
        return "RIVIAN"

    for canonical, patterns in SERVICE_TYPE_MATRIX:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return canonical

    return "OTHER"


def to_float(value) -> float:
    cleaned = str(value).strip().replace(',', '')
    return float(cleaned) if cleaned else 0.0


def parse_date_iso_or_us(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date().isoformat()
        except Exception:
            continue
    return None


def infer_day_bucket(service_date: Optional[str], description: Optional[str]) -> str:
    day = parse_date_iso_or_us(service_date)
    if day:
        return day

    text = _normalize(description)
    if "weekday" in text:
        return "WEEKDAY"
    if "weekend" in text:
        return "WEEKEND"

    day_tokens = {
        "monday": "MONDAY",
        "tuesday": "TUESDAY",
        "wednesday": "WEDNESDAY",
        "thursday": "THURSDAY",
        "friday": "FRIDAY",
        "saturday": "SATURDAY",
        "sunday": "SUNDAY",
    }
    for token, label in day_tokens.items():
        if token in text:
            return label

    return "WEEK_TOTAL"
