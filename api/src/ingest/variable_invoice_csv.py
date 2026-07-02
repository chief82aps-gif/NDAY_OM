"""
Parse and ingest variable invoice CSV files
Supports real DSP invoice format with multiple service categories
"""

import csv
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple
from api.src.database import SessionLocal, VariableInvoice, VariableInvoiceLineItem
from sqlalchemy import text

logger = logging.getLogger(__name__)


class VariableInvoiceCSVParser:
    """Parse variable invoice CSV exports from DSP systems"""

    REQUIRED_COLUMNS = [
        "Station", "Invoice Number", "Invoice Date", "Invoice Type",
        "Payment Type", "Service Type Category", "Service Type", "Transaction Amount"
    ]

    SERVICE_CATEGORIES = {
        "Variable Per Shipment": "packages",
        "Routes": "routes",
        "Training Ride Along": "training",
        "Nursery Route": "nursery_route",
        "Unplanned Delay": "unplanned_delay",
        "Late Cancellation AMZL": "late_cancellation",
        "Delivery-Excellence Incentive": "dei_incentive",
        "Peak Seasonal Route Payment": "seasonal_route",
        "Per Piece Incentive": "piece_incentive",
        "Thank My Driver": "thank_driver",
        "Other": "other",
    }

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.rows = []
        self.invoices: Dict[str, Dict] = {}
        self.errors = []

    def parse(self) -> Tuple[Dict, List[str]]:
        """Parse CSV file and aggregate by invoice."""
        try:
            with open(self.file_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)

                if not reader.fieldnames:
                    self.errors.append("CSV is empty")
                    return {}, self.errors

                cleaned_fieldnames = [col.strip().strip('"').strip() for col in reader.fieldnames]

                missing_cols = set(self.REQUIRED_COLUMNS) - set(cleaned_fieldnames)
                if missing_cols:
                    self.errors.append(f"Missing columns: {missing_cols}")
                    return {}, self.errors

                for row_num, row in enumerate(reader, start=2):
                    try:
                        cleaned_row = {k.strip().strip('"').strip(): v for k, v in row.items()}
                        self._process_row(cleaned_row)
                    except Exception as e:
                        self.errors.append(f"Row {row_num}: {str(e)}")
                        logger.error(f"Error parsing row {row_num}: {e}")

            return self.invoices, self.errors

        except Exception as e:
            self.errors.append(f"File read error: {str(e)}")
            return {}, self.errors

    def _process_row(self, row: Dict) -> None:
        """Process a single CSV row."""
        invoice_number = row.get("Invoice Number", "").strip()
        station = row.get("Station", "").strip()
        invoice_date_str = row.get("Invoice Date", "").strip()
        invoice_type = row.get("Invoice Type", "ORIGINAL").strip()
        payment_type = row.get("Payment Type", "").strip()
        service_category = row.get("Service Type Category", "").strip()
        service_type = row.get("Service Type", "").strip()

        try:
            amount = float(row.get("Transaction Amount", "0"))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid amount: {row.get('Transaction Amount')}")

        if not invoice_number or not station:
            raise ValueError("Missing invoice number or station")

        try:
            invoice_date = datetime.fromisoformat(invoice_date_str.replace(" 00:00:00", ""))
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid date format: {invoice_date_str}")

        if invoice_number not in self.invoices:
            self.invoices[invoice_number] = {
                "station": station,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "invoice_type": invoice_type,
                "line_items": [],
                "total_amount": 0,
                "categories": {}
            }

        line_item = {
            "payment_type": payment_type,
            "service_category": service_category,
            "service_type": service_type,
            "amount": amount,
        }

        self.invoices[invoice_number]["line_items"].append(line_item)
        self.invoices[invoice_number]["total_amount"] += amount

        if service_category not in self.invoices[invoice_number]["categories"]:
            self.invoices[invoice_number]["categories"][service_category] = {
                "amount": 0,
                "items": []
            }
        self.invoices[invoice_number]["categories"][service_category]["amount"] += amount
        self.invoices[invoice_number]["categories"][service_category]["items"].append(line_item)


class VariableInvoiceIngestor:
    """Ingest parsed invoices into database."""

    def __init__(self):
        self.db = SessionLocal()

    def ingest(self, invoices: Dict) -> Tuple[int, List[str]]:
        """Ingest invoices into database. Returns (count_ingested, errors)."""
        ingested = 0
        errors = []

        try:
            for invoice_number, invoice_data in invoices.items():
                try:
                    existing = self.db.query(VariableInvoice).filter_by(
                        invoice_number=invoice_number
                    ).first()

                    if existing:
                        logger.info(f"Invoice {invoice_number} already exists, skipping")
                        continue

                    total_amount = Decimal(str(invoice_data["total_amount"]))

                    invoice = VariableInvoice(
                        invoice_number=invoice_number,
                        station=invoice_data["station"],
                        invoice_date=invoice_data["invoice_date"],
                        total_due=total_amount,
                        subtotal=total_amount,
                        tax_rate=Decimal(0),
                        tax_due=Decimal(0),
                        source_file=self.file_path if hasattr(self, 'file_path') else "csv_import",
                    )

                    self.db.add(invoice)
                    self.db.flush()

                    for category, cat_data in invoice_data["categories"].items():
                        li = VariableInvoiceLineItem(
                            invoice_id=invoice.id,
                            description=category,
                            amount=Decimal(str(cat_data["amount"])),
                            quantity=Decimal(len(cat_data["items"])),
                            rate=Decimal(0),
                            instance_count=len(cat_data["items"])
                        )
                        self.db.add(li)

                    self.db.commit()
                    ingested += 1
                    logger.info(f"Ingested invoice {invoice_number}")

                except Exception as e:
                    self.db.rollback()
                    errors.append(f"Invoice {invoice_number}: {str(e)}")
                    logger.error(f"Error ingesting {invoice_number}: {e}")

        finally:
            self.db.close()

        return ingested, errors


def ingest_variable_invoice_csv(file_path: str) -> Tuple[Dict, List[str]]:
    """Parse and ingest a variable invoice CSV file. Returns (result_dict, errors)."""
    parser = VariableInvoiceCSVParser(file_path)
    invoices, parse_errors = parser.parse()

    if parse_errors:
        logger.warning(f"Parse errors: {parse_errors}")

    ingestor = VariableInvoiceIngestor()
    ingestor.file_path = file_path
    ingested, ingest_errors = ingestor.ingest(invoices)

    result = {
        "invoices_parsed": len(invoices),
        "invoices_ingested": ingested,
        "total_amount": sum(inv["total_amount"] for inv in invoices.values()),
        "line_items": sum(len(inv["line_items"]) for inv in invoices.values()),
        "categories": list(set(
            cat for inv in invoices.values()
            for cat in inv["categories"].keys()
        )),
    }

    return result, parse_errors + ingest_errors
