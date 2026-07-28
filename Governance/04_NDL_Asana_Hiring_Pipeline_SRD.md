# Asana Hiring Pipeline — Current-State SRD & Integration Opportunities

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** Asana board "New Day Hiring" (project gid `1202834412268957`)
**Status:** Ground-truth capture, 2026-07-28 — read directly from the live board via the Asana API (not from a description of the process)
**Relationship to `03_NDL_Hiring_Onboarding_Automation.md`:** that doc is the existing *build spec* for Phase 1 (Indeed → Asana capture, already built) and a *documented but unverified* sketch of Phases 2–3. This doc corrects and extends that sketch against what the board actually looks like today, and adds integration recommendations. Read this alongside that doc, not instead of it.

---

## 1. Summary

The hiring pipeline runs entirely inside one Asana project, using **column position** (section) as the only pipeline-stage indicator and **free-text task notes** as the only structured data — because, as this capture confirmed, **the current Asana plan does not include Custom Fields** (a live API call for the project's custom field schema returned `402 Payment Required`). Every piece of process knowledge in this doc — vendor names, recruiter shorthand, the size of the drop-off backlog — came from reading real task notes, not from asking someone to describe the process from memory.

---

## 2. The real board, section by section

Sections are listed in actual board order (left to right), each with its real task count as of 2026-07-28.

| # | Section | Tasks | What it actually is |
|---|---|---|---|
| 1 | **1st Contact/Interview** | 2 | Fresh accepted candidates. Task notes = email + phone, nothing else yet. |
| 2 | **2nd Contact/ (call)** | 0 | Second contact attempt. Empty today — a transient stage, not unused. |
| 3 | **+3rd Contact** | 6 | Third attempt. Notes start picking up "Recruiting Assistant summary" (Indeed's AI blurb, per §4.2 of the existing SRD) and real due dates 1–2 days out. |
| 4 | **SET INTERVIEWS** | 7 | First stage where **`assignee`** is actually populated — Michelle Burrell and Connie Harper, by name, are the two people running interviews. One task (Nick Naney) is `completed: true` while still sitting in this section — completion checkmark and section position are used independently, not as the same signal. |
| 5 | **Ready for Onboarding Email** | 0 | Queue between "hired" and "onboarding email sent." Empty today. |
| 6 | **Onboarding instructions sent** | 5 | Not named in the existing SRD's step list (§7 calls this generically "onboarding email"). This is its real name. |
| 7 | **Onboarding Follow up** | 0 | A dedicated follow-up stage after onboarding instructions — also not named in the existing SRD. |
| 8 | **Flex Invite Sent** | 2 | Matches existing SRD §7 step 5 (Amazon Flex portal invite). |
| 9 | **Waiting BGC (In Accurate)** | 5 | Background check, via **Accurate** — a real, named background-check vendor. The existing SRD only said "Waiting Background Check" with no vendor named. |
| 10 | **Waiting Drug Test** | 2 | Matches existing SRD step 7. |
| 11 | **Ready for Amazon Training** | 2 | Matches existing SRD step 8. |
| 12 | **Training Scheduled** | 2 | Not in the existing SRD's step list at all — a real intermediate stage between "ready for" and "in" training. One task's notes read "Gustavo Perez Rivas- **ADP Done**" — confirming ADP (the old payroll system) is/was a manual per-candidate step tracked by hand-editing the task name, not a system integration. |
| 13 | **In Training** | 0 | Matches existing SRD step 9 (ORE). Empty today. |
| 14 | **SHIFTS AVAILABLE** | 1 | **Not a candidate pipeline stage at all.** Its one "task" is a running note — *"As of 7/27/26 — keeping track of shifts needed to be filled"* — dispatch/ops using a spare column on the hiring board as a scratchpad for open-shift tracking, unrelated to any individual candidate. |
| 15 | **Onboarding Paused** | **60** | **The single biggest finding in this capture.** A parking-lot column for candidates who went quiet, got flagged, or are otherwise stalled — not a clean "Rejected" the way the existing SRD's step list implies. Due dates on these tasks range from **2025-07-25 to 2026-07-20** — some candidates have been sitting here for over a year. This column is where most of the funnel's real drop-off lives, and it has no automation or reporting pointed at it today. |
| 16 | **Repeat Rejects** | 9 | **Not candidates.** These 9 "tasks" are a fixed, informal reason taxonomy used to tag why someone is a repeat-reject/blocklist entry: `NRE`, `DNLH`, `Already worked for us`, `Ass Hat`, `No Communication`, `JDLR`, `Not 21`, `NMT`, `BG Issues`. This is effectively a lookup list, implemented as fake tasks because there's no Custom Fields feature to hold a real enum. |

---

## 3. Process texture that only shows up in real data

- **Recruiter shorthand in notes**: initials (`mb`, `tw`) after a comment identify who left it (e.g. *"Indeed shows him as Diego Vega (confirmed and clarified full name for me). mb"*). This is tribal knowledge, not documented anywhere — if a new recruiter joins, nothing tells them what `mb`/`tw` mean.
- **Manual disambiguation of common names**: notes like *"Due to the commonality of his name, I could only ascertain he is approx. 38 years old... All other BG means disclosed multiple Andrew Guitierrez's"* show real background-check identity-matching friction being solved by a human reading text, with no system support.
- **Age-eligibility tracking is manual**: e.g. *"Juan Villa- TBD- turns 21 next week"* — turning 21 (the minimum age for this role) is tracked by hand-editing the task title with a date-relative note, not a stored birthdate + computed eligibility flag.
- **Asana project lookup already has a known quirk**: per the existing SRD §9, `GET /projects` didn't reliably find this board by name in production; `ASANA_PROJECT_GID` is hardcoded as a required env var. This capture confirms that workaround is still in place and still necessary.

---

## 4. Integration recommendations for NDAY_OM

These are suggestions, not commitments — ranked roughly by effort vs. value.

### 4.1 Surface the "Onboarding Paused" backlog (highest value, lowest effort)
60 stalled candidates sitting in a column nobody's automated anything against is real recoverable pipeline value. A simple NDAY_OM read-only report — pull this section via the same `AsanaClient.get_tasks_in_section()` call this capture used, sort by `due_on` ascending, surface the oldest/most-stale first — would turn an invisible graveyard into an actionable weekly follow-up list. No write access to Asana needed, no risk to the existing board.

### 4.2 Replace the "Repeat Rejects" fake-task taxonomy with a real one
The existing SRD's `KeywordRule` table (§5) is already designed exactly for this pattern — an admin-editable, categorized dictionary. Since Asana Custom Fields are confirmed unavailable on the current plan, don't wait on that; move this specific 9-value reason list into `KeywordRule` (category e.g. `reject_reason`) now, and reference it from `Candidate.status`/a new `Candidate.reject_reason` column. This also makes the reasons reportable (e.g. "how many rejects this month were `BG Issues` vs `Not 21`") in a way nine identically-shaped Asana tasks never could be.

### 4.3 Recruiter-shorthand glossary
A one-time, low-effort fix: a short reference table (initials → name) somewhere durable — even just a comment in `candidates.py` or a line in this doc — so `mb`/`tw`-style notes are decodable by anyone, not just current staff. Cheap insurance against the exact kind of tribal-knowledge loss this capture had to work around.

### 4.4 Age-eligibility as a real field, not a note
`Candidate` (existing SRD §5) doesn't currently have a birthdate field. Adding one, plus a computed "turns 21 on X" flag surfaced wherever candidates are listed, replaces the manual "TBD- turns 21 next week" title-editing pattern with something that can't be missed or forgotten.

### 4.5 Onboarding-stage hooks, feeding the broader lifecycle-automation goal
Per the separate "Full Lifecycle Automation" initiative (logged 2026-07-27), the natural trigger points for auto-provisioning a new NDAY_OM account are exactly the stages this capture confirmed are real and populated: **"Onboarding instructions sent"** and **"Training Scheduled."** When UZIO/email-vendor integration work starts, these two Asana sections — not a generic "hired" status — are the right hook points, since they're where the board shows a candidate is genuinely moving forward rather than just accepted.

### 4.6 Don't build toward Asana Custom Fields
Explicit, confirmed constraint: the current Asana plan returns `402 Payment Required` for custom field settings. Any future design that assumes structured Asana fields (rather than NDAY_OM's own DB tables + free-text notes) will hit this same wall. The existing SRD's decision to own structured candidate data in NDAY_OM's own tables (§5) is validated by this finding, not just a preference — keep it that way unless the Asana plan is upgraded.

---

## 5. Open items

- Whether **"SHIFTS AVAILABLE"** should be moved off the hiring board entirely (it's dispatch/ops content, not hiring pipeline) — flag for the user, not decided here.
- The **Accurate** background-check vendor has no integration today (manual column move only) — worth checking whether Accurate offers a status webhook/API before assuming this must stay manual.
- No custom field schema could be captured (402), so if Asana is ever upgraded to a plan with Custom Fields, this doc's "notes-only" assumption should be revisited.
