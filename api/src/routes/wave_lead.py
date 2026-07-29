"""
Wave Lead Module — added 2026-07-29. See Governance/05_NDL_Wave_Lead_Module_SRD.md
for the full design.

Owns: WaveLeadRole, WaveTeam, WaveTeamMembership, WaveRosterSuggestion,
WaveRosterDiscrepancy (all in api/src/database.py).

Model, in short:
  - Waves 1-4 each get exactly one standing lead (WaveLeadRole), shared
    across both Front/Back Half teams. Wave 5 (the 4x4 truck) gets exactly
    two, and has no team concept at all.
  - 8 fixed teams = Waves 1-4 x {Front Half, Back Half} (WaveTeam, seeded
    once via seed_wave_teams()). A driver's team (WaveTeamMembership) is a
    standing assignment, not recomputed nightly.
  - Senior wave leads (Spencer/Gallo) are deliberately NOT modeled here --
    they're independent/roving, not wave-scoped, per explicit decision.
  - Competition standings (get_team_standings()) rank teams by average of
    driver_scoring.py's blended overall score across standing membership.

v1 is suggestion + validation only -- this module never becomes dispatch's
system of record for rostering; see WaveRosterSuggestion/WaveRosterDiscrepancy.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, DriverRosterEntry,
    WaveLeadRole, WaveTeam, WaveTeamMembership,
    WaveRosterSuggestion, WaveRosterDiscrepancy,
    get_reminder_state, set_reminder_state,
)
from api.src.authorization import require_any_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wave-lead", tags=["wave-lead"])

PACIFIC = ZoneInfo("America/Los_Angeles")
MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt

WAVE_NUMBERS = (1, 2, 3, 4)          # standard waves, each with one standing lead + a team per half
WAVE_5 = 5                            # the 4x4 truck -- two leads, no team
HALVES = ("front", "back")            # Front Half: Sun-Wed: Back Half: Wed-Sun (Wednesday is a real overlap day)

# Feature gate — same pattern as every other new automated send this
# session: off until confirmed working, then flipped on deliberately.
WAVE_COMPETITION_ACTIVE = os.getenv("WAVE_COMPETITION_ACTIVE", "false").lower() == "true"
_COMPETITION_MESSAGE_KEY = "wave_competition_daily_message"
_COMPETITION_SEND_HOUR = 7  # 7 AM Pacific


# ─────────────────────────────────────────────────────────────────────────────
# Seeding — the 8 teams are static reference data, created once
# ─────────────────────────────────────────────────────────────────────────────

def seed_wave_teams(db: Session) -> None:
    """Idempotent — creates the 8 WaveTeam rows (Wave 1-4 x Front/Back) if
    they don't already exist. Call at startup alongside seed_default_users."""
    existing = {(t.wave_number, t.half) for t in db.query(WaveTeam).all()}
    created = False
    for wave_number in WAVE_NUMBERS:
        for half in HALVES:
            if (wave_number, half) not in existing:
                db.add(WaveTeam(wave_number=wave_number, half=half))
                created = True
    if created:
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Public resolver functions — for other modules (driver_lead_schedule.py,
# rostering.py) to call instead of querying these tables directly, per the
# hub-and-spoke module boundary rule.
# ─────────────────────────────────────────────────────────────────────────────

def get_active_wave_leads(wave_number: int, db: Session) -> list[DriverRosterEntry]:
    """All active leads for a wave — normally 1 (waves 1-4) or 2 (wave 5)."""
    roles = (
        db.query(WaveLeadRole)
        .filter(WaveLeadRole.wave_number == wave_number, WaveLeadRole.active == True)  # noqa: E712
        .all()
    )
    roster_ids = [r.roster_id for r in roles]
    if not roster_ids:
        return []
    return db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()


def get_team_for_driver(roster_id: int, db: Session) -> Optional[WaveTeam]:
    membership = db.query(WaveTeamMembership).filter(WaveTeamMembership.roster_id == roster_id).first()
    if not membership:
        return None
    return db.query(WaveTeam).filter(WaveTeam.id == membership.team_id).first()


def team_label(team: WaveTeam) -> str:
    return f"Wave {team.wave_number} {team.half.capitalize()}"


def wave_number_for_assignment(wave_time: Optional[str], service_type: Optional[str]) -> int:
    """Bucket a driver's daily assignment into a wave number (1-5) for lead
    resolution. Wave 5 (the 4x4 truck) is vehicle-type driven, not
    time-driven -- normalization.py maps the raw "4WD P31 Delivery Truck"
    service type to "AmFlex Large Vehicle" (its Fleet-side name), so that's
    what actually shows up on a real assignment row. Waves 1-4 are bucketed
    by departure time, mirroring pdf_generator.py's display-only
    _extract_wave_number() (before 8am=1, 8-10am=2, 10am-12pm=3, after
    12pm=4) -- duplicated here as a small standalone function rather than
    reaching into that class method, since this is now the canonical
    version for anything lead-resolution-related."""
    if service_type and "amflex" in service_type.lower():
        return WAVE_5

    if not wave_time:
        return 1
    try:
        s = wave_time.strip().upper()
        time_part = s.replace("AM", "").replace("PM", "").strip()
        is_pm = "PM" in s
        parts = time_part.split(":")
        if len(parts) != 2:
            return 1
        hour = int(parts[0])
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
        if hour < 8:
            return 1
        elif hour < 10:
            return 2
        elif hour < 12:
            return 3
        else:
            return 4
    except Exception:
        return 1


def get_team_standings(db: Session) -> list[dict]:
    """Rank the 8 teams by average of driver_scoring.py's blended overall
    score across each team's standing membership. A driver missing quality
    data entirely just doesn't count toward their team's average (not
    treated as a zero)."""
    from api.src.routes.driver_scoring import compute_driver_scores
    from api.src.driver_identity import resolve_roster_entry

    scores = compute_driver_scores(db)
    score_by_roster_id: dict[int, float] = {}
    for s in scores:
        if s["overall"] is None or not s["driver_name"]:
            continue
        entry = resolve_roster_entry(s["driver_name"], db)
        if entry:
            score_by_roster_id[entry.id] = s["overall"]

    teams = db.query(WaveTeam).order_by(WaveTeam.wave_number, WaveTeam.half).all()
    standings = []
    for team in teams:
        member_ids = [
            m.roster_id for m in
            db.query(WaveTeamMembership).filter(WaveTeamMembership.team_id == team.id).all()
        ]
        scored = [score_by_roster_id[rid] for rid in member_ids if rid in score_by_roster_id]
        avg = round(sum(scored) / len(scored), 2) if scored else None
        standings.append({
            "team_id": team.id,
            "wave_number": team.wave_number,
            "half": team.half,
            "team_label": team_label(team),
            "member_count": len(member_ids),
            "scored_member_count": len(scored),
            "avg_score": avg,
        })

    standings.sort(key=lambda t: (t["avg_score"] is None, -(t["avg_score"] or 0)))
    for i, t in enumerate(standings):
        t["rank"] = i + 1
    return standings


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    standings = get_team_standings(db)
    return {"teams": standings}


@router.get("/teams/{team_id}/members")
def list_team_members(team_id: int, db: Session = Depends(get_db)):
    team = db.query(WaveTeam).filter(WaveTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, f"Team {team_id} not found")
    memberships = db.query(WaveTeamMembership).filter(WaveTeamMembership.team_id == team_id).all()
    roster_ids = [m.roster_id for m in memberships]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}
    return {
        "team_id": team_id,
        "team_label": team_label(team),
        "members": [
            {"roster_id": rid, "payroll_name": entries[rid].payroll_name if rid in entries else "Unknown"}
            for rid in roster_ids
        ],
    }


@router.get("/roles")
def list_wave_lead_roles(db: Session = Depends(get_db)):
    roles = db.query(WaveLeadRole).filter(WaveLeadRole.active == True).order_by(WaveLeadRole.wave_number).all()  # noqa: E712
    roster_ids = [r.roster_id for r in roles]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}
    return {
        "roles": [
            {
                "id": r.id,
                "wave_number": r.wave_number,
                "roster_id": r.roster_id,
                "payroll_name": entries[r.roster_id].payroll_name if r.roster_id in entries else "Unknown",
                "assigned_at": r.assigned_at.isoformat() if r.assigned_at else None,
            }
            for r in roles
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write endpoints — RBAC-gated from day one (unlike rostering.py /
# driver_lead_schedule.py, which currently have none — logged debt there,
# not precedent to repeat here).
# ─────────────────────────────────────────────────────────────────────────────

class AssignTeamMemberRequest(BaseModel):
    roster_id: int
    team_id: int
    assigned_by: Optional[str] = None


@router.post("/teams/assign")
def assign_team_member(
    payload: AssignTeamMemberRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    team = db.query(WaveTeam).filter(WaveTeam.id == payload.team_id).first()
    if not team:
        raise HTTPException(404, f"Team {payload.team_id} not found")
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == payload.roster_id).first()
    if not entry:
        raise HTTPException(404, f"Driver {payload.roster_id} not found")

    membership = db.query(WaveTeamMembership).filter(WaveTeamMembership.roster_id == payload.roster_id).first()
    if membership:
        membership.team_id = payload.team_id
        membership.assigned_at = datetime.utcnow()
        membership.assigned_by = payload.assigned_by
    else:
        membership = WaveTeamMembership(
            roster_id=payload.roster_id, team_id=payload.team_id, assigned_by=payload.assigned_by,
        )
        db.add(membership)
    db.commit()
    return {"status": "assigned", "roster_id": payload.roster_id, "team_id": payload.team_id, "team_label": team_label(team)}


@router.delete("/teams/members/{roster_id}")
def remove_team_member(
    roster_id: int,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    membership = db.query(WaveTeamMembership).filter(WaveTeamMembership.roster_id == roster_id).first()
    if not membership:
        raise HTTPException(404, f"No team membership found for driver {roster_id}")
    db.delete(membership)
    db.commit()
    return {"status": "removed", "roster_id": roster_id}


class AssignWaveLeadRequest(BaseModel):
    wave_number: int
    roster_id: int
    assigned_by: Optional[str] = None


@router.post("/roles/assign")
def assign_wave_lead(
    payload: AssignWaveLeadRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    if payload.wave_number not in (*WAVE_NUMBERS, WAVE_5):
        raise HTTPException(400, "wave_number must be 1-5")
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == payload.roster_id).first()
    if not entry:
        raise HTTPException(404, f"Driver {payload.roster_id} not found")

    # Waves 1-4 get exactly one active lead; deactivate any existing one.
    # Wave 5 allows two concurrent active leads (the two 4x4 truck leads).
    if payload.wave_number != WAVE_5:
        existing = (
            db.query(WaveLeadRole)
            .filter(WaveLeadRole.wave_number == payload.wave_number, WaveLeadRole.active == True)  # noqa: E712
            .all()
        )
        for role in existing:
            role.active = False

    role = WaveLeadRole(
        wave_number=payload.wave_number, roster_id=payload.roster_id, assigned_by=payload.assigned_by,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"status": "assigned", "role_id": role.id, "wave_number": payload.wave_number, "roster_id": payload.roster_id}


@router.delete("/roles/{role_id}")
def deactivate_wave_lead(
    role_id: int,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    role = db.query(WaveLeadRole).filter(WaveLeadRole.id == role_id).first()
    if not role:
        raise HTTPException(404, f"Wave lead role {role_id} not found")
    role.active = False
    db.commit()
    return {"status": "deactivated", "role_id": role_id}


# ─────────────────────────────────────────────────────────────────────────────
# Morning competition standings message — friendly inter-team competition,
# per explicit request. Posts once a day to #nday-mgt naming the leading
# team(s). Awards/bonuses tied to this are a stated future idea, not built.
# ─────────────────────────────────────────────────────────────────────────────

def _standings_message_text(standings: list[dict]) -> str:
    scored = [t for t in standings if t["avg_score"] is not None]
    if not scored:
        return "🏁 *Wave Team Standings* — no scored teams yet (need drivers assigned + quality data ingested)."

    lines = ["🏁 *Wave Team Standings* — friendly competition, updated daily!", ""]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for t in standings:
        if t["avg_score"] is None:
            continue
        medal = medals.get(t["rank"], f"{t['rank']}.")
        lines.append(
            f"{medal} *{t['team_label']}* — {t['avg_score']} avg score "
            f"({t['scored_member_count']}/{t['member_count']} drivers scored)"
        )
    leader = scored[0]
    lines.append("")
    lines.append(f"👑 *{leader['team_label']}* is leading today. Keep it up!")
    return "\n".join(lines)


def send_wave_competition_standings(db: Session, force: bool = False) -> dict:
    """Once per day: post the team standings to #nday-mgt. force=True
    bypasses the hour gate and already-sent guard for manual testing/
    an ad-hoc re-send."""
    if not WAVE_COMPETITION_ACTIVE:
        return {"status": "inactive", "note": "Set WAVE_COMPETITION_ACTIVE=true on Render to enable"}

    now_pt = datetime.now(PACIFIC)
    today = now_pt.date()

    if not force and now_pt.hour != _COMPETITION_SEND_HOUR:
        return {"status": "not_send_hour", "date": today.isoformat()}

    state_key = f"{_COMPETITION_MESSAGE_KEY}_{today.isoformat()}"
    if not force and get_reminder_state(db, state_key).get("sent_at"):
        return {"status": "already_sent", "date": today.isoformat()}

    standings = get_team_standings(db)
    text = _standings_message_text(standings)

    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        if token:
            from slack_sdk import WebClient
            WebClient(token=token).chat_postMessage(channel=MGT_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Wave competition standings post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    set_reminder_state(db, state_key, {"sent_at": datetime.utcnow().isoformat()})
    return {"status": "sent", "date": today.isoformat(), "standings": standings}


@router.post("/trigger-standings")
def trigger_standings(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the morning loop calls."""
    return send_wave_competition_standings(db, force=force)
