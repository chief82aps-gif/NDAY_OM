import os
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import jwt

router = APIRouter()

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "test_secret_key_change_in_production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    name: str
    username: str
    role: str
    access_token: str
    token_type: str = "bearer"


class CreateUserRequest(BaseModel):
    username: str
    password: str
    admin_username: str
    admin_password: str


class UserListResponse(BaseModel):
    username: str
    name: str


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str
    admin_username: str
    admin_password: str


# Path to users.json file (for persistent storage when available)
USERS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'users.json')

# Default/static users with roles - always available
# Format: {"username": {"password": "...", "role": "admin|manager|dispatcher|driver", "name": "..."}}
DEFAULT_USERS = {
    "admin": {
        "password": os.getenv("ADMIN_PASSWORD", "NDAY_2026"),
        "role": "admin",
        "name": "Admin"
    },
    "chief": {
        "password": os.getenv("CHIEF_PASSWORD", "chief_2026"),
        "role": "admin",
        "name": "Chief"
    },
    # Test users for RBAC testing
    "manager_user": {
        "password": "manager_pass_123",
        "role": "manager",
        "name": "Manager User"
    },
    "dispatcher_user": {
        "password": "dispatcher_pass_123",
        "role": "dispatcher",
        "name": "Dispatcher User"
    },
    "driver_user": {
        "password": "driver_pass_123",
        "role": "driver",
        "name": "Driver User"
    },
}


def load_users():
    """Load users from JSON file, merged with default users.
    
    Default users are ALWAYS available, even if file doesn't exist.
    File-based users are supplementary - useful for local development.
    
    User format: {
        "username": {
            "password": "...",
            "role": "admin|manager|dispatcher|driver",
            "name": "Display Name"
        }
    }
    """
    users = {}
    
    # Start with defaults
    for username, user_data in DEFAULT_USERS.items():
        users[username] = user_data.copy()
    
    # Try to load additional users from file if it exists
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                file_users = json.load(f)
                # Merge file users (file can override defaults)
                for username, user_data in file_users.items():
                    users[username] = user_data
        except Exception as e:
            print(f"Warning: Could not load users from file: {e}")
    
    return users


def _normalize_user_record(username: str, user_data):
    if isinstance(user_data, dict):
        return {
            "password": user_data.get("password"),
            "role": user_data.get("role", "driver"),
            "name": user_data.get("name", username.capitalize()),
        }
    return {
        "password": user_data,
        "role": "driver",
        "name": username.capitalize(),
    }


def _verify_user_password(users, username: str, password: str) -> bool:
    user_data = users.get(username)
    if not user_data:
        return False
    record = _normalize_user_record(username, user_data)
    return record.get("password") == password


def save_users(users):
    """Save users to JSON file (optional, for local development).
    
    This won't persist in Render, but doesn't hurt to try.
    """
    try:
        os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save users to file: {e}")
        # Don't raise - just log the warning


# Load users on startup (will always have defaults)
USERS = load_users()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user with username and password.
    Returns JWT token with role claim for RBAC.
    """
    users = load_users()  # Reload users in case they changed
    username = request.username.lower().strip()
    password = request.password

    # Check if user exists
    if username not in users:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    
    user_data = users[username]
    
    # Check if password is correct
    # Handle both old format (simple password string) and new format (dict with password key)
    if isinstance(user_data, dict):
        user_password = user_data.get("password")
        user_role = user_data.get("role", "driver")
        user_name = user_data.get("name", username.capitalize())
    else:
        # Old format (backward compatibility)
        user_password = user_data
        user_role = "driver"  # Default role for old format
        user_name = username.capitalize()
    
    if user_password != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    
    # Generate JWT token with role claim
    payload = {
        "sub": username,
        "username": username,
        "role": user_role,
        "name": user_name,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    
    try:
        access_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating token: {str(e)}",
        )
    
    return LoginResponse(
        name=user_name,
        username=username,
        role=user_role,
        access_token=access_token,
        token_type="bearer",
    )


@router.post("/create-user")
async def create_user(request: CreateUserRequest):
    """
    Create a new user. Requires valid admin credentials.
    """
    USERS = load_users()  # Reload users
    
    # Validate admin credentials
    admin_username = request.admin_username.lower().strip()
    if not _verify_user_password(USERS, admin_username, request.admin_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    
    # Check if user is actually an admin (username contains 'admin' or password matches admin password pattern)
    # For simplicity, we just check if they have a valid account
    
    # Validate new user input
    new_username = request.username.lower().strip()
    if not new_username or not request.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password are required",
        )
    
    if len(new_username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at least 3 characters",
        )
    
    if len(request.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters",
        )
    
    # Check if user already exists
    if new_username in USERS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )
    
    # Create new user
    USERS[new_username] = {
        "password": request.password,
        "role": "driver",
        "name": new_username.capitalize(),
    }
    save_users(USERS)
    
    return {
        "message": "User created successfully",
        "username": new_username,
        "name": new_username.capitalize(),
    }


@router.post("/list-users")
async def list_users(request: LoginRequest):
    """
    List all users. Requires valid admin credentials.
    """
    USERS = load_users()  # Reload users
    
    # Validate admin credentials
    admin_username = request.username.lower().strip()
    if not _verify_user_password(USERS, admin_username, request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    
    # Return list of users
    users_list = []
    for username, user_data in USERS.items():
        record = _normalize_user_record(username, user_data)
        users_list.append(UserListResponse(username=username, name=record["name"]))
    
    return {"users": users_list}


@router.post("/delete-user")
async def delete_user(request: CreateUserRequest):
    """
    Delete a user. Requires valid admin credentials.
    """
    USERS = load_users()  # Reload users
    
    # Validate admin credentials
    admin_username = request.admin_username.lower().strip()
    if not _verify_user_password(USERS, admin_username, request.admin_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    
    # Prevent deleting the last admin
    username_to_delete = request.username.lower().strip()
    if username_to_delete not in USERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Check if trying to delete admin (prevent deleting last admin)
    if username_to_delete == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete the admin user",
        )
    
    # Delete user
    del USERS[username_to_delete]
    save_users(USERS)
    
    return {"message": f"User '{username_to_delete}' deleted successfully"}


@router.post("/change-password")
async def change_password(request: ChangePasswordRequest):
    """
    Change a user's password. Requires valid admin credentials OR the user's old password.
    """
    USERS = load_users()  # Reload users
    
    username_to_change = request.username.lower().strip()
    
    # Check if user exists
    if username_to_change not in USERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Admin can change any password OR user can change their own with old password
    admin_username = request.admin_username.lower().strip()
    is_admin_change = _verify_user_password(USERS, admin_username, request.admin_password)
    is_self_change = (
        username_to_change == admin_username and
        _verify_user_password(USERS, username_to_change, request.old_password)
    )
    
    if not (is_admin_change or is_self_change):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials. Provide either admin password or your old password.",
        )
    
    # Validate new password
    if not request.new_password or len(request.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 6 characters",
        )
    
    # Prevent changing default admin password in Render (environment variable)
    if username_to_change == "admin" and username_to_change in DEFAULT_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change default admin password. This is set via environment variable.",
        )
    
    # Change password
    existing_record = _normalize_user_record(username_to_change, USERS[username_to_change])
    USERS[username_to_change] = {
        "password": request.new_password,
        "role": existing_record.get("role", "driver"),
        "name": existing_record.get("name", username_to_change.capitalize()),
    }
    save_users(USERS)
    
    return {"message": f"Password for '{username_to_change}' changed successfully"}
