"""Ingest validation and orchestration."""
import os
from typing import Optional, Dict
from api.src.models import IngestStatus, RouteDOP, Vehicle, RouteSheet, CortexRoute, DriverScheduleSummary
from api.src.ingest_dop import parse_dop_excel
from api.src.ingest_fleet import parse_fleet_excel
from api.src.ingest_cortex import parse_cortex_excel
from api.src.ingest_route_sheets import parse_route_sheet_pdf
from api.src.ingest_driver_schedule import parse_driver_schedule_excel
from api.src.normalization import normalize_route_code, normalize_service_type
from api.src.assignment import VehicleAssignmentEngine
from api.src.pdf_generator import DriverHandoutGenerator
from api.src.driver_schedule_report import DriverScheduleReportGenerator


class IngestOrchestrator:
    """Orchestrates ingest, validation, and status tracking."""
    
    def __init__(self):
        self.status = IngestStatus()
        # Use absolute path or create in a temp location if not available
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.upload_dir = os.path.join(base_dir, 'uploads')
        os.makedirs(self.upload_dir, exist_ok=True)
        self.assignment_engine: Optional[VehicleAssignmentEngine] = None
        self.assignments: Dict = {}
        self.pdf_generator = DriverHandoutGenerator()
        self.schedule_report_generator = DriverScheduleReportGenerator()
    
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
        
        # Enrich route sheets with expected return times
        self._enrich_route_sheets_with_expected_return()
        
        return self.status.route_sheets_uploaded
    
    def ingest_driver_schedule(self, file_path: str) -> bool:
        """Ingest driver schedule Excel file (Rostered Work Blocks and Shifts & Availability)."""
        schedule, errors = parse_driver_schedule_excel(file_path)
        self.status.driver_schedule = schedule
        self.status.validation_errors.extend(errors)
        self.status.driver_schedule_uploaded = True
        return True
    
    def generate_driver_schedule_report(self) -> bool:
        """Generate PDF report for current driver schedule."""
        if not self.status.driver_schedule:
            self.status.validation_errors.append("No driver schedule data available for report generation")
            return False
        
        output_path = os.path.join(self.upload_dir, "driver_schedule_report.pdf")
        try:
            self.schedule_report_generator.generate_schedule_report(
                self.status.driver_schedule,
                output_path
            )
            self.status.driver_schedule_report_path = output_path
            return True
        except Exception as e:
            self.status.validation_errors.append(f"Failed to generate report: {str(e)}")
            return False
    
    def _enrich_route_sheets_with_expected_return(self):
        """Calculate expected return times for route sheets."""
        if not self.status.dop_records or not self.status.route_sheets:
            return
        
        # Build DOP lookup by route code
        dop_lookup = {r.route_code: r for r in self.status.dop_records}
        
        # Calculate expected return for each route sheet
        from api.src.pdf_generator import DriverHandoutGenerator
        gen = DriverHandoutGenerator()
        
        for route_sheet in self.status.route_sheets:
            dop = dop_lookup.get(route_sheet.route_code)
            if dop:
                # Calculate expected return: wave_time + route_duration - 30 min
                route_sheet.expected_return = gen._calculate_expected_return(
                    route_sheet.wave_time,
                    dop.route_duration
                )
    
    def validate_cross_file_consistency(self) -> bool:
        """Validate DOP, Fleet, Cortex, and Route Sheets consistency."""
        # Enrich route sheets with expected return times if both DOP and route sheets are loaded
        if self.status.dop_records and self.status.route_sheets:
            self._enrich_route_sheets_with_expected_return()
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
        
        # Cortex is used for driver enrichment only - service type matching is not needed
        # (Assignment logic uses DOP service type + Fleet match)
        
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
            Dictionary with assignment status including failed routes requiring manual assignment
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
        
        # Build failed routes with details for manual assignment
        failed_routes_detail = []
        if assignment_status["failed"] > 0:
            # Build lookup dicts
            dop_lookup = {route.route_code: route for route in self.status.dop_records}
            cortex_lookup = {}
            if self.status.cortex_records:
                cortex_lookup = {record.route_code: record for record in self.status.cortex_records}
            
            for failed_route_code in assignment_status["failed_routes"]:
                dop_record = dop_lookup.get(failed_route_code)
                cortex_record = cortex_lookup.get(failed_route_code)
                
                if dop_record:
                    # Get available vehicles for this service type and fallbacks
                    available_options = self._get_available_vehicles_for_route(dop_record.service_type)
                    
                    failed_routes_detail.append({
                        "route_code": failed_route_code,
                        "service_type": dop_record.service_type,
                        "driver_name": cortex_record.driver_name if cortex_record else None,
                        "wave_time": dop_record.wave,
                        "available_vehicles": available_options,
                    })
        
        return {
            "success": assignment_status["failed"] == 0,  # Only fully successful if no failures
            "total_routes": assignment_status["total_routes"],
            "assigned": assignment_status["assigned"],
            "failed": assignment_status["failed"],
            "fallback_used": assignment_status["fallback_used"],
            "success_rate": assignment_status["success_rate"],
            "failed_routes": assignment_status["failed_routes"],
            "failed_routes_detail": failed_routes_detail,  # Detail for manual assignment UI
            "message": f"{assignment_status['assigned']}/{assignment_status['total_routes']} routes assigned. {assignment_status['failed']} routes require manual vehicle selection." if assignment_status["failed"] > 0 else "All routes assigned successfully.",
        }
    
    def _get_available_vehicles_for_route(self, service_type: str) -> list:
        """Get all operational vehicles available for manual assignment."""
        available = []

        if not self.assignment_engine:
            return available

        # Get already-assigned VINs so we can exclude them
        assigned_vins = {
            assignment.vehicle_vin
            for assignment in self.assignment_engine.assignments.values()
        }

        # Manual assignment uses ALL fleet vehicles (not just vehicle_pool which may be depleted)
        # Show all non-grounded vehicles regardless of service type
        for vehicle in self.status.fleet_records:
            status_norm = (vehicle.operational_status or "").strip().upper()
            if status_norm == "GROUNDED":
                continue
            if vehicle.vin in assigned_vins:
                continue
            available.append({
                "vehicle_name": vehicle.vehicle_name,
                "vin": vehicle.vin,
                "service_type": vehicle.service_type,
            })

        return available
    
    def manual_assign_vehicle(self, route_code: str, vehicle_vin: str) -> Dict:
        """
        Manually assign a vehicle to a failed route.
        
        Args:
            route_code: The route to assign
            vehicle_vin: The VIN of the vehicle to assign
        
        Returns:
            Dictionary with assignment result
        """
        if not self.assignment_engine:
            return {
                "success": False,
                "message": "No assignments made yet. Run assign_vehicles first.",
            }
        
        # Find the vehicle in the pool
        vehicle_to_assign = None
        assigned_from_pool = None
        
        for pool_type, vehicles in self.assignment_engine.vehicle_pool.items():
            for idx, vehicle in enumerate(vehicles):
                if vehicle.vin == vehicle_vin:
                    vehicle_to_assign = vehicle
                    assigned_from_pool = (pool_type, idx)
                    break
            if vehicle_to_assign:
                break
        
        if not vehicle_to_assign:
            return {
                "success": False,
                "message": f"Vehicle with VIN {vehicle_vin} not found in available pool.",
            }
        
        # Get route and driver info
        dop_lookup = {route.route_code: route for route in self.status.dop_records}
        cortex_lookup = {}
        if self.status.cortex_records:
            cortex_lookup = {record.route_code: record for record in self.status.cortex_records}
        
        dop_record = dop_lookup.get(route_code)
        cortex_record = cortex_lookup.get(route_code)
        
        if not dop_record:
            return {
                "success": False,
                "message": f"Route {route_code} not found in DOP records.",
            }
        
        # Create assignment
        from api.src.assignment import RouteAssignment
        assignment = RouteAssignment(
            route_code=route_code,
            vehicle_vin=vehicle_to_assign.vin,
            vehicle_name=vehicle_to_assign.vehicle_name,
            service_type=vehicle_to_assign.service_type,
            driver_name=cortex_record.driver_name if cortex_record else None,
            driver_id=cortex_record.transporter_id if cortex_record else None,
            dsp=cortex_record.dsp if cortex_record else None,
            wave_time=dop_record.wave,
            route_duration=dop_record.route_duration,
        )
        
        # Add to assignments and remove from pool
        self.assignments[route_code] = assignment
        if assigned_from_pool:
            pool_type, idx = assigned_from_pool
            self.assignment_engine.vehicle_pool[pool_type].pop(idx)
        
        return {
            "success": True,
            "message": f"Route {route_code} assigned to {vehicle_to_assign.vehicle_name}",
            "route_code": route_code,
            "vehicle_name": vehicle_to_assign.vehicle_name,
        }
    
    
    def get_capacity_status(self) -> Dict:
        """
        Get van capacity utilization and alerts.
        
        Returns:
            Dictionary with capacity status by service type and alerts for types at 80%+
        """
        if not self.assignment_engine:
            return {
                "error": "No assignments made yet. Run assign_vehicles first.",
                "by_service_type": {},
                "alerts": [],
                "has_alerts": False,
                "alert_count": 0,
            }
        
        return self.assignment_engine.get_capacity_status()
    
    def authorize_electric_van_assignment(self, route_code: str, van_vin: str, reason: str = "") -> Dict:
        """
        Authorize using an electric van on a non-electric route.
        
        Args:
            route_code: The route code to authorize
            van_vin: The vehicle VIN (for audit purposes)
            reason: Optional reason for the authorization
        
        Returns:
            Dictionary with authorization status
        """
        if not self.assignment_engine:
            return {
                "success": False,
                "message": "No assignments made yet. Run assign_vehicles first.",
            }
        
        # Add to authorized set
        self.assignment_engine.authorized_electric_assignments.add(route_code)
        
        # Get updated violations
        assignment_status = self.assignment_engine.get_assignment_status()
        
        return {
            "success": True,
            "message": f"Electric van authorization approved for route {route_code}",
            "route_code": route_code,
            "van_vin": van_vin,
            "reason": reason,
            "remaining_violations": assignment_status.get("electric_van_violations", []),
            "violation_count": assignment_status.get("electric_violation_count", 0),
        }
    
    def get_electric_van_violations(self) -> Dict:
        """
        Get all electric van constraint violations.
        
        Returns:
            Dictionary with violations and authorization status
        """
        if not self.assignment_engine:
            return {
                "error": "No assignments made yet. Run assign_vehicles first.",
                "violations": [],
                "pending_violations": [],
                "authorized_routes": [],
                "total_violations": 0,
                "pending_count": 0,
            }
        
        assignment_status = self.assignment_engine.get_assignment_status()
        all_violations = assignment_status.get("electric_van_violations", [])
        authorized = self.assignment_engine.authorized_electric_assignments
        
        # Split violations into pending and authorized
        pending = [v for v in all_violations if v["route_code"] not in authorized]
        authorized_violations = [v for v in all_violations if v["route_code"] in authorized]
        
        return {
            "violations": all_violations,
            "pending_violations": pending,
            "authorized_violations": authorized_violations,
            "authorized_routes": list(authorized),
            "total_violations": len(all_violations),
            "pending_count": len(pending),
            "has_pending": len(pending) > 0,
        }
    
    def generate_handouts(self, output_path: str) -> Dict:
        """
        Generate driver handout PDF with 2x2 card layout.
        
        BLOCKS generation if any routes are unassigned. User must manually assign
        vehicles for all failed routes via /manual-assign-vehicle before PDF generation.
        
        Args:
            output_path: Path to save PDF
        
        Returns:
            Dictionary with generation status or error if unassigned routes exist
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
        
        # Check for unassigned routes - BLOCK generation if any exist
        total_routes = len(self.status.dop_records) if self.status.dop_records else 0
        assigned_count = len(self.assignments)
        
        if assigned_count < total_routes:
            unassigned_count = total_routes - assigned_count
            return {
                "success": False,
                "message": f"Cannot generate handouts: {unassigned_count} route(s) still unassigned. Use /manual-assign-vehicle to assign all routes before proceeding.",
                "blocked_reason": "unassigned_routes",
                "total_routes": total_routes,
                "assigned": assigned_count,
                "unassigned": unassigned_count,
            }
        
        # All routes are assigned - proceed with PDF generation
        # Ensure expected return times are calculated
        if self.status.dop_records:
            self._enrich_route_sheets_with_expected_return()
        
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
