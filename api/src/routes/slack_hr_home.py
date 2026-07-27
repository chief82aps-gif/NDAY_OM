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
from datetime import date

from sqlalchemy.orm import Session

from api.src.database import EodSurveyResponse, SentimentSurveyResponse
from api.src.routes.document_routing import is_hr_staff, get_role_slack_ids

logger = logging.getLogger(__name__)

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nday-om.vercel.app")
BACKEND_URL = os.getenv("BACKEND_URL", "https://nday-om.onrender.com")


def _slack_login_url(redirect_path: str) -> str:
    """Routes a dashboard button through Slack OAuth login first (added
    2026-07-27) so clicking it from Slack authenticates via the clicker's
    Slack identity and lands directly on the target page — no separate
    website login screen, as long as their Slack ID is already linked to
    a User row (same as the plain Sign in with Slack flow)."""
    from urllib.parse import quote
    return f"{BACKEND_URL}/auth/slack/login?redirect={quote(redirect_path)}"

HR_INVITE_CALLBACK_ID = "hr_home_invite_user_submit"
HR_INVITE_ROLE_OPTIONS = ["admin", "manager", "dispatcher", "driver"]


def build_hr_home_view_blocks(db: Session) -> list:
    """Pure builder — no Slack API calls, unit-testable against fixture data."""
    today = date.today()

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🗂️ HR Dashboard", "emoji": True}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "hr_home_open_eod_admin",
                    "text": {"type": "plain_text", "text": "📋 EOD Responses", "emoji": True},
                    "url": _slack_login_url("/eod-admin"),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_open_sentiment_admin",
                    "text": {"type": "plain_text", "text": "💬 Sentiment Survey", "emoji": True},
                    "url": _slack_login_url("/sentiment-survey-admin"),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_open_discipline_tracker",
                    "text": {"type": "plain_text", "text": "📝 Write-Ups", "emoji": True},
                    "url": _slack_login_url("/discipline-tracker"),
                },
                {
                    "type": "button",
                    "action_id": "hr_home_invite_user_button",
                    "text": {"type": "plain_text", "text": "➕ Invite to Website", "emoji": True},
                    "style": "primary",
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
