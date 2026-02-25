from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.src.audit_variable_invoice import (
    build_variable_invoice_audit,
    METRIC_KEYS,
    parse_query_dates,
    resolve_audit_date_range,
    save_invoice_mappings,
)
from api.src.authorization import get_current_user_role, require_financial_access
from api.src.database import SessionLocal, VariableInvoice

router = APIRouter()


class InvoiceMappingItem(BaseModel):
    description: str
    metric_key: str


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
        report = build_variable_invoice_audit(
            db,
            invoice,
            resolved_start,
            resolved_end,
            audit_station,
        )
        return report
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
