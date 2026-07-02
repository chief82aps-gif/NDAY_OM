"""Audit helpers for comparing WST weekly exports to weekly invoice exports.

This module compares:
- WST Weekly Report (NDAY Weekly Report)
- Weekly Invoice Export (variable invoice with line items)

Metrics compared:
- Completed routes (WST weekly report vs invoice line items)
- Service types (Flex, Fresh, Standard, etc.)
- Cancellations and late cancels
- Distance allowances
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, and_

from api.src.database import (
    WstWeeklyReport,
    VariableInvoice,
    VariableInvoiceLineItem,
    SessionLocal,
)
from api.src.service_type_matrix import map_service_type, infer_day_bucket


@dataclass
class WeeklyAuditMetrics:
    """Aggregated metrics from WST weekly report for audit comparison."""
    report_date: date
    station: str
    dsp_short_code: str
    service_type: str
    total_completed_routes: int
    total_distance_planned: float
    total_distance_allowance: float
    planned_distance_unit: str
    amzl_late_cancel: float
    dsp_late_cancel: float
    quick_coverage_accepted: float
    planned_duration: str


@dataclass
class InvoiceLineComparison:
    """Single line item from invoice with expected WST values."""
    description: str
    invoice_quantity: float
    invoice_rate: float
    invoice_amount: float
    category: str  # 'route', 'service_type', 'cancellation', 'distance', 'other'
    matched_metric: Optional[str] = None
    wst_expected_value: Optional[float] = None
    variance: Optional[float] = None
    status: str = "pending"  # 'matched', 'variance', 'unmatched'
    issues: List[str] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


@dataclass
class WeeklyAuditResult:
    """Complete audit result combining WST data and invoice comparison."""
    invoice_number: str
    invoice_date: Optional[date]
    period_start: date
    period_end: date
    station: str
    wst_metrics: WeeklyAuditMetrics
    invoice_lines: List[InvoiceLineComparison]
    total_matches: int
    total_variances: int
    total_unmatched: int
    daily_summary_matrix: List[Dict[str, Any]]
    critical_issues: List[str]
    warnings: List[str]
    aligned: bool


def parse_week_from_date(value: date) -> int:
    """Extract ISO week number from date."""
    return value.isocalendar()[1]


def normalize_service_type(service_type: str) -> str:
    """Normalize service type names for comparison."""
    return (service_type or "").strip().lower()


def build_daily_summary_matrix(invoice_lines, wst_metrics: List[WeeklyAuditMetrics]) -> List[Dict[str, Any]]:
    """Build day/service matrix with WST qty, invoice qty, and variance."""
    wst_totals: Dict[Tuple[str, str], float] = {}
    invoice_totals: Dict[Tuple[str, str], float] = {}

    for metric in wst_metrics:
        qty = float(metric.total_completed_routes or 0)
        service_bucket = map_service_type(metric.service_type)
        date_label = metric.report_date.isoformat()
        weekday_label = "WEEKDAY" if metric.report_date.weekday() < 5 else "WEEKEND"

        for day_label in [date_label, weekday_label, "WEEK_TOTAL"]:
            key = (day_label, service_bucket)
            wst_totals[key] = wst_totals.get(key, 0.0) + qty

    for line in invoice_lines:
        qty = float(getattr(line, "quantity", 0) or 0)
        description = getattr(line, "description", "")
        service_bucket = getattr(line, "service_type", None) or map_service_type(description)
        day_label = getattr(line, "day_bucket", None) or infer_day_bucket(getattr(line, "service_date", None), description)

        # Mirror into WEEKDAY/WEEKEND and WEEK_TOTAL when we have concrete date
        date_obj = None
        try:
            date_obj = datetime.strptime(day_label, "%Y-%m-%d").date()
        except Exception:
            date_obj = None

        key = (day_label, service_bucket)
        invoice_totals[key] = invoice_totals.get(key, 0.0) + qty

        if date_obj:
            weekday_label = "WEEKDAY" if date_obj.weekday() < 5 else "WEEKEND"
            invoice_totals[(weekday_label, service_bucket)] = invoice_totals.get((weekday_label, service_bucket), 0.0) + qty
            invoice_totals[("WEEK_TOTAL", service_bucket)] = invoice_totals.get(("WEEK_TOTAL", service_bucket), 0.0) + qty

    all_keys = sorted(set(wst_totals.keys()) | set(invoice_totals.keys()), key=lambda k: (k[0], k[1]))

    matrix = []
    for day_label, service_bucket in all_keys:
        wst_qty = float(wst_totals.get((day_label, service_bucket), 0.0))
        invoice_qty = float(invoice_totals.get((day_label, service_bucket), 0.0))
        if wst_qty == 0.0 and invoice_qty == 0.0:
            continue
        matrix.append({
            "day": day_label,
            "service_type": service_bucket,
            "wst_qty": wst_qty,
            "invoice_qty": invoice_qty,
            "variance": invoice_qty - wst_qty,
        })

    return matrix


def classify_invoice_line_weekly(description: str) -> Tuple[str, Optional[str]]:
    """
    Classify invoice line item for weekly audit.
    
    Returns: (category, subtype)
    Categories: 'route', 'service_type', 'cancellation', 'distance', 'other'
    """
    desc = (description or "").lower()
    
    if "cancel" in desc or "late" in desc:
        if "dsp" in desc:
            return "cancellation", "dsp_late_cancel"
        if "amzl" in desc or "amazon" in desc:
            return "cancellation", "amzl_late_cancel"
        return "cancellation", None
    
    if "route" in desc or "completed" in desc:
        return "route", None
    
    if "flex" in desc or "fresh" in desc or "standard" in desc:
        # Extract service type
        for st in ["flex", "fresh", "standard"]:
            if st in desc:
                return "service_type", st
    
    if "distance" in desc or "mile" in desc or "km" in desc:
        if "allowance" in desc:
            return "distance", "allowance"
        return "distance", "planned"
    
    return "other", None


def collect_weekly_metrics(
    db_session,
    start_date: date,
    end_date: date,
    station: str,
    dsp_short_code: Optional[str] = None,
) -> List[WeeklyAuditMetrics]:
    """
    Collect aggregated weekly metrics from WST weekly report.
    
    Returns list of metrics (one per (date, station, dsp, service_type) combination).
    """
    query = db_session.query(WstWeeklyReport).filter(
        WstWeeklyReport.report_date >= start_date,
        WstWeeklyReport.report_date <= end_date,
        WstWeeklyReport.station == station,
    )
    
    if dsp_short_code:
        query = query.filter(WstWeeklyReport.dsp_short_code == dsp_short_code)
    
    rows = query.all()
    
    metrics = []
    for row in rows:
        metrics.append(WeeklyAuditMetrics(
            report_date=row.report_date,
            station=row.station,
            dsp_short_code=row.dsp_short_code or "",
            service_type=row.service_type or "",
            total_completed_routes=row.completed_routes or 0,
            total_distance_planned=float(row.total_distance_planned or 0),
            total_distance_allowance=float(row.total_distance_allowance or 0),
            planned_distance_unit=row.planned_distance_unit or "",
            amzl_late_cancel=float(row.amzl_late_cancel or 0),
            dsp_late_cancel=float(row.dsp_late_cancel or 0),
            quick_coverage_accepted=float(row.quick_coverage_accepted or 0),
            planned_duration=row.planned_duration or "",
        ))
    
    return metrics


def match_invoice_to_wst(
    invoice_line: VariableInvoiceLineItem,
    wst_metrics: List[WeeklyAuditMetrics],
) -> InvoiceLineComparison:
    """
    Match single invoice line to WST metrics.
    
    Returns comparison with matched metric, expected value, and variance.
    """
    category, subtype = classify_invoice_line_weekly(invoice_line.description)
    
    comparison = InvoiceLineComparison(
        description=invoice_line.description,
        invoice_quantity=float(invoice_line.quantity or 0),
        invoice_rate=float(invoice_line.rate or 0),
        invoice_amount=float(invoice_line.amount or 0),
        category=category,
    )
    
    if category == "route":
        # Match to total completed routes
        total_routes = sum(m.total_completed_routes for m in wst_metrics)
        comparison.matched_metric = "total_completed_routes"
        comparison.wst_expected_value = float(total_routes)
        comparison.variance = comparison.invoice_quantity - float(total_routes)
        comparison.status = "matched" if comparison.variance == 0 else "variance"
    
    elif category == "cancellation":
        if subtype == "dsp_late_cancel":
            total_cancels = sum(m.dsp_late_cancel for m in wst_metrics)
        elif subtype == "amzl_late_cancel":
            total_cancels = sum(m.amzl_late_cancel for m in wst_metrics)
        else:
            total_cancels = sum(m.dsp_late_cancel + m.amzl_late_cancel for m in wst_metrics)
        
        comparison.matched_metric = f"cancellation_{subtype}" if subtype else "cancellation_total"
        comparison.wst_expected_value = total_cancels
        comparison.variance = comparison.invoice_quantity - total_cancels
        comparison.status = "matched" if comparison.variance == 0 else "variance"
    
    elif category == "service_type":
        # Match to service type metrics
        service_filter = subtype or comparison.description.lower()
        matching_metrics = [m for m in wst_metrics if normalize_service_type(m.service_type) == service_filter]
        
        if matching_metrics:
            total_routes_by_type = sum(m.total_completed_routes for m in matching_metrics)
            comparison.matched_metric = f"routes_by_service_{subtype or 'unspecified'}"
            comparison.wst_expected_value = float(total_routes_by_type)
            comparison.variance = comparison.invoice_quantity - float(total_routes_by_type)
            comparison.status = "matched" if comparison.variance == 0 else "variance"
        else:
            comparison.status = "unmatched"
            comparison.issues.append(f"No WST metrics found for service type: {service_filter}")
    
    elif category == "distance":
        if subtype == "allowance":
            total_distance = sum(m.total_distance_allowance for m in wst_metrics)
        else:
            total_distance = sum(m.total_distance_planned for m in wst_metrics)
        
        comparison.matched_metric = f"distance_{subtype or 'planned'}"
        comparison.wst_expected_value = total_distance
        comparison.variance = comparison.invoice_quantity - total_distance
        comparison.status = "matched" if comparison.variance == 0 else "variance"
    
    else:
        comparison.status = "unmatched"
        comparison.issues.append(f"Unable to classify invoice line: {invoice_line.description}")
    
    return comparison


def build_weekly_audit(
    db_session,
    invoice: VariableInvoice,
    station: str,
    dsp_short_code: Optional[str] = None,
) -> WeeklyAuditResult:
    """
    Build complete weekly audit comparing invoice to WST weekly report.
    
    Args:
        db_session: SQLAlchemy session
        invoice: VariableInvoice record
        station: Station code (e.g., 'MIA1')
        dsp_short_code: Optional DSP code filter
    
    Returns:
        WeeklyAuditResult with all comparisons and issues
    """
    # Determine period
    start_date = invoice.period_start or invoice.invoice_date
    end_date = invoice.period_end or invoice.invoice_date
    
    if not start_date or not end_date:
        raise ValueError("Invoice must have period_start and period_end or invoice_date")
    
    # Collect WST metrics for the period
    wst_metrics = collect_weekly_metrics(
        db_session,
        start_date,
        end_date,
        station,
        dsp_short_code,
    )
    
    if not wst_metrics:
        raise ValueError(f"No WST weekly report data found for {station} {start_date} to {end_date}")
    
    # Aggregate metrics (primary metric for the week)
    primary_metric = wst_metrics[0]
    
    # Match each invoice line to WST
    comparisons: List[InvoiceLineComparison] = []
    for line_item in invoice.line_items:
        comparison = match_invoice_to_wst(line_item, wst_metrics)
        comparisons.append(comparison)

    daily_summary_matrix = build_daily_summary_matrix(invoice.line_items, wst_metrics)
    
    # Count results
    total_matches = sum(1 for c in comparisons if c.status == "matched")
    total_variances = sum(1 for c in comparisons if c.status == "variance")
    total_unmatched = sum(1 for c in comparisons if c.status == "unmatched")
    
    # Identify critical issues
    critical_issues: List[str] = []
    warnings: List[str] = []
    
    # Check for significant variances
    for comparison in comparisons:
        if comparison.status == "variance" and comparison.variance is not None:
            if abs(comparison.variance) > 0:
                critical_issues.append(
                    f"{comparison.description}: Invoice {comparison.invoice_quantity} vs WST {comparison.wst_expected_value} (delta: {comparison.variance})"
                )
    
    # Check for unmatched lines
    if total_unmatched > 0:
        warnings.append(f"{total_unmatched} invoice line(s) could not be matched to WST metrics")
    
    # Overall alignment
    aligned = total_variances == 0 and total_unmatched == 0
    
    return WeeklyAuditResult(
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        period_start=start_date,
        period_end=end_date,
        station=station,
        wst_metrics=primary_metric,
        invoice_lines=comparisons,
        total_matches=total_matches,
        total_variances=total_variances,
        total_unmatched=total_unmatched,
        daily_summary_matrix=daily_summary_matrix,
        critical_issues=critical_issues,
        warnings=warnings,
        aligned=aligned,
    )


def format_audit_report(audit_result: WeeklyAuditResult) -> Dict:
    """Format audit result for API response."""
    return {
        "invoice_number": audit_result.invoice_number,
        "invoice_date": audit_result.invoice_date.isoformat() if audit_result.invoice_date else None,
        "period_start": audit_result.period_start.isoformat(),
        "period_end": audit_result.period_end.isoformat(),
        "station": audit_result.station,
        "wst_snapshot": {
            "report_date": audit_result.wst_metrics.report_date.isoformat(),
            "dsp_short_code": audit_result.wst_metrics.dsp_short_code,
            "service_type": audit_result.wst_metrics.service_type,
            "total_completed_routes": audit_result.wst_metrics.total_completed_routes,
            "total_distance_planned": audit_result.wst_metrics.total_distance_planned,
            "total_distance_allowance": audit_result.wst_metrics.total_distance_allowance,
            "planned_distance_unit": audit_result.wst_metrics.planned_distance_unit,
            "amzl_late_cancel": audit_result.wst_metrics.amzl_late_cancel,
            "dsp_late_cancel": audit_result.wst_metrics.dsp_late_cancel,
            "quick_coverage_accepted": audit_result.wst_metrics.quick_coverage_accepted,
            "planned_duration": audit_result.wst_metrics.planned_duration,
        },
        "invoice_lines": [
            {
                "description": c.description,
                "invoice_quantity": c.invoice_quantity,
                "invoice_rate": c.invoice_rate,
                "invoice_amount": c.invoice_amount,
                "category": c.category,
                "matched_metric": c.matched_metric,
                "wst_expected_value": c.wst_expected_value,
                "variance": c.variance,
                "status": c.status,
                "issues": c.issues,
            }
            for c in audit_result.invoice_lines
        ],
        "summary": {
            "total_lines": len(audit_result.invoice_lines),
            "matched": audit_result.total_matches,
            "variances": audit_result.total_variances,
            "unmatched": audit_result.total_unmatched,
            "aligned": audit_result.aligned,
        },
        "daily_summary_matrix": audit_result.daily_summary_matrix,
        "critical_issues": audit_result.critical_issues,
        "warnings": audit_result.warnings,
    }
