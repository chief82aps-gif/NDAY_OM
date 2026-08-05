"""
HR Home tab — added 2026-07-26. A persistent Slack App Home view for HR
staff (gated by document_routing.is_hr_staff(), separate from the "hr"
role which now routes to a channel for message posts, not people).

Surfaces what HR actually needs day to day: links straight into the
website dashboards, today's EOD survey flags (crash/injury/van issues/
management-contact requests), and a pointer to the sentiment survey —
the full AI-analyzed summary still posts to the HR channel nightly
(sentiment_survey.py), this tab is for checking in on demand rather than
waiting for that post.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from api.src.database import EodSurveyResponse, SentimentSurveyResponse
from api.src.routes.document_routing import is_hr_staff, get_role_slack_ids
from api.src.timezone import PACIFIC

logger = logging.getLogger(__name__)

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
BACKEND_URL = os.getenv("BACKEND_URL", "https://nday-om.onrender.com")


def _slack_login_url(redirect_path: str, dash_user, dash_token: str) -> str:
    """Links straight to the frontend page with a real, ready-to-use
    session token already attached (changed 2026-07-30 — was: always
    force a fresh /auth/slack/login round trip on every single tap,
    which re-hit Slack's own "sign in to workspace" wall every time on
    mobile, since Slack's in-app browser doesn't reliably persist that
    between separate link opens). dash_user/dash_token are minted once
    per Home-tab render (see slack_home.py's _build_combined_home_blocks)
    via auth.get_or_create_user_for_slack()/issue_jwt_for_user() — this
    trusts Slack's own app_home_opened event + the live is_dispatch_staff/
    is_hr_staff channel-membership check as sufficient identity proof, so
    no separate OAuth handshake is needed just to open a dashboard button,
    even the very first time. Same query-param shape the OAuth callback
    already produces (slack_token/username/name/role), so the frontend's
    existing AuthContext handling needs no changes."""
    from urllib.parse import urlencode
    params = urlencode({
        "slack_token": dash_token,
        "username": dash_user.username,
        "name": dash_user.name or dash_user.username,
        "role": dash_user.role,
    })
    return f"{FRONTEND_URL}{redirect_path}?{params}"

HR_INVITE_CALLBACK_ID = "hr_home_invite_user_submit"
HR_INVITE_ROLE_OPTIONS = ["admin", "manager", "dispatcher", "driver"]

SEND_SENTIMENT_SURVEY_CALLBACK_ID = "hr_home_send_sentiment_survey_submit"


def build_hr_home_view_blocks(db: Session, dash_user, dash_token: str) -> list:
    """Pure builder — no Slack API calls, unit-testable against fixture data."""
    today = datetime.now(PACIFIC).date()

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🗂️ HR Dashboard", "emoji": True}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "hr_home_open_eod_admin",
                    "text": {"type": "plain_text", "text": "📋 EOD Responses", "emoji": True},
                    "url": _slack_login_url("/eod-admin", dash_user, dash_token),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_open_sentiment_admin",
                    "text": {"type": "plain_text", "text": "💬 Sentiment Survey", "emoji": True},
                    "url": _slack_login_url("/sentiment-survey-admin", dash_user, dash_token),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_open_discipline_tracker",
                    "text": {"type": "plain_text", "text": "📝 Write-Ups", "emoji": True},
                    "url": _slack_login_url("/discipline-tracker", dash_user, dash_token),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_invite_user_button",
                    "text": {"type": "plain_text", "text": "➕ Invite to Website", "emoji": True},
                    "style": "primary",
                },
                {
                    "type": "button",
                    "action_id": "hr_home_send_sentiment_survey_button",
                    "text": {"type": "plain_text", "text": "🗣️ Send Sentiment Survey", "emoji": True},
                },
                {
                    "type": "button",
                    "action_id": "hr_report_glitch",
                    "text": {"type": "plain_text", "text": "🐛 Report an App Glitch", "emoji": True},
                },
                {
                    "type": "button",
                    "action_id": "hr_submit_suggestion",
                    "text": {"type": "plain_text", "text": "💡 Submit a Suggestion", "emoji": True},
                },
            ],
        },
        {"type": "divider"},
    ]

    # ── Today's EOD flags ──────────────────────────────────────────────────
    eod_rows = db.query(EodSurveyResponse).filter(EodSurveyResponse.survey_date == today).all()
    flagged = [
        r for r in eod_rows
        if r.crash_occurred or r.injury_occurred or r.incident_occurred or r.van_issues or r.needs_management_contact
    ]
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Today's EOD Survey* — {len(eod_rows)} submitted" + (f", *{len(flagged)} flagged*" if flagged else ", none flagged"),
        },
    })
    for r in flagged[:10]:
        tags = []
        if r.crash_occurred:
            tags.append("🚨 Crash")
        if r.injury_occurred:
            tags.append("🚨 Injury")
        if r.incident_occurred:
            tags.append("⚠️ Incident")
        if r.van_issues:
            tags.append("🔧 Van")
        if r.needs_management_contact:
            tags.append("👔 Wants contact")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"• *{r.driver_name}* — {', '.join(tags)}"},
        })
    if len(flagged) > 10:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"+ {len(flagged) - 10} more — see the full report"}],
        })

    blocks.append({"type": "divider"})

    # ── Sentiment survey — count only, full AI summary posts nightly ──────
    sentiment_count = db.query(SentimentSurveyResponse).filter(SentimentSurveyResponse.survey_date == today).count()
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Sentiment Survey* — {sentiment_count} check-in{'s' if sentiment_count != 1 else ''} today. "
                    "Full AI-flagged summary posts here nightly; use the button above for raw responses.",
        },
    })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_Updated when you open this tab · {today.strftime('%A, %B %-d')}_"}],
    })

    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Invite to Website — lets HR bring a new hire into the dashboard/website
# login directly from this tab, reusing the same pending-account + set-your-
# own-password mechanism as Dispatch Home's "Invite User" button
# (auth.create_invite). Kept as its own copy here (not a shared import of
# slack_dispatch_home's modal) since the two tabs are gated by different
# roles and post their audit trail to different channels.
# ─────────────────────────────────────────────────────────────────────────────

def _invite_user_modal() -> dict:
    return {
        "type": "modal",
        "callback_id": HR_INVITE_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Invite to Website"},
        "submit": {"type": "plain_text", "text": "Send Invite"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Creates a pending dashboard login and DMs the person a link to set their "
                            "own password. The account stays inactive until they complete it — this also "
                            "links their Slack account so they can use *Sign in with Slack* afterward.",
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
                    "options": [{"text": {"type": "plain_text", "text": r}, "value": r} for r in HR_INVITE_ROLE_OPTIONS],
                },
            },
        ],
    }


def _handle_hr_home_invite_user_button(payload: dict, db: Session) -> None:
    from api.src.routes.slack_home import _client

    user_id = payload.get("user", {}).get("id", "")
    if not is_hr_staff(user_id, db):
        logger.warning("Non-HR user %s attempted hr_home_invite_user_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_invite_user_modal())
    except Exception as exc:
        logger.warning("views_open failed for HR invite modal: %s", exc)


def _handle_hr_home_invite_user_submit(payload: dict, db: Session) -> dict:
    from api.src.routes.slack_home import _client, _dm_driver
    from api.src.routes import auth as auth_routes

    clicker_id = payload.get("user", {}).get("id", "")
    if not is_hr_staff(clicker_id, db):
        logger.warning("Non-HR user %s attempted hr_home_invite_user_submit", clicker_id)
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

    if client and slack_user_id:
        try:
            hr_channel_ids = get_role_slack_ids(db, "hr")
            if hr_channel_ids:
                client.conversations_invite(channel=hr_channel_ids[0], users=slack_user_id)
        except Exception as exc:
            # already_in_channel is expected/harmless; anything else just
            # gets logged — a failed channel-invite shouldn't block the
            # account invite itself.
            logger.warning("Adding %s to hr channel failed (or already a member): %s", slack_user_id, exc)

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
            logger.warning("HR invite DM to %s failed: %s", slack_user_id, exc)

    summary = (
        f"➕ *Invite sent* — *{username}* ({role})"
        + (f", DM sent to <@{slack_user_id}>" if dm_sent else ", DM NOT sent — share this link directly")
        + f"\n{link}"
    )
    if client:
        try:
            _dm_driver(client, clicker_id, summary)
        except Exception as exc:
            logger.warning("HR invite confirmation DM to clicker failed: %s", exc)
        try:
            hr_channel_ids = get_role_slack_ids(db, "hr")
            if hr_channel_ids:
                client.chat_postMessage(
                    channel=hr_channel_ids[0],
                    text=f"➕ *Invite sent* — *{username}* ({role}), DM {'sent' if dm_sent else 'failed'}",
                )
        except Exception as exc:
            logger.warning("HR invite audit log post failed: %s", exc)

    return {"response_action": "clear"}


# ─────────────────────────────────────────────────────────────────────────────
# Send Sentiment Survey — select specific drivers and/or send to everyone.
# The driver-facing copy stays identical either way (see sentiment_survey.py's
# send_sentiment_survey() docstring) — this modal is purely an HR-side
# targeting tool, it doesn't change what the driver sees or is told.
# ─────────────────────────────────────────────────────────────────────────────

def _send_sentiment_survey_modal(db: Session) -> dict:
    from api.src.database import DriverRosterEntry

    drivers = (
        db.query(DriverRosterEntry)
        .filter(DriverRosterEntry.is_active == True, DriverRosterEntry.slack_member_id.isnot(None))  # noqa: E712
        .order_by(DriverRosterEntry.payroll_name)
        .all()
    )
    options = [
        {"text": {"type": "plain_text", "text": d.payroll_name[:75]}, "value": str(d.id)}
        for d in drivers[:100]  # static_select's practical option-count ceiling
    ]

    blocks: list = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Check *Send to all* below, or select specific drivers instead. "
                        "Anyone who already checked in today is skipped automatically — "
                        "the message is the same either way, so nobody can tell they were "
                        "individually targeted.",
            },
        },
        {
            "type": "input",
            "block_id": "send_to_all_block",
            "optional": True,
            "label": {"type": "plain_text", "text": "Send to all"},
            "element": {
                "type": "checkboxes",
                "action_id": "send_to_all",
                "options": [{"text": {"type": "plain_text", "text": "Send to every active, linked driver"}, "value": "all"}],
            },
        },
    ]
    if options:
        blocks.append({
            "type": "input",
            "block_id": "drivers_block",
            "optional": True,
            "label": {"type": "plain_text", "text": "Or select specific drivers"},
            "element": {"type": "multi_static_select", "action_id": "drivers", "options": options},
        })

    return {
        "type": "modal",
        "callback_id": SEND_SENTIMENT_SURVEY_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Send Sentiment Survey"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def _handle_hr_home_send_sentiment_survey_button(payload: dict, db: Session) -> None:
    from api.src.routes.slack_home import _client

    user_id = payload.get("user", {}).get("id", "")
    if not is_hr_staff(user_id, db):
        logger.warning("Non-HR user %s attempted hr_home_send_sentiment_survey_button", user_id)
        return
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_send_sentiment_survey_modal(db))
    except Exception as exc:
        logger.warning("views_open failed for send-sentiment-survey modal: %s", exc)


def _send_sentiment_survey_background(roster_ids: list[int], send_to_all: bool) -> None:
    """The actual bulk send (up to 100+ individual chat_postMessage calls)
    happens here, off the Slack view_submission request/response cycle --
    that loop was blowing well past Slack's ~3s ack window, causing Slack's
    client to show a "trouble connecting" error even though the backend
    request kept running to completion in the background (confirmed
    2026-07-29: a "104 sent" DM still arrived after the on-screen error).
    Same BackgroundTasks pattern as slack_dispatch_home.py's
    _run_rerun_and_report -- opens its own SessionLocal() since the
    request-scoped db session is gone by the time this runs.

    Only posts to #nday-hr -- no DM to the clicker -- per explicit
    direction (2026-07-29) to keep this send's confirmation out of every
    other channel/DM it was reaching."""
    from api.src.database import SessionLocal
    from api.src.routes.slack_home import _client
    from api.src.routes.sentiment_survey import send_sentiment_survey

    db = SessionLocal()
    hr_channel_ids: list[str] = []
    try:
        hr_channel_ids = get_role_slack_ids(db, "hr")
        result = send_sentiment_survey(roster_ids, send_to_all, db)
        summary = (
            f"🗣️ *Sentiment survey sent* — {result['sent']} sent"
            f", {result['already_submitted']} already checked in today"
            f", {result['no_slack_id']} skipped (no Slack link)"
            f" (of {result['total_candidates']} considered)"
        )
    except Exception as exc:
        logger.exception("Sentiment survey background send failed")
        summary = f":x: Sentiment survey send failed: {exc}"
    finally:
        db.close()

    client = _client()
    if client and hr_channel_ids:
        try:
            client.chat_postMessage(channel=hr_channel_ids[0], text=summary)
        except Exception as exc:
            logger.warning("Sentiment survey send audit log post failed: %s", exc)


def _handle_hr_home_send_sentiment_survey_submit(payload: dict, db: Session, background_tasks: BackgroundTasks) -> dict:
    clicker_id = payload.get("user", {}).get("id", "")
    if not is_hr_staff(clicker_id, db):
        logger.warning("Non-HR user %s attempted hr_home_send_sentiment_survey_submit", clicker_id)
        return {"response_action": "clear"}

    values = payload.get("view", {}).get("state", {}).get("values", {})
    send_to_all = bool(values.get("send_to_all_block", {}).get("send_to_all", {}).get("selected_options"))
    selected = values.get("drivers_block", {}).get("drivers", {}).get("selected_options") or []
    roster_ids = [int(opt["value"]) for opt in selected]

    if not send_to_all and not roster_ids:
        return {"response_action": "errors", "errors": {"send_to_all_block": "Check 'Send to all' or select at least one driver."}}

    background_tasks.add_task(_send_sentiment_survey_background, roster_ids, send_to_all)
    return {"response_action": "clear"}
