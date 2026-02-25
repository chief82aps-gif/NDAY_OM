# NDAY Route Manager — Role-Based Access Control Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI APPLICATION                           │
│                         (api/main.py - port 8000)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTP Requests
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AUTHORIZATION LAYER                               │
│                    (api/src/authorization.py)                          │
│                                                                         │
│  ┌─ get_current_user_role(jwt_token) ──────────────────────────────┐  │
│  │ • Extracts JWT from Authorization header                        │  │
│  │ • Decodes and validates token                                   │  │
│  │ • Returns role: "admin" | "manager" | "dispatcher" | "driver"  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─ Route Decorator Layer ────────────────────────────────────────┐   │
│  │ @require_role("admin", "manager")                             │   │
│  │ @require_permission(Permission.VIEW_FINANCIAL)                │   │
│  │ @require_permission_all(Permission.A, Permission.B)           │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─ Dependency Injection Layer ──────────────────────────────────┐    │
│  │ Depends(require_admin)                  → 403 if not admin    │    │
│  │ Depends(require_admin_or_manager)       → 403 if not A/M      │    │
│  │ Depends(require_financial_access)       → 403 if no financial │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                          ┌─────────┴──────────┐
                          │                    │
                    Access Denied         Access Granted
                     (403/401)                  │
                          │                    ▼
                          │        ┌──────────────────────────┐
                          │        │  PERMISSION CHECK        │
                          │        │ (api/src/permissions.py) │
                          │        │                          │
                          │        │  Role → Permissions Set  │
                          │        │  Check if allowed        │
                          │        │  Return True/False       │
                          │        └──────────────────────────┘
                          │                    │
                          │          ┌─────────┴──────────┐
                          │          │                    │
                    Access Denied    Access Granted
                     (403/401)       (200 OK)
                          │                    │
                          ▼                    ▼
                    ┌──────────────┐  ┌─────────────────┐
                    │  ERROR RESP  │  │ ROUTE HANDLER   │
                    │              │  │                 │
                    │ 403 Forbidden│  │ Execute business│
                    │ 401 Unauth   │  │ logic, query DB │
                    └──────────────┘  │ Return 200 + data
                                      └─────────────────┘
```

---

## Request Flow with Role Example

### Example 1: Admin Accessing Financial Data ✅

```
┌─────────────────────────┐
│ GET /api/invoices       │
│ Authorization: Bearer.. │
│ (JWT contains role:admin)
└────────────┬────────────┘
             │
             ▼
    ┌────────────────────┐
    │ get_current_user   │
    │ _role()            │
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │ Decode JWT         │
    │ role = "admin"     │
    └────────┬───────────┘
             │
             ▼
    ┌──────────────────────────────┐
    │ @require_permission()         │
    │ checking VIEW_FINANCIAL       │
    └────────┬─────────────────────┘
             │
             ▼
    ┌──────────────────────────────┐
    │ get_permissions("admin")      │
    │ Returns ALL permissions      │
    │ (includes VIEW_FINANCIAL)    │
    └────────┬─────────────────────┘
             │
             ▼
    ┌──────────────────────────────┐
    │ Check: admin has             │
    │ VIEW_FINANCIAL? YES ✅        │
    └────────┬─────────────────────┘
             │
             ▼
    ┌──────────────────────────────┐
    │ Route Handler Executes:      │
    │ • Query invoices from DB     │
    │ • Return 200 OK + data       │
    └──────────────────────────────┘
```

### Example 2: Dispatcher Blocked from Financial Data ❌

```
┌─────────────────────────┐
│ GET /api/invoices       │
│ Authorization: Bearer.. │
│ (JWT contains role:disp)
└────────┬────────────────┘
         │
         ▼
┌────────────────────┐
│ get_current_user   │
│ _role()            │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│ Decode JWT         │
│ role = "dispatcher"│
└────────┬───────────┘
         │
         ▼
┌──────────────────────────────┐
│ @require_permission()         │
│ checking VIEW_FINANCIAL       │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ get_permissions("dispatcher") │
│ Returns OPERATIONAL perms     │
│ (NO VIEW_FINANCIAL)          │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ Check: dispatcher has         │
│ VIEW_FINANCIAL? NO ❌         │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ Raise HTTPException(403)      │
│ {"detail": "Insufficient     │
│  permissions. Required:      │
│  view_financial"}            │
└──────────────────────────────┘
```

---

## Role Hierarchy Tree

```
                    SYSTEM ACCESS
                         │
          ┌──────────────┬┴┬──────────────┐
          │              │ │              │
      ADMIN         MANAGER │        UNAUTH
    Code/Sys       Financial │
    + All Data     + Ops      │
         │              │     │
    Full Suite:    Finance    │
    • Users        Suite:     │
    • Financial    • Invoices │
    • Operational │ • Scorecard
    • Driver      • Reports
    • Config      • Assignments
                  • WST Data
                        │
                   DISPATCHER
                   Operations
                   Suite:
                   • Assignments
                   • Routes
                   • Reports
                   • WST Data
                   (NO Financial)
                        │
                      DRIVER
                   Portal
                   Suite:
                   • Own Schedule
                   • Own Assignments
```

---

## File Interaction Map

```
┌──────────────────────────────────────────────────────────────────┐
│                     API ENDPOINTS                                │
│              (api/src/routes/*.py files)                         │
│  /auth/login, /invoices, /assignments, /reports, etc.           │
└─────────┬────────────────────────────────────────────┬───────────┘
          │ Use decorators from authorization.py      │
          │                                            │ Use User model
          │                                            │ methods
    ┌─────▼──────────────────────────────┐         ┌──▼─────────────┐
    │ authorization.py                  │         │ database.py    │
    ├─────────────────────────────────┬─┤         ├───────────────┤
    │ • get_current_user_role()       │ │         │ • User model  │
    │ • @require_role()               │ │         │ • Role column │
    │ • @require_permission()         │ │         │ • Helper      │
    │ • require_admin()               │ │         │   methods:    │
    │ • require_financial_access()    │ │         │   .has_fin... │
    └─────┬──────────────────────────┬┘ │         │   .can_manage │
          │ Calls helper functions   │  │         └─────┬─────────┘
          │                          │  │               │
    ┌─────▼──────────────────────────▼──▼─────┐        │
    │ permissions.py                          │        │
    ├─────────────────────────────────────────┤        │
    │ • Role enum                             │        │
    │ • Permission enum                       │        │
    │ • ROLE_PERMISSIONS dict                 │        │
    │ • get_permissions()                     │        │
    │ • has_permission()                      │        │
    │ • is_manager_or_admin()                 │        │
    │ • can_access_financial_data()           │        │
    │ • Other helper functions                │        │
    └─────────────────────────────────────────┘        │
          ▲                                             │
          │ References (no dependency)                 │
          └─────────────────────────────────────────────┘
```

---

## Permission Checking Flow (Detailed)

```
REQUEST → /api/endpoint

    ↓

STEP 1: Authentication
    ├─ Extract JWT from Authorization header
    ├─ Decode token
    ├─ Validate signature (if strict)
    └─ Get role from "role" claim

    ↓

STEP 2: Route Decorator Check
    ├─ If @require_role("admin", "manager"):
    │   └─ Is role in ["admin", "manager"]?
    │       ├─ YES → Continue
    │       └─ NO  → 403 Forbidden + return
    │
    └─ If @require_permission(Permission.VIEW_FINANCIAL):
        └─ Call get_permissions(role)
           → Returns Set of all allowed permissions
           └─ Is Permission.VIEW_FINANCIAL in set?
               ├─ YES → Continue
               └─ NO  → 403 Forbidden + return

    ↓

STEP 3: Route Handler Executes
    ├─ Query database
    ├─ Process business logic
    └─ Return 200 OK + response data

    ↓

RESPONSE → 200 OK + data to client
```

---

## Integration Timeline

### Phase 1: Foundation (✅ DONE)
- Created permissions.py with role/permission definitions
- Created authorization.py with decorators and dependencies
- Updated database.py User model
- Documentation completed

### Phase 2: Endpoint Protection (⧗ NEXT)
- Add @require_permission decorators to actual endpoints
- Add JWT role claim to login endpoint
- Test with each role type
- Update frontend to respect permissions

### Phase 3: Hardening (FUTURE)
- Add database-level row-level security (RLS)
- Implement audit logging for sensitive access
- Add rate limiting by role
- Field-level encryption for financial data

---

## Key Design Principles

1. **Separation of Concerns**
   - permissions.py: Role/permission definitions only
   - authorization.py: FastAPI integration only
   - database.py: Data models only
   - routes/*.py: Business logic only

2. **Fail-Safe Defaults**
   - Missing role claim → 401 Unauthorized
   - Invalid role → 401 Unauthorized
   - Missing permission → 403 Forbidden
   - No exceptions granted

3. **Hierarchical Permissions**
   - Admin ⊇ Manager ⊇ Dispatcher ⊇ Driver
   - Each role is strict superset of lower tier
   - Financial access exclusive to Admin/Manager

4. **Explicit Authorization**
   - Every endpoint must explicitly declare access requirements
   - No implicit access based on role
   - Every decorator is visible in code

---

## Deployment Checklist

- [ ] Set JWT_SECRET environment variable
- [ ] Update /auth/login to include role claim in JWT
- [ ] Add @require_permission decorators to financial endpoints
- [ ] Add @require_role decorators to admin endpoints
- [ ] Test login with each role type
- [ ] Verify JWT tokens contain correct role
- [ ] Test endpoint access with each role
- [ ] Verify 403 responses on denied access
- [ ] Check frontend handles permission errors
- [ ] Run security test suite
- [ ] Document any custom roles or exceptions
- [ ] Plan audit logging implementation

---

See [ROLE_HIERARCHY_IMPLEMENTATION.md](ROLE_HIERARCHY_IMPLEMENTATION.md) for complete technical details.
