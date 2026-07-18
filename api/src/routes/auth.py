import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt
from passlib.context import CryptContext

from api.src.database import get_db, User, get_user_by_username, get_user_by_reset_token

router = APIRouter()

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "test_secret_key_change_in_production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")
INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=24)

# Sentinel password_hash for an account that's been invited but hasn't set
# its own password yet — never matches any real password through
# verify_password(), so an invited-but-not-activated account simply can't
# log in until it completes the set-password link.
PENDING_PASSWORD_HASH = "!pending-invite!"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or password_hash == PENDING_PASSWORD_HASH:
        return False
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# One-time seed — this app used to authenticate against a local
# api/users.json file, which is .gitignore'd and doesn't ship with a Render
# deploy (so any account only living in that file, or created via
# /create-user against a running instance with an ephemeral disk, vanished
# on the next redeploy). These are the same accounts that file held, seeded
# once into the real database (users table) so nobody already using these
# credentials gets locked out during the migration. Idempotent — safe to
# call on every startup; only inserts usernames that don't already exist.
# ─────────────────────────────────────────────────────────────────────────────
_SEED_USERS = {
    "admin":           {"password": os.getenv("ADMIN_PASSWORD", "NDAY_26!"), "role": "admin", "name": "Admin"},
    "chief":           {"password": os.getenv("CHIEF_PASSWORD", "chief_2026"), "role": "admin", "name": "Chief"},
    "manager_user":    {"password": "manager_pass_123", "role": "manager", "name": "Manager User"},
    "dispatcher_user": {"password": "dispatcher_pass_123", "role": "dispatcher", "name": "Dispatcher User"},
    "driver_user":     {"password": "driver_pass_123", "role": "driver", "name": "Driver User"},
    "test":            {"password": "testpass123", "role": "dispatcher", "name": "Test User"},
    "tam":             {"password": "HotGrammy", "role": "driver", "name": "Tam"},
    "galo":            {"password": "Paperwork26", "role": "dispatcher", "name": "Galo"},
    "spencer":         {"password": "BelCanto", "role": "driver", "name": "Spencer"},
    "jefe":            {"password": "GoRRRRRRRRR", "role": "manager", "name": "Jefe"},
}


def seed_default_users(db: Session) -> None:
    changed = False
    for username, info in _SEED_USERS.items():
        if get_user_by_username(db, username):
            continue
        db.add(User(
            username=username,
            password_hash=hash_password(info["password"]),
            role=info["role"],
            name=info["name"],
            is_active=True,
        ))
        changed = True
    if changed:
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Invite / reset — shared functions, called both by the HTTP endpoints below
# (web /admin page) and directly by the Slack Dispatch Home handlers
# (slack_dispatch_home.py), which gate access via is_dispatch_staff() instead
# of an admin password.
# ─────────────────────────────────────────────────────────────────────────────

def create_invite(db: Session, username: str, name: str, role: str, slack_user_id: Optional[str] = None) -> tuple[User, str]:
    username = username.lower().strip()
    if get_user_by_username(db, username):
        raise ValueError(f"User '{username}' already exists")
    token = secrets.token_urlsafe(32)
    user = User(
        username=username,
        password_hash=PENDING_PASSWORD_HASH,
        role=role,
        name=name or username.capitalize(),
        slack_user_id=slack_user_id,
        is_active=False,
        reset_token=token,
        reset_token_expires_at=datetime.utcnow() + INVITE_TTL,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, token


def create_password_reset(db: Session, username: str, slack_user_id: Optional[str] = None) -> tuple[User, str]:
    user = get_user_by_username(db, username)
    if not user:
        raise ValueError(f"User '{username}' not found")
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires_at = datetime.utcnow() + RESET_TTL
    if slack_user_id:
        user.slack_user_id = slack_user_id
    db.commit()
    db.refresh(user)
    return user, token


def complete_token(db: Session, token: str, new_password: str) -> User:
    user = get_user_by_reset_token(db, token)
    if not user:
        raise ValueError("Invalid or already-used link")
    if not user.reset_token_expires_at or user.reset_token_expires_at < datetime.utcnow():
        raise ValueError("This link has expired — ask for a new invite or reset")
    if len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters")
    user.password_hash = hash_password(new_password)
    user.is_active = True
    user.reset_token = None
    user.reset_token_expires_at = None
    db.commit()
    db.refresh(user)
    return user


def set_password_url(token: str) -> str:
    return f"{APP_URL}/set-password?token={token}"


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
    role: str = "driver"


class UserListResponse(BaseModel):
    username: str
    name: str


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str
    admin_username: str
    admin_password: str


class InviteRequest(BaseModel):
    username: str
    name: str
    role: str = "driver"
    slack_user_id: Optional[str] = None
    admin_username: str
    admin_password: str


class RequestResetRequest(BaseModel):
    username: str
    admin_username: str
    admin_password: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


def _verify_admin_password(db: Session, username: str, password: str) -> bool:
    """Requires the account's role to actually be admin — used to gate
    create/delete-user, invites, and resets so a valid non-admin login can't
    pass its own credentials as "admin creds"."""
    user = get_user_by_username(db, username)
    if not user:
        return False
    return user.role == "admin" and verify_password(password, user.password_hash)


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate user with username and password. Returns JWT token with
    role claim for RBAC."""
    username = request.username.lower().strip()
    user = get_user_by_username(db, username)

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not yet activated — check your Slack DM for a link to set your password.",
        )

    user.last_login = datetime.utcnow()
    db.commit()

    payload = {
        "sub": user.username,
        "username": user.username,
        "role": user.role,
        "name": user.name or user.username.capitalize(),
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
        name=user.name or user.username.capitalize(),
        username=user.username,
        role=user.role,
        access_token=access_token,
        token_type="bearer",
    )


@router.post("/create-user")
async def create_user_endpoint(request: CreateUserRequest, db: Session = Depends(get_db)):
    """Create a new user with a known password up front. Requires valid
    admin credentials. For accounts where the person should choose their
    own password, use /auth/invite instead."""
    admin_username = request.admin_username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    new_username = request.username.lower().strip()
    if not new_username or not request.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username and password are required")
    if len(new_username) < 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username must be at least 3 characters")
    if len(request.password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 6 characters")
    if get_user_by_username(db, new_username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    db.add(User(
        username=new_username,
        password_hash=hash_password(request.password),
        role=request.role,
        name=new_username.capitalize(),
        is_active=True,
    ))
    db.commit()

    return {"message": "User created successfully", "username": new_username, "name": new_username.capitalize()}


@router.post("/list-users")
async def list_users(request: LoginRequest, db: Session = Depends(get_db)):
    """List all users. Requires valid admin credentials."""
    admin_username = request.username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    users_list = [
        UserListResponse(username=u.username, name=u.name or u.username.capitalize())
        for u in db.query(User).order_by(User.username).all()
    ]
    return {"users": users_list}


@router.post("/delete-user")
async def delete_user_endpoint(request: CreateUserRequest, db: Session = Depends(get_db)):
    """Delete a user. Requires valid admin credentials."""
    admin_username = request.admin_username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    username_to_delete = request.username.lower().strip()
    user = get_user_by_username(db, username_to_delete)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if username_to_delete == "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete the admin user")

    db.delete(user)
    db.commit()

    return {"message": f"User '{username_to_delete}' deleted successfully"}


@router.post("/change-password")
async def change_password_endpoint(request: ChangePasswordRequest, db: Session = Depends(get_db)):
    """Change a user's password. Requires valid admin credentials OR the
    user's own old password."""
    username_to_change = request.username.lower().strip()
    user = get_user_by_username(db, username_to_change)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    admin_username = request.admin_username.lower().strip()
    is_admin_change = _verify_admin_password(db, admin_username, request.admin_password)
    is_self_change = (
        username_to_change == admin_username and
        verify_password(request.old_password, user.password_hash)
    )
    if not (is_admin_change or is_self_change):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials. Provide either admin password or your old password.",
        )

    if not request.new_password or len(request.new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 6 characters")

    if username_to_change == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the default admin password here. Set the ADMIN_PASSWORD environment variable instead.",
        )

    user.password_hash = hash_password(request.new_password)
    db.commit()

    return {"message": f"Password for '{username_to_change}' changed successfully"}


@router.post("/invite")
async def invite_user_endpoint(request: InviteRequest, db: Session = Depends(get_db)):
    """Invite a new user — creates a pending account (no password yet) and
    returns a set-password link. Requires valid admin credentials. Slack
    delivery of this link is handled by the caller (e.g. the Dispatch Home
    Invite User button) — this endpoint just creates the invite."""
    admin_username = request.admin_username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    try:
        user, token = create_invite(db, request.username, request.name, request.role, request.slack_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return {"username": user.username, "set_password_url": set_password_url(token)}


@router.post("/request-reset")
async def request_reset_endpoint(request: RequestResetRequest, db: Session = Depends(get_db)):
    """Generate a password-reset link for an existing user. Requires valid
    admin credentials."""
    admin_username = request.admin_username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    try:
        user, token = create_password_reset(db, request.username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return {"username": user.username, "reset_url": set_password_url(token)}


@router.post("/set-password")
async def set_password_endpoint(request: SetPasswordRequest, db: Session = Depends(get_db)):
    """Public endpoint — the token itself is the credential. Used by both
    the invite-acceptance and password-reset links."""
    try:
        user = complete_token(db, request.token, request.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return {"message": "Password set successfully", "username": user.username}
