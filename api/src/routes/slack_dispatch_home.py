"""
Slack Dispatch Home — a second App Home tab, scoped to ops/dispatch staff
(gated by document_routing.is_dispatch_staff), living alongside the
driver-facing Home tab in slack_home.py. Slack only allows one Events
Subscriptions Request URL per app, so slack_home.py's app_home_opened
handler branches by role rather than this module owning its own endpoint —
see slack_home.py's _publish_home().

v1 ships exactly one tool: removing terminated-but-still-Slack-linked
employees from every channel the bot can reach. There's no existing
termination workflow in this app (drivers.py never deactivates anyone) —
this button is effectively the first real termination-adjacent action, not
a hook into something bigger. Full workspace deactivation isn't available
on this (non-Enterprise Grid) Slack plan, so this only removes channel
membership, not the account itself.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from api.src.database import DriverRosterEntry, User, SlackIngestLog, OpsIngestJob
from api.src.routes.document_routing import is_dispatch_staff
from api.src.routes.slack_home import _client, _dm_driver
from api.src.routes.slack_interactions import FRONTEND_URL
from api.src.routes import auth as auth_routes

PACIFIC = ZoneInfo("America/Los_Angeles")

logger = logging.getLogger(__name__)

DISPATCH_HOME_CHANNEL_ID = os.getenv("DISPATCH_HOME_CHANNEL_ID", "C0BHGL7DLLC")

REMOVE_TERMINATED_CALLBACK_ID = "dispatch_remove_terminated_submit"
MAX_CANDIDATES = 10  # Block Kit checkboxes element practical/UI limit per block

INVITE_USER_CALLBACK_ID = "dispatch_invite_user_submit"
RESET_PASSWORD_CALLBACK_ID = "dispatch_reset_password_submit"
ROLE_OPTIONS = ["admin", "manager", "dispatcher", "driver"]

INGEST_ALERTS_PIN_CALLBACK_ID = "dispatch_ingest_alerts_pin_submit"
INGEST_ALERTS_PIN = "2468"

# ─────────────────────────────────────────────────────────────────────────────
# Time-of-day phases (added 2026-07-21) — organizes buttons by when they're
# normally used, but deliberately NEVER hides one outside its window: every
# button stays clickable all day. The point is orientation ("what phase are
# we in"), not access control — dispatch needed these same tools well
# outside their nominal windows constantly during this week's fixes, and
# hiding them then would have actively gotten in the way.
# ─────────────────────────────────────────────────────────────────────────────

PHASES = [
    {"key": "morning_ingest", "label": "📥 Morning Ingest & Route Prep", "window": "8–10 AM", "start": (8, 0), "end": (10, 0)},
    {"key": "okami", "label": "📊 Okami / FRT", "window": "3:30–5 PM (ECP)", "start": (15, 30), "end": (17, 0)},
]


def _current_phase_key(now: datetime) -> Optional[str]:
    t = (now.hour, now.minute)
    for phase in PHASES:
        if phase["start"] <= t < phase["end"]:
            return phase["key"]
    return None


def _phase_header_text(phase: dict, current_key: Optional[str]) -> str:
    marker = "  ▶ *now*" if phase["key"] == current_key else ""
    return f"{phase['label']} _({phase['window']})_{marker}"


def _ingest_status_today(db: Session) -> dict:
    """{'dop': bool, 'cortex': bool, 'route_sheet': bool, 'fleet': bool} —
    True means successfully ingested for today's Pacific date. DOP/Cortex/
    Route Sheet are tracked in SlackIngestLog; Fleet goes through a
    completely separate pipeline/table (ops_ingest.py's OpsIngestJob), not
    SlackIngestLog — mixing them up would silently always show Fleet as
    undetected."""
    today = datetime.now(PACIFIC).date()
    status = {"dop": False, "cortex": False, "route_sheet": False, "fleet": False}

    logs = (
        db.query(SlackIngestLog)
        .filter(SlackIngestLog.ingest_date == today, SlackIngestLog.status == "success")
        .all()
    )
    for log in logs:
        if log.file_type in status:
            status[log.file_type] = True

    fleet_jobs = (
        db.query(OpsIngestJob)
        .filter(OpsIngestJob.detected_type == "fleet", OpsIngestJob.status == "complete")
        .all()
    )
    status["fleet"] = any(j.detected_at and j.detected_at.date() == today for j in fleet_jobs)

    return status


def _ingest_status_block(db: Session) -> dict:
    status = _ingest_status_today(db)
    labels = {"dop": "DOP", "cortex": "Cortex", "route_sheet": "Route Sheet", "fleet": "Fleet"}
    line = "   ".join(
        f"{'✅' if status[key] else '⏳'} {label}"
        for key, label in labels.items()
    )
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": line}]}


# ─────────────────────────────────────────────────────────────────────────────
# Home tab builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dispatch_home_view_blocks(db: Session) -> list:
    """Pure builder — no Slack API calls in here, so it's unit-testable."""
    now = datetime.now(PACIFIC)
    current_phase = _current_phase_key(now)

    morning_phase = PHASES[0]
    okami_phase = PHASES[1]

    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🛠️ Dispatch Home", "emoji": True}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Tools for ops/dispatch staff. More lands here over time."},
        },
        {"type": "divider"},

        # ── Morning Ingest & Route Prep — status only, no buttons here.
        # Time-phase headers are for orientation ("what phase are we in"),
        # never for hiding a button outside its window — see module note.
        {"type": "section", "text": {"type": "mrkdwn", "text": _phase_header_text(morning_phase, current_phase)}},
        _ingest_status_block(db),
        {"type": "divider"},

        # ── Re-Submit / Re-Send — corrective re-triggers, always visible
        # regardless of time of day (added 2026-07-21, separated out from
        # the Morning Ingest phase on request: these get used all day when
        # something needs correcting, not just during the morning window).
        {"type": "section", "text": {"type": "mrkdwn", "text": "🔁 *Re-Submit / Re-Send*"}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_rerun_route_assignments",
                    "text": {"type": "plain_text", "text": "🔄 Re-Run Route Assignments", "emoji": True},
                    "style": "primary",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Re-run today's route assignments?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": "Re-scans for corrected files, rebuilds today's assignments, and notifies "
                                     "affected drivers: new assignments get the normal DM, changed ones get an "
                                     "update DM, and drivers dropped from a route get a removal DM. Also refreshes "
                                     "the #nday-mgt matrix.",
                        },
                        "confirm": {"type": "plain_text", "text": "Re-run"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "action_id": "dispatch_republish_showtime",
                    "text": {"type": "plain_text", "text": "🔁 Re-Publish Showtime Matrix", "emoji": True},
                },
                {
                    "type": "button",
                    "action_id": "dispatch_send_route_matrix",
                    "text": {"type": "plain_text", "text": "📤 Send Route Matrix", "emoji": True},
                },
            ],
        },
        {"type": "divider"},

        # ── Okami / FRT ──────────────────────────────────────────────────
        {"type": "section", "text": {"type": "mrkdwn", "text": _phase_header_text(okami_phase, current_phase)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_open_okami",
                    "text": {"type": "plain_text", "text": "📊 Enter OKAMI", "emoji": True},
                    "style": "primary",
                    "url": f"{FRONTEND_URL}/okami-capacity",
                },
            ],
        },
        {"type": "divider"},

        # ── Admin — always available, not tied to any phase ─────────────
        {"type": "section", "text": {"type": "mrkdwn", "text": "🛠️ *Admin (always available)*"}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_open_rescue",
                    "text": {"type": "plain_text", "text": "🚨 Generate Rescue", "emoji": True},
                    "style": "primary",
                    "url": f"{FRONTEND_URL}/rescue/open",
                },
                {
                    "type": "button",
                    "action_id": "dispatch_open_crash_report",
                    "text": {"type": "plain_text", "text": "🚗 Generate Crash Report", "emoji": True},
                    "style": "danger",
                    "url": f"{FRONTEND_URL}/crash-report",
                },
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_remove_terminated_button",
                    "text": {"type": "plain_text", "text": "🗑️ Remove Terminated Employees", "emoji": True},
                    "style": "danger",
                },
                {
                    "type": "button",
                    "action_id": "dispatch_preview_driver_home",
                    "text": {"type": "plain_text", "text": "👁️ Preview Driver Home", "emoji": True},
                },
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_invite_user_button",
                    "text": {"type": "plain_text", "text": "➕ Invite User", "emoji": True},
                    "style": "primary",
                },
                {
                    "type": "button",
                    "action_id": "dispatch_reset_password_button",
                    "text": {"type": "plain_text", "text": "🔑 Reset Password", "emoji": True},
                },
            ],
        },
        {
            "type": "actions",
            "elements": [_ingest_alerts_button(db)],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "New Day Logistics · Dispatch tools"}],
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Preview Driver Home — lets a dispatch-staff account see the driver Home
# tab without needing a second Slack login. Stateless: re-renders on demand
# via these two buttons rather than persisting a "view mode" per user, so it
# naturally resets to the normal role-based view (see slack_home.py's
# _publish_home) the next time the tab is reopened fresh. Safe to show the
# *real* interactive driver view here (not a placeholder) even while
# DRIVER_DM_ACTIVE is off — every button underneath still checks that flag
# independently in its own handler, so nothing can actually fire from a
# preview.
# ─────────────────────────────────────────────────────────────────────────────

def _sample_driver_for_preview(db: Session) -> Optional[DriverRosterEntry]:
    """Best-effort representative driver for the preview — a dispatch
    account usually isn't itself a linked driver, so there's nothing
    driver-specific to show for the real viewer. Not meant to be a
    particular/special driver, just a realistic-looking example."""
    return (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.is_active == True, DriverRosterEntry.slack_member_id.isnot(None))  # noqa: E712
        .order_by(DriverRosterEntry.payroll_name)
        .first()
    )


def _handle_dispatch_preview_driver_home(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        return
    client = _client()
    if not client:
        return

    from api.src.routes.slack_home import build_home_view_blocks
    sample = _sample_driver_for_preview(db)
    driver_blocks = build_home_view_blocks(sample, db)

    banner = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":eye: *Previewing the driver Home tab"
                    f"{f' as {sample.payroll_name}' if sample else ''}.* This is what drivers see.",
        },
    }
    back_button = {
        "type": "actions",
        "elements": [{
            "type": "button",
            "action_id": "dispatch_back_from_preview",
            "text": {"type": "plain_text", "text": "← Back to Dispatch Home", "emoji": True},
        }],
    }
    full_blocks = [banner, {"type": "divider"}] + driver_blocks + [back_button]
    try:
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": full_blocks})
    except Exception as exc:
        logger.warning("Preview driver home publish failed for %s: %s", user_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Re-Run Route Assignments — rerun_route_assignments() (daily_notify.py) can
# take a while (channel re-scans, ingest, possibly several DM sends), well
# past Slack's 3-second interaction ack window. So the button handler only
# acks fast (an ephemeral "running" message) and hands the real work to a
# FastAPI BackgroundTask, which opens its own DB session — the request-scoped
# one is on its way to being torn down by the time a background task runs.
# ─────────────────────────────────────────────────────────────────────────────

def _run_rerun_and_report(user_id: str) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from api.src.database import SessionLocal
    from api.src.routes.daily_notify import rerun_route_assignments

    client = _client()
    db = SessionLocal()
    try:
        today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
        result = rerun_route_assignments(today, db)
        changed = result["changed_dms"]
        removed = result["removed_dms"]
        new_dms = (result.get("initial_check") or {}).get("dms") or {}
        lines = [
            f":white_check_mark: *Re-Run Route Assignments complete* for {today.isoformat()}",
            f"New DMs: {new_dms.get('sent', 0)} sent, {new_dms.get('skipped', 0)} skipped",
            f"Changed DMs: {changed['sent']} sent, {changed['skipped']} skipped",
            f"Removed DMs: {removed['sent']} sent, {removed['skipped']} skipped",
        ]
        if not result.get("dm_active"):
            lines.append("_DRIVER_DM_ACTIVE is off — assignments were rebuilt and the summary refreshed, but no driver DMs were actually sent._")

        from api.src.routes.daily_notify import get_missing_docs_today
        from api.src.routes.ops_ingest import is_type_ingested_today
        missing_docs = get_missing_docs_today(today, db)
        missing_labels = [
            label for label, dockey in (("DOP", "dop"), ("Route Sheet", "route_sheets"), ("Cortex", "cortex"))
            if missing_docs[dockey]
        ]
        if not is_type_ingested_today(db, "fleet", today):
            missing_labels.append("Fleet")
        if missing_labels:
            lines.append(f":warning: Missing today: {', '.join(missing_labels)} — counts above may be incomplete until these are posted.")

        summary = "\n".join(lines)
    except Exception as exc:
        logger.exception("Re-run route assignments background task failed")
        summary = f":x: Re-Run Route Assignments failed: {exc}"
    finally:
        db.close()

    if client:
        try:
            _dm_driver(client, user_id, summary)
        except Exception as exc:
            logger.warning("Rerun summary DM failed for %s: %s", user_id, exc)
        try:
            client.chat_postMessage(channel=DISPATCH_HOME_CHANNEL_ID, text=summary)
        except Exception as exc:
            logger.warning("Rerun summary audit-log post failed: %s", exc)


def _handle_dispatch_rerun_route_assignments(payload: dict, db: Session, background_tasks: BackgroundTasks) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_rerun_route_assignments", user_id)
        return
    client = _client()
    if client:
        try:
            client.chat_postMessage(
                channel=user_id,
                text=":arrows_counterclockwise: Re-running today's route assignments — results will post here shortly.",
            )
        except Exception as exc:
            logger.warning("Rerun ack DM failed for %s: %s", user_id, exc)
    background_tasks.add_task(_run_rerun_and_report, user_id)


def _handle_dispatch_republish_showtime(payload: dict, db: Session) -> None:
    """Re-Publish Showtime Matrix — unlike Re-Run Route Assignments, this
    is just a DB query plus two Slack posts (post_showtime_summary()
    updates the existing #nday-mgt/#nday-team-room messages in place, no
    channel re-scan or file downloads involved), so it comfortably finishes
    inside Slack's 3-second interaction window and doesn't need a
    BackgroundTask."""
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_republish_showtime", user_id)
        return

    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from sqlalchemy import func, or_
    from api.src.database import DriverScheduleEntry
    from api.src.routes.rostering import post_showtime_summary

    today = _dt.now(ZoneInfo("America/Los_Angeles")).date()
    # Showtime is a night-before roster for the NEXT shift, never the
    # calendar day the button happens to be clicked on -- so the date
    # filter must be strictly > today, not >= today. Today's row still
    # carries a real show_time (replays no longer wipe it), so >= today
    # would let MIN() land back on today and reproduce the exact "shows
    # today instead of tomorrow" bug this whole fix started from. Also
    # picks the EARLIEST qualifying future date, not MAX -- a multi-week
    # schedule upload can create bare name-only rows for several future
    # dates before DOP enrichment adds real wave_time/show_time to the
    # nearest one (same reasoning as _tomorrow_schedule_landed() above).
    target_date = (
        db.query(func.min(DriverScheduleEntry.schedule_date))
        .filter(
            DriverScheduleEntry.schedule_date > today,
            or_(DriverScheduleEntry.wave_time.isnot(None), DriverScheduleEntry.show_time.isnot(None)),
        )
        .scalar()
    )

    client = _client()
    if not client:
        return

    # No future date has real showtime data yet -- do NOT guess "tomorrow"
    # and repost stale/wrong data under that label (e.g. a holiday gap
    # could mean the real next shift is several days out). Tell the
    # clicker plainly instead of picking any date on their behalf.
    if target_date is None:
        try:
            _dm_driver(client, user_id, ":warning: No upcoming schedule with showtimes has been ingested yet — nothing to publish. Ingest the next driver schedule file first, then try again.")
        except Exception as exc:
            logger.warning("Republish showtime summary DM failed for %s: %s", user_id, exc)
        return

    try:
        result = post_showtime_summary(target_date, db, force=True)
    except Exception as exc:
        logger.exception("Re-publish showtime matrix failed")
        result = {"status": "error", "detail": str(exc)}

    if result.get("status") == "posted":
        summary = f":white_check_mark: Showtime matrix republished for {target_date.isoformat()}."
    elif result.get("status") == "no_schedule":
        summary = f":warning: No driver schedule data found for {target_date.isoformat()} — nothing to publish."
    elif result.get("status") == "inactive":
        summary = ":warning: ROSTERING_ACTIVE is off — nothing was published."
    else:
        summary = f":x: Re-publish failed: {result}"
    try:
        _dm_driver(client, user_id, summary)
    except Exception as exc:
        logger.warning("Republish showtime summary DM failed for %s: %s", user_id, exc)


DISPATCHER_MGT_CHANNEL = "C0BCYAW7QP3"  # #nday-mgt
# Was defaulting to "C0BAQAYKANS" -- the literal channel ID for
# #nday-team-room (driver-facing) -- so "Send Route Matrix" silently sent
# the dispatcher-only assignment matrix (van numbers, Perf column) straight
# into the driver channel every time it was clicked, unrelated to and
# independent of the TEAM_ROOM_MESSAGES_ACTIVE gate (confirmed live
# 2026-07-19, message force-deleted from #nday-team-room). Default now
# points at #nday-mgt; set ROUTE_MATRIX_TARGET_CHANNEL explicitly if a
# different destination is actually wanted.
ROUTE_MATRIX_TARGET_CHANNEL = os.getenv("ROUTE_MATRIX_TARGET_CHANNEL", DISPATCHER_MGT_CHANNEL)


def _handle_dispatch_send_route_matrix(payload: dict, db: Session) -> None:
    """Send Route Matrix — manual, on-demand send of today's assignment
    matrix to ROUTE_MATRIX_TARGET_CHANNEL, separate from the automatic
    #nday-mgt post (post_assignment_matrix()). No idempotency guard —
    every click sends a fresh copy, same as Re-Publish Showtime Matrix."""
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_send_route_matrix", user_id)
        return

    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from api.src.routes.rostering import send_assignment_matrix_to_channel

    today = _dt.now(ZoneInfo("America/Los_Angeles")).date()
    try:
        result = send_assignment_matrix_to_channel(today, db, ROUTE_MATRIX_TARGET_CHANNEL)
    except Exception as exc:
        logger.exception("Send route matrix failed")
        result = {"status": "error", "detail": str(exc)}

    client = _client()
    if not client:
        return
    if result.get("status") == "posted":
        summary = f":white_check_mark: Route matrix sent to <#{ROUTE_MATRIX_TARGET_CHANNEL}> for {today.isoformat()} ({result.get('drivers')} drivers)."
    elif result.get("status") == "no_assignments":
        summary = f":warning: No route assignments found for {today.isoformat()} — nothing sent."
    elif result.get("status") == "no_slack_token":
        summary = ":warning: No Slack bot token configured — nothing sent."
    else:
        summary = f":x: Send route matrix failed: {result}"
    try:
        _dm_driver(client, user_id, summary)
    except Exception as exc:
        logger.warning("Send-route-matrix summary DM failed for %s: %s", user_id, exc)


def _handle_dispatch_back_from_preview(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    client = _client()
    if not client:
        return
    try:
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": build_dispatch_home_view_blocks(db)})
    except Exception as exc:
        logger.warning("Back-to-dispatch publish failed for %s: %s", user_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Remove Terminated Employees
# ─────────────────────────────────────────────────────────────────────────────

def _terminated_linked_candidates(db: Session) -> list[DriverRosterEntry]:
    """Terminated (is_active=False) drivers who still have a Slack ID on
    file — i.e. plausibly still sitting in channels. Most-recently-flagged
    first."""
    return (
        db.query(DriverRosterEntry)
        .filter(
            DriverRosterEntry.is_active == False,  # noqa: E712
            DriverRosterEntry.slack_member_id.isnot(None),
            DriverRosterEntry.slack_member_id != "",
        )
        .order_by(DriverRosterEntry.flagged_inactive_at.desc().nullslast())
        .limit(MAX_CANDIDATES)
        .all()
    )


def _remove_terminated_modal(db: Session) -> dict:
    candidates = _terminated_linked_candidates(db)
    if not candidates:
        return {
            "type": "modal",
            "title": {"type": "plain_text", "text": "Remove Terminated"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "No terminated employees with a linked Slack account found. Nothing to do."},
                }
            ],
        }

    options = [
        {
            "text": {"type": "plain_text", "text": c.payroll_name[:75]},
            "value": c.slack_member_id,
        }
        for c in candidates
    ]

    return {
        "type": "modal",
        "callback_id": REMOVE_TERMINATED_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Remove Terminated"},
        "submit": {"type": "plain_text", "text": "Remove"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Select who to remove from every Slack channel the bot can reach. Their Slack account itself is *not* deactivated — only channel membership."},
            },
            {
                "type": "input",
                "block_id": "candidates_block",
                "label": {"type": "plain_text", "text": "Terminated employees"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "candidates",
                    "options": options,
                },
            },
        ],
    }


def _handle_dispatch_remove_terminated_button(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_remove_terminated_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_remove_terminated_modal(db))
    except Exception as exc:
        logger.warning("views_open failed for remove-terminated modal: %s", exc)


def _bot_member_channel_ids(client) -> list[str]:
    """All public/private channel IDs the bot is currently a member of."""
    channel_ids: list[str] = []
    cursor: Optional[str] = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=200,
            cursor=cursor,
        )
        for ch in resp.get("channels", []):
            if ch.get("is_member"):
                channel_ids.append(ch["id"])
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return channel_ids


def _remove_from_all_channels(client, slack_user_id: str, channel_ids: list[str]) -> tuple[int, int]:
    """Returns (removed_count, skipped_count)."""
    removed = 0
    skipped = 0
    for channel_id in channel_ids:
        try:
            client.conversations_kick(channel=channel_id, user=slack_user_id)
            removed += 1
        except Exception as exc:
            # not_in_channel (person wasn't there), cant_kick_self, missing_scope, etc. —
            # log and keep going, one failure shouldn't abort the whole batch.
            logger.info("Kick skipped for %s in %s: %s", slack_user_id, channel_id, exc)
            skipped += 1
    return removed, skipped


def _handle_dispatch_remove_terminated_submit(payload: dict, db: Session) -> dict:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_remove_terminated_submit", user_id)
        return {"response_action": "clear"}

    view = payload.get("view", {})
    values = view.get("state", {}).get("values", {})
    selected = values.get("candidates_block", {}).get("candidates", {}).get("selected_options", []) or []
    targets = [(opt["text"]["text"], opt["value"]) for opt in selected]

    client = _client()
    if not client or not targets:
        return {"response_action": "clear"}

    try:
        channel_ids = _bot_member_channel_ids(client)
    except Exception as exc:
        logger.warning("conversations_list failed during remove-terminated: %s", exc)
        channel_ids = []

    lines = []
    for name, slack_id in targets:
        removed, skipped = _remove_from_all_channels(client, slack_id, channel_ids)
        lines.append(f"• *{name}* — removed from {removed} channel(s), skipped {skipped}")

    summary = "🗑️ *Remove Terminated Employees* — results:\n" + "\n".join(lines)

    try:
        _dm_driver(client, user_id, summary)
    except Exception as exc:
        logger.warning("Remove-terminated summary DM failed: %s", exc)

    try:
        client.chat_postMessage(channel=DISPATCH_HOME_CHANNEL_ID, text=summary)
    except Exception as exc:
        logger.warning("Remove-terminated audit log post failed: %s", exc)

    return {"response_action": "clear"}


# ─────────────────────────────────────────────────────────────────────────────
# Invite User — creates a pending account (auth.py's User table) and DMs the
# invitee a link to set their own password. Root cause this fixes: this app
# used to authenticate against a local api/users.json file that's
# .gitignore'd and doesn't ship with a Render deploy, so any account only
# living there (or created via the old /create-user against a running
# instance's ephemeral disk) silently stopped working on the next redeploy.
# auth.py now backs everything with the users table instead.
# ─────────────────────────────────────────────────────────────────────────────

def _invite_user_modal() -> dict:
    return {
        "type": "modal",
        "callback_id": INVITE_USER_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Invite User"},
        "submit": {"type": "plain_text", "text": "Send Invite"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Creates a pending account and DMs the person a link to set their own "
                            "password. The account stays inactive until they complete it.",
                },
            },
            {
                "type": "input",
                "block_id": "slack_user_block",
                "label": {"type": "plain_text", "text": "Slack user to invite"},
                "element": {"type": "users_select", "action_id": "slack_user"},
            },
            {
                "type": "input",
                "block_id": "username_block",
                "label": {"type": "plain_text", "text": "Username (for logging in)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "username",
                    "placeholder": {"type": "plain_text", "text": "e.g. jsmith"},
                },
            },
            {
                "type": "input",
                "block_id": "name_block",
                "label": {"type": "plain_text", "text": "Display name"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name",
                    "placeholder": {"type": "plain_text", "text": "e.g. John Smith"},
                },
            },
            {
                "type": "input",
                "block_id": "role_block",
                "label": {"type": "plain_text", "text": "Role"},
                "element": {
                    "type": "static_select",
                    "action_id": "role",
                    "initial_option": {"text": {"type": "plain_text", "text": "driver"}, "value": "driver"},
                    "options": [{"text": {"type": "plain_text", "text": r}, "value": r} for r in ROLE_OPTIONS],
                },
            },
        ],
    }


def _handle_dispatch_invite_user_button(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_invite_user_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_invite_user_modal())
    except Exception as exc:
        logger.warning("views_open failed for invite-user modal: %s", exc)


def _handle_dispatch_invite_user_submit(payload: dict, db: Session) -> dict:
    clicker_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(clicker_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_invite_user_submit", clicker_id)
        return {"response_action": "clear"}

    values = payload.get("view", {}).get("state", {}).get("values", {})
    slack_user_id = values.get("slack_user_block", {}).get("slack_user", {}).get("selected_user")
    username = (values.get("username_block", {}).get("username", {}).get("value") or "").strip().lower()
    name = (values.get("name_block", {}).get("name", {}).get("value") or "").strip()
    role = values.get("role_block", {}).get("role", {}).get("selected_option", {}).get("value", "driver")

    if not username or len(username) < 3:
        return {"response_action": "errors", "errors": {"username_block": "Username must be at least 3 characters."}}

    try:
        user, token = auth_routes.create_invite(db, username, name, role, slack_user_id)
    except ValueError as exc:
        return {"response_action": "errors", "errors": {"username_block": str(exc)}}

    link = auth_routes.set_password_url(token)
    client = _client()

    dm_sent = False
    if client and slack_user_id:
        try:
            _dm_driver(
                client, slack_user_id,
                f":wave: You've been invited to New Day Logistics Route Manager as *{username}* ({role}).\n"
                f"👉 <{link}|Set your password> to activate your account.",
            )
            dm_sent = True
        except Exception as exc:
            logger.warning("Invite DM to %s failed: %s", slack_user_id, exc)

    summary = (
        f"➕ *Invite sent* — *{username}* ({role})"
        + (f", DM sent to <@{slack_user_id}>" if dm_sent else ", DM NOT sent — share this link directly")
        + f"\n{link}"
    )
    if client:
        try:
            _dm_driver(client, clicker_id, summary)
        except Exception as exc:
            logger.warning("Invite confirmation DM to clicker failed: %s", exc)
        try:
            client.chat_postMessage(
                channel=DISPATCH_HOME_CHANNEL_ID,
                text=f"➕ *Invite sent* — *{username}* ({role}), DM {'sent' if dm_sent else 'failed'}",
            )
        except Exception as exc:
            logger.warning("Invite audit log post failed: %s", exc)

    return {"response_action": "clear"}


# ─────────────────────────────────────────────────────────────────────────────
# Reset Password — generates a one-time set-password link for an existing
# user and DMs it via Slack. Same underlying token mechanism as Invite User
# (auth.py's complete_token()) — a reset is really just "set a password on
# an already-active account" using the same link/page.
# ─────────────────────────────────────────────────────────────────────────────

def _reset_password_modal(db: Session) -> dict:
    users = db.query(User).order_by(User.username).all()
    if not users:
        return {
            "type": "modal",
            "title": {"type": "plain_text", "text": "Reset Password"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "No users found."}}],
        }

    options = [
        {"text": {"type": "plain_text", "text": f"{u.name or u.username} ({u.username})"[:75]}, "value": u.username}
        for u in users[:100]  # static_select's practical option-count ceiling
    ]

    return {
        "type": "modal",
        "callback_id": RESET_PASSWORD_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Reset Password"},
        "submit": {"type": "plain_text", "text": "Send Reset Link"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Generates a one-time link for the user to set a new password. DMs it to "
                            "them via Slack if we have their Slack account on file.",
                },
            },
            {
                "type": "input",
                "block_id": "user_block",
                "label": {"type": "plain_text", "text": "User"},
                "element": {"type": "static_select", "action_id": "username", "options": options},
            },
            {
                "type": "input",
                "block_id": "slack_user_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Re-confirm Slack user (only if not already linked)"},
                "element": {"type": "users_select", "action_id": "slack_user"},
            },
        ],
    }


def _handle_dispatch_reset_password_button(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_reset_password_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_reset_password_modal(db))
    except Exception as exc:
        logger.warning("views_open failed for reset-password modal: %s", exc)


def _handle_dispatch_reset_password_submit(payload: dict, db: Session) -> dict:
    clicker_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(clicker_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_reset_password_submit", clicker_id)
        return {"response_action": "clear"}

    values = payload.get("view", {}).get("state", {}).get("values", {})
    username = values.get("user_block", {}).get("username", {}).get("selected_option", {}).get("value")
    slack_user_override = values.get("slack_user_block", {}).get("slack_user", {}).get("selected_user")

    if not username:
        return {"response_action": "clear"}

    try:
        user, token = auth_routes.create_password_reset(db, username, slack_user_id=slack_user_override)
    except ValueError as exc:
        logger.warning("Reset-password failed for %s: %s", username, exc)
        return {"response_action": "clear"}

    link = auth_routes.set_password_url(token)
    client = _client()

    dm_sent = False
    target_slack_id = slack_user_override or user.slack_user_id
    if client and target_slack_id:
        try:
            _dm_driver(
                client, target_slack_id,
                f":key: Your New Day Logistics Route Manager password reset was requested.\n"
                f"👉 <{link}|Set a new password>. If you didn't request this, contact dispatch.",
            )
            dm_sent = True
        except Exception as exc:
            logger.warning("Reset DM to %s failed: %s", target_slack_id, exc)

    summary = (
        f"🔑 *Password reset* — *{username}*"
        + (", DM sent" if dm_sent else ", DM NOT sent — share this link directly")
        + f"\n{link}"
    )
    if client:
        try:
            _dm_driver(client, clicker_id, summary)
        except Exception as exc:
            logger.warning("Reset confirmation DM to clicker failed: %s", exc)
        try:
            client.chat_postMessage(
                channel=DISPATCH_HOME_CHANNEL_ID,
                text=f"🔑 *Password reset* — *{username}*, DM {'sent' if dm_sent else 'failed'}",
            )
        except Exception as exc:
            logger.warning("Reset audit log post failed: %s", exc)

    return {"response_action": "clear"}


# ─────────────────────────────────────────────────────────────────────────────
# Stop/Resume Ingest Notifications — PIN-gated (added 2026-07-20). A broken
# ingest can re-alert every time someone re-uploads the same file trying to
# fix it, even though the team already knows it's broken and dispatch is
# already working the fix. This mutes that one alert channel
# (_post_ingest_error in daily_notify.py) without needing a redeploy — the
# PIN is a lightweight guard against an accidental click silencing a real
# alert, not real access control.
# ─────────────────────────────────────────────────────────────────────────────

def _ingest_alerts_muted(db: Session) -> bool:
    from api.src.routes.daily_notify import ingest_alerts_muted
    return ingest_alerts_muted(db)


def _ingest_alerts_button(db: Session) -> dict:
    muted = _ingest_alerts_muted(db)
    button = {
        "type": "button",
        "action_id": "dispatch_ingest_alerts_button",
        "text": (
            {"type": "plain_text", "text": "🔔 Resume Ingest Notifications", "emoji": True}
            if muted else
            {"type": "plain_text", "text": "🔕 Stop Ingest Notifications", "emoji": True}
        ),
    }
    if muted:
        button["style"] = "danger"
    return button


def _handle_dispatch_ingest_alerts_button(payload: dict, db: Session) -> None:
    user_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(user_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_ingest_alerts_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return

    muted = _ingest_alerts_muted(db)
    action_label = "Resume" if muted else "Stop"
    modal = {
        "type": "modal",
        "callback_id": INGEST_ALERTS_PIN_CALLBACK_ID,
        "private_metadata": "resume" if muted else "stop",
        "title": {"type": "plain_text", "text": "Ingest Notifications"},
        "submit": {"type": "plain_text", "text": action_label},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Notifications are currently *{'muted' if muted else 'active'}*.\n"
                        f"Enter the PIN to *{action_label.lower()}* ingest-failure alerts to #nday-mgt."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "pin_block",
                "label": {"type": "plain_text", "text": "PIN"},
                "element": {"type": "plain_text_input", "action_id": "pin", "min_length": 1, "max_length": 10},
            },
        ],
    }
    try:
        client.views_open(trigger_id=trigger_id, view=modal)
    except Exception as exc:
        logger.warning("views_open failed for ingest-alerts PIN modal: %s", exc)


def _handle_dispatch_ingest_alerts_pin_submit(payload: dict, db: Session) -> dict:
    from api.src.routes.daily_notify import set_ingest_alerts_muted

    clicker_id = payload.get("user", {}).get("id", "")
    if not is_dispatch_staff(clicker_id, db):
        logger.warning("Non-dispatch user %s attempted dispatch_ingest_alerts_pin_submit", clicker_id)
        return {"response_action": "clear"}

    view = payload.get("view", {})
    action = view.get("private_metadata")  # "stop" or "resume"
    values = view.get("state", {}).get("values", {})
    pin = values.get("pin_block", {}).get("pin", {}).get("value", "")

    client = _client()
    if pin != INGEST_ALERTS_PIN:
        if client:
            try:
                _dm_driver(client, clicker_id, ":no_entry: Incorrect PIN — ingest notifications were not changed.")
            except Exception as exc:
                logger.warning("Wrong-PIN DM failed for %s: %s", clicker_id, exc)
        return {"response_action": "clear"}

    muted = action == "stop"
    set_ingest_alerts_muted(db, muted, by=clicker_id)

    summary = (
        ":no_bell: Ingest-failure notifications *stopped* — flip this back on once ingests are confirmed working."
        if muted else
        ":bell: Ingest-failure notifications *resumed*."
    )
    if client:
        try:
            _dm_driver(client, clicker_id, summary)
        except Exception as exc:
            logger.warning("Ingest-alerts confirmation DM failed for %s: %s", clicker_id, exc)
        try:
            client.chat_postMessage(channel=DISPATCH_HOME_CHANNEL_ID, text=summary)
        except Exception as exc:
            logger.warning("Ingest-alerts audit log post failed: %s", exc)

    return {"response_action": "clear"}
