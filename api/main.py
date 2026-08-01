import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Load .env from repo root if present (local dev only — production uses host env vars)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.src.routes import uploads, auth, audit, enhanced_audit, weekly_audit, weekly_audit_upload, rescue
from api.src.routes import daily_notify, quality, attendance, attendance_reports, ops_ingest, dvic, dsp_scorecard_weekly, eod_survey, route_assignment, slack_interactions, slack_home, manager_accountability
from api.src.routes import rostering, cortex_tracking, adp, rts, mgt_reminders, document_routing, crash_report, drivers, candidates, safety_events, okami_capacity, driver_scoring, route_bands, driver_lead_schedule, injury_report, sentiment_survey, wave_lead, glitch_reports, daily_quality, nday_points, feature_flags, coaching_notifications
from api.src.routes.daily_notify import check_and_notify, check_ecp_and_prompt
from api.src.routes.rostering import send_nightly_roster_reminder, send_wave_lead_pre_wave_dm, send_missing_drivers_summary
from api.src.schedule_config import SCHEDULE_GAP_CHECK_HOUR
from api.src.database import Base, engine, SessionLocal, ensure_dop_driver_name_column, ensure_ssn_last4_column, ensure_callout_signature_column, ensure_assignment_board_columns, _ensure_manager_signature_columns, _ensure_position_id_nullable, ensure_driver_shift_dm_checklist_columns, ensure_route_duration_columns, ensure_dvic_raw_fields_column, ensure_driver_roster_tracking_columns, ensure_daily_route_assignment_unique_index, ensure_okami_capacity_finalize_columns, ensure_crash_report_evidence_columns, ensure_daily_route_assignment_notified_snapshot_column, ensure_user_auth_columns, ensure_route_sheet_load_size_columns, ensure_driver_shift_dm_decline_column, ensure_driver_shift_dm_callout_column, ensure_dvic_manager_signature_columns, ensure_eod_crash_columns, ensure_driver_roster_preferred_name_column, ensure_driver_identity_roster_id_columns, ensure_dvic_video_watch_column, ensure_daily_route_assignment_pending_ack_column, ensure_safety_event_review_columns, ensure_dvic_video_started_column, ensure_dvic_escalation_columns, ensure_dvic_violation_instance_columns, ensure_rescue_bonus_ledger_columns, ensure_wave_lead_role_half_column, ensure_sentiment_survey_rating_columns, ensure_sentiment_survey_response_columns, ensure_attendance_reason_valid_column
from api.src.slack_notification_gate import apply_slack_send_gate

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")

# Applied at import time — before any request or background task can
# construct a Slack client and send. See api/src/slack_notification_gate.py.
apply_slack_send_gate()

app = FastAPI()


async def _daily_notify_loop():
    """Poll Slack for today's files every 10 min between 7:00–10:00 AM
    Pacific (widened from 8:00 on 2026-07-24, per explicit direction — a
    10-min interval is fine for the whole window, including 7:00-8:30)."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            if 7 <= now.hour < 10:
                db = SessionLocal()
                try:
                    await asyncio.to_thread(check_and_notify, db)
                except Exception as exc:
                    logger.warning("Daily notify poll error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("Daily notify loop error: %s", exc)
        await asyncio.sleep(600)  # 10 minutes


async def _dvic_reminder_loop():
    """Every 60 s — delegates to dvic.run_dvic_upload_reminder() which handles its own 3-6 PM throttle."""
    while True:
        try:
            await asyncio.to_thread(dvic.run_dvic_upload_reminder)
        except Exception as exc:
            logger.warning("DVIC reminder loop error: %s", exc)
        await asyncio.sleep(60)


async def _coaching_notifications_scan_loop():
    """Every 10 min — delegates to coaching_notifications.scan_for_coaching_notifications(),
    which no-ops entirely unless COACHING_NOTIFICATIONS_ACTIVE is on."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(coaching_notifications.scan_for_coaching_notifications, db)
            except Exception as exc:
                logger.warning("Coaching notifications scan loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Coaching notifications scan loop outer error: %s", exc)
        await asyncio.sleep(600)


async def _eod_survey_loop():
    """Every 60 s — checks whether it's the 1900 Pacific mass-send hour and
    fires the EOD survey to every not-yet-submitted scheduled driver if so
    (see run_eod_survey_check()'s docstring in eod_survey.py)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(eod_survey.run_eod_survey_check, db)
            except Exception as exc:
                logger.warning("EOD survey check error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("EOD survey loop error: %s", exc)
        await asyncio.sleep(60)


async def _rescue_payroll_hr_report_loop():
    """Every 60 s — delegates to rescue.send_weekly_hr_report(), which
    no-ops on any day but Sunday and respects an "already sent today"
    guard. Gated by RESCUE_PAYROLL_REPORT_ACTIVE (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(rescue.send_weekly_hr_report, db)
            except Exception as exc:
                logger.warning("Rescue payroll HR report loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Rescue payroll HR report loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _wave_competition_standings_loop():
    """Every 60 s — delegates to wave_lead.send_wave_competition_standings(),
    which no-ops outside the 7 AM Pacific send hour and respects an
    "already sent today" guard. Gated by WAVE_COMPETITION_ACTIVE (default
    false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(wave_lead.send_wave_competition_standings, db)
            except Exception as exc:
                logger.warning("Wave competition standings loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Wave competition standings loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _wave_channel_sync_loop():
    """Every 60 s, active 6-11 AM Pacific — delegates to
    wave_lead.sync_wave_channels(), which no-ops outside that window and
    outside WAVE_PTT_CHANNELS_ACTIVE (default false). Safe to re-run
    repeatedly (idempotent membership sync, not a one-shot message send),
    so this just keeps correcting drift through the morning as rosters
    get finalized, rather than a strict single daily fire."""
    while True:
        try:
            now_pt = datetime.now(PACIFIC)
            if 6 <= now_pt.hour < 11:
                db = SessionLocal()
                try:
                    await asyncio.to_thread(wave_lead.sync_wave_channels, db)
                except Exception as exc:
                    logger.warning("Wave channel sync loop error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("Wave channel sync loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _sentiment_survey_report_loop():
    """Every 60 s — delegates to sentiment_survey.send_daily_sentiment_report(),
    which no-ops outside the 21:00+ Pacific window and respects an
    "already sent today" guard. Gated by SENTIMENT_SURVEY_ACTIVE (default
    false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(sentiment_survey.send_daily_sentiment_report, db)
            except Exception as exc:
                logger.warning("Sentiment survey report loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Sentiment survey report loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _sentiment_survey_monthly_push_loop():
    """Every 60 s — delegates to sentiment_survey.run_monthly_sentiment_survey_push(),
    which no-ops outside the target Sunday (the last full week of the
    month) and respects an "already sent this month" guard. Gated by
    SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(sentiment_survey.run_monthly_sentiment_survey_push, db)
            except Exception as exc:
                logger.warning("Sentiment survey monthly push loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Sentiment survey monthly push loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _sentiment_survey_weekly_summary_loop():
    """Every 60 s — delegates to sentiment_survey.send_weekly_sentiment_summary(),
    which no-ops outside Monday 8 AM Pacific and respects an "already sent
    this week" guard. Gated by SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(sentiment_survey.send_weekly_sentiment_summary, db)
            except Exception as exc:
                logger.warning("Sentiment survey weekly summary loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Sentiment survey weekly summary loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _website_user_sync_loop():
    """Every 60 s — delegates to auth.run_website_user_sync(), which
    throttles itself to once an hour. Gated by WEBSITE_USER_SYNC_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(auth.run_website_user_sync, db)
            except Exception as exc:
                logger.warning("Website user sync loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Website user sync loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _safety_violation_review_loop():
    """Every 60 s — delegates to safety_events.post_pending_safety_violations(),
    which posts a Confirm/False-Flag review message for every not-yet-posted
    SafetyEvent (whether from CSV ingest or manual entry) to the dedicated
    safety events channel. Previously only ran on manual trigger — added
    2026-07-27 per explicit user direction that validation requests must go
    out automatically, not wait for someone to remember to hit the endpoint.
    Gated by SAFETY_VIOLATION_REVIEW_ACTIVE (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(safety_events.post_pending_safety_violations, db)
            except Exception as exc:
                logger.warning("Safety violation review loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Safety violation review loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _eod_category_digest_loop():
    """Every 60 s — delegates to eod_survey.send_daily_eod_category_digests(),
    which no-ops outside the 22:15+ Pacific window and respects an
    "already sent today" guard. Gated by EOD_CATEGORY_DIGEST_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(eod_survey.send_daily_eod_category_digests, db)
            except Exception as exc:
                logger.warning("EOD category digest loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("EOD category digest loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _weekly_slack_relink_loop():
    """Every 60 s — delegates to drivers.run_weekly_slack_relink(), which
    no-ops on any day but Monday and respects an "already ran today"
    guard. Gated by WEEKLY_SLACK_RELINK_ACTIVE (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(drivers.run_weekly_slack_relink, db)
            except Exception as exc:
                logger.warning("Weekly Slack relink loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Weekly Slack relink loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _dsp_scorecard_reminder_loop():
    """Every 60 s — delegates to dsp_scorecard_weekly.run_dsp_scorecard_reminder() which handles Wednesday 12:30-5 PM throttle."""
    while True:
        try:
            await asyncio.to_thread(dsp_scorecard_weekly.run_dsp_scorecard_reminder)
        except Exception as exc:
            logger.warning("DSP scorecard reminder loop error: %s", exc)
        await asyncio.sleep(60)


async def _mgt_reminders_loop():
    """Every 60 s — delegates to mgt_reminders.run_mgt_reminders_check(), which
    DMs every #nday-mgt member when Cortex (9 AM), Fleet (9 AM), Okami capacity
    forecast (3:30 PM), or the driver schedule (7:30 PM) hasn't posted yet."""
    while True:
        try:
            await asyncio.to_thread(mgt_reminders.run_mgt_reminders_check)
        except Exception as exc:
            logger.warning("Mgt reminders loop error: %s", exc)
        await asyncio.sleep(60)


async def _timecard_report_nudge_loop():
    """Every 60 s — delegates to mgt_reminders.run_timecard_report_nudge(),
    which no-ops outside TIMECARD_REPORT_NUDGE_HOUR and respects an
    "already sent today" guard. Gated by TIMECARD_REPORT_NUDGE_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(mgt_reminders.run_timecard_report_nudge, db)
            except Exception as exc:
                logger.warning("Timecard report nudge loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Timecard report nudge loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _daily_fallback_pin_loop():
    """Every 60 s — delegates to mgt_reminders.run_daily_fallback_pin_post(),
    which no-ops outside DAILY_FALLBACK_PIN_POST_HOUR and respects an
    "already sent today" guard. Gated by DAILY_FALLBACK_PIN_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(mgt_reminders.run_daily_fallback_pin_post, db)
            except Exception as exc:
                logger.warning("Daily fallback PIN loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Daily fallback PIN loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _ecp_screenshot_reminder_loop():
    """Every 60 s — delegates to mgt_reminders.run_ecp_screenshot_reminder(),
    which no-ops before 5 PM Pacific, scans #dlv3-nday-info for Amazon's ECP
    message, and posts to #nday-mgt asking for the Scheduling-page
    screenshot once seen. Gated by ECP_SCREENSHOT_REMINDER_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(mgt_reminders.run_ecp_screenshot_reminder, db)
            except Exception as exc:
                logger.warning("ECP screenshot reminder loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("ECP screenshot reminder loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _okami_finalize_reminder_loop():
    """Every 60 s — delegates to okami_capacity.run_okami_finalize_reminder(),
    which nags #nday-mgt every 5 min from 3:30 PM until Okami is finalized
    (hard deadline 5 PM / ECP). Gated by OKAMI_FINALIZE_REMINDER_ACTIVE
    (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(okami_capacity.run_okami_finalize_reminder, db)
            except Exception as exc:
                logger.warning("Okami finalize reminder loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Okami finalize reminder loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _associate_data_reminder_loop():
    """Every 60 s — delegates to drivers.run_associate_data_reminder(),
    which nags #nday-mgt weekly if the Amazon associate roster export
    hasn't been re-uploaded via /drivers/import-ssn-slack. Gated by
    ASSOCIATE_DATA_REMINDER_ACTIVE (default false)."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(drivers.run_associate_data_reminder, db)
            except Exception as exc:
                logger.warning("Associate Data reminder loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Associate Data reminder loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _dm_response_summary_loop():
    """Every 30 min — delegates to rostering.refresh_all_dm_response_summaries(),
    which rebuilds ONE consolidated #nday-mgt message per shift_date for
    Showtime responses (green=acked/yellow=no reply/red=declined) and
    Route Assignment responses (green=arrived/yellow=not yet/red=called
    out), instead of a new message per individual driver event."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(rostering.refresh_all_dm_response_summaries, db)
            except Exception as exc:
                logger.warning("DM response summary loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("DM response summary loop outer error: %s", exc)
        await asyncio.sleep(1800)


async def _misrouted_file_watch_loop():
    """Every 60 s — delegates to ops_ingest.run_misrouted_file_watch(),
    which only actually scans every 15 min (own DB-backed throttle). Checks
    every Slack channel the bot can see other than #nday-operations-management
    and #dlv3-nday-info for a file that looks like it should have landed
    there instead, and alerts #nday-mgt if so. Gated by
    MISROUTED_FILE_WATCH_ACTIVE (default false)."""
    while True:
        try:
            await asyncio.to_thread(ops_ingest.run_misrouted_file_watch)
        except Exception as exc:
            logger.warning("Misrouted-file watch loop error: %s", exc)
        await asyncio.sleep(60)


async def _schedule_escalation_loop():
    """Every 60 s — delegates to rostering.run_schedule_escalation_check(),
    which posts an escalating #nday-mgt nag once tomorrow's driver schedule
    is overdue: 15-min cadence 19:00-20:00 PT, then 5-min with no upper
    bound until it lands. Gated by SCHEDULE_ESCALATION_ACTIVE=true."""
    while True:
        try:
            db = SessionLocal()
            try:
                await asyncio.to_thread(rostering.run_schedule_escalation_check, db)
            except Exception as exc:
                logger.warning("Schedule escalation loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Schedule escalation loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _schedule_gap_alert_loop():
    """Fires once around SCHEDULE_GAP_CHECK_HOUR PT (default 21:00) — posts
    a soft 'potential gap' #nday-mgt alert for tomorrow's unconfirmed
    drivers. No automated callout — soft v1 per explicit direction.
    Gated by SCHEDULE_ESCALATION_ACTIVE=true."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            if now.hour == SCHEDULE_GAP_CHECK_HOUR and now.minute < 10:
                from datetime import timedelta
                tomorrow = now.date() + timedelta(days=1)
                db = SessionLocal()
                try:
                    await asyncio.to_thread(rostering.send_schedule_gap_alert, tomorrow, db)
                except Exception as exc:
                    logger.warning("Schedule gap alert loop error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("Schedule gap alert loop outer error: %s", exc)
        await asyncio.sleep(600)


async def _callout_queue_loop():
    """8:30 AM Pacific — send morning digest of normal callouts.
    Every 15 min — re-notify #nday-mgt for unacknowledged tight-roster callouts."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            db = SessionLocal()
            try:
                # Morning digest at 8:30 AM ± 7 minutes
                if now.hour == 8 and 23 <= now.minute <= 37:
                    from api.src.routes.attendance import send_morning_callout_digest
                    count = await asyncio.to_thread(send_morning_callout_digest, now.date(), db)
                    if count:
                        logger.info("Morning callout digest sent: %d callout(s)", count)

                # Tight-roster reminders every 15 min
                from api.src.routes.attendance import send_tight_roster_reminders
                sent = await asyncio.to_thread(send_tight_roster_reminders, db)
                if sent:
                    logger.info("Tight-roster reminders sent: %d", sent)

                # Recurring callout summary, 9:30 AM - 12:30 PM, every 15 min
                # (same cadence as this loop already runs on) -- gated by
                # CALLOUT_SUMMARY_ACTIVE, default false.
                from api.src.routes.attendance import send_recurring_callout_summary
                await asyncio.to_thread(send_recurring_callout_summary, db)
            except Exception as exc:
                logger.warning("Callout queue loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Callout queue loop outer error: %s", exc)
        await asyncio.sleep(900)  # 15 minutes


async def _ops_ingest_scan_loop():
    """Scan #nday-operations-management every 60 s for new file uploads."""
    while True:
        try:
            db = SessionLocal()
            try:
                new_files = await asyncio.to_thread(ops_ingest.scan_ops_channel, db)
                if new_files:
                    logger.info("Ops ingest scan: queued %d new file(s): %s", len(new_files), new_files)
            except Exception as exc:
                logger.warning("Ops ingest scan error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Ops ingest scan loop error: %s", exc)
        await asyncio.sleep(60)


async def _ops_auto_ingest_loop():
    """Every 60 s — ingest any pending OpsIngestJob of a type safe to run
    unattended (dvic/driver_schedule/fleet/quality_csv/safety_events/
    dsp_scorecard — see ops_ingest.py's module docstring for what's
    excluded and why). Gated by OPS_AUTO_INGEST_ACTIVE (default true)."""
    while True:
        try:
            db = SessionLocal()
            try:
                result = await asyncio.to_thread(ops_ingest.run_ops_auto_ingest, db)
                if result.get("ingested"):
                    logger.info("Ops auto-ingest: %s", result)
            except Exception as exc:
                logger.warning("Ops auto-ingest loop error: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Ops auto-ingest loop outer error: %s", exc)
        await asyncio.sleep(60)


async def _ecp_watch_loop():
    """Poll #dlv3-nday-info every 15 min from 6 PM–midnight Pacific for the ECP roster message.
    NOTE: ECP channel source is being updated — see daily_notify.py NOTIFY_CHANNEL."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            if 18 <= now.hour < 24:
                db = SessionLocal()
                try:
                    await asyncio.to_thread(check_ecp_and_prompt, db)
                except Exception as exc:
                    logger.warning("ECP watch poll error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("ECP watch loop error: %s", exc)
        await asyncio.sleep(900)  # 15 minutes


async def _nightly_roster_reminder_loop():
    """Fire at 19:00 PT daily — DM Spencer/Luis/Fabian with suggested roster for tomorrow.
    Gated by ROSTERING_ACTIVE=true env var (default off)."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            if now.hour == 19 and now.minute < 10:
                from datetime import timedelta
                tomorrow = now.date() + timedelta(days=1)
                db = SessionLocal()
                try:
                    await asyncio.to_thread(send_nightly_roster_reminder, tomorrow, db)
                except Exception as exc:
                    logger.warning("Nightly roster reminder error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("Nightly roster reminder loop error: %s", exc)
        await asyncio.sleep(600)  # check every 10 min


async def _wave_lead_watcher_loop():
    """
    Runs every 60 s. For each unique wave time today:
      - Fires pre-wave DM to wave lead when now >= wave_time - 10 min (deduped)
      - Fires missing-drivers summary when now >= wave_time (deduped)
    Gated by ROSTERING_ACTIVE=true.
    """
    import os as _os
    while True:
        try:
            if _os.getenv("ROSTERING_ACTIVE", "false").lower() == "true":
                now_pt = datetime.now(PACIFIC)
                today = now_pt.date()
                db = SessionLocal()
                try:
                    from api.src.database import DailyRouteAssignment
                    from sqlalchemy import distinct
                    wave_times = [
                        r[0] for r in
                        db.query(DailyRouteAssignment.wave)
                        .filter(
                            DailyRouteAssignment.assignment_date == today,
                            DailyRouteAssignment.wave != None,
                            DailyRouteAssignment.wave != "",
                        )
                        .distinct()
                        .all()
                    ]
                    for wave_str in wave_times:
                        # Parse wave time into today's datetime (Pacific)
                        wave_dt = None
                        for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
                            try:
                                parsed = datetime.strptime(wave_str.strip(), fmt)
                                wave_dt = now_pt.replace(
                                    hour=parsed.hour, minute=parsed.minute,
                                    second=0, microsecond=0
                                )
                                break
                            except ValueError:
                                continue
                        if not wave_dt:
                            continue

                        minutes_to_wave = (wave_dt - now_pt).total_seconds() / 60

                        # Pre-wave briefing: between 12 and 9 minutes before wave
                        if -12 <= minutes_to_wave <= -9:
                            await asyncio.to_thread(
                                send_wave_lead_pre_wave_dm, today, wave_str, db
                            )

                        # Missing summary: between 0 and 5 minutes after wave
                        if -5 <= minutes_to_wave <= 0:
                            await asyncio.to_thread(
                                send_missing_drivers_summary, today, wave_str, db
                            )
                except Exception as exc:
                    logger.warning("Wave lead watcher poll error: %s", exc)
                finally:
                    db.close()
        except Exception as exc:
            logger.warning("Wave lead watcher loop error: %s", exc)
        await asyncio.sleep(60)  # every minute


async def _grounded_van_watcher_loop():
    """Poll #nday-team-room every 30 min for grounded-van mentions.
    Gated by ROSTERING_ACTIVE=true."""
    import os as _os
    TEAM_CHANNEL = _os.getenv("SLACK_TEAM_CHANNEL", "C0BAQAYKANS")
    while True:
        try:
            if _os.getenv("ROSTERING_ACTIVE", "false").lower() == "true":
                token = _os.getenv("SLACK_BOT_TOKEN", "")
                if token:
                    from slack_sdk import WebClient
                    client = WebClient(token=token)
                    try:
                        resp = client.conversations_history(channel=TEAM_CHANNEL, limit=50)
                        grounded: list[str] = []
                        for msg in resp.get("messages", []):
                            text = (msg.get("text") or "").lower()
                            if "grounded" in text:
                                grounded.append(msg.get("text", "")[:120])
                        if grounded:
                            logger.info("Grounded van mentions in #nday-team-room: %s", grounded)
                    except Exception as exc:
                        logger.warning("Grounded van watcher poll error: %s", exc)
        except Exception as exc:
            logger.warning("Grounded van watcher loop error: %s", exc)
        await asyncio.sleep(1800)  # 30 minutes


# Create all tables on startup
@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    ensure_dop_driver_name_column()
    ensure_ssn_last4_column()
    ensure_callout_signature_column()
    ensure_assignment_board_columns()
    _ensure_manager_signature_columns()
    _ensure_position_id_nullable()
    ensure_route_duration_columns()
    ensure_route_sheet_load_size_columns()
    ensure_dvic_raw_fields_column()
    ensure_driver_roster_tracking_columns()
    ensure_daily_route_assignment_unique_index()
    ensure_okami_capacity_finalize_columns()
    ensure_crash_report_evidence_columns()
    ensure_daily_route_assignment_notified_snapshot_column()
    ensure_driver_shift_dm_decline_column()
    ensure_driver_shift_dm_callout_column()
    ensure_dvic_manager_signature_columns()
    ensure_eod_crash_columns()
    ensure_driver_roster_preferred_name_column()
    ensure_driver_identity_roster_id_columns()
    ensure_dvic_video_watch_column()
    ensure_dvic_video_started_column()
    ensure_dvic_violation_instance_columns()
    ensure_dvic_escalation_columns()
    ensure_daily_route_assignment_pending_ack_column()
    ensure_safety_event_review_columns()
    ensure_user_auth_columns()
    ensure_rescue_bonus_ledger_columns()
    ensure_wave_lead_role_half_column()
    ensure_sentiment_survey_rating_columns()
    ensure_sentiment_survey_response_columns()
    ensure_attendance_reason_valid_column()
    from api.src.routes.auth import seed_default_users, ensure_owner_slack_link
    from api.src.routes.wave_lead import seed_wave_teams
    _seed_db = SessionLocal()
    try:
        seed_default_users(_seed_db)
        ensure_owner_slack_link(_seed_db)
        seed_wave_teams(_seed_db)
    finally:
        _seed_db.close()
    asyncio.create_task(_daily_notify_loop())
    asyncio.create_task(_ecp_watch_loop())
    asyncio.create_task(_ops_ingest_scan_loop())
    asyncio.create_task(_ops_auto_ingest_loop())
    asyncio.create_task(_dvic_reminder_loop())
    asyncio.create_task(_coaching_notifications_scan_loop())
    asyncio.create_task(_dsp_scorecard_reminder_loop())
    asyncio.create_task(_eod_survey_loop())
    asyncio.create_task(manager_accountability.manager_accountability_loop())
    asyncio.create_task(_callout_queue_loop())
    asyncio.create_task(_wave_competition_standings_loop())
    asyncio.create_task(_wave_channel_sync_loop())
    ensure_driver_shift_dm_checklist_columns()
    asyncio.create_task(_nightly_roster_reminder_loop())
    asyncio.create_task(_grounded_van_watcher_loop())
    asyncio.create_task(_wave_lead_watcher_loop())
    asyncio.create_task(_mgt_reminders_loop())
    asyncio.create_task(_timecard_report_nudge_loop())
    asyncio.create_task(_daily_fallback_pin_loop())
    asyncio.create_task(_ecp_screenshot_reminder_loop())
    asyncio.create_task(_okami_finalize_reminder_loop())
    asyncio.create_task(_associate_data_reminder_loop())
    asyncio.create_task(_dm_response_summary_loop())
    asyncio.create_task(_misrouted_file_watch_loop())
    asyncio.create_task(_schedule_escalation_loop())
    asyncio.create_task(_schedule_gap_alert_loop())
    asyncio.create_task(_rescue_payroll_hr_report_loop())
    asyncio.create_task(_sentiment_survey_report_loop())
    asyncio.create_task(_sentiment_survey_monthly_push_loop())
    asyncio.create_task(_sentiment_survey_weekly_summary_loop())
    asyncio.create_task(_weekly_slack_relink_loop())
    asyncio.create_task(_eod_category_digest_loop())
    asyncio.create_task(_safety_violation_review_loop())
    asyncio.create_task(_website_user_sync_loop())

cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_origins_env:
    cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    cors_origins = [
        "https://www.newdaylogisticsllc.com",
        "https://newdaylogisticsllc.com",
        "https://nday-om.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router, prefix="/upload")
app.include_router(auth.router, prefix="/auth")
app.include_router(audit.router, prefix="/audit")
app.include_router(enhanced_audit.router)
app.include_router(weekly_audit.router)
app.include_router(weekly_audit_upload.router)
app.include_router(rescue.router)
app.include_router(daily_notify.router)
app.include_router(quality.router)
app.include_router(attendance.router)
app.include_router(attendance_reports.router)
app.include_router(ops_ingest.router)
app.include_router(dvic.router)
app.include_router(dsp_scorecard_weekly.router)
app.include_router(eod_survey.router)
app.include_router(sentiment_survey.router)
app.include_router(route_assignment.router)
app.include_router(slack_interactions.router)
app.include_router(slack_home.router)
app.include_router(manager_accountability.router)
app.include_router(rostering.router)
app.include_router(cortex_tracking.router)
app.include_router(adp.router)
app.include_router(rts.router)
app.include_router(mgt_reminders.router)
app.include_router(document_routing.router)
app.include_router(crash_report.router)
app.include_router(injury_report.router)
app.include_router(drivers.router)
app.include_router(candidates.router)
app.include_router(safety_events.router)
app.include_router(driver_scoring.router)
app.include_router(okami_capacity.router)
app.include_router(route_bands.router)
app.include_router(driver_lead_schedule.router)
app.include_router(wave_lead.router)
app.include_router(glitch_reports.router)
app.include_router(daily_quality.router)
app.include_router(nday_points.router)
app.include_router(feature_flags.router)
app.include_router(coaching_notifications.router)

@app.get("/")
def root():
    return {"message": "NDAY_OM API is running."}
