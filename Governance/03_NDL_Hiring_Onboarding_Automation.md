# Hiring & Onboarding Automation — Concept & Build Spec

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** Chrome extension (Indeed capture) + backend Asana sync + candidate analytics module
**Status:** Phase 1 build in progress (2026-07-13)
**Purpose of this doc:** Portable spec to move into VS Code / Claude Code for implementation. Supersedes the one-line "Asana Integration (Hiring/Onboarding)" backlog item (`UPGRADE_BACKLOG.md` §4.7) with a full design.

---

## 1. Summary

Hiring speed is the priority: the faster a qualifying candidate is contacted, the higher the answer/conversion rate. Today the pipeline is fully manual — HR reviews applicants on Indeed, then someone has to notice, decide, and hand-create an Asana card. This spec automates the capture step (Indeed → Asana) and adds a candidate-analytics layer (keyword tagging + average tenure) so hiring signals can eventually be correlated against real driver performance data already tracked elsewhere in NDAY_OM.

This is **not** a rebuild of Asana. Asana (board: **"New Day Hiring"**) stays the system of record for the pipeline — this spec automates *populating and advancing* it.

---

## 2. What already exists

- **Asana board is live** with columns matching the real process: `Undecided in Indeed` → `1st Contact/Interview` → `2nd Contact/(call)` → `+3rd Contact` → `SET INTERVIEWS` → `Ready for Onboarding Email` → (further downstream stages per §7).
- **Backend scaffolding exists but is unwired**: `api/src/asana_integration.py` (present in both `NDAY_OM` and `NDAY_OM_MODULAR`) defines `AsanaClient` (get project/task, update custom fields, create task) and a stubbed `NewDriverScheduler`. No route imports it; no `ASANA_API_TOKEN` is configured anywhere. This spec finishes wiring `AsanaClient`, not replacing it.
- **No SMS or transactional email infrastructure exists yet.** The only outbound channel in the codebase today is Slack, used for internal staff (rescue, callouts, RTS, attendance) — candidates are not in the Slack workspace.
- **No inbound email ingest exists.** The existing `OpsIngestJob` pattern (`routes/ops_ingest.py`) ingests files shared *in Slack*, not inbound email. Indeed has no public API/webhook for standard employer accounts, so automatic resume detection needs a different mechanism (see §4).

---

## 3. Phasing

| Phase | Scope | New infra required |
|---|---|---|
| **1** | Chrome extension captures Indeed candidates → backend endpoint → Asana card create/update. Candidate analytics (keyword tags, avg tenure) captured at the same time. | Asana API token only. |
| **2** | Automated SMS/email for the 3-contact-attempt cadence and the onboarding-email handshake. Optional Google Contacts push. | SMS provider (e.g. Twilio), email provider (e.g. SendGrid/SES), Google Workspace OAuth. |
| **3** | Downstream stage automation from "Ready for Onboarding Email" through ORE (mostly manual "mark complete" triggers against external systems with no API access — Flex, background check vendor, Amazon training — except the ORE trainer-report hook, which is a real webhook from the future training module). | Depends on training module. |

Phase 1 is the only phase currently scoped for build. Phases 2–3 are documented here so the data model doesn't need to change later.

---

## 4. Phase 1 — Chrome extension (Indeed capture)

### 4.1 Why an extension, not server-side polling
Indeed gives no public API/webhook to this account. A server-side headless scraper (e.g. on Render, which hosts the NDAY_OM backend) was considered and rejected for Phase 1: automated logins from a datacenter IP are far more likely to trip Indeed's bot/fraud detection than a real person browsing normally, risking the employer account. A human-in-the-loop browser extension avoids that risk entirely — HR does the reviewing they'd do anyway; the extension only automates the data-entry step afterward.

### 4.2 Two page types, two sync actions

**A. Candidates list page — bulk triage sync**
- Indeed's own review UI already has three per-candidate actions: ✓ (shortlist/accept), ? (undecided), ✗ (reject).
- Extension injects a single **"Sync to Asana"** button.
- On click, content script walks every visible candidate row, reads which of the three states is currently selected, and batches the result.
- Rule set:
  | Indeed action | Asana result |
  |---|---|
  | ✗ Reject | Do nothing |
  | ? Undecided | Create/update card in **"Undecided in Indeed"** column |
  | ✓ Accept | Create/update card in **"1st Contact/Interview"** column |
- List view only exposes name, location, and work-experience summary — not phone/email.

**B. Candidate detail page — contact-info sync**
- Extension injects a second **"Sync"** button on the individual candidate profile page.
- Same ✓/?/✗ buttons are present here too; same rule set applies.
- Full contact info is captured here via the **Screener questions** section — Indeed does not expose phone/email as a fixed structured field, they appear as free-text answers to employer-configured questions (e.g. "PLEASE PROVIDE A CURRENT WORKING PHONE NUMBER", "...EMAIL ADDRESS"). Because question wording can change across job postings, extraction must scan *all* screener Q&A answer text and pattern-match with a phone regex and an email regex, not match on question label text.
- Also capture from this page (for the candidate-analytics module, §6):
  - Full work-experience section (employer names + date ranges) → tenure calculation + keyword matching
  - Certifications, skills, education (optional context, stored in card notes)
  - Indeed's own AI **"Recruiting assistant summary"** (match score, highlights, flagged concerns) → appended to Asana card notes as recruiter context
  - Resume text/link

### 4.3 Backend intake endpoint (not client-side Asana calls)
The extension does **not** call the Asana API directly — it POSTs the scraped payload to a new NDAY_OM backend endpoint, which performs the Asana write using the existing (currently unwired) `AsanaClient`. Rationale: keeps the Asana token server-side, and this is the point where analytics (§6) get computed and persisted regardless of whether the recruiter later loses the browser tab/session.

- Match/dedupe key: Indeed candidate ID (stable across list-page and detail-page syncs from the same candidate, so a later detail-page sync enriches the same Asana card rather than creating a duplicate).
- **Asana field mapping** (confirmed against the live "Kenneth Brown" card):
  - Task title = candidate name, normalized to **"First Last"** with proper capitalization (Indeed's raw name casing/order is not guaranteed).
  - Task description (`notes`) = phone + email + Recruiting assistant summary.
  - Task section/column = derived from the ✓/? decision per §4.2.
  - Task lives under the existing **"New Day Hiring"** Asana project.

---

## 5. Data model (Phase 1)

New tables, owned by a new backend module (see §8):

- **`Candidate`**
  `id`, `indeed_candidate_id` (unique, dedupe key), `first_name`, `last_name`, `phone`, `email`, `location`, `resume_url`, `indeed_profile_url`, `indeed_match_score`, `recruiting_summary_text`, `avg_tenure_months`, `status` (mirrors Asana column), `asana_task_gid`, `driver_id` (nullable FK, set once hired — this is what makes the performance-correlation join possible later), `created_at`, `updated_at`.
- **`CandidateKeywordTag`**
  `id`, `candidate_id` (FK), `keyword`, `category` (e.g. `prior_employer`, `certification`, `disqualifier`, `local_dsp`, `nonlocal_dsp`), `matched_text`, `created_at`.
- **`KeywordRule`**
  `id`, `keyword`, `category`, `active`. Admin-editable dictionary — not hardcoded — so new terms can be added without a code change. Seed values from the examples already given: `FedEx`, `DoorDash`, `CDL`, `Tow truck`, plus a mechanism to flag "any other DSP" (local to Reno vs. non-local) — this likely needs a maintained list of known DSP names rather than a single keyword.

---

## 6. Candidate analytics module

Goal: capture objective signals at intake time so they can be compared against real driver performance later (DSP scorecard, safety violations, rescue-tracker activity — all already tracked elsewhere in NDAY_OM once `Candidate.driver_id` is set).

- **Keyword tagging**: resume/work-experience text scanned against `KeywordRule` at intake, stored as `CandidateKeywordTag` rows. Runs once per candidate at sync time; re-run if the detail page is re-synced with updated resume text.
- **Average tenure**: parse each work-experience entry's date range (e.g. "March 2026–Present", "June 2023–February 2026"), compute duration per role, average across all listed roles, store as `Candidate.avg_tenure_months`.
- **Reporting/correlation**: explicitly deferred — needs enough hired-and-scored drivers accumulated before it's statistically meaningful. Not built in Phase 1; the data capture in Phase 1 is what makes it possible later.

---

## 7. Phase 3 — downstream pipeline stages (documented, not built)

Full process as described by ops, for future reference so the `Candidate.status` enum and Asana columns don't need rework later:

1. **Accepted** (Phase 1, built)
2. **Contact cadence** (Phase 2) — up to 3 attempts, timing depends on when the resume arrived:
   - Morning arrival: Contact 1 = Day 1 AM, Contact 2 = Day 1 PM, Contact 3 = Day 2 AM
   - Evening arrival: Contact 1 = Day 1 PM, Contact 2 = Day 2 AM, Contact 3 = Day 3 PM
   - No response after 3rd attempt → **Bounced/Rejected**
   - Interview happens during whichever contact attempt reaches the candidate; scripted, same questions every time (script/questions to be supplied separately and turned into a structured form)
3. **Ready for Onboarding Email** (Phase 2/3) — explains how the candidate sets up a new email account per instructions
4. Candidate emails back from the new address (requires NDAY_OM to run its own email server to fully automate — flagged as an infra dependency, not yet decided)
5. **Flex invite** sent to the new email address (Amazon Flex portal)
6. Candidate sets up Flex account → **Waiting Background Check** (typically 3–5 days)
7. Background check passes → **drug test**
8. **Ready for Amazon Training** → scheduled
9. Pass → assigned to shift (per interview answers) → **ORE** (On Road Experience, first day with a trainer)
10. **Trainer report** — real webhook/data hook from the future training module (not a manual step) — pass/fail
11. Fail → terminated. Pass → onboarding complete; I-9 and remaining paperwork handled by a separate future **orientation module** (out of scope here).

---

## 8. Module placement

Per `Governance/SRD_MODULE_ARCHITECTURE_v3.md`: one module owns its own route file(s) and tables exclusively; cross-module reads go through public functions, not direct table access.

- New backend module: `routes/candidates.py` (or `routes/hiring.py`) owning `Candidate`, `CandidateKeywordTag`, `KeywordRule`.
- Apply the `require_permission`/`require_role` RBAC decorator from `api/src/authorization.py` (currently only 5/24 route files have this — don't add a 6th gap).
- Move `asana_integration.py`'s `AsanaClient` into active use from this module rather than duplicating an Asana client elsewhere.
- Frontend: new `frontend/modules/candidates.ts` (or similar) registered in `frontend/modules/index.ts` per the existing pattern in `MODULARIZATION_MAP.md`.
- Once `Candidate.driver_id` is set, this module should only read driver performance data through whatever public functions the Driver Scoring & Coaching / Operations Intelligence modules expose — no direct queries into their tables.

---

## 9. Open items / risks

- **Indeed ToS risk**: even the human-triggered extension approach involves programmatic DOM reading of Indeed's site; server-side polling (rejected for Phase 1) would carry materially higher account-risk. Revisit if Indeed changes their page structure or terms.
- **Screener question fragility**: phone/email extraction depends on regex-matching answer values, not fixed field names, specifically because the screener questions are employer-configured and could be reworded. Needs a fallback/alert if a sync produces no matched phone or email (likely means either the question was reworded in a way the regex still fails, or the candidate didn't answer it).
- **Own email server dependency** (§7 step 4): fully automating the "candidate replies from their new email" step needs a mail server decision — not yet made.
- **SMS/email provider not yet chosen** for Phase 2 (Twilio + SendGrid/SES suggested, not confirmed).
- **Screening criteria and interview script**: to be supplied separately; will define the disqualification rules (e.g. felons, tow-truck-only drivers) referenced in §1 and the structured interview form referenced in §7 step 2.
- **DSP name list**: "any other DSP local/non-local to Reno" keyword category needs an actual maintained list of DSP names, not a single keyword — TBD who maintains it.
