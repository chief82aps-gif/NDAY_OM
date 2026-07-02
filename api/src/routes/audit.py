from typing import List, Optional, Dict, Literal
from datetime import datetime, date, timedelta
from hashlib import sha1

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from api.src.authorization import get_current_user_role, require_financial_access
from api.src.database import (
    SessionLocal,
    ApprovedAudit,
    VariableInvoice,
    Cortex,
    DOP,
    WstServiceDetails,
    WstDeliveredPackages,
    WstTrainingWeekly,
    WstUnplannedDelay,
    WstWeeklyReport,
    AuditMismatchReview,
    AuditRouteReview,
    engine,
)
from api.src.audit_variable_invoice import (
    build_variable_invoice_audit,
    METRIC_KEYS,
    parse_query_dates,
    resolve_audit_date_range,
    save_invoice_mappings,
)

router = APIRouter()

# ===================== APPROVED AUDITS ENDPOINTS =====================

class ApprovedAuditCreate(BaseModel):
    station: str
    audit_date: date
    cortex_raw: str
    wst_raw: str
    notes: str = ""
    variance_responses: Optional[Dict] = None

class ApprovedAuditOut(BaseModel):
    id: int
    submitted_at: datetime
    station: str
    audit_date: date
    cortex_raw: str
    wst_raw: str
    submitted_by: str = ""
    notes: str = ""
    variance_responses: Optional[Dict] = None

    class Config:
        orm_mode = True

@router.post("/approved-audits", response_model=ApprovedAuditOut)
def submit_approved_audit(
    audit: ApprovedAuditCreate,
    role: str = Depends(get_current_user_role),
):
    """Store a new approved audit submission."""
    db = SessionLocal()
    try:
        entry = ApprovedAudit(
            station=audit.station,
            audit_date=audit.audit_date,
            cortex_raw=audit.cortex_raw,
            wst_raw=audit.wst_raw,
            notes=audit.notes,
            submitted_by=role,
            variance_responses=audit.variance_responses or {}
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save audit: {str(e)}"
        )
    finally:
        db.close()

@router.get("/approved-audits", response_model=List[ApprovedAuditOut])
def list_approved_audits(
    station: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    role: str = Depends(get_current_user_role),
):
    """List approved audit submissions (optionally filtered)."""
    db = SessionLocal()
    try:
        query = db.query(ApprovedAudit)
        if station:
            query = query.filter(ApprovedAudit.station == station)
        if start_date:
            query = query.filter(ApprovedAudit.audit_date >= start_date)
        if end_date:
            query = query.filter(ApprovedAudit.audit_date <= end_date)
        audits = query.order_by(ApprovedAudit.audit_date.desc()).all()
        return audits
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch audits: {str(e)}"
        )
    finally:
        db.close()


class InvoiceMappingItem(BaseModel):
    description: str
    metric_key: str


class MismatchActionUpdate(BaseModel):
    mismatch_key: str
    action_status: Literal["pending", "ignore", "dispute_entered"]
    manager_note: Optional[str] = None
    dispute_portal_reference: Optional[str] = None
    dispute_verified: bool = False


class MismatchBulkUpdate(BaseModel):
    items: List[MismatchActionUpdate] = Field(default_factory=list)


class RoutePromptActionUpdate(BaseModel):
    route_code: str
    action_status: Literal["pending", "confirm_valid_route", "exclude_missort"]
    manager_note: Optional[str] = None


class RoutePromptBulkUpdate(BaseModel):
    comparison_date: str
    station: Optional[str] = None
    invoice_number: Optional[str] = None
    items: List[RoutePromptActionUpdate] = Field(default_factory=list)


def _ensure_mismatch_review_table() -> None:
    AuditMismatchReview.__table__.create(bind=engine, checkfirst=True)


def _ensure_route_review_table() -> None:
    AuditRouteReview.__table__.create(bind=engine, checkfirst=True)


def _discrepancy_key(invoice_number: str, discrepancy: Dict) -> str:
    line_description = discrepancy.get("line_description") or ""
    week_number = discrepancy.get("week_number")
    issues = discrepancy.get("issues") or []
    seed = f"{invoice_number}|{line_description}|{week_number}|{'|'.join(issues)}"
    return sha1(seed.encode("utf-8")).hexdigest()[:24]


def _discrepancy_items_with_actions(db, report: Dict) -> List[Dict]:
    invoice_number = report.get("invoice_number")
    discrepancies = report.get("dispute_report", {}).get("discrepancies", [])
    if not invoice_number or not discrepancies:
        return []

    keys = [_discrepancy_key(invoice_number, d) for d in discrepancies]
    existing = (
        db.query(AuditMismatchReview)
        .filter(
            AuditMismatchReview.invoice_number == invoice_number,
            AuditMismatchReview.mismatch_key.in_(keys),
        )
        .all()
    )
    review_by_key = {r.mismatch_key: r for r in existing}

    items = []
    for discrepancy in discrepancies:
        mismatch_key = _discrepancy_key(invoice_number, discrepancy)
        review = review_by_key.get(mismatch_key)
        action_status = review.action_status if review else "pending"
        request_for_action = (
            "Manager review required: mark as ignore if valid, "
            "or confirm dispute entered in Amazon portal."
        )

        items.append({
            "mismatch_key": mismatch_key,
            "line_description": discrepancy.get("line_description"),
            "week_number": discrepancy.get("week_number"),
            "issues": discrepancy.get("issues", []),
            "station": discrepancy.get("station"),
            "request_for_action": request_for_action,
            "manager_action": {
                "action_status": action_status,
                "manager_note": review.manager_note if review else None,
                "dispute_verified": bool(review.dispute_verified) if review else False,
                "dispute_portal_reference": review.dispute_portal_reference if review else None,
                "reviewed_role": review.reviewed_role if review else None,
                "reviewed_at": review.reviewed_at.isoformat() if review and review.reviewed_at else None,
            },
        })

    return items


def _build_invoice_report(
    db,
    invoice: VariableInvoice,
    start_date: Optional[str],
    end_date: Optional[str],
    station: Optional[str],
) -> Dict:
    query_start, query_end = parse_query_dates(start_date, end_date)
    resolved_start, resolved_end = resolve_audit_date_range(
        invoice,
        query_start,
        query_end,
    )

    if not resolved_start or not resolved_end:
        raise HTTPException(
            status_code=400,
            detail="Invoice missing period dates. Provide start_date and end_date.",
        )

    audit_station = station or invoice.station
    return build_variable_invoice_audit(
        db,
        invoice,
        resolved_start,
        resolved_end,
        audit_station,
    )


@router.get("/variable-invoice/{invoice_number}")
def audit_variable_invoice(
    invoice_number: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    station: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """Audit a variable invoice against WST data for the invoice period."""
    require_financial_access(role)

    db = SessionLocal()
    try:
        invoice = db.query(VariableInvoice).filter(
            VariableInvoice.invoice_number == invoice_number
        ).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        report = _build_invoice_report(db, invoice, start_date, end_date, station)
        return report
    finally:
        db.close()


@router.get("/wst-cortex-dop-comparison")
def compare_wst_cortex_dop_by_date(
    comparison_date: str,
    station: Optional[str] = None,
    service_type: str = "Standard Parcel",
    invoice_number: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """Compare WST Service Details vs DOP for a selected date."""
    require_financial_access(role)

    parsed_date, _ = parse_query_dates(comparison_date, None)
    if not parsed_date:
        raise HTTPException(status_code=400, detail="comparison_date must be YYYY-MM-DD")

    db = SessionLocal()
    try:
        _ensure_route_review_table()
        cortex_query = db.query(func.count(Cortex.id)).filter(Cortex.assignment_date == parsed_date)
        dop_query = db.query(func.count(DOP.id)).filter(DOP.schedule_date == parsed_date)
        wst_service_query = db.query(func.count(WstServiceDetails.id)).filter(WstServiceDetails.report_date == parsed_date)
        wst_delivered_query = db.query(func.count(WstDeliveredPackages.id)).filter(WstDeliveredPackages.report_date == parsed_date)
        wst_training_query = db.query(func.count(WstTrainingWeekly.id)).filter(WstTrainingWeekly.assignment_date == parsed_date)
        wst_delay_query = db.query(func.count(WstUnplannedDelay.id)).filter(WstUnplannedDelay.report_date == parsed_date)
        wst_weekly_query = db.query(func.count(WstWeeklyReport.id)).filter(WstWeeklyReport.report_date == parsed_date)

        if station:
            cortex_query = cortex_query.filter(Cortex.station == station)
            dop_query = dop_query.filter(DOP.station == station)
            wst_service_query = wst_service_query.filter(WstServiceDetails.station == station)
            wst_delivered_query = wst_delivered_query.filter(WstDeliveredPackages.station == station)
            wst_training_query = wst_training_query.filter(WstTrainingWeekly.station == station)
            wst_delay_query = wst_delay_query.filter(WstUnplannedDelay.station == station)
            wst_weekly_query = wst_weekly_query.filter(WstWeeklyReport.station == station)

        cortex_count = int(cortex_query.scalar() or 0)
        dop_count = int(dop_query.scalar() or 0)
        wst_service_count = int(wst_service_query.scalar() or 0)
        wst_delivered_count = int(wst_delivered_query.scalar() or 0)
        wst_training_count = int(wst_training_query.scalar() or 0)
        wst_delay_count = int(wst_delay_query.scalar() or 0)
        wst_weekly_count = int(wst_weekly_query.scalar() or 0)
        wst_total_all_tables = (
            wst_service_count
            + wst_delivered_count
            + wst_training_count
            + wst_delay_count
            + wst_weekly_count
        )
        wst_total = wst_service_count

        issues: List[str] = []
        if dop_count == 0:
            issues.append("Missing DOP rows for selected date")
        if wst_service_count == 0:
            issues.append("Missing WST Service Details rows for selected date")

        invoice_routes: Optional[int] = None
        if invoice_number:
            invoice = db.query(VariableInvoice).filter(
                VariableInvoice.invoice_number == invoice_number
            ).first()
            if not invoice:
                issues.append(f"Invoice not found: {invoice_number}")
            else:
                try:
                    invoice_report = _build_invoice_report(db, invoice, None, None, station)
                    invoice_routes = int(invoice_report.get("metrics", {}).get("total_routes") or 0)
                except HTTPException as exc:
                    issues.append(f"Invoice comparison unavailable: {exc.detail}")

        cortex_vs_wst_delta = cortex_count - wst_service_count
        cortex_vs_invoice_delta = (cortex_count - invoice_routes) if invoice_routes is not None else None

        if cortex_count > 0 and wst_service_count > 0 and cortex_vs_wst_delta != 0:
            issues.append(f"Cortex vs WST route mismatch ({cortex_count} vs {wst_service_count})")
        if invoice_routes is not None and cortex_count > 0 and cortex_vs_invoice_delta != 0:
            issues.append(f"Cortex vs Invoice route mismatch ({cortex_count} vs {invoice_routes})")

        cortex_route_query = db.query(func.distinct(Cortex.route_code)).filter(
            Cortex.assignment_date == parsed_date,
            Cortex.route_code.isnot(None),
            Cortex.route_code != "",
        )
        dop_route_query = db.query(func.distinct(DOP.route_code)).filter(
            DOP.schedule_date == parsed_date,
            DOP.route_code.isnot(None),
            DOP.route_code != "",
        )
        wst_route_query = db.query(func.distinct(WstServiceDetails.route_code)).filter(
            WstServiceDetails.report_date == parsed_date,
            WstServiceDetails.route_code.isnot(None),
            WstServiceDetails.route_code != "",
        )

        if station:
            cortex_route_query = cortex_route_query.filter(Cortex.station == station)
            dop_route_query = dop_route_query.filter(DOP.station == station)
            wst_route_query = wst_route_query.filter(WstServiceDetails.station == station)

        cortex_route_codes = {str(row[0]).strip() for row in cortex_route_query.all() if row and row[0]}
        dop_route_codes = {str(row[0]).strip() for row in dop_route_query.all() if row and row[0]}
        wst_route_codes = {str(row[0]).strip() for row in wst_route_query.all() if row and row[0]}

        candidate_route_codes = sorted(cortex_route_codes - dop_route_codes)

        review_query = db.query(AuditRouteReview).filter(
            AuditRouteReview.audit_date == parsed_date,
        )
        if station:
            review_query = review_query.filter(AuditRouteReview.station == station)
        if invoice_number:
            review_query = review_query.filter(AuditRouteReview.invoice_number == invoice_number)
        reviews = review_query.all()
        review_by_route = {r.route_code: r for r in reviews}

        prompt_items: List[Dict] = []
        pending_count = 0
        excluded_count = 0
        confirmed_count = 0
        for route_code in candidate_route_codes:
            review = review_by_route.get(route_code)
            action_status = review.action_status if review else "pending"
            if action_status == "pending":
                pending_count += 1
            elif action_status == "exclude_missort":
                excluded_count += 1
            elif action_status == "confirm_valid_route":
                confirmed_count += 1

            prompt_items.append({
                "route_code": route_code,
                "action_status": action_status,
                "manager_note": review.manager_note if review else None,
                "reviewed_role": review.reviewed_role if review else None,
                "reviewed_at": review.reviewed_at.isoformat() if review and review.reviewed_at else None,
                "required": True,
                "prompt": "Route appears in Cortex but not in DOP. Confirm valid route or mark as missort to exclude.",
            })

        effective_cortex_routes = len(cortex_route_codes) - excluded_count
        effective_cortex_vs_invoice_delta = (
            effective_cortex_routes - invoice_routes if invoice_routes is not None else None
        )
        requires_user_confirmation = pending_count > 0
        prompt_message = (
            "One or more Cortex routes are not in DOP. Confirm each as valid route or remove as missort before finalizing audit."
            if requires_user_confirmation
            else None
        )

        if pending_count > 0:
            issues.append(f"{pending_count} Cortex routes not in DOP require user confirmation")

        wst_service_routes_query = db.query(
            WstServiceDetails.route_code,
            WstServiceDetails.service_type,
            WstServiceDetails.shipments_delivered,
            WstServiceDetails.pickup_packages,
            WstServiceDetails.delivery_associate,
        ).filter(
            WstServiceDetails.report_date == parsed_date,
            WstServiceDetails.service_type.ilike(f"%{service_type}%"),
        )
        dop_service_query = db.query(
            DOP.route_code,
            DOP.service_type,
            DOP.planned_packages,
        ).filter(
            DOP.schedule_date == parsed_date,
            DOP.service_type.ilike(f"%{service_type}%"),
        )

        if station:
            wst_service_routes_query = wst_service_routes_query.filter(WstServiceDetails.station == station)
            dop_service_query = dop_service_query.filter(DOP.station == station)

        wst_service_rows = wst_service_routes_query.all()
        dop_service_rows = dop_service_query.all()

        wst_by_route: Dict[str, Dict] = {}
        for route_code, svc, delivered, pickup, delivery_associate in wst_service_rows:
            key = (route_code or "").strip()
            if not key:
                continue
            wst_packages = int(delivered or 0) + int(pickup or 0)
            if key not in wst_by_route:
                wst_by_route[key] = {
                    "service_type": svc,
                    "packages": wst_packages,
                    "driver_name": delivery_associate,
                }
            else:
                wst_by_route[key]["packages"] += wst_packages
                if not wst_by_route[key].get("driver_name") and delivery_associate:
                    wst_by_route[key]["driver_name"] = delivery_associate

        dop_by_route: Dict[str, Dict] = {}
        for route_code, svc, planned_packages in dop_service_rows:
            key = (route_code or "").strip()
            if not key:
                continue
            if key not in dop_by_route:
                dop_by_route[key] = {
                    "service_type": svc,
                    "packages": int(planned_packages or 0),
                }
            else:
                dop_by_route[key]["packages"] += int(planned_packages or 0)

        compared_routes = sorted(set(wst_by_route.keys()) | set(dop_by_route.keys()))
        line_items: List[Dict] = []
        for route_code in compared_routes:
            wst_route = wst_by_route.get(route_code)
            dop_route = dop_by_route.get(route_code)

            wst_packages = wst_route["packages"] if wst_route else None
            dop_packages = dop_route["packages"] if dop_route else None
            package_delta = None
            if wst_packages is not None and dop_packages is not None:
                package_delta = wst_packages - dop_packages

            row_issues: List[str] = []
            if wst_route is None:
                row_issues.append("missing_in_wst")
            if dop_route is None:
                row_issues.append("missing_in_dop")
            if (
                wst_route is not None
                and dop_route is not None
                and (wst_route.get("service_type") or "").strip() != (dop_route.get("service_type") or "").strip()
            ):
                row_issues.append("service_type_mismatch")
            if package_delta not in (None, 0):
                row_issues.append("package_mismatch")

            line_items.append({
                "route_code": route_code,
                "wst": {
                    "service_type": wst_route.get("service_type") if wst_route else None,
                    "packages": wst_packages,
                    "driver_name": wst_route.get("driver_name") if wst_route else None,
                },
                "dop": {
                    "service_type": dop_route.get("service_type") if dop_route else None,
                    "packages": dop_packages,
                },
                "package_delta": package_delta,
                "status": "matched" if not row_issues else "mismatch",
                "issues": row_issues,
            })

        wst_service_route_count = len(wst_by_route)
        dop_service_route_count = len(dop_by_route)
        wst_service_package_total = sum(item["packages"] for item in wst_by_route.values())
        dop_service_package_total = sum(item["packages"] for item in dop_by_route.values())
        service_route_delta = wst_service_route_count - dop_service_route_count
        service_package_delta = wst_service_package_total - dop_service_package_total

        if wst_service_route_count == 0:
            issues.append(f"No WST rows matched service type filter: {service_type}")
        if dop_service_route_count == 0:
            issues.append(f"No DOP rows matched service type filter: {service_type}")
        if wst_service_route_count > 0 and dop_service_route_count > 0 and service_route_delta != 0:
            issues.append(
                f"{service_type} route mismatch (WST {wst_service_route_count} vs DOP {dop_service_route_count})"
            )
        if wst_service_route_count > 0 and dop_service_route_count > 0 and service_package_delta != 0:
            issues.append(
                f"{service_type} package mismatch (WST {wst_service_package_total} vs DOP {dop_service_package_total})"
            )

        return {
            "comparison_date": parsed_date.isoformat(),
            "week_number": parsed_date.isocalendar()[1],
            "station": station,
            "counts": {
                "cortex_routes": cortex_count,
                "invoice_routes": invoice_routes,
                "dop_routes": dop_count,
                "wst_service_details": wst_service_count,
                "wst_delivered_packages": wst_delivered_count,
                "wst_training_weekly": wst_training_count,
                "wst_unplanned_delay": wst_delay_count,
                "wst_weekly_report": wst_weekly_count,
                "wst_total": wst_total,
                "wst_total_all_tables": wst_total_all_tables,
            },
            "route_alignment": {
                "invoice_number": invoice_number,
                "cortex_vs_wst_delta": cortex_vs_wst_delta,
                "cortex_vs_invoice_delta": cortex_vs_invoice_delta,
                "aligned_cortex_to_wst": cortex_vs_wst_delta == 0,
                "aligned_cortex_to_invoice": cortex_vs_invoice_delta == 0 if cortex_vs_invoice_delta is not None else None,
            },
            "route_payment_audit": {
                "comparison_date": parsed_date.isoformat(),
                "station": station,
                "invoice_number": invoice_number,
                "routes_completed_cortex": len(cortex_route_codes),
                "routes_paid_invoice": invoice_routes,
                "routes_seen_wst": len(wst_route_codes),
                "routes_planned_dop": len(dop_route_codes),
                "routes_not_in_dop": len(candidate_route_codes),
                "pending_confirmation": pending_count,
                "confirmed_valid": confirmed_count,
                "excluded_missort": excluded_count,
                "effective_cortex_routes": effective_cortex_routes,
                "effective_cortex_vs_invoice_delta": effective_cortex_vs_invoice_delta,
                "requires_user_confirmation": requires_user_confirmation,
                "prompt_message": prompt_message,
                "prompt_items": prompt_items,
            },
            "service_type_comparison": {
                "service_type_filter": service_type,
                "totals": {
                    "wst_routes": wst_service_route_count,
                    "dop_routes": dop_service_route_count,
                    "route_delta": service_route_delta,
                    "wst_packages": wst_service_package_total,
                    "dop_packages": dop_service_package_total,
                    "package_delta": service_package_delta,
                },
                "line_items": line_items,
            },
            "aligned": len(issues) == 0,
            "issues": issues,
        }
    finally:
        db.close()


@router.post("/route-payment/review")
def update_route_payment_review(
    payload: RoutePromptBulkUpdate,
    role: str = Depends(get_current_user_role),
):
    """Persist confirmation/removal decisions for Cortex routes not present in DOP."""
    require_financial_access(role)

    if not payload.items:
        raise HTTPException(status_code=400, detail="No route review items provided")

    parsed_date, _ = parse_query_dates(payload.comparison_date, None)
    if not parsed_date:
        raise HTTPException(status_code=400, detail="comparison_date must be YYYY-MM-DD")

    db = SessionLocal()
    try:
        _ensure_route_review_table()
        saved = 0
        for item in payload.items:
            route_code = (item.route_code or "").strip()
            if not route_code:
                continue

            existing = db.query(AuditRouteReview).filter(
                AuditRouteReview.audit_date == parsed_date,
                AuditRouteReview.station == payload.station,
                AuditRouteReview.invoice_number == payload.invoice_number,
                AuditRouteReview.route_code == route_code,
            ).first()

            if not existing:
                existing = AuditRouteReview(
                    audit_date=parsed_date,
                    station=payload.station,
                    invoice_number=payload.invoice_number,
                    route_code=route_code,
                )
                db.add(existing)

            existing.action_status = item.action_status
            existing.manager_note = item.manager_note
            existing.reviewed_role = role
            saved += 1

        db.commit()
        return {
            "comparison_date": parsed_date.isoformat(),
            "station": payload.station,
            "invoice_number": payload.invoice_number,
            "saved_count": saved,
        }
    finally:
        db.close()


@router.get("/variable-invoice/{invoice_number}/dispute-report")
def variable_invoice_dispute_report(
    invoice_number: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    station: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """Return only dispute-ready discrepancies for a variable invoice."""
    require_financial_access(role)

    db = SessionLocal()
    try:
        invoice = db.query(VariableInvoice).filter(
            VariableInvoice.invoice_number == invoice_number
        ).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        report = _build_invoice_report(db, invoice, start_date, end_date, station)
        _ensure_mismatch_review_table()
        dispute = report.get("dispute_report", {})
        action_items = _discrepancy_items_with_actions(db, report)
        return {
            "invoice_number": report.get("invoice_number"),
            "station": report.get("station"),
            "period_start": report.get("period_start"),
            "period_end": report.get("period_end"),
            "week_alignment": report.get("week_alignment", {}),
            "dispute_report": dispute,
            "action_items": action_items,
            "broadcast_payload": {
                "type": "invoice_dispute",
                "invoice_number": report.get("invoice_number"),
                "station": report.get("station"),
                "ready_for_dispute": dispute.get("ready_for_dispute", False),
                "discrepancy_count": dispute.get("discrepancy_count", 0),
                "discrepancies": dispute.get("discrepancies", []),
            },
        }
    finally:
        db.close()


@router.get("/variable-invoice/{invoice_number}/mismatch-actions")
def get_variable_invoice_mismatch_actions(
    invoice_number: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    station: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """Return each mismatch with manager action status for review workflows."""
    require_financial_access(role)

    db = SessionLocal()
    try:
        _ensure_mismatch_review_table()
        invoice = db.query(VariableInvoice).filter(
            VariableInvoice.invoice_number == invoice_number
        ).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        report = _build_invoice_report(db, invoice, start_date, end_date, station)
        action_items = _discrepancy_items_with_actions(db, report)
        return {
            "invoice_number": report.get("invoice_number"),
            "station": report.get("station"),
            "period_start": report.get("period_start"),
            "period_end": report.get("period_end"),
            "count": len(action_items),
            "items": action_items,
        }
    finally:
        db.close()


@router.post("/variable-invoice/{invoice_number}/mismatch-actions")
def update_variable_invoice_mismatch_actions(
    invoice_number: str,
    payload: MismatchBulkUpdate,
    role: str = Depends(get_current_user_role),
):
    """Save manager decisions for mismatches (ignore vs dispute entered)."""
    require_financial_access(role)

    if not payload.items:
        raise HTTPException(status_code=400, detail="No action items provided")

    db = SessionLocal()
    try:
        _ensure_mismatch_review_table()
        invoice_exists = db.query(VariableInvoice.id).filter(
            VariableInvoice.invoice_number == invoice_number
        ).first()
        if not invoice_exists:
            raise HTTPException(status_code=404, detail="Invoice not found")

        saved = 0
        for item in payload.items:
            if item.action_status == "dispute_entered":
                if not item.dispute_verified:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Mismatch {item.mismatch_key} requires dispute_verified=true when dispute_entered",
                    )
                if not (item.dispute_portal_reference or "").strip():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Mismatch {item.mismatch_key} requires dispute_portal_reference when dispute_entered",
                    )

            existing = db.query(AuditMismatchReview).filter(
                AuditMismatchReview.invoice_number == invoice_number,
                AuditMismatchReview.mismatch_key == item.mismatch_key,
            ).first()

            if not existing:
                existing = AuditMismatchReview(
                    invoice_number=invoice_number,
                    mismatch_key=item.mismatch_key,
                )
                db.add(existing)

            existing.action_status = item.action_status
            existing.manager_note = item.manager_note
            existing.dispute_verified = item.dispute_verified
            existing.dispute_portal_reference = item.dispute_portal_reference
            existing.reviewed_role = role
            saved += 1

        db.commit()
        return {
            "invoice_number": invoice_number,
            "saved_count": saved,
        }
    finally:
        db.close()


@router.get("/variable-invoices/disputes")
def list_variable_invoice_disputes(
    only_ready: bool = True,
    limit: int = 100,
    role: str = Depends(get_current_user_role),
):
    """Return dispute-ready reports across variable invoices for broadcast workflows."""
    require_financial_access(role)

    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    db = SessionLocal()
    try:
        invoices = db.query(VariableInvoice).order_by(VariableInvoice.invoice_date.desc()).limit(limit).all()
        results = []
        for invoice in invoices:
            try:
                report = _build_invoice_report(db, invoice, None, None, invoice.station)
                dispute = report.get("dispute_report", {})
                ready = dispute.get("ready_for_dispute", False)
                if only_ready and not ready:
                    continue

                results.append({
                    "invoice_number": report.get("invoice_number"),
                    "station": report.get("station"),
                    "period_start": report.get("period_start"),
                    "period_end": report.get("period_end"),
                    "ready_for_dispute": ready,
                    "discrepancy_count": dispute.get("discrepancy_count", 0),
                    "discrepancies": dispute.get("discrepancies", []),
                })
            except HTTPException:
                continue

        return {
            "count": len(results),
            "only_ready": only_ready,
            "items": results,
        }
    finally:
        db.close()


@router.get("/variable-invoices/disputes/weekly")
def create_weekly_dispute_report(
    week_date: str,
    station: Optional[str] = None,
    only_ready: bool = True,
    role: str = Depends(get_current_user_role),
):
    """Create a weekly dispute report for invoices overlapping the selected week."""
    require_financial_access(role)

    parsed_date, _ = parse_query_dates(week_date, None)
    if not parsed_date:
        raise HTTPException(status_code=400, detail="week_date must be YYYY-MM-DD")

    week_start = parsed_date - timedelta(days=parsed_date.weekday())
    week_end = week_start + timedelta(days=6)

    db = SessionLocal()
    try:
        invoice_query = db.query(VariableInvoice).filter(
            VariableInvoice.period_start.isnot(None),
            VariableInvoice.period_end.isnot(None),
            VariableInvoice.period_start <= week_end,
            VariableInvoice.period_end >= week_start,
        )
        if station:
            invoice_query = invoice_query.filter(VariableInvoice.station == station)

        invoices = invoice_query.order_by(VariableInvoice.invoice_date.desc()).all()
        items = []

        for invoice in invoices:
            try:
                report = _build_invoice_report(
                    db,
                    invoice,
                    week_start.isoformat(),
                    week_end.isoformat(),
                    station or invoice.station,
                )
                dispute = report.get("dispute_report", {})
                ready = dispute.get("ready_for_dispute", False)
                if only_ready and not ready:
                    continue

                items.append({
                    "invoice_number": report.get("invoice_number"),
                    "station": report.get("station"),
                    "period_start": report.get("period_start"),
                    "period_end": report.get("period_end"),
                    "ready_for_dispute": ready,
                    "discrepancy_count": dispute.get("discrepancy_count", 0),
                    "discrepancies": dispute.get("discrepancies", []),
                })
            except HTTPException:
                continue

        return {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "week_number": week_start.isocalendar()[1],
            "station": station,
            "only_ready": only_ready,
            "count": len(items),
            "items": items,
            "broadcast_payload": {
                "type": "weekly_invoice_dispute_report",
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "week_number": week_start.isocalendar()[1],
                "station": station,
                "count": len(items),
                "items": items,
            },
        }
    finally:
        db.close()


@router.get("/variable-invoices")
def list_variable_invoices(
    role: str = Depends(get_current_user_role),
):
    """List all variable invoices available for audit."""
    require_financial_access(role)

    db = SessionLocal()
    try:
        invoices = db.query(VariableInvoice).order_by(
            VariableInvoice.invoice_date.desc()
        ).all()
        return {
            "invoices": [
                {
                    "id": inv.id,
                    "invoice_number": inv.invoice_number,
                    "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                    "period_start": inv.period_start.isoformat() if inv.period_start else None,
                    "period_end": inv.period_end.isoformat() if inv.period_end else None,
                    "station": inv.station,
                    "subtotal": float(inv.subtotal) if inv.subtotal else 0,
                    "tax_due": float(inv.tax_due) if inv.tax_due else 0,
                    "total_due": float(inv.total_due) if inv.total_due else 0,
                }
                for inv in invoices
            ]
        }
    finally:
        db.close()


@router.post("/variable-invoice/mappings")
def save_variable_invoice_mappings(
    mappings: List[InvoiceMappingItem],
    role: str = Depends(get_current_user_role),
):
    """Persist invoice line-item mappings for WST audit."""
    require_financial_access(role)

    invalid = [item.metric_key for item in mappings if item.metric_key not in METRIC_KEYS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric_key values: {sorted(set(invalid))}",
        )

    db = SessionLocal()
    try:
        saved = save_invoice_mappings(
            db,
            [(item.description, item.metric_key) for item in mappings],
        )
        return {
            "status": "saved",
            "count": saved,
        }
    finally:
        db.close()
