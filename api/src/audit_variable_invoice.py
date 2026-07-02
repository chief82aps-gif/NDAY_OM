"""Audit helpers for comparing WST data to weekly variable invoices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import func

from api.src.database import (
    Cortex,
    DOP,
    InvoiceAuditMapping,
    VariableInvoice,
    VariableInvoiceLineItem,
    WstDeliveredPackages,
    WstServiceDetails,
    WstTrainingWeekly,
    WstUnplannedDelay,
    WstWeeklyReport,
)

METRIC_KEYS = {
    "routes_total": "Total routes (WST weekly report or service details)",
    "training_eligible_total": "Training payments (WST training eligible)",
    "packages_total": "Packages total (delivered + pickup)",
    "packages_delivered_total": "Delivered packages total",
    "packages_pickup_total": "Pickup packages total",
}


@dataclass
class WstAuditMetrics:
    total_routes: Optional[int]
    delivered_packages_total: int
    pickup_packages_total: int
    training_eligible_total: int
    distinct_routes_total: int


@dataclass
class WeekSourceCoverage:
    cortex: Dict[int, int]
    dop: Dict[int, int]
    wst: Dict[int, int]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _sum_decimal(value: Optional[Decimal]) -> float:
    return float(value) if value is not None else 0.0


def _week_number_from_date(value: Optional[date]) -> Optional[int]:
    if value is None:
        return None
    return value.isocalendar()[1]


def _extract_week_number(text: Optional[str]) -> Optional[int]:
    if not text:
        return None

    patterns = [
        r"week\s*#?\s*(\d{1,2})",
        r"wk\s*#?\s*(\d{1,2})",
        r"\bW(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = int(match.group(1))
        if 1 <= value <= 53:
            return value
    return None


def _weeks_in_range(start_date: date, end_date: date) -> Set[int]:
    if start_date > end_date:
        return set()
    weeks: Set[int] = set()
    current = start_date
    while current <= end_date:
        weeks.add(current.isocalendar()[1])
        current += timedelta(days=1)
    return weeks


def normalize_description(description: str) -> str:
    return " ".join((description or "").strip().lower().split())


def _query_week_counts(db_session, model, date_column, start_date: date, end_date: date, station: Optional[str]) -> Dict[int, int]:
    week_label = func.extract("week", date_column).label("week_num")
    query = db_session.query(week_label, func.count(model.id))
    query = query.filter(date_column >= start_date, date_column <= end_date)
    if station:
        query = query.filter(model.station == station)

    rows = query.group_by(week_label).all()
    return {int(week): int(count or 0) for week, count in rows if week is not None}


def collect_week_source_coverage(
    db_session,
    start_date: date,
    end_date: date,
    station: Optional[str] = None,
) -> WeekSourceCoverage:
    cortex_weeks = _query_week_counts(
        db_session,
        Cortex,
        Cortex.assignment_date,
        start_date,
        end_date,
        station,
    )
    dop_weeks = _query_week_counts(
        db_session,
        DOP,
        DOP.schedule_date,
        start_date,
        end_date,
        station,
    )

    wst_tables = [
        (WstWeeklyReport, WstWeeklyReport.report_date),
        (WstServiceDetails, WstServiceDetails.report_date),
        (WstDeliveredPackages, WstDeliveredPackages.report_date),
        (WstTrainingWeekly, WstTrainingWeekly.assignment_date),
        (WstUnplannedDelay, WstUnplannedDelay.report_date),
    ]
    wst_weeks: Dict[int, int] = {}
    for model, date_column in wst_tables:
        table_weeks = _query_week_counts(
            db_session,
            model,
            date_column,
            start_date,
            end_date,
            station,
        )
        for week_num, count in table_weeks.items():
            wst_weeks[week_num] = wst_weeks.get(week_num, 0) + count

    return WeekSourceCoverage(
        cortex=cortex_weeks,
        dop=dop_weeks,
        wst=wst_weeks,
    )


def collect_wst_metrics(
    db_session,
    start_date: date,
    end_date: date,
    station: Optional[str] = None,
) -> WstAuditMetrics:
    wst_weekly_query = db_session.query(func.sum(WstWeeklyReport.completed_routes))
    wst_weekly_query = wst_weekly_query.filter(
        WstWeeklyReport.report_date >= start_date,
        WstWeeklyReport.report_date <= end_date,
    )
    if station:
        wst_weekly_query = wst_weekly_query.filter(WstWeeklyReport.station == station)

    total_routes = wst_weekly_query.scalar()
    total_routes = int(total_routes) if total_routes is not None else None

    service_query = db_session.query(func.count(func.distinct(WstServiceDetails.route_code)))
    service_query = service_query.filter(
        WstServiceDetails.report_date >= start_date,
        WstServiceDetails.report_date <= end_date,
    )
    if station:
        service_query = service_query.filter(WstServiceDetails.station == station)
    distinct_routes_total = int(service_query.scalar() or 0)

    delivered_query = db_session.query(
        WstDeliveredPackages.package_type,
        func.sum(WstDeliveredPackages.package_count),
    )
    delivered_query = delivered_query.filter(
        WstDeliveredPackages.report_date >= start_date,
        WstDeliveredPackages.report_date <= end_date,
    )
    if station:
        delivered_query = delivered_query.filter(WstDeliveredPackages.station == station)

    delivered_packages_total = 0
    pickup_packages_total = 0
    for package_type, count in delivered_query.group_by(WstDeliveredPackages.package_type).all():
        package_count = int(count or 0)
        if package_type and "pickup" in package_type.lower():
            pickup_packages_total += package_count
        else:
            delivered_packages_total += package_count

    training_query = db_session.query(func.count(WstTrainingWeekly.id))
    training_query = training_query.filter(
        WstTrainingWeekly.assignment_date >= start_date,
        WstTrainingWeekly.assignment_date <= end_date,
        WstTrainingWeekly.dsp_payment_eligible.is_(True),
    )
    if station:
        training_query = training_query.filter(WstTrainingWeekly.station == station)
    training_eligible_total = int(training_query.scalar() or 0)

    return WstAuditMetrics(
        total_routes=total_routes,
        delivered_packages_total=delivered_packages_total,
        pickup_packages_total=pickup_packages_total,
        training_eligible_total=training_eligible_total,
        distinct_routes_total=distinct_routes_total,
    )


def classify_invoice_line(description: str) -> Tuple[str, Optional[str]]:
    desc = (description or "").lower()
    if "training" in desc:
        return "training", None
    if "package" in desc or "delivery" in desc or "delivered" in desc:
        if "pickup" in desc:
            return "package", "pickup"
        if "delivered" in desc or "delivery" in desc:
            return "package", "delivered"
        return "package", None
    if "route" in desc:
        return "route", None
    return "other", None


def metric_key_from_category(category: str, subtype: Optional[str]) -> Optional[str]:
    if category == "route":
        return "routes_total"
    if category == "training":
        return "training_eligible_total"
    if category == "package":
        if subtype == "pickup":
            return "packages_pickup_total"
        if subtype == "delivered":
            return "packages_delivered_total"
        return "packages_total"
    return None


def expected_quantity_for_metric(metrics: WstAuditMetrics, metric_key: Optional[str]) -> Optional[int]:
    if metric_key == "routes_total":
        return metrics.total_routes if metrics.total_routes is not None else metrics.distinct_routes_total
    if metric_key == "training_eligible_total":
        return metrics.training_eligible_total
    if metric_key == "packages_delivered_total":
        return metrics.delivered_packages_total
    if metric_key == "packages_pickup_total":
        return metrics.pickup_packages_total
    if metric_key == "packages_total":
        return metrics.delivered_packages_total + metrics.pickup_packages_total
    return None


def load_mappings(db_session, descriptions: List[str]) -> Dict[str, str]:
    if not descriptions:
        return {}

    normalized = [normalize_description(desc) for desc in descriptions]
    rows = (
        db_session.query(InvoiceAuditMapping)
        .filter(InvoiceAuditMapping.description_normalized.in_(normalized))
        .all()
    )
    return {row.description_normalized: row.metric_key for row in rows}


def save_invoice_mappings(db_session, mappings: List[Tuple[str, str]]) -> int:
    saved = 0
    for description, metric_key in mappings:
        normalized = normalize_description(description)
        existing = (
            db_session.query(InvoiceAuditMapping)
            .filter(InvoiceAuditMapping.description_normalized == normalized)
            .first()
        )
        if existing:
            existing.description = description
            existing.metric_key = metric_key
        else:
            db_session.add(InvoiceAuditMapping(
                description=description,
                description_normalized=normalized,
                metric_key=metric_key,
            ))
        saved += 1
    db_session.commit()
    return saved


def build_variable_invoice_audit(
    db_session,
    invoice: VariableInvoice,
    start_date: date,
    end_date: date,
    station: Optional[str],
) -> Dict:
    metrics = collect_wst_metrics(db_session, start_date, end_date, station)
    source_coverage = collect_week_source_coverage(db_session, start_date, end_date, station)
    comparisons = []
    needs_prompt = []
    discrepancies = []

    invoice_period_weeks = sorted(_weeks_in_range(start_date, end_date))
    invoice_anchor_week = _week_number_from_date(start_date)
    invoice_number_week = _extract_week_number(invoice.invoice_number)

    line_descriptions = [item.description for item in invoice.line_items]
    saved_mappings = load_mappings(db_session, line_descriptions)

    for item in invoice.line_items:
        normalized = normalize_description(item.description)
        category, subtype = classify_invoice_line(item.description)
        metric_key = saved_mappings.get(normalized) or metric_key_from_category(category, subtype)
        mapping_source = "saved" if normalized in saved_mappings else "auto"

        expected_qty = expected_quantity_for_metric(metrics, metric_key)
        if metric_key is None:
            mapping_source = "unmapped"
        if metric_key in ("packages_total",) and mapping_source == "auto":
            needs_prompt.append({
                "description": item.description,
                "normalized": normalized,
                "suggested_metric_key": metric_key,
                "options": list(METRIC_KEYS.keys()),
            })
        if mapping_source == "unmapped":
            needs_prompt.append({
                "description": item.description,
                "normalized": normalized,
                "suggested_metric_key": None,
                "options": list(METRIC_KEYS.keys()),
            })

        line_week = _extract_week_number(item.description)
        resolved_week = line_week or invoice_number_week or invoice_anchor_week
        cortex_count = source_coverage.cortex.get(resolved_week, 0) if resolved_week else 0
        dop_count = source_coverage.dop.get(resolved_week, 0) if resolved_week else 0
        wst_count = source_coverage.wst.get(resolved_week, 0) if resolved_week else 0

        week_errors: List[str] = []
        if resolved_week is None:
            week_errors.append("Unable to resolve Week # from line description, invoice number, or invoice period")
        else:
            if line_week is not None and line_week not in invoice_period_weeks:
                week_errors.append(
                    f"Line week {line_week} does not fall within invoice period weeks {invoice_period_weeks}"
                )
            if cortex_count == 0:
                week_errors.append(f"Missing Cortex data for Week {resolved_week}")
            if dop_count == 0:
                week_errors.append(f"Missing DOP data for Week {resolved_week}")
            if wst_count == 0:
                week_errors.append(f"Missing WST data for Week {resolved_week}")

        quantity_delta = (
            float(item.quantity) - float(expected_qty)
            if expected_qty is not None and item.quantity is not None
            else None
        )
        if quantity_delta is not None and quantity_delta != 0:
            week_errors.append(
                f"Line quantity {float(item.quantity)} != expected {float(expected_qty)} for metric {metric_key}"
            )

        if week_errors:
            discrepancies.append({
                "invoice_number": invoice.invoice_number,
                "line_description": item.description,
                "week_number": resolved_week,
                "issues": week_errors,
                "station": station or invoice.station,
            })

        comparisons.append({
            "description": item.description,
            "normalized": normalized,
            "category": category,
            "subtype": subtype,
            "metric_key": metric_key,
            "mapping_source": mapping_source,
            "invoice_rate": _sum_decimal(item.rate),
            "invoice_quantity": _sum_decimal(item.quantity),
            "invoice_amount": _sum_decimal(item.amount),
            "expected_quantity": expected_qty,
            "quantity_delta": quantity_delta,
            "week_number": resolved_week,
            "line_week": line_week,
            "invoice_number_week": invoice_number_week,
            "invoice_period_weeks": invoice_period_weeks,
            "source_week_counts": {
                "cortex": cortex_count,
                "dop": dop_count,
                "wst": wst_count,
            },
            "week_alignment_passed": len(week_errors) == 0,
            "week_errors": week_errors,
        })

    dispute_report = {
        "ready_for_dispute": len(discrepancies) > 0,
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "broadcast_recommendation": (
            "Broadcast to dispute queue"
            if discrepancies
            else "No dispute broadcast required"
        ),
    }

    return {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "station": station or invoice.station,
        "metrics": {
            "total_routes": expected_quantity_for_metric(metrics, "routes_total"),
            "routes_from_weekly_report": metrics.total_routes,
            "routes_from_service_details": metrics.distinct_routes_total,
            "training_eligible_total": metrics.training_eligible_total,
            "delivered_packages_total": metrics.delivered_packages_total,
            "pickup_packages_total": metrics.pickup_packages_total,
            "package_total": expected_quantity_for_metric(metrics, "packages_total"),
        },
        "week_alignment": {
            "invoice_anchor_week": invoice_anchor_week,
            "invoice_number_week": invoice_number_week,
            "invoice_period_weeks": invoice_period_weeks,
            "source_week_coverage": {
                "cortex": source_coverage.cortex,
                "dop": source_coverage.dop,
                "wst": source_coverage.wst,
            },
        },
        "line_item_comparisons": comparisons,
        "dispute_report": dispute_report,
        "needs_prompt": needs_prompt,
        "metric_options": METRIC_KEYS,
    }


def resolve_audit_date_range(
    invoice: VariableInvoice,
    start_date: Optional[date],
    end_date: Optional[date],
) -> Tuple[Optional[date], Optional[date]]:
    resolved_start = invoice.period_start or start_date
    resolved_end = invoice.period_end or end_date
    return resolved_start, resolved_end


def parse_query_dates(start_date: Optional[str], end_date: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
    return _parse_date(start_date), _parse_date(end_date)
