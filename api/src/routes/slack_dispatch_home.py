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
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from api.src.database import DriverRosterEntry
from api.src.routes.document_routing import is_dispatch_staff
from api.src.routes.slack_home import _client, _dm_driver
from api.src.routes.slack_interactions import FRONTEND_URL

logger = logging.getLogger(__name__)

DISPATCH_HOME_CHANNEL_ID = os.getenv("DISPATCH_HOME_CHANNEL_ID", "C0BHGL7DLLC")

REMOVE_TERMINATED_CALLBACK_ID = "dispatch_remove_terminated_submit"
MAX_CANDIDATES = 10  # Block Kit checkboxes element practical/UI limit per block


# ─────────────────────────────────────────────────────────────────────────────
# Home tab builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dispatch_home_view_blocks(db: Session) -> list:
    """Pure builder — no Slack API calls in here, so it's unit-testable."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🛠️ Dispatch Home", "emoji": True}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Tools for ops/dispatch staff. More lands here over time."},
        },
        {"type": "divider"},
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
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "dispatch_preview_driver_home",
                    "text": {"type": "plain_text", "text": "👁️ Preview Driver Home", "emoji": True},
                },
            ],
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
