"""
Pilot roster — scope driver-facing features to a named subset of drivers.

The gap this closes was written up in
`Governance/FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md` §4 Phase 2: every
driver-facing send processes "everyone scheduled today" indiscriminately, so
the only way to try something was an all-or-nothing global flip — which is
precisely the shape that caused the 2026-08-06 pause. A feature could be
either off, or live for ~100 drivers. Nothing in between.

## Semantics — deliberately fail-open

`allow_driver()` returns True when NO pilot list is set. An empty list means
"no pilot in effect, behave normally", never "send to nobody". A pilot roster
is an extra *restriction* layered on top of the existing flags, never a new
way to switch a feature on:

    global flag OFF  + pilot set   -> nothing sends (the flag still wins)
    global flag ON   + no pilot    -> everyone, exactly as before
    global flag ON   + pilot set   -> ONLY the pilot drivers

That ordering matters. Checking the pilot list must never be mistaken for
checking the feature flag — see the call-site note in dvic.py.

State lives in the same persisted key/JSON table as the reminder throttles
(`ReminderThrottleState`), NOT a module-level variable: Render restarts on
every deploy, and in-memory state that silently resets is the exact bug
recorded in CLAUDE.md's "never store reminder/throttle state in memory".
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from api.src.database import get_reminder_state, set_reminder_state, DriverRosterEntry

logger = logging.getLogger(__name__)

PILOT_ROSTER_KEY = "pilot_driver_roster_ids"


def get_pilot_roster_ids(db: Session) -> set[int]:
    """Roster ids currently in the pilot. Empty set == no pilot in effect."""
    state = get_reminder_state(db, PILOT_ROSTER_KEY) or {}
    ids = state.get("roster_ids") or []
    out: set[int] = set()
    for rid in ids:
        try:
            out.add(int(rid))
        except (TypeError, ValueError):
            continue
    return out


def pilot_is_active(db: Session) -> bool:
    return bool(get_pilot_roster_ids(db))


def allow_driver(roster_id: Optional[int], db: Session) -> bool:
    """True if this driver may receive a piloted feature right now.

    Fail-open by design: no pilot list == everyone allowed. A driver with no
    roster_id at all is allowed when no pilot is set, and excluded when one
    is — an unidentifiable driver can't be verified as a pilot member, and
    during a pilot the safe default is to leave them out rather than guess.
    """
    pilot = get_pilot_roster_ids(db)
    if not pilot:
        return True
    return roster_id is not None and int(roster_id) in pilot


def set_pilot_roster_ids(db: Session, roster_ids: list[int], updated_by: str = "") -> dict:
    """Replace the pilot list. Pass [] to end the pilot (back to everyone)."""
    clean: list[int] = []
    for rid in roster_ids or []:
        try:
            clean.append(int(rid))
        except (TypeError, ValueError):
            continue
    clean = sorted(set(clean))
    set_reminder_state(db, PILOT_ROSTER_KEY, {"roster_ids": clean, "updated_by": updated_by})
    logger.warning("Pilot roster set to %s by %s", clean, updated_by or "unknown")
    return describe_pilot(db)


def describe_pilot(db: Session) -> dict:
    """The pilot list with driver names resolved — what the admin UI shows."""
    ids = sorted(get_pilot_roster_ids(db))
    drivers = []
    if ids:
        rows = db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(ids)).all()
        by_id = {r.id: r for r in rows}
        for rid in ids:
            r = by_id.get(rid)
            drivers.append({
                "roster_id": rid,
                "payroll_name": r.payroll_name if r else "(no roster entry)",
                "is_active": r.is_active if r else None,
                "slack_member_id": r.slack_member_id if r else None,
                # A pilot driver with no Slack link would silently receive
                # nothing — surfaced rather than left to be discovered later.
                "reachable": bool(r and r.is_active and r.slack_member_id),
            })
    return {
        "pilot_active": bool(ids),
        "count": len(ids),
        "roster_ids": ids,
        "drivers": drivers,
        "note": ("Piloted features send ONLY to these drivers. Their own feature flag "
                 "must still be on — the pilot list restricts, it does not enable."
                 if ids else
                 "No pilot in effect — piloted features behave normally for everyone."),
    }
