from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from typing import List
from datetime import datetime
import os
import tempfile
from api.src.orchestrator import orchestrator
from api.src.authorization import get_current_user_role, require_financial_access
from api.src.permissions import Permission
from api.src.database import (
    SessionLocal,
    VariableInvoice,
    WstDeliveredPackages,
    WstServiceDetails,
    WstTrainingWeekly,
    WstUnplannedDelay,
    WstWeeklyReport,
    WeeklyIncentiveInvoice,
    FleetInvoice,
    DspScorecardSummary,
    PodReportSummary,
)
from api.src.ingest_variable_invoice import ingest_variable_invoice_pdf
from api.src.ingest_wst_zip import ingest_wst_zip
from api.src.ingest_weekly_incentive import parse_weekly_incentive_pdf
from api.src.ingest_fleet_invoice import parse_fleet_invoice_pdf
from api.src.ingest_dsp_scorecard import parse_dsp_scorecard_pdf
from api.src.ingest_pod_report import parse_pod_report_pdf

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '../../uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _to_bool(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return None


def _to_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _to_decimal(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


@router.post("/dop")
def upload_dop(file: UploadFile = File(...)):
    """Upload DOP Excel or CSV file and parse."""
    try:
        # Validate file type before processing
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ['.xlsx', '.xls', '.csv']:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type: {file_ext}. DOP file must be .xlsx, .xls, or .csv"
            )
        
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload DOP: {str(e)}")


@router.post("/fleet")
def upload_fleet(file: UploadFile = File(...)):
    """Upload Fleet Excel or CSV file and parse."""
    try:
        # Validate file type before processing
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ['.xlsx', '.xls', '.csv']:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type: {file_ext}. Fleet file must be .xlsx, .xls, or .csv"
            )
        
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Fleet: {str(e)}")


@router.post("/cortex")
def upload_cortex(file: UploadFile = File(...)):
    """Upload Cortex Excel or CSV file and parse."""
    try:
        # Validate file type before processing
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ['.xlsx', '.xls', '.csv']:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type: {file_ext}. Cortex file must be .xlsx, .xls, or .csv"
            )
        
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
    except HTTPException:
        raise
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


@router.post("/wst-zip")
def upload_wst_zip(file: UploadFile = File(...)):
    """Upload WST ZIP and ingest all five CSVs."""
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".zip":
            raise HTTPException(status_code=400, detail="WST upload must be a .zip file")

        file_path = os.path.join(UPLOAD_DIR, f"wst_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        results, errors = ingest_wst_zip(file_path)

        db = SessionLocal()
        try:
            counts = {}
            summary = {
                "total_records": 0,
                "delivered_packages_total": 0,
                "pickup_packages_total": 0,
                "excluded_routes": 0,
                "dsp_late_cancel_total": 0,
            }
            for key, records in results.items():
                if key == "delivered packages report":
                    for record in records:
                        package_count = _to_int(record.get("package count")) or 0
                        package_type = (record.get("package type") or "").strip().lower()
                        if "pickup" in package_type:
                            summary["pickup_packages_total"] += package_count
                        else:
                            summary["delivered_packages_total"] += package_count
                        db.add(WstDeliveredPackages(
                            report_date=_to_date(record.get("date")),
                            station=record.get("station"),
                            dsp_short_code=record.get("dsp short code"),
                            package_count=package_count,
                            package_type=record.get("package type"),
                            source_file=file.filename,
                        ))
                    counts[key] = len(records)
                elif key == "service details report":
                    for record in records:
                        excluded = _to_bool(record.get("excluded?") or record.get("excluded"))
                        if excluded:
                            summary["excluded_routes"] += 1
                        db.add(WstServiceDetails(
                            report_date=_to_date(record.get("date")),
                            station=record.get("station"),
                            dsp_short_code=record.get("dsp short code"),
                            delivery_associate=record.get("delivery associate"),
                            route_code=record.get("route"),
                            service_type=record.get("service type"),
                            planned_duration=record.get("planned duration"),
                            log_in=record.get("log in"),
                            log_out=record.get("log out"),
                            total_distance_planned=_to_decimal(record.get("total distance planned")),
                            total_distance_allowance=_to_decimal(record.get("total distance allowance")),
                            distance_unit=record.get("distance unit"),
                            shipments_delivered=_to_int(record.get("shipments delivered")),
                            shipments_returned=_to_int(record.get("shipments returned")),
                            pickup_packages=_to_int(record.get("pickup packages")),
                            excluded=excluded,
                            source_file=file.filename,
                        ))
                    counts[key] = len(records)
                elif key == "training weekly report":
                    for record in records:
                        db.add(WstTrainingWeekly(
                            assignment_date=_to_date(record.get("assignment date")),
                            payment_date=_to_date(record.get("payment date")),
                            station=record.get("station"),
                            dsp_short_code=record.get("dsp short code"),
                            delivery_associate=record.get("delivery associate"),
                            service_type=record.get("service type"),
                            course_name=record.get("course name"),
                            dsp_payment_eligible=_to_bool(record.get("dsp payment eligible")),
                            source_file=file.filename,
                        ))
                    counts[key] = len(records)
                elif key == "unplanned delay weekly report":
                    for record in records:
                        db.add(WstUnplannedDelay(
                            report_date=_to_date(record.get("date")),
                            station=record.get("station"),
                            dsp_short_code=record.get("dsp short code"),
                            delay_reason=record.get("unplanned delay"),
                            total_delay_minutes=_to_decimal(record.get("total delay in minutes")),
                            impacted_routes=_to_int(record.get("impacted routes")),
                            notes=record.get("notes"),
                            source_file=file.filename,
                        ))
                    counts[key] = len(records)
                elif key == "weekly report":
                    for record in records:
                        dsp_late_cancel = _to_decimal(record.get("dsp late cancel")) or 0
                        summary["dsp_late_cancel_total"] += dsp_late_cancel
                        db.add(WstWeeklyReport(
                            report_date=_to_date(record.get("date")),
                            station=record.get("station"),
                            dsp_short_code=record.get("dsp short code"),
                            service_type=record.get("service type"),
                            planned_duration=record.get("planned duration"),
                            total_distance_planned=_to_decimal(record.get("total distance planned")),
                            total_distance_allowance=_to_decimal(record.get("total distance allowance")),
                            planned_distance_unit=record.get("planned distance unit"),
                            amzl_late_cancel=_to_decimal(record.get("amzl late cancel")),
                            dsp_late_cancel=dsp_late_cancel,
                            quick_coverage_accepted=_to_decimal(record.get("quick coverage accepted") or record.get("quick coverage")),
                            completed_routes=_to_int(record.get("completed routes")),
                            source_file=file.filename,
                        ))
                    counts[key] = len(records)

                summary["total_records"] += len(records)

            db.commit()

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {
            "filename": file.filename,
            "status": "uploaded",
            "records_parsed": counts,
            "summary": summary,
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload WST ZIP: {str(e)}")


@router.post("/variable-invoice")
def upload_variable_invoice(file: UploadFile = File(...), role: str = Depends(get_current_user_role)):
    """Upload weekly variable invoice PDF and store summary lines.
    
    Requires: Admin or Manager role (financial data access)
    """
    # Verify financial access
    require_financial_access(role)
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".pdf":
            raise HTTPException(status_code=400, detail="Variable invoice must be a .pdf file")

        file_path = os.path.join(UPLOAD_DIR, f"variable_invoice_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        db = SessionLocal()
        try:
            invoice, errors = ingest_variable_invoice_pdf(file_path, db)
        finally:
            db.close()

        summary = None
        if invoice:
            total_amount = sum([float(item.amount or 0) for item in invoice.line_items])
            total_quantity = sum([float(item.quantity or 0) for item in invoice.line_items])
            summary = {
                "line_items": len(invoice.line_items),
                "total_quantity": total_quantity,
                "total_amount": total_amount,
            }

        return {
            "filename": file.filename,
            "status": "uploaded",
            "invoice_number": invoice.invoice_number if invoice else None,
            "line_items": len(invoice.line_items) if invoice else 0,
            "summary": summary,
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Variable Invoice: {str(e)}")


@router.post("/fleet-invoice")
def upload_fleet_invoice(file: UploadFile = File(...), role: str = Depends(get_current_user_role)):
    """Upload monthly fleet invoice PDF (header only for now).
    
    Requires: Admin or Manager role (financial data access)
    """
    require_financial_access(role)
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".pdf":
            raise HTTPException(status_code=400, detail="Fleet invoice must be a .pdf file")

        file_path = os.path.join(UPLOAD_DIR, f"fleet_invoice_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        data, errors = parse_fleet_invoice_pdf(file_path)
        invoice_number = data.get("invoice_number")

        db = SessionLocal()
        try:
            invoice = FleetInvoice(
                invoice_number=invoice_number or f"fleet_{file.filename}",
                source_file=file.filename,
            )
            db.add(invoice)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {
            "filename": file.filename,
            "status": "uploaded",
            "invoice_number": invoice_number,
            "summary": {
                "stored": True,
                "parsed_fields": list(data.keys()),
            },
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Fleet Invoice: {str(e)}")


@router.post("/weekly-incentive")
def upload_weekly_incentive(file: UploadFile = File(...), role: str = Depends(get_current_user_role)):
    """Upload weekly incentive invoice PDF (header only for now).
    
    Requires: Admin or Manager role (financial data access)
    """
    require_financial_access(role)
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".pdf":
            raise HTTPException(status_code=400, detail="Weekly incentive must be a .pdf file")

        file_path = os.path.join(UPLOAD_DIR, f"weekly_incentive_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        data, errors = parse_weekly_incentive_pdf(file_path)
        invoice_number = data.get("invoice_number")

        db = SessionLocal()
        try:
            invoice = WeeklyIncentiveInvoice(
                invoice_number=invoice_number or f"weekly_{file.filename}",
                source_file=file.filename,
            )
            db.add(invoice)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {
            "filename": file.filename,
            "status": "uploaded",
            "invoice_number": invoice_number,
            "summary": {
                "stored": True,
                "parsed_fields": list(data.keys()),
            },
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload Weekly Incentive: {str(e)}")


@router.post("/dsp-scorecard")
def upload_dsp_scorecard(file: UploadFile = File(...)):
    """Upload DSP scorecard PDF (summary stored, parsing later)."""
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".pdf":
            raise HTTPException(status_code=400, detail="DSP scorecard must be a .pdf file")

        file_path = os.path.join(UPLOAD_DIR, f"dsp_scorecard_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        _, _, errors = parse_dsp_scorecard_pdf(file_path)

        db = SessionLocal()
        try:
            summary = DspScorecardSummary(source_file=file.filename)
            db.add(summary)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {
            "filename": file.filename,
            "status": "uploaded",
            "summary": {
                "stored": True,
                "parsed_sections": ["summary", "driver_section"],
            },
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload DSP Scorecard: {str(e)}")


@router.post("/pod-report")
def upload_pod_report(file: UploadFile = File(...)):
    """Upload POD report PDF (summary stored, parsing later)."""
    try:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != ".pdf":
            raise HTTPException(status_code=400, detail="POD report must be a .pdf file")

        file_path = os.path.join(UPLOAD_DIR, f"pod_report_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(file.file.read())

        _, _, errors = parse_pod_report_pdf(file_path)

        db = SessionLocal()
        try:
            summary = PodReportSummary(source_file=file.filename)
            db.add(summary)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return {
            "filename": file.filename,
            "status": "uploaded",
            "summary": {
                "stored": True,
                "parsed_sections": ["summary", "driver_section"],
            },
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload POD Report: {str(e)}")
