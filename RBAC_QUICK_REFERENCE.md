# RBAC Quick Reference Card

## Roles at a Glance

```
┌─────────────┬──────────────────────────────────────────────────────────┐
│ Role        │ Access Level                                             │
├─────────────┼──────────────────────────────────────────────────────────┤
│ 👑 ADMIN    │ Everything (system, financial, operational, code edit)  │
│ 💼 MANAGER  │ Financial + Operational (NO code editing)               │
│ 📋 DISPATCH │ Operational only (assignments, routes)                  │
│ 👤 DRIVER   │ Own assignments & schedule only                         │
└─────────────┴──────────────────────────────────────────────────────────┘
```

---

## Quick Protection Examples

### Protect Financial Endpoint (Manager+)
```python
from api.src.authorization import require_financial_access

@router.get("/invoices")
def get_invoices(role: str = Depends(require_financial_access)):
    return {"invoices": [...]}
```

### Protect Operational Endpoint (Dispatcher+)
```python
from api.src.permissions import Permission
from api.src.authorization import require_permission, get_current_user_role

@router.post("/assign-vehicle")
@require_permission(Permission.MANAGE_ASSIGNMENTS)
def assign_vehicle(role: str = Depends(get_current_user_role), data: dict = None):
    return {"success": True}
```

### Protect Admin Endpoint
```python
from api.src.authorization import require_admin

@router.post("/create-user")
def create_user(role: str = Depends(require_admin), data: dict = None):
    return {"success": True}
```

---

## Permissions You Should Know

```python
Permission.VIEW_FINANCIAL           # All financial data (admin + manager)
Permission.VIEW_VARIABLE_INVOICES   # Specific to variable invoices
Permission.VIEW_WEEKLY_INCENTIVES   # Incentive programs
Permission.VIEW_FLEET_INVOICES      # Fleet costs
Permission.VIEW_DSP_SCORECARD       # Partner scorecards
Permission.VIEW_POD_REPORTS         # Delivery reports
Permission.MANAGE_ASSIGNMENTS       # Vehicle/route assignments
Permission.MANAGE_USERS             # User creation/deletion (admin only)
```

---

## Testing Roles

### Admin Test
```bash
ADMIN_TOKEN="<jwt_with_role:admin>"
curl -X GET http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# ✅ 200 OK - Returns data
```

### Manager Test
```bash
MANAGER_TOKEN="<jwt_with_role:manager>"
curl -X GET http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $MANAGER_TOKEN"
# ✅ 200 OK - Returns data
```

### Dispatcher Test
```bash
DISPATCH_TOKEN="<jwt_with_role:dispatcher>"
curl -X GET http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $DISPATCH_TOKEN"
# ❌ 403 Forbidden - Insufficient permissions
```

---

## Common Error Responses

```json
// Missing/invalid token
401 Unauthorized
{"detail": "Invalid token"}

// Insufficient permission
403 Forbidden
{"detail": "Insufficient permissions. Required: view_variable_invoices"}

// Admin-only endpoint
403 Forbidden
{"detail": "Admin access required"}

// Invalid role in token
401 Unauthorized
{"detail": "Invalid role: invalid_role"}
```

---

## Helper Methods on User Model

```python
user = session.query(User).first()

# Check financial access
if user.has_financial_access():
    # Show financial dashboard

# Check assignment management
if user.can_manage_assignments():
    # Show assignment tools
```

---

## Creating Users by Role

```bash
# Create admin
curl -X POST http://localhost:8000/auth/create-user \
  -d '{"username":"admin1","role":"admin",...}'

# Create manager (NEW)
curl -X POST http://localhost:8000/auth/create-user \
  -d '{"username":"mgr1","role":"manager",...}'

# Create dispatcher
curl -X POST http://localhost:8000/auth/create-user \
  -d '{"username":"disp1","role":"dispatcher",...}'

# Create driver
curl -X POST http://localhost:8000/auth/create-user \
  -d '{"username":"driver1","role":"driver",...}'
```

---

## File Locations

| File | Purpose |
|------|---------|
| `api/src/permissions.py` | Role & permission definitions |
| `api/src/authorization.py` | FastAPI decorators & dependencies |
| `api/src/database.py` | User model (updated) |
| `api/src/routes/RBAC_INTEGRATION_EXAMPLES.py` | Code patterns & examples |
| `ROLE_HIERARCHY_IMPLEMENTATION.md` | Full documentation |
| `RBAC_SESSION_UPDATE_2026-02-23.md` | Implementation summary |

---

## Integration Checklist

- [ ] Import `get_current_user_role` in your route file
- [ ] Import `Permission` enum if using permission-based protection
- [ ] Add decorator(`@require_permission`, `@require_role`, etc.) above `@router`
- [ ] Add dependency parameter to function: `role: str = Depends(get_current_user_role)`
- [ ] Test with JWT token containing the correct role claim
- [ ] Update error response documentation
- [ ] Check frontend can handle 403 responses

---

## Decorator Syntax Quick Ref

```python
# One permission (any of multiple)
@require_permission(Permission.VIEW_FINANCIAL)

# All permissions
@require_permission_all(Permission.VIEW_FINANCIAL, Permission.MANAGE_ASSIGNMENTS)

# Specific roles
@require_role("admin", "manager")

# Using dependencies
def endpoint(role: str = Depends(require_admin)): ...
def endpoint(role: str = Depends(require_admin_or_manager)): ...
def endpoint(role: str = Depends(require_financial_access)): ...
```

---

Need help? See [ROLE_HIERARCHY_IMPLEMENTATION.md](ROLE_HIERARCHY_IMPLEMENTATION.md)
