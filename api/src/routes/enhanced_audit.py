"""
Enhanced Daily Screenshot Audit API with OCR, Service Matching, and Dispute Tracking.
"""

from typing import List, Optional, Dict
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import base64
import json

from api.src.authorization import get_current_user_role
from api.src.database import SessionLocal, ApprovedAudit, AuditDispute, AuditDARouteStats
from api.src.ocr_parser import parse_screenshot_audit, validate_audit_reconciliation
from api.src.ocr_service_library import (
    match_service_type,
    get_service_display_name,
    is_training_service,
    is_excluded_service,
    extract_cortex_packages,
    ocr_image_to_text,
    ocr_image_to_text_with_confidence,
    SERVICE_TYPE_LIBRARY,
)

router = APIRouter(prefix="/audit/screenshot", tags=["screenshot_audit"])


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ValidateTextRequest(BaseModel):
    cortex_raw: str
    wst_raw: str


class ValidateMetricsRequest(BaseModel):
    """Simplified request for direct metric numbers (no OCR)"""
    cortex_routes: int
    cortex_packages: int
    wst_routes: int
    wst_packages: int


class DisputeInput(BaseModel):
    dispute_type: str
    metric: Optional[str] = None
    cortex_value: Optional[int] = None
    wst_value: Optional[int] = None
    reason: str
    status: str  # acknowledged, dispute_submitted


class DARouteStatInput(BaseModel):
    driver_name: str
    route_code: str
    service_type: str
    completed_stops: int
    total_stops: int
    completed_deliveries: int
    total_deliveries: int
    hours_worked: float


class EnhancedAuditSubmission(BaseModel):
    audit_date: date
    station: str = "Daily Audit"
    cortex_raw: str
    wst_raw: str
    cortex_route_count: int
    wst_route_count: int
    cortex_package_count: int
    wst_package_count: int
    training_routes: List[str] = []
    excluded_services: List[Dict] = []  # [{"name": "svc", "status": "acknowledged", "reason": "..."}]
    disputes: List[DisputeInput] = []
    da_route_stats: List[DARouteStatInput] = []
    notes: str = ""


class AuditValidationResponse(BaseModel):
    is_valid: bool
    route_count_match: bool
    package_variance: int
    critical_issues: List[str]
    warnings: List[str]
    disputes_required: List[str]
    cortex_route_count: int = 0
    wst_route_count: int = 0
    cortex_package_count: int = 0
    wst_package_count: int = 0


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/validate-text")
def validate_ocr_text(
    request: ValidateTextRequest,
) -> AuditValidationResponse:
    """
    Validate OCR-extracted text and identify issues requiring user confirmation.
    """
    # Parse both screenshots
    cortex_parsed = parse_screenshot_audit(request.cortex_raw)
    wst_parsed = parse_screenshot_audit(request.wst_raw)
    
    cortex_routes = cortex_parsed.get('completed_routes')
    wst_routes = wst_parsed.get('completed_routes')
    cortex_packages = cortex_parsed.get('delivered_packages')
    wst_packages = wst_parsed.get('delivered_packages')
    
    # Validate
    validation = validate_audit_reconciliation(cortex_parsed)
    
    # Check route count match
    routes_match = (cortex_routes.header_value == wst_routes.header_value 
                   if cortex_routes.header_value and wst_routes.header_value else False)
    
    # Check package variance
    package_variance = 0
    if cortex_packages.header_value and wst_packages.header_value:
        package_variance = wst_packages.header_value - cortex_packages.header_value
    
    disputes_required = []
    
    # Rule: If routes don't match, user must confirm
    if not routes_match and cortex_routes.header_value and wst_routes.header_value:
        disputes_required.append(
            f"Route count mismatch: Cortex={cortex_routes.header_value}, WST={wst_routes.header_value}"
        )
    
    # Rule: Package variance > 25 (WST lower) must be disputed
    if package_variance < -25:
        disputes_required.append(
            f"Package variance > 25: Cortex has {abs(package_variance)} more packages than WST"
        )
    
    return AuditValidationResponse(
        is_valid=validation['is_valid'],
        route_count_match=routes_match,
        package_variance=package_variance,
        critical_issues=validation.get('critical_issues', []),
        warnings=validation.get('warnings', []),
        disputes_required=disputes_required,
        cortex_route_count=cortex_routes.header_value or 0,
        wst_route_count=wst_routes.header_value or 0,
        cortex_package_count=cortex_packages.header_value or 0,
        wst_package_count=wst_packages.header_value or 0,
    )


@router.post("/validate-metrics")
def validate_metrics(request: ValidateMetricsRequest) -> Dict:
    """
    Simplified validation: Compare Cortex and WST metrics directly.
    
    Rules:
    - Routes must match exactly (zero tolerance)
    - Packages: absolute difference must be <= 25 packages
    """
    routes_match = request.cortex_routes == request.wst_routes
    
    # Calculate package variance (absolute difference)
    packages_variance = abs(request.cortex_packages - request.wst_packages)
    packages_ok = packages_variance <= 25
    
    issues = []
    if not routes_match:
        issues.append(f"Routes mismatch: Cortex {request.cortex_routes} vs WST {request.wst_routes}")
    if not packages_ok:
        issues.append(f"Package difference {packages_variance} exceeds 25 package tolerance")
    
    return {
        "routes_match": routes_match,
        "routes_difference": abs(request.cortex_routes - request.wst_routes),
        "packages_variance": packages_variance,
        "packages_ok": packages_ok,
        "issues": issues,
        "all_pass": routes_match and packages_ok,
    }


class MatchServicesRequest(BaseModel):
    service_names: List[str]


@router.post("/match-services")
def match_service_types(
    request: MatchServicesRequest,
    role: str = Depends(get_current_user_role),
) -> Dict[str, Dict]:
    """
    Match service names to the service type library.
    
    Returns mapping of service_name -> {matched_type, is_training, is_excluded}
    """
    results = {}
    
    for service_name in request.service_names:
        matched = match_service_type(service_name)
        results[service_name] = {
            "matched_type": get_service_display_name(matched) if matched else None,
            "is_training": is_training_service(service_name),
            "is_excluded": is_excluded_service(service_name),
            "canonical_name": get_service_display_name(matched) if matched else service_name,
        }
    
    return results


@router.post("/generate-dispute-summary")
def generate_dispute_summary(
    disputes: List[DisputeInput],
    max_chars: int = 350,
) -> Dict:
    """
    Generate a concise dispute summary from all disputes.
    
    Returns: {summary: str, character_count: int}
    """
    if not disputes:
        return {"summary": "No disputes recorded.", "character_count": 19}
    
    # Group by type
    dispute_groups = {}
    for dispute in disputes:
        dtype = dispute.dispute_type
        if dtype not in dispute_groups:
            dispute_groups[dtype] = []
        dispute_groups[dtype].append(dispute)
    
    # Build summary parts
    summary_parts = []
    
    for dtype, dtype_disputes in dispute_groups.items():
        if dtype == "route_count_mismatch":
            cortex_val = dtype_disputes[0].cortex_value
            wst_val = dtype_disputes[0].wst_value
            summary_parts.append(f"Route count: Cortex {cortex_val} vs WST {wst_val}.")
            if dtype_disputes[0].reason:
                summary_parts.append(f"Reason: {dtype_disputes[0].reason[:100]}.")
        
        elif dtype == "package_variance":
            diff = abs((dtype_disputes[0].cortex_value or 0) - (dtype_disputes[0].wst_value or 0))
            summary_parts.append(f"Package variance: {diff} units disputed.")
            if dtype_disputes[0].reason:
                summary_parts.append(f"Action: {dtype_disputes[0].reason[:100]}.")
        
        elif dtype == "excluded_service":
            count = len(dtype_disputes)
            summary_parts.append(f"{count} excluded services disputed.")
            if dtype_disputes[0].reason:
                summary_parts.append(f"Details: {dtype_disputes[0].reason[:80]}.")
    
    # Join and truncate
    summary = " ".join(summary_parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars-3] + "..."
    
    return {
        "summary": summary,
        "character_count": len(summary),
        "within_limit": len(summary) <= max_chars
    }


@router.post("/submit-audit")
def submit_enhanced_audit(
    audit: EnhancedAuditSubmission,
    role: str = Depends(get_current_user_role),
):
    """
    Submit a complete audit with all enhancements:
    - Service type matching
    - Training/excluded service tracking
    - DA route statistics
    - Dispute documentation
    """
    db = SessionLocal()
    try:
        # Generate dispute summary
        dispute_summary_resp = generate_dispute_summary(audit.disputes)
        
        # Create audit record
        approved_audit = ApprovedAudit(
            submitted_at=datetime.utcnow(),
            station=audit.station,
            audit_date=audit.audit_date,
            cortex_raw=audit.cortex_raw,
            wst_raw=audit.wst_raw,
            submitted_by=role,
            notes=audit.notes,
            
            # Enhanced fields
            cortex_route_count=audit.cortex_route_count,
            wst_route_count=audit.wst_route_count,
            cortex_package_count=audit.cortex_package_count,
            wst_package_count=audit.wst_package_count,
            training_routes_count=len(audit.training_routes),
            excluded_services=audit.excluded_services,
            disputes_json=[d.dict() for d in audit.disputes],
            dispute_summary=dispute_summary_resp['summary'],
            da_route_stats={s.driver_name: s.dict() for s in audit.da_route_stats},
        )
        
        db.add(approved_audit)
        db.flush()  # Get the ID without committing
        
        # Create dispute records
        for dispute_input in audit.disputes:
            dispute = AuditDispute(
                audit_id=approved_audit.id,
                approved_audit_date=audit.audit_date,
                dispute_type=dispute_input.dispute_type,
                variance_metric=dispute_input.metric,
                cortex_value=dispute_input.cortex_value,
                wst_value=dispute_input.wst_value,
                variance_amount=(dispute_input.cortex_value or 0) - (dispute_input.wst_value or 0),
                user_input_reason=dispute_input.reason,
                dispute_status=dispute_input.status,
                submitted_by=role,
            )
            db.add(dispute)
        
        # Create DA route stats
        for stat_input in audit.da_route_stats:
            # Calculate metrics
            avg_stops_per_hour = 0
            if stat_input.hours_worked > 0:
                avg_stops_per_hour = stat_input.completed_stops / stat_input.hours_worked
            
            route_efficiency = 0
            if stat_input.total_stops > 0:
                route_efficiency = (stat_input.completed_stops / stat_input.total_stops) * 100
            
            da_stat = AuditDARouteStats(
                audit_id=approved_audit.id,
                approved_audit_date=audit.audit_date,
                driver_name=stat_input.driver_name,
                route_code=stat_input.route_code,
                service_type=stat_input.service_type,
                completed_stops=stat_input.completed_stops,
                total_stops=stat_input.total_stops,
                completed_deliveries=stat_input.completed_deliveries,
                total_deliveries=stat_input.total_deliveries,
                avg_stops_per_hour=avg_stops_per_hour,
                route_efficiency=route_efficiency,
            )
            db.add(da_stat)
        
        db.commit()
        db.refresh(approved_audit)
        
        return {
            "success": True,
            "audit_id": approved_audit.id,
            "message": "Audit submitted successfully",
            "dispute_summary": dispute_summary_resp['summary'],
            "disputes_count": len(audit.disputes),
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to submit audit: {str(e)}")
    finally:
        db.close()


if __name__ == '__main__':
    # Test dispute summary generation
    test_disputes = [
        DisputeInput(
            dispute_type="route_count_mismatch",
            cortex_value=39,
            wst_value=35,
            reason="WST system lag during peak hours may have caused missed synchronization",
            status="dispute_submitted"
        ),
        DisputeInput(
            dispute_type="package_variance",
            cortex_value=9385,
            wst_value=9360,
            reason="25 packages returned to station after cutoff",
            status="acknowledged"
        ),
    ]
    
    print("Testing dispute summary generation...")
    result = generate_dispute_summary(test_disputes)
    print(f"\nSummary ({result['character_count']} chars):")
    print(f"  {result['summary']}")
    print(f"  Within 350 char limit: {result['within_limit']}")
