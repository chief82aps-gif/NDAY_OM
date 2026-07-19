# Build Queue — 2026-07-15 Session Handoff

Working list assembled 2026-07-15 to review one item at a time. For each
item: mark a decision (Build now / Delay / Decline / Needs more info),
add notes, and check off sub-steps as they're completed. This file is
the single source of truth for "what's left" — update it as we go
rather than re-deriving status from scratch each session.

**Decision key:** 🟢 Build now · 🟡 Delay · 🔴 Decline · ⚪ Needs more info (not yet decided)

---

## Cross-cutting facts (checked 2026-07-17, don't re-derive from stale memory)

- **Session of 2026-07-16/17 shipped**: generalized auto-ingest (no more
  manual "click ingest" for 7 safe file types — `dvic`, `driver_schedule`,
  `fleet`, `quality_csv`, `safety_events`, `dsp_scorecard`,
  `tenured_workforce` — see `run_ops_auto_ingest()` in `ops_ingest.py`);
  Route Sheet persistence fix (`RouteSheetEntry` — van/wave/stage data no
  longer lost when DOP lands late in the same day); Cortex-authoritative
  route-code reconciliation (`check_route_code_reconciliation()`, >10%
  mismatch warns `#nday-mgt`); "Re-Run Route Assignments" and "Re-Publish
  Showtime Matrix" Dispatch Home buttons; the new Tenured Workforce
  ingest pipeline + Friday-COB reminder; the new Driver Individual
  Scoring module (`driver_scoring.py`). Full detail in
  `Governance/DOP_ROUTE_SHEET_INGEST_RULES.md` and the new
  `Governance/DRIVER_SCORING_RULES.md`.
- **Driver Individual Scoring is live via API but has no visual list
  yet.** `GET /driver-scoring/scores` returns Overall/Safety/Quality/
  Attendance + color (92%/90% thresholds) + ranking/bonus eligibility
  for every driver in the latest quality snapshot. User asked to see a
  real list built from production data — blocked only on confirming the
  scores endpoint's deploy went live, not on any further code work.
- **Still open — `im:write` OAuth scope missing.** Confirmed live in
  Render logs: `conversations.open` fails with `missing_scope`, which
  blocks "Re-Run Route Assignments"'s DM-back-to-the-clicker step (the
  rebuild itself still runs and posts to channels fine). Needs: add
  `im:write` in the Slack app's OAuth & Permissions page, reinstall the
  app to the workspace, update `SLACK_BOT_TOKEN` in Render if it
  rotates.
- **Still open — `DISPATCH_HOME_CHANNEL_ID` misconfigured.** Default
  `C0BHGL7DLLC` returns `channel_not_found`, blocking the Dispatch Home
  audit-log post step. Needs the correct channel ID confirmed and the
  bot invited as a member before it'll post.
- **Fixed 2026-07-17 — root cause of "usernames don't seem to be
  working" found and fixed.** `api/users.json` (the real staff account
  store) was `.gitignore`'d and not part of any commit in `main`, so it
  never shipped with a Render deploy — any account only living there
  vanished on the next redeploy, and the hardcoded admin fallback
  password didn't match `LOGIN.md`. `auth.py` now reads/writes the DB's
  existing (previously unused) `User` table with bcrypt-hashed
  passwords; a one-time seed migrates every account that used to live in
  the JSON file. Added Invite User / Reset Password buttons on Slack
  Dispatch Home (tokenized set-password links via
  `frontend/pages/set-password.tsx`) — same `im:write` scope gap above
  blocks the DM-to-invitee step until that scope is added, but the
  audit-log post + DM-to-clicker fallback always carries the link.
- **Fixed 2026-07-17 — Okami finalize Slack message reformatted** to
  match the dashboard's own card layout (green "Logged" card + neutral
  "Finalized" card with OK/MET badges), per user request.

- **Driver Slack-linking: 84 of 104 active drivers linked** (verified live
  2026-07-16). Up from 61/102. A deterministic email-bridge matcher
  (`best_slack_match_via_associates()` in `driver_matching.py`) is built,
  deployed, and locally verified to resolve **10 more** with 100%
  confidence and zero conflicts — but **has not actually been run against
  production yet** (`POST /drivers/import-ssn-slack?dry_run=false` with
  both `slack_file` + `associate_file`). That's a live action item, not
  done. Of the remaining 20: 2 (Adrian Shelton, daniela gonzalez) are
  confirmed active employees who just haven't joined Slack; 1 (Diana
  Esmeralda Vazquez) was terminated (now marked `is_active=False` via the
  new `/drivers/{id}/terminate` endpoint); 7 are genuinely unexplained —
  not in the Amazon associate export at all, active or inactive, and never
  seen on a real schedule (`source: adp_import`, `last_seen_on_schedule:
  None`) — likely stale ADP-import leftovers, not real current drivers.
- **Production is now fully current** — `okami_capacity.py`, `safety_events.py`,
  crash-report v2 (S3 evidence, Claude sanitization, sequential approval
  chain), the single-driver edit UI (`/drivers`), and everything else
  built through 2026-07-16 is live and deploy-verified. The "not fully
  current" note from 2026-07-15 no longer applies.
- `SLACK_NOTIFICATIONS_ACTIVE=true`, `ROSTERING_ACTIVE=true` in production.
  `DRIVER_DM_ACTIVE` **was found flipped to `true` in production on
  2026-07-16** (not set by anyone in this session — cause unconfirmed) and
  flipped back to `false`. Given what §"Driver DM readiness" below found,
  treat any future `true` sighting as worth investigating, not assuming
  intentional.
- **Driver DM readiness — real progress, not there yet.** Two real bugs
  found and fixed this session, both would have caused problems the
  moment `DRIVER_DM_ACTIVE` went live:
  1. `daily_notify.py`'s own driver-DM pipeline (`send_all_dms`,
     `send_sweeper_notifications` — separate from `rostering.py`'s) had
     **no `DRIVER_DM_ACTIVE` gate at all**. Fixed.
  2. `build_daily_assignments()` deleted and recreated the whole day's
     `DailyRouteAssignment` rows on every scheduler tick (every ~10 min,
     8-10 AM), resetting `dm_sent`/`ack_token` each time — would have
     re-sent every driver's DM on every rebuild. Converted to an upsert
     that preserves notification state; also fixed a related bug where a
     scheduler replay with empty `pdf_data` was blanking out
     previously-known van/wave data.
  Still open: the ~20 unlinked drivers above, and no live end-to-end test
  has ever been run with `DRIVER_DM_ACTIVE=true` against a real driver.
- **Two independent, non-communicating DOP/Route-Sheet detection
  pipelines exist**: `ops_ingest.py` (content-based classification,
  `OpsIngestJob` table, feeds the 9 AM `mgt_reminders.py` reminder) vs.
  `daily_notify.py` (was filename-keyword-based — `.xlsx` only, missed a
  real `.csv` DOP upload on 2026-07-16; now fixed to accept `.csv` too;
  `SlackIngestLog` table; feeds `DailyRouteAssignment`/driver DMs/summary
  matrix). See `Governance/DOP_ROUTE_SHEET_INGEST_RULES.md` — don't
  re-diagnose a miss in one pipeline as a bug in the other.
- **New this session**: Cortex-authoritative route-code reconciliation
  (`check_route_code_reconciliation()` — >10% DOP-or-Route-Sheet-vs-Cortex
  mismatch posts a one-time `#nday-mgt` warning naming the bad file);
  "Re-Run Route Assignments" Dispatch Home button (rebuilds today's
  assignments, DMs only what actually changed — new/changed/removed —
  since a driver was last told, via a new `notified_snapshot` column;
  backgrounded via FastAPI `BackgroundTasks` since it can exceed Slack's
  3-second ack window); `/drivers/{id}/terminate` (nothing previously set
  `is_active=False` anywhere, so "Remove Terminated Employees" on
  Dispatch Home had no way to ever find anyone); single-driver edit UI on
  `/drivers` (fix one bad Slack link/phone/PIN without a full re-import).
- **Security — status unconfirmed, re-check before next commit:**
  `api/users.json` plaintext-password concern and `document_routing.py`'s
  `dispatch_staff` hardcoded names — both were flagged 2026-07-15; the
  latter appeared fixed (reads `DOC_ROUTING_DISPATCH_STAFF_SLACK_IDS` env
  var) as of a system reminder mid-session, but re-verify both directly
  rather than trusting this note.

---

## 1. Driver Home Page — Slack App Home tab

**Status: CORRECTED 2026-07-15 — this is already substantially built,
not "zero built" as first reported.** My earlier research agent missed
`api/src/routes/slack_home.py` (514 lines, uncommitted), which already
implements: the `/slack/events` endpoint (`app_home_opened` →
`views.publish`, signature-verified), a full Home tab view (driver
standing/quality metrics, RTS/Call Out/Report Crash/Report Injury/
Incident Report/Request Time Off buttons), quick-capture modals for
crash/injury/incident (opens a lightweight record, notifies dispatch/
ops/HR via `document_routing.resolve_recipients`, hands off to the
existing `/crash-report` flow for full paperwork), and a full RTO modal
→ `TimeOffRequest` record → notification flow. It extends the
**existing** bot (shares `slack_interactions.py`'s signature
verification and dispatcher) — the "new app vs. existing app" question
is already answered in code.

**Fixed today:** it had no `DRIVER_DM_ACTIVE` gate, unlike every other
driver-facing send in this codebase — meaning it would have gone fully
live (real DMs, real HR/ops notifications) the moment the Home tab
feature is turned on in Slack's app config, with zero staged rollout.
Added the same gate `rostering.py`/`dvic.py` use: while off, the tab
shows a "Coming Soon" placeholder and the DM/notification-sending
handlers no-op.

**Update:** `slack_dispatch_home.py` (the dispatch-facing counterpart) is
now extensively built and reviewed — quick-link buttons (OKAMI, Rescue,
Crash Report), "Remove Terminated Employees" (now actually functional,
see cross-cutting facts), "Preview Driver Home", and "Re-Run Route
Assignments" (rebuilds today's assignments, DMs drivers only what
changed since they were last told).

- [x] Review `slack_dispatch_home.py`
- [ ] **Security: move `document_routing.py`'s `dispatch_staff` role
      off hardcoded real names/Slack IDs** before this gets committed —
      violates the file's own stated policy, in a confirmed-public repo
- [ ] Enable the Home tab feature + Events API subscription
      (`/slack/events`) in the existing bot's config at api.slack.com
- [ ] Confirm/add required OAuth scopes (`im:write` for the DM helper,
      plus whatever `views.publish`/`views.open` need)
- [ ] End-to-end test with `DRIVER_DM_ACTIVE=true` against a couple of
      the 61 already-linked drivers before wider rollout
- [ ] Decide exact HR field sets vs. the spec's full HRM/OPS form
      library (current implementation is intentionally a lightweight
      free-text capture, not the compliant full form)
- [ ] Start-of-shift bot DM with deep link to the Home tab (not built)
- [ ] Metric-cards-only items from the original spec not yet in
      `build_home_view_blocks()`: Route/Van/Stops/Wave header cards,
      shift-state badge, today's checklist, resources section — the
      current view leads with quality standing instead

**Decision:** ⚪
**Notes:**

---

## 2. Driver Assignment DM (route info + performance + coaching)

**Status:** Route/logistics half is live and tested (Route, Van, Staging,
Showtime, Wave, Est. Return, Wave Lead, Acknowledge button — validated
via the interim Driver Summary Matrix). Performance Coaching Message
(1 improvement area + 1 positive reinforcement) is explicitly deferred
to its own module — not started. ACE Eligibility shows a static "TBD"
placeholder — no real criteria defined.

- [ ] Run the already-built associate-bridge matcher against production
      (`dry_run=false`) to pick up 10 more confirmed links; investigate
      the 7 unexplained unlinked names before assuming they're stale
- [ ] Define Performance Coaching Message criteria and build the module
- [ ] Define real ACE Eligibility criteria
- [ ] End-to-end test with a handful of already-linked drivers — never
      actually done; two real send-side bugs were found and fixed this
      session without ever having sent a real test DM (see cross-cutting
      facts) — do this before flipping the flag, not after
- [ ] Explicit sign-off, then flip `DRIVER_DM_ACTIVE`
      (`SLACK_NOTIFICATIONS_ACTIVE` is already `true`)

**Decision:** ⚪
**Notes:**

---

## 3. Asana Sync Button (hiring Chrome extension) — launch

**Status:** Built and actively iterated (`chrome-extension/`, Manifest
v3, v0.1.0, ~13 commits dated 2026-07-13/14). Exists only as unpacked
source — not installed in any browser, not configured anywhere.

- [ ] Set backend secrets: `CANDIDATE_SYNC_KEY` (shared secret) and
      `ASANA_API_TOKEN` — endpoint hard-fails 500 without these
- [ ] Set `ASANA_HIRING_PROJECT_GID` so the correct board/section
      resolves (otherwise falls back to slower name lookup)
- [ ] Load the extension into the real hiring person's Chrome
      (`chrome://extensions` → Developer mode → Load unpacked), or
      package/publish it properly
- [ ] Configure the extension's Options page (API base + extension key)
- [ ] Run one real end-to-end sync against a live Indeed candidate
- [ ] Write a one-page install/usage doc (none exists today)
- [ ] Google Contacts OAuth — optional, degrades gracefully if skipped

**Decision:** ⚪
**Notes:**

---

## 4. Okami Validation Process (dispatch dashboard)

**Status: DEPLOYED 2026-07-15, live in production.** Draft submit,
Finalize, FRT/DA/van coverage checks, Slack notifications to `#nday-mgt`
+ `#nday-fleet` + Jayson/Tamra, and the "low driver buffer" messaging fix
(distinguishes a real route-coverage shortfall from just being under the
10% buffer target) are all live. Dispatch Home has a quick-link button.

- [x] Commit, push, and manually redeploy on Render
- [ ] Run one real test against the actual `#nday-mgt`/`#nday-fleet`
      channels (only a stub client has been tested so far)
- [ ] Build a small UI for the buffer-% settings (currently API-only:
      `GET`/`PUT /okami-capacity/settings`)
- [ ] Phase 2 (deferred by earlier decision): auto-generate the
      "drivers available for extra shift" list on an FRT miss

**Decision:** ⚪
**Notes:**

---

## 5. DVIC Discipline DMs (escalating discipline ladder)

**Status:** Logic fully built and looks solid — 4-stage ladder,
per-stage messaging, stage-4 formal write-up, acknowledgment sync,
DB-backed throttle state (already fixed from the earlier in-memory-dict
incident). **No background loop drives it** — only manual trigger
endpoints exist (`POST /dvic/send-dm/{tid}`, `send-all-dms`). Never
tested with a real send.

- [ ] Decide: automatic background loop, or stay manually triggered?
- [ ] If automatic, wire a loop in `main.py` matching the existing
      pattern
- [ ] End-to-end test with `DRIVER_DM_ACTIVE=true` against a few of the
      61 already-linked drivers
- [ ] Explicit sign-off before any real discipline DM goes out

**Decision:** ⚪
**Notes:**

---

## 6. Safety Violation Review/Dispute Workflow (`C0ADM0M5UNQ`)

**Status:** Net-new — no Governance doc or code references this channel
today. An earlier planning note (`project_safety_violations.md`)
sketched a similar idea around watching raw text in a different leaders
channel, but we've since built a better data source instead:
`safety_events.py`'s structured Netradyne CSV ingest (`SafetyEvent`
model — driver_name, video_link, metric_type, event_at, etc.). None of
the interactive review workflow exists yet, but the button/DM/ack
pattern is well-established elsewhere (`slack_interactions.py`'s
`action_id` routing, used by the rescue tracker and DVIC's Acknowledge
buttons; `dvic-ack.tsx` is a strong template for a driver-facing
acknowledgment page).

- [ ] **Decide the trigger source**: watch channel `C0ADM0M5UNQ`
      directly, or key off new `SafetyEvent` rows from the CSV ingest?
      These are genuinely different designs.
- [ ] Add review/dispute status fields to `SafetyEvent` (or a companion
      table) — currently has none (no `status`/`reviewed_by`/`disputed`)
- [ ] Post to `#nday-mgt` with Confirm / False-Flag buttons per new
      violation
- [ ] Build the dispatch review handlers (confirm → proceed; false-flag
      → dismiss, no DM)
- [ ] Build the driver DM + Acknowledge button (reuse the existing
      `dvic_ack`-style pattern)
- [ ] Record the validation outcome + acknowledgment on the record
- [ ] Write a Governance doc for this once built (none exists)
- [ ] Note for later, not now: forced training video/quiz hook

**Decision:** ⚪
**Notes:**

---

## 7. Other outstanding TODOs (from `UPGRADE_BACKLOG.md`)

- [ ] Populate real driver PINs (all still default `1234`)
- [ ] Invite the ~24 remaining drivers to `#nday-team-room`
- [ ] Loadout van timing dashboard (van-in/van-out tracking)
- [ ] Playwright/RPA Cortex auto-download (7:30 AM PT)
- [ ] Daily reports checklist widget (submitted vs. outstanding)
- [ ] Admin role-management UI
- [ ] Driver performance analytics dashboard
- [ ] Telnyx SMS account + 10DLC registration (needs Jayson to start —
      1–2 week carrier approval lead time)
- [ ] Screenshot-capture Chrome extension (separate from the hiring
      one — for the daily Cortex vs. WST audit)
- [ ] Phase 3/4 items (mobile app, incident reporting, van inspection,
      etc.) — long-horizon, not urgent

**Decision:** ⚪
**Notes:**

---

## 8. Nightly Summary (end-of-day recap to #nday-mgt)

**Status:** Not started — no such loop or module exists today.

- [ ] Define what feeds "accomplished / remaining / focus areas" (which
      tables or checks get summarized)
- [ ] Build a nightly loop (same cron pattern as other `main.py` loops)
      that posts to `#nday-mgt`
- [ ] Decide exact content shape (today's completions, tomorrow's open
      items, an incomplete-checklist digest?)

**Decision:** ⚪
**Notes:**

---

## 9. Good Morning Team-Room Message

**Status:** Not started. Work anniversaries are feasible today
(`DriverRosterEntry.hire_date` already exists). Birthdays are **not** —
no birthdate field exists anywhere in the schema.

- [ ] Decide birthday data source (new field, HR import, manual entry?)
- [ ] Work-anniversary logic off existing `hire_date`
- [ ] New-driver detection (recent `hire_date` or first schedule/roster
      appearance)
- [ ] Build the morning loop + message composer, post to
      `#nday-team-room`
- [ ] Decide tone/content variety (fixed template vs. rotating pool)

**Decision:** ⚪
**Notes:**

---

## 10. Driver Individual Scoring — visual list + home screen display

**Status:** Formula, ingest pipeline, and API endpoint are built and
deployed (`Governance/DRIVER_SCORING_RULES.md`, `driver_scoring.py`,
`tenured_workforce.py`). Not yet built: any way to actually look at the
scores besides calling the raw endpoint.

- [ ] Once the `/driver-scoring/scores` deploy is confirmed live, pull
      real production data and build the visual list the user asked for
      (Overall/Safety/Quality/Attendance, color-coded 92%/90%) — this was
      requested and is the next concrete step, just paused for EOD wrap-up
- [ ] Decide where this list lives: a dispatch-facing page (`/driver-
      scoring` in the frontend?), a Slack digest, or both
- [ ] Driver-facing version for the Slack Home tab (see item 1) — show a
      driver their own score + bonus-eligible flag, not the full roster
- [ ] Bonus **dollar amount** calc/messaging ("here's what you're
      leaving on the table by missing the bonus") — not started, needs a
      bonus-per-tier dollar figure from the user first
- [ ] Add `transporter_id` to `DriverRosterEntry` — flagged by the user
      as the right long-term primary key (survives payroll/email
      changes); today's scoring join goes through
      `QualityMetricDriver.transporter_id` instead, which works but means
      two tables carry the same identity concept
- [ ] Manual correction path for a bad Tenured Workforce row (agreed:
      reuse the existing single-driver edit page pattern, not a new
      dedicated button) — low priority, the report is authoritative

**Decision:** ⚪
**Notes:**

---

## 11. Route Bands, Roster Priority Lists, Auto-Assign Refinement, Coaching DMs

**Context (2026-07-19):** user wants to eventually recommend which
performance "band" a driver belongs in and refine auto-assignment using
area-level route history, get driver DMs turned on (the stated primary
goal), and build encouragement-only coaching messaging — plus a not-yet-
revealed pay idea. Explicit sequencing decision: route-band tracking
first, everything else after.

- [x] **Route Bands (`route_bands.py`)** — built and deployed-pending.
      Calibrates geographic-proximity clusters from gaps in route-code
      numbers (no real lat/long data exists anywhere in this system —
      `Cortex.zone` is always `None`). `POST /route-bands/calibrate`,
      `GET /route-bands`, `GET /route-bands/report` (band → drivers who
      ran it → their latest quality score). Uses already-accumulated
      Cortex history, not a fresh week of new data.
- [ ] **Validate the calibration against real geography** — once
      deployed, run `/route-bands/calibrate` against real production
      Cortex history and sanity-check the resulting bands against an
      actual station route map (user mentioned possibly screenshotting
      the map to cross-check). Tune `gap_multiplier`/`lookback_days` if
      the bands don't look right.
- [ ] **Per-week attribution** (deferred, not per-driver-latest-score) —
      the report currently uses each driver's *most recent* quality score
      regardless of which week they ran a given band. A real per-week
      join needs Amazon's own week-label boundaries reconciled against
      calendar dates first (`QualityMetricDriver.week` is a string like
      `"2026-W28"`, not derived from a date anywhere in this codebase).
- [ ] **Roster priority list** — not started. The system recommends which
      band/tier a driver belongs in based on overall historical
      performance. Needs `route_bands.py`'s data to mature first before
      the actual recommendation logic can be designed meaningfully.
- [ ] **Auto-assignment refinement** — `route_assignment.py`'s
      `_auto_assign()` today ranks purely by quality standing/score (no
      area signal). Wiring in route-band history is a future pass, not
      started.
- [ ] **Driver coaching DMs — encouragement-only, never negative.**
      Confirms/extends the already-documented-as-"planned, not built"
      Performance Coaching Message
      (`Governance/DRIVER_DM_CONTENT_RULES.md`). New explicit constraint
      from this session: framing must always mentor/encourage, never
      read as a penalty — needs concrete message-criteria design before
      building, not started.
- [ ] **Getting `DRIVER_DM_ACTIVE` turned on is the stated primary goal**
      — this is the same still-open item tracked in section 2 above
      (end-to-end test with real linked drivers, then explicit sign-off)
      — not a new item, just re-confirmed as the priority.
- [ ] **$1/hour bonus-rate idea — logged only, not built.** Per explicit
      2026-07-19 decision: drivers who'd earn the driver-scoring
      high-performer bonus (see `Governance/DRIVER_SCORING_RULES.md`)
      would eventually get a $1/hour bump to their base rate. User does
      **not** want this communicated to drivers yet ("we won't tell them
      that yet") and asked to log it as a roadmap item rather than build
      the calculation now — this is intentionally not implemented
      anywhere. Revisit deliberately later; this touches real pay.

**Decision:** 🟢 (route bands — built) / ⚪ (everything else in this section)
**Notes:**
