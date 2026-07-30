# CAP/BOC Compliance Monitoring — Backlog SRD

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** Future module — Amazon DSP CAP/BOC compliance monitoring, a corrective-video enforcement gate, and the related "actual hours worked" data gap that several other backlog ideas also depend on.
**Status:** Backlog captured 2026-07-29/30, following a real-world CAP list supplied by the user and a full codebase coverage check. **Not scoped, not built.** No clarifying-questions pass has happened yet — treat everything below as a strong starting point, not an agreed build order.
**Relationship to existing docs:** Governance/DSP_SCORECARD_RULES.md documents Amazon's own scorecard categories (Safety and Compliance, Delivery Quality, Pickup Quality, Team and Fleet) and feeds `driver_scoring.py`'s composite score. This doc is explicitly about the *separate* set of things Amazon enforces via CAP (Corrective Action Plan) / BOC (Breach of Contract) — which **overlaps with, but is not identical to**, what affects a driver's score. DVIC is the proof: it's Amazon-monitored, carries real CAP risk, has its own counseling flow (`dvic.py`), but never once feeds `driver_scoring.py`'s composite score.

---

## 1. Origin

User's own framing (2026-07-29): "we need to enforce many items that are not directly impacting the scorecard or driver weight and rank. DVIC is one example. If it is monitored by Amazon with threat of CAP or BOC we need to measure, monitor and mentor to the requirement." The user then supplied a detailed real-world CAP list (from a DSP operator community forum thread) and asked for a "battle test" — a check of which of these areas NDAY_OM actually monitors today, and which are gaps.

Note on the source: the forum thread the list came from included Amazon's own Ignite Team explicitly cautioning that compiled community lists can miss things and pointing DSPs back to "the DSP Program Policies and Program Agreement" as the authoritative source. Treat the list below as a strong starting point, not a guaranteed-complete one — no Governance doc in this repo enumerates Amazon's actual CAP/BOC criteria beyond what's captured here.

---

## 2. Full CAP list, cross-checked against real coverage (2026-07-29)

Legend: ✅ covered · 🟡 partial / needs verification · ❌ total gap (nothing in the app tracks it today)

### Fleet
- 🟡 **Low STC / update capacity daily** — Okami capacity module (`okami_capacity.py`) already exists with its own daily reminder (3:30-9 PM PT, `mgt_reminders.py`). Closest existing match on the whole list — verify it captures "STC" the way Amazon means it.
- 🟡 **Not utilizing bigger vans / EDVs when available** — `van_assignment_rules.md`'s CDV14→CDV16→XL fallback and "electric vans ONLY on electric routes" rule exist, but that's about *correct* van-type matching, not necessarily *maximizing* EDV utilization whenever one is available.
- ❌ Netradyne not installed in all rental vans
- ❌ FCA (fleet compliance audit) not completed by end of quarter
- ❌ AMXL Roadside Inspection violations (headlights out, tire tread low, U-bolt loose, etc.) — directly connects to DVIC quality: a rushed/fake DVIC is exactly what lets these defects reach the road (see §3).
- 🟡 **Overdue Repairs Notification** — `Vehicle` model has maintenance-date fields but no confirmed overdue-repair alerting.
- ✅ **Too-short DVICs** — `dvic.py`'s core, already-live function (<90 second violations, counseling DM flow).
- ❌ Failed VSA (vehicle safety audit) after passed DVICs — same DVIC-integrity theme as roadside inspections.
- ❌ Failure to provide a Repair Order from a shop

### WHC (Working Hours Compliance) — the single biggest confirmed gap
- ❌ **12 hrs max/day, 10 hr rest between shifts, >6 consecutive days in a rolling week, >60 hrs in a rolling week — "have ops monitor WHC dashboard daily."** Confirmed: **nothing in this codebase computes actual hours worked against any threshold.** `attendance.py`'s HRM-023.1 ladder tracks callout/no-show *patterns* for discipline, not literal hours worked. All four thresholds are exact, well-specified numbers — this is the most concretely buildable item on the entire list, once an hours-worked data source exists (see §4). Amazon's own 2025/2026 investment (per the forum thread) is specifically in WHC tooling — this is a live, active enforcement area for Amazon, not a stale rule.

### Onboarding / Hiring
- ❌ Drug test vendor requirements (MRO + 4-panel test, ≤30 days pre-hire, before first delivery)
- ❌ PTO cap under 120 hours (rehire check) · ❌ Amazon-compliant PTO policy (rehire check)
- ❌ Paying contracted driver amount correctly
- 🟡 **Ads not clarifying Amazon isn't the employer** — relevant to the existing candidate-intake pipeline (`candidates.py`, Chrome extension Indeed scraper), even though nothing checks ad copy today.
- ❌ Business name typos in audit-submitted documents · ❌ Post-accident vs. pre-employment drug screening mixup
- 🟡 **Uniform non-compliance** — `MANAGER_ACCOUNTABILITY_RULES.md` already lists `uniform_violation` as a named "(future)" accountability-flag type — planned but not built. Direct match.

### Payroll
- ❌ Driver PTO not paid out in ADP/Paycom, including at termination
- 🟡 **Driver has no clock-in/clock-out on a day WST shows they worked (HR should review weekly)** — directly tied to the still-stub WST Service Details ingest (see §4).
- ❌ Driver not clocked in for a delivery / lunch punch accuracy (Flex app vs. clock)
- ❌ More than ~3% of a week's timecards edited (HR should proactively reach out before it's too late) — same shape as the timecard-report nudge already built (§5).
- ❌ Amazon-provided bonuses not paid timely · ❌ Lunch punches mismatching ADP vs. Flex app
- ❌ DA not clocked in for actual training hours · ❌ Not updating every active DA during an Amazon-announced pay increase

### Administrative
- ❌ NextMile bills not paid
- ❌ Workers' comp poster photo missing floor-to-ceiling view / missing its last few lines / missing contact info
- 🟡 **Insufficient digital signature audit trail** — DVIC acks, safety-violation acks, callout signatures, and crash-report signatures all already exist in-app; worth an internal audit to confirm each genuinely captures a timestamped, attributable trail, not just a boolean flag.
- ❌ DA not completing state-required sexual harassment training within 6 months of hire
- ❌ FMCSA profile listed as interstate instead of intrastate · ❌ Loss runs not submitted on time
- ❌ Various failed elements of a comprehensive audit (catch-all) · ❌ Health insurance not meeting Amazon requirements · ❌ Poor OSHA recordkeeping forms submission

**Honest caveat:** a meaningful chunk of the above (payroll timing, PTO policy, insurance posters, FMCSA filing status, health insurance) is pure HR/legal process — not something this app can monitor from data it has access to. Those need a human checklist/process, not a software feature, if this backlog is ever scoped for real.

---

## 3. The corrective-video enforcement mechanism

Separate from the monitoring/detection question above is *what happens once a violation is detected*. The user's proposal, tied specifically to a future UZIO clock-in/out integration:

1. Driver taps "I Have Arrived for My Shift" (existing button).
2. Before clock-in actually fires via UZIO's API, the system checks whether the driver is outside boundaries on any tracked metric — score-affecting (speeding, following distance, etc.) or CAP-only (DVIC, and potentially others from §2).
3. If so, the driver must actually watch a corrective training video for that specific category before clock-in proceeds — a genuine required watch, not just an acknowledgment checkbox.
4. Once clear, the UZIO clock-in API call fires — that's what starts the clock, not the button tap itself.
5. Driver completes the route, submits the EOD survey (existing flow) — EOD submission triggers the UZIO clock-out call.

**Existing foundation to build this on:** `dvic.py`'s forced training-video gate is already built, just switched off (`DVIC_TRAINING_VIDEO_ACTIVE=false`). One fixed YouTube video via the IFrame Player API (`frontend/pages/dvic-training.tsx`), with real server-side anti-skip enforcement (`video_watched_at` only sets once a genuine minimum watch time has elapsed — not just frontend polling). The future work is generalizing this into a **per-category video library** and moving its trigger point from a DVIC-violation DM to the driver's own clock-in attempt.

### Video tone, explicit creative direction
Two distinct libraries, different tone:

**Compliance/corrective videos — "fun yet SUPER cheesy, a little painful because they're over the top... a little Pee Wee Herman."** Not dry or punitive — deliberately campy. Scrubbed from real metrics (`driver_scoring.py`, `dvic.py`, `safety_events.py`, `DSP_SCORECARD_RULES.md`), consolidating overlapping metrics into one video each:

*Safety/conduct (7):* Speeding · Seatbelt compliance · Distracted driving (phone/device use) · Following distance (tailgating) · Stop sign / signal violations · DVIC pre-trip inspection (rushed/under 90 seconds) · Illegal or unsafe roadside parking

*Delivery quality (4):* Delivery completion — finishing every stop (folds in DC DPMO) · Package handling (DSB) · Proof-of-delivery photos (folds in PSB) · Customer experience/professionalism (folds in CDF DPMO + Customer Escalation Defect)

11 videos, initial pass. Team & Fleet and Pickup Quality scorecard categories excluded (DSP-wide/fleet-level, not an individual driver's behavior). Netradyne's Safety Dashboard export (`SafetyEvent.metric_type`) is free-text, not a fixed enum — Speeding and Roadside Parking are the two named in code today, but others could surface later and may need a video added.

**Sentiment-survey videos — more serious, parable-driven, not slapstick.** Aimed at the 6 sentiment-survey categories (`sentiment_survey.py`'s `SENTIMENT_QUESTIONS`). Only one concrete concept exists so far, for **"easy_reach"** ("I can easily reach out to my DSP when needed"): NDAY_OM already provides many communication channels (Home tab buttons, Message Dispatch, Talk to My Wave Team, PTT channels, the sentiment survey itself, EOD survey) — so a driver answering negatively while ignoring all of them is like the parable of the man who drowned in a flood after turning down a car, a boat, and a helicopter, then asking God in heaven why he wasn't saved: "I sent a car, a boat, and a helicopter — what else could I do?" The other 5 categories (recognition, practical solutions, leadership info, clear expectations, feel valued) don't have concepts yet — needs a follow-up conversation before finalizing.

**Production note:** all of the above videos need to actually be filmed — this is a content-creation to-do, not just a software feature, and can proceed independently of the UZIO/clock-in engineering work.

---

## 4. The shared data dependency — real hours-worked data

Three separate backlog ideas all converge on the exact same missing ingredient:

1. **This doc's WHC hours tracking** (§2) — needs actual hours worked per driver per day/week.
2. **The CAP item "driver has no clock-in/clock-out on a day WST shows they worked"** (§2, Payroll) — same need.
3. **A proposed driver-scoring factor** (expected route duration + 40 min station buffer vs. actual clocked time, raising/lowering a driver's score) — logged separately, same dependency.

None of these can be built today because **no reliable actual-clocked-time data exists in this system**:
- `DriverShiftDM.arrived_at` / `eod_checklist_at` are real timestamps but shift-boundary/checklist proxies, not route-precise.
- `EodSurveyResponse`'s clock-in/out fields are free-text, self-reported by the driver — not system-captured.
- `WstServiceDetails.log_in`/`log_out` is the intended real source (Amazon's own WST "Service Details Report" already documents this) — but the parser (`api/src/ingest/wst/service_details.py`) is a stub that returns nothing, and nothing in `ops_ingest.py` ever calls it. Schema-ready, zero real data flowing.
- **UZIO** (the incoming payroll provider, replacing ADP) is the other possible source, but its public API access is effectively unconfirmed — no developer docs, likely requires going through UZIO's account/sales team rather than a self-serve key (same class of blocker as the paused ADP integration, just a different vendor).

**Recommendation, when this is picked up:** don't scope WHC tracking, the clock-in CAP item, and the driver-scoring efficiency idea as three separate projects. Whichever data source gets unblocked first (finished WST ingest, or UZIO API access) should feed all three at once.

---

## 5. Interim stopgap already built (2026-07-29)

Ahead of any real timekeeping integration, a daily Slack nudge exists (`mgt_reminders.py`, `TIMECARD_REPORT_NUDGE_ACTIVE`, off by default) reminding HR to post the daily timecard audit report into #nday-operations-management, so there's at least a human-readable report available in the interim. `TIMECARD_REPORT_NUDGE_HOUR` defaults to a 2 PM Pacific placeholder pending confirmation of the actual daily audit completion time from HR — update once confirmed so the automated nudge doesn't land before/during that workflow.

---

## 6. Open items / next steps

- No clarifying-questions pass has happened on any part of this doc yet — before building anything, confirm priority order (WHC tracking is the recommended first target) and which items are worth a software feature vs. a documented human process.
- Sentiment-survey video concepts needed for 5 of 6 categories (only "easy_reach" has one).
- UZIO API access needs to actually be pursued (sales/account conversation) before any clock-in/out or WHC automation can be designed concretely.
- WST ingest (`parse_service_details_csv()`) would need to actually be finished and wired into `ops_ingest.py` regardless of which path (UZIO or WST) ends up being the real data source, since Amazon's own export is likely still the more complete historical record either way.
