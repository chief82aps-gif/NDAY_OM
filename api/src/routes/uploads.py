"""
File-upload endpoints retired 2026-07-20 — the website "Upload Center"
(/upload/dop, /cortex, /fleet, /route-sheets, /driver-schedule, /wst-zip,
/variable-invoice, /fleet-invoice, /weekly-incentive, /dsp-scorecard,
/pod-report) was an untracked third writer alongside the Slack-based
ingest pipelines, with no SlackIngestLog-style dedup protection — proven
live to have caused a same-day duplicate-DOP-row incident. Per explicit
decision: all uploads now go through Slack, not the website. What
remains here is admin/diagnostic/assignment-management endpoints only
(DOP debug/backfill/purge, status, vehicle assignment, handouts, etc.) —
none of them accept a file upload.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from datetime import datetime
import os
from api.src.orchestrator import orchestrator
from api.src.database import (
    SessionLocal,
    Vehicle,
    Cortex,
    DOP,
    RouteSheet,
    UploadRetentionRecord,
)

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '../../uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "__dict__"):
        return {str(k): _json_safe(v) for k, v in value.__dict__.items()}
    return str(value)


@router.get("/dop/debug")
def debug_dop_state(date_str: str):
    """Read-only diagnostic: today's DOP rows (source_file, route_code,
    route_duration) plus the most recent upload_retention_records for
    upload_type=dop, so we can tell which ingestion path actually wrote them."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date_str — use YYYY-MM-DD.")

    from api.src.database import SlackIngestLog

    db = SessionLocal()
    try:
        dop_rows = db.query(DOP).filter(DOP.schedule_date == target).all()
        archives = (
            db.query(UploadRetentionRecord)
            .filter(UploadRetentionRecord.upload_type == "dop")
            .order_by(UploadRetentionRecord.uploaded_at.desc())
            .limit(10)
            .all()
        )
        ingest_logs = (
            db.query(SlackIngestLog)
            .filter(SlackIngestLog.file_type == "dop", SlackIngestLog.ingest_date == target)
            .order_by(SlackIngestLog.processed_at.desc())
            .all()
        )
        return {
            "date": date_str,
            "dop_row_count": len(dop_rows),
            "dop_sample": [
                {
                    "route_code": r.route_code,
                    "source_file": r.source_file,
                    "route_duration": r.route_duration,
                    "planned_packages": r.planned_packages,
                }
                for r in dop_rows[:10]
            ],
            "dop_source_files": sorted({r.source_file for r in dop_rows if r.source_file}),
            "recent_dop_archives": [
                {
                    "source_file": a.source_file,
                    "uploaded_at": a.uploaded_at.isoformat() if a.uploaded_at else None,
                    "record_count": a.record_count,
                    "sample_payload_keys": list(a.payload[0].keys()) if a.payload else None,
                }
                for a in archives
            ],
            "dop_ingest_logs": [
                {
                    "filename": log.filename,
                    "status": log.status,
                    "error": log.error,
                    "records_processed": log.records_processed,
                    "processed_at": log.processed_at.isoformat() if log.processed_at else None,
                }
                for log in ingest_logs
            ],
        }
    finally:
        db.close()


@router.get("/cortex/debug")
def debug_cortex_state(date_str: str):
    """Read-only diagnostic mirroring /dop/debug — added 2026-07-20 while
    tracking down driver_name corruption in DailyRouteAssignment traced to
    cortex_by_route (build_daily_assignments() in daily_notify.py)."""
    from api.src.database import SlackIngestLog

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date_str — use YYYY-MM-DD.")

    db = SessionLocal()
    try:
        cortex_rows = db.query(Cortex).filter(Cortex.assignment_date == target).all()
        ingest_logs = (
            db.query(SlackIngestLog)
            .filter(SlackIngestLog.file_type == "cortex", SlackIngestLog.ingest_date == target)
            .order_by(SlackIngestLog.processed_at.desc())
            .all()
        )
        return {
            "date": date_str,
            "cortex_row_count": len(cortex_rows),
            "cortex_sample": [
                {
                    "route_code": r.route_code,
                    "driver_name": r.driver_name,
                    "source_file": r.source_file,
                    "service_type": r.service_type,
                }
                for r in cortex_rows[:15]
            ],
            "cortex_source_files": sorted({r.source_file for r in cortex_rows if r.source_file}),
            "cortex_ingest_logs": [
                {
                    "filename": log.filename,
                    "status": log.status,
                    "error": log.error,
                    "records_processed": log.records_processed,
                    "processed_at": log.processed_at.isoformat() if log.processed_at else None,
                }
                for log in ingest_logs
            ],
        }
    finally:
        db.close()


@router.post("/dop/backfill-duration")
def backfill_dop_duration(date_str: str):
    """
    One-time repair for DOP rows saved before route_duration was copied onto
    the DOP/DailyRouteAssignment tables (see upload_dop). Re-reads the
    archived upload_retention_records payload for each DOP row's source_file
    on the given date and fills in route_duration wherever it's still null.
    """
    from api.src.database import DailyRouteAssignment

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date_str — use YYYY-MM-DD.")

    from api.src.database import get_latest_dop_rows

    db = SessionLocal()
    try:
        dop_rows = get_latest_dop_rows(db, target)
        if not dop_rows:
            return {"date": date_str, "dop_rows": 0, "updated": 0, "detail": "No DOP rows for this date."}

        source_files = {r.source_file for r in dop_rows if r.source_file}
        duration_by_route: dict[str, int] = {}
        for sf in source_files:
            archive = (
                db.query(UploadRetentionRecord)
                .filter(UploadRetentionRecord.upload_type == "dop", UploadRetentionRecord.source_file == sf)
                .order_by(UploadRetentionRecord.uploaded_at.desc())
                .first()
            )
            if not archive or not archive.payload:
                continue
            for rec in archive.payload:
                rc = rec.get("route_code")
                dur = rec.get("route_duration")
                if rc and dur is not None:
                    duration_by_route[rc] = dur

        # Fall back to the DOP rows themselves — covers the case where DOP
        # was fixed/re-ingested after DailyRouteAssignment was built, so no
        # archive is needed at all; the correct value already lives on DOP.
        for row in dop_rows:
            if row.route_code and row.route_duration is not None and row.route_code not in duration_by_route:
                duration_by_route[row.route_code] = row.route_duration

        updated_dop = 0
        for row in dop_rows:
            if row.route_duration is None and row.route_code in duration_by_route:
                row.route_duration = duration_by_route[row.route_code]
                updated_dop += 1

        updated_assignments = 0
        assignments = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date == target).all()
        for a in assignments:
            if a.route_duration is None and a.route_code in duration_by_route:
                a.route_duration = duration_by_route[a.route_code]
                updated_assignments += 1

        db.commit()
        return {
            "date": date_str,
            "dop_rows": len(dop_rows),
            "durations_found": len(duration_by_route),
            "dop_updated": updated_dop,
            "assignments_updated": updated_assignments,
        }
    finally:
        db.close()


@router.post("/dop/purge-old")
def purge_old_dop_cortex(days: int = 90):
    """Delete DOP/Cortex rows older than `days` days (by created_at).

    Ingestion is append-only (see get_latest_dop_rows/get_latest_cortex_rows
    in api.src.database) so historical rows accumulate over time — call this
    periodically to bound table growth. Safe to run anytime: only rows older
    than the cutoff are removed, current-day data is never touched.
    """
    from api.src.database import purge_old_dop_cortex_rows

    db = SessionLocal()
    try:
        result = purge_old_dop_cortex_rows(db, days=days)
        return {"days": days, **result}
    finally:
        db.close()


@router.get("/driver-schedule-summary")
def get_driver_schedule_summary():
    """Return a compatibility summary payload for report-only mode."""
    try:
        # Keep this endpoint backward-compatible for older frontend bundles that
        # still fetch summary after upload. In report-only mode we return an
        # empty schedule payload plus report availability metadata.
        has_report = bool(
            orchestrator.status.driver_schedule_report_path
            and os.path.exists(orchestrator.status.driver_schedule_report_path)
        )

        return {
            "timestamp": "",
            "date": "",
            "assignments": [],
            "sweepers": [],
            "report_available": has_report,
            "report_path": orchestrator.status.driver_schedule_report_path,
            "report_only": True,
            "message": "Driver schedule summary is not retained. Use the generated PDF report.",
        }
    except HTTPException:
        raise
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
    status = orchestrator.get_status()

    # Fallback to persisted DB counts so uploads survive process restarts
    if not any([status.get("dop_uploaded"), status.get("fleet_uploaded"), status.get("cortex_uploaded"), status.get("route_sheets_uploaded")]):
        db = SessionLocal()
        try:
            dop_count = db.query(DOP).count()
            fleet_count = db.query(Vehicle).count()
            cortex_count = db.query(Cortex).count()
            route_sheets_count = db.query(RouteSheet).count()

            status["dop_uploaded"] = dop_count > 0
            status["fleet_uploaded"] = fleet_count > 0
            status["cortex_uploaded"] = cortex_count > 0
            status["route_sheets_uploaded"] = route_sheets_count > 0
            status["dop_record_count"] = dop_count
            status["fleet_record_count"] = fleet_count
            status["cortex_record_count"] = cortex_count
            status["route_sheets_count"] = route_sheets_count
        finally:
            db.close()

    return status


@router.post("/reset")
def reset_upload_cycle():
    """Reset in-memory ingest status for a new test cycle."""
    orchestrator.reset()
    return {
        "status": "reset",
        "message": "Ingest status reset successfully.",
        "details": "In-memory state cleared; uploaded files on disk are unchanged."
    }


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


@router.post("/primary-driver")
def set_primary_driver(route_code: str, driver_name: str):
    """Set the primary driver for a route with multiple assigned drivers."""
    try:
        assignment = orchestrator.assignments.get(route_code)
        if not assignment:
            raise HTTPException(status_code=404, detail=f"Route not found: {route_code}")

        assignment.driver_name = driver_name.strip() if driver_name else assignment.driver_name
        return {
            "status": "updated",
            "route_code": route_code,
            "driver_name": assignment.driver_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set primary driver: {str(e)}")


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


