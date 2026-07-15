"""
Slack Home — the driver-facing App Home tab (Track 1 of the two-track driver
platform, see Governance/01_NDL_Slack_Dashboard_App.md).

Handles the Events API subscription (app_home_opened -> views.publish) and
the quick-capture modals reachable from the Home tab (crash/injury/incident
report, request time off). Button *taps* from the Home tab still land on
slack_interactions.py's single /slack/interactions endpoint, same as every
other Slack interactive component in this app — this module only owns the
/slack/events endpoint and the Block Kit builders/handlers that
slack_interactions.py dispatches into.

Crash/injury/incident quick-capture intentionally does NOT try to replicate
the compliant "DA Incident Packet" field set — it captures a short free-text
description, opens a lightweight record, and immediately notifies dispatch/
ops/HR. The office finishes the compliant paperwork through the existing
(correctly login-gated) /crash-report flow. Exact field sets get revisited
once NDL's manual HRM/OPS forms are reviewed.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.src.database import (
    get_db,
    DriverRosterEntry,
    QualityMetricDriver,
    QualityMetricSnapshot,
    TimeOffRequest,
)
from api.src.routes.dvic import _name_tokens
from api.src.routes.document_routing import resolve_recipients, is_dispatch_staff
from api.src.routes.quality import get_rankings, _METRIC_LABELS
from api.src.routes.slack_interactions import (
    _resolve_driver,
    _verify_slack_signature,
    _issue_callout_token,
    _default_shift_date,
    FRONTEND_URL,
    TOKEN_TTL_HOURS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/slack", tags=["slack"])

# Same gate used for every other driver-facing DM in this codebase
# (rostering.py, dvic.py) — this module sends real driver DMs (callout
# tokens, report/RTO confirmations) and hadn't been wired to it, which
# would have let it go live with zero staged rollout the moment the Home
# tab is enabled in Slack's app config. Kept off until explicit sign-off,
# same as the others.
_DM_ACTIVE = os.getenv("DRIVER_DM_ACTIVE", "false").lower() == "true"

_INACTIVE_BLOCKS = [
    {"type": "header", "text": {"type": "plain_text", "text": "🚧 Coming Soon", "emoji": True}},
    {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "The Driver Home tab isn't live yet. Check back soon."},
    },
]


def _client():
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return None
    from slack_sdk import WebClient
    return WebClient(token=token)


def _dm_driver(client, slack_user_id: str, text: str) -> None:
    """App Home has no channel context to send an ephemeral into — a real
    DM is the correct surface for anything private triggered from Home."""
    convo = client.conversations_open(users=slack_user_id)
    channel_id = convo["channel"]["id"]
    client.chat_postMessage(channel=channel_id, text=text)


# ─────────────────────────────────────────────────────────────────────────────
# Identity, rank, and color helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_quality_driver(roster_entry: DriverRosterEntry, db: Session) -> Optional[QualityMetricDriver]:
    """Match a roster entry to its latest-week quality row by name-token
    overlap — same approach dvic.py's _find_roster_entry uses in reverse.
    No FK exists between the two tables today."""
    latest_week = db.query(func.max(QualityMetricSnapshot.week)).scalar()
    if not latest_week:
        return None
    snap = db.query(QualityMetricSnapshot).filter(QualityMetricSnapshot.week == latest_week).first()
    if not snap:
        return None

    target = _name_tokens(roster_entry.payroll_name)
    best: Optional[QualityMetricDriver] = None
    best_score = 0
    for row in db.query(QualityMetricDriver).filter(QualityMetricDriver.snapshot_id == snap.id).all():
        score = len(target & _name_tokens(row.driver_name))
        if score > best_score:
            best_score, best = score, row
    return best if best_score >= 1 else None


def _score_emoji(score: Optional[float]) -> str:
    """Mirrors frontend/pages/driver-quality.tsx's scoreBar() thresholds
    exactly — Block Kit has no native colored progress bar."""
    if score is None:
        return "⚪"
    if score >= 90:
        return "🟢"
    if score >= 70:
        return "🟡"
    return "🔴"


def _metric_bar_fields(entry: dict, n: int = 6) -> list:
    metrics = entry.get("metrics", {})
    scored = [
        (label, metrics.get(attr))
        for attr, label in _METRIC_LABELS.items()
        if metrics.get(attr) is not None
    ]
    scored.sort(key=lambda x: x[1])
    return [
        {"type": "mrkdwn", "text": f"{_score_emoji(score)} *{label}*\n{score:.0f}"}
        for label, score in scored[:n]
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Home tab builder
# ─────────────────────────────────────────────────────────────────────────────

def _footer_block() -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "New Day Logistics · Questions? Contact dispatch directly."}],
    }


def _no_driver_blocks() -> list:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "👋 Welcome", "emoji": True}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "⚠️ *Your Slack account isn't linked to a driver roster entry yet.*\n"
                        "Contact your dispatcher to get set up, then reopen this tab.",
            },
        },
        {"type": "divider"},
        _footer_block(),
    ]


def build_home_view_blocks(driver: Optional[DriverRosterEntry], db: Session) -> list:
    """Pure builder — no Slack API calls in here, so it's unit-testable
    against fixture data without a live token."""
    if not driver:
        return _no_driver_blocks()

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"👋 {driver.payroll_name}", "emoji": True}},
    ]

    quality_row = _resolve_quality_driver(driver, db)
    match = None
    driver_count = 0
    if quality_row:
        rankings = get_rankings(week=None, db=db)
        driver_count = rankings.get("driver_count", 0)
        match = next(
            (d for d in rankings.get("drivers", []) if d["transporter_id"] == quality_row.transporter_id),
            None,
        )

    if match:
        score = match.get("overall_score")
        score_text = f" · Score {score:.1f}" if score is not None else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Standing:* {match['rank']} of {driver_count} · {match.get('overall_standing') or '—'}{score_text}",
            },
        })
        fields = _metric_bar_fields(match)
        if fields:
            blocks.append({"type": "section", "fields": fields})
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No quality data matched to your driver record yet this week._"},
        })

    blocks.append({"type": "divider"})

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": "btn_rts",
                "text": {"type": "plain_text", "text": "🔄 Return to Station", "emoji": True},
                "style": "primary",
                "url": f"{FRONTEND_URL}/rts",
            },
            {
                "type": "button",
                "action_id": "home_callout_button",
                "text": {"type": "plain_text", "text": "📋 Call Out", "emoji": True},
                "style": "danger",
            },
        ],
    })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": "home_report_crash",
                "text": {"type": "plain_text", "text": "🚗 Report Crash", "emoji": True},
                "style": "danger",
            },
            {
                "type": "button",
                "action_id": "home_report_injury",
                "text": {"type": "plain_text", "text": "🩹 Report Injury", "emoji": True},
                "style": "danger",
            },
            {
                "type": "button",
                "action_id": "home_incident_report",
                "text": {"type": "plain_text", "text": "⚠️ Incident Report", "emoji": True},
            },
        ],
    })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": "home_rto_button",
                "text": {"type": "plain_text", "text": "🗓️ Request Time Off", "emoji": True},
            },
        ],
    })

    blocks.append({"type": "divider"})
    blocks.append(_footer_block())
    return blocks


def _publish_home(slack_user_id: str, db: Session) -> None:
    if not slack_user_id:
        return
    client = _client()
    if not client:
        return
    try:
        # Dispatch staff get a completely different Home tab — not gated by
        # DRIVER_DM_ACTIVE, since that flag is specifically about whether
        # it's safe to DM real drivers, not about dispatch staff using
        # their own tool.
        if is_dispatch_staff(slack_user_id, db):
            from api.src.routes.slack_dispatch_home import build_dispatch_home_view_blocks
            blocks = build_dispatch_home_view_blocks(db)
            client.views_publish(user_id=slack_user_id, view={"type": "home", "blocks": blocks})
            return
        if not _DM_ACTIVE:
            client.views_publish(user_id=slack_user_id, view={"type": "home", "blocks": _INACTIVE_BLOCKS})
            return
        driver = _resolve_driver(slack_user_id, db)
        blocks = build_home_view_blocks(driver, db)
        client.views_publish(user_id=slack_user_id, view={"type": "home", "blocks": blocks})
    except Exception as exc:
        logger.warning("views_publish failed for %s: %s", slack_user_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Events API — Home tab open
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/events")
async def slack_events(request: Request, db: Session = Depends(get_db)):
    """Request URL for Slack's Event Subscriptions (separate config from the
    Interactivity Request URL that slack_interactions.py owns)."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        raise HTTPException(403, "Invalid Slack signature.")

    payload = json.loads(body or b"{}")
    event_type = payload.get("type")

    if event_type == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if event_type == "event_callback":
        event = payload.get("event", {})
        if event.get("type") == "app_home_opened" and event.get("tab") == "home":
            _publish_home(event.get("user", ""), db)

    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Home tab action handlers — dispatched from slack_interactions.py
# ─────────────────────────────────────────────────────────────────────────────

def _handle_home_callout_button(payload: dict, db: Session) -> None:
    if not _DM_ACTIVE:
        return
    user_id = payload.get("user", {}).get("id", "")
    client = _client()
    if not client:
        return

    driver = _resolve_driver(user_id, db)
    if not driver:
        try:
            _dm_driver(
                client, user_id,
                "⚠️ Your Slack account isn't linked to a driver roster entry. "
                "Contact your dispatcher to get set up, then try again.",
            )
        except Exception as exc:
            logger.warning("Home callout DM failed: %s", exc)
        return

    shift_date = _default_shift_date()
    token = _issue_callout_token(driver.payroll_name, shift_date)
    url = f"{FRONTEND_URL}/callout?token={token}"
    try:
        _dm_driver(
            client, user_id,
            f"Your personal absence report link — *only you can see this message.*\n\n"
            f"<{url}|👆 Tap here to report your absence>\n\n"
            f"_Expires in {TOKEN_TTL_HOURS} hours. Do not share this link._",
        )
    except Exception as exc:
        logger.warning("Home callout DM failed: %s", exc)


_REPORT_TITLES = {"crash": "Report a Crash", "injury": "Report an Injury", "incident": "Incident Report"}
_REPORT_TYPE_BY_ACTION = {
    "home_report_crash": "crash",
    "home_report_injury": "injury",
    "home_incident_report": "incident",
}


def _quick_report_modal(report_type: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "home_report_submit",
        "private_metadata": report_type,
        "title": {"type": "plain_text", "text": _REPORT_TITLES.get(report_type, "Report")},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "description_block",
                "label": {"type": "plain_text", "text": "What happened, briefly?"},
                "element": {"type": "plain_text_input", "action_id": "description", "multiline": True},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "A manager will follow up for full details."}],
            },
        ],
    }


def _handle_home_report_button(payload: dict, db: Session, action_id: str) -> None:
    report_type = _REPORT_TYPE_BY_ACTION.get(action_id, "incident")
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_quick_report_modal(report_type))
    except Exception as exc:
        logger.warning("views_open failed for %s report: %s", report_type, exc)


def _handle_home_report_submit(payload: dict, db: Session) -> dict:
    if not _DM_ACTIVE:
        return {"response_action": "clear"}
    view = payload.get("view", {})
    report_type = view.get("private_metadata", "incident")
    user_id = payload.get("user", {}).get("id", "")
    values = view.get("state", {}).get("values", {})
    description = values.get("description_block", {}).get("description", {}).get("value") or ""

    driver = _resolve_driver(user_id, db)
    driver_name = driver.payroll_name if driver else (payload.get("user", {}).get("username") or user_id)

    record_note = ""
    if report_type == "crash":
        from api.src.routes.crash_report import start_crash_report, StartRequest
        try:
            result = start_crash_report(StartRequest(driver_name=driver_name, submitted_by="Slack (driver quick-report)"), db)
            record_note = f" (draft {result['report']['report_number']} created)"
        except Exception as exc:
            logger.warning("Quick crash-report draft creation failed: %s", exc)

    doc_type = {"crash": "crash_report", "injury": "injury_report"}.get(report_type, "incident_report")
    client = _client()
    if client:
        recipients = resolve_recipients(doc_type, db)
        all_ids = sorted({sid for ids in recipients.values() for sid in ids})
        text = f"🚨 *{report_type.title()} report* from *{driver_name}*{record_note}\n> {description}"
        for slack_id in all_ids:
            try:
                client.chat_postMessage(channel=slack_id, text=text)
            except Exception as exc:
                logger.warning("Report notify failed for %s: %s", slack_id, exc)
        try:
            _dm_driver(client, user_id, f"✅ Your {report_type} report was submitted. A manager will follow up.")
        except Exception as exc:
            logger.warning("Report confirmation DM failed: %s", exc)

    return {"response_action": "clear"}


def _rto_modal() -> dict:
    return {
        "type": "modal",
        "callback_id": "home_rto_submit",
        "title": {"type": "plain_text", "text": "Request Time Off"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "start_block",
                "label": {"type": "plain_text", "text": "Start date"},
                "element": {"type": "datepicker", "action_id": "start_date"},
            },
            {
                "type": "input",
                "block_id": "end_block",
                "label": {"type": "plain_text", "text": "End date"},
                "element": {"type": "datepicker", "action_id": "end_date"},
            },
            {
                "type": "input",
                "block_id": "type_block",
                "label": {"type": "plain_text", "text": "Type"},
                "element": {
                    "type": "static_select",
                    "action_id": "request_type",
                    "options": [
                        {"text": {"type": "plain_text", "text": "PTO"}, "value": "PTO"},
                        {"text": {"type": "plain_text", "text": "UTO"}, "value": "UTO"},
                        {"text": {"type": "plain_text", "text": "Unpaid"}, "value": "Unpaid"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "reason_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Reason"},
                "element": {"type": "plain_text_input", "action_id": "reason", "multiline": True},
            },
        ],
    }


def _handle_home_rto_button(payload: dict, db: Session) -> None:
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_rto_modal())
    except Exception as exc:
        logger.warning("views_open failed for RTO modal: %s", exc)


def _handle_home_rto_submit(payload: dict, db: Session) -> dict:
    if not _DM_ACTIVE:
        return {"response_action": "clear"}
    view = payload.get("view", {})
    values = view.get("state", {}).get("values", {})
    user_id = payload.get("user", {}).get("id", "")

    start_date_str = values.get("start_block", {}).get("start_date", {}).get("selected_date")
    end_date_str = values.get("end_block", {}).get("end_date", {}).get("selected_date")
    request_type = values.get("type_block", {}).get("request_type", {}).get("selected_option", {}).get("value")
    reason = values.get("reason_block", {}).get("reason", {}).get("value") or ""

    errors = {}
    if not start_date_str:
        errors["start_block"] = "Start date is required"
    if not end_date_str:
        errors["end_block"] = "End date is required"
    if not request_type:
        errors["type_block"] = "Type is required"
    if errors:
        return {"response_action": "errors", "errors": errors}

    driver = _resolve_driver(user_id, db)
    driver_name = driver.payroll_name if driver else (payload.get("user", {}).get("username") or user_id)

    record = TimeOffRequest(
        driver_name=driver_name,
        slack_member_id=user_id,
        request_type=request_type,
        start_date=date.fromisoformat(start_date_str),
        end_date=date.fromisoformat(end_date_str),
        reason=reason,
    )
    db.add(record)
    db.commit()

    client = _client()
    if client:
        recipients = resolve_recipients("time_off_request", db)
        all_ids = sorted({sid for ids in recipients.values() for sid in ids})
        text = f"🗓️ *Time off request* from *{driver_name}*: {request_type} {start_date_str} → {end_date_str}\n> {reason}"
        for slack_id in all_ids:
            try:
                client.chat_postMessage(channel=slack_id, text=text)
            except Exception as exc:
                logger.warning("RTO notify failed for %s: %s", slack_id, exc)
        try:
            _dm_driver(client, user_id, "✅ Your time-off request was submitted.")
        except Exception as exc:
            logger.warning("RTO confirmation DM failed: %s", exc)

    return {"response_action": "clear"}
