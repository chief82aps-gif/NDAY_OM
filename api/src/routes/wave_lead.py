"""
Wave Lead Module — added 2026-07-29. See Governance/05_NDL_Wave_Lead_Module_SRD.md
for the full design.

Owns: WaveLeadRole, WaveTeam, WaveTeamMembership, WaveRosterSuggestion,
WaveRosterDiscrepancy (all in api/src/database.py).

Model, in short:
  - Senior Wave Lead: one per half ("front"/"back"), roving across ALL of
    waves 1-4 for that half -- Spencer=front, Gallo=back. Corrected
    2026-07-29 from an earlier, incorrect "one standing lead per
    individual wave 1-4" model, which was never a real feature and has
    been removed (see get_senior_wave_lead()).
  - Wave 5 (the 4x4 truck) gets exactly two independent leads (WaveLeadRole,
    wave_number=5) and has no team/half concept at all.
  - 8 fixed teams = Waves 1-4 x {Front Half, Back Half} (WaveTeam, seeded
    once via seed_wave_teams()). A driver's team (WaveTeamMembership) is a
    standing assignment, not recomputed nightly.
  - Competition standings (get_team_standings()) rank teams by average of
    driver_scoring.py's blended overall score across standing membership.

v1 is suggestion + validation only -- this module never becomes dispatch's
system of record for rostering; see WaveRosterSuggestion/WaveRosterDiscrepancy.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, DriverRosterEntry, DriverScheduleEntry, DailyRouteAssignment,
    WaveLeadRole, WaveTeam, WaveTeamMembership,
    WaveRosterSuggestion, WaveRosterDiscrepancy,
    get_reminder_state, set_reminder_state,
)
from api.src.authorization import require_any_role
from api.src.routes.auth import JWT_SECRET, JWT_ALGORITHM
from api.src.feature_flags import get_flag
from api.src.timezone import PACIFIC

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wave-lead", tags=["wave-lead"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt

WAVE_NUMBERS = (1, 2, 3, 4)          # standard waves, each with one standing lead + a team per half
WAVE_5 = 5                            # the 4x4 truck -- two leads, no team
HALVES = ("front", "back")            # Front Half: Sun-Wed: Back Half: Wed-Sun (Wednesday is a real overlap day)

# Feature gate — same pattern as every other new automated send this
# session: off until confirmed working, then flipped on deliberately.
_COMPETITION_MESSAGE_KEY = "wave_competition_daily_message"
_COMPETITION_SEND_HOUR = 7  # 7 AM Pacific

# Dynamic wave channels — "Option A" PTT-lite, added 2026-07-29: Slack's own
# client already has native voice-clip recording/sending built in, so no new
# app or paid PTT SDK (Zello Work, Agora, etc. -- see
# project_ptt_future_options.md) is needed for a driver to record a voice
# message that reaches exactly their current wave's group. This just
# auto-creates one Slack channel per wave (1-5) and keeps membership synced
# to whoever's ACTUALLY working that wave today (operational, not standing
# team -- same distinction as _resolve_wave_lead_for_driver() in
# rostering.py), including that wave's lead(s).
_WAVE_CHANNEL_SYNC_KEY_PREFIX = "wave_ptt_channel_"


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
    """All active leads for a wave_number-scoped role — only Wave 5 (the
    4x4 truck) uses this now; normally returns 2 (its two dedicated
    leads). Waves 1-4 no longer have a per-wave lead — see
    get_senior_wave_lead() for the half-scoped Senior Wave Lead."""
    roles = (
        db.query(WaveLeadRole)
        .filter(WaveLeadRole.wave_number == wave_number, WaveLeadRole.active == True)  # noqa: E712
        .all()
    )
    roster_ids = [r.roster_id for r in roles]
    if not roster_ids:
        return []
    return db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()


def get_senior_wave_lead(half: str, db: Session) -> Optional[DriverRosterEntry]:
    """The standing Senior Wave Lead for a half ("front"/"back") -- roves
    across ALL of waves 1-4, not tied to any individual wave number.
    Exactly one active row per half (Spencer=front, Gallo=back)."""
    role = (
        db.query(WaveLeadRole)
        .filter(WaveLeadRole.half == half, WaveLeadRole.active == True)  # noqa: E712
        .first()
    )
    if not role:
        return None
    return db.query(DriverRosterEntry).filter(DriverRosterEntry.id == role.roster_id).first()


def get_team_for_driver(roster_id: int, db: Session) -> Optional[WaveTeam]:
    membership = db.query(WaveTeamMembership).filter(WaveTeamMembership.roster_id == roster_id).first()
    if not membership:
        return None
    return db.query(WaveTeam).filter(WaveTeam.id == membership.team_id).first()


def suggest_team_assignments(weeks: int, db: Session) -> list[dict]:
    """Analyzes each active driver's real historical schedule (DriverScheduleEntry,
    trailing `weeks` weeks) to suggest a standing team — added 2026-07-29
    because there's no way for dispatch to know ~100 drivers' typical wave/
    day pattern from memory. Doesn't touch anything; purely a read-only
    suggestion for a human to review and apply via /wave-lead/teams/assign.

    Wave: most common wave_number (1-4) across their entries -- Wave 5
    occurrences are ignored for team purposes (Wave 5 has no team; a driver
    who mostly runs the 4x4 truck is presumably one of its two dedicated
    leads, not someone needing a Front/Back Half team).

    Half: Sun/Mon/Tue count toward Front, Thu/Fri/Sat count toward Back,
    Wednesday splits 0.5/0.5 both ways (it's a real overlap day per the
    SRD) -- whichever side has the higher total wins."""
    cutoff = date.today() - timedelta(weeks=weeks)
    entries = (
        db.query(DriverScheduleEntry)
        .filter(DriverScheduleEntry.schedule_date >= cutoff, DriverScheduleEntry.roster_id.isnot(None))
        .all()
    )

    by_driver: dict[int, list[DriverScheduleEntry]] = {}
    for e in entries:
        by_driver.setdefault(e.roster_id, []).append(e)

    roster_ids = list(by_driver.keys())
    active_entries = {
        r.id: r for r in db.query(DriverRosterEntry).filter(
            DriverRosterEntry.id.in_(roster_ids), DriverRosterEntry.is_active == True,  # noqa: E712
        ).all()
    }

    results = []
    for roster_id, driver_entries in by_driver.items():
        roster_entry = active_entries.get(roster_id)
        if not roster_entry:
            continue  # inactive or not found -- skip, don't suggest a team for them

        wave_counts: dict[int, int] = {}
        front_score = back_score = 0.0
        for e in driver_entries:
            wn = wave_number_for_assignment(e.wave_time, e.service_type)
            if wn != WAVE_5:
                wave_counts[wn] = wave_counts.get(wn, 0) + 1
            weekday = e.schedule_date.weekday()  # Mon=0 ... Sun=6
            if weekday == 6:  # Sunday, Monday, Tuesday -> Front
                front_score += 1
            elif weekday in (0, 1):
                front_score += 1
            elif weekday == 2:  # Wednesday -- real overlap day, splits both ways
                front_score += 0.5
                back_score += 0.5
            elif weekday in (3, 4, 5):  # Thursday, Friday, Saturday -> Back
                back_score += 1

        if not wave_counts:
            continue  # only ever ran Wave 5 -- not a team candidate

        suggested_wave = max(wave_counts, key=lambda w: wave_counts[w])
        suggested_half = "front" if front_score >= back_score else "back"

        team = db.query(WaveTeam).filter(WaveTeam.wave_number == suggested_wave, WaveTeam.half == suggested_half).first()
        current_team = get_team_for_driver(roster_id, db)

        results.append({
            "roster_id": roster_id,
            "payroll_name": roster_entry.payroll_name,
            "sample_size": len(driver_entries),
            "suggested_wave": suggested_wave,
            "suggested_half": suggested_half,
            "suggested_team_id": team.id if team else None,
            "suggested_team_label": team_label(team) if team else None,
            "current_team_id": current_team.id if current_team else None,
            "current_team_label": team_label(current_team) if current_team else None,
            "matches_current": bool(current_team and team and current_team.id == team.id),
        })

    results.sort(key=lambda r: r["payroll_name"])
    return results


@router.get("/suggest-teams")
def get_suggest_teams(weeks: int = 6, db: Session = Depends(get_db)):
    return {"suggestions": suggest_team_assignments(weeks, db)}


def get_wave_channel_id(wave_number: int, db: Session) -> Optional[str]:
    """Read-only lookup of a wave's Slack channel ID (for the Home tab's
    quick-link button) — never creates it. Returns None if the channel
    hasn't been created yet (WAVE_PTT_CHANNELS_ACTIVE off, or sync hasn't
    run) -- callers should just omit the button/link in that case."""
    state = get_reminder_state(db, f"{_WAVE_CHANNEL_SYNC_KEY_PREFIX}{wave_number}")
    return state.get("channel_id")


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
    if service_type:
        st = service_type.lower()
        # "amflex" is the normalized Fleet-side name (normalization.py);
        # "4wd"/"p31" catch the raw Amazon string ("4WD P31 Delivery
        # Truck") before/without normalization -- confirmed live
        # 2026-07-29 that the raw form is what actually shows up in at
        # least one real data view, so both must be checked.
        if "amflex" in st or "4wd" in st or "p31" in st:
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
# Wave Lead Team Focus — added 2026-07-30. Per explicit request: each Senior
# Wave Lead needs visibility into their own team's performance metrics plus
# an improvement focus area per driver, ordered by "biggest bang for the
# buck" -- not raw worst-score-first, but whoever is CLOSEST to their next
# tier threshold, since that's where a single coaching conversation is most
# likely to actually move the needle. A driver already at Platinum (no tier
# left to climb into) or with no computable score sorts to the bottom --
# there's no tier-crossing upside to prioritize there.
# ─────────────────────────────────────────────────────────────────────────────

# Maps each driver_scoring.py sub-metric to the matching corrective-video
# topic from Governance/06_NDL_CAP_Compliance_Monitoring_SRD.md's video
# list, so a driver's focus area comes with a concrete next step, not just
# a diagnosis. PSB folds into the same "proof-of-delivery photos" video as
# POD per that doc's consolidation.
_METRIC_TO_VIDEO = {
    "speeding_score": "Speeding",
    "seatbelt_score": "Seatbelt compliance",
    "distraction_score": "Distracted driving",
    "sign_violation_score": "Stop sign / signal violations",
    "following_distance_score": "Following distance",
    "dc_dpmo_score": "Delivery completion",
    "dsb_score": "Package handling",
    "pod_score": "Proof-of-delivery photos",
    "cdf_dpmo_score": "Customer experience / professionalism",
    "psb_score": "Proof-of-delivery photos",
}


def _gap_to_next_tier(overall: Optional[float], tier: str) -> Optional[float]:
    """Points needed to exceed the next tier threshold up. None if
    already Platinum (nothing higher to climb into) or score/tier
    couldn't be computed at all."""
    from api.src.routes.driver_scoring import TIER_THRESHOLDS

    if overall is None or tier == "gray" or tier == "platinum":
        return None
    tier_order = [name for name, _ in TIER_THRESHOLDS] + ["sawdust"]
    if tier not in tier_order:
        return None
    idx = tier_order.index(tier)
    next_threshold = TIER_THRESHOLDS[idx - 1][1]
    return round(next_threshold - overall, 2)


def get_team_focus_for_half(half: str, db: Session) -> list[dict]:
    """Per-driver performance snapshot + top improvement focus area for
    every standing team member of a half (roving across all of waves
    1-4), sorted by 'biggest bang for the buck' -- smallest gap to the
    next tier threshold first."""
    from api.src.routes.driver_scoring import compute_driver_scores
    from api.src.routes.quality import _METRIC_LABELS
    from api.src.driver_identity import resolve_roster_entry
    from api.src.database import QualityMetricDriver, QualityMetricSnapshot

    team_ids = [t.id for t in db.query(WaveTeam).filter(WaveTeam.half == half).all()]
    member_roster_ids = {
        m.roster_id for m in db.query(WaveTeamMembership).filter(WaveTeamMembership.team_id.in_(team_ids)).all()
    } if team_ids else set()
    if not member_roster_ids:
        return []

    scores = compute_driver_scores(db)
    by_roster_id: dict[int, dict] = {}
    for s in scores:
        if not s["driver_name"]:
            continue
        entry = resolve_roster_entry(s["driver_name"], db)
        if entry and entry.id in member_roster_ids:
            by_roster_id[entry.id] = s

    latest_week = db.query(QualityMetricSnapshot).order_by(QualityMetricSnapshot.week.desc()).first()

    results = []
    for roster_id in member_roster_ids:
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == roster_id).first()
        if not entry:
            continue
        s = by_roster_id.get(roster_id)
        overall = s["overall"] if s else None
        tier = s["overall_tier"] if s else "gray"

        focus_areas: list[dict] = []
        if s and s.get("transporter_id") and latest_week:
            row = (
                db.query(QualityMetricDriver)
                .filter(
                    QualityMetricDriver.transporter_id == s["transporter_id"],
                    QualityMetricDriver.snapshot_id == latest_week.id,
                )
                .first()
            )
            if row:
                metric_scores = [
                    (attr, label, getattr(row, attr))
                    for attr, label in _METRIC_LABELS.items()
                    if getattr(row, attr, None) is not None
                ]
                metric_scores.sort(key=lambda m: m[2])
                focus_areas = [
                    {"metric": label, "score": round(float(val), 1), "video": _METRIC_TO_VIDEO.get(attr)}
                    for attr, label, val in metric_scores[:2]
                ]

        results.append({
            "roster_id": roster_id,
            "driver_name": entry.payroll_name,
            "overall": overall,
            "tier": tier,
            "safety": s["safety"] if s else None,
            "quality": s["quality"] if s else None,
            "attendance": s["attendance"] if s else None,
            "gap_to_next_tier": _gap_to_next_tier(overall, tier),
            "focus_areas": focus_areas,
        })

    # "Biggest bang for the buck" first: smallest gap-to-next-tier sorts
    # first; no-gap (Platinum, or no data at all) sorts last.
    results.sort(key=lambda r: (r["gap_to_next_tier"] is None, r["gap_to_next_tier"] or 0))
    return results


def is_senior_wave_lead_half(username: str, db: Session) -> Optional[str]:
    """Resolve a website username to the half they lead, if they're
    currently an active Senior Wave Lead -- returns 'front'/'back', or
    None. Lets Spencer/Gallo reach their own team's focus page without
    needing an elevated website role (they're otherwise just driver-role
    accounts)."""
    from api.src.database import User

    user = db.query(User).filter(User.username == username).first()
    if not user or not user.slack_user_id:
        return None
    roster = db.query(DriverRosterEntry).filter(DriverRosterEntry.slack_member_id == user.slack_user_id).first()
    if not roster:
        return None
    role = (
        db.query(WaveLeadRole)
        .filter(WaveLeadRole.roster_id == roster.id, WaveLeadRole.active == True, WaveLeadRole.half.isnot(None))  # noqa: E712
        .first()
    )
    return role.half if role else None


def _current_username_and_role(authorization: Optional[str] = Header(None)) -> tuple[str, str]:
    """Same JWT already used everywhere else in the app -- decoded
    directly here (rather than authorization.get_current_user_role())
    since this endpoint needs the username too, not just the role, to
    resolve self-access for a Senior Wave Lead."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise ValueError("not bearer")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("username") or payload.get("sub")
    role = payload.get("role")
    if not username or not role:
        raise HTTPException(status_code=401, detail="Token missing required claims")
    return username, role


_TEAM_FOCUS_FULL_ACCESS_ROLES = {"admin", "manager", "dispatcher", "hr", "owner"}


@router.get("/team-focus")
def team_focus(
    half: str,
    db: Session = Depends(get_db),
    identity: tuple[str, str] = Depends(_current_username_and_role),
):
    if half not in HALVES:
        raise HTTPException(400, "half must be 'front' or 'back'")

    username, role = identity
    if role not in _TEAM_FOCUS_FULL_ACCESS_ROLES:
        own_half = is_senior_wave_lead_half(username, db)
        if own_half != half:
            raise HTTPException(403, "You can only view your own team's focus page.")

    return {"half": half, "drivers": get_team_focus_for_half(half, db)}


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
    roles = db.query(WaveLeadRole).filter(WaveLeadRole.active == True).order_by(WaveLeadRole.wave_number, WaveLeadRole.half).all()  # noqa: E712
    roster_ids = [r.roster_id for r in roles]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}
    return {
        "roles": [
            {
                "id": r.id,
                "wave_number": r.wave_number,
                "half": r.half,
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
    """Wave 5 (the 4x4 truck) only — its two independent leads. Waves 1-4
    no longer have a per-wave standing lead (removed 2026-07-29, never a
    real feature); use POST /wave-lead/senior/assign for the Senior Wave
    Lead instead."""
    if payload.wave_number != WAVE_5:
        raise HTTPException(
            400,
            "Standing per-wave leads only apply to Wave 5 (the 4x4 truck). "
            "Waves 1-4 use the Senior Wave Lead — see POST /wave-lead/senior/assign.",
        )
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == payload.roster_id).first()
    if not entry:
        raise HTTPException(404, f"Driver {payload.roster_id} not found")

    # Wave 5 allows two concurrent active leads (the two 4x4 truck leads).
    role = WaveLeadRole(
        wave_number=payload.wave_number, roster_id=payload.roster_id, assigned_by=payload.assigned_by,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"status": "assigned", "role_id": role.id, "wave_number": payload.wave_number, "roster_id": payload.roster_id}


class AssignSeniorWaveLeadRequest(BaseModel):
    half: str   # "front" | "back"
    roster_id: int
    assigned_by: Optional[str] = None


@router.post("/senior/assign")
def assign_senior_wave_lead(
    payload: AssignSeniorWaveLeadRequest,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    """Senior Wave Lead — one per half, roving across ALL of waves 1-4
    (Spencer=front, Gallo=back). Replaces the removed per-wave 'Standing
    Wave Lead' concept."""
    if payload.half not in HALVES:
        raise HTTPException(400, "half must be 'front' or 'back'")
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == payload.roster_id).first()
    if not entry:
        raise HTTPException(404, f"Driver {payload.roster_id} not found")

    existing = (
        db.query(WaveLeadRole)
        .filter(WaveLeadRole.half == payload.half, WaveLeadRole.active == True)  # noqa: E712
        .all()
    )
    for role in existing:
        role.active = False

    role = WaveLeadRole(
        half=payload.half, wave_number=None, roster_id=payload.roster_id, assigned_by=payload.assigned_by,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"status": "assigned", "role_id": role.id, "half": payload.half, "roster_id": payload.roster_id}


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
    if not get_flag("WAVE_COMPETITION_ACTIVE"):
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


# ─────────────────────────────────────────────────────────────────────────────
# Nightly roster suggestion + discrepancy checking — v1 suggestion/
# validation-only pipeline. Manually triggered for now (not yet on its own
# schedule tied to the Rostered Work Blocks ingest landing — see
# Governance/05_NDL_Wave_Lead_Module_SRD.md open items).
#
# v1 simplification, explicit: suggests purely from each driver's standing
# team (WaveTeamMembership) + blended rank, filtered to who's actually
# scheduled that night. Does NOT yet cross-reference against a per-wave
# headcount inferred from Work Blocks columns -- that needs the promised
# screenshot to build correctly rather than guessing at a file format.
# ─────────────────────────────────────────────────────────────────────────────

def generate_wave_roster_suggestion(roster_date: date, db: Session) -> dict:
    """Suggest each driver's wave for roster_date. Idempotent — clears any
    prior suggestion for this date first, safe to re-run as the night's
    schedule data changes."""
    from api.src.routes.driver_scoring import compute_driver_scores
    from api.src.driver_identity import resolve_roster_entry

    db.query(WaveRosterSuggestion).filter(WaveRosterSuggestion.roster_date == roster_date).delete()

    scheduled = db.query(DriverScheduleEntry).filter(DriverScheduleEntry.schedule_date == roster_date).all()
    scheduled_roster_ids = {e.roster_id for e in scheduled if e.roster_id}

    scores = compute_driver_scores(db)
    score_by_roster_id: dict[int, float] = {}
    for s in scores:
        if s["overall"] is None or not s["driver_name"]:
            continue
        entry = resolve_roster_entry(s["driver_name"], db)
        if entry:
            score_by_roster_id[entry.id] = s["overall"]

    created = 0
    for wave_number in WAVE_NUMBERS:
        team_ids = [t.id for t in db.query(WaveTeam).filter(WaveTeam.wave_number == wave_number).all()]
        member_roster_ids = [
            m.roster_id for m in db.query(WaveTeamMembership).filter(WaveTeamMembership.team_id.in_(team_ids)).all()
        ] if team_ids else []
        candidates = sorted(
            (rid for rid in member_roster_ids if rid in scheduled_roster_ids),
            key=lambda rid: score_by_roster_id.get(rid, -1),
            reverse=True,
        )

        # No per-wave lead slot anymore (removed 2026-07-29) -- the Senior
        # Wave Lead roves across all waves for their half, not tied to a
        # specific wave's roster order.
        position = 1
        for rid in candidates:
            db.add(WaveRosterSuggestion(
                roster_date=roster_date, roster_id=rid, suggested_wave=wave_number,
                suggested_rank_position=position, is_wave_lead_slot=False,
            ))
            position += 1
            created += 1

    db.commit()
    return {"roster_date": roster_date.isoformat(), "suggestions_created": created}


def check_roster_discrepancies(roster_date: date, db: Session) -> dict:
    """Compare the suggestion against dispatch's actual roster
    (DailyRouteAssignment) for roster_date. Idempotent — clears prior
    discrepancy rows for this date first.

    Deliberately does NOT flag a driver landing in a different wave than
    their standing team's nominal wave ("wave_mismatch") — confirmed
    2026-07-29 this is normal, expected spillover (NDL doesn't control
    Amazon's real per-wave route volume day to day), not an error. The
    standing team/wave a driver belongs to is for competition/mentoring/
    discipline attribution only; their actual day-of wave (and therefore
    which lead they reach on Zello/Slack) is resolved fresh from the real
    schedule regardless of team, via _resolve_wave_lead_for_driver() in
    rostering.py. The two are intentionally decoupled — see
    Governance/05_NDL_Wave_Lead_Module_SRD.md.

    v1 also deliberately does not flag 'unexpected' (driver rostered but
    not suggested at all) — that would conflate a driver simply not yet
    assigned a standing team with a real misrostering, which needs a
    cleaner signal than v1 has."""
    db.query(WaveRosterDiscrepancy).filter(WaveRosterDiscrepancy.roster_date == roster_date).delete()

    suggestions = db.query(WaveRosterSuggestion).filter(WaveRosterSuggestion.roster_date == roster_date).all()
    suggested_by_roster_id = {s.roster_id: s for s in suggestions}

    actual = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date == roster_date).all()
    actual_by_roster_id: dict[int, DailyRouteAssignment] = {a.roster_id: a for a in actual if a.roster_id}

    created = 0
    for roster_id, suggestion in suggested_by_roster_id.items():
        a = actual_by_roster_id.get(roster_id)
        if not a:
            db.add(WaveRosterDiscrepancy(
                roster_date=roster_date, roster_id=roster_id, discrepancy_type="missing",
                detail=f"Suggested for Wave {suggestion.suggested_wave} but not found in today's actual roster.",
            ))
            created += 1
            continue
        # A driver landing in a different wave than suggested is normal
        # spillover (see docstring above) -- not flagged as a discrepancy.

    # No more per-wave lead_slot_unfilled check (removed 2026-07-29) --
    # there's no per-wave lead slot anymore; the Senior Wave Lead isn't
    # part of any wave's roster suggestion at all.

    db.commit()
    return {"roster_date": roster_date.isoformat(), "discrepancies_created": created}


def send_discrepancy_summary(roster_date: date, db: Session) -> dict:
    """DMs #nday-mgt a summary of unresolved discrepancies for roster_date,
    framed as confirm-as-is or fix — matches the "you missed these, leave
    it or make corrections" framing from the original request."""
    rows = (
        db.query(WaveRosterDiscrepancy)
        .filter(WaveRosterDiscrepancy.roster_date == roster_date, WaveRosterDiscrepancy.resolved == False)  # noqa: E712
        .all()
    )
    if not rows:
        return {"status": "no_discrepancies", "roster_date": roster_date.isoformat()}

    roster_ids = [r.roster_id for r in rows]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}

    lines = [
        f"🔍 *Wave Roster Check — {roster_date.strftime('%A, %B %-d')}*",
        f"{len(rows)} discrepancy(ies) found vs. the suggested roster:",
        "",
    ]
    for r in rows:
        name = entries[r.roster_id].payroll_name if r.roster_id in entries else f"driver #{r.roster_id}"
        lines.append(f"• *{name}* — {r.detail}")
    lines.append("")
    lines.append("Leave it as-is, or make corrections in your usual rostering process.")

    try:
        token = os.getenv("SLACK_BOT_TOKEN")
        if token:
            from slack_sdk import WebClient
            WebClient(token=token).chat_postMessage(channel=MGT_CHANNEL, text="\n".join(lines))
    except Exception as exc:
        logger.warning("Wave roster discrepancy summary post failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    return {"status": "sent", "roster_date": roster_date.isoformat(), "discrepancy_count": len(rows)}


@router.post("/generate-suggestion/{roster_date}")
def trigger_generate_suggestion(
    roster_date: date,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    return generate_wave_roster_suggestion(roster_date, db)


@router.get("/suggestion/{roster_date}")
def get_suggestion(roster_date: date, db: Session = Depends(get_db)):
    rows = (
        db.query(WaveRosterSuggestion)
        .filter(WaveRosterSuggestion.roster_date == roster_date)
        .order_by(WaveRosterSuggestion.suggested_wave, WaveRosterSuggestion.suggested_rank_position)
        .all()
    )
    roster_ids = [r.roster_id for r in rows]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}
    return {
        "roster_date": roster_date.isoformat(),
        "suggestions": [
            {
                "roster_id": r.roster_id,
                "payroll_name": entries[r.roster_id].payroll_name if r.roster_id in entries else "Unknown",
                "wave": r.suggested_wave,
                "position": r.suggested_rank_position,
                "is_wave_lead_slot": r.is_wave_lead_slot,
            }
            for r in rows
        ],
    }


@router.post("/check-discrepancies/{roster_date}")
def trigger_check_discrepancies(
    roster_date: date,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    result = check_roster_discrepancies(roster_date, db)
    send_result = send_discrepancy_summary(roster_date, db)
    return {**result, "summary_send": send_result}


@router.get("/discrepancies/{roster_date}")
def get_discrepancies(roster_date: date, db: Session = Depends(get_db)):
    rows = db.query(WaveRosterDiscrepancy).filter(WaveRosterDiscrepancy.roster_date == roster_date).all()
    roster_ids = [r.roster_id for r in rows]
    entries = {
        e.id: e for e in db.query(DriverRosterEntry).filter(DriverRosterEntry.id.in_(roster_ids)).all()
    } if roster_ids else {}
    return {
        "roster_date": roster_date.isoformat(),
        "discrepancies": [
            {
                "id": r.id,
                "roster_id": r.roster_id,
                "payroll_name": entries[r.roster_id].payroll_name if r.roster_id in entries else "Unknown",
                "type": r.discrepancy_type,
                "detail": r.detail,
                "resolved": r.resolved,
            }
            for r in rows
        ],
    }


@router.post("/discrepancies/{discrepancy_id}/resolve")
def resolve_discrepancy(
    discrepancy_id: int,
    resolved_by: str,
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    row = db.query(WaveRosterDiscrepancy).filter(WaveRosterDiscrepancy.id == discrepancy_id).first()
    if not row:
        raise HTTPException(404, f"Discrepancy {discrepancy_id} not found")
    row.resolved = True
    row.resolved_by = resolved_by
    row.resolved_at = datetime.utcnow()
    db.commit()
    return {"status": "resolved", "id": discrepancy_id}


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic wave channels (PTT-lite via Slack's native voice clips)
# ─────────────────────────────────────────────────────────────────────────────

def _slack_client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


_WAVE_CHANNEL_NAMES = {
    1: "wave-1-team", 2: "wave-2-team", 3: "wave-3-team", 4: "wave-4-team",
    WAVE_5: "wave-5-4x4-truck",
}


def _get_or_create_wave_channel(client, wave_number: int, db: Session) -> Optional[str]:
    """Idempotent — returns the Slack channel ID for a wave's dynamic
    channel, creating it via conversations_create() the first time and
    caching the ID afterward (ReminderThrottleState) so we don't re-create
    or re-list on every sync."""
    state_key = f"{_WAVE_CHANNEL_SYNC_KEY_PREFIX}{wave_number}"
    cached = get_reminder_state(db, state_key)
    if cached.get("channel_id"):
        return cached["channel_id"]

    name = _WAVE_CHANNEL_NAMES[wave_number]
    try:
        resp = client.conversations_create(name=name, is_private=False)
        channel_id = resp["channel"]["id"]
    except Exception as exc:
        # name_taken means it already exists (e.g. created manually before
        # this ran) -- look it up by name instead of failing.
        if "name_taken" in str(exc):
            try:
                cursor = None
                channel_id = None
                while True:
                    listing = client.conversations_list(types="public_channel", limit=200, cursor=cursor)
                    channel_id = next((c["id"] for c in listing["channels"] if c["name"] == name), None)
                    if channel_id or not listing.get("response_metadata", {}).get("next_cursor"):
                        break
                    cursor = listing["response_metadata"]["next_cursor"]
                if not channel_id:
                    logger.warning("Wave channel '%s' reported name_taken but not found in listing", name)
                    return None
            except Exception as exc2:
                logger.warning("Wave channel lookup-by-name failed for '%s': %s", name, exc2)
                return None
        else:
            logger.warning("Wave channel create failed for wave %s: %s", wave_number, exc)
            return None

    set_reminder_state(db, state_key, {"channel_id": channel_id})
    return channel_id


def sync_wave_channels(db: Session) -> dict:
    """Daily sync — makes each wave's Slack channel membership match
    exactly who's actually working that wave today (operational, via
    wave_number_for_assignment() on real DailyRouteAssignment rows), plus
    that wave's standing lead(s). Gated by WAVE_PTT_CHANNELS_ACTIVE."""
    if not get_flag("WAVE_PTT_CHANNELS_ACTIVE"):
        return {"status": "inactive", "note": "Set WAVE_PTT_CHANNELS_ACTIVE=true on Render to enable"}

    client = _slack_client()
    if not client:
        return {"status": "no_slack_client"}

    try:
        bot_user_id = client.auth_test()["user_id"]
    except Exception as exc:
        logger.warning("Wave channel sync: auth_test failed: %s", exc)
        bot_user_id = None

    today = datetime.now(PACIFIC).date()
    assignments = db.query(DailyRouteAssignment).filter(DailyRouteAssignment.assignment_date == today).all()

    desired: dict[int, set[str]] = {n: set() for n in (*WAVE_NUMBERS, WAVE_5)}
    for a in assignments:
        if not a.roster_id:
            continue
        entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == a.roster_id).first()
        if not entry or not entry.slack_member_id or not entry.slack_verified:
            continue
        wave_number = wave_number_for_assignment(a.wave, getattr(a, "service_type", None))
        desired[wave_number].add(entry.slack_member_id)

    # Wave 5 keeps its own two independent leads. Waves 1-4 no longer have
    # a per-wave lead -- both Senior Wave Leads (front & back) get added to
    # every wave 1-4 channel instead, since either half's driver could be
    # present in any of waves 1-4 on a given day (and both can be, on the
    # Wednesday overlap) -- simpler and safer than trying to filter by
    # which half is actually represented in that specific wave today.
    senior_leads = [entry for half in HALVES if (entry := get_senior_wave_lead(half, db))]
    for wave_number in WAVE_NUMBERS:
        for lead_entry in senior_leads:
            if lead_entry.slack_member_id and lead_entry.slack_verified:
                desired[wave_number].add(lead_entry.slack_member_id)
    for lead_entry in get_active_wave_leads(WAVE_5, db):
        if lead_entry.slack_member_id and lead_entry.slack_verified:
            desired[WAVE_5].add(lead_entry.slack_member_id)

    results = {}
    for wave_number, desired_members in desired.items():
        channel_id = _get_or_create_wave_channel(client, wave_number, db)
        if not channel_id:
            results[wave_number] = {"status": "no_channel"}
            continue

        try:
            current = set(client.conversations_members(channel=channel_id)["members"])
        except Exception as exc:
            logger.warning("Failed to list members for wave %s channel: %s", wave_number, exc)
            results[wave_number] = {"status": "list_failed"}
            continue

        keep = set(desired_members)
        if bot_user_id:
            keep.add(bot_user_id)  # never kick the bot itself

        added = removed = 0
        for uid in (desired_members - current):
            try:
                client.conversations_invite(channel=channel_id, users=uid)
                added += 1
            except Exception as exc:
                logger.info("Wave %s channel invite skipped for %s: %s", wave_number, uid, exc)
        for uid in (current - keep):
            try:
                client.conversations_kick(channel=channel_id, user=uid)
                removed += 1
            except Exception as exc:
                logger.info("Wave %s channel kick skipped for %s: %s", wave_number, uid, exc)

        results[wave_number] = {"channel_id": channel_id, "added": added, "removed": removed, "target_size": len(desired_members)}

    return {"status": "synced", "date": today.isoformat(), "waves": results}


@router.post("/sync-channels")
def trigger_sync_wave_channels(
    db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("dispatcher", "ops_manager", "manager")),
):
    """Manual trigger for testing/recovery — same function the daily loop calls."""
    return sync_wave_channels(db)
