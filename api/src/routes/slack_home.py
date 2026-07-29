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

Injury/incident quick-capture intentionally does NOT try to replicate the
compliant "DA Incident Packet" field set — it captures a short free-text
description, opens a lightweight record, and immediately notifies dispatch/
ops/HR. Exact field sets get revisited once NDL's manual HRM/OPS forms are
reviewed.

Crash reports do NOT originate here. A driver calls dispatch to report a
crash; dispatch creates the draft from the Dispatch Home tab
(slack_dispatch_home.py's "🚗 Generate Crash Report" button ->
/crash-report) so the record — and the chain of who's accountable for
routing it — starts in the right place. See crash_report.py.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
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
from api.src.routes.document_routing import resolve_recipients, is_dispatch_staff, is_hr_staff
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

MGT_CHANNEL = os.getenv("SLACK_MGT_CHANNEL", "C0BCYAW7QP3")   # #nday-mgt
_SLACK_TEAM_ID = os.getenv("SLACK_TEAM_ID")

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


def _driver_rescue_bonus_this_week(driver: DriverRosterEntry, db: Session) -> Optional[dict]:
    """Driver's own accumulated rescue bonus for the current pay period
    (Sun-Sat, same window rescue.py's payroll report uses), shown on their
    Home tab per explicit 2026-07-24 request. Returns None if they have no
    bonus-eligible rescues this week — keeps the tab quiet for the many
    drivers who never rescue, rather than a permanent "$0" line."""
    from sqlalchemy import or_ as _or
    from api.src.database import RescueContribution, RescueEvent
    from api.src.routes.rescue import _week_bounds, _bonus_amount

    sunday, saturday = _week_bounds(date.today())
    name_tokens = _name_tokens(driver.payroll_name)
    if not name_tokens:
        return None

    contribs = (
        db.query(RescueContribution)
        .join(RescueEvent, RescueContribution.event_id == RescueEvent.event_id)
        .filter(
            RescueEvent.event_date >= sunday,
            RescueEvent.event_date <= saturday,
            _or(RescueContribution.bonus_eligible == True, RescueContribution.bonus_reinstated == True),
        )
        .all()
    )
    packages = sum(
        c.packages_taken or 0
        for c in contribs
        if len(name_tokens & _name_tokens(c.rescuing_driver_name)) >= 2
    )
    if packages <= 0:
        return None
    return {"packages": packages, "bonus": _bonus_amount(packages), "week_start": str(sunday), "week_end": str(saturday)}


REDEEM_BONUS_MODAL_CALLBACK_ID = "home_redeem_bonus_submit"


def _bonus_ledger_block(driver: DriverRosterEntry, db: Session) -> Optional[list]:
    """Driver's persistent banked bonus balance (RescueBonusLedger) with a
    Redeem button, shown only when there's something to redeem — added
    2026-07-27 alongside the banked-balance redesign (rescue.py). Separate
    from _driver_rescue_bonus_this_week above, which is just this week's
    activity, not the running balance."""
    from api.src.database import RescueBonusLedger
    ledger = db.query(RescueBonusLedger).filter(RescueBonusLedger.roster_id == driver.id).first()
    if not ledger or ledger.banked_amount < 10:
        return None
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🏦 ${ledger.banked_amount} Banked Bonus!", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Ready to redeem in $10 increments — tap below any time."},
        },
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "action_id": "home_redeem_bonus_button",
                "text": {"type": "plain_text", "text": "💵 Redeem Bonus", "emoji": True},
                "style": "primary",
            }],
        },
    ]


def _communication_buttons_block(driver: DriverRosterEntry, db: Session) -> Optional[dict]:
    """Quick-access row added 2026-07-29: a deep link to the driver's
    *actual* wave-of-the-day PTT-lite channel (see wave_lead.py's
    sync_wave_channels()) and a deep link straight to #nday-mgt so a
    driver can message dispatch without hunting for the channel. Slack
    has no API to trigger the native voice-clip recorder itself — these
    are `slack://channel` deep links (same scheme already used for the
    Home tab link in slack_interactions.py), not a custom PTT control.
    Either button is simply omitted if we don't have what we need to
    build its link (no team ID configured, or today's wave channel
    hasn't been created yet)."""
    if not _SLACK_TEAM_ID:
        return None

    elements = []

    from api.src.database import DailyRouteAssignment
    from api.src.routes.wave_lead import wave_number_for_assignment, get_wave_channel_id

    today = date.today()
    assignment = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == today, DailyRouteAssignment.roster_id == driver.id)
        .first()
    )
    if assignment:
        wave_number = wave_number_for_assignment(assignment.wave, getattr(assignment, "service_type", None))
        channel_id = get_wave_channel_id(wave_number, db)
        if channel_id:
            elements.append({
                "type": "button",
                "action_id": "home_talk_to_wave_team",
                "text": {"type": "plain_text", "text": "🎙️ Talk to My Wave Team", "emoji": True},
                "style": "primary",
                "url": f"slack://channel?team={_SLACK_TEAM_ID}&id={channel_id}",
            })

    elements.append({
        "type": "button",
        "action_id": "home_message_dispatch",
        "text": {"type": "plain_text", "text": "💬 Message Dispatch", "emoji": True},
        "url": f"slack://channel?team={_SLACK_TEAM_ID}&id={MGT_CHANNEL}",
    })

    return {"type": "actions", "elements": elements} if elements else None


def _redeem_bonus_modal(banked_amount: int) -> dict:
    options = [
        {"text": {"type": "plain_text", "text": f"${amt}"}, "value": str(amt)}
        for amt in range(10, banked_amount + 1, 10)
    ]
    return {
        "type": "modal",
        "callback_id": REDEEM_BONUS_MODAL_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Redeem Bonus"},
        "submit": {"type": "plain_text", "text": "Redeem"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"You have *${banked_amount}* banked. Redeem in $10 increments — HR will mark it paid on the weekly report."},
            },
            {
                "type": "input",
                "block_id": "amount_block",
                "label": {"type": "plain_text", "text": "Amount to redeem"},
                "element": {"type": "static_select", "action_id": "amount", "options": options},
            },
        ],
    }


def _handle_home_redeem_bonus_button(payload: dict, db: Session) -> None:
    from api.src.database import RescueBonusLedger
    user_id = payload.get("user", {}).get("id", "")
    trigger_id = payload.get("trigger_id")
    client = _client()
    if not client or not trigger_id:
        return
    driver = _resolve_driver(user_id, db)
    if not driver:
        return
    ledger = db.query(RescueBonusLedger).filter(RescueBonusLedger.roster_id == driver.id).first()
    if not ledger or ledger.banked_amount < 10:
        return
    try:
        client.views_open(trigger_id=trigger_id, view=_redeem_bonus_modal(ledger.banked_amount))
    except Exception as exc:
        logger.warning("views_open failed for redeem-bonus modal: %s", exc)


def _handle_home_redeem_bonus_submit(payload: dict, db: Session) -> dict:
    from api.src.routes.rescue import do_redeem_bonus
    user_id = payload.get("user", {}).get("id", "")
    driver = _resolve_driver(user_id, db)
    if not driver:
        return {"response_action": "clear"}

    values = payload.get("view", {}).get("state", {}).get("values", {})
    amount_str = values.get("amount_block", {}).get("amount", {}).get("selected_option", {}).get("value")
    if not amount_str:
        return {"response_action": "errors", "errors": {"amount_block": "Select an amount."}}

    try:
        redemption = do_redeem_bonus(driver.id, int(amount_str), db)
    except ValueError as exc:
        return {"response_action": "errors", "errors": {"amount_block": str(exc)}}

    client = _client()
    if client:
        try:
            _dm_driver(client, user_id, f"✅ Redeemed *${redemption.amount}* — HR will mark it paid on the weekly rescue bonus report.")
        except Exception as exc:
            logger.warning("Redeem confirmation DM failed: %s", exc)
        try:
            _publish_home(user_id, db)
        except Exception as exc:
            logger.warning("Home refresh after redeem failed: %s", exc)

    return {"response_action": "clear"}


def _driver_return_countdown(driver: DriverRosterEntry, db: Session) -> Optional[str]:
    """'Time remaining until expected return', computed fresh each time the
    Home tab is opened/re-rendered — there's no true live-ticking
    countdown possible in a Slack Home tab (no client-side JS to run one),
    so this is a snapshot that's accurate at open-time, not a continuously
    updating clock. Reuses the same live Cortex-pace ETA that drives
    eod_survey.py's per-driver timing and the Wave Status board's 'Return'
    column, added 2026-07-24 per explicit request."""
    from api.src.database import DailyRouteAssignment
    from api.src.routes.rostering import get_driver_expected_return_dt

    today = date.today()
    a = (
        db.query(DailyRouteAssignment)
        .filter(DailyRouteAssignment.assignment_date == today, DailyRouteAssignment.roster_id == driver.id)
        .first()
    )
    if not a:
        return None
    eta = get_driver_expected_return_dt(a.driver_name, today, db)
    if not eta:
        return None

    remaining_minutes = int((eta - datetime.utcnow()).total_seconds() // 60)
    if remaining_minutes <= 0:
        return "Any time now"
    hours, minutes = divmod(remaining_minutes, 60)
    return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"


def build_home_view_blocks(driver: Optional[DriverRosterEntry], db: Session) -> list:
    """Pure builder — no Slack API calls in here, so it's unit-testable
    against fixture data without a live token."""
    if not driver:
        return _no_driver_blocks()

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"👋 {driver.payroll_name}", "emoji": True}},
    ]

    # Bonus gets top billing — moved above standing/quality per explicit
    # 2026-07-27 request to emphasize it, rather than being buried further
    # down the tab. Banked ledger balance (if any) uses a bigger "header"
    # block so it visually pops; this week's raw eligible-package text
    # stays a plain section underneath it.
    ledger_block = _bonus_ledger_block(driver, db)
    if ledger_block:
        blocks.extend(ledger_block)
        blocks.append({"type": "divider"})

    bonus = _driver_rescue_bonus_this_week(driver, db)
    if bonus:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"💰 *Rescue Bonus This Week:* ${bonus['bonus']} ({bonus['packages']} eligible packages)",
            },
        })
        blocks.append({"type": "divider"})

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

    countdown = _driver_return_countdown(driver, db)
    if countdown:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⏱️ *Est. Return In:* {countdown}"},
        })

    comms_block = _communication_buttons_block(driver, db)
    if comms_block:
        blocks.append(comms_block)

    blocks.append({"type": "divider"})

    from api.src.routes.eod_survey import _issue_eod_token
    eod_token = _issue_eod_token(driver.id, driver.position_id, driver.payroll_name)

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": "home_eod_survey_button",
                "text": {"type": "plain_text", "text": "🏁 End of Day Survey", "emoji": True},
                "style": "primary",
                "url": f"{FRONTEND_URL}/eod?token={eod_token}",
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
        blocks, _ = _build_combined_home_blocks(slack_user_id, db)
        client.views_publish(user_id=slack_user_id, view={"type": "home", "blocks": blocks})
    except Exception as exc:
        logger.warning("views_publish failed for %s: %s", slack_user_id, exc)


def _build_combined_home_blocks(slack_user_id: str, db: Session) -> tuple[list, dict]:
    """Additive, not exclusive — added 2026-07-27 per explicit user
    direction ("every employee should be able to see the driver
    dashboard, no filters"). Dispatch/HR tools used to REPLACE the driver
    dashboard entirely for anyone with either role, which broke for
    people who are both real drivers AND dispatch/HR staff (e.g. Krista,
    in both #nday-mgt and #nday-hr) — they lost access to their own EOD
    survey/callout button. Every applicable section is appended onto one
    combined view instead of picking only one. Shared by _publish_home()
    and the debug-publish-home diagnostic below so they can't drift out
    of sync with each other the way the old duplicated branch logic did.
    Returns (blocks, debug_info) — debug_info records which sections got
    included, for the diagnostic endpoint."""
    blocks: list = []
    info: dict = {"sections": []}

    if is_dispatch_staff(slack_user_id, db):
        from api.src.routes.slack_dispatch_home import build_dispatch_home_view_blocks
        blocks += build_dispatch_home_view_blocks(db)
        blocks.append({"type": "divider"})
        info["sections"].append("dispatch_staff")

    if is_hr_staff(slack_user_id, db):
        from api.src.routes.slack_hr_home import build_hr_home_view_blocks
        blocks += build_hr_home_view_blocks(db)
        blocks.append({"type": "divider"})
        info["sections"].append("hr_staff")

    # Driver dashboard — always included, no role filter. Still gated by
    # DRIVER_DM_ACTIVE (a different, unrelated flag about whether it's
    # safe to show/DM real driver content at all).
    if not _DM_ACTIVE:
        blocks += _INACTIVE_BLOCKS
        info["sections"].append("dm_inactive")
    else:
        driver = _resolve_driver(slack_user_id, db)
        info["resolved_driver"] = driver.payroll_name if driver else None
        blocks += build_home_view_blocks(driver, db)
        info["sections"].append("driver")

    return blocks, info


@router.get("/debug-publish-home")
async def debug_publish_home(slack_user_id: str, db: Session = Depends(get_db)) -> dict:
    """Read-only-ish diagnostic (does actually call views_publish, same as
    the real thing) — added 2026-07-27 because _publish_home() swallows
    every exception into a log line we can't see without Render log
    access, and coordinating "have them open it right now, then paste
    logs" was too slow/unreliable. Runs the exact same branch logic and
    returns the real exception + traceback directly in the response."""
    import traceback

    result = {"slack_user_id": slack_user_id}
    client = _client()
    if not client:
        result["error"] = "No SLACK_BOT_TOKEN configured"
        return result

    try:
        blocks, info = _build_combined_home_blocks(slack_user_id, db)
        result.update(info)
        result["block_count"] = len(blocks)
        resp = client.views_publish(user_id=slack_user_id, view={"type": "home", "blocks": blocks})
        result["status"] = "published"
        result["slack_ok"] = resp.get("ok")
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
    return result


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


_REPORT_TITLES = {"injury": "Report an Injury", "incident": "Incident Report"}
_REPORT_TYPE_BY_ACTION = {
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

    doc_type = {"injury": "injury_report"}.get(report_type, "incident_report")
    client = _client()
    if client:
        recipients = resolve_recipients(doc_type, db)
        all_ids = sorted({sid for ids in recipients.values() for sid in ids})
        text = f"🚨 *{report_type.title()} report* from *{driver_name}*\n> {description}"
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
