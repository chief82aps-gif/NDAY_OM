# NDAY Route Manager — Working Rules

This file is authoritative for how to work in this repo. It exists because this
project has already made — and had to re-learn — several architecture and
safety mistakes. Read it before making structural changes, touching driver-facing
Slack sends, or adding a new module. For product/business detail, this file
points into `Governance/*.md` rather than duplicating it; those docs are the
source of truth for the *what*, this file is the source of truth for the
*how* and *don't*.

**Canonical repo**: this directory (`NDAY_OM_MODULAR`) is the active, going-forward
codebase. A sibling `NDAY_OM` directory is a legacy/earlier version — do not
port fixes there or treat it as current. Some docs in this repo (root
`README.md`, `Governance/DATABASE_SETUP.md`, `Governance/LOCAL_DEVELOPMENT_SETUP.md`)
still describe the old repo's paths/ports (e.g. `api/main_dev.py`, port 8000) —
verify against actual code before trusting those specifics; the frontend's own
`resolveApi()` convention (env override → `127.0.0.1:8001` locally →
`https://nday-om.onrender.com` in prod) is the current truth for dev/prod URLs.

## Architecture: hub-and-spoke

Full detail: `Governance/SRD_MODULE_ARCHITECTURE_v3.md` (start here for any
new module) and `Governance/DSP_Route_Manager_Software_Manual.md`.

- **One module = one route file it owns + the DB tables it writes + an
  optional background loop.** Cross-module reads go through the owning
  module's public functions — never a raw query against another module's
  table, never importing another module's private (`_`-prefixed) functions.
  Named violations already in the codebase that must not be extended as a
  pattern: `attendance_reports.py` reaching into `attendance.py` internals;
  `route_assignment.py` querying `QualityMetricDriver` directly instead of
  through `quality.py`; Slack-posting helpers reimplemented independently in
  7+ files instead of one shared adapter.
- **New module = new route file + new frontend page + a two-line registry
  entry.** Don't modify existing files to bolt on a new feature except
  `api/main.py` (router registration) and the frontend's module registry.
- **RBAC**: every new or touched route file should carry a
  `require_permission`/`require_role` decorator. Only 5 of 24 route files
  currently comply — that's tracked debt, not precedent to copy.
- **Dead/superseded schema — do not build on without re-validating**:
  `Incident`, `IncidentPhoto`, `PerformanceMetric`, `VanInspection`.
  `Governance/DATABASE_SCHEMA.md`'s "Planned Schema" section (singular table
  names) is superseded by its "Actual Schema" section (snake_case plural,
  e.g. `cortex_routes`, `daily_route_assignments`) — only the latter is current.
- **DB migrations are always additive, safe `ensure_*` helpers**
  (try/except `ALTER TABLE ADD COLUMN IF NOT EXISTS`, SQLite- and
  Postgres-compatible), called once at startup in `main.py`. Never an inline
  destructive schema edit. See any `ensure_*_column` function in
  `api/src/database.py` for the pattern.
- **Next.js gotcha**: no `.lnk`/shortcut files anywhere under
  `frontend/pages/` or the dev server fails to start.

## Ingest: append-only, latest-snapshot, never auto-post

This is the single most expensive lesson this project has learned (2026-07).

- **Never delete-by-filename before inserting new ingested rows.** Amazon's
  same-day corrections arrive under a different filename each time
  (`Routes_DLV3_2026-07-10_09_39...xlsx` vs `..._2026-07-11_10_26...xlsx`).
  Deleting scoped to `source_file == this_upload` leaves the *previous*
  file's rows in place forever, and they silently reappear merged with the
  new data (this caused a real bug: a route dropped from a corrected file
  kept showing up because it was never deleted). Ingestion for DOP/Cortex is
  now **append-only** everywhere.
- **Every read goes through `get_latest_dop_rows()` / `get_latest_cortex_rows()`**
  (`api/src/database.py`) — not a naive `.filter(date == ...).all()`. These
  resolve to the single most-recently-ingested *source file* for that date
  and return only its rows (a full snapshot, not a per-route merge — a
  per-route "latest row wins" merge would still incorrectly resurrect a
  route deleted from a later corrected file). Single-route lookups use
  `order_by(id.desc())` or filter against the same latest-file snapshot for
  the same reason.
- **Never trust a file's name or extension to determine its format.**
  Slack/Amazon-sourced files can have mismatched extension vs. actual
  content (a `.csv`-named file can contain real `.xlsx` binary). Use
  `read_tabular_file()` (`api/src/column_mapping.py`), which sniffs the
  actual magic bytes.
- **Ingesting a file must never, by itself, post to Slack or send a DM.**
  Posting the assignment matrix, sending driver DMs, sending an MGT summary
  — all are separate, explicit actions triggered by their own endpoint call,
  not a side effect of `_dispatch()`/ingest. This was a real bug
  (`ops_ingest.py`'s Cortex branch used to auto-trigger both
  `send_day_of_dms()` and `post_assignment_matrix()`) and got fixed
  2026-07-12. If you find yourself wiring a Slack post into an ingest
  function "for convenience," stop — that's the same mistake recurring.
- Historical DOP/Cortex rows accumulate now that deletes don't happen on
  ingest — `POST /upload/dop/purge-old` (default 90 days) exists to bound
  table growth; nothing purges automatically.

## Production safety gates — do not flip without explicit sign-off

Full detail: `Governance/ROSTERING_DM_RULES.md`.

- **`DRIVER_DM_ACTIVE`** (env var, default `false`) gates every DM sent
  directly to a driver — `send_driver_shift_dms()`, `send_day_of_dms()`,
  `send_eod_checklist_dms()` (rostering.py), and DVIC's counseling DMs
  (dvic.py). **Do not set this to `true` on Render without the user's
  explicit go-ahead** — it is currently off deliberately, pending
  end-to-end testing.
- **`ROSTERING_ACTIVE`** (separate flag) gates ops/lead-facing sends that
  stay live: the assignment matrix, MGT summaries, nightly roster reminder,
  wave-lead DMs. Do not conflate the two flags — "matrix active, driver DMs
  inactive" is the intended state, not a bug.
- Before flipping `DRIVER_DM_ACTIVE=true`: confirm the full pipeline
  (DOP → Cortex → DailyRouteAssignment → driver DM) has been tested
  end-to-end, confirm `route_duration` populates correctly, and get
  explicit user sign-off — this is production-visible and affects real
  people's phones.
- **Any Slack-visible action** (posting to a channel, DMing a driver or
  manager) should be confirmed with the user first unless they've already
  explicitly approved that exact action in the current conversation.

## Security — this repo is public

- `chief82aps-gif/NDAY_OM` on GitHub is a **public** repository (confirmed
  2026-07-12). Never commit real people's names, Slack member IDs, phone
  numbers, or other PII to source. Config that identifies a real person
  (e.g. document-routing recipients) belongs in Render environment
  variables or a database row — see `api/src/routes/document_routing.py`'s
  `RoleDirectory` for the pattern (`DOC_ROUTING_OWNER_SLACK_ID` etc.).
  Existing Slack IDs already committed pre-date this policy — don't add more
  of the same mistake, and flag it if asked to touch that code.
- **Security debt status**: `SLACK_BOT_TOKEN` and `SLACK_USER_TOKEN` were
  both exposed in chat history and have both been **rotated (2026-07-13)**
  — resolved, verified live via a real read-only Slack call
  (`POST /ops-ingest/scan`) after redeploy. All ~114 drivers still default
  to PIN `1234` (`ssn_last4` callout-page auth) — PIN auth is currently
  meaningless until real values are populated.
- **`SLACK_NOTIFICATIONS_ACTIVE`** (env var, default `false`) — a hard,
  system-wide kill switch on every outbound Slack send (`chat.postMessage`,
  `chat.update`, `chat.postEphemeral`), implemented as a monkey-patch on
  `slack_sdk.WebClient` in `api/main.py` rather than touching each
  module's own Slack client (there are 24+ scattered `WebClient(...)`
  call sites across 15 files — see the architecture note above). Read-only
  Slack calls (file scanning, ingestion, channel history) are unaffected —
  only sends are gated. This sits *above* `ROSTERING_ACTIVE` and
  `DRIVER_DM_ACTIVE`: while it's off, nothing goes out regardless of what
  those two flags say. Added 2026-07-13 because the system isn't in live
  operation yet — do not set to `true` without the user's explicit go-ahead.
- No credential/key files are currently tracked in git (verified
  2026-07-12) — keep it that way. `.env`, `.env.development`, `.env.local`,
  `*.db` must stay gitignored, never committed.

## Business logic invariants (high-stakes, easy to silently break)

Full detail in the matching `Governance/*_RULES.md` — these are the ones
most likely to bite if re-derived from code alone rather than looked up:

- **Electric van constraint (critical)**: electric vans may ONLY be
  assigned to electric-designated routes — never as a fallback for any
  other service type, and never silently; a non-electric assignment
  requires an explicit dismissed-warning with authorization recorded.
  (`VAN_INGEST_RULES.md`)
- Van auto-assignment fallback chains have an exact required priority
  order (e.g. CDV14→CDV16→XL→DEFAULT) — don't improvise a different order.
  GROUNDED vehicles are always skipped. A driver's van from the last 7 days
  takes precedence over the fallback chain. (`VAN_INGEST_RULES.md`)
- **Callout precedence**: called-out drivers drop below all non-callout
  drivers in the assignment queue, used only once that pool is exhausted.
- **DSP Late Cancel > 0 on the daily screenshot audit is an automatic FAIL**
  requiring immediate escalation to the ops manager, regardless of any
  other metric matching. (`DAILY_SCREENSHOT_AUDIT_RULES.md`)
- **Weekly incentive rate is a strict lookup by scorecard rating**: any
  rating below "Fantastic" pays $0.00/package — no partial credit.
  (`WEEKLY_INCENTIVE_RULES.md`)
- Manager-accountability write-ups use a free-string `writeup_type`, not an
  enum — adding a new write-up catalyst (like DVIC's stage-4 formal
  write-up) needs no schema change, just a new insert plus (if recurring)
  an EOD-scan call. (`MANAGER_ACCOUNTABILITY_RULES.md`)

## Workflow preferences (established via direct user correction this session)

- **Minimize redeploy cycles.** Render does not auto-deploy on push — the
  user manually redeploys each time and has explicitly asked not to be put
  through "render after render." Do a thorough static/code review up front
  and bundle fixes into as few deploys as possible rather than iterating
  live in production.
- **Verify staged files before every commit.** `git status`/`git diff
  --cached --name-only` should show *exactly* the intended files. This repo
  carries 100+ pre-existing untracked/modified files unrelated to any given
  session (old test scripts, sample ingest files, generated reports) —
  never `git add -A`.
- Before any command that could discard uncommitted work, run `git status`
  first, per standing project-wide practice.

## Where to look for more

- `Governance/README.md` — doc index, start here for anything not covered above.
- `UPGRADE_BACKLOG.md` — single source of truth for roadmap/status; don't
  maintain a second one.
- `Governance/SRD_MODULE_ARCHITECTURE_v3.md` — module boundaries, before
  adding anything new.
- `DSP_Operations_SRD_v2.pdf` — business/policy requirement IDs (HRM/OPS
  tier system, CR/VS formulas). Its §9 recommended tech stack (React
  Native, Redis, AWS Chime, DocuSign) is aspirational and does **not**
  reflect the actual stack (FastAPI + Next.js + Postgres) — don't infer
  current architecture from it.
