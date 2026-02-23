from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from typing import List
import os
import tempfile
from api.src.orchestrator import orchestrator

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '../../uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/dop")
def upload_dop(file: UploadFile = File(...)):
    """Upload DOP Excel or CSV file and parse."""
    try:
        file_path = os.path.join(UPLOAD_DIR, f"dop_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())
        
        # Parse and validate
        orchestrator.ingest_dop(file_path)
        
        return {
            "filename": file.filename,
            "status": "uploaded",
            "records_parsed": len(orchestrator.status.dop_records),
            "errors": orchestrator.status.validation_errors[-5:],  # Last 5 errors
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload DOP: {str(e)}")


@router.post("/fleet")
def upload_fleet(file: UploadFile = File(...)):
    """Upload Fleet Excel or CSV file and parse."""
    try:
        file_path = os.path.join(UPLOAD_DIR, f"fleet_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())
        
        # Parse and validate
        orchestrator.ingest_fleet(file_path)
        
        return {
            "filename": file.filename,
            "status": "uploaded",
            "records_parsed": len(orchestrator.status.fleet_records),
            "errors": orchestrator.status.validation_errors[-5:],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Fleet: {str(e)}")


@router.post("/cortex")
def upload_cortex(file: UploadFile = File(...)):
    """Upload Cortex Excel or CSV file and parse."""
    try:
        file_path = os.path.join(UPLOAD_DIR, f"cortex_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())
        
        # Parse and validate
        orchestrator.ingest_cortex(file_path)
        
        return {
            "filename": file.filename,
            "status": "uploaded",
            "records_parsed": len(orchestrator.status.cortex_records),
            "errors": orchestrator.status.validation_errors[-5:],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Cortex: {str(e)}")


@router.post("/route-sheets")
def upload_route_sheets(files: List[UploadFile] = File(...)):
    """Upload one or more Route Sheet PDFs."""
    try:
        file_paths = []
        for file in files:
            file_path = os.path.join(UPLOAD_DIR, f"route_sheet_{file.filename}")
            with open(file_path, "wb") as f:
                f.write(file.file.read())
            file_paths.append(file_path)
        
        # Parse and validate
        orchestrator.ingest_route_sheets(file_paths)
        
        return {
            "filenames": [f.filename for f in files],
            "status": "uploaded",
            "records_parsed": len(orchestrator.status.route_sheets),
            "errors": orchestrator.status.validation_errors[-5:],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Route Sheets: {str(e)}")


@router.post("/driver-schedule")
def upload_driver_schedule(file: UploadFile = File(...)):
    """Upload Driver Schedule Excel file (Rostered Work Blocks and Shifts & Availability tabs)."""
    try:
        file_path = os.path.join(UPLOAD_DIR, f"driver_schedule_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())
        
        # Parse and validate
        orchestrator.ingest_driver_schedule(file_path)
        
        # Generate report
        report_generated = orchestrator.generate_driver_schedule_report()
        
        schedule = orchestrator.status.driver_schedule
        return {
            "filename": file.filename,
            "status": "uploaded",
            "timestamp": schedule.timestamp if schedule else "",
            "scheduled_date": schedule.date if schedule else "",
            "assignments_count": len(schedule.assignments) if schedule else 0,
            "sweepers_count": len(schedule.sweepers) if schedule else 0,
            "report_generated": report_generated,
            "report_path": orchestrator.status.driver_schedule_report_path,
            "errors": orchestrator.status.validation_errors[-5:],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Driver Schedule: {str(e)}")


@router.get("/driver-schedule-summary")
def get_driver_schedule_summary():
    """Get the current driver schedule summary with show times and sweepers."""
    try:
        if not orchestrator.status.driver_schedule:
            return {"error": "No driver schedule uploaded"}
        
        schedule = orchestrator.status.driver_schedule
        
        # Format assignments
        assignments = []
        for assignment in schedule.assignments:
            assignments.append({
                "driver_name": assignment.driver_name,
                "date": assignment.date,
                "wave_time": assignment.wave_time or "",
                "service_type": assignment.service_type or "",
                "show_time": assignment.show_time or "",
            })
        
        return {
            "timestamp": schedule.timestamp,
            "scheduled_date": schedule.date,
            "assignments": assignments,
            "sweepers": schedule.sweepers,
            "show_times": schedule.show_times,
            "summary": {
                "total_assigned": len(schedule.assignments),
                "total_sweepers": len(schedule.sweepers),
                "total_drivers": len(schedule.assignments) + len(schedule.sweepers),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve driver schedule: {str(e)}")


@router.get("/download-schedule-report")
def download_schedule_report():
    """Download generated driver schedule report PDF."""
    pdf_path = orchestrator.status.driver_schedule_report_path
    
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Schedule report PDF not found. Upload and process a driver schedule first.")
    
    return FileResponse(
        path=pdf_path,
        filename="NDAY_Driver_Schedule_Report.pdf",
        media_type="application/pdf",
    )

@router.get("/status")
def get_upload_status():
    """Get current ingest status and validation results."""
    orchestrator.validate_cross_file_consistency()
    return orchestrator.get_status()


@router.post("/assign-vehicles")
def assign_vehicles():
    """Assign fleet vehicles to routes based on service type."""
    try:
        result = orchestrator.assign_vehicles()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign vehicles: {str(e)}")


@router.post("/manual-assign-vehicle")
def manual_assign_vehicle(route_code: str, vehicle_vin: str):
    """
    Manually assign a vehicle to a route that failed automatic assignment.
    
    Args:
        route_code: The route code to assign
        vehicle_vin: The VIN of the vehicle to assign
    
    Returns:
        Result of manual assignment
    """
    try:
        result = orchestrator.manual_assign_vehicle(route_code, vehicle_vin)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to manually assign vehicle: {str(e)}")


@router.get("/capacity-status")
def get_capacity_status():
    """Get van capacity utilization and alerts for service types at 80%+ capacity."""
    try:
        capacity_status = orchestrator.get_capacity_status()
        return capacity_status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get capacity status: {str(e)}")


@router.get("/electric-van-violations")
def get_electric_van_violations():
    """Get electric van constraint violations that need user approval."""
    try:
        violations = orchestrator.get_electric_van_violations()
        return violations
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve violations: {str(e)}")


@router.post("/authorize-electric-van")
def authorize_electric_van(route_code: str, van_vin: str, reason: str = ""):
    """Authorize using an electric van on a non-electric route."""
    try:
        result = orchestrator.authorize_electric_van_assignment(route_code, van_vin, reason)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to authorize electric van: {str(e)}")


@router.post("/generate-handouts")
def generate_handouts():
    """Generate driver handout PDF with 2x2 card layout."""
    try:
        output_path = os.path.join(UPLOAD_DIR, "driver_handouts.pdf")
        result = orchestrator.generate_handouts(output_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate handouts: {str(e)}")


@router.get("/download-handouts")
def download_handouts():
    """Download generated driver handout PDF."""
    pdf_path = os.path.join(UPLOAD_DIR, "driver_handouts.pdf")
    
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="Handouts PDF not found. Generate handouts first.")
    
    return FileResponse(
        path=pdf_path,
        filename="NDAY_Driver_Handouts.pdf",
        media_type="application/pdf",
    )


@router.get("/assignments")
def get_assignments():
    """Get all current assignments for database view."""
    try:
        assignments_list = []
        for route_code, assignment in sorted(orchestrator.assignments.items()):
            assignments_list.append({
                "id": route_code,
                "route_code": route_code,
                "driver_name": assignment.driver_name or "N/A",
                "vehicle_name": assignment.vehicle_name or "N/A",
                "wave_time": assignment.wave_time or "N/A",
                "service_type": assignment.service_type or "N/A",
                "dsp": assignment.dsp or "N/A",
                "assignment_date": assignment.assignment_date.isoformat() if hasattr(assignment, 'assignment_date') and assignment.assignment_date else "",
            })
        
        return {"assignments": assignments_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve assignments: {str(e)}")


@router.get("/affinity-stats")
def get_affinity_stats():
    """Get driver-van affinity statistics."""
    try:
        from api.src.driver_van_affinity import affinity_tracker
        
        # Build comprehensive affinity view
        affinity_summary = {}
        driver_names = set()
        
        for affinity_key, affinities_list in affinity_tracker.affinities.items():
            driver_name, service_type = affinity_key.split('|', 1)
            driver_names.add(driver_name)
            
            if driver_name not in affinity_summary:
                affinity_summary[driver_name] = []
            
            for affinity in affinities_list:
                affinity_summary[driver_name].append({
                    'vehicle_name': affinity['vehicle_name'],
                    'service_type': service_type,
                    'frequency': affinity['frequency'],
                    'last_used': affinity['last_used'],
                    'routes_assigned': len(affinity['routes']),
                })
        
        return {
            "total_drivers": len(driver_names),
            "total_affinities": sum(len(v) for v in affinity_summary.values()),
            "drivers": affinity_summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve affinity stats: {str(e)}")
