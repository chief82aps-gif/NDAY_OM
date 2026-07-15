# NDAY_OM_MODULAR — API Backend

FastAPI backend for the NDAY Route Manager. Handles file ingest, route management, driver assignments, audits, and the Rescue Tracker module.

**Last updated:** 2026-06-28

---

## Quick Start (Local Development)

```bash
cd C:\Users\chief\NDAY_OM_MODULAR

# Activate the project venv (use the NDAY_OM venv that's already configured)
# The backend reads .env from the repo root automatically on startup

# Start backend
python -m uvicorn api.main:app --reload --port 8000
```

API available at: `http://127.0.0.1:8000`  
Interactive docs: `http://127.0.0.1:8000/docs`

### Seed test data

```bash
python sandbox/seed_test_data.py           # add test data
python sandbox/seed_test_data.py --reset   # wipe and re-seed
```

---

## Environment Variables

Stored in `NDAY_OM_MODULAR/.env` (git-ignored). Loaded automatically at startup via `python-dotenv`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | SQLite at repo root | Override to use PostgreSQL |
| `SLACK_BOT_TOKEN` | No | — | Enables Slack notifications; `xoxb-...` format |
| `SLACK_RESCUE_CHANNEL` | No | `#dispatch` | Channel for rescue event posts |
| `FRONTEND_URL` | No | `https://nday-om.vercel.app` | Used to build Stage 2 links in Slack DMs |

---

## Modules

### Rescue Tracker (`/rescue/*`)

3-stage rescue workflow with Slack integration, payroll reporting, and driver roster management.

**See:** `Governance/RESCUE_TRACKER_RULES.md` for full business rules.

**Key endpoints:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/rescue/routes` | Today's Cortex routes for Stage 1 dropdowns |
| POST | `/rescue/events` | Stage 1 — open a rescue event |
| POST | `/rescue/contributions` | Stage 2 — driver logs packages (public) |
| PATCH | `/rescue/events/{id}/close` | Stage 3 — close event |
| GET | `/rescue/payroll` | Weekly bonus report |
| POST | `/rescue/payroll/confirm` | Mark driver bonus as paid (prevents double-pay) |
| GET | `/rescue/missed-pulls` | Drivers who didn't take all packages |
| GET | `/rescue/roster` | Driver roster with Slack link status |
| PATCH | `/rescue/roster/{id}/slack` | Link + verify a Slack Member ID for a driver |
| POST | `/rescue/roster/{id}/slack/test-dm` | Send test DM to confirm driver receives messages |

**Slack DM flow:** When a rescue is opened, the system looks up the rescuing driver's verified Slack ID from the roster and DMs them the Stage 2 link directly, plus posts to `#rescue-tracking`.

---

### File Ingest (`/upload/*`)

Processes four data sources uploaded daily:

| Source | Format | Endpoint | Content |
|---|---|---|---|
| DOP | Excel | `POST /upload/dop` | Day-of-plan route list |
| Fleet | Excel | `POST /upload/fleet` | Active vehicle inventory |
| Cortex | Excel | `POST /upload/cortex` | Driver-to-route assignments |
| Route Sheets | PDF | `POST /upload/route-sheets` | Per-route bag and overflow manifest |

### Audit (`/audit/*`, `/weekly-audit/*`)

Daily screenshot audit comparing Cortex vs WST route/package counts. Weekly invoice audit comparing invoice line items to WST weekly export.

### Auth (`/auth/*`)

JWT-based authentication. Roles: `admin` > `manager` > `dispatcher` > `driver`.  
Credentials defined in `api/users.json` (overrides defaults).

Default admin: `admin` / `NDAY_26!`

---

## Database

**Local:** SQLite at `NDAY_OM_MODULAR/nday_om.db` (path anchored to repo root via `os.path.abspath(__file__)` — CWD-independent).

**Production:** PostgreSQL on Render (set via `DATABASE_URL` env var).

Tables are created automatically on startup via `Base.metadata.create_all()`.

**Key models in `api/src/database.py`:**

| Model | Table | Purpose |
|---|---|---|
| `RescueEvent` | `rescue_events` | Stage 1 + 3 data |
| `RescueContribution` | `rescue_contributions` | Stage 2 driver package logs |
| `DriverRosterEntry` | `driver_roster` | ADP roster + Slack ID linking |
| `Cortex` | `cortex_routes` | Daily driver-route assignments |
| `VariableInvoice` | `variable_invoices` | Weekly Amazon invoices |
| `WeeklyInvoiceAudit` | `weekly_invoice_audits` | Invoice vs WST audit results |
| `ApprovedAudit` | `approved_audits` | Daily screenshot audit submissions |

---

## Architecture

```
api/
  main.py              — FastAPI app, startup, CORS, route registration
  users.json           — Auth credential overrides
  src/
    database.py        — SQLAlchemy ORM models + engine config
    permissions.py     — Role definitions and permission matrix
    routes/
      rescue.py        — Rescue Tracker (all stages + reports + roster)
      uploads.py       — File ingest endpoints
      auth.py          — Login / JWT
      audit.py         — Daily screenshot audit
      enhanced_audit.py
      weekly_audit.py
      weekly_audit_upload.py

sandbox/
  seed_test_data.py    — Local test data seeder (--reset to wipe)
```

---

## Dependencies

Key packages (full list in `api/requirements.txt`):

- `fastapi` — web framework
- `uvicorn` — ASGI server
- `sqlalchemy` — ORM
- `pydantic` — request/response validation
- `slack_sdk` — Slack DMs and channel posts
- `python-dotenv` — `.env` file loading
- `pandas` / `openpyxl` — Excel ingest
- `pdfplumber` — PDF ingest
- `python-jose` — JWT auth

---

## Copyright

Copyright © 2026 New Day Logistics LLC. All rights reserved.
