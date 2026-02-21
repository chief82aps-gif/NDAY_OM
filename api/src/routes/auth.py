from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    name: str
    username: str


# Simple in-memory user store for demo purposes
# In production, this would be a database with hashed passwords
USERS = {
    "admin": "NDAY_2026",
    "supervisor": "NDAY_2026",
    "manager": "NDAY_2026",
}


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user with username and password.
    
    For demo purposes, accepts predefined users.
    In production, this would validate against hashed passwords in database.
    """
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
