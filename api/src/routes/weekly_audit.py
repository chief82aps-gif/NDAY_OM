"""
Weekly Invoice Audit API endpoints.

Compares WST weekly exports with weekly invoice exports.
"""

from typing import List, Optional
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.src.authorization import get_current_user_role, require_financial_access
from api.src.database import (
    SessionLocal,
    VariableInvoice,
    WeeklyInvoiceAudit,
    WeeklyAuditLineItem,
)
from api.src.audit_weekly_invoice import (
    build_weekly_audit,
    format_audit_report,
)

router = APIRouter(prefix="/audit/weekly", tags=["weekly_invoice_audit"])


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class WeeklyAuditRequest(BaseModel):
    """Request to audit a weekly invoice."""
    invoice_number: str
    station: str
    dsp_short_code: Optional[str] = None


class WeeklyAuditLineItemOut(BaseModel):
    """Single line item comparison in audit."""
    invoice_description: str
    invoice_quantity: float
    invoice_rate: Optional[float]
    invoice_amount: Optional[float]
    category: str
    subcategory: Optional[str]
    matched_metric: Optional[str]
    wst_expected_value: Optional[float]
    variance: Optional[float]
    status: str
    issues: List[str]

    class Config:
        orm_mode = True


class WeeklyAuditOut(BaseModel):
    """Complete weekly audit response."""
    id: int
    invoice_number: str
    audit_date: datetime
    period_start: date
    period_end: date
    station: str
    dsp_short_code: Optional[str]
    
    # WST snapshot
    wst_completed_routes: Optional[int]
    wst_distance_planned: Optional[float]
    wst_distance_allowance: Optional[float]
    wst_amzl_late_cancel: Optional[float]
    wst_dsp_late_cancel: Optional[float]
    
    # Invoice snapshot
    invoice_total_quantity: Optional[float]
    invoice_subtotal: Optional[float]
    invoice_total_due: Optional[float]
    
    # Results
    total_lines: int
    matched_lines: int
    variance_lines: int
    unmatched_lines: int
    aligned: bool
    
    critical_issues: List[str]
    warnings: List[str]
    
    approval_status: str
    approval_notes: Optional[str]
    
    line_items: List[WeeklyAuditLineItemOut]

    class Config:
        orm_mode = True


class WeeklyAuditApprovalRequest(BaseModel):
    """Request to approve or dispute a weekly audit."""
    approval_status: str  # 'approved', 'disputed'
    approval_notes: Optional[str] = None


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/audit", response_model=WeeklyAuditOut)
def run_weekly_audit(
    request: WeeklyAuditRequest,
    role: str = Depends(get_current_user_role),
):
    """
    Run audit comparing weekly invoice to WST weekly export.
    
    Fetches the invoice and WST data for the invoice period,
    compares line items to metrics, and identifies variances.
    """
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        # Fetch invoice
        invoice = db.query(VariableInvoice).filter(
            VariableInvoice.invoice_number == request.invoice_number
        ).first()
        
        if not invoice:
            raise HTTPException(status_code=404, detail=f"Invoice not found: {request.invoice_number}")
        
        if not invoice.period_start or not invoice.period_end:
            raise HTTPException(
                status_code=400,
                detail="Invoice must have period_start and period_end for weekly audit"
            )
        
        # Run audit
        try:
            audit_result = build_weekly_audit(
                db,
                invoice,
                request.station,
                request.dsp_short_code,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        # Save audit to database
        db_audit = WeeklyInvoiceAudit(
            invoice_number=audit_result.invoice_number,
            period_start=audit_result.period_start,
            period_end=audit_result.period_end,
            station=request.station,
            dsp_short_code=request.dsp_short_code,
            wst_completed_routes=audit_result.wst_metrics.total_completed_routes,
            wst_distance_planned=audit_result.wst_metrics.total_distance_planned,
            wst_distance_allowance=audit_result.wst_metrics.total_distance_allowance,
            wst_amzl_late_cancel=audit_result.wst_metrics.amzl_late_cancel,
            wst_dsp_late_cancel=audit_result.wst_metrics.dsp_late_cancel,
            wst_quick_coverage_accepted=audit_result.wst_metrics.quick_coverage_accepted,
            invoice_total_quantity=sum(c.invoice_quantity for c in audit_result.invoice_lines),
            invoice_subtotal=invoice.subtotal,
            invoice_total_due=invoice.total_due,
            total_lines=len(audit_result.invoice_lines),
            matched_lines=audit_result.total_matches,
            variance_lines=audit_result.total_variances,
            unmatched_lines=audit_result.total_unmatched,
            aligned=audit_result.aligned,
            critical_issues=audit_result.critical_issues,
            warnings=audit_result.warnings,
            comparison_details=format_audit_report(audit_result),
        )
        
        db.add(db_audit)
        db.flush()  # Get the ID
        
        # Save line item comparisons
        for comparison in audit_result.invoice_lines:
            line_item = WeeklyAuditLineItem(
                audit_id=db_audit.id,
                invoice_description=comparison.description,
                invoice_quantity=comparison.invoice_quantity,
                invoice_rate=comparison.invoice_rate,
                invoice_amount=comparison.invoice_amount,
                category=comparison.category,
                subcategory=comparison.matched_metric.split('_')[1] if '_' in (comparison.matched_metric or '') else None,
                matched_metric=comparison.matched_metric,
                wst_expected_value=comparison.wst_expected_value,
                variance=comparison.variance,
                status=comparison.status,
                issues=comparison.issues,
            )
            db.add(line_item)
        
        db.commit()
        db.refresh(db_audit)
        
        # Load full audit with line items
        db_audit = db.query(WeeklyInvoiceAudit).filter(
            WeeklyInvoiceAudit.id == db_audit.id
        ).first()
        
        return db_audit
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Audit failed: {str(e)}")
    finally:
        db.close()


@router.get("/audit/{audit_id}", response_model=WeeklyAuditOut)
def get_weekly_audit(
    audit_id: int,
    role: str = Depends(get_current_user_role),
):
    """Retrieve a saved weekly audit by ID."""
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        audit = db.query(WeeklyInvoiceAudit).filter(
            WeeklyInvoiceAudit.id == audit_id
        ).first()
        
        if not audit:
            raise HTTPException(status_code=404, detail=f"Audit not found: {audit_id}")
        
        return audit
    
    finally:
        db.close()


@router.get("/invoice/{invoice_number}", response_model=List[WeeklyAuditOut])
def list_weekly_audits_for_invoice(
    invoice_number: str,
    role: str = Depends(get_current_user_role),
):
    """List all audits for a given invoice."""
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        audits = db.query(WeeklyInvoiceAudit).filter(
            WeeklyInvoiceAudit.invoice_number == invoice_number
        ).order_by(WeeklyInvoiceAudit.audit_date.desc()).all()
        
        return audits
    
    finally:
        db.close()


@router.get("/period", response_model=List[WeeklyAuditOut])
def list_weekly_audits_by_period(
    start_date: date,
    end_date: date,
    station: Optional[str] = None,
    approval_status: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """
    List weekly audits for a date range.
    
    Optional filters:
    - station: Filter by station code
    - approval_status: Filter by approval status (pending, approved, disputed)
    """
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        query = db.query(WeeklyInvoiceAudit).filter(
            WeeklyInvoiceAudit.period_start >= start_date,
            WeeklyInvoiceAudit.period_end <= end_date,
        )
        
        if station:
            query = query.filter(WeeklyInvoiceAudit.station == station)
        
        if approval_status:
            query = query.filter(WeeklyInvoiceAudit.approval_status == approval_status)
        
        audits = query.order_by(WeeklyInvoiceAudit.audit_date.desc()).all()
        
        return audits
    
    finally:
        db.close()


@router.patch("/audit/{audit_id}/approve", response_model=WeeklyAuditOut)
def approve_weekly_audit(
    audit_id: int,
    request: WeeklyAuditApprovalRequest,
    role: str = Depends(get_current_user_role),
):
    """
    Update approval status for a weekly audit.
    
    Status can be:
    - 'approved': Invoice matches WST data
    - 'disputed': Invoice has variances requiring dispute
    """
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        audit = db.query(WeeklyInvoiceAudit).filter(
            WeeklyInvoiceAudit.id == audit_id
        ).first()
        
        if not audit:
            raise HTTPException(status_code=404, detail=f"Audit not found: {audit_id}")
        
        if request.approval_status not in ("approved", "disputed"):
            raise HTTPException(
                status_code=400,
                detail="approval_status must be 'approved' or 'disputed'"
            )
        
        # Validation: don't approve if there are critical issues
        if request.approval_status == "approved" and audit.critical_issues:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve audit with {len(audit.critical_issues)} critical issue(s)"
            )
        
        audit.approval_status = request.approval_status
        audit.approval_notes = request.approval_notes
        audit.reviewed_by = role
        audit.reviewed_at = datetime.utcnow()
        
        db.commit()
        db.refresh(audit)
        
        return audit
    
    finally:
        db.close()


@router.get("/summary")
def weekly_audit_summary(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    station: Optional[str] = None,
    role: str = Depends(get_current_user_role),
):
    """
    Get summary statistics for weekly audits.
    
    Returns counts by approval status and alignment.
    """
    require_financial_access(role)
    
    db = SessionLocal()
    try:
        query = db.query(WeeklyInvoiceAudit)
        
        if start_date:
            query = query.filter(WeeklyInvoiceAudit.audit_date >= start_date)
        if end_date:
            query = query.filter(WeeklyInvoiceAudit.audit_date <= end_date)
        if station:
            query = query.filter(WeeklyInvoiceAudit.station == station)
        
        audits = query.all()
        
        summary = {
            "total_audits": len(audits),
            "aligned": sum(1 for a in audits if a.aligned),
            "has_variances": sum(1 for a in audits if not a.aligned),
            "by_approval_status": {
                "pending": sum(1 for a in audits if a.approval_status == "pending"),
                "approved": sum(1 for a in audits if a.approval_status == "approved"),
                "disputed": sum(1 for a in audits if a.approval_status == "disputed"),
            },
            "average_variance_count": (
                sum(a.variance_lines for a in audits) / len([a for a in audits if not a.aligned])
                if any(not a.aligned for a in audits)
                else 0
            ),
        }
        
        return summary
    
    finally:
        db.close()
