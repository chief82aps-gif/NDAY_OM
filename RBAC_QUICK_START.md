# RBAC Local Testing - Quick Start Guide

## Your RBAC System is Live! 🎉

Backend running on: **http://127.0.0.1:8000**

---

## Test It Right Now (Ctrl+C to copy, Ctrl+Z to paste)

### 1️⃣ Get Admin Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"NDAY_2026\"}"
```
**Copy the `access_token` value from response**

### 2️⃣ Get Manager Token (NEW!)
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"manager_user\",\"password\":\"manager_pass_123\"}"
```
**Copy the `access_token` value**

### 3️⃣ Get Dispatcher Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"dispatcher_user\",\"password\":\"dispatcher_pass_123\"}"
```
**Copy the `access_token` value**

### 4️⃣ Get Driver Token
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"driver_user\",\"password\":\"driver_pass_123\"}"
```
**Copy the `access_token` value**

### 5️⃣ Test with Tokens
```bash
# Replace YOUR_TOKEN_HERE with the token you copied above

curl -X GET http://127.0.0.1:8000/upload/status \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

All roles can access this endpoint. Next, we'll add protected endpoints.

---

## Test Users Available

Ready to use these accounts:

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `NDAY_2026` |
| Manager ⭐ | `manager_user` | `manager_pass_123` |
| Dispatcher | `dispatcher_user` | `dispatcher_pass_123` |
| Driver | `driver_user` | `driver_pass_123` |

---

## What Each Role Can Access

### 👑 Admin
- ✅ Everything (system, financial, operations)
- ✅ Code editing
- ✅ User management

### 💼 Manager (NEW - Requested by You)
- ✅ All financial data (invoices, incentives, scorecards)
- ✅ Operational data (assignments, reports)
- ❌ No code editing
- ❌ No user management

### 📋 Dispatcher
- ✅ Operational data (assignments, routes)
- ❌ No financial data
- ❌ No code editing

### 👤 Driver
- ✅ Own assignments & schedule only
- ❌ No financial data
- ❌ No operational management

---

## Next: Protect Your First Endpoint

### Step 1: Open `api/src/routes/uploads.py`

### Step 2: Find any endpoint to protect (example: `/download-schedule-report`)

### Step 3: Add imports at top of file
```python
from api.src.authorization import require_permission, get_current_user_role
from api.src.permissions import Permission
```

### Step 4: Add decorator
```python
@router.get("/download-schedule-report")
@require_permission(Permission.VIEW_REPORTS)  # <- Add this line
def download_schedule_report(role: str = Depends(get_current_user_role)):  # <- Add 'role' parameter
    # ... existing code ...
```

### Step 5: Save and test
```bash
# All roles can access (VIEW_REPORTS for all)
curl -X GET http://127.0.0.1:8000/upload/download-schedule-report \
  -H "Authorization: Bearer <ANY_TOKEN>"
```

---

## Protecting Financial Data (Manager & Admin Only)

Example: Protect an invoices endpoint

```python
from api.src.permissions import Permission
from api.src.authorization import require_permission, get_current_user_role

@router.get("/variable-invoices")
@require_permission(Permission.VIEW_VARIABLE_INVOICES)  # <- Add this
def get_invoices(role: str = Depends(get_current_user_role)):  # <- Add 'role' param
    # ... return invoices ...
```

Now test:
```bash
# Manager (WORKS)
curl -X GET http://127.0.0.1:8000/upload/variable-invoices \
  -H "Authorization: Bearer <MANAGER_TOKEN>"
# Returns: 200 OK + data

# Dispatcher (FAILS)
curl -X GET http://127.0.0.1:8000/upload/variable-invoices \
  -H "Authorization: Bearer <DISPATCHER_TOKEN>"
# Returns: 403 Forbidden (Insufficient permissions)
```

---

## Available Permissions to Use

For **Financial Data** (Admin & Manager only):
- `Permission.VIEW_FINANCIAL` - All financial data
- `Permission.VIEW_VARIABLE_INVOICES` - Amazon invoices
- `Permission.VIEW_WEEKLY_INCENTIVES` - Driver incentives
- `Permission.VIEW_FLEET_INVOICES` - Fleet costs
- `Permission.VIEW_DSP_SCORECARD` - Partner scorecards
- `Permission.VIEW_POD_REPORTS` - Delivery reports

For **Operational Data** (Admin, Manager, Dispatcher):
- `Permission.VIEW_REPORTS` - All reports
- `Permission.VIEW_WST_DATA` - Work summary tracking
- `Permission.MANAGE_ASSIGNMENTS` - Vehicle assignments

For **Admin Only**:
- `Permission.MANAGE_USERS` - Create/delete users
- `Permission.MANAGE_SYSTEM` - System configuration

---

## Run Test Script (Optional)

For automated testing:
```bash
.\.venv\Scripts\python.exe test_rbac_simple.py
```

This tests all user logins and token generation.

---

## Troubleshooting

**Backend not responding?**
```bash
# Check if running
curl http://127.0.0.1:8000/

# If not, start it
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

**Token not working?**
- Make sure you copied the full token
- Include the `Bearer ` prefix: `Authorization: Bearer <token>`
- Check the role in the token matches what you expect

**Import errors?**
- PyJWT is installed: `pip install PyJWT==2.11.0`
- Authorization module exists: `api/src/authorization.py`
- Permissions module exists: `api/src/permissions.py`

---

## PowerShell Testing (One-Liner)

Get a token and test in one command:

```powershell
$login = @{username="manager_user"; password="manager_pass_123"} | ConvertTo-Json;
$token = (Invoke-WebRequest -Uri http://127.0.0.1:8000/auth/login -Method Post -ContentType "application/json" -Body $login).Content | ConvertFrom-Json | Select -ExpandProperty access_token;
Invoke-WebRequest -Uri http://127.0.0.1:8000/upload/status -Headers @{Authorization="Bearer $token"} | ConvertFrom-Json
```

---

## How the System Works

1. **User logs in** → Posts username/password to `/auth/login`
2. **Get JWT token** → Token includes role claim (admin, manager, dispatcher, driver)
3. **Use token** → Send in `Authorization: Bearer <token>` header
4. **Check permission** → Endpoint decorator checks if role has permission
5. **Grant/Deny** → Returns 200 OK (allowed) or 403 Forbidden (denied)

---

## Files You Modified

- ✅ `api/src/routes/auth.py` - JWT token generation
- ✅ `api/src/permissions.py` - Role/permission definitions (NEW)
- ✅ `api/src/authorization.py` - Endpoint decorators (NEW)
- ✅ `api/users.json` - User format update
- ✅ `api/requirements.txt` - Added PyJWT

---

## Documentation

Detailed docs available:
- [RBAC_QUICK_REFERENCE.md](RBAC_QUICK_REFERENCE.md) - One-page reference
- [RBAC_LOCAL_TESTING_REPORT.md](RBAC_LOCAL_TESTING_REPORT.md) - Full testing guide
- [RBAC_INTEGRATION_EXAMPLES.py](api/src/routes/RBAC_INTEGRATION_EXAMPLES.py) - Code patterns
- [ROLE_HIERARCHY_IMPLEMENTATION.md](ROLE_HIERARCHY_IMPLEMENTATION.md) - Complete details

---

## You're All Set! ✅

Your RBAC system is:
- ✅ Running locally on port 8000
- ✅ Generating JWT tokens with roles
- ✅ Ready for endpoint protection
- ✅ Tested with 4 different roles

**Next**: Add decorators to your endpoints and test!

Questions? Check the documentation files listed above.
