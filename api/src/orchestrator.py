"""Ingest validation and orchestration."""
import os
from typing import Optional, Dict
from api.src.models import IngestStatus, RouteDOP, Vehicle, RouteSheet, CortexRoute
from api.src.ingest_dop import parse_dop_excel
from api.src.ingest_fleet import parse_fleet_excel
from api.src.ingest_cortex import parse_cortex_excel
from api.src.ingest_route_sheets import parse_route_sheet_pdf
from api.src.normalization import normalize_route_code, normalize_service_type
from api.src.assignment import VehicleAssignmentEngine
from api.src.pdf_generator import DriverHandoutGenerator


class IngestOrchestrator:
    """Orchestrates ingest, validation, and status tracking."""
    
    def __init__(self):
        self.status = IngestStatus()
        self.upload_dir = os.path.join(os.path.dirname(__file__), '../../uploads')
        os.makedirs(self.upload_dir, exist_ok=True)
        self.assignment_engine: Optional[VehicleAssignmentEngine] = None
        self.assignments: Dict = {}
        self.pdf_generator = DriverHandoutGenerator()
    
    def ingest_dop(self, file_path: str) -> bool:
        """Ingest DOP Excel file."""
        records, errors = parse_dop_excel(file_path)
        self.status.dop_records = records
        self.status.validation_errors.extend(errors)
        self.status.dop_uploaded = len(records) > 0
        return self.status.dop_uploaded
    
    def ingest_fleet(self, file_path: str) -> bool:
        """Ingest Fleet Excel file."""
        records, errors = parse_fleet_excel(file_path)
        self.status.fleet_records = records
        self.status.validation_errors.extend(errors)
        self.status.fleet_uploaded = len(records) > 0
        return self.status.fleet_uploaded
    
    def ingest_cortex(self, file_path: str) -> bool:
        """Ingest Cortex Excel file."""
        records, errors = parse_cortex_excel(file_path)
        self.status.cortex_records = records
        self.status.validation_errors.extend(errors)
        self.status.cortex_uploaded = len(records) > 0
        return self.status.cortex_uploaded
    
    def ingest_route_sheets(self, file_paths: list) -> bool:
        """Ingest one or more Route Sheet PDFs."""
        all_records = []
        all_errors = []
        
        for file_path in file_paths:
            records, errors = parse_route_sheet_pdf(file_path)
            all_records.extend(records)
            all_errors.extend(errors)
        
        self.status.route_sheets = all_records
        self.status.validation_errors.extend(all_errors)
        self.status.route_sheets_uploaded = len(all_records) > 0
        return self.status.route_sheets_uploaded
    
    def validate_cross_file_consistency(self) -> bool:
        """Validate DOP, Fleet, Cortex, and Route Sheets consistency."""
        # Clear previous cross-file validation messages (but keep parser errors)
        # Store original parser errors
        original_errors = [e for e in self.status.validation_errors if not any(keyword in e for keyword in ['Routes in', 'Service type', 'Route '])]
        self.status.validation_errors = original_errors
        self.status.validation_warnings = []
        
        if not self.status.dop_uploaded:
            self.status.validation_errors.append("DOP file not uploaded.")
            return False
        
        if not self.status.fleet_uploaded:
            self.status.validation_warnings.append("Fleet file not uploaded yet.")
        
        if not self.status.cortex_uploaded:
            self.status.validation_warnings.append("Cortex file not uploaded yet.")
        
        if not self.status.route_sheets_uploaded:
            self.status.validation_warnings.append("Route Sheets not uploaded yet.")
        
        # Cross-check route codes in DOP vs Route Sheets
        if self.status.dop_records and self.status.route_sheets:
            dop_route_codes = {r.route_code for r in self.status.dop_records}
            sheet_route_codes = {r.route_code for r in self.status.route_sheets}
            
            missing_in_sheets = dop_route_codes - sheet_route_codes
            missing_in_dop = sheet_route_codes - dop_route_codes
            
            if missing_in_sheets:
                self.status.validation_warnings.append(
                    f"Routes in DOP but not in Route Sheets: {', '.join(missing_in_sheets)}"
                )
            
            if missing_in_dop:
                self.status.validation_warnings.append(
                    f"Routes in Route Sheets but not in DOP: {', '.join(missing_in_dop)}"
                )
        
        # Cross-check service types
        if self.status.dop_records and self.status.fleet_records:
            fleet_service_types = {v.service_type for v in self.status.fleet_records}
            
            for dop_record in self.status.dop_records:
                if dop_record.service_type not in fleet_service_types:
                    self.status.validation_warnings.append(
                        f"Route {dop_record.route_code}: Service type '{dop_record.service_type}' not available in Fleet."
                    )
        
        # Cross-check Cortex routes against DOP
        if self.status.cortex_records and self.status.dop_records:
            dop_route_codes = {r.route_code for r in self.status.dop_records}
            cortex_route_codes = {r.route_code for r in self.status.cortex_records}
            
            cortex_not_in_dop = cortex_route_codes - dop_route_codes
            if cortex_not_in_dop:
                self.status.validation_warnings.append(
                    f"Routes in Cortex but not in DOP: {', '.join(cortex_not_in_dop)}"
                )
        
        # Cross-check Cortex service types against DOP
        if self.status.cortex_records and self.status.dop_records:
            dop_routes = {r.route_code: r for r in self.status.dop_records}
            
            for cortex_record in self.status.cortex_records:
                if cortex_record.route_code in dop_routes:
                    dop_route = dop_routes[cortex_record.route_code]
                    if cortex_record.delivery_service_type != dop_route.service_type:
                        self.status.validation_warnings.append(
                            f"Route {cortex_record.route_code}: Cortex service type '{cortex_record.delivery_service_type}' "
                            f"does not match DOP '{dop_route.service_type}'."
                        )
        
        return True
    
    def get_status(self) -> dict:
        """Return current ingest status as dict."""
        return {
            "dop_uploaded": self.status.dop_uploaded,
            "fleet_uploaded": self.status.fleet_uploaded,
            "cortex_uploaded": self.status.cortex_uploaded,
            "route_sheets_uploaded": self.status.route_sheets_uploaded,
            "dop_record_count": len(self.status.dop_records),
            "fleet_record_count": len(self.status.fleet_records),
            "cortex_record_count": len(self.status.cortex_records),
            "route_sheets_count": len(self.status.route_sheets),
            "assignments_count": len(self.assignments),
            "validation_errors": self.status.validation_errors,
            "validation_warnings": self.status.validation_warnings,
            "last_updated": self.status.last_updated.isoformat(),
        }
    
    def assign_vehicles(self) -> Dict:
        """
        Assign fleet vehicles to routes based on service type.
        
        Returns:
            Dictionary with assignment status
        """
        if not self.status.dop_records or not self.status.fleet_records:
            return {
                "success": False,
                "message": "DOP and Fleet files must be uploaded first.",
            }
        
        # Create assignment engine
        self.assignment_engine = VehicleAssignmentEngine(self.status.fleet_records)
        
        # Perform assignments
        self.assignments = self.assignment_engine.assign_routes(
            self.status.dop_records,
            self.status.cortex_records if self.status.cortex_records else None,
        )
        
        # Get assignment status
        assignment_status = self.assignment_engine.get_assignment_status()
        
        return {
            "success": True,
            "total_routes": assignment_status["total_routes"],
            "assigned": assignment_status["assigned"],
            "failed": assignment_status["failed"],
            "fallback_used": assignment_status["fallback_used"],
            "success_rate": assignment_status["success_rate"],
            "failed_routes": assignment_status["failed_routes"],
        }
    
    def generate_handouts(self, output_path: str) -> Dict:
        """
        Generate driver handout PDF with 2x2 card layout.
        
        Args:
            output_path: Path to save PDF
        
        Returns:
            Dictionary with generation status
        """
        if not self.assignments:
            return {
                "success": False,
                "message": "Vehicle assignments must be completed first. Run assign_vehicles().",
            }
        
        if not self.status.route_sheets:
            return {
                "success": False,
                "message": "Route Sheets must be uploaded first.",
            }
        
        try:
            pdf_path = self.pdf_generator.generate_handouts(
                self.assignments,
                self.status.route_sheets,
                output_path,
            )
            
            return {
                "success": True,
                "message": "Driver handouts generated successfully.",
                "output_path": pdf_path,
                "cards_generated": len(self.assignments),
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to generate handouts: {str(e)}",
            }
    
    def reset(self):
        """Reset status for new ingest cycle."""
        self.status = IngestStatus()
        self.assignments = {}
        self.assignment_engine = None


# Global orchestrator instance
orchestrator = IngestOrchestrator()
