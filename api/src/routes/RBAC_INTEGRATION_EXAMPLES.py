"""
ROLE-BASED ACCESS CONTROL - INTEGRATION EXAMPLES

This file shows how to add role-based access control to existing endpoints
and create new protected endpoints for financial data.

Copy patterns from here to update api/src/routes/uploads.py and api/src/routes/auth.py
"""

# ============================================================================
# IMPORTS (add to top of routes file)
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from api.src.authorization import (
    get_current_user_role,
    require_role,
    require_permission,
    require_admin,
    require_admin_or_manager,
    require_financial_access,
)
from api.src.permissions import Permission, Role, has_permission


# ============================================================================
# PATTERN 1: Protect Admin-Only Endpoint (System Management)
# ============================================================================

# BEFORE: No protection
# @router.post("/create-user")
# def create_user(user_data: dict):
#     """Any authenticated user could create users - SECURITY RISK"""
#     pass

# AFTER: Protected with admin dependency
# @router.post("/create-user")
# def create_user(admin_role: str = Depends(require_admin), user_data: dict = None):
#     """Only admins can create users"""
#     # admin_role is guaranteed to be "admin"
#     return {"success": True, "message": f"User created by {admin_role}"}


# ============================================================================
# PATTERN 2: Protect Manager/Admin Endpoint (Financial Access)
# ============================================================================

# BEFORE: No protection
# @router.get("/variable-invoices")
# def get_variable_invoices():
#     """All authenticated users could view financial data - SECURITY RISK"""
#     pass

# AFTER: Protected with financial access dependency
# @router.get("/variable-invoices")
# def get_variable_invoices(role: str = Depends(require_financial_access)):
#     """Only admin and manager can view"""
#     # If role reaches here, it's either "admin" or "manager"
#     return {"invoices": []}


# ============================================================================
# PATTERN 3: Protect Operational Endpoint (Multiple Roles)
# ============================================================================

# BEFORE: No protection
# @router.post("/assign-vehicles")
# def assign_vehicles(data: dict):
#     """Any authenticated user could modify assignments - SECURITY RISK"""
#     pass

# AFTER: Protected with permission decorator
# @router.post("/assign-vehicles")
# @require_permission(Permission.MANAGE_ASSIGNMENTS)
# def assign_vehicles(role: str = Depends(get_current_user_role), data: dict = None):
#     """Admin, manager, or dispatcher can assign vehicles"""
#     # If role reaches here, they have MANAGE_ASSIGNMENTS permission
#     return {"success": True, "assignments_updated": 10}


# ============================================================================
# PATTERN 4: Role-Based Response Filtering
# ============================================================================

# Use when you want different data based on role, not just restrict access

def get_reports(role: str = Depends(get_current_user_role)):
    """
    Return different reports based on user role:
    - Admin/Manager: All reports including financial
    - Dispatcher: Only operational reports
    """
    reports = {
        "operational": ["route_status", "vehicle_utilization"],
        "financial": ["invoices", "incentives", "costs"],
        "system": ["audit_log", "configuration"],
    }
    
    if role == Role.ADMIN.value:
        # Return ALL reports
        return {
            "role": role,
            "available_reports": list(reports.keys()),
            "count": sum(len(v) for v in reports.values())
        }
    elif role == Role.MANAGER.value:
        # Return operational + financial
        return {
            "role": role,
            "available_reports": ["operational", "financial"],
            "count": len(reports["operational"]) + len(reports["financial"])
        }
    elif role == Role.DISPATCHER.value:
        # Return operational only
        return {
            "role": role,
            "available_reports": ["operational"],
            "count": len(reports["operational"])
        }
    else:  # DRIVER or other
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to access reports"
        )


# ============================================================================
# PATTERN 5: Multiple Permission Checks
# ============================================================================

# @router.post("/bulk-update-financial")
# def bulk_update_financial(
#     role: str = Depends(get_current_user_role),
#     updates: dict = None
# ):
#     """Example: Require BOTH financial access AND assignment management"""
#     
#     # Check multiple permissions
#     if not has_permission(role, Permission.VIEW_FINANCIAL):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Financial access required"
#         )
#     
#     if not has_permission(role, Permission.MANAGE_ASSIGNMENTS):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Assignment management rights required"
#         )
#     
#     # Proceed with bulk update
#     return {"success": True, "updated": 5}


# ============================================================================
# PATTERN 6: Creating New Protected Financial Data Endpoints
# ============================================================================

router = APIRouter()

@router.get("/financial/variable-invoices")
@require_permission(Permission.VIEW_VARIABLE_INVOICES)
def get_variable_invoices(role: str = Depends(get_current_user_role)):
    """
    Retrieve all variable invoices.
    
    **Access**: Admin, Manager only
    **Data**: Read-only
    
    Returns:
        - List of variable invoices with line items
    
    Raises:
        - 403: Insufficient permissions (role is Dispatcher or Driver)
    """
    # Query database for invoices
    return {
        "status": "success",
        "invoices": [],
        "accessed_by": role
    }


@router.get("/financial/weekly-incentives")
@require_permission(Permission.VIEW_WEEKLY_INCENTIVES)
def get_weekly_incentives(role: str = Depends(get_current_user_role)):
    """Weekly incentive data"""
    return {"status": "success", "incentives": []}


@router.get("/financial/fleet-invoices")
@require_permission(Permission.VIEW_FLEET_INVOICES)
def get_fleet_invoices(role: str = Depends(get_current_user_role)):
    """Fleet vehicle invoice data"""
    return {"status": "success", "invoices": []}


@router.get("/financial/dsp-scorecard")
@require_permission(Permission.VIEW_DSP_SCORECARD)
def get_dsp_scorecard(role: str = Depends(get_current_user_role)):
    """Delivery service partner scorecard"""
    return {"status": "success", "scorecard_data": []}


@router.get("/financial/pod-reports")
@require_permission(Permission.VIEW_POD_REPORTS)
def get_pod_reports(role: str = Depends(get_current_user_role)):
    """Proof of delivery reports"""
    return {"status": "success", "reports": []}


# ============================================================================
# PATTERN 7: Operational Data (Manager, Dispatcher, Admin)
# ============================================================================

@router.get("/reports/wst-summary")
@require_permission(Permission.VIEW_WST_DATA)
def get_wst_summary(role: str = Depends(get_current_user_role)):
    """Work/Service summary tracking (all managers and dispatchers can access)"""
    return {"status": "success", "summary": []}


@router.post("/assignments/assign-vehicles")
@require_permission(Permission.MANAGE_ASSIGNMENTS)
def assign_vehicles(role: str = Depends(get_current_user_role), assignment_data: dict = None):
    """Assign vehicles to routes (manager and dispatcher only)"""
    return {
        "status": "success",
        "message": f"Vehicles assigned by {role}",
        "assignments": 5
    }


# ============================================================================
# PATTERN 8: Driver Portal (Driver-Only)
# ============================================================================

@router.get("/driver/my-assignments")
@require_role("driver")
def get_driver_assignments(role: str = Depends(get_current_user_role)):
    """Get driver's own assignments only"""
    # In real implementation, would filter by user_id
    return {"assignments": [], "driver_id": "TODO_from_token"}


# ============================================================================
# ADVANCED PATTERN: Dynamic Permission Checking
# ============================================================================

from api.src.permissions import get_permissions

def get_detailed_user_capabilities(role: str = Depends(get_current_user_role)) -> dict:
    """
    Return what this user can and cannot do
    Useful for frontend to dynamically show/hide UI elements
    """
    permissions = get_permissions(role)
    
    return {
        "role": role,
        "capabilities": {
            "can_view_financial": Permission.VIEW_FINANCIAL in permissions,
            "can_manage_assignments": Permission.MANAGE_ASSIGNMENTS in permissions,
            "can_manage_users": Permission.MANAGE_USERS in permissions,
            "can_access_admin_panel": role == Role.ADMIN.value,
        },
        "financial_access": [
            "variable_invoices" if Permission.VIEW_VARIABLE_INVOICES in permissions else None,
            "weekly_incentives" if Permission.VIEW_WEEKLY_INCENTIVES in permissions else None,
            "fleet_invoices" if Permission.VIEW_FLEET_INVOICES in permissions else None,
            "dsp_scorecard" if Permission.VIEW_DSP_SCORECARD in permissions else None,
            "pod_reports" if Permission.VIEW_POD_REPORTS in permissions else None,
        ]
    }


# ============================================================================
# ERROR HANDLING - What users see when access is denied
# ============================================================================

# When manager tries to access admin endpoint:
# HTTP 403 Forbidden
# {"detail": "Admin access required"}

# When driver tries to access financial data:
# HTTP 403 Forbidden
# {"detail": "Insufficient permissions. Required: view_variable_invoices"}

# When token is missing or invalid:
# HTTP 401 Unauthorized
# {"detail": "Invalid token"}

# ============================================================================
# TESTING GUIDE
# ============================================================================

"""
1. Get tokens for each role:

curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_user","password":"admin_pass"}'
# Returns: {"access_token": "admin_token", ...}

curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"manager_user","password":"manager_pass"}'
# Returns: {"access_token": "manager_token", ...}

curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"dispatcher_user","password":"dispatcher_pass"}'
# Returns: {"access_token": "dispatcher_token", ...}


2. Test financial endpoint with different tokens:

# Manager accessing financial data (should work)
curl -X GET http://localhost:8000/financial/variable-invoices \
  -H "Authorization: Bearer manager_token"
# Result: 200 OK - Returns data

# Dispatcher accessing financial data (should be blocked)
curl -X GET http://localhost:8000/financial/variable-invoices \
  -H "Authorization: Bearer dispatcher_token"
# Result: 403 Forbidden - {"detail": "Insufficient permissions..."}


3. Test operational endpoint with different tokens:

# Dispatcher accessing assignments (should work)
curl -X POST http://localhost:8000/assignments/assign-vehicles \
  -H "Authorization: Bearer dispatcher_token" \
  -H "Content-Type: application/json" \
  -d '{"route_id": "001", "vehicle_id": "van_01"}'
# Result: 200 OK - Vehicles assigned

# Driver accessing assignments (should be blocked)
curl -X POST http://localhost:8000/assignments/assign-vehicles \
  -H "Authorization: Bearer driver_token" \
  -H "Content-Type: application/json" \
  -d '{"route_id": "001", "vehicle_id": "van_01"}'
# Result: 403 Forbidden - Insufficient permissions
"""

# ============================================================================
# MIGRATION CHECKLIST
# ============================================================================

"""
When adding RBAC to existing routes:

[ ] Identify all endpoints
[ ] Categorize by sensitivity:
    - Admin-only (user management, system config)
    - Financial (invoices, incentives, scorecards)
    - Operational (assignments, reports, schedules)
    - Public/Driver (driver portal, schedules)
[ ] Add appropriate decorators per category
[ ] Update route documentation with access requirements
[ ] Test with each role type
[ ] Update frontend to respect role capabilities
[ ] Add audit logging for sensitive operations
[ ] Create test users for each role
[ ] Document in ROLE_HIERARCHY_IMPLEMENTATION.md
"""
