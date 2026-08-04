"""
Attendance Tracker — per SRD HR-02, HR-03, HR-04.

Endpoints:
  POST /attendance/log                  Manually log an attendance event (dispatch/OM)
  GET  /attendance/today                All attendance events for today
  GET  /attendance/driver/{name}        Full attendance history for a driver
  GET  /attendance/missed-shifts        Drivers with 2+ missed shifts (HR-03 flag)
  GET  /attendance/compliance           4-hour call-in rule compliance report
  POST /attendance/ringcentral-webhook  Inbound RingCentral call events
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    AttendanceEvent,
    RingCentralCallLog,
    DriverRosterEntry,
    CalloutQueue,
    DriverScheduleEntry,
    get_reminder_state,
    set_reminder_state,
)
from api.src.driver_identity import resolve_roster_entry
from api.src.authorization import require_any_role
from api.src.feature_flags import get_flag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/attendance", tags=["attendance"])

PACIFIC = ZoneInfo("America/Los_Angeles")


def _pin_matches(roster_entry: Optional["DriverRosterEntry"], submitted_pin: Optional[str], db: Session) -> bool:
    """True if submitted_pin is either the driver's own ssn_last4 PIN or
    today's shared daily fallback PIN (mgt_reminders.py) -- the fallback is
    additive, checked only when the driver's own PIN doesn't match, never a
    replacement for it. Added 2026-08-01 alongside the daily fallback PIN
    feature so dispatch has a way to unlock a driver who's forgotten theirs."""
    if not roster_entry or not submitted_pin:
        return False
    submitted = submitted_pin.strip()
    if roster_entry.ssn_last4 and roster_entry.ssn_last4 == submitted:
        return True
    from api.src.routes.mgt_reminders import get_daily_fallback_pin
    fallback = get_daily_fallback_pin(db)
    return bool(fallback and fallback == submitted)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern Detection Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of a weekday (0=Mon) in the given month."""
    first = date(year, month, 1)
    first_match = first + timedelta(days=(weekday - first.weekday()) % 7)
    return first_match + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of a weekday (0=Mon) in the given month."""
    if month == 12:
        next_m = date(year + 1, 1, 1)
    else:
        next_m = date(year, month + 1, 1)
    last = next_m - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _federal_holidays(year: int) -> dict[date, str]:
    """Return {date: name} for US federal holidays in a given year."""
    h: dict[date, str] = {}
    h[date(year, 1, 1)]   = "New Year's Day"
    h[date(year, 7, 4)]   = "Independence Day"
    h[date(year, 11, 11)] = "Veterans Day"
    h[date(year, 12, 25)] = "Christmas Day"
    h[_nth_weekday(year, 1, 0, 3)]  = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, 0, 3)]  = "Presidents' Day"
    h[_last_weekday(year, 5, 0)]    = "Memorial Day"
    h[_nth_weekday(year, 9, 0, 1)]  = "Labor Day"
    h[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving"
    return h


def _pre_holiday_label(d: date) -> Optional[str]:
    """
    Returns a holiday label if today is:
      - The day before a federal holiday, OR
      - A Friday before a Monday federal holiday (creates a 3-day weekend).
    Returns None if no such pattern.
    """
    holidays = {}
    for yr in (d.year, d.year + 1):
        holidays.update(_federal_holidays(yr))

    tomorrow = d + timedelta(days=1)
    if tomorrow in holidays:
        return holidays[tomorrow]

    if d.weekday() == 4:  # Friday
        monday = d + timedelta(days=3)
        if monday in holidays:
            return holidays[monday]

    return None


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def _first_name(payroll_name: str) -> str:
    """Extract first name from 'Last, First' ADP format."""
    if "," in payroll_name:
        rest = payroll_name.split(",", 1)[1].strip()
        return rest.split()[0].title() if rest else payroll_name
    return payroll_name.split()[0].title()


_DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_DOW_FOLLOW_UP = {
    "Monday":    "Is there something about Monday mornings that's been making it hard to get in?",
    "Tuesday":   "Is there something on Tuesdays that's been a challenge lately?",
    "Wednesday": "Is there something midweek that's been a recurring issue?",
    "Thursday":  "Is there something on Thursdays we should know about?",
    "Friday":    "Is there something about Fridays that's been making it tough to finish the week?",
    "Saturday":  "Is there something making Saturday shifts particularly difficult?",
    "Sunday":    "Is there something happening on Saturday evenings that may be making Sunday mornings harder?",
}


def _detect_callout_patterns(driver_name: str, today: date, db: Session) -> list[dict]:
    """
    Detect suspicious absence patterns and return empathetic push-back messages.
    Called before the driver selects a reason so general patterns surface early.
    """
    first = _first_name(driver_name)
    since_60 = today - timedelta(days=60)
    since_30 = today - timedelta(days=30)

    prior = (
        db.query(AttendanceEvent)
        .filter(
            func.lower(AttendanceEvent.driver_name) == driver_name.lower(),
            AttendanceEvent.event_date >= since_60,
            AttendanceEvent.event_date < today,
            AttendanceEvent.event_type.in_(["call_in", "no_show"]),
        )
        .order_by(AttendanceEvent.event_date.desc())
        .all()
    )

    patterns: list[dict] = []

    # ── 1. Same day-of-week repeat ────────────────────────────────────────────
    today_dow = today.weekday()
    day_name  = _DOW_NAMES[today_dow]
    same_dow  = [e for e in prior if e.event_date.weekday() == today_dow]
    if len(same_dow) >= 2:
        days_since = (today - same_dow[0].event_date).days
        follow_up  = _DOW_FOLLOW_UP.get(day_name, "Is everything okay?")
        patterns.append({
            "type": "day_of_week",
            "severity": "flag",
            "message": (
                f"Hey {first} — this would be your {_ordinal(len(same_dow) + 1)} {day_name} "
                f"call-out in the last 60 days (most recently {days_since} days ago). "
                f"We hope everything is alright. {follow_up}"
            ),
        })

    # ── 2. Pre-holiday / 3-day-weekend eve ───────────────────────────────────
    holiday_label = _pre_holiday_label(today)
    if holiday_label:
        prior_holiday_eves = [e for e in prior if _pre_holiday_label(e.event_date)]
        if prior_holiday_eves:
            cnt = len(prior_holiday_eves)
            patterns.append({
                "type": "pre_holiday",
                "severity": "flag",
                "message": (
                    f"Hey {first} — today is the day before {holiday_label}. "
                    f"We've noticed this pattern {cnt} time{'s' if cnt > 1 else ''} before — "
                    f"call-outs right before holidays or long weekends. "
                    f"We'd love for you to request PTO in advance when possible so we can plan the roster. "
                    f"Is there something we can do to make that easier?"
                ),
            })

    # ── 3. High call-out frequency in 30 days ────────────────────────────────
    recent_30 = [e for e in prior if e.event_date >= since_30]
    if len(recent_30) >= 2:
        patterns.append({
            "type": "high_frequency",
            "severity": "concern",
            "message": (
                f"Hey {first} — this would be your {_ordinal(len(recent_30) + 1)} call-out "
                f"in the last 30 days. We're genuinely concerned and want to make sure you're okay. "
                f"If something is going on that's making it hard to come in consistently, "
                f"please reach out to your manager — we want to help."
            ),
        })

    # ── 4. Family emergency frequency + repeat member ─────────────────────────
    family_prior = [e for e in prior if e.reason_code == "family"]
    if family_prior:
        member_counts: dict[str, int] = {}
        for e in family_prior:
            if e.notes:
                m = re.search(r"Pertains to:\s*(\w+)", e.notes, re.IGNORECASE)
                if m:
                    member_counts[m.group(1).capitalize()] = (
                        member_counts.get(m.group(1).capitalize(), 0) + 1
                    )

        flagged_member = False
        for member, cnt in member_counts.items():
            if cnt >= 1:
                flagged_member = True
                if member in ("Father", "Mother"):
                    patterns.append({
                        "type": "repeat_parent",
                        "severity": "flag",
                        "message": (
                            f"Hey {first} — we show {cnt} prior family emergency call-out{'s' if cnt > 1 else ''} "
                            f"involving your {member} in the last 60 days. "
                            f"We sincerely hope they're doing better. "
                            f"If this is an ongoing situation, please speak with your manager — "
                            f"we may be able to work out a support plan."
                        ),
                    })
                else:
                    patterns.append({
                        "type": "repeat_family_member",
                        "severity": "concern",
                        "message": (
                            f"Hey {first} — this is your {_ordinal(cnt + 1)} family emergency "
                            f"involving your {member} in the last 60 days. "
                            f"We hope the situation is improving."
                        ),
                    })

        if not flagged_member and len(family_prior) >= 2:
            patterns.append({
                "type": "family_frequency",
                "severity": "concern",
                "message": (
                    f"Hey {first} — this would be your {_ordinal(len(family_prior) + 1)} family emergency "
                    f"call-out in 60 days. We're sorry your family is going through a difficult time. "
                    f"If there is an ongoing situation, please talk to your manager — "
                    f"we may be able to accommodate."
                ),
            })

    return patterns[:3]  # cap at 3 to avoid overwhelming the driver


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

VALID_EVENT_TYPES = {"call_in", "no_show", "late_arrival", "early_departure", "present", "excused"}
VALID_REASON_CODES = {
    "sick", "personal", "family", "weather", "transportation",
    "doctor_appointment", "childcare", "no_call", "other",
}

# Tightened callout reason enforcement -- added 2026-07-30, per explicit HR
# request. These reason codes are still selectable (so we can catch and
# respond to them rather than let the driver quietly submit under "other"),
# but are NOT valid excuses on their own -- the driver sees this pushback
# message and either picks a genuine reason or acknowledges they're
# submitting anyway, which gets flagged as an unauthorized callout
# (AttendanceEvent.reason_valid=False) rather than a quietly-accepted one.
# "family" is validated separately below (see _family_reason_valid) since
# its validity depends on which family member + whether the driver
# currently lives with them, not the reason code alone.
INVALID_REASON_MESSAGES = {
    "personal": (
        "\"Personal\" isn't a valid reason for a callout. Please give us a specific reason — "
        "sick, a true family emergency, weather, or a transportation issue."
    ),
    "doctor_appointment": (
        "Non-emergency doctor's appointments should be scheduled outside of work hours whenever "
        "possible. If this isn't a medical emergency, it should be rescheduled for a day you're not working."
    ),
    "childcare": (
        "School closures, snow days, and babysitter issues need a backup childcare plan in place — "
        "this isn't an approved reason to miss a scheduled shift."
    ),
}

# Family emergency is only valid for an immediate family member the driver
# currently lives with -- spouse, child (son/daughter), mother, or father.
# Does not extend to extended family or anyone not in the same household.
VALID_FAMILY_MEMBERS = {"spouse", "child", "mother", "father"}


def _family_reason_valid(family_who: Optional[str], lives_with: Optional[bool]) -> bool:
    if not family_who or family_who.lower() not in VALID_FAMILY_MEMBERS:
        return False
    return bool(lives_with)


def check_reason_validity(reason_code: str, family_who: Optional[str] = None, lives_with_family: Optional[bool] = None) -> tuple[bool, Optional[str]]:
    """Returns (is_valid, pushback_message_or_None). Used both by the
    pre-submit reason-check endpoint (frontend shows this inline the
    moment a reason is picked) and re-validated server-side on actual
    submission, so a modified client can't bypass it."""
    if reason_code in INVALID_REASON_MESSAGES:
        return False, INVALID_REASON_MESSAGES[reason_code]
    if reason_code == "family":
        if not _family_reason_valid(family_who, lives_with_family):
            return False, (
                "A family emergency callout only applies to an immediate family member you currently "
                "live with — spouse, child, mother, or father. It doesn't extend beyond that."
            )
        return True, None
    return True, None

# Wave → scheduled time (Pacific) for 4-hour rule calculation
WAVE_TIMES: dict[str, tuple[int, int]] = {
    "1020": (10, 20),
    "1025": (10, 25),
    "1045": (10, 45),
    "1050": (10, 50),
    "1100": (11, 0),
    "1115": (11, 15),
}

MISSED_TYPES = {"no_show", "call_in"}  # both count as missed per handbook


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceLogRequest(BaseModel):
    driver_name: str
    event_date: Optional[str] = None          # YYYY-MM-DD, defaults to today
    event_type: str                            # call_in | no_show | late_arrival | etc.
    reason_code: Optional[str] = None
    call_time: Optional[str] = None           # ISO datetime string
    scheduled_wave: Optional[str] = None      # "1020", "1025", etc.
    notes: Optional[str] = None
    logged_by: Optional[str] = None


class CalloutRequest(BaseModel):
    driver_name: str
    ssn_last4: Optional[str] = None          # 4-digit PIN — required unless callout_token is provided
    callout_token: Optional[str] = None      # Signed token from Slack link — alternative to PIN
    reason_code: str
    family_who: Optional[str] = None         # spouse | child | mother | father -- only when reason_code == "family"
    lives_with_family: Optional[bool] = None  # does the driver currently live with family_who
    reason_override_ack: bool = False        # driver was shown the pushback message and chose to submit anyway
    scheduled_wave: Optional[str] = None
    shift_date: Optional[str] = None         # ISO date of the shift being called out for
    notes: Optional[str] = None
    signature_name: Optional[str] = None     # driver types full name to sign


class SetPinRequest(BaseModel):
    ssn_last4: str


class ChangePinRequest(BaseModel):
    driver_name: str
    current_pin: str
    new_pin: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """Strip all non-digits from a phone number."""
    return re.sub(r"\D", "", phone or "")


def _calc_compliance(call_time: datetime, scheduled_wave: str, event_date: date) -> tuple[Optional[float], Optional[bool]]:
    """
    Returns (hours_before_shift, compliant).
    compliant = True if call was made ≥4 hours before scheduled wave.
    """
    if not scheduled_wave or scheduled_wave not in WAVE_TIMES:
        return None, None
    h, m = WAVE_TIMES[scheduled_wave]
    shift_start = datetime(event_date.year, event_date.month, event_date.day, h, m,
                           tzinfo=PACIFIC).replace(tzinfo=None)
    delta = (shift_start - call_time).total_seconds() / 3600
    return round(delta, 2), delta >= 4.0


def _missed_shift_count(driver_name: str, as_of_date: date, db: Session) -> int:
    """Count missed shifts for driver in the trailing 60 days."""
    since = as_of_date - timedelta(days=60)
    return db.query(func.count(AttendanceEvent.id)).filter(
        func.lower(AttendanceEvent.driver_name) == driver_name.lower(),
        AttendanceEvent.is_missed == True,
        AttendanceEvent.event_date >= since,
        AttendanceEvent.event_date <= as_of_date,
    ).scalar() or 0


def _match_driver_by_phone(phone: str, db: Session) -> Optional[DriverRosterEntry]:
    """Match an inbound caller to a roster entry by normalized phone number."""
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    all_active = db.query(DriverRosterEntry).filter(DriverRosterEntry.is_active == True).all()
    for entry in all_active:
        if entry.phone and _normalize_phone(entry.phone) == normalized:
            return entry
    return None


def _event_to_dict(e: AttendanceEvent) -> dict:
    return {
        "id": e.id,
        "driver_name": e.driver_name,
        "event_date": e.event_date.isoformat() if e.event_date else None,
        "event_type": e.event_type,
        "reason_code": e.reason_code,
        "reason_valid": e.reason_valid,
        "call_time": e.call_time.isoformat() if e.call_time else None,
        "scheduled_wave": e.scheduled_wave,
        "hours_before_shift": float(e.hours_before_shift) if e.hours_before_shift is not None else None,
        "compliant": e.compliant,
        "is_missed": e.is_missed,
        "missed_shift_count": e.missed_shift_count,
        "voluntary_resign_flag": e.voluntary_resign_flag,
        "notes": e.notes,
        "logged_by": e.logged_by,
        "ringcentral_call_id": e.ringcentral_call_id,
        "caller_number": e.caller_number,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/log")
def log_attendance(req: AttendanceLogRequest, db: Session = Depends(get_db)):
    """Manually log an attendance event — used by dispatch/OM during shift."""
    if req.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(400, f"Invalid event_type. Must be one of: {sorted(VALID_EVENT_TYPES)}")
    if req.reason_code and req.reason_code not in VALID_REASON_CODES:
        raise HTTPException(400, f"Invalid reason_code. Must be one of: {sorted(VALID_REASON_CODES)}")

    event_date = date.fromisoformat(req.event_date) if req.event_date else datetime.now(PACIFIC).date()

    call_time = None
    if req.call_time:
        try:
            call_time = datetime.fromisoformat(req.call_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(400, "Invalid call_time format. Use ISO 8601.")

    hours_before, compliant = (None, None)
    if call_time and req.scheduled_wave:
        hours_before, compliant = _calc_compliance(call_time, req.scheduled_wave, event_date)

    is_missed = req.event_type in MISSED_TYPES
    count = _missed_shift_count(req.driver_name, event_date, db)
    if is_missed:
        count += 1
    resign_flag = count >= 2

    # Match to roster. resolve_roster_entry() (exact match, then token-
    # overlap fallback) — a plain exact/lowercase-only match here left
    # roster_id null/wrong for the same middle-name mismatches fixed
    # elsewhere in the driver-identity refactor, which in turn kept
    # rostering.py's _called_out_today() from finding this event via its
    # roster_id check.
    roster_entry = resolve_roster_entry(req.driver_name, db)

    event = AttendanceEvent(
        driver_name=req.driver_name,
        roster_id=roster_entry.id if roster_entry else None,
        event_date=event_date,
        event_type=req.event_type,
        reason_code=req.reason_code,
        call_time=call_time,
        scheduled_wave=req.scheduled_wave,
        hours_before_shift=Decimal(str(hours_before)) if hours_before is not None else None,
        compliant=compliant,
        is_missed=is_missed,
        missed_shift_count=count,
        voluntary_resign_flag=resign_flag,
        notes=req.notes,
        logged_by=req.logged_by,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    result = _event_to_dict(event)
    if resign_flag:
        result["alert"] = f"⚠️ {req.driver_name} has {count} missed shifts in the last 60 days — potential voluntary resignation per handbook."
    if compliant is False:
        result["compliance_alert"] = f"⚠️ Call-in was {abs(hours_before or 0):.1f} hrs before shift — less than the required 4 hours."

    return result


@router.get("/today")
def attendance_today(for_date: Optional[str] = None, db: Session = Depends(get_db)):
    """All attendance events logged for today (or a specific date)."""
    target = date.fromisoformat(for_date) if for_date else datetime.now(PACIFIC).date()
    events = (
        db.query(AttendanceEvent)
        .filter(AttendanceEvent.event_date == target)
        .order_by(AttendanceEvent.created_at)
        .all()
    )
    return {
        "date": target.isoformat(),
        "total": len(events),
        "events": [_event_to_dict(e) for e in events],
    }


@router.get("/driver/{driver_name}")
def attendance_driver(driver_name: str, days: int = 60, db: Session = Depends(get_db)):
    """Full attendance history for a specific driver (trailing N days)."""
    since = datetime.now(PACIFIC).date() - timedelta(days=days)
    events = (
        db.query(AttendanceEvent)
        .filter(
            func.lower(AttendanceEvent.driver_name) == driver_name.lower(),
            AttendanceEvent.event_date >= since,
        )
        .order_by(AttendanceEvent.event_date.desc())
        .all()
    )
    missed = sum(1 for e in events if e.is_missed)
    non_compliant = sum(1 for e in events if e.compliant is False)
    return {
        "driver_name": driver_name,
        "days": days,
        "total_events": len(events),
        "missed_shifts": missed,
        "non_compliant_callins": non_compliant,
        "voluntary_resign_risk": missed >= 2,
        "events": [_event_to_dict(e) for e in events],
    }


@router.get("/missed-shifts")
def missed_shifts_report(days: int = 60, db: Session = Depends(get_db)):
    """Drivers with 2+ missed shifts in the trailing N days — HR-03 flag."""
    since = datetime.now(PACIFIC).date() - timedelta(days=days)
    rows = (
        db.query(AttendanceEvent.driver_name, func.count(AttendanceEvent.id).label("missed_count"))
        .filter(
            AttendanceEvent.is_missed == True,
            AttendanceEvent.event_date >= since,
        )
        .group_by(AttendanceEvent.driver_name)
        .having(func.count(AttendanceEvent.id) >= 2)
        .order_by(func.count(AttendanceEvent.id).desc())
        .all()
    )
    return {
        "days": days,
        "flagged_count": len(rows),
        "drivers": [
            {
                "driver_name": r.driver_name,
                "missed_shifts": r.missed_count,
                "voluntary_resign_risk": r.missed_count >= 2,
            }
            for r in rows
        ],
    }


@router.get("/compliance")
def compliance_report(for_date: Optional[str] = None, db: Session = Depends(get_db)):
    """4-hour call-in rule compliance report for a given date."""
    target = date.fromisoformat(for_date) if for_date else datetime.now(PACIFIC).date()
    events = (
        db.query(AttendanceEvent)
        .filter(
            AttendanceEvent.event_date == target,
            AttendanceEvent.event_type == "call_in",
        )
        .all()
    )
    compliant = [e for e in events if e.compliant is True]
    non_compliant = [e for e in events if e.compliant is False]
    unknown = [e for e in events if e.compliant is None]

    return {
        "date": target.isoformat(),
        "total_callins": len(events),
        "compliant": len(compliant),
        "non_compliant": len(non_compliant),
        "unknown": len(unknown),
        "details": [_event_to_dict(e) for e in events],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public callout endpoints (no auth — driver-facing)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Attendance point system (HRM-023.1)
# ─────────────────────────────────────────────────────────────────────────────

POINT_VALUES: dict[str, float] = {
    "no_show": 5.0,          # No Call / No Show
    "call_in": 2.0,          # Absence with notification
    "late_arrival": 1.0,     # Late / Tardy
    "early_departure": 0.5,  # Early departure / refusing requested work
    "present": 0.0,
    "excused": 0.0,
}

POINT_THRESHOLDS = [
    (5.0,  "Written Warning"),
    (7.5,  "Final Warning or Suspension"),
    (10.0, "Termination"),
]


def _event_points(event_type: str) -> float:
    return POINT_VALUES.get(event_type, 0.0)


def _attendance_status(points: float) -> str:
    if points >= 10.0:
        return "termination"
    if points >= 7.5:
        return "final_warning"
    if points >= 5.0:
        return "written_warning"
    return "good"


def _next_threshold(points: float) -> dict:
    for threshold, label in POINT_THRESHOLDS:
        if points < threshold:
            return {"points": threshold, "label": label, "points_away": round(threshold - points, 1)}
    return {"points": 10.0, "label": "Termination", "points_away": 0.0}


def _driver_points_summary(driver_name: str, db: Session) -> dict:
    today = datetime.now(PACIFIC).date()
    since = today - timedelta(days=60)
    events = (
        db.query(AttendanceEvent)
        .filter(
            func.lower(AttendanceEvent.driver_name) == driver_name.lower(),
            AttendanceEvent.event_date >= since,
            AttendanceEvent.event_date <= today,
        )
        .all()
    )
    current = sum(_event_points(e.event_type) for e in events)
    return {
        "current_points": current,
        "status": _attendance_status(current),
        "next_threshold": _next_threshold(current),
        "event_count": len(events),
        "period_start": since.isoformat(),
    }


NOTIFY_CHANNEL = os.getenv("SLACK_NOTIFY_CHANNEL", "C0AF48TPAMV")

REASON_LABELS = {
    "sick": "Sick",
    "personal": "Personal",
    "family": "Family emergency",
    "weather": "Weather",
    "transportation": "Transportation",
    "doctor_appointment": "Doctor's Appointment",
    "childcare": "Childcare / School Issue",
    "no_call": "No call / No show",
    "other": "Other",
}


def _notify_dispatch_callout(
    driver_name: str,
    reason_code: str,
    wave: Optional[str],
    notes: Optional[str],
    compliant: Optional[bool],
    hours_before: Optional[float],
) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return
    try:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        wave_text = f" · Wave {wave}" if wave else ""
        compliance_text = ""
        if compliant is True:
            compliance_text = f"  ✅ {hours_before:.1f}h before shift"
        elif compliant is False:
            compliance_text = f"  ⚠️ *{abs(hours_before or 0):.1f}h before shift — non-compliant*"
        notes_text = f"\n> _{notes}_" if notes else ""
        signed_text = f"\n✍️ _Signed by driver electronically_" if notes and "Emergency:" not in (notes or "") else (f"\n✍️ _Signed by driver electronically_" if notes else "\n✍️ _Signed by driver electronically_")
        text = (
            f"📞 *Driver Call-Out*{wave_text}\n"
            f"*Driver:* {driver_name}\n"
            f"*Reason:* {REASON_LABELS.get(reason_code, reason_code)}{compliance_text}{notes_text}"
            f"{signed_text}"
        )
        client.chat_postMessage(channel=NOTIFY_CHANNEL, text=text)
    except Exception as exc:
        logger.warning("Slack callout notification failed: %s", exc)


@router.get("/roster-names")
def roster_names(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Public — driver names for callout page dropdown.
    If date (YYYY-MM-DD) is provided, returns only drivers scheduled for that date
    from driver_schedule_entries. Falls back to full active roster if no schedule data."""
    if date:
        try:
            from api.src.database import DriverScheduleEntry
            from datetime import date as _date
            sched_date = _date.fromisoformat(date)
            rows = (
                db.query(DriverScheduleEntry.driver_name)
                .filter(DriverScheduleEntry.schedule_date == sched_date)
                .order_by(DriverScheduleEntry.driver_name)
                .all()
            )
            if rows:
                return {"names": [r.driver_name for r in rows], "source": "schedule"}
        except Exception:
            pass  # Fall through to full roster
    rows = (
        db.query(DriverRosterEntry.payroll_name)
        .filter(DriverRosterEntry.is_active == True)
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    return {"names": [r.payroll_name for r in rows], "source": "roster"}


@router.get("/schedule-dates")
def schedule_dates(db: Session = Depends(get_db)):
    """Public — dates that have schedule data, for the callout date picker."""
    from api.src.database import DriverScheduleEntry
    rows = (
        db.query(DriverScheduleEntry.schedule_date)
        .distinct()
        .order_by(DriverScheduleEntry.schedule_date)
        .all()
    )
    return {"dates": [r.schedule_date.isoformat() for r in rows]}


@router.get("/verify-callout-token")
def verify_callout_token(token: str):
    """Public — validate a callout token issued by any platform adapter (Slack, etc.).
    Returns driver_name and shift_date if valid; 401 if expired or tampered."""
    import jwt as _jwt
    import os
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("purpose") != "callout":
            raise HTTPException(401, "Invalid token purpose.")
        return {
            "driver_name": payload["driver_name"],
            "shift_date": payload.get("shift_date"),
        }
    except _jwt.ExpiredSignatureError:
        raise HTTPException(401, "Callout link has expired. Ask dispatch to send a new one.")
    except Exception:
        raise HTTPException(401, "Invalid callout link.")


@router.post("/seed-roster-from-schedule")
def seed_roster_from_schedule(db: Session = Depends(get_db)):
    """One-time migration: populate driver_roster from driver_schedule_entries
    for any driver not already in the roster. Default PIN = 1234."""
    from api.src.database import DriverScheduleEntry
    names = {r.driver_name for r in db.query(DriverScheduleEntry.driver_name).all()}
    existing = {
        r.payroll_name
        for r in db.query(DriverRosterEntry.payroll_name)
            .filter(DriverRosterEntry.payroll_name.in_(list(names)))
            .all()
    }
    added = 0
    for name in names - existing:
        db.add(DriverRosterEntry(payroll_name=name, is_active=True, ssn_last4="1234"))
        added += 1
    db.commit()
    return {"seeded": added, "total_schedule_names": len(names), "already_existed": len(existing)}


@router.get("/roster-list")
def roster_list(db: Session = Depends(get_db)):
    """Admin — roster with PIN status for PIN management UI (no PIN values returned)."""
    rows = (
        db.query(DriverRosterEntry.id, DriverRosterEntry.payroll_name, DriverRosterEntry.ssn_last4)
        .filter(DriverRosterEntry.is_active == True)
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    return {
        "drivers": [
            {"id": r.id, "payroll_name": r.payroll_name, "has_pin": bool(r.ssn_last4)}
            for r in rows
        ]
    }


@router.get("/driver-status-by-token")
def driver_status_by_token(token: str, db: Session = Depends(get_db)):
    """Token-gated driver status — called by callout page when opened via Slack link.
    No PIN required; the signed token is the identity proof."""
    import jwt as _jwt, os
    secret = os.getenv("JWT_SECRET", "dev-secret")
    try:
        payload = _jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("purpose") != "callout":
            raise HTTPException(401, "Invalid token.")
        driver_name = payload["driver_name"]
    except _jwt.ExpiredSignatureError:
        raise HTTPException(401, "Callout link has expired. Ask dispatch to resend.")
    except Exception:
        raise HTTPException(401, "Invalid callout link.")

    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()
    if not roster_entry:
        raise HTTPException(404, "Driver not found in roster.")

    return _build_driver_status_response(roster_entry, db)


def _build_driver_status_response(roster_entry: DriverRosterEntry, db: Session) -> dict:
    """Shared logic for driver-status and driver-status-by-token."""
    summary = _driver_points_summary(roster_entry.payroll_name, db)
    callout_pts = POINT_VALUES["call_in"]
    projected = summary["current_points"] + callout_pts

    today = datetime.now(PACIFIC).date()
    try:
        patterns = _detect_callout_patterns(roster_entry.payroll_name, today, db)
    except Exception:
        patterns = []

    # Third-callout-in-60-days lockout — added 2026-08-02 per explicit
    # request. _missed_shift_count already counts every self-reported
    # callout in the trailing 60 days (submit_callout() sets is_missed=True
    # unconditionally); this attempt would be the 3rd once that count
    # reaches 2 already on file. Locks the *tool*, not the driver's ability
    # to report an absence -- they still must call dispatch and speak to
    # them directly, which is the whole point.
    missed_count = _missed_shift_count(roster_entry.payroll_name, today, db)
    third_callout_lockout = missed_count >= 2

    return {
        "driver_name": roster_entry.payroll_name,
        **summary,
        "callout_points_added": callout_pts,
        "projected_total": projected,
        "projected_status": _attendance_status(projected),
        "projected_next_threshold": _next_threshold(projected),
        "is_default_pin": roster_entry.ssn_last4 == "1234",
        "patterns": patterns,
        "third_callout_lockout": third_callout_lockout,
        "dispatch_phone": DISPATCH_PHONE_NUMBER if third_callout_lockout else None,
    }


@router.get("/driver-status")
def driver_status(driver_name: str, ssn_last4: str, db: Session = Depends(get_db)):
    """
    Public — driver's 60-day attendance point summary. PIN-gated.
    Called by the callout page after PIN verification to show the driver their standing.
    """
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()

    if not _pin_matches(roster_entry, ssn_last4, db):
        raise HTTPException(401, "Name or PIN is incorrect.")

    return _build_driver_status_response(roster_entry, db)


@router.get("/callout/reason-check")
def callout_reason_check(reason_code: str, family_who: Optional[str] = None, lives_with_family: Optional[bool] = None):
    """Public — the callout page calls this the moment a driver picks a
    reason, so the pushback message (if any) shows immediately rather
    than only being discovered at final submit. Re-checked server-side
    in submit_callout() regardless, so this is a UX convenience, not the
    actual enforcement point."""
    if reason_code not in VALID_REASON_CODES:
        raise HTTPException(400, "Invalid reason.")
    valid, message = check_reason_validity(reason_code, family_who, lives_with_family)
    return {"valid": valid, "message": message}


class CalloutBlockedAttemptRequest(BaseModel):
    driver_name: str
    ssn_last4: Optional[str] = None
    callout_token: Optional[str] = None


@router.post("/callout/log-blocked-attempt")
def log_callout_blocked_attempt(req: CalloutBlockedAttemptRequest, db: Session = Depends(get_db)):
    """Records that a driver hit the third-callout-in-60-days lockout and
    was sent to call dispatch directly instead of completing the normal
    self-service flow. Added 2026-08-02 per explicit request: the tool
    itself locks (still reachable, still logs the attempt), the driver
    does not. Zero attendance-point impact by design -- event_type isn't
    in POINT_VALUES and is_missed=False, so this never double-counts
    toward the same lockout or a driver's point total; whatever dispatch
    logs after actually talking to the driver is the real record."""
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == req.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()
    if not roster_entry:
        raise HTTPException(401, "Name or PIN is incorrect.")

    if req.callout_token:
        import jwt as _jwt, os as _os
        try:
            payload = _jwt.decode(req.callout_token, _os.getenv("JWT_SECRET", "dev-secret"), algorithms=["HS256"])
            if payload.get("purpose") != "callout" or payload.get("driver_name", "").lower() != roster_entry.payroll_name.lower():
                raise HTTPException(401, "Invalid callout token.")
        except _jwt.ExpiredSignatureError:
            raise HTTPException(401, "Callout link has expired.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(401, "Invalid callout token.")
    else:
        if not _pin_matches(roster_entry, req.ssn_last4, db):
            raise HTTPException(401, "Name or PIN is incorrect.")

    today = datetime.now(PACIFIC).date()
    event = AttendanceEvent(
        driver_name=roster_entry.payroll_name,
        roster_id=roster_entry.id,
        event_date=today,
        event_type="callout_tool_blocked",
        is_missed=False,
        notes=(
            "Driver attempted a callout via the self-service tool but was on "
            "their 3rd callout within 60 days -- directed to call dispatch "
            "directly. No points applied here; see dispatch's own log entry "
            "for the real record."
        ),
        logged_by="System (callout tool 3rd-callout lockout)",
    )
    db.add(event)
    db.commit()
    return {"status": "logged", "driver_name": roster_entry.payroll_name, "dispatch_phone": DISPATCH_PHONE_NUMBER}


@router.post("/callout")
def submit_callout(req: CalloutRequest, db: Session = Depends(get_db)):
    """
    Public — driver self-reports absence via mobile callout page.
    PIN = last 4 SSN digits (same as ADP kiosk).
    """
    if req.reason_code not in VALID_REASON_CODES:
        raise HTTPException(400, "Invalid reason.")

    reason_valid, reason_message = check_reason_validity(req.reason_code, req.family_who, req.lives_with_family)
    if not reason_valid and not req.reason_override_ack:
        # Re-validated server-side regardless of what the reason-check
        # endpoint already told the frontend -- a modified client
        # shouldn't be able to skip straight past this. 409, not 400: the
        # request itself is well-formed, it just needs the driver to see
        # the pushback and explicitly acknowledge before it'll go through.
        raise HTTPException(status_code=409, detail=reason_message)

    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == req.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()

    if not roster_entry:
        raise HTTPException(401, "Name or PIN is incorrect.")

    # Accept either a valid callout token (Slack flow) or a PIN (manual flow)
    if req.callout_token:
        import jwt as _jwt, os as _os
        try:
            payload = _jwt.decode(req.callout_token, _os.getenv("JWT_SECRET", "dev-secret"), algorithms=["HS256"])
            if payload.get("purpose") != "callout" or payload.get("driver_name", "").lower() != roster_entry.payroll_name.lower():
                raise HTTPException(401, "Invalid callout token.")
        except _jwt.ExpiredSignatureError:
            raise HTTPException(401, "Callout link has expired.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(401, "Invalid callout token.")
    else:
        if not _pin_matches(roster_entry, req.ssn_last4, db):
            raise HTTPException(401, "Name or PIN is incorrect.")

    today = datetime.now(PACIFIC).date()
    call_time = datetime.utcnow()

    hours_before, compliant = (None, None)
    if req.scheduled_wave:
        hours_before, compliant = _calc_compliance(call_time, req.scheduled_wave, today)

    count = _missed_shift_count(req.driver_name, today, db) + 1
    resign_flag = count >= 2

    # Check if driver is scheduled for the callout shift date
    shift_date = today
    not_scheduled = False
    if req.shift_date:
        try:
            shift_date = date.fromisoformat(req.shift_date)
        except ValueError:
            pass
    try:
        from api.src.database import DriverScheduleEntry
        # Prefer roster_id (set at ingest time — driver_identity.py); fall
        # back to name matching only for rows that predate the
        # driver-identity refactor / haven't been backfilled yet.
        scheduled = (
            db.query(DriverScheduleEntry)
            .filter(DriverScheduleEntry.schedule_date == shift_date, DriverScheduleEntry.roster_id == roster_entry.id)
            .first()
        )
        if not scheduled:
            scheduled = db.query(DriverScheduleEntry).filter(
                DriverScheduleEntry.schedule_date == shift_date,
                func.lower(DriverScheduleEntry.driver_name) == roster_entry.payroll_name.lower(),
            ).first()
        if not scheduled:
            from api.src.driver_identity import _tokens, TOKEN_MATCH_THRESHOLD
            name_tokens = _tokens(roster_entry.payroll_name)
            if name_tokens:
                best_candidate = None
                best_score = 0
                for candidate in db.query(DriverScheduleEntry).filter(DriverScheduleEntry.schedule_date == shift_date).all():
                    score = len(name_tokens & _tokens(candidate.driver_name))
                    if score >= TOKEN_MATCH_THRESHOLD and score > best_score:
                        best_candidate = candidate
                        best_score = score
                scheduled = best_candidate
        not_scheduled = scheduled is None
    except Exception:
        pass

    notes_with_flag = req.notes or ""
    if req.reason_code == "family" and req.family_who:
        notes_with_flag = f"Family: {req.family_who.title()} | Lives with driver: {'Yes' if req.lives_with_family else 'No'} {notes_with_flag}".strip()
    if not reason_valid:
        notes_with_flag = f"[UNAUTHORIZED — driver acknowledged and submitted anyway] {notes_with_flag}".strip()
    if not_scheduled:
        notes_with_flag = f"[NOT ON SCHEDULE FOR {shift_date}] {notes_with_flag}".strip()

    event = AttendanceEvent(
        driver_name=roster_entry.payroll_name,
        roster_id=roster_entry.id,
        event_date=shift_date,
        event_type="call_in",
        reason_code=req.reason_code,
        reason_valid=reason_valid,
        call_time=call_time,
        scheduled_wave=req.scheduled_wave,
        hours_before_shift=Decimal(str(hours_before)) if hours_before is not None else None,
        compliant=compliant,
        is_missed=True,
        missed_shift_count=count,
        voluntary_resign_flag=resign_flag,
        notes=notes_with_flag,
        logged_by="Driver (self-reported via callout page)",
        signature_name=req.signature_name,
        signature_at=datetime.utcnow() if req.signature_name else None,
    )
    db.add(event)
    db.commit()

    # Once showtimes are out for this date, a tight roster means the
    # self-service path stops being enough -- the callout is still
    # logged above, but the driver gets sent to a real phone call
    # instead of a confirmation screen. Checked BEFORE queuing the
    # notification so the alert to #nday-mgt can flag it too.
    must_call_dispatch = _showtimes_published(shift_date, db) and (
        _get_replacement_pool(shift_date, roster_entry.payroll_name, req.scheduled_wave, db)[1]
    )

    # Queue callout notification for #nday-mgt
    wave_time = scheduled.wave_time if (scheduled and hasattr(scheduled, "wave_time")) else req.scheduled_wave
    roster_tight = queue_callout_notification(
        event.id, roster_entry.payroll_name, req.reason_code,
        shift_date, wave_time, db,
        must_call_dispatch=must_call_dispatch,
    )

    # Return updated points summary so the confirmation screen can show the new total
    updated_summary = _driver_points_summary(roster_entry.payroll_name, db)

    return {
        "status": "received",
        "driver_name": roster_entry.payroll_name,
        "compliant": compliant,
        "not_scheduled": not_scheduled,
        "shift_date": shift_date.isoformat(),
        "hours_before_shift": float(hours_before) if hours_before is not None else None,
        "points_added": POINT_VALUES["call_in"],
        "new_total_points": updated_summary["current_points"],
        "new_status": updated_summary["status"],
        "next_threshold": updated_summary["next_threshold"],
        "roster_tight": roster_tight,
        "reason_valid": reason_valid,
        "must_call_dispatch": must_call_dispatch,
        "dispatch_phone": DISPATCH_PHONE_NUMBER if must_call_dispatch else None,
        "unauthorized_message": (
            "This is not a valid reason for a callout, so this has been logged as UNAUTHORIZED — "
            "you are expected to report to work."
            if not reason_valid else None
        ),
    }


@router.post("/callout/change-pin")
def change_driver_pin(req: ChangePinRequest, db: Session = Depends(get_db)):
    """
    Public — driver sets a personal PIN after logging in with the default 1234.
    Requires the current PIN to authenticate before allowing the change.
    """
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == req.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()

    if not _pin_matches(roster_entry, req.current_pin, db):
        raise HTTPException(401, "Name or PIN is incorrect.")

    if not req.new_pin.isdigit() or len(req.new_pin) != 4:
        raise HTTPException(400, "New PIN must be exactly 4 digits.")

    if req.new_pin == "1234":
        raise HTTPException(400, "Please choose a PIN other than the default (1234).")

    roster_entry.ssn_last4 = req.new_pin
    db.commit()
    return {"ok": True, "driver_name": roster_entry.payroll_name}


@router.get("/callout/family-pattern")
def family_pattern_check(
    driver_name: str,
    ssn_last4: str,
    family_who: str,
    db: Session = Depends(get_db),
):
    """
    Public / PIN-gated — check if a specific family member has appeared in prior
    family emergency call-outs for this driver in the last 60 days.
    Called from the callout page when the driver selects who the emergency involves.
    """
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()
    if not _pin_matches(roster_entry, ssn_last4, db):
        raise HTTPException(401, "Name or PIN is incorrect.")

    since_60 = datetime.now(PACIFIC).date() - timedelta(days=60)
    prior = db.query(AttendanceEvent).filter(
        func.lower(AttendanceEvent.driver_name) == roster_entry.payroll_name.lower(),
        AttendanceEvent.reason_code == "family",
        AttendanceEvent.event_date >= since_60,
    ).all()

    count = 0
    for e in prior:
        if e.notes:
            m = re.search(r"Pertains to:\s*(\w+)", e.notes, re.IGNORECASE)
            if m and m.group(1).lower() == family_who.lower():
                count += 1

    if count == 0:
        return {"has_pattern": False, "count": 0, "message": None}

    first  = _first_name(roster_entry.payroll_name)
    member = family_who.capitalize()
    pronoun = "they" if member in ("Father", "Mother", "Spouse") else "they"

    # Tactful message based on how many times this specific member has appeared
    if count >= 2 and member in ("Father", "Mother"):
        msg = (
            f"Hey {first} — we show {count} prior family emergencies involving your {member} "
            f"in the last 60 days. We genuinely hope {pronoun} are doing better. "
            f"If this is an ongoing situation, your manager may be able to help with scheduling accommodations."
        )
    elif count >= 2:
        msg = (
            f"Hey {first} — your {member} has been involved in {count} family emergency "
            f"call-outs in the last 60 days. We hope everything is improving."
        )
    else:
        msg = (
            f"Hey {first} — we have a prior family emergency call-out involving your {member} "
            f"in the last 60 days. We hope {pronoun} are doing okay."
        )

    return {"has_pattern": True, "count": count, "message": msg}


@router.patch("/roster/{driver_id}/pin")
def set_driver_pin(
    driver_id: int,
    body: SetPinRequest,
    db: Session = Depends(get_db),
):
    """Admin — set the callout page PIN (SSN last 4) for a driver. Called from ProtectedRoute page."""
    if not body.ssn_last4.isdigit() or len(body.ssn_last4) != 4:
        raise HTTPException(400, "PIN must be exactly 4 digits.")
    entry = db.query(DriverRosterEntry).filter(DriverRosterEntry.id == driver_id).first()
    if not entry:
        raise HTTPException(404, "Driver not found.")
    entry.ssn_last4 = body.ssn_last4
    db.commit()
    return {"status": "ok", "driver_name": entry.payroll_name}


@router.post("/roster/import-ssn-last4")
def import_ssn_last4(
    file: UploadFile = File(...),
    dry_run: bool = True,
    db: Session = Depends(get_db),
):
    """
    Bulk-import real SSN-last-4 PINs from an ADP export (columns: Associate ID,
    Legal First Name, Legal Last Name, Legal Middle Name, Salutation, last 4).

    Matches each row to driver_roster by first+last name token overlap. Only
    overwrites entries currently on the default placeholder PIN ("1234") or
    blank — never clobbers a PIN a driver has already personalized via
    self-service change.

    dry_run=true (default) reports match counts without writing anything;
    call again with dry_run=false to commit.
    """
    import pandas as pd
    from io import BytesIO

    content = file.file.read()
    df = pd.read_excel(BytesIO(content))

    from api.src.driver_identity import resolve_roster_entry

    matched = updated = skipped_custom_pin = 0
    unmatched: list[str] = []

    for _, row in df.iterrows():
        first = str(row.get("Legal First Name") or "").strip()
        last = str(row.get("Legal Last Name") or "").strip()
        raw_last4 = str(row.get("last 4") or "").strip()
        last4 = raw_last4.zfill(4) if raw_last4.isdigit() else ""
        if not first or not last or not last4:
            continue

        best = resolve_roster_entry(f"{first} {last}", db)

        if best:
            matched += 1
            if best.ssn_last4 in (None, "", "1234"):
                if not dry_run:
                    best.ssn_last4 = last4
                updated += 1
            else:
                skipped_custom_pin += 1
        else:
            unmatched.append(f"{first} {last}")

    if not dry_run:
        db.commit()

    return {
        "dry_run": dry_run,
        "total_rows": len(df),
        "matched": matched,
        "updated": updated,
        "skipped_custom_pin": skipped_custom_pin,
        "unmatched_count": len(unmatched),
        "unmatched_names": unmatched[:50],
    }


# ─────────────────────────────────────────────────────────────────────────────
# RingCentral Webhook
# ─────────────────────────────────────────────────────────────────────────────

RC_VERIFICATION_TOKEN = os.getenv("RINGCENTRAL_WEBHOOK_TOKEN", "")


@router.post("/ringcentral-webhook")
async def ringcentral_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives inbound call events from RingCentral.
    RingCentral sends a validation token header on first setup — we return it to confirm.
    On live events, we log the call and auto-match to a driver by phone number.
    """
    # RingCentral subscription validation handshake
    validation_token = request.headers.get("Validation-Token")
    if validation_token:
        return {"validationToken": validation_token}

    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    # Extract call details from RingCentral event body
    # RingCentral telephony/sessions event structure
    body_data = payload.get("body", {})
    parties = body_data.get("parties", [])

    for party in parties:
        direction = party.get("direction", "")
        if direction != "Inbound":
            continue

        from_info = party.get("from", {})
        to_info = party.get("to", {})
        caller_number = from_info.get("phoneNumber", "")
        called_number = to_info.get("phoneNumber", "")
        call_id = body_data.get("telephonySessionId") or payload.get("uuid", "")
        received_at = datetime.utcnow()

        # Deduplicate
        if call_id and db.query(RingCentralCallLog).filter(
            RingCentralCallLog.call_id == call_id
        ).first():
            continue

        # Match to driver
        matched_entry = _match_driver_by_phone(caller_number, db)

        rc_log = RingCentralCallLog(
            call_id=call_id,
            caller_number=caller_number,
            called_number=called_number,
            received_at=received_at,
            call_direction="Inbound",
            matched_driver=matched_entry.payroll_name if matched_entry else None,
            matched_roster_id=matched_entry.id if matched_entry else None,
            processed=False,
            raw_payload=body.decode("utf-8", errors="replace"),
        )
        db.add(rc_log)
        db.flush()

        # Auto-create attendance event if matched to a driver
        if matched_entry:
            today = datetime.now(PACIFIC).date()
            event = AttendanceEvent(
                driver_name=matched_entry.payroll_name,
                roster_id=matched_entry.id,
                event_date=today,
                event_type="call_in",
                call_time=received_at,
                ringcentral_call_id=call_id,
                caller_number=caller_number,
                logged_by="RingCentral (auto)",
                notes="Auto-logged from inbound call — confirm reason code in dashboard.",
            )
            db.add(event)
            db.flush()
            rc_log.attendance_event_id = event.id
            rc_log.processed = True

        db.commit()
        logger.info(
            "RingCentral call logged: %s → %s (driver: %s)",
            caller_number,
            called_number,
            matched_entry.payroll_name if matched_entry else "unmatched",
        )

    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Manager review & countersignature
# ─────────────────────────────────────────────────────────────────────────────

MGT_CHANNEL = "C0BCYAW7QP3"  # #nday-mgt
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
REASON_LABELS = {
    "sick": "Sick", "personal": "Personal", "family": "Family Emergency",
    "weather": "Weather", "transportation": "Transportation",
    "doctor_appointment": "Doctor's Appointment", "childcare": "Childcare / School Issue",
    "other": "Other",
}
MIN_REPLACEMENT_POOL = 2  # fewer available drivers than this = tight roster

# Added 2026-07-31, explicit request: once showtimes have gone out for a
# shift_date AND the roster is already tight (same MIN_REPLACEMENT_POOL
# definition as the existing tight-roster alert), the self-service callout
# page/DM stops being a convenient one-tap action for THAT shift. The
# callout is still logged (see submit_callout()) -- this never tells a
# driver they can't call out, only that they can't use the automated path
# and must call dispatch directly so a real conversation happens while
# there's still time to react.
DISPATCH_PHONE_NUMBER = os.getenv("DISPATCH_PHONE_NUMBER", "775-467-2283")


def _showtimes_published(shift_date: date, db: Session) -> bool:
    """True once the night-before Showtime DM batch has gone out for
    shift_date (any DriverShiftDM row with dm_sent_at set) -- the
    practical marker for "too late for the normal automated callout flow
    to matter without a live conversation," per explicit request."""
    from api.src.database import DriverShiftDM
    return db.query(DriverShiftDM).filter(
        DriverShiftDM.shift_date == shift_date,
        DriverShiftDM.dm_sent_at.isnot(None),
    ).first() is not None


# ── Replacement pool ───────────────────────────────────────────────────────────

def _get_replacement_pool(
    shift_date: date, caller_name: str, wave_time: str | None, db: Session
) -> tuple[list[str], bool]:
    """Return (available_driver_names, is_tight).
    Available = scheduled that day (same wave if known), not yet called out."""
    q = db.query(DriverScheduleEntry).filter(
        DriverScheduleEntry.schedule_date == shift_date,
        func.lower(DriverScheduleEntry.driver_name) != caller_name.lower(),
    )
    if wave_time:
        q = q.filter(DriverScheduleEntry.wave_time == wave_time)
    scheduled = {e.driver_name for e in q.all()}

    already_out = {
        e.driver_name.lower()
        for e in db.query(AttendanceEvent).filter(
            AttendanceEvent.event_date == shift_date,
            AttendanceEvent.is_missed == True,
        ).all()
    }
    available = sorted(n for n in scheduled if n.lower() not in already_out)
    return available, len(available) < MIN_REPLACEMENT_POOL


# ── Slack client helper ────────────────────────────────────────────────────────

def _slack_client():
    from slack_sdk import WebClient
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    return WebClient(token=token)


# ── Immediate tight-roster alert ───────────────────────────────────────────────

def _send_tight_roster_alert(queue_entry: CalloutQueue, reason_code: str, db: Session, must_call_dispatch: bool = False) -> None:
    """Post an urgent alert to #nday-mgt immediately when no replacement is available."""
    client = _slack_client()
    if not client:
        return
    try:
        review_url = f"{FRONTEND_URL}/admin/callout-review/{queue_entry.event_id}"
        reason_label = REASON_LABELS.get(reason_code, reason_code.title())
        date_str = queue_entry.shift_date.strftime("%A, %b %-d")
        wave_str = f" (Wave {queue_entry.wave_time})" if queue_entry.wave_time else ""
        resp = client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=f"ROSTER ALERT: {queue_entry.driver_name} called out — no replacement available",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🚨 ROSTER ALERT — No Replacement Available", "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Driver:*\n{queue_entry.driver_name}"},
                        {"type": "mrkdwn", "text": f"*Date:*\n{date_str}{wave_str}"},
                        {"type": "mrkdwn", "text": f"*Reason:*\n{reason_label}"},
                        {"type": "mrkdwn", "text": "*Replacement:*\n❌ No drivers available"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ The roster is tight for this wave. Immediate action required."
                        + (
                            "\n📞 *Showtimes were already out, so this driver was blocked from the "
                            "automated callout and told to call dispatch directly — expect that call.*"
                            if must_call_dispatch else ""
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "acknowledge_callout_alert",
                            "value": str(queue_entry.id),
                            "text": {"type": "plain_text", "text": "✅  Acknowledge", "emoji": True},
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✍️  Review & Sign", "emoji": True},
                            "url": review_url,
                        },
                    ],
                },
            ],
        )
        queue_entry.alert_slack_ts = resp.get("ts")
        db.commit()
    except Exception as exc:
        logger.warning("Tight-roster alert failed: %s", exc)


# ── Tight-roster reminders ─────────────────────────────────────────────────────

def send_tight_roster_reminders(db: Session) -> int:
    """Re-post to #nday-mgt for any tight-roster callouts not yet acknowledged.
    Called every 15 min by the background loop."""
    from datetime import timezone
    cutoff = datetime.utcnow() - timedelta(hours=12)
    pending = db.query(CalloutQueue).filter(
        CalloutQueue.roster_tight == True,
        CalloutQueue.acknowledged_at == None,
        CalloutQueue.queued_at >= cutoff,
    ).all()
    if not pending:
        return 0

    client = _slack_client()
    if not client:
        return 0

    sent = 0
    for entry in pending:
        try:
            review_url = f"{FRONTEND_URL}/admin/callout-review/{entry.event_id}"
            date_str = entry.shift_date.strftime("%A, %b %-d")
            n = entry.reminder_count + 1
            client.chat_postMessage(
                channel=MGT_CHANNEL,
                text=f"REMINDER #{n}: {entry.driver_name} callout still unacknowledged — no replacement",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"🔴 *Reminder #{n}* — Roster alert for *{entry.driver_name}* "
                                f"({date_str}) has not been acknowledged. "
                                f"No replacement driver is available for this wave."
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "acknowledge_callout_alert",
                                "value": str(entry.id),
                                "text": {"type": "plain_text", "text": "✅  Acknowledge", "emoji": True},
                                "style": "primary",
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✍️  Review & Sign", "emoji": True},
                                "url": review_url,
                            },
                        ],
                    },
                ],
            )
            entry.reminder_count += 1
            entry.last_reminder_at = datetime.utcnow()
            sent += 1
        except Exception as exc:
            logger.warning("Tight-roster reminder failed for queue %d: %s", entry.id, exc)
    db.commit()
    return sent


# ── 8:30 AM morning digest ─────────────────────────────────────────────────────

def send_morning_callout_digest(shift_date: date, db: Session) -> int:
    """Send a single grouped callout digest to #nday-mgt at 8:30 AM.
    Returns number of callouts included."""
    pending = db.query(CalloutQueue).filter(
        CalloutQueue.shift_date == shift_date,
        CalloutQueue.roster_tight == False,
        CalloutQueue.digest_sent_at == None,
    ).all()
    if not pending:
        return 0

    client = _slack_client()
    if not client:
        return 0

    date_str = shift_date.strftime("%A, %B %-d")
    lines = []
    for entry in pending:
        event = db.query(AttendanceEvent).filter(AttendanceEvent.id == entry.event_id).first()
        reason = REASON_LABELS.get(event.reason_code, event.reason_code.title()) if event else "—"
        unauthorized_flag = " ⚠️ *UNAUTHORIZED*" if event and event.reason_valid is False else ""
        pool = json.loads(entry.replacement_pool or "[]")
        wave_str = f" · Wave {entry.wave_time}" if entry.wave_time else ""
        if pool:
            replacement = pool[0]
            extra = f" (+{len(pool)-1} more)" if len(pool) > 1 else ""
            repl_str = f"Replacement: *{replacement}*{extra}"
        else:
            repl_str = "Replacement: *None available*"
        review_url = f"{FRONTEND_URL}/admin/callout-review/{entry.event_id}"
        lines.append(
            f"• <{review_url}|{entry.driver_name}>{wave_str} — {reason}{unauthorized_flag} | {repl_str}"
        )

    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=f"Morning callout digest for {date_str} — {len(pending)} call-out(s)",
            blocks=[
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"📋 Morning Callout Digest — {date_str}",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(lines),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Click a driver name to review & countersign their writeup.",
                        }
                    ],
                },
            ],
        )
        now = datetime.utcnow()
        for entry in pending:
            entry.digest_sent_at = now
        db.commit()
        return len(pending)
    except Exception as exc:
        logger.warning("Morning callout digest failed: %s", exc)
        return 0


# ── Recurring callout summary (9:30 AM - 12:30 PM, every 15 min) ──────────────
# Added 2026-07-30 per explicit HR request: "the callouts are not flagging
# the nday-mgt and nday-hr rooms... we need a summary of callouts that
# happen" sent repeatedly through the morning so it can't be missed and
# actually reaches HR too, not just dispatch. Separate from (additive to)
# send_morning_callout_digest() above, which is a one-time 8:30 AM
# per-callout digest to #nday-mgt only -- this is an always-current
# rolling summary of the whole day so far, reposted every 15 minutes.

_CALLOUT_SUMMARY_WINDOW_START = (9, 30)    # 9:30 AM Pacific
_CALLOUT_SUMMARY_WINDOW_END = (12, 30)     # 12:30 PM Pacific -- "roughly when everybody is on the road"
_CALLOUT_SUMMARY_INTERVAL_MINUTES = 15
_CALLOUT_SUMMARY_KEY_PREFIX = "callout_recurring_summary_"


def send_recurring_callout_summary(db: Session, force: bool = False) -> dict:
    """Repeating summary of today's callouts, posted to BOTH #nday-mgt and
    #nday-hr. force=True bypasses the time window and the 15-minute
    throttle for manual testing/recovery."""
    if not get_flag("CALLOUT_SUMMARY_ACTIVE"):
        return {"status": "inactive", "note": "Set CALLOUT_SUMMARY_ACTIVE=true on Render to enable"}

    now = datetime.now(PACIFIC)
    today = now.date()
    window_start = now.replace(hour=_CALLOUT_SUMMARY_WINDOW_START[0], minute=_CALLOUT_SUMMARY_WINDOW_START[1], second=0, microsecond=0)
    window_end = now.replace(hour=_CALLOUT_SUMMARY_WINDOW_END[0], minute=_CALLOUT_SUMMARY_WINDOW_END[1], second=0, microsecond=0)

    if not force and not (window_start <= now <= window_end):
        return {"status": "outside_window", "date": today.isoformat()}

    state_key = f"{_CALLOUT_SUMMARY_KEY_PREFIX}{today.isoformat()}"
    state = get_reminder_state(db, state_key)
    last_sent_at = datetime.fromisoformat(state["last_sent_at"]) if state.get("last_sent_at") else None
    if not force and last_sent_at and (now - last_sent_at).total_seconds() < _CALLOUT_SUMMARY_INTERVAL_MINUTES * 60:
        return {"status": "throttled", "date": today.isoformat()}

    events = (
        db.query(AttendanceEvent)
        .filter(AttendanceEvent.event_date == today, AttendanceEvent.event_type == "call_in")
        .order_by(AttendanceEvent.call_time)
        .all()
    )
    if not events:
        set_reminder_state(db, state_key, {"last_sent_at": now.isoformat()})
        return {"status": "no_callouts", "date": today.isoformat()}

    lines = []
    for e in events:
        reason = REASON_LABELS.get(e.reason_code, (e.reason_code or "").title())
        flag = " ⚠️ *UNAUTHORIZED*" if e.reason_valid is False else ""
        lines.append(f"• *{e.driver_name}* — {reason}{flag}")

    unauthorized_count = sum(1 for e in events if e.reason_valid is False)
    text = (
        f"📋 *Callout Summary — {now.strftime('%A, %B %-d')}* (as of {now.strftime('%-I:%M %p')} PT)\n"
        f"{len(events)} callout(s) today"
        + (f", {unauthorized_count} unauthorized" if unauthorized_count else "")
        + "\n\n" + "\n".join(lines)
    )

    client = _slack_client()
    if not client:
        return {"status": "no_slack_token"}

    from api.src.routes.document_routing import get_role_slack_ids
    channel_ids = {MGT_CHANNEL} | set(get_role_slack_ids(db, "hr"))
    sent = 0
    for cid in channel_ids:
        try:
            client.chat_postMessage(channel=cid, text=text)
            sent += 1
        except Exception as exc:
            logger.warning("Recurring callout summary post failed for %s: %s", cid, exc)

    set_reminder_state(db, state_key, {"last_sent_at": now.isoformat()})
    return {"status": "sent", "date": today.isoformat(), "callouts": len(events), "channels_sent": sent}


@router.post("/callout/trigger-summary")
def trigger_callout_summary(force: bool = True, db: Session = Depends(get_db)):
    """Manual trigger for testing/recovery — same function the loop calls."""
    return send_recurring_callout_summary(db, force=force)


# ── Queue a callout notification ───────────────────────────────────────────────

def queue_callout_notification(
    event_id: int, driver_name: str, reason_code: str,
    shift_date: date, wave_time: str | None, db: Session,
    must_call_dispatch: bool = False,
) -> bool:
    """Create a CalloutQueue entry. Returns True if roster_tight."""
    available, is_tight = _get_replacement_pool(shift_date, driver_name, wave_time, db)
    entry = CalloutQueue(
        event_id=event_id,
        shift_date=shift_date,
        driver_name=driver_name,
        wave_time=wave_time,
        replacement_pool=json.dumps(available),
        roster_tight=is_tight,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    if is_tight:
        _send_tight_roster_alert(entry, reason_code, db, must_call_dispatch=must_call_dispatch)
    return is_tight


class ManagerSignRequest(BaseModel):
    manager_name: str
    manager_id: Optional[str] = None


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Get a single attendance event for the manager review page."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")
    return {
        "id": event.id,
        "driver_name": event.driver_name,
        "event_date": event.event_date.isoformat() if event.event_date else None,
        "event_type": event.event_type,
        "reason_code": event.reason_code,
        "notes": event.notes,
        "call_time": event.call_time.isoformat() if event.call_time else None,
        "hours_before_shift": float(event.hours_before_shift) if event.hours_before_shift else None,
        "compliant": event.compliant,
        "scheduled_wave": event.scheduled_wave,
        "signature_name": event.signature_name,
        "signature_at": event.signature_at.isoformat() if event.signature_at else None,
        "manager_signature_name": event.manager_signature_name,
        "manager_signature_at": event.manager_signature_at.isoformat() if event.manager_signature_at else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db)):
    """Delete an attendance event — for correcting test/erroneous
    entries (e.g. a real driver used for end-to-end callout-button
    testing). Not exposed on any dashboard UI; call directly when
    cleanup is needed."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")
    db.delete(event)
    db.commit()
    return {"status": "deleted", "event_id": event_id}


@router.post("/events/{event_id}/manager-sign")
def manager_sign_event(
    event_id: int, req: ManagerSignRequest, db: Session = Depends(get_db),
    caller_role: str = Depends(require_any_role("ops_manager")),
):
    """Ops-manager countersigns an attendance writeup. Previously had no
    permission check at all — any caller could sign as anyone; now
    requires an actual ops_manager (or admin) JWT role, per the write-up
    review dashboard's real per-role sign-off requirement."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")
    if not req.manager_name.strip():
        raise HTTPException(400, "Manager name is required.")
    event.manager_signature_name = req.manager_name.strip()
    event.manager_signature_at = datetime.utcnow()
    event.manager_id = req.manager_id
    db.commit()
    return {"status": "signed", "manager_signature_name": event.manager_signature_name}


class DriverSignRequest(BaseModel):
    signature_name: str


@router.post("/events/{event_id}/driver-sign")
def driver_sign_event(event_id: int, req: DriverSignRequest, db: Session = Depends(get_db)):
    """Driver signs their own attendance write-up after the fact — for
    events dispatch logged on the driver's behalf (e.g. a no-show)
    outside the self-service /callout page, which normally sets
    signature_name at creation. Added 2026-07-23 for the outstanding-
    items gate (api/src/outstanding_items.py) — this is the one genuine
    driver-facing gap in the write-up flow (everything else pending is a
    manager/HR countersignature, not a driver action)."""
    event = db.query(AttendanceEvent).filter(AttendanceEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found.")
    if not req.signature_name.strip():
        raise HTTPException(400, "Signature name is required.")
    event.signature_name = req.signature_name.strip()
    event.signature_at = datetime.utcnow()
    db.commit()
    return {"status": "signed", "signature_name": event.signature_name}


@router.get("/unsigned-writeups")
def pending_review(db: Session = Depends(get_db)):
    """Admin — all callout writeups with driver signature but no manager countersignature."""
    events = (
        db.query(AttendanceEvent)
        .filter(
            AttendanceEvent.signature_name != None,
            AttendanceEvent.manager_signature_name == None,
        )
        .order_by(AttendanceEvent.created_at.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "driver_name": e.driver_name,
            "event_date": e.event_date.isoformat() if e.event_date else None,
            "reason_code": e.reason_code,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.post("/eod-unsigned-reminder")
def eod_unsigned_reminder(db: Session = Depends(get_db)):
    """Post a summary of all unsigned writeups to #nday-mgt. Intended to run at EOD."""
    events = (
        db.query(AttendanceEvent)
        .filter(
            AttendanceEvent.signature_name != None,
            AttendanceEvent.manager_signature_name == None,
        )
        .order_by(AttendanceEvent.event_date.desc())
        .all()
    )
    if not events:
        return {"status": "nothing_to_remind", "count": 0}
    try:
        from slack_sdk import WebClient
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise HTTPException(500, "SLACK_BOT_TOKEN not set.")
        client = WebClient(token=token)
        lines = "\n".join(
            f"• <{FRONTEND_URL}/admin/callout-review/{e.id}|{e.driver_name}> — "
            f"{REASON_LABELS.get(e.reason_code, e.reason_code)} "
            f"({e.event_date})"
            for e in events
        )
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text=f"⏰ EOD Reminder: {len(events)} unsigned writeup(s) still need manager review.",
            blocks=[
                {"type": "header", "text": {"type": "plain_text", "text": f"⏰ EOD — {len(events)} Unsigned Writeup(s)", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "The following callout writeups are awaiting manager countersignature:\n\n" + lines}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "Click each driver's name to review and sign."}]},
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"status": "reminded", "count": len(events)}


# ─────────────────────────────────────────────────────────────────────────────
# Callout queue endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/callout-queue/{queue_id}/acknowledge")
def acknowledge_callout_alert(
    queue_id: int,
    acknowledged_by: str = "",
    db: Session = Depends(get_db),
):
    """Mark a tight-roster callout alert as acknowledged (called by Slack interaction handler)."""
    entry = db.query(CalloutQueue).filter(CalloutQueue.id == queue_id).first()
    if not entry:
        raise HTTPException(404, "Queue entry not found.")
    if not entry.acknowledged_at:
        entry.acknowledged_at = datetime.utcnow()
        entry.acknowledged_by = acknowledged_by
        db.commit()
    return {"status": "acknowledged", "queue_id": queue_id, "by": acknowledged_by}


@router.post("/callout-queue/send-digest")
def trigger_morning_digest(shift_date_str: str = "", db: Session = Depends(get_db)):
    """Manually trigger the morning callout digest for a given date (default: today)."""
    try:
        d = date.fromisoformat(shift_date_str) if shift_date_str else datetime.now(PACIFIC).date()
    except ValueError:
        raise HTTPException(400, "Invalid date format.")
    count = send_morning_callout_digest(d, db)
    return {"status": "sent", "callouts_included": count, "date": d.isoformat()}


@router.get("/callout-queue/pending")
def get_pending_queue(db: Session = Depends(get_db)):
    """Admin — list all queued callout notifications not yet sent."""
    rows = (
        db.query(CalloutQueue)
        .filter(CalloutQueue.digest_sent_at == None, CalloutQueue.acknowledged_at == None)
        .order_by(CalloutQueue.queued_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "event_id": r.event_id,
            "shift_date": r.shift_date.isoformat(),
            "driver_name": r.driver_name,
            "wave_time": r.wave_time,
            "roster_tight": r.roster_tight,
            "replacement_pool": json.loads(r.replacement_pool or "[]"),
            "reminder_count": r.reminder_count,
            "queued_at": r.queued_at.isoformat() if r.queued_at else None,
        }
        for r in rows
    ]
