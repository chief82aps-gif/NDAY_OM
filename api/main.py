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
from api.src.routes import daily_notify, quality, attendance, attendance_reports, ops_ingest, dvic, dsp_scorecard_weekly, eod_survey, route_assignment, slack_interactions, manager_accountability
from api.src.routes.daily_notify import check_and_notify, check_ecp_and_prompt
from api.src.database import Base, engine, SessionLocal, ensure_dop_driver_name_column, ensure_ssn_last4_column, ensure_callout_signature_column, ensure_assignment_board_columns, _ensure_manager_signature_columns, _ensure_position_id_nullable

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")

app = FastAPI()


async def _daily_notify_loop():
    """Poll Slack for today's files every 10 min between 8:00–10:00 AM Pacific."""
    while True:
        try:
            now = datetime.now(PACIFIC)
            if 8 <= now.hour < 10:
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


async def _eod_survey_loop():
    """Every 60 s — handles both the 3 PM daily channel post and the 7:30 PM DM reminders."""
    while True:
        try:
            await asyncio.to_thread(eod_survey.post_daily_survey_message)
            await asyncio.to_thread(eod_survey.send_eod_reminders)
        except Exception as exc:
            logger.warning("EOD survey loop error: %s", exc)
        await asyncio.sleep(60)


async def _dsp_scorecard_reminder_loop():
    """Every 60 s — delegates to dsp_scorecard_weekly.run_dsp_scorecard_reminder() which handles Wednesday 12:30-5 PM throttle."""
    while True:
        try:
            await asyncio.to_thread(dsp_scorecard_weekly.run_dsp_scorecard_reminder)
        except Exception as exc:
            logger.warning("DSP scorecard reminder loop error: %s", exc)
        await asyncio.sleep(60)


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


async def _ecp_watch_loop():
    """Poll #dlv3-nday-info every 15 min from 6 PM–midnight Pacific for the ECP roster message."""
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
    asyncio.create_task(_daily_notify_loop())
    asyncio.create_task(_ecp_watch_loop())
    asyncio.create_task(_ops_ingest_scan_loop())
    asyncio.create_task(_dvic_reminder_loop())
    asyncio.create_task(_dsp_scorecard_reminder_loop())
    asyncio.create_task(_eod_survey_loop())
    asyncio.create_task(manager_accountability.manager_accountability_loop())

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
app.include_router(route_assignment.router)
app.include_router(slack_interactions.router)
app.include_router(manager_accountability.router)

@app.get("/")
def root():
    return {"message": "NDAY_OM API is running."}
