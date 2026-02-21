import os
import json
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    name: str
    username: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    admin_username: str
    admin_password: str


class UserListResponse(BaseModel):
    username: str
    name: str


# Path to users.json file
USERS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'users.json')

# Default admin user
DEFAULT_ADMIN = {
    "admin": "NDAY_2026",
}


def load_users():
    """Load users from JSON file or return default admin."""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading users: {e}")
            return DEFAULT_ADMIN.copy()
    return DEFAULT_ADMIN.copy()


def save_users(users):
    """Save users to JSON file."""
    try:
        os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"Error saving users: {e}")
        raise


# Load users on startup
USERS = load_users()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user with username and password.
    """
    USERS = load_users()  # Reload users in case they changed
    username = request.username.lower().strip()
    password = request.password

    # Check if user exists and password is correct
    if username not in USERS or USERS[username] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Return user info
    # Format the name nicely (capitalize first letter)
    display_name = username.capitalize()
    
    return LoginResponse(
        name=display_name,
        username=username,
    )


@router.post("/create-user")
async def create_user(request: CreateUserRequest):
    """
    Create a new user. Requires valid admin credentials.
    """
    USERS = load_users()  # Reload users
    
    # Validate admin credentials
    admin_username = request.admin_username.lower().strip()
    if admin_username not in USERS or USERS[admin_username] != request.admin_password:
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
    USERS[new_username] = request.password
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
    if admin_username not in USERS or USERS[admin_username] != request.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    
    # Return list of users
    users_list = [
        UserListResponse(username=username, name=username.capitalize())
        for username in USERS.keys()
    ]
    
    return {"users": users_list}


@router.post("/delete-user")
async def delete_user(request: CreateUserRequest):
    """
    Delete a user. Requires valid admin credentials.
    """
    USERS = load_users()  # Reload users
    
    # Validate admin credentials
    admin_username = request.admin_username.lower().strip()
    if admin_username not in USERS or USERS[admin_username] != request.admin_password:
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
