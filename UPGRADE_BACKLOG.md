# NDAY Route Manager — Development Roadmap & Todo List

**Last Updated:** 2026-07-03  
**Format:** Chronological todo list — items ordered by when they should be done, not by category.  
**Status Key:** ✅ Done · 🔄 In Progress · 🔲 Not Started · 🚧 Blocked (dependency noted)

---

## Next Steps — Suggested Immediate Priorities

> This section was added 2026-07-03 at session close. Items are ordered by urgency and dependency chain.

### ⚪ Attendance — Policy Updates

| # | Task | Detail |
|---|---|---|
| AT1 | **Change rolling window from 90 → 60 days** | ✅ Done 2026-07-07 — backend + frontend both updated |
| AT2 | **Point deduction system** — positive behaviors that reduce point total | See below |

**AT2 — Point Deduction Events (to be designed and coded):**
- **Short-notice fill-in** — driver responds to management request and covers a route on short notice → deduct points
- **Sweep completion** — driver performs a sweep shift → deduct points
- **Perfect attendance period** — driver goes X days with zero attendance events → deduct points (reward consistency)

> Rule details (thresholds, deduction amounts, eligibility windows) TBD with Jayson before coding. These should appear on the callout page status screen and in the attendance reports.

---

### 🔴 Security — Do First

| # | Task | Why Now |
|---|---|---|
| S1 | **Rotate SLACK_BOT_TOKEN** (xoxb-) on Render → OAuth & Permissions → Revoke → Reinstall | ✅ Done 2026-07-13 — was exposed in chat history |
| S2 | **Rotate SLACK_USER_TOKEN** (xoxp-) on Render | ✅ Done 2026-07-13 — was exposed in chat history |
| S3 | **Populate driver PINs** — enter actual SSN last-4 for all active drivers via Admin Panel | All 114 drivers default to `1234` — PIN auth is currently meaningless |

### 🟡 Route Assignment — Finish the Loop

| # | Task | Why Now |
|---|---|---|
| R1 | **Wire post-Cortex DMs** — after Cortex is ingested by ops_ingest, send each driver their route/van/show time via Slack DM | Route Assignment module is built; DM delivery is the last mile |
| R2 | **Ops manager morning DMs** — after DOP + Route Sheet both detected, DM Spencer (`U0AJGCYKXPB`), Fabian (`U0AJPQALDLL`), Luis (`U0B36C9R8N4`) | Currently only logs ingest; no outbound prompt to ops team |
| R3 | **Route Sheets staging data** — pull staging_location and bag info from ingested Route Sheets into route_assignment `/board` response | Board shows staging from Cortex only; Route Sheet detail (bag counts, overflow) not yet surfaced |
| R4 | **Test Route Assignment end-to-end** — upload a real Cortex file, click Auto-Assign, verify board populates, mark a callout, confirm coverage logic | Board is built but untested in a live session |

### 🟢 Automation — High Ops Value

| # | Task | Why Now |
|---|---|---|
| A1 | **Driver invite wave** — once Slack tokens are rotated, invite the 24 missing active drivers to #nday-team-room | Required for DM delivery to those drivers |
| A2 | **Loadout van timing dashboard** — UI to record van-in / van-out per route; enables post-loadout scrum prompt | Missing link in the daily drumbeat (no departure tracking) |
| A3 | **Drumbeat cron jobs** — wire 3:00 PM rescue reminder, 3:30 PM OKAMI prompt, 5:00 PM ECP reminder as FastAPI background tasks | Currently manual; all other prompts are automated |
| A4 | **Playwright/RPA module** — Cortex auto-download at 7:30 AM PT; baseline session via `playwright codegen` | Eliminates manual Cortex upload; highest single time-save for ops |

### 🔵 Infrastructure — Before Production Load

| # | Task | Why Now |
|---|---|---|
| I1 | **SQLite → PostgreSQL migration on Render** | SQLite file may not persist across Render deploys on free tier |
| I2 | **Telnyx account + 10DLC registration** | 1–2 week carrier approval; start now to unblock SMS fallback |
| I3 | **Daily reports checklist widget** — dashboard tile showing which daily reports are submitted vs. outstanding | Ops currently has no visibility into missing reports |

### 📋 In the Next Session — Suggested Order

1. S1 + S2: Rotate tokens (5 min, Slack dashboard)
2. R4: Load a real Cortex file, run the board end-to-end — catch any data mapping issues
3. R1: Wire post-Cortex driver DMs (extend `ops_ingest.py` Cortex handler → call route_assignment board → DM each driver)
4. R2: Wire ops manager morning DMs (extend `daily_notify.py` post-detection handler)
5. A3: Add 3 PM + 3:30 PM + 5 PM cron reminders as FastAPI tasks in `main.py`
6. I1: Migrate to PostgreSQL on Render

---

---

## Operational Drumbeat (Source of Truth for All Automation)

This section defines the timed operational rhythm that all system automations, notifications, and checklists are built against.

### Daily Schedule

| Time (PT) | Event | Trigger Type | System Action |
|---|---|---|---|
| 08:00–09:15 | DOP + Route Sheets drop in `#dlv3-nday-info` | Auto (Slack poll, every 10 min) | Ingest files → Slack + SMS prompt to Spencer, Fabian, Luis to roster and upload Cortex |
| ~09:15 | Cortex file uploaded to `#nday-operations-management` | Auto (Slack poll detects file) | Ingest Cortex → send each rostered driver their Van / Route / Stage / Show Time via Slack DM + SMS |
| ~09:15 | Post-Cortex: Driver report / roll call | Prompt (after Cortex ingest) | Dispatcher prompted via Slack to begin roll call and confirm attendance |
| Loadout window | Van arrivals and departures | Manual entry | Dashboard: record van-in and van-out timestamps per route |
| Post-loadout | Staging area scrum | Prompt (after last van departs) | Dispatcher prompted: confirm all carts/packages accounted for and taken |
| By 15:00 | Rescue plans formulated | Checklist | Dashboard reminder: rescue plans must be logged in system by 3:00 PM |
| 15:30–16:00 | OKAMI scheduling tool | Manual + Prompt | System reminds ops at 15:30 to work on OKAMI; final submission due by 16:00 |
| ~17:00 | ECP process (Amazon side) | Prompt | System reminds ops: ECP must complete before 19:00 |
| Post-ECP | Roster team for next day | Prompt (ECP watch detects message) | Existing ECP watch loop prompts `#nday-operations-management` to upload next-day Cortex |
| End of day | Driver sign-out / end-of-day sheets | Prompt | Drivers prompted (Slack DM + SMS) to complete and submit sign-out form |

### Daily Reports (Office Hours — between Rescue Plans and OKAMI)

| Report | Responsible | System Role |
|---|---|---|
| 7-day DA break utilization report | Ops | Prompt, receive upload, store |
| Daily report | Ops | Prompt, receive upload, store |
| Pre-trip DVIC under 90-second report | Dispatcher | Prompt, receive upload, validate |
| Performance summary dashboard | Ops | Screenshot capture (Playwright), auto-upload |
| Quality dashboard | Ops | Screenshot capture (Playwright), auto-upload |
| Safety dashboard | Ops | Screenshot capture (Playwright), auto-upload |

### Weekly Schedule

| Day | Event | System Action |
|---|---|---|
| Monday | Process route payments received | Prompt ops; receive and ingest payment data |
| Wednesday | **Scorecard Day** — big data drops | Ingest all of the following: Fleet Execute / Engine Off compliance · No Lap Belt report · Netradyne 3P vehicles report · Tenured Workforce dashboard · Scorecard · Report · POD report — then flag errors and generate action item list |
| Friday | Process incentive payments | Prompt ops; ingest and reconcile incentive data |

### Monthly Schedule

| Timing | Event | System Action |
|---|---|---|
| ~5th of month | Upload Fleet Invoice | Ingest fleet invoice → auto-scrub against vans used that month → flag discrepancies for review |

---

## Phase 0 — Completed (Foundation)

- ✅ Route Sheet PDF parsing (multi-page, A/B/E/G zone support)
- ✅ DOP Excel ingest with column auto-detection
- ✅ Cortex Excel ingest (route assignments, driver names)
- ✅ Fleet Excel ingest with GROUNDED filtering
- ✅ Driver schedule ingest (Rostered Work Blocks + Shifts & Availability tabs)
- ✅ Show time calculation with wave consolidation (25 min before wave)
- ✅ Sweeper identification from schedule vs. assignment comparison
- ✅ Vehicle assignment engine (VIN → route mapping)
- ✅ Driver handout PDF generation
- ✅ Driver schedule PDF report generation
- ✅ Daily driver assignment dashboard (frontend)
- ✅ Assignment database with search and filter
- ✅ Admin panel and user management
- ✅ Authentication (JWT, role-based access)
- ✅ Slack bot setup (`nday_route_manager`, bot token, user token)
- ✅ Slack channel monitoring via `files_list` API (avoids `groups:history` scope)
- ✅ Daily Cortex ingest from `#nday-operations-management`
- ✅ Daily DOP + Route Sheet ingest from `#dlv3-nday-info`
- ✅ ECP watch loop (6 PM–midnight PT) — detects ECP message, prompts Cortex upload
- ✅ `ecp_roster_prompts` table and deduplication
- ✅ Slack ingest log (`slack_ingest_log` table) with per-file status tracking
- ✅ Modular ingest package (`api/src/ingest/` with `wst/` subpackage)
- ✅ Rescue tracker (business rules, 3-stage workflow, bonus formula, reinstatement)
- ✅ Invoice audit tool (variable invoice CSV parsing, line-item reconciliation)
- ✅ Weekly audit disputes and WST comparison
- ✅ Daily screenshot audit (OCR-based Cortex vs WST comparison)
- ✅ Ops Ingest hub (60s scan loop, classifies all file types, #nday-operations-management)
- ✅ DVIC sub-90s validation module
- ✅ DSP Scorecard weekly data module
- ✅ End-of-Day Survey (3 PM channel post + 7:30 PM driver DMs)
- ✅ Driver Quality Rankings (Platinum/Gold/Silver/Bronze, week selector, expandable metric rows)
- ✅ Route Assignment Board (Cortex + DOP + Fleet + Quality + Callout rule) — *2026-07-03*

---

## Phase 1 — Now (Week of 2026-07-01)

### 1.1 — Driver Slack ID Population
- 🔲 Match 70 drivers in `#nday-team-room` to `DriverRosterEntry` by email
- 🔲 Bulk-insert Slack user IDs into driver roster table
- 🔲 Fix Cantrell/Cantrall email typo in Slack (Slack admin action)
- 🔲 Invite 24 missing active drivers to `#nday-team-room`
  - List: a.tepehua, b.dietmeier, c.litada, c.dyson, d.hutchinson, f.saldarriaga,
    j.figueroa, k.dyson, k.holdman, l.rojas, m.gronostalski, r.tapado, r.reinberg,
    s.estep, j.ybarra, s.malic, s.embree, s.navarrosegura, s.webb7, s.davis2,
    t.mix, t.freisner1, v.geroe, w.wardrobe1

### 1.2 — Daily Morning Notification Flow (Ops Managers)
- 🔲 After DOP + Route Sheet both detected, send Slack DM to:
  - Spencer Colby (`U0AJGCYKXPB`)
  - Fabian Marcillo (`U0AJPQALDLL`)
  - Luis Rojas (`U0B36C9R8N4`)
- 🔲 Message: route/package counts + instruction to roster and drop Cortex into `#nday-operations-management`
- 🔲 Deduplication: only send once per day per ops manager

### 1.3 — Driver Assignment Notifications (Post-Cortex)
- 🔲 After Cortex ingested, look up each rostered driver's Slack ID
- 🔲 Send each rostered driver a DM with:
  - Route code
  - Van number (from fleet assignment engine)
  - Stage location (from DOP)
  - Show time (wave time − 25 min)
  - Package count
- 🔲 Send sweeper DM to drivers who are active but not assigned a route:
  - "You are a sweeper today. Please report by [earliest show time]."
- 🔲 Log all DMs sent to `slack_ingest_log` or a new `driver_notifications` table

### 1.4 — SMS Module (Telnyx)
- 🔲 Create Telnyx account and purchase a US phone number
- 🔲 Register 10DLC brand and campaign (takes 1–2 weeks for carrier approval)
- 🔲 Add env vars: `TELNYX_API_KEY`, `TELNYX_FROM_NUMBER`
- 🔲 Install Telnyx SDK: `pip install telnyx`
- 🔲 Create `api/src/sms/` module:
  - `client.py` — Telnyx client singleton, reads env vars
  - `sender.py` — `send_sms(to_number, message)` with retry and logging
  - `__init__.py` — exports `send_sms`
- 🔲 Store driver phone numbers from Cortex CSV in `driver_roster` table
- 🔲 SMS fallback for drivers without a Slack ID (the 24 missing from Slack)
- 🔲 SMS ops manager alerts (Spencer, Fabian, Luis) in parallel with Slack DMs
- 🔲 SMS delivery log table (`sms_log`: recipient, message, status, timestamp)
- **Est. cost:** ~$32/month (Telnyx at 200 SMS/day + 10DLC + number rental)

### 1.5 — Drumbeat Automation (Timed Prompts & Checklists)
- 🔲 **Post-Cortex roll call prompt** — after Cortex ingested, Slack message to dispatcher: begin roll call, confirm attendance
- 🔲 **Post-loadout staging scrum prompt** — after last van departure recorded, Slack prompt: confirm all carts/packages staged and taken
- 🔲 **3:00 PM rescue plan reminder** — cron at 15:00 PT: Slack reminder to log rescue plans before cutoff
- 🔲 **3:30 PM OKAMI reminder** — cron at 15:30 PT: Slack prompt to begin OKAMI scheduling; deadline 16:00
- 🔲 **OKAMI input form** — dashboard form for ops to submit OKAMI scheduling data directly (fields/schema TBD with Jayson)
- 🔲 **5:00 PM ECP reminder** — cron at 17:00 PT: Slack reminder ECP must complete by 19:00
- 🔲 **End-of-day sign-out prompt** — after ECP confirmed, Slack DM + SMS to all rostered drivers: submit sign-out sheet
- 🔲 **Monday payment prompt** — cron Monday morning: Slack reminder to process route payments
- 🔲 **Wednesday scorecard prompt** — cron Wednesday morning: Slack checklist of all data drops due (7 items)
- 🔲 **Friday incentive prompt** — cron Friday morning: Slack reminder to process incentive payments
- 🔲 **5th-of-month fleet invoice prompt** — cron 1st of month (5-day warning): Slack reminder fleet invoice due by 5th
- 🔲 **Loadout van timing dashboard** — UI for dispatcher to record van-in / van-out times per route per day
- 🔲 **Daily reports checklist** — dashboard widget showing which daily reports are submitted vs. outstanding:
  - 7-day DA break utilization
  - Daily report
  - Pre-trip DVIC under 90-second
  - Performance summary (screenshot)
  - Quality dashboard (screenshot)
  - Safety dashboard (screenshot)

### 1.6 — Playwright / RPA Module
- 🔲 Install Playwright: `pip install playwright && python -m playwright install chromium`
- 🔲 Create `api/src/automation/` module:
  - `__init__.py`
  - `browser.py` — browser lifecycle, session persistence (`amazon_session.json`)
  - `cortex.py` — Cortex portal login + route file download
  - `tasks/` — one file per distinct workflow (future: each Amazon portal task)
- 🔲 Record baseline session using `playwright codegen` against Cortex URL
- 🔲 Schedule Cortex auto-download at 7:30 AM PT (before DOP drops) via `main.py` loop
- 🔲 Drop downloaded file directly into ingest pipeline (bypass Slack upload)
- 🔲 Log automation runs to `automation_log` table (task, status, duration, error)
- 🔲 Map remaining "operational click" workflows — list all Amazon portal tasks
  that need automation (schedule for Phase 2 once list is confirmed)

### 1.6 — Production Deploy (Render)
- 🔲 Push all current changes to GitHub
- 🔲 Set Render env vars:
  - `SLACK_BOT_TOKEN`, `SLACK_USER_TOKEN`
  - `SLACK_NOTIFY_CHANNEL` (C0AF48TPAMV)
  - `CORTEX_NOTIFY_CHANNEL` (C0BE4ALL1EX)
  - `TELNYX_API_KEY`, `TELNYX_FROM_NUMBER`
  - `FRONTEND_URL`
- 🔲 Rotate both Slack tokens (bot + user tokens were shared in chat)
- 🚧 **Blocked:** Telnyx 10DLC approval needed before bulk SMS can go live

---

## Phase 2 — Near Term (2–4 Weeks)

### 2.1 — Browser Extension for Screenshot Capture
- 🔲 Build Chrome/Edge extension (Manifest v3) for daily Cortex vs WST audit
- 🔲 One-click full-page capture including off-screen content
- 🔲 Guided capture sequence (Cortex first, then WST)
- 🔲 Auto-attach metadata: timestamp, URL, detected page type
- 🔲 Direct handoff to NDAY Daily Screenshot Audit upload pipeline
- 🔲 Validation banner if date headers differ between captures

### 2.2 — Sweeper Management Workflow
- 🔲 Sweeper queue view on dashboard (who's available, who's been dispatched)
- 🔲 Sweeper assignment history and response time metrics
- 🔲 Notification flow when a sweeper is dispatched to a rescue

### 2.3 — Admin Role Management (UI)
- 🔲 Admin endpoint to update user role (admin, manager, dispatcher, driver)
- 🔲 Role selector in admin panel user list
- 🔲 Audit log entry for role changes (who, when, old → new role)
- 🔲 Guard: prevent removal of last admin account

### 2.4 — Driver Performance Analytics (Phase 1)
- 🔲 KPI cards on dashboard: on-time %, packages delivered, rescue count
- 🔲 Per-driver scorecard page
- 🔲 Weekly trend graphs

### 2.5 — Automation: Additional RPA Workflows
- 🔲 Confirm full list of "operational clicks" with ops team
- 🔲 Build one task file per workflow under `api/src/automation/tasks/`
- 🔲 Dashboard trigger buttons for on-demand automation runs
- 🔲 Scheduling for recurring automations

---

## Phase 3 — Medium Term (1–3 Months)

### 3.1 — Driver Mobile App (MVP)
- **Framework:** React Native (shares code with Next.js frontend)
- 🔲 Authentication (existing JWT)
- 🔲 View daily assignment (route, van, stage, show time)
- 🔲 Push notifications via Firebase Cloud Messaging (FCM)
- 🔲 Handout PDF download and viewing
- 🔲 Basic driver profile
- **Cost:** FCM free · EAS Build $7–25/mo · Apple Dev $99/yr · Google Play $25 one-time

### 3.2 — Incident & Accident Reporting (Mobile)
- 🔲 Incident form with photo capture (timestamp + GPS)
- 🔲 Incident type selection (accident, damage, safety, customer complaint)
- 🔲 Offline queue (submit when reconnected)
- 🔲 Auto-attach vehicle/driver/route context
- 🔲 Cloud photo storage (AWS S3 or Google Cloud)
- 🔲 Manager instant notification on submission

### 3.3 — Van Inspection Tool (Mobile)
- 🔲 Pre-shift walk-around checklist
- 🔲 Photo tagging by damage location and severity
- 🔲 Inspection history per VIN
- 🔲 Maintenance alert system (recurring issues)
- 🔲 Comparison photos over time

### 3.4 — Vehicle Rotation & Maintenance Tracking
- 🔲 Log mileage per route per VIN
- 🔲 Maintenance due alerts
- 🔲 Vehicle defect tracking
- 🔲 Rotation schedule to prevent overuse

### 3.5 — Driver Scorecard & Coaching System
- 🔲 Automated KPI calculation (on-time %, safety incidents, attendance)
- 🔲 Performance tiers and trend analysis
- 🔲 Coaching recommendation engine by weakness area
- 🔲 Manager 1:1 discussion guide export
- 🔲 Top performer celebration alerts

### 3.6 — Motivational Phrase Generator
- 🔲 Track phrase impact on key metrics per driver cohort
- 🔲 A/B test motivational vs safety messaging
- 🔲 Recommend best-performing phrase per driver profile
- 🔲 Admin manual override

---

## Phase 4 — Longer Term (3+ Months)

### 4.1 — Property Sign-Out / Checkout App
- 🔲 Digital sign-out form (tablets, scanners, badges, vehicle keys)
- 🔲 Vehicle condition checklist (fuel, damage, cleanliness)
- 🔲 Digital signature capture
- 🔲 Real-time alert if property not returned

### 4.2 — Attendance App & HR Forms
- 🔲 Call-out / absence reporting
- 🔲 Time-off request workflow with manager approval
- 🔲 HR form submission (suggestions, feedback)
- 🔲 Audit trail of all requests

### 4.3 — Multi-Week Schedule Planning
- 🔲 Upload template schedules
- 🔲 Generate recurring schedules weeks in advance
- 🔲 Identify coverage gaps and conflicts
- 🔲 Alert on low-coverage days

### 4.4 — Advanced Load Balancing
- 🔲 Equal package/stop distribution per driver
- 🔲 Experience level balancing (mix senior + new)
- 🔲 Route difficulty rating
- 🔲 Driver skill affinity (preferred routes/vehicles)

### 4.5 — Real-Time Driver Status Tracking
- 🔲 Status updates throughout shift (arriving, on route, returning, complete)
- 🔲 Integration with delivery tracking system
- 🔲 Performance reports from actual completion vs. planned
- 🔲 Delay alert system

### 4.6 — Driver Self-Service Portal
- 🔲 View own assignments and show times
- 🔲 Shift swap requests (with manager approval)
- 🔲 Time-off requests
- 🔲 Availability calendar management

### 4.7 — Hiring & Onboarding Automation (Asana integration)
- 🔲 Full spec: `Governance/03_NDL_Hiring_Onboarding_Automation.md`
- 🔲 Phase 1: Chrome extension captures Indeed candidates → Asana sync + candidate analytics (keyword tags, avg tenure)
- 🔲 Phase 2: automated SMS/email contact cadence, Google Contacts push
- 🔲 Phase 3: downstream stage automation (Flex invite → background check → drug test → training → ORE)
- **Dependencies:** Asana API key (project/section mapping already live — "New Day Hiring" board), SMS/email provider for Phase 2

### 4.8 — Google Maps / Route Optimization
- 🔲 Drive time calculations per route
- 🔲 Traffic-aware scheduling
- 🔲 Zone mapping visualization
- 🔲 Sweeper geographic routing

### 4.9 — Forecasting & Capacity Planning
- 🔲 Demand forecasting for upcoming weeks
- 🔲 Staffing requirement predictions
- 🔲 Hiring recommendations
- 🔲 Seasonal pattern analysis

---

## External Dependencies Tracker

| Dependency | Status | Owner | Notes |
|---|---|---|---|
| Telnyx account + number | 🔲 Not started | Jayson | Purchase number, start 10DLC registration |
| 10DLC brand registration | 🔲 Not started | Jayson | ~1–2 week carrier approval wait |
| Playwright codegen session | 🔲 Not started | Jayson | Run against Cortex URL, save session |
| RPA workflow list | 🔲 Not started | Ops team | Full list of "operational clicks" needed |
| Slack token rotation | 🔲 Not started | Jayson | Both bot + user tokens exposed in chat |
| 24 missing driver Slack invites | 🔲 Not started | Ops | Invite list captured in session 2026-07-01 |
| Apple Developer Account | 🔲 Not started | Jayson | Needed for Phase 3 mobile app ($99/yr) |
| Firebase project (FCM) | 🔲 Not started | Jayson | Needed for push notifications (free tier) |
| AWS S3 or GCS bucket | 🔲 Not started | Jayson | Needed for incident/inspection photos |

---

## Monthly Cost Estimate (At Full Build-Out)

| Service | Monthly |
|---|---|
| Telnyx SMS (200/day) | ~$32 |
| Telnyx 10DLC campaign | ~$5 |
| Firebase FCM (push notifications) | $0 (free tier) |
| AWS S3 (photos ~2.5GB/yr) | ~$5 |
| EAS Build (mobile CI) | ~$7–25 |
| Render (backend hosting) | existing |
| **Total additions** | **~$49–67/mo** |
