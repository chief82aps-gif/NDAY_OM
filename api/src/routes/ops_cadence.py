"""
Packages + Cortex progress cadence, and the automatic "All In" post --
added 2026-08-04.

After the day's last wave launches, #nday-mgt needs a fresh Packages
export (packages.py) and a fresh Cortex Routes re-upload (the same file
daily_notify.py ingests each morning -- api/src/database.py's `Cortex`
table -- just re-shared periodically through the day) every 60-90 min,
right up through COB. Once both have landed since the last wave
launched AND we're at/after the COB cutoff, this auto-posts "All In" to
#nday-mgt and #dlv3-nday-info. eod_survey.py's existing
_all_in_posted_today() already scans #nday-mgt for the literal text
"all in", so posting it for real here also satisfies that check --
replacing the manual dispatcher post it was originally built around.

COB cutoff = last wave launch time + 11 hours (explicit user rule -- the
day's real length varies with when the last wave actually launches, so
this is not a fixed clock time).

Called every 60s from main.py's background loop, same pattern as
mgt_reminders.py and eod_survey.py.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import (
    get_db, get_reminder_state, set_reminder_state, DailyRouteAssignment, Cortex, PackagesSnapshot,
)
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ops-cadence", tags=["ops-cadence"])

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")           # #nday-mgt
DLV3_INFO_CHANNEL = os.getenv("DLV3_INFO_CHANNEL_ID", "C0AF48TPAMV")  # #dlv3-nday-info
APP_URL = os.getenv("APP_URL", "https://nday-om.vercel.app")

CADENCE_INTERVAL_SECONDS = 75 * 60   # midpoint of "every 60-90 min"
PAST_COB_NAG_SECONDS = 10 * 60       # once overdue, nag as tight as the other can't-miss reminders
COB_OFFSET_HOURS = 11                # All-In cutoff = last wave launch + 11h

_STATE_KEY = "ops_cadence_packages_cortex"
_TIME_FMTS = ("%I:%M %p", "%I:%M%p", "%H:%M", "%I %p")


def _parse_wave_time_of_day(wave_str: Optional[str]):
    """Parse a wave time string (e.g. "8:00 AM") into a time-of-day.
    Deliberately self-contained rather than importing daily_notify.py's
    or rostering.py's own _parse_wave_dt (already duplicated between
    those two, a pre-existing gap this doesn't try to fix here)."""
    if not wave_str:
        return None
    s = str(wave_str).strip()
    for fmt in _TIME_FMTS:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _last_wave_launch_dt(db: Session, today: date) -> Optional[datetime]:
    """The latest wave time scheduled today, as an absolute Pacific
    datetime -- the anchor for both the cadence reminders and the COB
    (last wave + 11h) All-In cutoff."""
    waves = (
        db.query(DailyRouteAssignment.wave)
        .filter(DailyRouteAssignment.assignment_date == today, DailyRouteAssignment.wave.isnot(None))
        .distinct()
        .all()
    )
    latest_time = None
    for (wave_str,) in waves:
        t = _parse_wave_time_of_day(wave_str)
        if t and (latest_time is None or t > latest_time):
            latest_time = t
    if not latest_time:
        return None
    return datetime.combine(today, latest_time, tzinfo=PT)


def _to_naive_utc(dt: datetime) -> datetime:
    """Every imported_at/created_at column in this codebase stores naive
    UTC (datetime.utcnow(), or datetime.now(timezone.utc) written into a
    plain, non-timezone-aware DateTime column, which has the same
    on-disk effect) -- convert the Pacific-aware anchor to match before
    filtering against them."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _packages_uploaded_since(db: Session, since_utc_naive: datetime) -> bool:
    return db.query(PackagesSnapshot).filter(PackagesSnapshot.imported_at >= since_utc_naive).first() is not None


def _cortex_uploaded_since(db: Session, since_utc_naive: datetime, today: date) -> bool:
    return (
        db.query(Cortex)
        .filter(Cortex.assignment_date == today, Cortex.created_at >= since_utc_naive)
        .first() is not None
    )


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _mgt_member_ids(client) -> list[str]:
    try:
        bot_id = client.auth_test().get("user_id")
    except Exception as exc:
        logger.warning("ops_cadence: auth_test failed: %s", exc)
        bot_id = None
    try:
        resp = client.conversations_members(channel=MGT_CHANNEL)
        members = resp.get("members", [])
    except Exception as exc:
        logger.warning("ops_cadence: conversations_members failed: %s", exc)
        return []
    return [m for m in members if m != bot_id]


def _load_state(db: Session) -> dict:
    raw = get_reminder_state(db, _STATE_KEY)
    return {
        "last_sent_at": datetime.fromisoformat(raw["last_sent_at"]) if raw.get("last_sent_at") else None,
        "all_in_posted_date": date.fromisoformat(raw["all_in_posted_date"]) if raw.get("all_in_posted_date") else None,
    }


def _save_state(db: Session, state: dict) -> None:
    set_reminder_state(db, _STATE_KEY, {
        "last_sent_at": state["last_sent_at"].isoformat() if state.get("last_sent_at") else None,
        "all_in_posted_date": state["all_in_posted_date"].isoformat() if state.get("all_in_posted_date") else None,
    })


def _send_cadence_reminder(missing: list[str], urgent: bool) -> None:
    client = _client()
    if not client:
        return
    members = _mgt_member_ids(client)
    if not members:
        return
    label = " and ".join(missing)
    if urgent:
        text = (
            f"⏰ *Past COB* — still need a fresh *{label}* upload before All In can post. "
            f"👉 {APP_URL}/ops-ingest"
        )
    else:
        text = f"🔄 *Progress check-in* — please re-upload the current *{label}* export(s). 👉 {APP_URL}/ops-ingest"
    for uid in members:
        try:
            client.chat_postMessage(channel=uid, text=text)
        except Exception as exc:
            logger.warning("ops_cadence reminder DM failed (%s): %s", uid, exc)


# Short, human variants matching the existing dispatcher convention
# ("NDAY All in. Have a great night and we will see you tomorrow.") --
# rewritten 2026-08-05 per explicit direction: the auto-post had been a
# single, longer, always-identical line ("Packages and Cortex progress
# both confirmed for the COB round"), not the quick human sign-off this
# channel is used to. One picked at random each night, occasionally
# paired with a dad joke.
_ALL_IN_VARIANTS = [
    "NDAY All In. Have a great night, see you tomorrow!",
    "NDAY All In! Everyone's in for the night — see you tomorrow.",
    "That's a wrap — NDAY All In. Have a good one!",
    "NDAY All In. Rest up, we'll do it again tomorrow.",
    "All in for NDAY tonight. Nice work out there — see you tomorrow.",
]

_DAD_JOKES = [
    "Why don't scientists trust atoms? Because they make up everything.",
    "I'd tell you a construction joke, but I'm still working on it.",
    "Why did the van go to therapy? Too many issues.",
    "What do you call a driver who never delivers? Late.",
    "I used to be a baker, but I couldn't make enough dough.",
    "Why don't eggs tell jokes? They'd crack each other up.",
]

_DAD_JOKE_CHANCE = 0.25


def _post_all_in(today: date) -> None:
    client = _client()
    if not client:
        return
    text = random.choice(_ALL_IN_VARIANTS)
    if random.random() < _DAD_JOKE_CHANCE:
        text += f"\n\n😄 {random.choice(_DAD_JOKES)}"
    for channel in (MGT_CHANNEL, DLV3_INFO_CHANNEL):
        try:
            client.chat_postMessage(channel=channel, text=text)
        except Exception as exc:
            logger.warning("All In post failed (%s): %s", channel, exc)


def _post_all_in_blocked_notice() -> None:
    """#nday-mgt only, never #dlv3-nday-info -- that channel only ever gets
    the one-line All In / incidents comment, never internal ops issues."""
    client = _client()
    if not client:
        return
    try:
        client.chat_postMessage(
            channel=MGT_CHANNEL,
            text="🚫 All In summary withheld tonight — tomorrow's Showtime DMs haven't gone out. Check the escalation DM from Blake.",
        )
    except Exception as exc:
        logger.warning("All-In-blocked notice failed: %s", exc)


def run_ops_cadence_check(db: Session, force: bool = False) -> dict:
    """Called every 60s from main.py's background loop. Two jobs:
      1. Nag #nday-mgt every ~75 min (once the last wave has launched)
         for a fresh Packages export + Cortex re-upload.
      2. Once both have landed since last-wave-launch AND we're at/after
         the COB cutoff (last wave + 11h), auto-post "All In" once --
         otherwise nag every 10 min past COB until it lands.
    """
    now_pt = datetime.now(PT)
    today = now_pt.date()

    last_wave_dt = _last_wave_launch_dt(db, today)
    if not last_wave_dt:
        return {"status": "no_wave_data", "date": today.isoformat()}

    if now_pt < last_wave_dt:
        return {"status": "before_last_wave", "last_wave_at": last_wave_dt.isoformat()}

    state = _load_state(db)
    if state.get("all_in_posted_date") == today:
        return {"status": "all_in_already_posted", "date": today.isoformat()}

    cob_dt = last_wave_dt + timedelta(hours=COB_OFFSET_HOURS)
    since_utc = _to_naive_utc(last_wave_dt)
    packages_ok = _packages_uploaded_since(db, since_utc)
    cortex_ok = _cortex_uploaded_since(db, since_utc, today)
    last_sent = state.get("last_sent_at")
    now_naive = now_pt.replace(tzinfo=None)

    if now_pt >= cob_dt:
        if packages_ok and cortex_ok:
            from api.src.routes.rostering import is_all_in_blocked_today
            if is_all_in_blocked_today(db):
                if force or not last_sent or (now_naive - last_sent).total_seconds() >= PAST_COB_NAG_SECONDS:
                    _post_all_in_blocked_notice()
                    state["last_sent_at"] = now_naive
                    _save_state(db, state)
                return {"status": "all_in_blocked_showtime", "date": today.isoformat()}
            _post_all_in(today)
            state["all_in_posted_date"] = today
            _save_state(db, state)
            return {"status": "all_in_posted", "date": today.isoformat()}

        missing = [n for n, ok in (("Packages", packages_ok), ("Cortex", cortex_ok)) if not ok]
        if force or not last_sent or (now_naive - last_sent).total_seconds() >= PAST_COB_NAG_SECONDS:
            _send_cadence_reminder(missing, urgent=True)
            state["last_sent_at"] = now_naive
            _save_state(db, state)
        return {"status": "past_cob_missing", "missing": missing}

    # Between last-wave-launch and COB: recurring nag every ~75 min,
    # regardless of whether an earlier upload already happened today --
    # this is a fresh progress check-in each cycle, not "any upload ever."
    if force or not last_sent or (now_naive - last_sent).total_seconds() >= CADENCE_INTERVAL_SECONDS:
        _send_cadence_reminder(["Packages", "Cortex"], urgent=False)
        state["last_sent_at"] = now_naive
        _save_state(db, state)
        return {"status": "reminder_sent"}

    return {
        "status": "waiting",
        "next_due_at": (last_sent + timedelta(seconds=CADENCE_INTERVAL_SECONDS)).isoformat(),
    }


@router.post("/check")
def manual_check(force: bool = False, db: Session = Depends(get_db)):
    """Manual trigger — same call the background loop makes every 60s."""
    return run_ops_cadence_check(db, force=force)


@router.post("/resend-all-in")
def resend_all_in():
    """One-off duplicate post -- added 2026-08-05 for a night where the
    automated All In already fired once (based on earlier uploads) but a
    second, up-to-date post was wanted after the truly final uploads
    landed. Does not touch the once-per-day state, so tomorrow's cycle is
    unaffected either way."""
    _post_all_in(date.today())
    return {"status": "sent"}
