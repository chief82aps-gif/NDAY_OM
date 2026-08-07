"""
Pilot roster admin — read/set which drivers piloted features send to.

See api/src/pilot_roster.py for the semantics (fail-open: an empty list means
"no pilot", never "send to nobody"; the pilot restricts, it never enables).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.authorization import require_any_role
from api.src.database import get_db
from api.src.pilot_roster import describe_pilot, set_pilot_roster_ids

router = APIRouter(prefix="/pilot", tags=["pilot"])


@router.get("/roster")
def get_roster(db: Session = Depends(get_db)):
    """Unauthenticated read — it returns no PII beyond names already visible
    across the app, and being able to check "who is in the pilot?" without a
    token is genuinely useful while testing."""
    return describe_pilot(db)


class SetPilotRequest(BaseModel):
    roster_ids: list[int]
    updated_by: Optional[str] = None


@router.post("/roster")
def set_roster(payload: SetPilotRequest, db: Session = Depends(get_db),
               caller_role: str = Depends(require_any_role("owner", "hr", "ops_manager"))):
    """Replace the pilot list. Post {"roster_ids": []} to end the pilot."""
    return set_pilot_roster_ids(db, payload.roster_ids, payload.updated_by or caller_role)
