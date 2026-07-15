# SRD Module Architecture v3 — Defined Modules & Development Boundaries

> Discovery: Browse all governance docs in [Governance Index](README.md).
> Supersedes/extends `DSP_Operations_SRD_v2.pdf` for architecture purposes. The PDF still owns business-rule requirement IDs (HR-01, FL-01, etc.); this doc owns module boundaries, table ownership, and the two cross-cutting infrastructure modules identified in the 2026-07-11 architecture review.

---

## 0. Why this doc exists

The 2026-07-11 gap analysis found that the codebase has drifted past its own governance docs, and that several "modules" already share tables, private functions, and Slack logic that were supposed to stay isolated. This doc is the single place that defines:

1. What each module owns (routes, tables, background jobs).
2. What a module may **not** do (boundary rules).
3. Two new cross-cutting infrastructure modules — **Ingest** and **Scheduling (Cadence)** — that most of the 8 SRD business modules depend on but that don't exist as first-class modules yet.

**Ground rule for all modules, existing and new:** one module = one or more backend route files it owns exclusively + the DB tables it writes + (optionally) a background loop. A module may **read** another module's tables through that module's public functions, never through direct private-function imports or raw table access if avoidable. Every new route file must apply an RBAC decorator (see [[Security note]] below) — no route ships ungated.

---

## 1. The 8 SRD business modules — ownership & boundaries

| # | Module | Owns (routes) | Owns (tables) | Reads from | Boundary rule | Status |
|---|---|---|---|---|---|---|
| 1 | **HR & Compliance** | `attendance.py`, `attendance_reports.py`, `manager_accountability.py` | `AttendanceEvent`, `DriverCallout`, `CalloutQueue`, `RingCentralCallLog` | Ingest Module (`DriverScheduleEntry`), Scheduling Module (cadence for reminders) | Owns all discipline/attendance state; must not write to `DriverScheduleEntry` (that's Ingest's table) — read-only there | Partial |
| 2 | **Fleet Management** | `dvic.py` | `DvicSnapshot`, `DvicViolation`, `DvicAcknowledgment` | Ingest Module (`Vehicle`) | DVIC owns inspection/violation state only; vehicle master data stays in Ingest's `Vehicle` table | Partial |
| 3 | **Route & Dispatch** | `route_assignment.py`, `rostering.py`, `rts.py`, `rescue.py` | `DailyRouteAssignment`, `RtsDebrief`, `RescueEvent`, `RescueContribution`, `NightlyRosterReminder`, `DriverShiftDM`, `WaveLeadNotification` | Ingest Module (`DOP`, `Cortex`, `Vehicle`, `RouteSheet`), Driver Quality (`QualityMetricDriver`) | `route_assignment.py` must call a public `quality.get_driver_rank()`-style function instead of querying `QualityMetricDriver` directly (currently violated — see §4) | Mostly done |
| 4 | **Safety Management** | *(none yet)* | *(none yet)* | Netradyne (future) | Not started — first route file should be `routes/safety.py`; must not reuse dead `Incident`/`IncidentPhoto` tables without re-validating their schema first | Not started |
| 5 | **Operations Intelligence** | `cortex_tracking.py` | `CortexSnapshot`, `DriverRoutePerformance` | Ingest Module (`Cortex`), Route & Dispatch (`DailyRouteAssignment`) | Read-only against other modules' tables; owns only pace/snapshot analytics | Partial |
| 6 | **Delivery Tracking** | *(none yet — closest analog is `rts.py`'s debrief)* | *(none yet)* | Route & Dispatch | Not started — when built, should own a `DriverIssueLog` table with reason codes rather than extending `RtsDebrief` | Not started |
| 7 | **Driver Scoring & Coaching** | `quality.py`, `dsp_scorecard_weekly.py` | `QualityMetricSnapshot`, `QualityMetricDriver`, `DspScorecardWeeklySnapshot`, `DspScorecardWeeklyMetric` | Ingest Module (raw CSV/PDF parse output) | Owns all scoring/ranking logic; must expose a public lookup function for Route & Dispatch instead of letting other modules query `QualityMetricDriver` directly | Partial |
| 8 | **Communications Hub** | `slack_interactions.py` (adapter) + calls scattered across other modules | *(no tables of its own — a service layer)* | n/a | **Target state, not current state:** every module should call one Slack adapter instead of instantiating its own Slack client. Currently violated by 7+ files (see §4) | Mostly done (functionally), boundary violated (structurally) |

**Reserved, explicitly separate from anything above:** a future **Driver-Facing Schedule module** (who's working when — reconciling `DailyRouteAssignment` vs. `DriverScheduleEntry` into one source of truth) is intentionally its own future module and must never be merged into the Scheduling (Cadence) Module defined in §3. One is "when is a person working"; the other is "when does the system check/act." Keep them separate in all future design work.

---

## 2. New cross-cutting module: **Ingest**

### 2.1 Current state (as found 2026-07-11)

`api/src/ingest/` already exists as an attempted central ingest package, funneled through `api/src/orchestrator.py`, and it's real and working for 7 of ~15 data sources (DOP, Cortex, Fleet, Route Sheets, Driver Schedule, DVIC, Quality Metrics). It is not, however, actually centralized in practice:

- 6 parsers are stubs returning empty data (variable invoice PDF, fleet invoice, weekly incentive, legacy DSP scorecard route, POD report, all 5 WST CSVs), some reached via routes with commented-out imports (silent 500s).
- The WST/weekly-invoice-audit path has a third, fully separate, actually-working implementation in `weekly_audit_upload.py` that duplicates `ingest/wst/`.
- `Cortex`/`DOP` are written by 3 different code paths with 2 different upsert semantics (delete-and-reinsert by `source_file` vs. upsert by `(schedule_date, source_file)`).
- `orchestrator = IngestOrchestrator()` is a global singleton holding in-memory parse state shared across concurrent requests — a real concurrency hazard.
- Two unrelated functions share the name `parse_route_sheet_pdf`.
- Parsing and notification/business-logic are fused in the same function bodies in places (`ops_ingest.py::_dispatch()`), so there's no clean seam between "data arrived" and "someone got notified."

### 2.2 Target end state

One ingest ledger table, one dispatcher, one parser per data source, reporting modules never parse anything themselves.

**`IngestJob`** — formalizes and extends the existing `OpsIngestJob` table to cover *every* ingest pathway (not just the Slack admin-queue), so it becomes the single audit trail for "did this data arrive, did it parse, is it in the database":

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `source_type` | enum | `dop`, `cortex`, `fleet`, `route_sheet`, `driver_schedule`, `dvic`, `quality_metrics`, `dsp_scorecard`, `pod_report`, `variable_invoice`, `fleet_invoice`, `weekly_incentive`, `wst_delivered_packages`, `wst_service_details`, `wst_training_weekly`, `wst_unplanned_delay`, `wst_weekly_report` |
| `trigger_channel` | enum | `manual_upload`, `slack_daily_notify_watch`, `slack_ops_admin_queue` |
| `source_file_name` | string | |
| `source_file_ref` | string | local path or Slack file ID |
| `received_at` | timestamp | |
| `status` | enum | `received`, `queued`, `parsing`, `parsed`, `stored`, `failed`, `rejected` |
| `parser_key` | string | e.g. `ingest.dop.parse_dop_excel` — maps to a registered callable, never a hardcoded import per caller |
| `target_table` | string | e.g. `DOP` |
| `write_mode` | enum | `upsert_by_date`, `delete_reinsert_by_source_file` — **pick exactly one per `source_type` and document it here**, not per caller |
| `rows_written` | int | |
| `error_detail` | text, nullable | |
| `reviewed_by` / `reviewed_at` | nullable | for admin-queue jobs requiring human approval |

**Dispatcher rule:** every ingest pathway (manual upload endpoint, `daily_notify` Slack watcher, `ops_ingest` admin queue) creates an `IngestJob` row first, then calls **one** `ingest.dispatch(job)` function that looks up `parser_key`, invokes the registered parser, writes via the documented `write_mode` for that `source_type`, and updates job status. No route file parses a file inline — it only ever creates a job and calls dispatch.

### 2.3 Cleanup migration plan (priority order)

1. Fix the `IngestOrchestrator` singleton concurrency hazard — scope parse state to the request/job, not a shared module-level instance.
2. Retire `api/src/ingest/wst/*.py` stubs; make `weekly_audit_upload.py`'s working WST/invoice parsing the canonical implementation, called through the same dispatcher.
3. Resolve the `parse_route_sheet_pdf` naming collision — rename one (they do different things: bag/overflow extraction vs. driver-van assignment extraction).
4. Fill in or explicitly delete the remaining stub parsers (variable invoice PDF, fleet invoice, weekly incentive, legacy DSP scorecard route, POD report) — don't leave silent-500 dead endpoints live.
5. Pick one `write_mode` for `Cortex`/`DOP` and migrate the 3 existing callers (`uploads.py`, `ops_ingest.py`, `daily_notify.py`) to it.
6. Split parsing from notification logic in `ops_ingest.py::_dispatch()` — dispatch should end at `status=stored`; a separate step (owned by the relevant business module, e.g. `rostering.py`) reacts to the new data.

---

## 3. New cross-cutting module: **Scheduling (Cadence)**

**This is the internal timing brain — when the system expects inputs and when it fires outputs. It is explicitly NOT the driver-facing "who's working when" schedule (see §1 reserved note). Never conflate the two.**

### 3.1 Current state (as found 2026-07-11)

All 12 background loops in `api/main.py` independently hardcode their own hour/minute/day-of-week window against `datetime.now(PACIFIC)`. Examples of cadence rules currently buried in code: DOP/Cortex ingest window 8-10am; DVIC reminder throttle 3-6pm; scorecard reminder throttle Wed 12:30-5pm; callout digest window 8:23-8:37am; nightly roster reminder 19:00-19:10; `mgt_reminders.py`'s three deadlines (Cortex/Fleet by 9am, Okami forecast by 3:30pm, driver schedule by 7:30pm). There is no single place to view or change an operational deadline, and no audit trail of what fired, what was skipped, or what was late.

### 3.2 Target end state

**`ScheduleRule`** — one row per operational cadence expectation:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `rule_key` | string, unique | e.g. `dop_ingest_window`, `dvic_reminder_window`, `scorecard_upload_deadline`, `fleet_invoice_expected_day` |
| `owning_module` | string | which business module this rule belongs to, e.g. `route_dispatch`, `fleet_management`, `driver_scoring` — for readability/reporting only, not an FK |
| `description` | text | human-readable, e.g. "DVIC pre-trip report expected daily" |
| `cadence_type` | enum | `daily`, `weekly`, `monthly`, `custom` |
| `day_of_week` | int, nullable 0-6 | for `weekly` cadence, e.g. scorecard = Wednesday |
| `day_of_month` | int, nullable 1-31 | for `monthly` cadence, e.g. fleet invoice due day |
| `window_start` / `window_end` | time | e.g. `08:00` / `10:00` |
| `timezone` | string | default `America/Los_Angeles` |
| `action_type` | enum | `ingest_expectation` (expect a file to arrive), `reminder_check` (nag if missing), `digest_post`, `dm_send` |
| `handler_ref` | string | maps to a registered callback in code (e.g. `daily_notify.check_and_notify`) — the rule table drives **when**, the code registry still owns **what** |
| `grace_period_minutes` | int, nullable | how late is "late" before escalating |
| `escalation_action` | string, nullable | e.g. "notify #nday-mgt" |
| `enabled` | bool | |
| `last_fired_at` | timestamp, nullable | dedup guard so a rule doesn't fire twice in one window |
| `last_fired_status` | enum, nullable | `fired`, `skipped`, `late`, `missing` |

**`ScheduleEventLog`** — append-only audit trail, one row per evaluation/fire:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `rule_id` | FK → `ScheduleRule` | |
| `evaluated_at` | timestamp | |
| `status` | enum | `fired`, `skipped`, `late`, `missing` |
| `detail` | text, nullable | e.g. "DOP not received by end of window" |

**Dispatcher rule:** one loop (replacing all 12 in `main.py`) ticks every 60s, loads all `enabled` rules, evaluates each rule's window against `datetime.now(rule.timezone)`, and if the rule should fire and hasn't already fired for this window (per `last_fired_at`), invokes the registered `handler_ref` callable and writes a `ScheduleEventLog` row. Each business module registers its callback with the dispatcher at startup instead of running its own `asyncio.create_task` loop.

### 3.3 Migration plan (priority order)

1. Stand up `ScheduleRule` + `ScheduleEventLog` tables and the single dispatcher loop.
2. Migrate the simplest, most self-contained loops first: `_dvic_reminder_loop`, `_dsp_scorecard_reminder_loop`, `_callout_queue_loop`, `_nightly_roster_reminder_loop` — each becomes one `ScheduleRule` row + the existing handler function registered as `handler_ref`.
3. Migrate `mgt_reminders.py`'s three internal deadlines into three separate `ScheduleRule` rows (Cortex/Fleet by 9am, Okami forecast by 3:30pm, driver schedule by 7:30pm) — this alone gives visibility into three previously-invisible SLAs.
4. Migrate `_daily_notify_loop` and `_ops_ingest_scan_loop` last — they're the most complex (continuous scan vs. windowed check) and most load-bearing; do them once the simpler pattern is proven.
5. Retire the per-loop hardcoded hour/minute checks in `main.py` once each is migrated; `main.py` should end up starting exactly one scheduling dispatcher task, not 12.
6. Add a small `/schedule` admin view (future, not scoped here) so ops can see the whole operational calendar and last-fired status in one place — this is the natural payoff of centralizing the table.

---

## 4. Cross-module boundary violations to fix (carried over from 2026-07-11 review)

- `attendance_reports.py` imports private internals (`_event_to_dict`, `_driver_points_summary`) from `attendance.py` — needs a public interface instead.
- `weekly_audit_upload.py` and `weekly_audit.py` overlap on the same tables without a clear ownership line.
- `route_assignment.py` queries `QualityMetricDriver` directly instead of through a `quality.py`-owned function.
- Slack posting logic is reimplemented in 7+ files instead of going through one adapter (`slack_interactions.py`'s own docstring says it should be the one integration point).
- Two live generations of driver/vehicle/assignment schema coexist (`Driver`/`Vehicle`/`Assignment` vs. `DriverRosterEntry`/fleet `Vehicle`/`DailyRouteAssignment`) with nothing marked deprecated; `Incident`, `IncidentPhoto`, `PerformanceMetric`, `VanInspection` are dead tables.

**Security note:** RBAC decorators (`require_permission`/`require_role` in `api/src/authorization.py`) are only applied to 5 of 24 route files today (the financial-upload surface). Every module boundary defined above assumes RBAC will be applied consistently — treat "no RBAC decorator" as a defect on any new or touched route file, not an acceptable gap.

---

## 5. Summary: full module list going forward

| Module | Type | Status |
|---|---|---|
| HR & Compliance | SRD business module | Partial |
| Fleet Management | SRD business module | Partial |
| Route & Dispatch | SRD business module | Mostly done |
| Safety Management | SRD business module | Not started |
| Operations Intelligence | SRD business module | Partial |
| Delivery Tracking | SRD business module | Not started |
| Driver Scoring & Coaching | SRD business module | Partial |
| Communications Hub | SRD business module | Mostly done, structurally leaky |
| **Ingest** | Cross-cutting infrastructure | Partially built, needs cleanup (§2) |
| **Scheduling (Cadence)** | Cross-cutting infrastructure | Not started as a module; logic exists scattered (§3) |
| *Driver-Facing Schedule* | *Reserved for future — do not build yet, do not merge into Scheduling (Cadence)* | *Not started* |
