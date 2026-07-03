# NDAY Route Manager — Progress Snapshot
**Date:** 2026-07-03  
**Captured by:** Claude Code (Sonnet 4.6)  
**Repo:** NDAY_OM_MODULAR (GitHub: chief82aps-gif/NDAY_OM)  
**Latest Commit:** `67da734` — Route Assignment Module  
**Deployments:** Backend → Render (`nday-om.onrender.com`) · Frontend → Vercel (`nday-om.vercel.app`)

---

## What This Is

This snapshot captures the complete state of the NDAY Route Manager system at the pause point on 2026-07-03, after the Route Assignment module was completed and pushed. Use this document as the authoritative "where we left off" reference for any future session.

---

## System Architecture

```
NDAY_OM_MODULAR/
├── api/
│   ├── main.py               ← FastAPI entrypoint; all routers registered here; startup migrations
│   ├── requirements.txt
│   └── src/
│       ├── database.py       ← All SQLAlchemy models + ensure_* migration helpers
│       ├── ingest/           ← Modular ingest package (wst/, cortex, dop, fleet, etc.)
│       └── routes/           ← One file per feature module
│           ├── uploads.py
│           ├── auth.py
│           ├── audit.py
│           ├── enhanced_audit.py
│           ├── weekly_audit.py
│           ├── weekly_audit_upload.py
│           ├── rescue.py
│           ├── daily_notify.py
│           ├── quality.py
│           ├── attendance.py
│           ├── attendance_reports.py
│           ├── ops_ingest.py
│           ├── dvic.py
│           ├── dsp_scorecard_weekly.py
│           ├── eod_survey.py
│           └── route_assignment.py   ← NEW (2026-07-03)
├── frontend/
│   ├── pages/                ← One .tsx file per page/feature
│   ├── modules/              ← Module registry (index.ts + one .ts per module)
│   └── components/
│       └── ProtectedRoute.tsx
```

**Hub-and-spoke module pattern:** New features are added as a new file in `api/src/routes/` and `frontend/pages/`, with 2-line additions to `api/main.py` (import + `app.include_router`) and `frontend/modules/index.ts` (import + array entry). No existing files are modified beyond those 2-line hooks.

---

## Backend Modules (api/src/routes/)

| File | Prefix | Description |
|---|---|---|
| `uploads.py` | `/upload` | File upload for Cortex, DOP, Fleet, Route Sheets |
| `auth.py` | `/auth` | JWT login, user create, PIN validation |
| `audit.py` | `/audit` | Daily screenshot audit (OCR comparison) |
| `enhanced_audit.py` | *(no prefix)* | Enhanced OCR audit with confidence scores |
| `weekly_audit.py` | *(no prefix)* | Weekly invoice audit disputes + WST comparison |
| `weekly_audit_upload.py` | *(no prefix)* | Upload endpoint for weekly audit files |
| `rescue.py` | *(no prefix)* | Rescue tracker — 3-stage workflow, bonus formula |
| `daily_notify.py` | *(no prefix)* | DOP/Route Sheet watcher → ops DMs; ECP watch loop |
| `quality.py` | `/quality` | Driver Quality Rankings (snapshots + weekly rankings) |
| `attendance.py` | *(no prefix)* | Driver callout/attendance recording |
| `attendance_reports.py` | *(no prefix)* | Attendance report views |
| `ops_ingest.py` | `/ops-ingest` | Hub: scans #nday-operations-management every 60s, classifies + ingests all file types |
| `dvic.py` | *(no prefix)* | DVIC pre-trip upload validation (sub-90s rule) |
| `dsp_scorecard_weekly.py` | *(no prefix)* | DSP Scorecard weekly data ingestion |
| `eod_survey.py` | *(no prefix)* | End-of-Day survey — 3 PM channel post + 7:30 PM DM reminders |
| `route_assignment.py` | `/route-assignment` | Route Assignment Board — Cortex+DOP+Fleet+Quality+Callouts |

---

## Frontend Pages (frontend/pages/)

| Page | Route | Description |
|---|---|---|
| `index.tsx` | `/` | Dashboard with module cards |
| `login.tsx` | `/login` | JWT login form |
| `handouts.tsx` | `/handouts` | Driver Handout PDF generation |
| `schedule.tsx` | `/schedule` | Driver Schedule report |
| `assignments.tsx` | `/assignments` | Daily Assignment database (search/filter) |
| `audit.tsx` | `/audit` | Daily Screenshot Audit tool |
| `weekly-audit.tsx` | `/weekly-audit` | Weekly Invoice Audit disputes |
| `rescue.tsx` | `/rescue` | Rescue Tracker (3-stage workflow) |
| `admin.tsx` | `/admin` | Admin Panel (users, roles) |
| `attendance-reports.tsx` | `/attendance-reports` | Attendance Reports |
| `ops-ingest.tsx` | `/ops-ingest` | Ops Ingest Monitor (file queue status) |
| `dvic.tsx` | `/dvic` | DVIC Upload Validation |
| `dsp-scorecard.tsx` | `/dsp-scorecard` | DSP Scorecard viewer |
| `eod-survey.tsx` | `/eod-survey` | End of Day Survey management |
| `driver-quality.tsx` | `/driver-quality` | Driver Quality Rankings (Platinum/Gold/Silver/Bronze) |
| `route-assignment.tsx` | `/route-assignment` | Route Assignment Board (NEW 2026-07-03) |
| `upload.tsx` | `/upload` | Generic file upload page |

---

## Database Tables (Actual, as of 2026-07-03)

All tables defined in `api/src/database.py` via SQLAlchemy. SQLite in dev, PostgreSQL target for production. Safe `ensure_*` migration helpers handle column additions on existing DBs.

| Table | Key Columns | Purpose |
|---|---|---|
| `users` | id, username, password_hash, role, is_active | System users + auth |
| `driver_roster` | id, payroll_name, transporter_id, slack_user_id, ssn_last4, pin, role | Driver master list (PIN auth) |
| `cortex_routes` | id, route_date, route_code, driver_name, transporter_id, service_type, wave_time, stops, packages, staging_location | Cortex ingest (daily) |
| `dop_routes` | id, route_date, route_code, wave_time, route_duration_min, planned_packages, staging_location | DOP ingest (daily) |
| `fleet_vehicles` | id, van_number, vin, service_type, operational_status, is_electric, is_grounded | Fleet vehicle roster |
| `daily_route_assignments` | id, assignment_date, route_code, transporter_id, driver_name, van_number, vin, quality_rank, quality_standing, is_callout_coverage, departure_time, stops, assignment_status | Final daily assignments |
| `driver_quality_snapshots` | id, week_label, upload_date, raw_json | Raw quality CSV uploads |
| `driver_quality_rankings` | id, snapshot_id, transporter_id, week_label, overall_score, standing, rank, metric_* | Per-driver weekly quality |
| `rescue_events` | id, event_date, route_code, driver_name, stage, bonus_eligible, bonus_amount | Rescue tracker events |
| `rescue_escalations` | id, rescue_event_id, escalated_to, notes | Rescue escalation log |
| `screenshot_audits` | id, audit_date, cortex_route_code, wst_route_code, status, confidence_score, notes | Daily screenshot audit rows |
| `weekly_audit_rows` | id, audit_date, invoice_number, amount, status, dispute_notes | Weekly invoice audit rows |
| `ecp_roster_prompts` | id, prompt_date, sent_at, channel_id | ECP daily roster prompt dedup |
| `slack_ingest_log` | id, file_id, file_name, file_type, channel_id, ingested_at, status, error_msg | Slack file ingest tracking |
| `ops_ingest_jobs` | id, file_id, file_name, classified_as, status, result_summary, created_at | Ops ingest job queue |
| `dvic_uploads` | id, upload_date, driver_name, video_duration_seconds, is_compliant, notes | DVIC upload validation |
| `dsp_scorecard_snapshots` | id, week_label, upload_date, parsed_data_json | DSP Scorecard weekly data |
| `eod_survey_submissions` | id, driver_name, transporter_id, submission_date, answers_json | EOD survey responses |
| `driver_callouts` | id, callout_date, transporter_id, driver_name, callout_type, notes, recorded_by, created_at | Callout tracking (route assignment) |

---

## Background Loops (api/main.py on_event("startup"))

| Loop | Interval | Window | Purpose |
|---|---|---|---|
| `_daily_notify_loop` | 10 min | 8–10 AM PT | Poll for DOP + Route Sheet in #dlv3-nday-info; DM ops managers |
| `_ecp_watch_loop` | 15 min | 6 PM–midnight PT | Detect ECP message; prompt next-day Cortex upload |
| `_ops_ingest_scan_loop` | 60s | Always | Scan #nday-operations-management; classify + ingest files |
| `_dvic_reminder_loop` | 60s | 3–6 PM PT (internal throttle) | DVIC upload reminders |
| `_dsp_scorecard_reminder_loop` | 60s | Wed 12:30–5 PM PT (internal throttle) | Scorecard Wednesday reminder |
| `_eod_survey_loop` | 60s | 3 PM post + 7:30 PM DMs (internal throttle) | EOD survey channel post + driver DMs |

---

## Slack Channels

| Channel | ID | Used For |
|---|---|---|
| `#nday-operations-management` | `C0BE4ALL1EX` | Ops file drop (Cortex, fleet, DOP); ops ingest scanner |
| `#nday-mgt` | `C0BCYAW7QP3` | Management alerts and prompts |
| `#driver-dashboard` | `C0BEDCXNQNT` | Driver-facing messages (DMs to drivers) |
| `#dlv3-nday-info` | *(scanned by daily_notify)* | Amazon posts DOP + Route Sheets here |

---

## Key Business Rules (Implemented)

### Van Assignment (route_assignment.py)
- **GROUNDED vans**: always skipped — never assigned
- **Electric routes**: ONLY electric vans assigned; no fallback to diesel (critical constraint)
- **CDV14 shortage**: fall back CDV14 → CDV16 → XL (in that order)
- **CDV16 shortage**: fall back CDV16 → XL
- **7-day driver affinity**: prefer the van a driver used in the last 7 days (`daily_route_assignments` lookup)
- **85% capacity threshold**: warn if van utilization exceeds 85%
- **100% capacity**: hard block

### Callout Rule (route_assignment.py)
- Called-out drivers receive priority tier 3 regardless of quality ranking
- Non-callout drivers fill vacancies first (tiers 1–2, sorted by quality rank)
- Callout drivers only used when the non-callout pool is exhausted
- Callout-coverage assignments flagged `is_callout_coverage=True` and displayed amber in the UI

### Driver Quality Priority (route_assignment.py)
- Platinum (score ≥ 4.0) → Gold → Silver → Bronze (lowest)
- Within same standing: sorted by `overall_score` descending
- Quality data sourced from `driver_quality_rankings` (most recent week)

### Name Matching (route_assignment.py + ops_ingest.py)
- Token-overlap match: `frozenset(re.sub(r"[^a-z\s]", "", name.lower()).split())`
- Handles "Last, First" vs "First Last" vs "First Middle Last" across systems
- Minimum 1 token overlap required

### Rescue Tracker (rescue.py)
- Stage 1: Route confirmed in trouble (logged with route + time)
- Stage 2: Rescue driver dispatched (recorded)
- Stage 3: Resolved (packages rescued, bonus calculated)
- Bonus formula: base + per-stop amount, capped per event
- Reinstatement: rescinded if route driver returns

### DVIC Sub-90s Rule (dvic.py)
- Pre-trip video must be ≤ 90 seconds
- Upload flagged non-compliant if duration exceeds threshold
- Daily compliance report per driver

---

## Deployment Configuration

| Item | Value |
|---|---|
| Backend host | `nday-om.onrender.com` (Render free tier) |
| Frontend host | `nday-om.vercel.app` (Vercel) |
| Database (dev) | SQLite at `api/nday_om.db` |
| Database (prod target) | PostgreSQL (Render managed, pending migration) |
| Auth | JWT HS256 (`SECRET_KEY` env var) |
| Slack Bot Token | `SLACK_BOT_TOKEN` env var (xoxb-…) — needs rotation |
| Slack User Token | `SLACK_USER_TOKEN` env var (xoxp-…) — needs rotation |
| CORS | Vercel preview wildcard `*.vercel.app` + production domains |

---

## Outstanding Items (Priority Order)

1. **Rotate both Slack tokens** — both xoxb- and xoxp- tokens were exposed in chat history. Must rotate before any production use.
2. **Driver PIN population** — all 114 drivers default to PIN `1234`; actual SSN last-4 must be entered per driver
3. **Driver Slack ID matching** — 24 drivers not yet in #nday-team-room; block on DM delivery to them
4. **SMS module (Telnyx)** — 10DLC registration + sender setup for drivers without Slack IDs
5. **SQLite → PostgreSQL migration** — dev database works; production needs Render PostgreSQL
6. **Ops manager morning DMs** — post-DOP/Route Sheet trigger to Spencer, Fabian, Luis not yet wired
7. **Driver assignment DMs (post-Cortex)** — notify each driver of their route/van/show time after Cortex ingest
8. **Playwright/RPA module** — Cortex auto-download at 7:30 AM; map remaining "operational clicks"
9. **Route Sheets integration into route_assignment.py** — staging/bag data not yet pulled into board
10. **Invite 114 drivers to #driver-dashboard** — hold until Slack token rotation + PIN population done

---

## Files Produced This Session (2026-07-03)

| File | Status | Description |
|---|---|---|
| `api/src/routes/route_assignment.py` | ✅ Committed | Complete backend module |
| `frontend/pages/route-assignment.tsx` | ✅ Committed | Board UI with callout management |
| `frontend/modules/routeAssignment.ts` | ✅ Committed | Module registry entry |
| `frontend/modules/index.ts` | ✅ Committed (modified) | Added routeAssignmentModule |
| `api/main.py` | ✅ Committed (modified) | Added route_assignment router |
| `api/src/database.py` | ✅ Committed (modified) | DriverCallout model + ensure_assignment_board_columns |
| `api/src/routes/ops_ingest.py` | ✅ Committed (modified) | Saves transporter_id on Cortex ingest |
| `frontend/pages/driver-quality.tsx` | ✅ Committed (prior commit) | Driver Quality Rankings page |
| `frontend/modules/driverQuality.ts` | ✅ Committed (prior commit) | Module registry entry |

---

*This document was auto-generated at session close. Verify against git log for authoritative history.*
