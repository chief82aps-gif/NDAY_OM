# Feature Assessment, Definitions Backlog & Phased Rollout — 2026-08-06

**Context:** written after an overnight incident/build session (SLACK_SIGNING_SECRET
outage, showtime date-resolution bug, a Cortex/DVIC file-matching bug that
corrupted a day's route data, an ungated offender DM, plus five new driver-facing
features built and paused pending review). Every driver-DM and `#nday-team-room`
flag is currently **off**. This doc answers: where is testing actually at, what
open questions block "done," what should change about how we build here, and how
should re-enablement actually happen.

---

## 1. Testing Status — by confidence level

### Proven against real, live data today (not just unit tests)
- Slack signing secret / webhook health
- Cortex ingest (date-resolution fix + file-matching fix + content-validation fix — all three independently verified against real uploads)
- DVIC ingest (22 violations, 16 drivers, correctly skipped its driver DM under the pause)
- Packages ingest
- Customer Feedback (CDF) ingest, including the widened ops-channel scan
- Ops-ingest stuck-job recovery

### Built tonight, unit-tested with a mocked Slack client, never fired at a real driver
- **Arrival Re-Nudge** (`ARRIVAL_NUDGE_ACTIVE`) — 3-tier escalation logic verified correct (strict cascade, no skipped tiers, idempotent), never sent a real Slack message.
- **EOD Second Reminder** (`EOD_SECOND_REMINDER_ACTIVE`) — hour-gate + dedup verified, never sent for real.
- **Owner Meeting Scheduler** (`OWNER_MEETING_ACTIVE`) — candidate ranking proven against real sentiment-survey data; the invite/RSVP/reminder flow itself only tested with a mocked Slack client.
- **Wave Leads Channel Standings** (`WAVE_LEADS_CHANNEL_STANDINGS_ACTIVE`) — the channel and one real post were created and verified live; the *scheduled* daily version is unit-tested only.
- **Package Troubleshooting Guide** — live in production with real reason-code data; this one is data-entry only (no send), so "tested" here just means the CRUD works, which it does.

### Paused mid-incident, previously working, not yet re-verified
Everything gated by `DRIVER_DM_ACTIVE` and its dependents: the daily route DM,
coaching highlights, lead routing, the 3x/day driver progress DM, coaching
notifications, sentiment survey DM hints, the missed-EOD gate, and the
(newly-gated) package offender DM. These were live and working before tonight's
pause — they don't need re-testing from zero, but each should get one real
send confirmed before the pause lifts, since several ingest bugs found tonight
could plausibly have been feeding them bad dates/data for a while.

### Never given real driver-facing volume at all (older backlog, not from tonight)
- **NDAY Points** — placeholder point value (10/day, never set for real), redemption catalog is empty.
- **Sentiment Survey Monthly Push** — flag exists, never fired in anger.
- Per the 2026-07-31 recap: Admin Home page, Redeem Bonus, Invite-to-Website, and Wave Lead Team Focus were "built and deployed, none ever click-tested by a real user" — worth confirming whether that's still true before assuming they work.

---

## 2. Definitions Still Needed Before Things Are "Done"

1. **Blake's voice isn't in the actual message copy.** `08_NDL_Blake_Persona_SRD.md`
   is a rich, detailed v1.0 spec (signature phrases, tone rules, the "Noted" /
   "and here's why" pattern, dial-down rules for serious moments). Almost none of
   tonight's new message templates — arrival nudge, EOD second reminder, owner
   meeting invite — were written in that voice; they're generic and functional.
   This is the actual gap behind "make the bot more personalized," and it's a
   rewrite pass across many files, not a config change. **Needs a scoping
   decision: which surfaces get the Blake pass first?**

2. **Package RTS resolution** — explicitly draft/offline
   (`PACKAGE_RTS_RESOLUTION_MODULE.md`). The troubleshooting-guide tool is built
   and live; it's empty. Needs the actual dispatch interview (Luis, Spencer,
   etc.) before `PACKAGE_OFFENDER_DM_ACTIVE` can responsibly go on.

3. **Wave Lead has two competing systems.** The original hardcoded weekday dict
   (`_wave_lead_name()`, Spencer/Fabian split) is still called directly from at
   least 10 call sites in `rostering.py`, while the newer team/role-based system
   (built since) exists in parallel. A driver can see two different "Wave Lead"
   names depending on which DM they're looking at. The full "10 dedicated
   wave leads + cascading rank assignment" design from 2026-07-29 is still 0%
   built — only team standings and basic role admin exist today.

4. **No SOP library exists yet.** Blake's Pro Tips and SOP quizzes (SRD §9)
   explicitly require "one source of truth" — a version-dated library of the
   current Ops Manual / station SOPs. Without it, neither feature can safely ship
   (a confidently-wrong policy answer is worse than none).

5. **Showtime fix's real-world proof is still pending** — tonight's date-
   resolution fix hasn't yet seen a real post-fix ingest complete (the 5:30-8pm
   PT window). Check `Governance/SHOWTIME_MODULE_RULES.md`'s verification
   checklist the next time that file lands.

---

## 3. Process Changes Worth Making

- **"One flag per automated send" should be a hard check, not a convention.**
  The package offender DM shipped with *no* flag at all and went unnoticed until
  tonight's incident. Worth a literal pre-merge checklist item: does every new
  function that calls `chat_postMessage`/`_dm` to a driver check `get_flag(...)`
  first?
- **"A fix isn't verified until a real subsequent event proves it."** Written
  into `SHOWTIME_MODULE_RULES.md` after the date-resolution fix appeared to
  "come back" the next morning — it hadn't; a stale pre-fix ingest result was
  still driving sends. Worth generalizing: for any ingest/date-resolution
  change, a passing unit test is necessary but not sufficient — confirm against
  the next real occurrence before calling it closed.
- **Content-level validation, not just filename/metadata matching.** The
  Cortex/DVIC incident happened because file *routing* trusted a filename
  substring alone. Now fixed for Cortex specifically (route codes must match
  `CX/AX+digits`). Worth auditing whether DOP, WST, and other filename-routed
  ingests have the same class of gap.
- **The two `requirements.txt` files can drift silently** — just fixed once
  tonight (missing `anthropic`/`boto3`, plus a bad `psycopg2-binary` pin that
  only broke on a clean-cache build). Nothing currently stops them diverging
  again; worth either deleting the duplicate or adding an explicit diff check.
- **Two independent driver-matching modules exist** (`driver_identity.py`,
  `driver_matching.py`) — now flagged in `CLAUDE.md`. Worth a literal "check
  this list first" habit before writing any new name/identity matching code.

---

## 4. Phased Rollout Plan

### Phase 0 — Close out tonight's open items (no flags touched)
- Confirm the driver-schedule date fix against tonight's real 5:30-8pm PT upload.

  **Status 2026-08-06 ~17:40 PT — two of three preconditions proven, third pending:**
  1. **Resolver logic: PASS.** `_resolve_selected_schedule_date()` unit-tested
     against the real header format (`"Thu, 06/Aug"`) and the real Week-32
     column set. Resolves to `timestamp_date + 1 day` in every case, including
     the exact scenario that failed last night, and never to the upload date
     itself.
  2. **Fix is actually deployed: CONFIRMED.** This was the real failure last
     time — the fix went live *after* the evening ingest, so the stale pre-fix
     result drove the sends. Verified by probing production's OpenAPI for
     endpoints that only exist in the 2026-08-06 build
     (`/rostering/arrival-nudges/trigger`, `/eod-survey/trigger-second-reminder`,
     `/owner-meeting/invite`) — all three live, so the running build is newer
     than the showtime fix. **There is no version/build endpoint on this API;
     that absence is what made this check awkward, and a trivial
     `GET /version` returning the deployed git SHA would make every future
     "is the fix actually live?" question a one-second answer. Worth adding.**
  3. **Real subsequent ingest: PENDING.** Baseline is job **415**
     (`Week-32-Schedule (3).xlsx`, detected 2026-08-05 18:19 PT →
     `schedule_date: 08/05/2026`, i.e. same-day — the documented bug). All 16
     historical `driver_schedule` jobs show that same signature. Tonight's job
     must resolve to **08/07/2026**. Check:
     `GET /ops-ingest/jobs` → newest `driver_schedule` job → `result.schedule_date`
     is the day *after* that job's own `detected_at` (note: `detected_at` is UTC).
- Nothing else blocks Phase 1.

### Phase 1 — Single real-driver smoke test (no global flag flips)
Every paused feature already has (or can cheaply get) a manual, force-bypassing
trigger endpoint. Run each once against **one real, consenting driver** — Collin
LaTour has been the standing volunteer all session — before touching any global
flag:
- `/rostering/arrival-nudges/trigger` (needs a specific driver's `DriverShiftDM` row staged)
- `/eod-survey/trigger-second-reminder`
- `/owner-meeting/invite` with just Collin's `roster_id`
- The existing driver DM `/send-test` equivalents already used earlier this session

### Phase 2 — 5-10 driver pilot (the actual ask)
**Real gap to flag honestly: most of tonight's builds process "everyone
scheduled today" indiscriminately — none of them currently support a scoped
subset of drivers.** To run a genuine 5-10 driver pilot rather than an
all-or-nothing flip, each feature needs a pilot-roster override:
a small, shared allowlist (e.g. a `PILOT_DRIVER_ROSTER_IDS` reminder-state or a
tiny dedicated table) that every driver-facing loop checks *before* the global
flag — "if a pilot list exists, only send to those roster_ids, regardless of
who else qualifies." This is a real, common utility worth building once and
reusing across arrival nudge, EOD second reminder, progress DM, etc., rather
than a one-off per feature.

- **Duration:** 3-5 working days minimum, matching the precedent already set
  with the progress DM's "2-3 days of team-room feedback" window.
- **Group:** pick 5-10 real, willing drivers spanning at least two waves, so
  timing edge cases (early vs. late showtime) actually get exercised.
- **Success criteria, per feature:**
  - Arrival nudge: correct tier timing, no duplicate sends, no false escalations to #nday-mgt.
  - EOD second reminder: fires once, only for genuinely-missing drivers, no double-send with the 7pm mass-send.
  - Owner meeting: RSVP buttons register correctly, no drivers outside the pilot list ever see it.
  - Progress DM / coaching highlights / lead routing: tone lands as intended, no wrong-day/wrong-pace sends (the exact class of bug that triggered tonight's pause).
- **Team-room monitor stays on throughout** (once re-enabled) to catch real reactions from the pilot group specifically.

### Phase 3 — Full rollout
Flip global flags on **one at a time**, not all at once, with a 24-48h soak
between each — watching `#nday-team-room` and `#nday-mgt` for reactions before
moving to the next. Rough order (least risky first): EOD second reminder →
arrival nudge → coaching highlights/lead routing → progress DM → owner meeting
→ package offender DM (last, since it's the one still waiting on the dispatch
process definition).

---

## 5. Open Decisions — ANSWERED 2026-08-06

1. **Blake voice pass — start with the daily route DM + showtime DM.** These are
   the highest-volume, most-read driver touchpoints. Note the constraint: both are
   built by functions inside **locked** modules (`_build_driver_dm()` /
   `_build_shift_dm()` in `rostering.py`, locked 2026-07-21). This is a
   copy/wording change only — the Acknowledge/Arrived/Call-Out button structure,
   the Showtime-vs-Route-Assignment button split, and the outstanding-items
   early-exit branch must all stay exactly as-is. Treat it as an authorized
   edit to message *text* within those functions, not a licence to restructure
   them.
2. **Shared pilot-roster allowlist utility — approved, build it.** One shared
   check consulted by every driver-facing loop *before* the global flag: if a
   pilot list exists, send only to those `roster_id`s regardless of who else
   qualifies. Build once, reuse across arrival nudge, EOD second reminder,
   progress DM, coaching highlights and owner meeting. This is the actual
   unblocker for Phase 2 — without it the only option is the all-or-nothing
   flip that caused this pause.
3. **Pilot drivers — the user will name them directly.** Do not auto-select from
   roster/scoring data. Still hold to the doc's own requirement that the group
   span at least two waves so early-vs-late showtime timing edge cases get
   exercised; if the named list doesn't, flag that plainly rather than
   silently substituting names.
4. **Package RTS dispatch meeting — this week.** Keeps the package offender DM
   at the tail of the Phase 3 order rather than slipping it. The troubleshooting
   guide stays built-but-empty until that interview happens;
   `PACKAGE_OFFENDER_DM_ACTIVE` stays off until it's populated.
