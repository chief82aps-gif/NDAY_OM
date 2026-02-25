# RBAC System - Local Testing Report

**Date**: February 24, 2026  
**Environment**: Local Development (127.0.0.1:8000)  
**Status**: ✅ **WORKING & READY FOR TESTING**

---

## Test Results Summary

### ✅ Phase 1: Backend & JWT Authentication
- [x] Backend running on http://127.0.0.1:8000
- [x] JWT token generation implemented
- [x] Role claims properly added to tokens
- [x] All test users authenticate successfully

### ✅ Phase 2: User Role Verification
- [x] **Admin**: Logging in with `admin` role ✓
- [x] **Manager**: Logging in with `manager` role ✓ (NEW)
- [x] **Dispatcher**: Logging in with `dispatcher` role ✓
- [x] **Driver**: Logging in with `driver` role ✓

### Test User Credentials

| Username | Password | Role | Type |
|----------|----------|------|------|
| `admin` | `NDAY_2026` | admin | Built-in |
| `chief` | `chief_2026` | admin | Built-in |
| `manager_user` | `manager_pass_123` | manager | Test (NEW) |
| `dispatcher_user` | `dispatcher_pass_123` | dispatcher | Test |
| `driver_user` | `driver_pass_123` | driver | Test |
| `test` | `testpass123` | dispatcher | Legacy |
| `jefe` | `GoRRRRRRRRR` | dispatcher | Legacy |
| `dylan` | `IndianaBears` | dispatcher | Legacy |
| `tam` | `HotGrammy` | driver | Legacy |
| `galo` | `Paperwork26` | dispatcher | Legacy |
| `spencer` | `BelCanto` | driver | Legacy |

---

## Manual Testing Steps

### Step 1: Get Admin Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"NDAY_2026"}'

# Response:
{
  "name": "Admin",
  "username": "admin",
  "role": "admin",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Step 2: Get Manager Token (NEW)
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"manager_user","password":"manager_pass_123"}'

# Response:
{
  "name": "Manager User",
  "username": "manager_user",
  "role": "manager",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Step 3: Get Dispatcher Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"dispatcher_user","password":"dispatcher_pass_123"}'

# Response:
{
  "name": "Dispatcher User",
  "username": "dispatcher_user",
  "role": "dispatcher",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Step 4: Get Driver Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"driver_user","password":"driver_pass_123"}'

# Response:
{
  "name": "Driver User",
  "username": "driver_user",
  "role": "driver",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Step 5: Test Unprotected Endpoint (All Roles)
```bash
# All roles should be able to access /upload/status
curl -X GET http://127.0.0.1:8000/upload/status \
  -H "Authorization: Bearer <TOKEN_FROM_ABOVE>"

# Expected: 200 OK
```

---

## Next: Testing Protected Endpoints

Once you add `@require_permission()` decorators to your endpoints, test like this:

### Example: Financial Data Endpoint (Admin & Manager Only)

```bash
# 1. Admin accessing financial data (SHOULD SUCCEED)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
# Expected: 200 OK + financial data

# 2. Manager accessing financial data (SHOULD SUCCEED)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <MANAGER_TOKEN>"
# Expected: 200 OK + financial data

# 3. Dispatcher accessing financial data (SHOULD FAIL)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <DISPATCHER_TOKEN>"
# Expected: 403 Forbidden
# {"detail": "Insufficient permissions. Required: view_variable_invoices"}

# 4. Driver accessing financial data (SHOULD FAIL)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <DRIVER_TOKEN>"
# Expected: 403 Forbidden
# {"detail": "Insufficient permissions. Required: view_variable_invoices"}
```

---

## Backend Status

### Currently Running On
- **URL**: http://127.0.0.1:8000
- **Port**: 8000
- **Mode**: Development with `--reload`
- **Status**: ✅ Running

### To Start Backend (if stopped)
```bash
cd C:\Users\chief\NDAY_OM
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

### To Stop Backend
```powershell
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid in $pids) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}
```

---

## Files Modified for RBAC Testing

### 1. `api/src/routes/auth.py`
**Changes**:
- Added JWT token generation with role claims
- Updated LoginResponse to include `role` and `access_token`
- Modified DEFAULT_USERS to use new format with role field
- Updated load_users() to handle both old and new formats
- Modified login endpoint to generate JWT tokens

**Features**:
- Tokens expire after 24 hours
- Token includes: sub, username, role, name, exp, iat
- Algorithm: HS256
- Secret: `JWT_SECRET` environment variable (defaults to "test_secret_key_change_in_production")

### 2. `api/users.json`
**Changes**:
- Converted from old format (username: password) to new format (username: {password, role, name})
- Added role assignments for existing users
- Backward compatible with load_users() function

### 3. `api/requirements.txt`
**Changes**:
- Added `PyJWT==2.11.0` for JWT token generation and validation

---

## Test Scripts Available

### 1. `test_rbac_simple.py` ✅ **RECOMMENDED**
Simple test without color output (Windows-compatible)
```bash
.\.venv\Scripts\python.exe test_rbac_simple.py
```
**Purpose**: Verify all roles can login and get JWT tokens

### 2. `test_rbac.py`
Full test with detailed output (may have encoding issues on Windows PowerShell)
```bash
.\.venv\Scripts\python.exe test_rbac.py
```
**Purpose**: Complete RBAC testing including endpoint access

---

## JWT Token Format

All login responses now include an `access_token`:

```json
{
  "name": "Manager User",
  "username": "manager_user",
  "role": "manager",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtYW5hZ2VyX3VzZXIiLCJ1c2VybmFtZSI6Im1hbmFnZXJfdXNlciIsInJvbGUiOiJtYW5hZ2VyIiwibmFtZSI6Ik1hbmFnZXIgVXNlciIsImV4cCI6MTcwODc3OTIwMCwiaWF0IjoxNzA4NjkyODAwfQ.XYZ...",
  "token_type": "bearer"
}
```

**Token Contents** (decoded):
```json
{
  "sub": "manager_user",
  "username": "manager_user",
  "role": "manager",
  "name": "Manager User",
  "exp": 1708779200,
  "iat": 1708692800
}
```

---

## Using Tokens in Requests

All protected endpoints expect an Authorization header:

```bash
Authorization: Bearer <access_token_from_login>
```

**Example**:
```bash
curl -X GET http://127.0.0.1:8000/upload/status \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## Next Steps: Adding Endpoint Protection

To protect an endpoint with RBAC:

### 1. Open the route file
```bash
code api/src/routes/uploads.py
```

### 2. Add imports
```python
from api.src.authorization import require_permission, get_current_user_role
from api.src.permissions import Permission
```

### 3. Add decorator and dependency
```python
@router.get("/variable-invoices")
@require_permission(Permission.VIEW_VARIABLE_INVOICES)
async def get_invoices(role: str = Depends(get_current_user_role)):
    # This endpoint now requires VIEW_VARIABLE_INVOICES permission
    # Only admin and manager have this permission
    return {"invoices": [...]}
```

### 4. Test the protected endpoint
```bash
# Manager (should work)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <MANAGER_TOKEN>"

# Dispatcher (should fail with 403)
curl -X GET http://127.0.0.1:8000/api/variable-invoices \
  -H "Authorization: Bearer <DISPATCHER_TOKEN>"
```

---

## Endpoints to Protect (Recommendation)

Financial data endpoints (Manager & Admin only):
- `/upload/variable-invoices` or `/api/variable-invoices`
- `/upload/weekly-incentives` or `/api/weekly-incentives`
- `/upload/fleet-invoices` or `/api/fleet-invoices`
- `/upload/dsp-scorecard` or `/api/dsp-scorecard`
- `/upload/pod-reports` or `/api/pod-reports`

Use:
```python
@require_permission(Permission.VIEW_VARIABLE_INVOICES)
@require_permission(Permission.VIEW_WEEKLY_INCENTIVES)
@require_permission(Permission.VIEW_FLEET_INVOICES)
@require_permission(Permission.VIEW_DSP_SCORECARD)
@require_permission(Permission.VIEW_POD_REPORTS)
```

Operational endpoints (Dispatcher and Admin/Manager):
- `/upload/assign-vehicles`
- `/upload/status`
- `/upload/driver-schedule-summary`

Use:
```python
@require_permission(Permission.MANAGE_ASSIGNMENTS)  # assign-vehicles
# Status is likely fine for all roles
```

---

## Environment Variables

If you want to customize JWT settings:

```bash
export JWT_SECRET="your_secret_key_here"
export ADMIN_PASSWORD="custom_admin_pass"
export CHIEF_PASSWORD="custom_chief_pass"
```

Or set in `.env.development`:
```
JWT_SECRET=your_secret_key_here
ADMIN_PASSWORD=custom_admin_pass
CHIEF_PASSWORD=custom_chief_pass
```

---

## Troubleshooting

### Backend not starting?
```bash
# Check if port 8000 is in use
netstat -ano | findstr :8000

# Kill the process if needed
Stop-Process -Id <PID> -Force

# Then start again
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

### JWT import error?
PyJWT is now installed. If still having issues:
```bash
.\.venv\Scripts\pip.exe install PyJWT==2.11.0
```

### Users not logging in?
Check:
1. `api/users.json` exists and has proper format
2. Username/password matches (case-sensitive passwords, case-insensitive usernames)
3. DEFAULT_USERS in auth.py includes the user

### Token validation issues?
- Tokens must be in `Authorization: Bearer <token>` header
- Role claim must be present in token
- Token must not be expired

---

## Documentation References

For more information, see:
- [RBAC_QUICK_REFERENCE.md](../RBAC_QUICK_REFERENCE.md)
- [RBAC_INTEGRATION_EXAMPLES.py](../api/src/routes/RBAC_INTEGRATION_EXAMPLES.py)
- [ROLE_HIERARCHY_IMPLEMENTATION.md](../ROLE_HIERARCHY_IMPLEMENTATION.md)
- [RBAC_ARCHITECTURE_GUIDE.md](../RBAC_ARCHITECTURE_GUIDE.md)

---

## Summary

✅ **RBAC System is Ready for Local Testing**

- JWT authentication implemented with role claims
- All test users can login and receive tokens
- Backend running on http://127.0.0.1:8000
- Test scripts available for verification
- Ready to add endpoint protection decorators

**Next Action**: Add `@require_permission()` decorators to financial data endpoints and test with different roles.

---

**Created**: February 24, 2026  
**Status**: ✅ Local Testing Verified  
**Tested**: Admin, Manager, Dispatcher, Driver roles all working
