"""
Parse the weekly Amazon DSP Delivery Excellence Scorecard PDF.

Extracts:
  - Overall score + standing
  - Category standings (Safety, Delivery Quality, Pickup Quality, Team & Fleet)
  - Individual metric values + standings
  - Recommended focus areas
  - Metric weightings from Appendix A
"""
from __future__ import annotations

import io
import re
from typing import Optional

STANDINGS = ["Poor", "Fair", "Great", "Fantastic", "Fantastic Plus"]
STANDING_RANK = {s: i for i, s in enumerate(STANDINGS)}

# Known weightings (from Appendix A — may vary week to week; parsed if possible)
DEFAULT_WEIGHTS: dict[str, float] = {
    "on_road_safety_score":             47.5,
    "safe_driving_metric":               0.0,
    "speeding_event_rate":              11.7,
    "seatbelt_off_rate":                11.7,
    "sign_signal_violations_rate":      11.7,
    "distractions_rate":                 7.5,
    "following_distance_rate":           5.0,
    "delivery_completion_dpmo":         11.3,
    "delivery_success_behaviors":       11.3,
    "photo_on_delivery":                 2.8,
    "customer_delivery_feedback_dpmo":   5.7,
    "customer_escalation_defect_dpmo":  11.3,
    "pickup_success_behaviors":          5.0,
    "tenured_workforce":                 0.0,
    "fleet_execution":                   5.0,
}

# Dispute notes keyed by metric slug — used in summary generation
DISPUTE_NOTES: dict[str, str] = {
    "delivery_success_behaviors": (
        "Submit via LSC → Data Disputes → DSB. Focus on 'Delivered >50 meters' where GPS accuracy "
        "may be off, and 'Inaccurate Scan Usage' events tied to station scan issues."
    ),
    "customer_delivery_feedback_dpmo": (
        "NRD removed in W26. Dispute remaining NDPL false positives via LSC → "
        "Data Disputes → CDF."
    ),
    "delivery_completion_dpmo": (
        "Verify all RTS exemptions were captured via the RTS Dashboard. "
        "DC DPMO may already be adjusted — check the exemption note on page 1."
    ),
    "customer_escalation_defect_dpmo": (
        "Review each defect event — Violations are triple-weighted. Dispute any "
        "that lacked prior coaching or resulted from station-side issues."
    ),
    "photo_on_delivery": (
        "Drivers must ensure POD photos are usable. Dispute blurry/incomplete photos "
        "that occurred due to device issues via LSC."
    ),
}

# Metrics that are disputable when below Fantastic
DISPUTABLE_METRICS = set(DISPUTE_NOTES.keys())


def _extract_text(content: bytes) -> str:
    """Extract all text from the PDF using pdfplumber."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        parts = []
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _extract_week(text: str, filename: str) -> str:
    """Extract week label like '2026-W26' from PDF text or filename."""
    m = re.search(r"Week\s+(\d+)\s*\n\s*(\d{4})", text)
    if m:
        return f"{m.group(2)}-W{int(m.group(1)):02d}"
    # fallback to filename
    fm = re.search(r"Week(\d+)_(\d{4})", filename, re.IGNORECASE)
    if fm:
        return f"{fm.group(2)}-W{int(fm.group(1)):02d}"
    return "unknown"


def _parse_standing(s: str) -> Optional[str]:
    s = s.strip()
    for standing in STANDINGS:
        if s.lower() == standing.lower():
            return standing
    return None


def _parse_value(s: str) -> Optional[float]:
    m = re.search(r"([\d.]+)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_dsp_scorecard(content: bytes, filename: str) -> tuple[dict, list[dict]]:
    """
    Parse a DSP Scorecard PDF.

    Returns:
        summary dict: week, overall_score, overall_standing, focus_areas, category_standings,
                      dc_dpmo_adjustment_note
        metrics list: [{slug, label, category, value_numeric, value_text, standing,
                        weight_pct, is_disputable, dispute_note}]
    """
    text = _extract_text(content)
    week = _extract_week(text, filename)

    # Overall
    overall_m = re.search(r"Overall Standing:\s*([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)", text)
    overall_score = float(overall_m.group(1)) if overall_m else None
    overall_standing = overall_m.group(2).strip() if overall_m else None

    # DC DPMO adjustment note (page 1)
    dc_adj = re.search(r"has been adjusted from ([\d.,]+) to ([\d.,]+)", text)
    dc_adjustment_note = None
    if dc_adj:
        dc_adjustment_note = f"DC DPMO adjusted from {dc_adj.group(1)} to {dc_adj.group(2)} via RTS exemptions."

    # Category standings
    cats = {}
    for cat, slug in [
        ("Safety and Compliance", "safety"),
        ("Delivery Quality", "delivery_quality"),
        ("Pickup Quality", "pickup_quality"),
        ("Team and Fleet", "team_fleet"),
    ]:
        m = re.search(rf"{re.escape(cat)}:\s*(\w+(?:\s+\w+)?)", text)
        if m:
            cats[slug] = m.group(1).strip()

    # Focus areas
    fa_section = re.search(r"Recommended Focus Areas(.*?)(?:Current Week Tips|Page \d)", text, re.DOTALL)
    focus_areas: list[str] = []
    if fa_section:
        for m in re.finditer(r"\d+\.\s+(.+)", fa_section.group(1)):
            focus_areas.append(m.group(1).strip())

    # Individual metrics
    METRIC_PATTERNS: list[tuple[str, str, str, str]] = [
        # (slug, label, category, regex)
        ("seatbelt_off_rate", "Seatbelt-Off Rate", "safety",
         r"Seatbelt-Off Rate\s+([\d.]+)\s+events per 100 trips\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("speeding_event_rate", "Speeding Event Rate", "safety",
         r"Speeding Event Rate\s+([\d.]+)\s+events per 100 trips\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("sign_signal_violations_rate", "Sign/Signal Violations Rate", "safety",
         r"Sign/Signal Violations Rate\s+([\d.]+)\s+events per 100 trips\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("distractions_rate", "Distractions Rate", "safety",
         r"Distractions Rate\s+([\d.]+)\s+events per 100 trips\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("following_distance_rate", "Following Distance Rate", "safety",
         r"Following Distance Rate\s+([\d.]+)\s+events per 100 trips\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("customer_escalation_defect_dpmo", "Customer Escalation Defect DPMO", "delivery_quality",
         r"Customer Escalation Defect DPMO\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("customer_delivery_feedback_dpmo", "Customer Delivery Feedback DPMO", "delivery_quality",
         r"Customer Delivery Feedback DPMO\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("delivery_completion_dpmo", "Delivery Completion DPMO", "delivery_quality",
         r"Delivery Completion DPMO\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("delivery_success_behaviors", "Delivery Success Behaviors", "delivery_quality",
         r"Delivery Success Behaviors\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("photo_on_delivery", "Photo-On-Delivery Acceptance Rate", "delivery_quality",
         r"Photo-On-Delivery Acceptance Rate\s+([\d.]+)%\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("pickup_success_behaviors", "Pickup Success Behaviors", "pickup_quality",
         r"Pickup Success Behaviors\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("tenured_workforce", "Tenured Workforce", "team_fleet",
         r"Tenured Workforce\s+([\d.]+)%\s*\|\s*(\w+(?:\s+\w+)?)"),
        ("fleet_execution", "Fleet Execution", "team_fleet",
         r"Fleet Execution\s+([\d.]+)\s*\|\s*(\w+(?:\s+\w+)?)"),
    ]

    metrics: list[dict] = []
    for slug, label, category, pattern in METRIC_PATTERNS:
        m = re.search(pattern, text)
        if not m:
            continue
        value = float(m.group(1))
        standing = m.group(2).strip()
        weight = DEFAULT_WEIGHTS.get(slug, 0.0)
        disputable = slug in DISPUTABLE_METRICS and STANDING_RANK.get(standing, 0) < STANDING_RANK["Fantastic"]
        metrics.append({
            "slug":          slug,
            "label":         label,
            "category":      category,
            "value_numeric": value,
            "value_text":    m.group(0).split("|")[0].strip().split()[-1],
            "standing":      standing,
            "weight_pct":    weight,
            "is_disputable": disputable,
            "dispute_note":  DISPUTE_NOTES.get(slug) if disputable else None,
        })

    # Compliance metrics (text-only)
    for slug, label in [("breach_of_contract", "Breach of Contract"),
                        ("comprehensive_audit", "Comprehensive Audit (CAS)")]:
        m = re.search(rf"{re.escape(label)}\s+(Compliant|Non-Compliant)", text)
        if m:
            metrics.append({
                "slug":          slug,
                "label":         label,
                "category":      "safety",
                "value_numeric": None,
                "value_text":    m.group(1),
                "standing":      "Fantastic" if m.group(1) == "Compliant" else "Poor",
                "weight_pct":    0.0,
                "is_disputable": False,
                "dispute_note":  None,
            })

    summary = {
        "week":               week,
        "overall_score":      overall_score,
        "overall_standing":   overall_standing,
        "focus_areas":        focus_areas,
        "category_standings": cats,
        "dc_adjustment_note": dc_adjustment_note,
    }
    return summary, metrics
