"""
Fuzzy-match driver roster names against an SSN export (real callout PINs)
and a Slack workspace member export (Slack IDs for driver DMs).

Extracted 2026-07-15 from scripts/import_ssn_slack.py so the same logic
is usable both as a local CLI script and as a proper API upload endpoint
(api/src/routes/drivers.py) — the app never needs raw production DB
credentials handed to a local script when this exists.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.82   # SequenceMatcher ratio cutoff


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy compare."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _build_ssn_candidates(first: str, middle: str, last: str) -> list[str]:
    """All plausible full-name formats from SSN file columns."""
    first  = (first  or "").strip()
    middle = (middle or "").strip()
    last   = (last   or "").strip()
    candidates = []
    if first and middle and last:
        candidates.append(f"{first} {middle} {last}")
        candidates.append(f"{first} {last}")
        candidates.append(f"{last} {first} {middle}")
        candidates.append(f"{last} {first}")
    elif first and last:
        candidates.append(f"{first} {last}")
        candidates.append(f"{last} {first}")
    return candidates


def load_ssn(path: str) -> list[dict]:
    """Load SSN xlsx → list of {candidates: [...], last4}"""
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    hdrs = [str(c.value).strip() if c.value else "" for c in ws[1]]
    rows = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(hdrs, raw))
        last4 = str(row.get("last 4") or "").strip().zfill(4)
        first  = str(row.get("Legal First Name")   or "").strip()
        middle = str(row.get("Legal Middle Name")  or "").strip()
        last   = str(row.get("Legal Last Name")    or "").strip()
        if not last4 or not (first or last):
            continue
        rows.append({"candidates": _build_ssn_candidates(first, middle, last), "last4": last4})
    return rows


def load_slack(path: str) -> list[dict]:
    """Load Slack xlsx → list of {user_id, username, display_name}"""
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    hdrs = [str(c.value).strip() if c.value else "" for c in ws[1]]
    rows = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(hdrs, raw))
        user_id      = str(row.get("User ID")      or "").strip()
        username     = str(row.get("Username")      or "").strip()
        display_name = str(row.get("Display Name") or "").strip()
        if user_id:
            rows.append({"user_id": user_id, "username": username, "display_name": display_name})
    return rows


def best_ssn_match(roster_name: str, ssn_rows: list[dict]) -> tuple[str | None, float]:
    """Find the SSN row whose candidate names best match roster_name."""
    best_last4, best_score = None, 0.0
    for row in ssn_rows:
        for cand in row["candidates"]:
            score = _ratio(roster_name, cand)
            if score > best_score:
                best_score = score
                best_last4 = row["last4"]
    if best_score >= MATCH_THRESHOLD:
        return best_last4, best_score
    return None, best_score


def best_slack_match(roster_name: str, slack_rows: list[dict]) -> tuple[str | None, str | None, float]:
    """Find Slack user whose display_name or username best matches roster_name."""
    best_id, best_display, best_score = None, None, 0.0
    parts = _norm(roster_name).split()
    first = parts[0] if parts else ""
    last  = parts[-1] if len(parts) > 1 else ""

    for row in slack_rows:
        candidates = []
        if row["display_name"]:
            candidates.append(row["display_name"])
        if row["username"]:
            candidates.append(row["username"])
        for cand in candidates:
            score = _ratio(roster_name, cand)
            # Boost if first OR last name appears in the candidate
            nc = _norm(cand)
            if first in nc or last in nc:
                score = max(score, 0.60)
            if score > best_score:
                best_score = score
                best_id      = row["user_id"]
                best_display = row["display_name"] or row["username"]

    if best_score >= MATCH_THRESHOLD:
        return best_id, best_display, best_score
    return None, None, best_score
