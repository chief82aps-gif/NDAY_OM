# Role-Based Access Control (RBAC) Implementation

**Date Added**: February 23, 2026  
**Status**: Active  
**Location**: `api/src/permissions.py`, `api/src/authorization.py`, `api/src/database.py`

---

## Overview

A hierarchical role system has been implemented to restrict access to financial data and sensitive operational features. This ensures that basic operational users (drivers, dispatchers) cannot view financial reports while maintaining full administrative capabilities for authorized personnel.

---

## Role Hierarchy

### 1. **Admin** (Tier 4 - Highest Access)
- **Full system access** including all data and configuration
- Can create/delete users and modify roles
- Full code editing capabilities
- **Financial Access**: ✅ All
- **Use Case**: System administrators, deployment engineers

### 2. **Manager** (Tier 3 - Full Data Access, No Code Editing)
- **Cannot**: Edit code, manage users, modify system configuration
- **Can**: View all financial data and operational reports
- **Financial Access**: ✅ All (invoices, incentives, scorecards, reports)
- **Use Case**: Finance supervisors, operations managers, analytics team

### 3. **Dispatcher** (Tier 2 - Operational Only)
- **Cannot**: View financial data, edit code, manage users
- **Can**: Manage route assignments, view vehicle assignments, access operational reports
- **Financial Access**: ❌ No
- **Use Case**: Route managers, assignment coordinators

### 4. **Driver** (Tier 1 - Minimal Access)
- **Can**: View only own assignments and schedule
- **Cannot**: View other drivers' data, financial data, or system configuration
- **Financial Access**: ❌ No
- **Use Case**: Individual drivers accessing mobile portal

---

## Permission Matrix

| Permission | Admin | Manager | Dispatcher | Driver |
|-----------|:-----:|:-------:|:----------:|:------:|
| **System Management** |
| `manage_users` | ✅ | ❌ | ❌ | ❌ |
| `manage_system` | ✅ | ❌ | ❌ | ❌ |
| **Financial Data** |
| `view_financial` | ✅ | ✅ | ❌ | ❌ |
| `view_variable_invoices` | ✅ | ✅ | ❌ | ❌ |
| `view_weekly_incentives` | ✅ | ✅ | ❌ | ❌ |
| `view_fleet_invoices` | ✅ | ✅ | ❌ | ❌ |
| `view_dsp_scorecard` | ✅ | ✅ | ❌ | ❌ |
| `view_pod_reports` | ✅ | ✅ | ❌ | ❌ |
| **Operational Access** |
| `view_reports` | ✅ | ✅ | ✅ | ❌ |
| `view_wst_data` | ✅ | ✅ | ✅ | ❌ |
| `manage_assignments` | ✅ | ✅ | ✅ | ❌ |
| **Basic Access** |
| `view_assignments` | ✅ | ✅ | ✅ | ✅ |
| `view_schedule` | ✅ | ✅ | ✅ | ✅ |

---

## Implementation Files

### 1. `api/src/permissions.py`
Defines the role hierarchy and permission matrix.

**Key Contents:**
- `Role` enum: admin, manager, dispatcher, driver
- `Permission` enum: granular permission flags
- `ROLE_PERMISSIONS` dict: maps roles to their permissions
- Helper functions:
  - `get_permissions(role)` - Get all permissions for a role
  - `has_permission(role, permission)` - Check single permission
  - `can_access_financial_data(role)` - Check financial access
  - `is_admin(role)` - Check if admin
  - `is_manager_or_admin(role)` - Check if manager or admin

### 2. `api/src/authorization.py`
Provides FastAPI decorators and middleware for route protection.

**Key Components:**
- `get_current_user_role()` - Dependency to extract role from JWT token
- `@require_role()` - Decorator to restrict to specific roles
  ```python
  @router.get("/admin-panel")
  @require_role("admin")
  def admin_panel(role: str = Depends(get_current_user_role)):
      ...
  ```
- `@require_permission()` - Decorator to check any permission
  ```python
  @router.get("/invoices")
  @require_permission(Permission.VIEW_VARIABLE_INVOICES)
  def get_invoices(role: str = Depends(get_current_user_role)):
      ...
  ```
- `require_admin()` - Dependency for admin-only endpoints
- `require_admin_or_manager()` - Dependency for admin/manager endpoints
- `require_financial_access()` - Dependency for financial data endpoints

### 3. `api/src/database.py` (Updated)
User model now includes role validation methods:

- `user.has_financial_access()` - Boolean check
- `user.can_manage_assignments()` - Boolean check

---

## Usage Examples

### Protecting Financial Data Endpoints

```python
from fastapi import APIRouter, Depends
from api.src.authorization import get_current_user_role, require_financial_access
from api.src.permissions import Permission

router = APIRouter()

# Option 1: Using dependency
@router.get("/variable-invoices")
def get_variable_invoices(role: str = Depends(require_financial_access)):
    """Only admin and manager can access"""
    # ... fetch invoices ...
    return invoices

# Option 2: Using decorator (recommended for clarity)
@router.get("/fleet-invoices")
@require_permission(Permission.VIEW_FLEET_INVOICES)
def get_fleet_invoices(role: str = Depends(get_current_user_role)):
    """Only admin and manager can access"""
    return invoices

# Option 3: Using role decorator
@router.get("/scorecard-reports")
@require_role("admin", "manager")
def get_scorecard_reports(role: str = Depends(get_current_user_role)):
    """Only admin and manager can access"""
    return reports
```

### Protecting Operational Endpoints

```python
@router.post("/assign-vehicles")
@require_permission(Permission.MANAGE_ASSIGNMENTS)
def assign_vehicles(role: str = Depends(get_current_user_role), data: dict = None):
    """Admin, manager, and dispatcher can manage assignments"""
    return "Assignments updated"

@router.get("/route-report")
@require_permission(Permission.VIEW_REPORTS)
def get_route_report(role: str = Depends(get_current_user_role)):
    """Admin, manager, and dispatcher can view operational reports"""
    return reports
```

### Admin-Only Endpoints

```python
@router.post("/create-user")
def create_user(role: str = Depends(require_admin), user_data: dict = None):
    """Only admins can create users"""
    return "User created"

@router.post("/update-system-config")
def update_config(role: str = Depends(require_admin), config: dict = None):
    """Only admins can modify system configuration"""
    return "Config updated"
```

---

## Migration Steps

If adding protection to existing endpoints:

1. **Identify financial data endpoints** (currently unprotected):
   - `/variable-invoices` (if exists)
   - `/weekly-incentives` (if exists)
   - `/fleet-invoices` (if exists)
   - `/dsp-scorecard` (if exists)
   - `/pod-reports` (if exists)

2. **Add protection using one method**:
   ```python
   @require_permission(Permission.VIEW_VARIABLE_INVOICES)
   # OR
   @require_role("admin", "manager")
   # OR
   def endpoint(role: str = Depends(require_financial_access)):
   ```

3. **Test with different roles**:
   - Admin: ✅ Should pass
   - Manager: ✅ Should pass (for financial endpoints)
   - Dispatcher: ❌ Should fail with 403 (for financial endpoints)
   - Driver: ❌ Should fail with 403 (for financial endpoints)

---

## Creating Users with Different Roles

When creating new users via `/create-user` endpoint:

```json
{
  "username": "john_manager",
  "password": "secure_password",
  "name": "John Manager",
  "email": "john@company.com",
  "role": "manager"
}
```

**Valid role values**:
- `"admin"` - Full access
- `"manager"` - Financial access, no code editing
- `"dispatcher"` - Operational only
- `"driver"` - Portal access only

---

## JWT Token Format

The authorization system expects JWT tokens with a `role` claim:

```json
{
  "sub": "user_id",
  "role": "manager",
  "username": "john_manager",
  "exp": 1708704000
}
```

If token lacks a `role` claim, it will be rejected with a 401 Unauthorized response.

---

## Testing the Role System

### Test Case 1: Manager Access to Financial Data
```bash
# Login as manager
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"manager_user","password":"password"}'

# Access financial endpoint with manager token (should succeed)
curl -X GET http://localhost:8000/api/variable-invoices \
  -H "Authorization: Bearer <MANAGER_TOKEN>"
```

### Test Case 2: Dispatcher Blocked from Financial Data
```bash
# Login as dispatcher
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"dispatcher_user","password":"password"}'

# Access financial endpoint with dispatcher token (should fail with 403)
curl -X GET http://localhost:8000/api/variable-invoices \
  -H "Authorization: Bearer <DISPATCHER_TOKEN>"
# Response: {"detail": "Insufficient permissions. Required: view_variable_invoices"}
```

---

## Future Enhancements

- [ ] Add role-based row-level security (RLS) at database level
- [ ] Implement audit logging for sensitive data access
- [ ] Add temporary permission elevation with approval workflow
- [ ] Create permission customization UI for admins
- [ ] Add API rate limiting by role tier
- [ ] Implement field-level encryption for sensitive financial data

---

## Support & Questions

Refer to:
- `api/src/permissions.py` - Permission definitions
- `api/src/authorization.py` - Decorator usage examples
- `Governance/DATABASE_SCHEMA.md` - Financial data tables

See [SESSION_PAUSE_SUMMARY_2026-02-23.md](../SESSION_PAUSE_SUMMARY_2026-02-23.md) for overall project status.
