# Governance Index

Use this index to quickly locate governing standards, ingest rules, audit rules, and technical governance references.

> **Latest snapshot:** [Archive/PROGRESS_SNAPSHOT_2026-07-03.md](Archive/PROGRESS_SNAPSHOT_2026-07-03.md) — complete system state, all modules, all tables, outstanding items.

---

## System State & Progress

- [Progress Snapshot 2026-07-03](Archive/PROGRESS_SNAPSHOT_2026-07-03.md) — Full system state at session close; modules, tables, deployment config, outstanding items

---

## Operations & Audit

- [Daily Screenshot Audit Rules](DAILY_SCREENSHOT_AUDIT_RULES.md)
- [Work Summary Tool (WST) Rules](WST_RULES.md)
- [DSP Scorecard Rules](DSP_SCORECARD_RULES.md)
- [POD Report Rules](POD_REPORT_RULES.md)
- [Weekly Incentive Rules](WEEKLY_INCENTIVE_RULES.md)
- [Variable Invoice Rules](INVOICE_RULES.md)
- [Monthly Fleet Invoice Rules](FLEET_INVOICE_RULES.md)
- [Fleet Vehicle Ingest Rules & Van Assignment Governance](VAN_INGEST_RULES.md)

---

## Route & Driver Assignment

- [Van Assignment Rules](VAN_INGEST_RULES.md) — GROUNDED skip, electric constraint, CDV14→16→XL fallback, 7-day affinity, capacity thresholds
- [Driver Quality Rankings](DSP_SCORECARD_RULES.md) — Platinum/Gold/Silver/Bronze standing; used for assignment priority
- [Rostering & Driver DM Rules](ROSTERING_DM_RULES.md) — **Driver DMs and the assignment matrix stay OFF (`ROSTERING_ACTIVE=false`) until the pipeline is fully tested end-to-end**
- **Callout Rule (implemented):** Called-out drivers drop below all available drivers in the assignment queue; only assigned when non-callout pool is exhausted (`is_callout_coverage=True`)

---

## Platform & System

- [DSP Route Manager: Software Manual & System Blueprint](DSP_Route_Manager_Software_Manual.md) — Architecture overview; includes current implementation status section (updated 2026-07-03)
- [Database Schema](DATABASE_SCHEMA.md) — Actual tables (2026-07-03) + original planned schema
- [Database Setup Guide](DATABASE_SETUP.md)
- [Local Development Setup - PostgreSQL Testing (Offline)](LOCAL_DEVELOPMENT_SETUP.md)
- [Mobile App Development Requirements](MOBILE_APP_REQUIREMENTS.md)

---

## Module Registry (Built Features)

| Module | Backend | Frontend Route | Status |
|---|---|---|---|
| File Uploads | `routes/uploads.py` | `/upload` | ✅ |
| Auth (JWT + PIN) | `routes/auth.py` | `/login` | ✅ |
| Driver Handouts | `routes/uploads.py` | `/handouts` | ✅ |
| Driver Schedule | `routes/daily_notify.py` | `/schedule` | ✅ |
| Assignment Database | *(uploads)* | `/assignments` | ✅ |
| Daily Screenshot Audit | `routes/audit.py` | `/audit` | ✅ |
| Weekly Invoice Audit | `routes/weekly_audit.py` | `/weekly-audit` | ✅ |
| Rescue Tracker | `routes/rescue.py` | `/rescue` | ✅ |
| Admin Panel | `routes/auth.py` | `/admin` | ✅ |
| Attendance Reports | `routes/attendance_reports.py` | `/attendance-reports` | ✅ |
| Ops Ingest Monitor | `routes/ops_ingest.py` | `/ops-ingest` | ✅ |
| DVIC Validation | `routes/dvic.py` | `/dvic` | ✅ |
| DSP Scorecard | `routes/dsp_scorecard_weekly.py` | `/dsp-scorecard` | ✅ |
| EOD Survey | `routes/eod_survey.py` | `/eod-survey` | ✅ |
| Driver Quality Rankings | `routes/quality.py` | `/driver-quality` | ✅ |
| Route Assignment Board | `routes/route_assignment.py` | `/route-assignment` | ✅ 2026-07-03 |
