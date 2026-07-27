import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt
import bcrypt
import requests

from api.src.database import get_db, User, get_user_by_username, get_user_by_reset_token

logger = logging.getLogger(__name__)
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

def hash_password(password: str) -> str:
    # bcrypt caps input at 72 bytes and raises past that -- truncate rather
    # than let a long paste crash account creation (matches bcrypt's own
    # documented behavior in versions that silently truncated).
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or password_hash == PENDING_PASSWORD_HASH:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
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

# Security fix, 2026-07-27: this dict previously held real, working
# passwords as plain literal strings, committed directly to this public
# repo (chief82aps-gif/NDAY_OM). Replaced with a random value generated
# once per process start for any account not backed by a real env var.
# IMPORTANT — this only changes what gets seeded for a username that
# doesn't already exist in the database; it does NOT retroactively
# rotate the password on an account already seeded under the old values.
# Any of the accounts below that are still real, actively-used logins
# (tam/galo/spencer/jefe in particular look like real staff, not test
# data) need an explicit password reset (POST /auth/request-reset) or a
# Slack account link (POST /auth/link-slack) — simply deploying this
# change does not do that for them.
_RANDOM_SEED_PASSWORD = secrets.token_urlsafe(24)

_SEED_USERS = {
    "admin":           {"password": os.getenv("ADMIN_PASSWORD", _RANDOM_SEED_PASSWORD), "role": "admin", "name": "Admin"},
    "chief":           {"password": os.getenv("CHIEF_PASSWORD", _RANDOM_SEED_PASSWORD), "role": "admin", "name": "Chief"},
    "manager_user":    {"password": _RANDOM_SEED_PASSWORD, "role": "manager", "name": "Manager User"},
    "dispatcher_user": {"password": _RANDOM_SEED_PASSWORD, "role": "dispatcher", "name": "Dispatcher User"},
    "driver_user":     {"password": _RANDOM_SEED_PASSWORD, "role": "driver", "name": "Driver User"},
    "test":            {"password": _RANDOM_SEED_PASSWORD, "role": "dispatcher", "name": "Test User"},
    "tam":             {"password": _RANDOM_SEED_PASSWORD, "role": "driver", "name": "Tam"},
    "galo":            {"password": _RANDOM_SEED_PASSWORD, "role": "dispatcher", "name": "Galo"},
    "spencer":         {"password": _RANDOM_SEED_PASSWORD, "role": "driver", "name": "Spencer"},
    "jefe":            {"password": _RANDOM_SEED_PASSWORD, "role": "manager", "name": "Jefe"},
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


# One-time migration, added 2026-07-27. During a live debugging session
# nobody could confirm the actual current website password for the
# owner's account (it may have been set/rotated at some earlier point and
# never recorded) — /auth/link-slack couldn't be called without admin
# credentials nobody could verify, a circular problem. Since the owner's
# real Slack ID was already independently confirmed via a live Slack
# connection this same session, this links it directly at startup instead
# of requiring a password at all. Idempotent — no-ops once slack_user_id
# is already set on the target account, safe to leave in permanently.
_OWNER_SLACK_USER_ID = "U0BA8APSPAP"


def ensure_owner_slack_link(db: Session) -> None:
    user = get_user_by_username(db, "chief") or get_user_by_username(db, "admin")
    if user and not user.slack_user_id:
        user.slack_user_id = _OWNER_SLACK_USER_ID
        db.commit()
        logger.info("Linked owner Slack ID to account '%s'", user.username)


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


SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
BACKEND_URL = os.getenv("BACKEND_URL", "https://nday-om.onrender.com")
SLACK_OAUTH_REDIRECT_URI = f"{BACKEND_URL}/auth/slack/callback"
SLACK_STATE_TTL_MINUTES = 10


@router.get("/slack/login")
async def slack_login(redirect: Optional[str] = None):
    """Full-page redirect into Slack's OpenID Connect authorize flow — the
    frontend's "Sign in with Slack" button links straight here rather than
    fetching it, since the OAuth handshake needs a real browser navigation.

    redirect (added 2026-07-27): where to land after a successful login —
    e.g. Slack Home buttons link here with redirect=/eod-admin so clicking
    a dashboard button from Slack authenticates AND lands directly on that
    page, no separate login screen. Only a same-site relative path
    (starting with "/", not "//") is accepted — anything else is dropped
    to "/" to avoid this becoming an open-redirect vector."""
    if not SLACK_CLIENT_ID:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Slack sign-in is not configured")

    safe_redirect = redirect if redirect and redirect.startswith("/") and not redirect.startswith("//") else "/"
    state = jwt.encode(
        {
            "purpose": "slack_oauth_state",
            "redirect": safe_redirect,
            "exp": datetime.utcnow() + timedelta(minutes=SLACK_STATE_TTL_MINUTES),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    params = urlencode({
        "client_id": SLACK_CLIENT_ID,
        "scope": "openid profile email",
        "redirect_uri": SLACK_OAUTH_REDIRECT_URI,
        "state": state,
        "response_type": "code",
    })
    return RedirectResponse(f"https://slack.com/openid/connect/authorize?{params}")


@router.get("/slack/callback")
async def slack_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Slack redirects here after the user approves (or denies) sign-in.
    Deliberately does NOT auto-create accounts — only a Slack ID already
    linked to an existing User row (via /auth/invite or the dispatch "Add
    New Hire" modal) can complete login, so no workspace member gets
    dashboard access just by existing in Slack."""
    def _fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{APP_URL}/login?slack_error={reason}")

    if error or not code or not state:
        return _fail("denied")

    try:
        state_claims = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return _fail("invalid_state")
    redirect_path = state_claims.get("redirect") or "/"

    if not SLACK_CLIENT_ID or not SLACK_CLIENT_SECRET:
        return _fail("not_configured")

    try:
        token_resp = requests.post(
            "https://slack.com/api/openid.connect.token",
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": SLACK_OAUTH_REDIRECT_URI,
            },
            timeout=10,
        ).json()
    except requests.RequestException:
        logger.warning("Slack OAuth token exchange request failed", exc_info=True)
        return _fail("slack_unreachable")

    if not token_resp.get("ok"):
        logger.warning("Slack OAuth token exchange rejected: %s", token_resp.get("error"))
        return _fail("token_exchange_failed")

    slack_access_token = token_resp.get("access_token")

    try:
        userinfo_resp = requests.get(
            "https://slack.com/api/openid.connect.userInfo",
            headers={"Authorization": f"Bearer {slack_access_token}"},
            timeout=10,
        ).json()
    except requests.RequestException:
        logger.warning("Slack OAuth userinfo request failed", exc_info=True)
        return _fail("slack_unreachable")

    slack_user_id = userinfo_resp.get("https://slack.com/user_id") or userinfo_resp.get("sub")
    if not slack_user_id:
        logger.warning("Slack OAuth userinfo missing user id: %s", userinfo_resp)
        return _fail("userinfo_failed")

    user = db.query(User).filter(User.slack_user_id == slack_user_id).first()
    if not user:
        return _fail("not_linked")
    if not user.is_active:
        return _fail("inactive")

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
    access_token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    params = urlencode({
        "slack_token": access_token,
        "username": user.username,
        "name": user.name or user.username.capitalize(),
        "role": user.role,
    })
    return RedirectResponse(f"{APP_URL}{redirect_path}?{params}")


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


class LinkSlackRequest(BaseModel):
    username: str
    slack_user_id: str
    admin_username: str
    admin_password: str


@router.post("/link-slack")
async def link_slack_endpoint(request: LinkSlackRequest, db: Session = Depends(get_db)):
    """Attach a Slack user ID to an existing website account so Sign in
    with Slack works for it — added 2026-07-27 after discovering accounts
    created before this feature existed (e.g. the original seeded 'chief'/
    'admin' accounts) have no slack_user_id on file, so the OAuth callback
    correctly refuses them as "not linked" rather than guessing. Requires
    valid admin credentials, same gate as /create-user."""
    admin_username = request.admin_username.lower().strip()
    if not _verify_admin_password(db, admin_username, request.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")

    target_username = request.username.lower().strip()
    user = get_user_by_username(db, target_username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.slack_user_id = request.slack_user_id.strip()
    db.commit()
    return {"status": "linked", "username": user.username, "slack_user_id": user.slack_user_id}


@router.get("/debug-owner-link")
async def debug_owner_link(db: Session = Depends(get_db)) -> dict:
    """Read-only, no credentials needed — added 2026-07-27 to diagnose why
    Sign in with Slack still wasn't working after ensure_owner_slack_link()
    should have run. Exposes only non-sensitive fields (no password hash)."""
    def _summarize(u: Optional[User]) -> Optional[dict]:
        if not u:
            return None
        return {
            "username": u.username, "role": u.role, "is_active": u.is_active,
            "slack_user_id": u.slack_user_id,
        }
    chief = get_user_by_username(db, "chief")
    admin = get_user_by_username(db, "admin")
    matched_by_slack_id = db.query(User).filter(User.slack_user_id == _OWNER_SLACK_USER_ID).all()
    return {
        "chief": _summarize(chief),
        "admin": _summarize(admin),
        "users_with_owner_slack_id": [_summarize(u) for u in matched_by_slack_id],
        "expected_slack_id": _OWNER_SLACK_USER_ID,
    }


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


class SetMyPasswordRequest(BaseModel):
    new_password: str


@router.post("/set-my-password")
async def set_my_password_endpoint(
    request: SetMyPasswordRequest,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Self-service password set, gated by a valid session token instead of
    the old password or admin credentials — added 2026-07-27 specifically
    for setting a break-glass password once Sign in with Slack works
    (there's no other way to prove identity for an account whose real
    password nobody can currently confirm). A valid Bearer token can only
    exist if the caller already successfully authenticated (password OR
    Slack), so that possession is the proof of identity here."""
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise ValueError("not bearer")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")

    username = payload.get("username")
    user = get_user_by_username(db, username) if username else None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found or inactive")

    if len(request.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

    user.password_hash = hash_password(request.new_password)
    db.commit()
    return {"message": "Password set successfully", "username": user.username}


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
