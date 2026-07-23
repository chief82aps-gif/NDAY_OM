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

## Never store reminder/throttle "already sent" state in memory

Real incident, 2026-07-13: `mgt_reminders.py`, `dvic.py`, and
`dsp_scorecard_weekly.py` all tracked their "already sent this cycle" /
"resolved today" dedup state in a plain module-level Python dict. That
dict resets to empty on every process restart. Render restarts on every
redeploy, and a burst of redeploys in a short window (each env-var
toggle, each fix) repeatedly wiped the "sent 5 minutes ago" memory,
letting the next background-loop tick fire again immediately — #nday-mgt
got flooded with dozens of duplicate reminder DMs in minutes. Fixed by
adding `ReminderThrottleState` (`api/src/database.py`) — a persisted
key/JSON-blob table via `get_reminder_state()`/`set_reminder_state()` —
and moving all three loops onto it. **Any new periodic Slack
reminder/nag loop must persist its dedup/throttle state to the database,
never a module-level dict or variable.** This is the same root problem
as the `DailyRouteAssignment` duplication above — no durable state to
prevent an uncoordinated repeat trigger — just a different symptom.

## Always use Pacific local time for "today" — never the server clock

Real bug, found 2026-07-17/18: `okami_capacity.py`'s submit/finalize/`/today`
endpoints defaulted a missing date to `date.today()` / `datetime.utcnow()`.
Render's server clock runs UTC, and Okami is submitted in the 3:30-9:00 PM
Pacific window — which is already the *next* UTC calendar day for most of
that window. A submission with no explicit date got silently stamped
"tomorrow," so `has_submission_today()` (correctly checked against Pacific
"today") never found it, and the mgt_reminders.py nag kept firing all
evening despite a real submission existing. Confirmed live: a 2026-07-17
5:35 PM Pacific submission landed with `log_date: "2026-07-18"`.

**Rule: every "what date/time is it right now" check in this codebase must
resolve against `datetime.now(ZoneInfo("America/Los_Angeles"))`, never
`date.today()`, `datetime.now()` (naive), or `datetime.utcnow().date()`.**
This project's actual operations — DOP/Cortex windows, rostering, Okami,
reminders — all run on Pacific business hours; the server's own system
timezone is an implementation detail that must never leak into business-date
logic. `mgt_reminders.py`'s `_file_detected_today()` already does this
conversion correctly (Pacific midnight → UTC, not UTC midnight) — copy that
pattern, don't reinvent it. When in doubt, grep for `PT = ZoneInfo(...)` —
several files already define this constant; reuse it rather than adding a
new naive date call.

## Okami Capacity — locked 2026-07-19

User confirmed `okami_capacity.py` (submit/finalize, the card-style
`#nday-mgt` message, the Pacific-time fix above) is working correctly.
**Do not modify this module unless explicitly asked to** — it's done,
not a target for opportunistic cleanup while touching adjacent code.

## Daily Scheduler (Showtime + Route Assignment + ingest) — locked 2026-07-21

Verified end-to-end against real production data on 2026-07-21: 43/43
routes ingested cleanly across DOP/Cortex/Route Sheet, 0 routes left
without a van (28/28 electric routes correctly got real EDVs, 0
warnings), 0 driver-name corruption, 0 false route-code mismatches once
Cortex actually lands for the day. **Do not modify any of the following
without explicit authorization** — this is done, not a target for
opportunistic cleanup while touching adjacent code:

- `api/src/routes/daily_notify.py` — `check_and_notify()`,
  `build_daily_assignments()`, `ingest_dop_bytes()`,
  `ingest_cortex_bytes()`, `ingest_route_sheet_bytes()`,
  `check_route_code_reconciliation()`
- `api/src/routes/rostering.py` — `post_showtime_summary()`,
  `post_assignment_matrix()`
- `api/src/routes/route_assignment.py` — `assign_vans_for_routes()`,
  `_assign_van()`, the electric-van-shortage substitution and
  adjacency-based redistribution ranking
- `api/src/ingest/dop.py`, `cortex.py`, `route_sheets.py`, `fleet.py` —
  the shared parsers
- `api/src/routes/ops_ingest.py` — Fleet's separate ingest pipeline
- `api/src/routes/uploads.py`'s diagnostic/recovery endpoints
  (`/dop/debug`, `/cortex/debug`, `/clear-day`)

**Explicitly NOT covered by this lock** (still open, expected to keep
changing):

- `post_mgt_summary()` in `rostering.py` — known silent-`chat_update`
  bug, not yet fixed
- The driver-DM sending path (`send_driver_shift_dms()`,
  `send_day_of_dms()`, the older `send_all_dms()`/`send_driver_dm()`)
  and the scheduler-reconciliation work connecting them — this is the
  active, in-progress path to getting `DRIVER_DM_ACTIVE` live
- The Slack Events API push endpoint (`/slack/events` in
  `slack_interactions.py`) — built, not yet activated (needs Slack App
  Event Subscriptions configured on Slack's side)
- `driver_lead_schedule.py` / `LEAD_ROUTING_ACTIVE` — separate,
  unrelated feature, stays off

## Driver-Slack linking — locked 2026-07-21

Active roster verified at 101/101 linked (100%) against production on
2026-07-21: live Slack `users.list` roster + `AssociateData` bridge
match (`best_slack_match_via_associates()`) took it from 84/107 → the
final two holdouts were resolved individually — one terminated
(`Matthew Jonah Gronostalski`, was never actually active), one manually
linked via `PATCH /drivers/{id}` after the user found him directly in
Slack (`Victor Hugo Arteaga Sarmiento`, still shows "Invited member" —
pending his own invite acceptance, not a linking-logic problem).
**Do not modify without explicit authorization**:

- `api/src/driver_matching.py` — `best_ssn_match()`,
  `best_slack_match()`, `best_slack_match_via_associates()`,
  `load_ssn()`, `load_slack()`, `load_associates()`
- `api/src/routes/drivers.py` — `import_ssn_slack()`,
  `update_driver()`, `terminate_driver()`

**Explicitly NOT covered by this lock** (still open, expected to keep
changing):

- `run_associate_data_reminder()` / `notify_new_unlinked_drivers()`
  (both new 2026-07-21, both gated off by `ASSOCIATE_DATA_REMINDER_ACTIVE`
  / `UNLINKED_DRIVER_ALERT_ACTIVE`, default `false`) — not yet confirmed
  live
- Ongoing per-driver link maintenance (new hires from schedule uploads,
  terminations) is expected and not itself a "modification" of the
  matching logic

## Driver-DM button pipeline (Showtime + Route Assignment + callout) — locked 2026-07-21

Verified end-to-end 2026-07-21 against a real Slack account (driver
Spencer Colby, roster id 95) using mock schedule/assignment data on a
throwaway test date, then cleaned up: all four buttons confirmed
working through the real backend (Acknowledge, Arrived, Can't Make It,
Call Out), including the real `/callout` write-up submission
(`AttendanceEvent` + points) both negative buttons feed into. **Do not
modify without explicit authorization**:

- `api/src/routes/rostering.py` — `_build_shift_dm()`,
  `_build_driver_dm()`, `send_driver_shift_dms()`, `send_day_of_dms()`,
  `ack_schedule()`, `decline_shift()`, `mark_driver_arrived()`,
  `mark_callout_tapped()`, `refresh_shift_response_summary()`,
  `refresh_arrival_response_summary()`, `refresh_all_dm_response_summaries()`
- `api/src/routes/slack_interactions.py` — `_handle_schedule_ack()`,
  `_handle_driver_decline_shift()`, `_handle_driver_arrived()`,
  `_handle_driver_callout_from_dm()`, `_issue_callout_token()`
- `frontend/pages/callout.tsx` — the signature-match normalization fix
- The Showtime DM only ever offers Acknowledge/Can't Make It; the Route
  Assignment DM only ever offers Arrived/Call Out — never both pairs
  on one message. "Can't Make It" and "Call Out" are genuine one-click
  buttons (Slack `url` field baked in at send-time, 48h TTL) straight
  to the real `/callout` page — do not revert to the old two-tap
  (button → ephemeral link → tap again) pattern.
- `#nday-mgt` visibility for Acknowledge/Arrived/Decline/Call-Out comes
  ONLY from the consolidated periodic summary
  (`_dm_response_summary_loop()` in `main.py`, every 30 min) — do not
  reintroduce a per-event message per driver response.

**Explicitly NOT covered by this lock** (still open):

- Everything under "Production safety gates" below — `DRIVER_DM_ACTIVE`
  is still off pending the dry-run + explicit sign-off
- The write-up/sign-off review dashboard (ops manager / HR / owner
  sign-off chain) — new work, not yet built as of 2026-07-21

**2026-07-23 addition, explicitly authorized by the user**:
`send_day_of_dms()` now checks `api/src/outstanding_items.py`'s
`get_outstanding_items()` before calling `_build_driver_dm()` — a driver
with anything unacknowledged of their own (DVIC safety notice, unsigned
attendance write-up) gets a holding message + link to
`frontend/pages/outstanding-items.tsx` instead of route details, and
`dm_sent` stays `False` so the next ~10-min scheduler pass re-checks
automatically once cleared. This is an early-exit branch only —
`_build_driver_dm()`'s content and the Arrived/Call-Out button structure
are unchanged for every driver with nothing outstanding.

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
- **`TEAM_ROOM_MESSAGES_ACTIVE`** (env var, default `false`) — hard
  off-switch for all automatic messages to `#nday-team-room`, added
  2026-07-20 after a run of same-day production bugs (showtime/DVIC/van
  assignment) made the user decide unreliable info reaching the whole
  team causes more harm than a missed post. Currently gates
  `post_showtime_summary()`'s team-room copy only (the sole automatic
  sender to that channel — verified via full-repo grep). Does not affect
  `#nday-mgt` or any manually-triggered send (e.g. Send Route Matrix). Do
  not re-enable without the user's explicit go-ahead.
- **`DVIC_TRAINING_VIDEO_ACTIVE`** (env var, default `false`) — hard
  off-switch for the forced-training-video gate on Stage 2+ DVIC
  violations (`dvic.py`), added 2026-07-23. Fully built (upload/serve
  endpoints, `frontend/pages/dvic-training.tsx`, `_dm_blocks()`/
  `record_acknowledgment()` gating) but deliberately inert until a real
  training video has been uploaded via `POST /dvic/training-video` — the
  user does not have the video file yet and also wants to wait for a
  planned server migration. Do not flip on without both a real uploaded
  video and the user's explicit go-ahead.

## No Amazon portal automation — permanent, not a risk tradeoff

A Tier-1 feasibility review (2026-07-20) concluded there is no safe way
to automate logins/downloads against Amazon's operational portal
(`www.logistics.amazon.com`) — doing so risks the primary DSP contract,
and the "juice vs. squeeze" does not net out. **Do not build or propose
Playwright/headless-browser/scraping automation against that portal —
ever — unless the user states Amazon has given written permission.**
This applies to Cortex, Fleet, DVIC, WST, and driver schedule downloads
(all portal-gated). **DOP and Route Sheet are NOT subject to this** —
Amazon posts those directly into a Slack channel each morning, so they
never touch the portal; automating *that* detection (Slack Events API
instead of polling) is fine. For the five portal-gated sources, the
ceiling is a human-in-the-loop flow (reminder → deep link → confirmed
upload page), not removing the human from the download step.

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
