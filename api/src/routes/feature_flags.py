"""
Feature Flags admin API — added 2026-07-31. Admin-only (currently just
the owner's account); see feature_flags.py for the actual get_flag()/
FLAG_REGISTRY this reads and writes.
"""
from __future__ import annotations

import os
from typing import Optional

import jwt as _jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import get_db
from api.src.authorization import require_any_role
from api.src.feature_flags import FLAG_REGISTRY, list_flags, set_flag

router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])


def _current_username(authorization: Optional[str] = Header(None)) -> str:
    """Same JWT already used everywhere else in the app -- just the
    username, for the audit trail on who toggled a flag."""
    if not authorization:
        return "unknown"
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            return "unknown"
        secret = os.getenv("JWT_SECRET", "dev-secret")
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("username") or payload.get("sub") or "unknown"
    except Exception:
        return "unknown"


@router.get("")
def get_all_flags(
    db: Session = Depends(get_db),
    role: str = Depends(require_any_role()),   # admin only -- require_any_role() with no extra roles
):
    return {"flags": list_flags(db)}


class SetFlagRequest(BaseModel):
    enabled: bool


@router.post("/{key}")
def toggle_flag(
    key: str,
    payload: SetFlagRequest,
    db: Session = Depends(get_db),
    role: str = Depends(require_any_role()),
    username: str = Depends(_current_username),
):
    if key not in FLAG_REGISTRY:
        raise HTTPException(404, f"Unknown flag: {key}")
    row = set_flag(key, payload.enabled, updated_by=username, db=db)
    return {"key": row.key, "enabled": row.enabled, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
