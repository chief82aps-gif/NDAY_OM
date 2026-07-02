"""
Weekly Audit Upload and Validation API endpoints.

Handles:
- File uploads (WST and Invoice CSVs)
- Parsing and storing parsed data
- Invoice listing for audit selection
- Pre-audit validation prompts
- Correction and dispute collection
- Final audit execution
"""

import os
import csv
import json
import zipfile
import tempfile
import re
from datetime import datetime, date
from typing import List, Optional, Dict, Any
from decimal import Decimal
import pdfplumber

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from api.src.database import (
    SessionLocal,
    UploadedFile,
    ParsedInvoiceData,
    WstWeeklyReport,
    AuditCorrection,
    WeeklyAuditDispute,
    WeeklyInvoiceAudit,
    WeeklyAuditLineItem,
)
from api.src.authorization import require_financial_access
from api.src.audit_weekly_invoice import build_weekly_audit, format_audit_report
from api.src.service_type_matrix import map_service_type, to_float, infer_day_bucket

router = APIRouter(prefix="/audit/weekly", tags=["weekly_audit_upload"])

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads", "weekly_audit")


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# FILE PARSING HELPERS
# ============================================================================

def parse_csv_file(file_path: str) -> List[Dict[str, str]]:
    """Parse CSV file and return list of dicts."""
    rows = []
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:  # UTF-8-sig handles BOM
            csv_reader = csv.DictReader(f)
            # Clean column names
            if csv_reader.fieldnames:
                csv_reader.fieldnames = [h.strip() if h else h for h in csv_reader.fieldnames]
            
            for row in csv_reader:
                row_clean = {}
                if row:
                    for k, v in row.items():
                        # Clean keys and handle BOM
                        clean_k = (k.strip() if k else k).lstrip('\ufeff')
                        row_clean[clean_k] = v
                if row_clean:
                    rows.append(row_clean)
        
        # DEBUG
        import sys
        print(f"[CSV PARSE] File: {file_path}, Rows found: {len(rows)}", file=sys.stderr)
        if rows:
            print(f"[CSV PARSE] First row columns: {list(rows[0].keys())}", file=sys.stderr)
            print(f"[CSV PARSE] Sample data (first 3 rows):", file=sys.stderr)
            for i, row in enumerate(rows[:3]):
                print(f"  Row {i}: {dict(list(row.items())[:5])}", file=sys.stderr)
    except Exception as e:
        raise ValueError(f"Failed to parse CSV: {str(e)}")
    return rows


def parse_wst_file(file_path: str) -> Dict[str, Any]:
    """Parse WST weekly export file - handles both ZIP and CSV formats."""
    
    # Check if it's a ZIP file
    is_zip = file_path.lower().endswith('.zip')
    
    result = {
        "records": [],
        "completed_routes": 0,
        "distance_planned": 0.0,
        "distance_allowance": 0.0,
    }
    
    csv_file_path = file_path
    temp_dir = None
    
    # Extract CSV from ZIP if needed
    if is_zip:
        try:
            temp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Find first CSV file
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file.lower().endswith('.csv'):
                        csv_file_path = os.path.join(root, file)
                        break
                if csv_file_path != file_path:
                    break
        except Exception as e:
            result["error"] = f"Failed to extract ZIP: {str(e)}"
            return result
    
    try:
        rows = parse_csv_file(csv_file_path)
    finally:
        # Clean up temp directory
        if temp_dir:
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
    
    import sys
    print(f"[WST PARSE] Total rows in CSV: {len(rows)}", file=sys.stderr)
    if rows:
        print(f"[WST PARSE] First row columns: {list(rows[0].keys())}", file=sys.stderr)
    
    # Helper to find column by exact match (case-insensitive)
    def find_column_exact(row, exact_names):
        """Find column by exact name match (case-insensitive)."""
        if not row:
            return None
        row_keys_lower = {k.lower(): k for k in row.keys() if k}
        for name in exact_names:
            lower_name = name.lower()
            if lower_name in row_keys_lower:
                return row_keys_lower[lower_name]
        return None
    
    # Track columns found
    found_cols = {}
    record_count = 0

    for row_idx, row in enumerate(rows):
        if not row or all(not v for v in row.values()):
            continue
        
        # Find columns on first data row
        if row_idx == 0 or not found_cols:
            found_cols = {
                "date": find_column_exact(row, ["Date", "Week"]),
                "completed": find_column_exact(row, ["Completed Routes", "Completed"]),
                "distance_planned": find_column_exact(row, ["Distance Planned", "Distance"]),
                "allowance": find_column_exact(row, ["Distance Allowance", "Allowance"]),
                "associate": find_column_exact(row, ["Associate", "Driver", "Name"]),
                "route": find_column_exact(row, ["Route", "Route Code"]),
                "shipments": find_column_exact(row, ["Shipments"]),
                "package_count": find_column_exact(row, ["Package Count"]),
                "package_details": find_column_exact(row, ["Package Details"]),
                "package_type": find_column_exact(row, ["Package Type"]),
                "station": find_column_exact(row, ["Station"]),
                "dsp_short_code": find_column_exact(row, ["DSP Short Code"]),
            }
            print(f"[WST PARSE] Mapped columns: {found_cols}", file=sys.stderr)
        
        # Extract record data
        record = {}
        
        # Get values
        for key, col in found_cols.items():
            if col and row.get(col):
                val = str(row[col]).strip()
                if val:
                    record[key] = val
        
        # Add aggregates
        if found_cols.get("completed") and row.get(found_cols["completed"]):
            try:
                result["completed_routes"] += int(str(row[found_cols["completed"]]).replace(',', ''))
            except:
                pass

        # WST delivered packages format support (Date, Package Count, Package Details, Package Type)
        package_count = 0.0
        if found_cols.get("package_count") and row.get(found_cols["package_count"]):
            try:
                package_count = to_float(row[found_cols["package_count"]])
                result["completed_routes"] += int(package_count)
            except:
                package_count = 0.0
        
        if found_cols.get("distance_planned") and row.get(found_cols["distance_planned"]):
            try:
                result["distance_planned"] += float(str(row[found_cols["distance_planned"]]).replace(',', ''))
            except:
                pass
        
        if found_cols.get("allowance") and row.get(found_cols["allowance"]):
            try:
                result["distance_allowance"] += float(str(row[found_cols["allowance"]]).replace(',', ''))
            except:
                pass
        
        # Add record if has data
        if package_count > 0:
            record["quantity"] = package_count
            package_details_val = row.get(found_cols["package_details"], "") if found_cols.get("package_details") else ""
            package_type_val = row.get(found_cols["package_type"], "") if found_cols.get("package_type") else ""
            record["service_type"] = map_service_type(package_details_val, package_type_val)
            if found_cols.get("station") and row.get(found_cols["station"]):
                record["station"] = str(row[found_cols["station"]]).strip()
            if found_cols.get("dsp_short_code") and row.get(found_cols["dsp_short_code"]):
                record["dsp_short_code"] = str(row[found_cols["dsp_short_code"]]).strip()
            record["day_bucket"] = infer_day_bucket(record.get("date"), package_details_val)

        if record:
            result["records"].append(record)
            record_count += 1
    
    print(f"[WST PARSE] Extracted {record_count} records, completed_routes={result['completed_routes']}, distance_planned={result['distance_planned']}, allowance={result['distance_allowance']}", file=sys.stderr)
    
    return result


def parse_invoice_file(file_path: str) -> Dict[str, Any]:
    """Parse variable invoice export file - handle detailed line item format."""
    if file_path.lower().endswith('.pdf'):
        return parse_invoice_pdf(file_path)

    rows = parse_csv_file(file_path)
    
    invoice_data = {
        "invoice_number": None,
        "period_start": None,
        "period_end": None,
        "station": None,
        "subtotal": Decimal("0.00"),
        "tax_amount": Decimal("0.00"),
        "total_amount": Decimal("0.00"),
        "line_items": [],
    }
    
    if not rows:
        return invoice_data

    # Re-parse with header-row detection if DictReader likely used the wrong header row.
    def normalize_header(value: str) -> str:
        return "".join(ch for ch in str(value).lower().strip() if ch.isalnum())

    def build_rows_from_detected_header(path: str) -> List[Dict[str, Any]]:
        expected_tokens = {
            "station",
            "invoicenumber",
            "invoicedate",
            "servicetype",
            "transactionamount",
        }

        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            raw_rows = list(csv.reader(f))

        if not raw_rows:
            return []

        header_idx = None
        best_score = -1
        for idx, raw in enumerate(raw_rows[:25]):
            if not raw:
                continue
            normalized = [normalize_header(cell) for cell in raw if str(cell).strip()]
            score = sum(1 for cell in normalized if cell in expected_tokens)
            if score > best_score:
                best_score = score
                header_idx = idx

        if header_idx is None or best_score < 2:
            return []

        headers = [str(h).strip() for h in raw_rows[header_idx]]
        rebuilt: List[Dict[str, Any]] = []
        for raw in raw_rows[header_idx + 1 :]:
            if not raw or all(not str(v).strip() for v in raw):
                continue
            padded = raw + [""] * max(0, len(headers) - len(raw))
            rebuilt.append({headers[i]: padded[i] for i in range(len(headers))})
        return rebuilt
    
    # Helper to find column by normalized exact match (case-insensitive, punctuation-insensitive)
    def find_column_exact(row, exact_names):
        """Find column by normalized exact name match."""
        if not row:
            return None
        row_keys_lower = {normalize_header(k): k for k in row.keys() if k}
        for name in exact_names:
            lower_name = normalize_header(name)
            if lower_name in row_keys_lower:
                return row_keys_lower[lower_name]
        return None
    
    # Get first row to establish column mapping
    first_row = rows[0] if rows else {}
    
    import sys
    print(f"[INVOICE PARSE] Total rows: {len(rows)}", file=sys.stderr)
    print(f"[INVOICE PARSE] First row keys: {list(first_row.keys())}", file=sys.stderr)
    
    # Map columns - look for exact names (normalized)
    station_col = find_column_exact(first_row, ["Station", "station"])
    invoice_col = find_column_exact(first_row, ["Invoice Number", "invoice number"])
    description_col = find_column_exact(first_row, ["Service Type", "service type"])  # Exact match - NOT "Category"
    amount_col = find_column_exact(first_row, ["Transaction Amount", "transaction amount"])

    # If no useful mapping, attempt robust header detection and remap.
    if not any([station_col, invoice_col, description_col, amount_col]):
        rebuilt_rows = build_rows_from_detected_header(file_path)
        if rebuilt_rows:
            rows = rebuilt_rows
            first_row = rows[0]
            station_col = find_column_exact(first_row, ["Station", "station"])
            invoice_col = find_column_exact(first_row, ["Invoice Number", "invoice number"])
            description_col = find_column_exact(first_row, ["Service Type", "service type"])
            amount_col = find_column_exact(first_row, ["Transaction Amount", "transaction amount"])

    # Fallback: map by known export positions when headers are inconsistent.
    # Typical detail export order ends with: Service Type Category, Service Type, Transaction Amount
    keys_in_order = list(first_row.keys()) if first_row else []
    if keys_in_order:
        if not amount_col:
            amount_col = keys_in_order[-1]
        if not description_col and len(keys_in_order) >= 2:
            desc_candidate = keys_in_order[-2]
            if "category" not in normalize_header(desc_candidate):
                description_col = desc_candidate
        if not station_col:
            for key in keys_in_order:
                if "station" in normalize_header(key):
                    station_col = key
                    break
        if not invoice_col:
            for key in keys_in_order:
                norm = normalize_header(key)
                if "invoice" in norm and "number" in norm:
                    invoice_col = key
                    break
    
    print(f"[INVOICE PARSE] Mapped columns - station={station_col}, invoice={invoice_col}, desc={description_col}, amount={amount_col}", file=sys.stderr)

    def parse_amount_value(raw_value: Any) -> float:
        text = str(raw_value).strip()
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in {'.', '-', ','}).replace(',', '')
        return float(cleaned) if cleaned else 0.0
    
    total_amount = Decimal("0.00")
    line_count = 0
    
    # Process all rows as line items
    for row_idx, row in enumerate(rows):
        if not row:
            continue
        
        # Extract header info from first row
        if row_idx == 0:
            if station_col and row.get(station_col):
                invoice_data["station"] = str(row[station_col]).strip()
            if invoice_col and row.get(invoice_col):
                invoice_data["invoice_number"] = str(row[invoice_col]).strip()
        
        # Extract line item
        description = ""
        amount = 0.0
        
        if description_col and row.get(description_col):
            description = str(row[description_col]).strip()

        # Heuristic fallback for description when expected column is missing
        if not description:
            for key, val in row.items():
                if not val:
                    continue
                key_norm = normalize_header(key)
                if "category" in key_norm:
                    continue
                if any(token in key_norm for token in ["servicetype", "service", "description", "charge", "paymenttype"]):
                    candidate = str(val).strip()
                    if candidate:
                        description = candidate
                        break
        
        if amount_col and row.get(amount_col):
            try:
                amount = parse_amount_value(row[amount_col])
                total_amount += Decimal(str(amount))
            except:
                pass

        # Heuristic fallback for amount when expected column is missing
        if amount == 0.0:
            for key, val in row.items():
                if not val:
                    continue
                key_norm = normalize_header(key)
                if any(token in key_norm for token in ["amount", "charge", "total"]):
                    try:
                        parsed_amount = parse_amount_value(val)
                        if parsed_amount != 0.0:
                            amount = parsed_amount
                            total_amount += Decimal(str(amount))
                            break
                    except:
                        pass

        # Last-resort fallback: keep row as a line item if it has non-empty data
        if not description and amount == 0.0:
            non_empty_values = [str(v).strip() for v in row.values() if str(v).strip()]
            if len(non_empty_values) >= 3:
                description = non_empty_values[0]
        
        # Add as line item if has description or amount
        if description or amount:
            line_item = {
                "description": description,
                "quantity": 1,
                "rate": amount,
                "amount": amount,
                "service_type": map_service_type(description),
                "day_bucket": infer_day_bucket(None, description),
            }
            invoice_data["line_items"].append(line_item)
            line_count += 1
    
    invoice_data["total_amount"] = total_amount
    invoice_data["subtotal"] = total_amount
    
    print(f"[INVOICE PARSE] Extracted {line_count} line items with total {total_amount}", file=sys.stderr)
    
    # Use form dates as defaults if not found in file
    # (These will be set by caller with period_start/period_end parameters)
    
    return invoice_data


def parse_invoice_pdf(file_path: str) -> Dict[str, Any]:
    """Parse incentives invoice PDF into invoice_data shape used by upload endpoint."""
    import sys

    invoice_data = {
        "invoice_number": None,
        "period_start": None,
        "period_end": None,
        "station": None,
        "subtotal": Decimal("0.00"),
        "tax_amount": Decimal("0.00"),
        "total_amount": Decimal("0.00"),
        "line_items": [],
    }

    with pdfplumber.open(file_path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]

    if not pages_text:
        return invoice_data

    all_text = "\n".join(pages_text)

    invoice_match = re.search(r"Amazon\s+Unique\s+Id:\s*(INV-[A-Z0-9\-]+)", all_text, re.IGNORECASE)
    if not invoice_match:
        invoice_match = re.search(r"\b(INV-[A-Z0-9\-]+)\b", all_text)
    if not invoice_match:
        invoice_match = re.search(r"Invoice\s+Number:\s*([A-Z0-9\-]+)", all_text, re.IGNORECASE)
    if invoice_match:
        invoice_data["invoice_number"] = invoice_match.group(1).strip()

    station_match = re.search(r"Ship To\s+([A-Z]{3}\d)", all_text)
    if station_match:
        invoice_data["station"] = station_match.group(1).strip()

    period_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})", all_text)
    if period_match:
        try:
            invoice_data["period_start"] = datetime.strptime(period_match.group(1), "%m/%d/%Y").date()
            invoice_data["period_end"] = datetime.strptime(period_match.group(2), "%m/%d/%Y").date()
        except Exception:
            pass

    line_pattern = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+\$?([0-9,]+(?:\.[0-9]+)?)\s+([0-9,]+(?:\.[0-9]+)?)\s+\$?([0-9,]+(?:\.[0-9]+)?)$"
    )

    total_amount = Decimal("0.00")

    for page_text in pages_text:
        for raw_line in page_text.splitlines():
            line = " ".join(raw_line.split())
            match = line_pattern.match(line)
            if not match:
                continue

            service_date, description, rate_raw, qty_raw, amount_raw = match.groups()
            try:
                rate = float(rate_raw.replace(',', ''))
                quantity = float(qty_raw.replace(',', ''))
                amount = float(amount_raw.replace(',', ''))
            except Exception:
                continue

            invoice_data["line_items"].append({
                "service_date": service_date,
                "description": description.strip(),
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
                "service_type": map_service_type(description),
                "day_bucket": infer_day_bucket(service_date, description),
            })
            total_amount += Decimal(str(amount))

    invoice_data["total_amount"] = total_amount
    invoice_data["subtotal"] = total_amount

    print(
        f"[INVOICE PDF PARSE] invoice={invoice_data['invoice_number']} station={invoice_data['station']} "
        f"line_items={len(invoice_data['line_items'])} total={invoice_data['total_amount']}",
        file=sys.stderr,
    )

    return invoice_data


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/upload")
async def upload_files(
    file_type: str = Form(...),  # 'wst' or 'invoice'
    station: str = Form(...),
    period_start: str = Form(...),  # YYYY-MM-DD
    period_end: str = Form(...),  # YYYY-MM-DD
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    """Upload and parse WST or Invoice weekly file."""
    
    try:
        # Create upload directory
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        
        # Save file
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Parse based on type
        try:
            if file_type.lower() == "wst":
                data = parse_wst_file(file_path)
            elif file_type.lower() == "invoice":
                data = parse_invoice_file(file_path)
            else:
                raise ValueError("Invalid file_type. Must be 'wst' or 'invoice'")
            
            parse_status = "completed"
            parse_error = None
            
            # Count records appropriately
            if file_type.lower() == "invoice":
                record_count = len(data.get("line_items", []))
            else:  # wst
                record_count = len(data.get("records", []))
        except Exception as e:
            parse_status = "failed"
            parse_error = str(e)
            data = {}
            record_count = 0
        
        # Store file metadata
        period_start_date = datetime.strptime(period_start, "%Y-%m-%d").date()
        period_end_date = datetime.strptime(period_end, "%Y-%m-%d").date()
        
        uploaded_file = UploadedFile(
            file_type=file_type.lower(),
            filename=file.filename,
            file_path=file_path,
            file_size=len(content),
            period_start=period_start_date,
            period_end=period_end_date,
            station=station,
            uploaded_by="test_user",
            parse_status=parse_status,
            parse_error=parse_error,
            record_count=record_count,
        )
        db.add(uploaded_file)
        db.commit()
        db.refresh(uploaded_file)
        
        # If invoice, also store parsed data
        if file_type.lower() == "invoice" and parse_status == "completed":
            parsed_invoice = ParsedInvoiceData(
                file_id=uploaded_file.id,
                invoice_number=data.get("invoice_number", f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
                period_start=data.get("period_start") or period_start_date,
                period_end=data.get("period_end") or period_end_date,
                station=station,
                subtotal=data.get("subtotal"),
                tax_amount=data.get("tax_amount"),
                total_amount=data.get("total_amount"),
                line_items_json=data.get("line_items", []),
            )
            db.add(parsed_invoice)
            db.commit()
            db.refresh(parsed_invoice)
        
        # If WST, store weekly aggregate snapshot for audit lookup
        if file_type.lower() == "wst" and parse_status == "completed":
            # Replace existing WST rows for the same station/period to avoid duplicates
            db.query(WstWeeklyReport).filter(
                WstWeeklyReport.station == station,
                WstWeeklyReport.report_date >= period_start_date,
                WstWeeklyReport.report_date <= period_end_date,
            ).delete(synchronize_session=False)

            records = data.get("records", []) or []
            inserted = 0

            for record in records:
                report_date_val = None
                raw_date = str(record.get("date", "")).strip()
                if raw_date:
                    try:
                        report_date_val = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
                    except Exception:
                        report_date_val = None

                if not report_date_val:
                    continue

                quantity = record.get("quantity", 0)
                try:
                    completed_routes = int(float(quantity))
                except Exception:
                    completed_routes = 0

                wst_row = WstWeeklyReport(
                    report_date=report_date_val,
                    station=station,
                    dsp_short_code=record.get("dsp_short_code"),
                    service_type=record.get("service_type") or "UNKNOWN",
                    planned_duration=None,
                    total_distance_planned=Decimal(str(data.get("distance_planned", 0) or 0)),
                    total_distance_allowance=Decimal(str(data.get("distance_allowance", 0) or 0)),
                    planned_distance_unit="mi",
                    amzl_late_cancel=Decimal("0"),
                    dsp_late_cancel=Decimal("0"),
                    quick_coverage_accepted=Decimal("0"),
                    completed_routes=completed_routes,
                    source_file=file.filename,
                )
                db.add(wst_row)
                inserted += 1

            # Fallback single summary row when no daily rows could be parsed
            if inserted == 0:
                db.add(WstWeeklyReport(
                    report_date=period_start_date,
                    station=station,
                    dsp_short_code=None,
                    service_type="WEEK_TOTAL",
                    planned_duration=None,
                    total_distance_planned=Decimal(str(data.get("distance_planned", 0) or 0)),
                    total_distance_allowance=Decimal(str(data.get("distance_allowance", 0) or 0)),
                    planned_distance_unit="mi",
                    amzl_late_cancel=Decimal("0"),
                    dsp_late_cancel=Decimal("0"),
                    quick_coverage_accepted=Decimal("0"),
                    completed_routes=int(data.get("completed_routes", 0) or 0),
                    source_file=file.filename,
                ))

            db.commit()
        
        return {
            "file_id": uploaded_file.id,
            "filename": file.filename,
            "file_type": file_type,
            "parse_status": parse_status,
            "parse_error": parse_error,
            "record_count": record_count,
            "data": data if file_type.lower() == "invoice" else None,
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/invoices")
async def list_invoices(
    db: Session = Depends(get_session),
):
    """List available invoices for audit, newest first."""
    
    invoices = db.query(ParsedInvoiceData).filter(
        ParsedInvoiceData.is_validated == False
    ).order_by(
        desc(ParsedInvoiceData.created_at)
    ).all()
    
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "period_start": inv.period_start.isoformat(),
            "period_end": inv.period_end.isoformat(),
            "station": inv.station,
            "total_amount": float(inv.total_amount or 0),
            "line_count": len(inv.line_items_json) if inv.line_items_json else 0,
            "uploaded_at": inv.created_at.isoformat(),
        }
        for inv in invoices
    ]


@router.get("/validate/{invoice_id}")
async def get_invoice_validation_data(
    invoice_id: int,
    db: Session = Depends(get_session),
):
    """Get invoice data for validation/confirmation before audit."""
    
    inv = db.query(ParsedInvoiceData).filter(ParsedInvoiceData.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "period_start": inv.period_start.isoformat(),
        "period_end": inv.period_end.isoformat(),
        "station": inv.station,
        "subtotal": float(inv.subtotal or 0),
        "tax_amount": float(inv.tax_amount or 0),
        "total_amount": float(inv.total_amount or 0),
        "line_items": inv.line_items_json or [],
        "validation_prompts": [
            {
                "id": "header_check",
                "question": f"Does the invoice number '{inv.invoice_number}' look correct?",
                "type": "confirmation",
            },
            {
                "id": "period_check",
                "question": f"Is the period correct? {inv.period_start} to {inv.period_end}",
                "type": "confirmation",
            },
            {
                "id": "line_count_check",
                "question": f"Invoice has {len(inv.line_items_json or [])} line items. Is this complete?",
                "type": "confirmation",
            },
            {
                "id": "total_check",
                "question": f"Total amount is ${float(inv.total_amount or 0):.2f}. Is this correct?",
                "type": "confirmation",
            },
        ],
    }


@router.post("/validate")
async def validate_invoice(
    invoice_id: int,
    validation_responses: Dict[str, bool],
    db: Session = Depends(get_session),
):
    """Confirm invoice data and mark as validated."""
    
    inv = db.query(ParsedInvoiceData).filter(ParsedInvoiceData.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Check if all validations passed
    all_valid = all(validation_responses.values())
    
    if not all_valid:
        failed_checks = [k for k, v in validation_responses.items() if not v]
        return {
            "status": "needs_correction",
            "invoice_id": invoice_id,
            "failed_validations": failed_checks,
            "next_step": "correct_errors",
        }
    
    # Mark as validated
    inv.is_validated = True
    inv.validated_at = datetime.utcnow()
    inv.validated_by = "test_user"
    inv.validation_notes = json.dumps(validation_responses)
    db.commit()
    
    return {
        "status": "validated",
        "invoice_id": invoice_id,
        "message": "Invoice data validated successfully",
        "next_step": "check_wst_data",
    }


@router.post("/corrections/{invoice_id}")
async def submit_corrections(
    invoice_id: int,
    corrections: List[Dict[str, str]],  # [{field, original, corrected, reason}, ...]
    db: Session = Depends(get_session),
):
    """Submit corrections to invoice data before running audit."""
    
    inv = db.query(ParsedInvoiceData).filter(ParsedInvoiceData.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    correction_ids = []
    for correction in corrections:
        # For now, store corrections - they'll be applied when audit is run
        correction_ids.append(correction)
    
    return {
        "status": "corrections_recorded",
        "invoice_id": invoice_id,
        "correction_count": len(corrections),
        "next_step": "run_audit",
    }


@router.post("/disputes/{audit_id}")
async def submit_dispute(
    audit_id: int,
    dispute_category: str,
    dispute_description: str,
    amount_disputed: Optional[float] = None,
    wst_expected: Optional[str] = None,
    invoice_billed: Optional[str] = None,
    evidence_notes: Optional[str] = None,
    db: Session = Depends(get_session),
):
    """Submit a dispute for a specific audit finding."""
    
    audit = db.query(WeeklyInvoiceAudit).filter(WeeklyInvoiceAudit.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    
    dispute = WeeklyAuditDispute(
        audit_id=audit_id,
        dispute_category=dispute_category,
        dispute_description=dispute_description,
        amount_disputed=Decimal(str(amount_disputed)) if amount_disputed else None,
        wst_expected=wst_expected,
        invoice_billed=invoice_billed,
        evidence_notes=evidence_notes,
        created_by="test_user",
        dispute_status="pending",
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    
    return {
        "dispute_id": dispute.id,
        "audit_id": audit_id,
        "status": "submitted",
        "dispute_category": dispute_category,
        "created_at": dispute.created_at.isoformat(),
    }


@router.get("/disputes/{audit_id}")
async def get_audit_disputes(
    audit_id: int,
    db: Session = Depends(get_session),
):
    """Get all disputes for an audit."""
    
    disputes = db.query(WeeklyAuditDispute).filter(
        WeeklyAuditDispute.audit_id == audit_id
    ).all()
    
    return [
        {
            "id": d.id,
            "category": d.dispute_category,
            "description": d.dispute_description,
            "amount": float(d.amount_disputed or 0),
            "status": d.dispute_status,
            "created_at": d.created_at.isoformat(),
        }
        for d in disputes
    ]


@router.post("/run-audit")
async def run_validated_audit(
    invoice_id: int,
    station: str,
    db: Session = Depends(get_session),
):
    """Run audit after validation and corrections."""
    
    inv = db.query(ParsedInvoiceData).filter(ParsedInvoiceData.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    if not inv.is_validated:
        return {
            "status": "error",
            "message": "Invoice must be validated before running audit",
        }
    
    try:
        # Create a temporary invoice object for the audit function
        class InvoiceData:
            pass
        
        invoice = InvoiceData()
        invoice.number = inv.invoice_number
        invoice.invoice_number = inv.invoice_number
        invoice.invoice_date = None
        invoice.period_start = inv.period_start
        invoice.period_end = inv.period_end
        invoice.station = station
        class InvoiceLineItemData:
            pass

        invoice.line_items = []
        for item in (inv.line_items_json or []):
            line = InvoiceLineItemData()
            line.description = item.get("description", "")
            line.quantity = item.get("quantity", 0)
            line.rate = item.get("rate", 0)
            line.amount = item.get("amount", 0)
            line.service_type = item.get("service_type")
            line.day_bucket = item.get("day_bucket")
            line.service_date = item.get("service_date")
            invoice.line_items.append(line)
        
        # Run the audit
        audit_result = build_weekly_audit(db, invoice, station, None)
        
        # Persist audit header
        db_audit = WeeklyInvoiceAudit(
            invoice_number=audit_result.invoice_number,
            period_start=audit_result.period_start,
            period_end=audit_result.period_end,
            station=station,
            dsp_short_code=None,
            wst_completed_routes=audit_result.wst_metrics.total_completed_routes,
            wst_distance_planned=audit_result.wst_metrics.total_distance_planned,
            wst_distance_allowance=audit_result.wst_metrics.total_distance_allowance,
            wst_amzl_late_cancel=audit_result.wst_metrics.amzl_late_cancel,
            wst_dsp_late_cancel=audit_result.wst_metrics.dsp_late_cancel,
            wst_quick_coverage_accepted=audit_result.wst_metrics.quick_coverage_accepted,
            invoice_total_quantity=sum(c.invoice_quantity for c in audit_result.invoice_lines),
            invoice_subtotal=inv.subtotal,
            invoice_total_due=inv.total_amount,
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
        db.flush()

        # Persist line-level comparisons
        for comparison in audit_result.invoice_lines:
            db.add(WeeklyAuditLineItem(
                audit_id=db_audit.id,
                invoice_description=comparison.description,
                invoice_quantity=comparison.invoice_quantity,
                invoice_rate=comparison.invoice_rate,
                invoice_amount=comparison.invoice_amount,
                category=comparison.category,
                subcategory=None,
                matched_metric=comparison.matched_metric,
                wst_expected_value=comparison.wst_expected_value,
                variance=comparison.variance,
                status=comparison.status,
                issues=comparison.issues,
            ))

        # Link parsed invoice to audit record
        inv.audit_id = db_audit.id
        db.commit()
        
        return {
            "status": "success",
            "audit_id": db_audit.id,
            "invoice_number": inv.invoice_number,
            "aligned": audit_result.aligned,
            "matched_lines": audit_result.total_matches,
            "variance_lines": audit_result.total_variances,
            "unmatched_lines": audit_result.total_unmatched,
            "daily_summary_matrix": audit_result.daily_summary_matrix,
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audit failed: {str(e)}")
