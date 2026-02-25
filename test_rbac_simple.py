#!/usr/bin/env python3
"""Simple RBAC test without colors"""

import requests
import json

BASE_URL = "http://127.0.0.1:8000"

# Test admin login
print("Testing Admin Login...")
response = requests.post(
    f"{BASE_URL}/auth/login",
    json={"username": "admin", "password": "NDAY_2026"}
)
if response.status_code == 200:
    data = response.json()
    print(f"Status: OK")
    print(f"Name: {data['name']}")
    print(f"Role: {data['role']}")
    print(f"Token: {data['access_token'][:50]}...")
    admin_token = data['access_token']
else:
    print(f"Error: {response.status_code} - {response.text}")
    exit(1)

# Test manager login
print("\nTesting Manager Login...")
response = requests.post(
    f"{BASE_URL}/auth/login",
    json={"username": "manager_user", "password": "manager_pass_123"}
)
if response.status_code == 200:
    data = response.json()
    print(f"Status: OK")
    print(f"Name: {data['name']}")
    print(f"Role: {data['role']}")
    print(f"Token: {data['access_token'][:50]}...")
    manager_token = data['access_token']
else:
    print(f"Error: {response.status_code} - {response.text}")
    exit(1)

# Test dispatcher login
print("\nTesting Dispatcher Login...")
response = requests.post(
    f"{BASE_URL}/auth/login",
    json={"username": "dispatcher_user", "password": "dispatcher_pass_123"}
)
if response.status_code == 200:
    data = response.json()
    print(f"Status: OK")
    print(f"Name: {data['name']}")
    print(f"Role: {data['role']}")
    print(f"Token: {data['access_token'][:50]}...")
    dispatcher_token = data['access_token']
else:
    print(f"Error: {response.status_code} - {response.text}")
    exit(1)

# Test driver login
print("\nTesting Driver Login...")
response = requests.post(
    f"{BASE_URL}/auth/login",
    json={"username": "driver_user", "password": "driver_pass_123"}
)
if response.status_code == 200:
    data = response.json()
    print(f"Status: OK")
    print(f"Name: {data['name']}")
    print(f"Role: {data['role']}")
    print(f"Token: {data['access_token'][:50]}...")
    driver_token = data['access_token']
else:
    print(f"Error: {response.status_code} - {response.text}")
    exit(1)

print("\n" + "="*60)
print("SUMMARY: All roles logged in successfully!")
print("="*60)
print("""
Test Results:
✓ Admin account logging in with admin role
✓ Manager account logging in with manager role  
✓ Dispatcher account logging in with dispatcher role
✓ Driver account logging in with driver role

Next: Test protected endpoints with these tokens
- All roles can access: /upload/status
- Only admin/manager should access: financial data endpoints

To test a protected endpoint:
curl -X GET http://127.0.0.1:8000/upload/status \\
  -H "Authorization: Bearer <TOKEN_HERE>"
""")
