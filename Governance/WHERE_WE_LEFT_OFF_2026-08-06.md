# Governance: Where We Left Off (August 6, 2026)

## Summary

Long overnight session. Started as routine follow-up (mentoring DMs, progress
DM tone, TWF recalibration) and turned into a real production incident night:
a broken Slack signing secret silently killed every driver button/team-room
message for 13+ hours, a chain of ingest bugs (date resolution, file
mismatching, a busy-channel scan blind spot) were found and fixed one by one,
and — after a fast-moving cluster of driver complaints — **every driver-facing
DM and `#nday-team-room` post was deliberately paused** pending review. The
back half of the night built new features on top of that paused, stable
baseline: an owner-meeting scheduler, a wave-leads standings channel, an
arrival re-nudge, a real EOD second reminder, and a full ad-hoc survey/quiz
module. Everything is deployed and confirmed live. **Nothing is currently
sending to drivers or the team room** — that's a deliberate, standing decision
from tonight, not an accident.

---

## 1. Incidents found and fixed (chronological)

1. **Stuck `OpsIngestJob` rows** — jobs orphaned mid-flight at
   `status="ingesting"` (most likely a redeploy landing between the status
   write and dispatch finishing) were invisible to the retry loop forever.
   Widened `run_ops_auto_ingest()` to also retry stale `ingesting` rows.
2. **CDF ingest crash** — a duplicate `Delivery Group ID` within one uploaded
   file crashed the insert; the exception handler then crashed *again* trying
   to log it (touched an ORM attribute on a session already broken by the
   failed flush), so the job never even got marked `error`. Fixed both.
3. **`SLACK_SIGNING_SECRET` mismatch** — the big one. Render's copy of the
   secret didn't match Slack's current value, so every inbound Slack request
   (button clicks, channel messages) was silently rejected for 13+ hours
   starting 8:01 AM. This is the real reason drivers couldn't acknowledge
   shifts and the team-room monitor caught nothing. Fixed by updating the
   value on Render.
4. **Safety-violation reviews posting to a dead channel** — `channel_not_found`
   retried every 60s indefinitely, no backoff. Redirected to `#nday-mgt`.
5. **Showtime date-resolution bug** — `_resolve_selected_schedule_date()`
   matched the nightly schedule file against its own upload-day timestamp
   instead of the next day, so every night's ingest was (re)targeting an
   already-elapsed day. Fixed; then the fix appeared to "come back" the next
   morning — turned out the specific ingest that ran that evening had
   completed *before* the fix deployed, so its stale result was still driving
   sends. Real lesson, now written into `SHOWTIME_MODULE_RULES.md`'s Rule One:
   a date-resolution fix isn't verified until a real subsequent ingest proves
   it, not a unit test alone.
6. **`requirements.txt` drift** — the root file (what Render actually builds
   from) was missing `anthropic`/`boto3`/`qrcode`, and a `psycopg2-binary`
   pin copied over from the other copy had no Python 3.14 wheel, breaking a
   clean-cache build. Synced and unpinned.
7. **Cortex file mismatched as Cortex** (see below, #8) also broke the "All In"
   cadence post; recovering the correct file fixed both.
8. **Cortex/DVIC file-matching bug — real data corruption.** `scan_cortex_channel()`
   matched any filename containing "dlv3," so a DVIC PreTrip export got
   ingested as if it were the Cortex Routes file, corrupting a full day's
   route assignments (confirmed: every `packages` field went `None`, DOP
   mismatch spiked to 657%). Fixed the filename match (must start with
   `routes_dlv3`, excludes "dvic"), then added a **second, content-level**
   layer: `parse_cortex_excel` now rejects any row whose route code doesn't
   match `CX/AX+digits`, instead of silently fabricating records from
   whatever a blind column-position fallback found.
9. **`scan_ops_channel` missing real same-day uploads** — a flat
   `limit=100`, single page, no time bound, meant ordinary channel chatter
   (every automated job confirmation posts there too) pushed a real upload
   past what the scan ever looked at. Confirmed missed twice in one day.
   Widened to a 24h, paginated scan; proved it live against a real user
   re-upload (webhook caught it in real time, auto-ingest processed it a
   minute later).
10. **Logan Kelley's stale Slack ID** — his roster entry had a real but wrong
    `slack_member_id`, so his Home tab showed "not linked" despite being
    linked. Fixed directly; also surfaced that `run_weekly_slack_relink()`
    only ever re-checks *null* IDs, never re-validates an existing one, and
    that a second, separate driver-matching module (`driver_matching.py`)
    exists alongside `driver_identity.py` — both now flagged in `CLAUDE.md`.

## 2. The pause (still in effect)

Per explicit direction after a cluster of driver complaints, **every
driver-facing DM flag and `TEAM_ROOM_MESSAGES_ACTIVE` are off** —
`#nday-mgt`/`#nday-hr` are untouched. This includes the morning route DM,
progress DM, coaching highlights, lead routing, coaching notifications,
sentiment survey DM hints/monthly push, the missed-EOD gate, and the
(newly-flag-gated) package offender DM. See §5 for the full current list.

## 3. Features built tonight (all deployed, tested, currently off by default)

- **Owner Meeting Scheduler** (`OWNER_MEETING_ACTIVE`) — office-hours meeting
  tool for drivers who rated low on the sentiment survey. Candidates ranked
  by their own lowest average rating; the owner manually picks who to
  invite. Monday/Wednesday night confirm-reminder via Slack buttons.
- **Wave Leads Channel Standings** (`WAVE_LEADS_CHANNEL_STANDINGS_ACTIVE`) —
  `#nday-wave-leads` created; daily day/week standings post (today's EOD
  completion + this week's quality score, by wave) scheduled for 9 PM.
- **Arrival Re-Nudge** (`ARRIVAL_NUDGE_ACTIVE`) — previously zero re-nudge if
  a driver never tapped "I've Arrived." Escalating: +20 min, +60 min, then
  a #nday-mgt alert at +150 min if still unconfirmed.
- **EOD Second Reminder** (`EOD_SECOND_REMINDER_ACTIVE`) — a real second
  nudge at 9 PM for anyone still missing after the 7 PM mass-send (there
  was previously no follow-up at all).
- **Package Marking Troubleshooting Guide** — live now (no flag; pure data
  entry, not automated). Dispatch (Luis, etc.) can enter what a driver
  should be asked/checked per Amazon reason code, ahead of the real
  resolution-process conversation.
- **Survey/Quiz Module** (`SURVEY_NUDGE_ACTIVE` for the nudge loop) — the
  big one: admin authors questions (multiple choice/true-false/free text,
  optionally graded with a pass/fail threshold), assigns an ad-hoc set of
  drivers, sends a signed link, re-nudges every 24h indefinitely until
  done. New `/survey-admin` and `/survey` pages; a "📋 Surveys & Quizzes"
  button now on both the HR and Dispatch Home tabs. **Does not gate
  routing or escalate to termination** — that's explicitly backlogged
  (see `UPGRADE_BACKLOG.md`), not built, after confirming there's no
  existing precedent anywhere in this codebase for automated termination.

## 4. New Governance docs

- `SHOWTIME_MODULE_RULES.md` — Rule One (showtimes always use the next
  day's info) plus the incident history explaining why the fix appeared to
  recur.
- `PACKAGE_RTS_RESOLUTION_MODULE.md` — draft/offline planning doc for the
  dispatch conversation on handling non-delivered-package markings.
- `FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md` — full testing-status
  review, definitions still needed (Blake's voice isn't in the actual
  message copy yet; Wave Lead has two competing name-resolution systems;
  no SOP library exists), process-change recommendations, and a phased
  rollout plan (single-driver smoke test → 5-10 driver pilot → full
  rollout) — flags that a real pilot needs a shared pilot-roster allowlist
  that doesn't exist yet.
- This doc.

## 5. Current feature-flag state (19 on / 22 off)

**Off** (paused or never-enabled — includes everything built tonight):
`ARRIVAL_NUDGE_ACTIVE`, `COACHING_NOTIFICATIONS_ACTIVE`,
`DAILY_FALLBACK_PIN_ACTIVE`, `DM_COACHING_HIGHLIGHTS_ACTIVE`,
`DRIVER_DM_ACTIVE`, `DRIVER_PROGRESS_DM_ACTIVE`,
`EOD_SECOND_REMINDER_ACTIVE`, `LEAD_ROUTING_ACTIVE`,
`MISSED_EOD_GATE_ACTIVE`, `NDAY_POINTS_ACTIVE`,
`NDAY_POINTS_CASH_OUT_ACTIVE`, `OWNER_MEETING_ACTIVE`,
`PACKAGE_OFFENDER_DM_ACTIVE`, `RESCUE_PAYROLL_REPORT_ACTIVE`,
`SAFETY_VIOLATION_VIDEO_ACTIVE`, `SENTIMENT_SURVEY_DM_HINTS_ACTIVE`,
`SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE`, `SURVEY_NUDGE_ACTIVE`,
`TEAM_ROOM_MESSAGES_ACTIVE`, `WAVE_COMPETITION_ACTIVE`,
`WAVE_LEADS_CHANNEL_STANDINGS_ACTIVE`, `WAVE_PTT_CHANNELS_ACTIVE`.

**On** (unaffected by the pause — ingest, mgt/hr-facing reminders, safety
review, rostering matrix, etc.): `WEBSITE_USER_SYNC_ACTIVE`,
`CALLOUT_SUMMARY_ACTIVE`, `ASSOCIATE_DATA_REMINDER_ACTIVE`,
`UNLINKED_DRIVER_ALERT_ACTIVE`, `WEEKLY_SLACK_RELINK_ACTIVE`,
`EOD_CATEGORY_DIGEST_ACTIVE`, `EOD_COMPLETION_REPORT_ACTIVE`,
`MISROUTED_FILE_WATCH_ACTIVE`, `OPS_AUTO_INGEST_ACTIVE`,
`OKAMI_FINALIZE_REMINDER_ACTIVE`, `ECP_SCREENSHOT_REMINDER_ACTIVE`,
`TWF_RECALIBRATION_REMINDER_ACTIVE`, `TIMECARD_REPORT_NUDGE_ACTIVE`,
`ROSTERING_ACTIVE`, `SCHEDULE_ESCALATION_ACTIVE`,
`DVIC_TRAINING_VIDEO_ACTIVE`, `SAFETY_VIOLATION_REVIEW_ACTIVE`,
`SENTIMENT_SURVEY_ACTIVE`, `SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE`.

## 6. Outstanding / needs attention next session

- [ ] **Confirm tonight's showtime fix against a real ingest** — the
  5:30-8pm PT driver-schedule upload window; check the result resolves to
  the correct next day (see `SHOWTIME_MODULE_RULES.md`'s verification
  checklist).
- [ ] **Decide when/how to lift the pause.** `FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md`
  proposes single-driver smoke test → 5-10 driver pilot → full rollout,
  one flag at a time. Four open decisions in that doc need answers before
  the pilot can actually be scoped (Blake-voice-pass surfaces, green light
  to build the pilot-roster allowlist, who the 5-10 pilot drivers are,
  and the Package RTS dispatch-meeting date).
- [ ] **Blake's voice isn't in the actual message copy** — `08_NDL_Blake_Persona_SRD.md`
  is rich and detailed; almost nothing shipped recently (including
  tonight's new features) is written in it. Needs a scoping decision on
  which surfaces get the pass first, then a real rewrite effort.
- [ ] **Wave Lead's two competing systems** — the old hardcoded weekday
  dict (`_wave_lead_name()`) is still called from 10+ places in
  `rostering.py` alongside the newer team-based system. A driver can see
  two different "Wave Lead" names depending which DM they're looking at.
- [ ] **Package RTS resolution** — still draft/offline. Troubleshooting
  guide tool is live and empty; needs the actual Luis/dispatch interview.
- [ ] **Survey module backlog** (see `UPGRADE_BACKLOG.md`): automated
  routing gate, escalation-to-termination (should feed the existing
  points ladder, not a new system), cross-survey dashboard, new-hire
  ORE/orientation tracking use case.
- [ ] **NDAY Points** — still a placeholder point value and an empty
  redemption catalog (carried over from 2026-07-31, still true).

## 7. How to Resume

1. Read `FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md` first — it's the
   fastest "what's tested, what's not, what's next" answer.
2. Check `/feature-flags` directly for current real state — this doc's §5
   is a snapshot, that page is live and authoritative.
3. The pause is a deliberate standing decision, not a bug — don't flip
   `DRIVER_DM_ACTIVE` or `TEAM_ROOM_MESSAGES_ACTIVE` back on without
   checking in first.
4. Auto-deploy is on; assume the latest push is live in production.

---

## 8. Reference

- **Production API:** https://nday-om.onrender.com
- **Production frontend:** https://nday-om.vercel.app
- **Repo:** `C:\Users\chief\NDAY_OM_MODULAR`
- **Memory index:** `C:\Users\chief\.claude\projects\c--Users-chief-NDAY-OM\memory\MEMORY.md`
- **New docs this session:** `SHOWTIME_MODULE_RULES.md`,
  `PACKAGE_RTS_RESOLUTION_MODULE.md`,
  `FEATURE_ASSESSMENT_AND_ROLLOUT_2026-08-06.md`, this doc.

---

**Session closed: August 6, 2026.**
