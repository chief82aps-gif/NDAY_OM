"""
Feature Flags — DB-backed overrides for `_ACTIVE` gates, added 2026-07-31.

Every new automated Slack-send/driver-facing feature in this app ships
fully built but off by default, gated by an `_ACTIVE` env var — a
deliberate safety pattern (deploy and enable are separate steps; a bad
feature can be killed instantly without a code revert). That pattern
was working but required hand-editing Render's environment variables
for every single toggle, with no single place to see or change them all.

get_flag(key) is now the single source of truth for every one of these
gates: a FeatureFlag DB row (if one exists) always wins; otherwise it
falls back to the env var (or that env var's own coded default) exactly
as before. This means migrating a call site from raw os.getenv(...) to
get_flag(...) can never change behavior for a flag that's never been
touched via the admin page (/feature-flags) -- it only adds the option
to override live, without a redeploy/restart.

FLAG_REGISTRY is the catalog the admin page renders -- every flag this
app defines, whether or not it's ever been toggled via the DB yet.
Keep it in sync when adding a new `_ACTIVE` flag anywhere in the code.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.orm import Session

from api.src.database import SessionLocal, FeatureFlag


FLAG_REGISTRY: dict[str, dict] = {
    "DRIVER_DM_ACTIVE": {
        "label": "Driver Morning DM",
        "description": "Master gate for the daily route-assignment DM sent to drivers.",
        "category": "Driver Communication",
    },
    "DM_COACHING_HIGHLIGHTS_ACTIVE": {
        "label": "Coaching Highlights in Morning DM",
        "description": "Adds 'Doing great on / Room to grow' to the morning driver DM.",
        "category": "Driver Communication",
    },
    "TEAM_ROOM_MESSAGES_ACTIVE": {
        "label": "Team Room Messages",
        "description": "Rostering-related team room posts.",
        "category": "Driver Communication",
    },
    "LEAD_ROUTING_ACTIVE": {
        "label": "Talk to My Lead Routing",
        "description": "Replaces the generic Zello text in driver DMs with a live 'Talk to My Lead' button.",
        "category": "Driver Communication",
    },
    "ROSTERING_ACTIVE": {
        "label": "Assignment Matrix Posting",
        "description": "Posts the daily assignment matrix to #nday-mgt.",
        "category": "Rostering",
    },
    "SCHEDULE_ESCALATION_ACTIVE": {
        "label": "Schedule Escalation",
        "description": "Escalation nudges for missing/incomplete schedule data.",
        "category": "Rostering",
    },
    "WAVE_COMPETITION_ACTIVE": {
        "label": "Wave Team Standings",
        "description": "Daily friendly-competition standings message across wave teams.",
        "category": "Wave Lead",
    },
    "WAVE_PTT_CHANNELS_ACTIVE": {
        "label": "Wave PTT Channels",
        "description": "Auto-creates/syncs per-wave Slack channels for voice-clip PTT.",
        "category": "Wave Lead",
    },
    "SAFETY_VIOLATION_REVIEW_ACTIVE": {
        "label": "Safety Violation Review",
        "description": "Confirm/False-Flag review workflow for Netradyne safety events.",
        "category": "Safety",
    },
    "SAFETY_VIOLATION_VIDEO_ACTIVE": {
        "label": "Safety Violation Training Video Gate",
        "description": "Requires watching a training video before acknowledging a confirmed safety violation. No video exists yet -- do not enable.",
        "category": "Safety",
    },
    "DVIC_TRAINING_VIDEO_ACTIVE": {
        "label": "DVIC Training Video Gate",
        "description": "Requires watching a training video before acknowledging repeat DVIC violations.",
        "category": "Safety",
    },
    "NDAY_POINTS_ACTIVE": {
        "label": "NDAY Points Awarding",
        "description": "Auto-awards perfect-safety-day points to the NDAY Points ledger.",
        "category": "NDAY Points / Swag Store",
    },
    "NDAY_POINTS_CASH_OUT_ACTIVE": {
        "label": "NDAY Points Cash-Out",
        "description": "Allows converting an NDAY Points balance to a cash-out request. Needs legal review before enabling -- do not enable without that sign-off.",
        "category": "NDAY Points / Swag Store",
    },
    "CALLOUT_SUMMARY_ACTIVE": {
        "label": "Callout Summary",
        "description": "Recurring mid-morning callout digest to #nday-mgt and #nday-hr.",
        "category": "Attendance",
    },
    "MISSED_EOD_GATE_ACTIVE": {
        "label": "Missed EOD Gate",
        "description": "Gates outstanding-items follow-up for a missed EOD survey.",
        "category": "EOD Survey",
    },
    "EOD_CATEGORY_DIGEST_ACTIVE": {
        "label": "EOD Category Digest",
        "description": "Daily digest of EOD survey issues (van/injury/incident flags).",
        "category": "EOD Survey",
    },
    "SENTIMENT_SURVEY_ACTIVE": {
        "label": "Sentiment Survey Daily AI Summary",
        "description": "Daily job that analyzes free-text sentiment responses and posts an anonymized summary to owner/HR.",
        "category": "Sentiment Survey",
    },
    "SENTIMENT_SURVEY_DM_HINTS_ACTIVE": {
        "label": "Sentiment Survey DM Hints",
        "description": "Adds a 'Share Feedback' hint/button to morning DMs and the Home tab.",
        "category": "Sentiment Survey",
    },
    "SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE": {
        "label": "Sentiment Survey Monthly Push",
        "description": "Sends the sentiment survey once a month, ahead of Amazon's own survey window.",
        "category": "Sentiment Survey",
    },
    "SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE": {
        "label": "Sentiment Survey Weekly Summary",
        "description": "Posts a weekly stats + AI-flagged-themes summary to #nday-hr.",
        "category": "Sentiment Survey",
    },
    "OKAMI_FINALIZE_REMINDER_ACTIVE": {
        "label": "Okami Finalize Reminder",
        "description": "Nags #nday-mgt if today's Okami capacity numbers aren't finalized by 5 PM.",
        "category": "Okami Capacity",
    },
    "TIMECARD_REPORT_NUDGE_ACTIVE": {
        "label": "Timecard Report Nudge",
        "description": "10 AM nudge for HR to post the daily timecard audit report.",
        "category": "Reminders",
    },
    "ECP_SCREENSHOT_REMINDER_ACTIVE": {
        "label": "ECP Screenshot Reminder",
        "description": "Once Amazon's ECP message lands, asks #nday-mgt for the Scheduling-page screenshot.",
        "category": "Reminders",
    },
    "COACHING_NOTIFICATIONS_ACTIVE": {
        "label": "Coaching Notifications",
        "description": "Scans for Amazon's weekly Coaching Notifications digest (forwarded into Slack) and runs the driver-ack -> ops_manager -> HR approval chain.",
        "category": "Coaching Notifications",
    },
    "RESCUE_PAYROLL_REPORT_ACTIVE": {
        "label": "Rescue Payroll Report",
        "description": "Weekly rescue bonus payroll report send.",
        "category": "Rescue Tracker",
    },
    "ASSOCIATE_DATA_REMINDER_ACTIVE": {
        "label": "Associate Data Reminder",
        "description": "Reminds staff to complete missing driver associate data.",
        "category": "Drivers",
    },
    "UNLINKED_DRIVER_ALERT_ACTIVE": {
        "label": "Unlinked Driver Alert",
        "description": "Alerts on drivers not yet linked to a Slack account.",
        "category": "Drivers",
    },
    "WEEKLY_SLACK_RELINK_ACTIVE": {
        "label": "Weekly Slack Relink",
        "description": "Weekly job to re-attempt linking drivers to their Slack identity.",
        "category": "Drivers",
    },
    "WEBSITE_USER_SYNC_ACTIVE": {
        "label": "Website User Sync",
        "description": "Syncs website dashboard access from #nday-mgt/#nday-hr Slack channel membership.",
        "category": "Access",
    },
    "MISROUTED_FILE_WATCH_ACTIVE": {
        "label": "Misrouted File Watch",
        "description": "Alerts #nday-mgt when an ingest-looking file lands in the wrong Slack channel.",
        "category": "Ingest",
    },
    "OPS_AUTO_INGEST_ACTIVE": {
        "label": "Ops Auto-Ingest",
        "description": "Automatically processes detected ingest files instead of requiring manual confirmation. On by default.",
        "category": "Ingest",
    },
    "DAILY_FALLBACK_PIN_ACTIVE": {
        "label": "Daily Fallback PIN",
        "description": "Generates one shared PIN per day, posted to #nday-mgt, accepted alongside (not instead of) each driver's own PIN everywhere it's checked.",
        "category": "Attendance",
    },
}


def get_flag(key: str, db: Optional[Session] = None) -> bool:
    """Live-checked on every call (never cached at import time) so a
    toggle from the admin page takes effect immediately, no redeploy or
    restart needed. Falls back to the env var (and that env var's own
    coded default) when no DB override row exists yet."""
    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        row = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
        if row is not None:
            return row.enabled
    finally:
        if owns_session:
            db.close()

    default = "true" if key == "OPS_AUTO_INGEST_ACTIVE" else "false"
    return os.getenv(key, default).lower() == "true"


def set_flag(key: str, enabled: bool, updated_by: str, db: Session) -> FeatureFlag:
    row = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
    if row:
        row.enabled = enabled
        row.updated_by = updated_by
    else:
        row = FeatureFlag(key=key, enabled=enabled, updated_by=updated_by)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_flags(db: Session) -> list[dict]:
    """Every registered flag, merged with its current DB override (if
    any) and effective value -- what the admin page renders."""
    overrides = {row.key: row for row in db.query(FeatureFlag).all()}
    results = []
    for key, meta in FLAG_REGISTRY.items():
        override = overrides.get(key)
        default = "true" if key == "OPS_AUTO_INGEST_ACTIVE" else "false"
        env_value = os.getenv(key, default).lower() == "true"
        results.append({
            "key": key,
            "label": meta["label"],
            "description": meta["description"],
            "category": meta["category"],
            "enabled": override.enabled if override else env_value,
            "source": "database" if override else "env_default",
            "updated_at": override.updated_at.isoformat() if override and override.updated_at else None,
            "updated_by": override.updated_by if override else None,
        })
    results.sort(key=lambda r: (r["category"], r["label"]))
    return results
