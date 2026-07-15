# Build Queue — 2026-07-15 Session Handoff

Working list assembled 2026-07-15 to review one item at a time. For each
item: mark a decision (Build now / Delay / Decline / Needs more info),
add notes, and check off sub-steps as they're completed. This file is
the single source of truth for "what's left" — update it as we go
rather than re-deriving status from scratch each session.

**Decision key:** 🟢 Build now · 🟡 Delay · 🔴 Decline · ⚪ Needs more info (not yet decided)

---

## Cross-cutting facts (checked 2026-07-15, don't re-derive from stale memory)

- **Driver Slack-linking: 61 of 102 active drivers linked** (verified live against
  production `nday-om.onrender.com/drivers`). 41 remain unlinked — a
  re-run of the SSN/Slack import against updated export files would
  help, but this is no longer a hard blocker for driver-DM work.
- **Production is not fully current.** It already has the driver-profiles
  module (hence the real Slack-linking data above), but does **not**
  have `safety_events.py` or `okami_capacity.py` yet — both 404 there.
  All of today's Okami work is still uncommitted locally (`git status`
  confirmed). Nothing "restarts" into place — it needs an explicit
  commit → push → manual Render redeploy (Render does not auto-deploy).
- **Correction:** `SLACK_NOTIFICATIONS_ACTIVE=true` and `ROSTERING_ACTIVE=true`
  in production (confirmed via Render env var list) — the earlier "both
  false" note was stale. `DRIVER_DM_ACTIVE` is unset (defaults `false`) —
  still the real gate on driver-facing DMs.
- **Security — before the next commit:** `api/users.json` (tracked, modified)
  now contains what looks like a real admin password in plaintext, in a
  confirmed-public repo. Confirm whether it's real before it ships.
- **Security — before the next commit:** `document_routing.py`'s new
  `dispatch_staff` role hardcodes six real names + Slack IDs directly in
  source, violating the policy stated two lines above it in the same
  file. Needs to move to an env var or DB seed like `owner`/`hr` did.

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

There's also apparently a **separate `slack_dispatch_home.py`** (referenced
by `document_routing.py`'s new `dispatch_staff` role and an
`is_dispatch_staff` import) that hasn't been reviewed yet — a
dispatch-facing counterpart to this driver-facing tab. Needs its own
look before this item can be called fully assessed.

- [ ] Review `slack_dispatch_home.py` (exists, unreviewed)
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

- [ ] Get remaining 41 drivers linked (re-run import against updated
      export files, if available)
- [ ] Define Performance Coaching Message criteria and build the module
- [ ] Define real ACE Eligibility criteria
- [ ] End-to-end test with a handful of the 61 already-linked drivers
- [ ] Explicit sign-off, then flip `DRIVER_DM_ACTIVE` +
      `SLACK_NOTIFICATIONS_ACTIVE`

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

**Status:** Built and verified this session — draft submit, Finalize,
FRT/DA/van coverage checks, Slack notifications to `#nday-mgt` +
`#nday-fleet` + Jayson/Tamra. Logic confirmed correct via direct backend
tests and a stubbed-Slack-client test; full UI flow confirmed via
Playwright. **Not deployed** — see cross-cutting facts above.

- [ ] Commit, push, and manually redeploy on Render
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
