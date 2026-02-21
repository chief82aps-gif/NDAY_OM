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
    """Upload DOP Excel file and parse."""
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
    """Upload Fleet Excel file and parse."""
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
    """Upload Cortex Excel file and parse."""
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
