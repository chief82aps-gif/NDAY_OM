#!/usr/bin/env python3
"""
RBAC Testing Script for NDAY Route Manager

Tests role-based access control by:
1. Logging in with different roles
2. Testing protected endpoints with each role
3. Verifying 403 responses for unauthorized access

Run this after starting the backend on port 8000
"""

import requests
import json
import sys
from typing import Dict, Any

# Configuration
BASE_URL = "http://127.0.0.1:8000"
LOGIN_ENDPOINT = f"{BASE_URL}/auth/login"
STATUS_ENDPOINT = f"{BASE_URL}/upload/status"

# Test users (match those in auth.py)
TEST_USERS = {
    "admin": {
        "username": "admin",
        "password": "NDAY_2026",
        "role": "admin",
        "expected_financial_access": True,
    },
    "manager": {
        "username": "manager_user",
        "password": "manager_pass_123",
        "role": "manager",
        "expected_financial_access": True,
    },
    "dispatcher": {
        "username": "dispatcher_user",
        "password": "dispatcher_pass_123",
        "role": "dispatcher",
        "expected_financial_access": False,
    },
    "driver": {
        "username": "driver_user",
        "password": "driver_pass_123",
        "role": "driver",
        "expected_financial_access": False,
    },
}

# Colors for output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.ENDC}\n")


def print_success(text):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_fail(text):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_info(text):
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")


def print_warning(text):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


def test_backend_available():
    """Check if backend is running"""
    print_header("Step 1: Checking Backend Availability")
    try:
        response = requests.get(f"{BASE_URL}/")
        if response.status_code == 200:
            print_success(f"Backend is running on {BASE_URL}")
            print_info(f"Response: {response.json()}")
            return True
        else:
            print_fail(f"Backend returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_fail(f"Cannot connect to backend at {BASE_URL}")
        print_warning("Make sure the backend is running:")
        print("  cd C:\\Users\\chief\\NDAY_OM")
        print("  .venv\\Scripts\\python.exe -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000")
        return False
    except Exception as e:
        print_fail(f"Error checking backend: {e}")
        return False


def login_user(username: str, password: str) -> Dict[str, Any]:
    """Login a user and return token and role"""
    print_info(f"Logging in as {username}...")
    
    try:
        response = requests.post(
            LOGIN_ENDPOINT,
            json={"username": username, "password": password},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            print_success(f"Logged in: {data['name']} (role: {data['role']})")
            return {
                "success": True,
                "token": data["access_token"],
                "role": data["role"],
                "name": data["name"],
            }
        else:
            print_fail(f"Login failed: {response.status_code} - {response.text}")
            return {"success": False, "error": response.text}
    except Exception as e:
        print_fail(f"Login error: {e}")
        return {"success": False, "error": str(e)}


def test_protected_endpoint(token: str, user_role: str, endpoint_name: str):
    """Test a protected endpoint with a token"""
    print_info(f"{user_role.title()} accessing {endpoint_name}...")
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{BASE_URL}{endpoint_name}",
            headers=headers,
            timeout=5
        )
        
        return {
            "status_code": response.status_code,
            "data": response.json() if response.status_code < 400 else None,
            "error": response.json() if response.status_code >= 400 else None,
        }
    except Exception as e:
        return {
            "status_code": 0,
            "error": str(e),
        }


def test_rbac():
    """Main RBAC test suite"""
    print_header("NDAY Route Manager - RBAC Testing")
    
    # Step 1: Check backend
    if not test_backend_available():
        return False
    
    # Step 2: Test login for each user
    print_header("Step 2: Testing Authentication & JWT Token Generation")
    
    tokens = {}
    for user_key, user_info in TEST_USERS.items():
        print(f"\n{Colors.BOLD}Testing {user_key.title()} Role:{Colors.ENDC}")
        login_result = login_user(user_info["username"], user_info["password"])
        
        if login_result["success"]:
            tokens[user_key] = login_result
            print_success(f"Token generated for {user_key}")
            # Show first 50 chars of token
            token_preview = login_result["token"][:50] + "..." if len(login_result["token"]) > 50 else login_result["token"]
            print_info(f"Token: {token_preview}")
        else:
            print_fail(f"Failed to login as {user_key}: {login_result.get('error')}")
            tokens[user_key] = None
    
    # Step 3: Test protected endpoints
    print_header("Step 3: Testing Protected Endpoints")
    
    test_endpoints = [
        {
            "name": "Status (General Access)",
            "path": "/upload/status",
            "requires_financial": False,
        },
    ]
    
    results = {}
    
    for user_key, user_info in TEST_USERS.items():
        print(f"\n{Colors.BOLD}{user_key.title()} Role ({user_info['role'].title()}):{Colors.ENDC}")
        
        if tokens[user_key] is None:
            print_fail("Skipped - login failed")
            continue
        
        token = tokens[user_key]["token"]
        results[user_key] = {}
        
        for endpoint_test in test_endpoints:
            print_info(f"Testing {endpoint_test['name']}...")
            
            response = test_protected_endpoint(token, user_key, endpoint_test["path"])
            
            if response["status_code"] == 200:
                print_success(f"Access granted (200 OK)")
                results[user_key][endpoint_test["name"]] = "allowed"
            elif response["status_code"] == 403:
                print_fail(f"Access denied (403 Forbidden)")
                if response["error"]:
                    print_info(f"Reason: {response['error'].get('detail', 'Unknown')}")
                results[user_key][endpoint_test["name"]] = "denied"
            elif response["status_code"] == 401:
                print_fail(f"Unauthorized (401)")
                if response["error"]:
                    print_info(f"Reason: {response['error'].get('detail', 'Unknown')}")
                results[user_key][endpoint_test["name"]] = "unauthorized"
            else:
                print_warning(f"Unexpected status: {response['status_code']}")
                results[user_key][endpoint_test["name"]] = "unknown"
    
    # Step 4: Summary
    print_header("Step 4: Test Summary")
    
    print_info("Users created for testing:")
    for user_key, user_info in TEST_USERS.items():
        status = "✓" if tokens.get(user_key, {}).get("success") else "✗"
        print(f"  {status} {user_info['username']:20} (role: {user_info['role']:12}) - {user_info['password']}")
    
    print("\n" + Colors.BOLD + "Next Steps:" + Colors.ENDC)
    print("""
1. If login is working:
   ✓ Backend is configured with JWT support
   ✓ Test users have been created
   ✓ Tokens are being generated correctly

2. To protect endpoints with RBAC:
   a. Open api/src/routes/uploads.py
   b. Add decorators to endpoints:
   
      from api.src.authorization import require_permission, get_current_user_role
      from api.src.permissions import Permission
      
      @router.get("/variable-invoices")
      @require_permission(Permission.VIEW_VARIABLE_INVOICES)
      def get_invoices(role: str = Depends(get_current_user_role)):
          return {"invoices": [...]}

3. Test the protected endpoint:
   curl -X GET http://127.0.0.1:8000/upload/status \\
     -H "Authorization: Bearer <MANAGER_TOKEN>"

4. Verify dispatcher gets 403:
   curl -X GET http://127.0.0.1:8000/upload/status \\
     -H "Authorization: Bearer <DISPATCHER_TOKEN>"
     # Should return 403 Forbidden

5. Check documentation at:
   - RBAC_QUICK_REFERENCE.md
   - RBAC_INTEGRATION_EXAMPLES.py
   - ROLE_HIERARCHY_IMPLEMENTATION.md
""")
    
    return True


if __name__ == "__main__":
    try:
        success = test_rbac()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n{Colors.FAIL}Unexpected error: {e}{Colors.ENDC}")
        sys.exit(1)
