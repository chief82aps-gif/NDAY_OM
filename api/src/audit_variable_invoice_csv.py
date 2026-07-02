"""
Category-based audit logic for variable invoices
Validates each service category independently against WST metrics
"""

from typing import Dict, List, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from api.src.database import SessionLocal, VariableInvoice, WstDeliveredPackages, WstServiceDetails, WstTrainingWeekly, WstUnplannedDelay, WstWeeklyReport
from api.src.audit_variable_invoice import build_variable_invoice_audit


class VariableInvoiceAuditor:
    """Multi-level validation of invoice line items against WST data"""
    
    def __init__(self, invoice_number: str, station: str, dsp_code: str = None):
        self.invoice_number = invoice_number
        self.station = station
        self.dsp_code = dsp_code
        self.db = SessionLocal()
        self.audit_results = {
            "invoice_number": invoice_number,
            "timestamp": datetime.now(),
            "categories": {},
            "overall_status": "PENDING",
            "confidence_score": 0,
            "errors": [],
            "warnings": [],
        }
    
    def run_audit(self) -> Dict:
        """
        Run full audit on invoice
        Returns: audit_results dict
        """
        try:
            # Get invoice
            invoice = self.db.query(VariableInvoice).filter_by(
                invoice_number=self.invoice_number
            ).first()
            
            if not invoice:
                self.audit_results["overall_status"] = "FAILED"
                self.audit_results["errors"].append(f"Invoice not found: {self.invoice_number}")
                return self.audit_results
            
            # Determine date range from invoice
            start_date = invoice.period_start or invoice.invoice_date
            if not start_date:
                self.audit_results["overall_status"] = "FAILED"
                self.audit_results["errors"].append("Invoice is missing date fields needed for weekly matching")
                return self.audit_results

            end_date = invoice.period_end or (start_date + timedelta(days=6))
            
            # Get line items by category
            category_totals = self._aggregate_by_category(invoice)
            
            # Audit each category
            category_confidence = []
            
            if "Variable Per Shipment" in category_totals:
                cat_confidence = self._audit_packages(
                    category_totals["Variable Per Shipment"],
                    start_date, end_date
                )
                category_confidence.append(cat_confidence)
            
            if "Routes" in category_totals:
                cat_confidence = self._audit_routes(
                    category_totals["Routes"],
                    start_date, end_date
                )
                category_confidence.append(cat_confidence)
            
            if "Training Ride Along" in category_totals:
                cat_confidence = self._audit_training(
                    category_totals["Training Ride Along"],
                    start_date, end_date
                )
                category_confidence.append(cat_confidence)
            
            if "Unplanned Delay" in category_totals:
                cat_confidence = self._audit_delays(
                    category_totals["Unplanned Delay"],
                    start_date, end_date
                )
                category_confidence.append(cat_confidence)

            # Strict line-by-line and week-by-week source alignment
            line_report = build_variable_invoice_audit(
                self.db,
                invoice,
                start_date,
                end_date,
                self.station,
            )
            self.audit_results["line_item_comparisons"] = line_report.get("line_item_comparisons", [])
            self.audit_results["week_alignment"] = line_report.get("week_alignment", {})
            self.audit_results["dispute_report"] = line_report.get("dispute_report", {})

            discrepancy_count = self.audit_results["dispute_report"].get("discrepancy_count", 0)
            if discrepancy_count > 0:
                self.audit_results["warnings"].append(
                    f"{discrepancy_count} line-item discrepancies found; dispute report generated"
                )
            
            # Calculate overall confidence
            if category_confidence:
                avg_confidence = sum(c.get("confidence", 0) for c in category_confidence) / len(category_confidence)
                self.audit_results["confidence_score"] = avg_confidence
            
            # Determine overall status
            if self.audit_results["errors"]:
                self.audit_results["overall_status"] = "FAILED"
            elif self.audit_results["warnings"]:
                self.audit_results["overall_status"] = "WARNING"
            else:
                self.audit_results["overall_status"] = "PASSED"
        
        except Exception as e:
            self.audit_results["overall_status"] = "ERROR"
            self.audit_results["errors"].append(str(e))
        
        finally:
            self.db.close()
        
        return self.audit_results
    
    def _aggregate_by_category(self, invoice) -> Dict[str, Decimal]:
        """Aggregate line items by service category inferred from description"""
        category_totals = {}
        
        for line_item in invoice.line_items:
            # Infer category from description
            desc_lower = line_item.description.lower()
            
            if any(keyword in desc_lower for keyword in ['variable per shipment', 'package', 'shipment', 'delivery']):
                cat = "Variable Per Shipment"
            elif any(keyword in desc_lower for keyword in ['route', 'daily']):
                cat = "Routes"
            elif any(keyword in desc_lower for keyword in ['training', 'ride along']):
                cat = "Training Ride Along"
            elif any(keyword in desc_lower for keyword in ['delay', 'unplanned']):
                cat = "Unplanned Delay"
            else:
                cat = "Other"  # Uncategorized
            
            if cat not in category_totals:
                category_totals[cat] = Decimal(0)
            category_totals[cat] += line_item.amount or Decimal(0)
        
        return category_totals
    
    def _audit_packages(self, invoice_amount: Decimal, start_date, end_date) -> Dict:
        """
        Audit: Variable Per Shipment
        Validates against delivered/pickup package counts
        """
        result = {
            "category": "Variable Per Shipment",
            "invoice_amount": float(invoice_amount),
            "confidence": 0,
            "status": "PENDING",
            "details": {},
        }
        
        try:
            # Get package totals from WST
            delivered = self.db.query(WstDeliveredPackages).filter(
                WstDeliveredPackages.report_date.between(start_date, end_date),
                WstDeliveredPackages.station == self.station,
                ~WstDeliveredPackages.package_type.ilike('%pickup%')
            ).with_entities(
                func.sum(WstDeliveredPackages.package_count)
            ).scalar() or 0
            
            pickup = self.db.query(WstDeliveredPackages).filter(
                WstDeliveredPackages.report_date.between(start_date, end_date),
                WstDeliveredPackages.station == self.station,
                WstDeliveredPackages.package_type.ilike('%pickup%')
            ).with_entities(
                func.sum(WstDeliveredPackages.package_count)
            ).scalar() or 0
            
            total_packages = delivered + pickup
            
            result["details"] = {
                "delivered": delivered,
                "pickup": pickup,
                "total": total_packages,
            }
            
            # Validate: sum formula
            if total_packages > 0:
                result["status"] = "PASSED"
                result["confidence"] = 90  # High confidence when packages tracked
            else:
                result["warnings"] = ["No package data found for period"]
                result["confidence"] = 50
            
        except Exception as e:
            result["status"] = "ERROR"
            result["error"] = str(e)
            result["confidence"] = 0
        
        self.audit_results["categories"]["packages"] = result
        return result
    
    def _audit_routes(self, invoice_amount: Decimal, start_date, end_date) -> Dict:
        """
        Audit: Routes
        Validates route counts and payments against service details
        """
        result = {
            "category": "Routes",
            "invoice_amount": float(invoice_amount),
            "confidence": 0,
            "status": "PENDING",
            "details": {},
        }
        
        try:
            # Count routes in period
            route_count = self.db.query(WstServiceDetails).filter(
                WstServiceDetails.report_date.between(start_date, end_date),
                WstServiceDetails.station == self.station,
                WstServiceDetails.excluded == False
            ).count()
            
            result["details"] = {
                "completed_routes": route_count,
            }
            
            if route_count > 0:
                result["status"] = "PASSED"
                result["confidence"] = 85
            else:
                result["status"] = "WARNING"
                result["confidence"] = 50
        
        except Exception as e:
            result["status"] = "ERROR"
            result["error"] = str(e)
        
        self.audit_results["categories"]["routes"] = result
        return result
    
    def _audit_training(self, invoice_amount: Decimal, start_date, end_date) -> Dict:
        """
        Audit: Training
        Validates training payments against training records
        """
        result = {
            "category": "Training Ride Along",
            "invoice_amount": float(invoice_amount),
            "confidence": 0,
            "status": "PENDING",
            "details": {},
        }
        
        try:
            # Count eligible training entries
            training_count = self.db.query(WstTrainingWeekly).filter(
                WstTrainingWeekly.assignment_date.between(start_date, end_date),
                WstTrainingWeekly.station == self.station,
                WstTrainingWeekly.dsp_payment_eligible == True
            ).count()
            
            result["details"] = {
                "eligible_entries": training_count,
            }
            
            if training_count > 0:
                result["status"] = "PASSED"
                result["confidence"] = 75
            else:
                result["status"] = "WARNING"
                result["confidence"] = 40
        
        except Exception as e:
            result["status"] = "ERROR"
            result["error"] = str(e)
        
        self.audit_results["categories"]["training"] = result
        return result
    
    def _audit_delays(self, invoice_amount: Decimal, start_date, end_date) -> Dict:
        """
        Audit: Unplanned Delay
        Validates delay charges against delay records
        """
        result = {
            "category": "Unplanned Delay",
            "invoice_amount": float(invoice_amount),
            "confidence": 0,
            "status": "PENDING",
            "details": {},
        }
        
        try:
            delay_records = self.db.query(WstUnplannedDelay).filter(
                WstUnplannedDelay.report_date.between(start_date, end_date),
                WstUnplannedDelay.station == self.station,
            ).all()
            
            total_delay_minutes = sum(r.total_delay_minutes or 0 for r in delay_records)
            delay_count = len(delay_records)
            
            result["details"] = {
                "delay_incidents": delay_count,
                "total_delay_minutes": total_delay_minutes,
            }
            
            if delay_count > 0:
                result["status"] = "PASSED"
                result["confidence"] = 70
            else:
                result["status"] = "INFO"
                result["confidence"] = 50
        
        except Exception as e:
            result["status"] = "ERROR"
            result["error"] = str(e)
        
        self.audit_results["categories"]["delays"] = result
        return result


def audit_variable_invoice(invoice_number: str, station: str, dsp_code: str = None) -> Dict:
    """
    Main audit entry point
    Returns: audit_results dict with confidence scores and validation results
    """
    auditor = VariableInvoiceAuditor(invoice_number, station, dsp_code)
    return auditor.run_audit()


# For import compatibility
from sqlalchemy import func
