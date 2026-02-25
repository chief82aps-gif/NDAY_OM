"""Variable invoice PDF ingest parser (weekly Amazon payment)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Dict, List, Optional, Tuple

import pdfplumber

from api.src.database import VariableInvoice, VariableInvoiceLineItem


@dataclass
class ParsedInvoice:
    invoice_number: str
    amazon_unique_id: Optional[str]
    invoice_date: Optional[datetime]
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    station: Optional[str]
    subtotal: Optional[Decimal]
    tax_rate: Optional[Decimal]
    tax_due: Optional[Decimal]
    total_due: Optional[Decimal]


def _parse_date(value: str) -> Optional[datetime]:
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_decimal(value: str) -> Optional[Decimal]:
    try:
        cleaned = value.replace("$", "").replace(",", "").strip()
        if cleaned == "" or cleaned == "-":
            return None
        return Decimal(cleaned)
    except (InvalidOperation, AttributeError):
        return None


def _extract_summary_lines(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start_idx = None
    end_idx = None
    for i, ln in enumerate(lines):
        if ln.lower().startswith("summary"):
            start_idx = i + 1
            continue
        if start_idx is not None and ln.lower().startswith("subtotal"):
            end_idx = i
            break

    if start_idx is None:
        return []
    if end_idx is None:
        end_idx = len(lines)

    summary_lines = lines[start_idx:end_idx]
    return summary_lines


def _merge_wrapped_lines(lines: List[str]) -> List[str]:
    merged: List[str] = []
    for line in lines:
        # If line looks like a continuation (no money values), append to previous
        if merged and not re.search(r"\$?\d[\d,]*\.\d{2}\s+\d", line):
            merged[-1] = f"{merged[-1]} {line}".strip()
        else:
            merged.append(line)
    return merged


def _parse_summary_line(line: str) -> Optional[Tuple[str, Decimal, Decimal, Decimal]]:
    # Description Rate Quantity Amount
    match = re.match(
        r"^(?P<desc>.+?)\s+\$?(?P<rate>-?[\d,]+\.\d{2})\s+(?P<qty>-?[\d,]+(?:\.\d+)?)\s+\$?(?P<amt>-?[\d,]+\.\d{2})$",
        line,
    )
    if not match:
        return None

    desc = match.group("desc").strip()
    rate = _parse_decimal(match.group("rate"))
    qty = _parse_decimal(match.group("qty"))
    amt = _parse_decimal(match.group("amt"))
    if rate is None or qty is None or amt is None:
        return None
    return desc, rate, qty, amt


def parse_variable_invoice_pdf(file_path: str) -> Tuple[Optional[ParsedInvoice], List[Dict], List[str]]:
    """Parse Variable Invoice PDF and return header + aggregated summary line items."""
    errors: List[str] = []

    with pdfplumber.open(file_path) as pdf:
        if not pdf.pages:
            return None, [], ["Invoice PDF has no pages."]

        page_text = pdf.pages[0].extract_text() or ""

    invoice_number_match = re.search(r"INV-[A-Z0-9\-]+", page_text)
    invoice_number = invoice_number_match.group(0) if invoice_number_match else None
    if not invoice_number:
        errors.append("Invoice number not found.")
        return None, [], errors

    amazon_unique_match = re.search(r"Amazon Unique Id:\s*([A-Z0-9\-]+)", page_text)
    amazon_unique_id = amazon_unique_match.group(1) if amazon_unique_match else None

    invoice_date_match = re.search(r"Invoice Date:\s*([\w/\s,]+)", page_text)
    invoice_date = _parse_date(invoice_date_match.group(1)) if invoice_date_match else None

    period_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})", page_text)
    period_start = _parse_date(period_match.group(1)) if period_match else None
    period_end = _parse_date(period_match.group(2)) if period_match else None

    station_match = re.search(r"Ship To\s+([A-Z0-9]+)", page_text)
    station = station_match.group(1) if station_match else None

    subtotal_match = re.search(r"Subtotal\s+\$?([\d,]+\.\d{2})", page_text)
    tax_rate_match = re.search(r"Tax Rate\s+([\d,]+\.\d{2})%", page_text)
    tax_due_match = re.search(r"Tax Due\s+\$?([\d,]+\.\d{2}|-)", page_text)
    total_due_match = re.search(r"Total Due\s+\$?([\d,]+\.\d{2})", page_text)

    subtotal = _parse_decimal(subtotal_match.group(1)) if subtotal_match else None
    tax_rate = _parse_decimal(tax_rate_match.group(1)) if tax_rate_match else None
    tax_due = _parse_decimal(tax_due_match.group(1)) if tax_due_match else None
    total_due = _parse_decimal(total_due_match.group(1)) if total_due_match else None

    summary_lines = _extract_summary_lines(page_text)
    summary_lines = _merge_wrapped_lines(summary_lines)

    aggregated: Dict[Tuple[str, Decimal], Dict] = {}
    for line in summary_lines:
        parsed = _parse_summary_line(line)
        if not parsed:
            continue
        desc, rate, qty, amt = parsed
        key = (desc, rate)
        if key not in aggregated:
            aggregated[key] = {
                "description": desc,
                "rate": rate,
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "instance_count": 0,
            }
        aggregated[key]["quantity"] += qty
        aggregated[key]["amount"] += amt
        aggregated[key]["instance_count"] += 1

    if not aggregated:
        errors.append("No summary line items parsed.")

    invoice = ParsedInvoice(
        invoice_number=invoice_number,
        amazon_unique_id=amazon_unique_id,
        invoice_date=invoice_date,
        period_start=period_start,
        period_end=period_end,
        station=station,
        subtotal=subtotal,
        tax_rate=tax_rate,
        tax_due=tax_due,
        total_due=total_due,
    )

    return invoice, list(aggregated.values()), errors


def ingest_variable_invoice_pdf(file_path: str, db_session) -> Tuple[Optional[VariableInvoice], List[str]]:
    """Parse and persist a variable invoice PDF into the database."""
    invoice_data, line_items, errors = parse_variable_invoice_pdf(file_path)
    if invoice_data is None:
        return None, errors

    existing = db_session.query(VariableInvoice).filter(
        VariableInvoice.invoice_number == invoice_data.invoice_number
    ).first()
    if existing:
        errors.append("Invoice already exists in database.")
        return existing, errors

    invoice = VariableInvoice(
        invoice_number=invoice_data.invoice_number,
        amazon_unique_id=invoice_data.amazon_unique_id,
        invoice_date=invoice_data.invoice_date.date() if invoice_data.invoice_date else None,
        period_start=invoice_data.period_start.date() if invoice_data.period_start else None,
        period_end=invoice_data.period_end.date() if invoice_data.period_end else None,
        station=invoice_data.station,
        subtotal=invoice_data.subtotal,
        tax_rate=invoice_data.tax_rate,
        tax_due=invoice_data.tax_due,
        total_due=invoice_data.total_due,
        source_file=file_path,
    )

    for item in line_items:
        invoice.line_items.append(
            VariableInvoiceLineItem(
                description=item["description"],
                rate=item["rate"],
                quantity=item["quantity"],
                amount=item["amount"],
                instance_count=item["instance_count"],
            )
        )

    db_session.add(invoice)
    db_session.commit()
    return invoice, errors
