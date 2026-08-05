"""
Driver Daily Progress DM — added 2026-08-05, per explicit request for an
upbeat mid-day check-in DM showing a driver their delivery progress and
estimated time remaining.

Data sources, confirmed deliberately:
  - Packages export (packages.py's PackagesSnapshot/PackagesRecord) --
    one row per currently NON-delivered package, re-pulled multiple times
    a day. Gives a live "still remaining" count per driver.
  - DailyRouteAssignment -- planned total packages + planned route
    duration for the day (populated from DOP/Route Sheet, NOT Cortex).
  - DriverShiftDM.arrived_at -- "I've Arrived" tap time, for elapsed-vs-
    planned-duration framing.
Cortex is deliberately NOT used here -- confirmed elsewhere this same
session (ops_daily_digest.py) that CortexSnapshot has no real ingest path
in practice and stays empty; Cortex the historical route table is planned/
assignment data, not a live delivered/remaining signal.

TESTING PHASE: hard-restricted to _TESTING_DRIVER_NAMES (Collin LaTour
only) per explicit instruction ("I would like to use Collin Latour as the
only recipient for the first several rounds of testing"). This allowlist
gates every send path (manual trigger AND the automatic per-ingest hook in
ops_ingest.py) -- do not widen it without asking first.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.src.database import get_db, DailyRouteAssignment, DriverShiftDM, PackagesRecord
from api.src.routes.packages import get_latest_snapshot
from api.src.timezone import PACIFIC as PT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/driver-progress", tags=["driver-progress"])

# Both name forms needed: "Collin Jonathan LaTour" is the ADP roster
# payroll_name (used for Slack ID resolution), "Collin LaTour" is the
# shorter form DOP/Route Sheet ingest stores on DailyRouteAssignment
# (used for the actual package/duration stats lookup) -- same person,
# two different upstream sources' naming conventions.
_TESTING_DRIVER_NAMES = {"Collin Jonathan LaTour", "Collin LaTour"}


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def build_progress_stats(driver_name: str, target_date: date, db: Session) -> Optional[dict]:
    """Read-only. Returns None if there's no route assignment for this
    driver today at all (nothing to report on yet)."""
    assignment = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == target_date, DailyRouteAssignment.driver_name == driver_name)
        .first()
    )
    if not assignment:
        return None

    snapshot = get_latest_snapshot(db, target_date)
    remaining = None
    if snapshot:
        remaining = (
            db.query(PackagesRecord)
            .filter(PackagesRecord.snapshot_id == snapshot.id, PackagesRecord.transporter_name == driver_name)
            .count()
        )

    total = assignment.packages
    delivered = max(0, total - remaining) if (total and remaining is not None) else None
    pct = round(100 * delivered / total, 1) if (delivered is not None and total) else None

    dm = (
        db.query(DriverShiftDM)
        .filter(DriverShiftDM.shift_date == target_date, DriverShiftDM.driver_name == driver_name)
        .first()
    )
    arrived_at = dm.arrived_at if dm else None
    elapsed_minutes = (datetime.utcnow() - arrived_at).total_seconds() / 60 if arrived_at else None
    planned_duration = assignment.route_duration
    time_remaining_minutes = (
        (planned_duration - elapsed_minutes) if (planned_duration and elapsed_minutes is not None) else None
    )

    # pace_ratio: (fraction of packages done) / (fraction of planned time
    # elapsed) -- >1 means completing faster than the plan implies (ahead
    # of pace), <1 means behind. More meaningful than raw time_remaining
    # alone, which doesn't account for how much work is actually done.
    pace_ratio = None
    if pct is not None and planned_duration and elapsed_minutes is not None and elapsed_minutes > 0:
        time_fraction = elapsed_minutes / planned_duration
        if time_fraction > 0:
            pace_ratio = round((pct / 100) / time_fraction, 2)

    # Tenure phase (ORE/NL1/NL2/NL3/pre_tenured/tenured) -- per explicit
    # direction to boost encouragement for still-learning drivers. None
    # if this driver has no transporter_id on file or never appeared in
    # a Tenured Workforce report yet.
    tenure_phase = None
    if assignment.transporter_id:
        from api.src.database import get_latest_tenure_record
        from api.src.routes.driver_scoring import get_tenure_phase
        tenure_rec = get_latest_tenure_record(db, assignment.transporter_id)
        if tenure_rec:
            tenure_phase = get_tenure_phase(tenure_rec.lifetime_routes)

    return {
        "driver_name": driver_name,
        "date": target_date.isoformat(),
        "total_packages": total,
        "remaining_packages": remaining,
        "delivered_so_far": delivered,
        "pct_complete": pct,
        "elapsed_minutes": round(elapsed_minutes) if elapsed_minutes is not None else None,
        "planned_duration_minutes": planned_duration,
        "time_remaining_minutes": round(time_remaining_minutes) if time_remaining_minutes is not None else None,
        "pace_ratio": pace_ratio,
        "tenure_phase": tenure_phase,
        "van_number": assignment.van_number,
        "wave": assignment.wave,
    }


_OPENERS_STRONG = [
    "🔥 You're crushing it out there today!",
    "🌟 Look at you go — this is a great run!",
    "🚀 Absolutely flying through the route today!",
]
_OPENERS_MID = [
    "👍 Solid progress today — nice steady pace!",
    "💪 Making good moves out there, keep it rolling!",
    "🙂 Right on track — good work so far!",
]
_OPENERS_EARLY_OR_BEHIND = [
    "☀️ Just checking in — every stop counts, you've got plenty of route left!",
    "🚐 Still plenty of daylight — no stress, just keep knocking out stops!",
    "🎯 Early days on this one still — steady and sure wins it!",
]

_CLOSERS = [
    "You've got this! 💪",
    "Keep it up — we're rooting for you! 🙌",
    "Drive safe and keep truckin'! 🚚",
]


def _fmt_minutes(m: Optional[int]) -> str:
    if m is None:
        return "?"
    h, mm = divmod(abs(m), 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


_VERY_FAR_AHEAD_PACE_RATIO = 1.5   # completing ~50%+ faster than the plan implies
_FAR_BEHIND_PACE_RATIO = 0.9       # more than 10% behind (% complete vs. % of planned time used) -- explicit threshold, "very problematic"
_SAFETY_QUALITY_NUDGE_THRESHOLD = 90.0   # only nudge if there's real room to improve

_HELP_CHECK_IN_LINES = [
    "😟 Hey, looks like today's a tough one — you're more than 10% behind where the plan expects. Everything okay out there? Just reply here if you need a hand, or flag your wave lead — no judgment, we just want to help.",
    "🤝 Noticed you're running well behind pace today. If something's slowing you down (van issue, tough stops, anything), reply right here and we'll get you support — that's what we're for.",
]


def _safety_quality_nudge_line(driver_name: str, db: Session) -> str:
    """Only for the very-far-ahead tier: a good pace is only actually good
    if safety/quality come with it, per explicit direction ("A good pace
    at high safety/quality is a good thing") -- frames it as a positive
    combination, not a scolding, and only appears at all if the driver's
    own current safety/quality scores show real room to improve."""
    try:
        from api.src.routes.driver_scoring import compute_driver_scores
        scores = compute_driver_scores(db)
        match = next((s for s in scores if s["driver_name"].strip().lower() == driver_name.strip().lower()), None)
    except Exception:
        match = None

    if not match:
        return ""
    safety, quality = match.get("safety"), match.get("quality")
    low_scores = [n for n, v in (("safety", safety), ("quality", quality)) if v is not None and v < _SAFETY_QUALITY_NUDGE_THRESHOLD]
    if not low_scores:
        return "\n\n🌟 And your safety/quality scores back it up — that's the winning combo, fast AND careful!"
    topic = " and ".join(low_scores)
    return f"\n\n💡 One thing worth a look: your {topic} score has some room to grow. A great pace paired with strong safety and quality is the real win — don't let the speed come at the cost of either."


_NURSERY_ENCOURAGEMENT = {
    "ORE": "🌱 You're right at the very start of your NDAY journey (Orientation Route) — every stop today is real experience. We're proud of you for jumping in!",
    "NL1": "🌱 Nursery Level 1 — still early days, and you're doing awesome. Every route from here makes you sharper.",
    "NL2": "🌿 Nursery Level 2 already — look at that progress! Keep building on what you've learned.",
    "NL3": "🌳 Nursery Level 3 — you're almost through the nursery phase entirely. So much growth already!",
    "pre_tenured": "🚀 You're on the home stretch toward Tenured status — keep this momentum going, you're almost there!",
}


def build_progress_message_text(stats: dict, db: Optional[Session] = None) -> str:
    pct = stats["pct_complete"]
    pace = stats.get("pace_ratio")
    very_far_ahead = pace is not None and pace >= _VERY_FAR_AHEAD_PACE_RATIO
    phase = stats.get("tenure_phase")
    from api.src.routes.driver_scoring import is_non_tenured_phase
    is_nursery = is_non_tenured_phase(phase)

    far_behind = pace is not None and pace < _FAR_BEHIND_PACE_RATIO
    if very_far_ahead:
        opener = "🏆 Whoa — you're way ahead of pace today, awesome work!"
    elif far_behind:
        # Pace, not raw %-complete, drives the opener here -- a driver
        # can be at a "decent" completion % and still be genuinely far
        # behind where the plan expects them, and the tone needs to
        # match the concern in the check-in line below, not clash with it.
        opener = random.choice(_OPENERS_EARLY_OR_BEHIND)
    elif pct is not None and pct >= 85:
        opener = random.choice(_OPENERS_STRONG)
    elif pct is not None and pct >= 50:
        opener = random.choice(_OPENERS_MID)
    else:
        opener = random.choice(_OPENERS_EARLY_OR_BEHIND)

    lines = [opener, ""]

    # Heavy on the encouragement for still-learning drivers, per explicit
    # direction ("hit heavy on the uplifting comments in the NL and
    # non-tenured phase") -- placed right up front, not buried at the end.
    if is_nursery and phase in _NURSERY_ENCOURAGEMENT:
        lines.append(_NURSERY_ENCOURAGEMENT[phase])
        lines.append("")
    if stats["total_packages"] and stats["delivered_so_far"] is not None:
        pct_str = f" ({pct}%)" if pct is not None else ""
        lines.append(f"📦 {stats['delivered_so_far']}/{stats['total_packages']} delivered{pct_str}")
    if stats["remaining_packages"] is not None:
        lines.append(f"🎯 {stats['remaining_packages']} stop(s) to go")

    trm = stats["time_remaining_minutes"]
    if trm is not None:
        if trm >= 0:
            lines.append(f"⏱️ About {_fmt_minutes(trm)} left on today's planned route time")
        elif not far_behind:
            lines.append(f"⏱️ You're {_fmt_minutes(-trm)} past our usual estimate — no worries, every route's different!")

    nudge = ""
    if very_far_ahead and db is not None:
        nudge = _safety_quality_nudge_line(stats["driver_name"], db)

    if far_behind:
        # More than 10% behind (pace_ratio < 0.9) -- explicit direction:
        # "very problematic," should prompt a genuine "do you need help?"
        # check-in rather than the usual breezy "no worries" framing.
        lines.append("")
        lines.append(random.choice(_HELP_CHECK_IN_LINES))

    lines.append("")
    lines.append(random.choice(_CLOSERS) + nudge)
    return "\n".join(lines)


def send_progress_dm(driver_name: str, db: Session, target_date: Optional[date] = None) -> dict:
    if driver_name not in _TESTING_DRIVER_NAMES:
        return {"status": "not_in_testing_allowlist", "driver_name": driver_name}

    target_date = target_date or datetime.now(PT).date()
    stats = build_progress_stats(driver_name, target_date, db)
    if not stats:
        return {"status": "no_assignment", "driver_name": driver_name, "date": target_date.isoformat()}

    from api.src.driver_identity import resolve_roster_entry
    entry = resolve_roster_entry(driver_name, db)
    slack_id = entry.slack_member_id if entry else None
    if not slack_id:
        return {"status": "no_slack_id", "driver_name": driver_name}

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    text = build_progress_message_text(stats, db)
    try:
        client.chat_postMessage(channel=slack_id, text=text)
    except Exception as exc:
        logger.warning("Driver progress DM failed for %s: %s", driver_name, exc)
        return {"status": "send_failed", "error": str(exc)}

    return {"status": "sent", "driver_name": driver_name, "text": text, "stats": stats}


@router.get("/preview")
def preview_progress(driver_name: str = "Collin Jonathan LaTour", target_date: Optional[str] = None, db: Session = Depends(get_db)):
    """See the message text without sending it."""
    d = date.fromisoformat(target_date) if target_date else datetime.now(PT).date()
    stats = build_progress_stats(driver_name, d, db)
    if not stats:
        return {"status": "no_assignment", "driver_name": driver_name, "date": d.isoformat()}
    return {"stats": stats, "text": build_progress_message_text(stats, db)}


@router.post("/send-test")
def send_test(driver_name: str = "Collin Jonathan LaTour", db: Session = Depends(get_db)):
    """Manual on-demand trigger for testing rounds. Restricted to
    _TESTING_DRIVER_NAMES regardless of what's passed here."""
    return send_progress_dm(driver_name, db)


MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt


def send_sample_to_mgt(
    driver_name: str, db: Session, target_date: Optional[date] = None,
    synthetic_elapsed_minutes: Optional[float] = None,
) -> dict:
    """Posts what a given real driver's progress DM would look like to
    #nday-mgt (labeled with their name/pace), instead of actually DMing
    them -- for reviewing tone/format across different real pace
    scenarios (behind/on-time/ahead/very-far-ahead) before this goes out
    to anyone directly. NOT gated by _TESTING_DRIVER_NAMES -- this never
    reaches the driver's own DM, so the testing-allowlist restriction
    (which exists specifically to limit who gets directly messaged)
    doesn't apply here.

    synthetic_elapsed_minutes (added 2026-08-05): most drivers have no
    real DriverShiftDM.arrived_at recorded (a known, separate capture
    gap), so pace_ratio is usually None for real data. Rather than
    writing a fake arrived_at into the real DriverShiftDM row -- leaving
    fabricated data sitting in production -- this overrides the elapsed-
    time figure in-memory only, for this one sample message. Real
    package counts, planned duration, and safety/quality scores are
    still 100% real; only the clock is synthetic, and it's labeled as
    such in the header."""
    target_date = target_date or datetime.now(PT).date()
    stats = build_progress_stats(driver_name, target_date, db)
    if not stats:
        return {"status": "no_assignment", "driver_name": driver_name, "date": target_date.isoformat()}

    is_synthetic = synthetic_elapsed_minutes is not None and stats.get("elapsed_minutes") is None
    if is_synthetic:
        stats["elapsed_minutes"] = round(synthetic_elapsed_minutes)
        planned = stats.get("planned_duration_minutes")
        if planned:
            stats["time_remaining_minutes"] = round(planned - synthetic_elapsed_minutes)
            if stats.get("pct_complete") is not None and synthetic_elapsed_minutes > 0:
                time_fraction = synthetic_elapsed_minutes / planned
                if time_fraction > 0:
                    stats["pace_ratio"] = round((stats["pct_complete"] / 100) / time_fraction, 2)

    client = _client()
    if not client:
        return {"status": "no_slack_token"}

    text = build_progress_message_text(stats, db)
    pace = stats.get("pace_ratio")
    pace_label = (
        "very far ahead" if pace is not None and pace >= _VERY_FAR_AHEAD_PACE_RATIO else
        "ahead" if pace is not None and pace >= 1.1 else
        "far behind (needs help check-in)" if pace is not None and pace < _FAR_BEHIND_PACE_RATIO else
        "behind" if pace is not None and pace < 0.95 else
        "on time" if pace is not None else "unknown pace"
    )
    synthetic_note = " _(synthetic elapsed-time, for demo purposes -- real package/duration/safety/quality data)_" if is_synthetic else ""
    header = f"*Sample progress DM* — {driver_name} ({pace_label}, pace ratio {pace if pace is not None else 'n/a'}){synthetic_note}\n\n"
    try:
        client.chat_postMessage(channel=MGT_CHANNEL, text=header + text)
    except Exception as exc:
        logger.warning("Progress DM sample post to #nday-mgt failed for %s: %s", driver_name, exc)
        return {"status": "send_failed", "error": str(exc)}

    return {"status": "sent", "driver_name": driver_name, "pace_label": pace_label, "text": text, "stats": stats}


@router.post("/send-sample-to-mgt")
def send_sample_to_mgt_endpoint(driver_name: str, synthetic_elapsed_minutes: Optional[float] = None, db: Session = Depends(get_db)):
    return send_sample_to_mgt(driver_name, db, synthetic_elapsed_minutes=synthetic_elapsed_minutes)
