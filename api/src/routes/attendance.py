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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    AttendanceEvent,
    RingCentralCallLog,
    DriverRosterEntry,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/attendance", tags=["attendance"])

PACIFIC = ZoneInfo("America/Los_Angeles")


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
VALID_REASON_CODES = {"sick", "personal", "family", "weather", "transportation", "no_call", "other"}

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

    # Match to roster
    roster_entry = db.query(DriverRosterEntry).filter(
        func.lower(DriverRosterEntry.payroll_name) == req.driver_name.lower(),
        DriverRosterEntry.is_active == True,
    ).first()

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

    return {
        "driver_name": roster_entry.payroll_name,
        **summary,
        "callout_points_added": callout_pts,
        "projected_total": projected,
        "projected_status": _attendance_status(projected),
        "projected_next_threshold": _next_threshold(projected),
        "is_default_pin": roster_entry.ssn_last4 == "1234",
        "patterns": patterns,
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

    if not roster_entry or not roster_entry.ssn_last4 or roster_entry.ssn_last4 != ssn_last4.strip():
        raise HTTPException(401, "Name or PIN is incorrect.")

    return _build_driver_status_response(roster_entry, db)


@router.post("/callout")
def submit_callout(req: CalloutRequest, db: Session = Depends(get_db)):
    """
    Public — driver self-reports absence via mobile callout page.
    PIN = last 4 SSN digits (same as ADP kiosk).
    """
    if req.reason_code not in VALID_REASON_CODES:
        raise HTTPException(400, "Invalid reason.")

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
        if not roster_entry.ssn_last4 or not req.ssn_last4 or roster_entry.ssn_last4 != req.ssn_last4.strip():
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
        scheduled = db.query(DriverScheduleEntry).filter(
            DriverScheduleEntry.schedule_date == shift_date,
            func.lower(DriverScheduleEntry.driver_name) == roster_entry.payroll_name.lower(),
        ).first()
        not_scheduled = scheduled is None
    except Exception:
        pass

    notes_with_flag = req.notes or ""
    if not_scheduled:
        notes_with_flag = f"[NOT ON SCHEDULE FOR {shift_date}] {notes_with_flag}".strip()

    event = AttendanceEvent(
        driver_name=roster_entry.payroll_name,
        roster_id=roster_entry.id,
        event_date=shift_date,
        event_type="call_in",
        reason_code=req.reason_code,
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

    _notify_dispatch_callout(
        roster_entry.payroll_name, req.reason_code,
        req.scheduled_wave, notes_with_flag, compliant, hours_before,
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

    if not roster_entry or not roster_entry.ssn_last4 or roster_entry.ssn_last4 != req.current_pin.strip():
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
    if not roster_entry or not roster_entry.ssn_last4 or roster_entry.ssn_last4 != ssn_last4.strip():
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
