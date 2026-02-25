"""
Role-based access control (RBAC) for NDAY Route Manager

Defines role hierarchy and permission mappings.
"""

from enum import Enum
from typing import List, Set

# ============================================================================
# ROLE DEFINITIONS
# ============================================================================

class Role(str, Enum):
    """Hierarchical user roles"""
    ADMIN = "admin"              # Full system access, code editing
    MANAGER = "manager"          # Financial data access, reporting, no code editing
    DISPATCHER = "dispatcher"    # Route/assignment management, no financial access
    DRIVER = "driver"            # Driver portal only, assignment viewing


# ============================================================================
# PERMISSION MATRIX
# ============================================================================

class Permission(str, Enum):
    """Granular permission flags"""
    # System management
    MANAGE_USERS = "manage_users"
    MANAGE_SYSTEM = "manage_system"
    
    # Financial data - only admin and manager
    VIEW_FINANCIAL = "view_financial"           # Invoices, incentives, scorecards
    VIEW_VARIABLE_INVOICES = "view_variable_invoices"
    VIEW_WEEKLY_INCENTIVES = "view_weekly_incentives"
    VIEW_FLEET_INVOICES = "view_fleet_invoices"
    VIEW_DSP_SCORECARD = "view_dsp_scorecard"
    VIEW_POD_REPORTS = "view_pod_reports"
    
    # Operational data - manager, dispatcher, admin
    VIEW_REPORTS = "view_reports"
    VIEW_WST_DATA = "view_wst_data"             # Work summary tracking
    MANAGE_ASSIGNMENTS = "manage_assignments"
    
    # Basic access
    VIEW_ASSIGNMENTS = "view_assignments"       # Driver-specific only
    VIEW_SCHEDULE = "view_schedule"             # Schedule viewing


# ============================================================================
# ROLE-PERMISSION MAPPING
# ============================================================================

ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        # System
        Permission.MANAGE_USERS,
        Permission.MANAGE_SYSTEM,
        # Financial
        Permission.VIEW_FINANCIAL,
        Permission.VIEW_VARIABLE_INVOICES,
        Permission.VIEW_WEEKLY_INCENTIVES,
        Permission.VIEW_FLEET_INVOICES,
        Permission.VIEW_DSP_SCORECARD,
        Permission.VIEW_POD_REPORTS,
        # Operational
        Permission.VIEW_REPORTS,
        Permission.VIEW_WST_DATA,
        Permission.MANAGE_ASSIGNMENTS,
        Permission.VIEW_ASSIGNMENTS,
        Permission.VIEW_SCHEDULE,
    },
    
    Role.MANAGER: {
        # Financial (all invoices, incentives, scorecards)
        Permission.VIEW_FINANCIAL,
        Permission.VIEW_VARIABLE_INVOICES,
        Permission.VIEW_WEEKLY_INCENTIVES,
        Permission.VIEW_FLEET_INVOICES,
        Permission.VIEW_DSP_SCORECARD,
        Permission.VIEW_POD_REPORTS,
        # Operational
        Permission.VIEW_REPORTS,
        Permission.VIEW_WST_DATA,
        Permission.MANAGE_ASSIGNMENTS,
        Permission.VIEW_ASSIGNMENTS,
        Permission.VIEW_SCHEDULE,
    },
    
    Role.DISPATCHER: {
        # Operational only (no financial)
        Permission.VIEW_REPORTS,
        Permission.VIEW_WST_DATA,
        Permission.MANAGE_ASSIGNMENTS,
        Permission.VIEW_ASSIGNMENTS,
        Permission.VIEW_SCHEDULE,
    },
    
    Role.DRIVER: {
        # Driver portal only
        Permission.VIEW_ASSIGNMENTS,
        Permission.VIEW_SCHEDULE,
    },
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_permissions(role: str) -> Set[Permission]:
    """Get all permissions for a given role"""
    try:
        role_enum = Role(role)
        return ROLE_PERMISSIONS.get(role_enum, set())
    except ValueError:
        return set()


def has_permission(role: str, permission: Permission) -> bool:
    """Check if role has specific permission"""
    return permission in get_permissions(role)


def has_any_permission(role: str, permissions: List[Permission]) -> bool:
    """Check if role has any of the given permissions"""
    role_perms = get_permissions(role)
    return any(perm in role_perms for perm in permissions)


def has_all_permissions(role: str, permissions: List[Permission]) -> bool:
    """Check if role has all of the given permissions"""
    role_perms = get_permissions(role)
    return all(perm in role_perms for perm in permissions)


def can_access_financial_data(role: str) -> bool:
    """Check if role can access financial reports and invoices"""
    return has_permission(role, Permission.VIEW_FINANCIAL)


def can_manage_route_assignments(role: str) -> bool:
    """Check if role can manage vehicle assignments"""
    return has_permission(role, Permission.MANAGE_ASSIGNMENTS)


def get_role_hierarchy_level(role: str) -> int:
    """Get numeric hierarchy level (higher = more access)"""
    hierarchy = {
        Role.DRIVER: 0,
        Role.DISPATCHER: 1,
        Role.MANAGER: 2,
        Role.ADMIN: 3,
    }
    try:
        role_enum = Role(role)
        return hierarchy.get(role_enum, -1)
    except ValueError:
        return -1


def is_admin(role: str) -> bool:
    """Check if role is admin"""
    return role == Role.ADMIN.value


def is_manager_or_admin(role: str) -> bool:
    """Check if role is manager or admin"""
    return role in {Role.MANAGER.value, Role.ADMIN.value}
