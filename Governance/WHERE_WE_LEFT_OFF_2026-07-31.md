# Governance: Where We Left Off (July 31, 2026)

## Summary
Shorter session than yesterday. Two pieces of shipped code (tier calibration fix, ECP screenshot reminder), plus a long, still-unbuilt design conversation for a new **Wave/Rank Rostering** system — that design is the most important thing to read before the next session, since none of it exists in code yet. All commits below are pushed to `origin/main`; Render needs a manual redeploy to pick up any of them.

---

## 1. Tier calibration fix — shipped (commit `f62ac83`)

User-reported: driver dashboards weren't showing the current Tin/Lead/Sawdust tiers. Root cause: `quality.py`'s `/rankings` and `rostering.py`'s roster/matrix helpers were still sorting and labeling drivers off Amazon's raw 4-tier `overall_standing` (`_STANDING_RANK`: Platinum/Gold/Silver/Bronze only) — the same ranking-disagreement-with-`driver_scoring.py` architecture violation already fixed once in `route_assignment.py` on 2026-07-29, just not everywhere yet.

Fixed: `quality.py` (`/rankings`, feeds the driver's own Slack Home quality block) and `rostering.py` (Assignment Matrix, Driver Summary Matrix, roster suggestions) now both source tier/rank from `driver_scoring.compute_driver_scores()`. Also extended `route-assignment.tsx`'s `STANDING_COLOR` chip map (text was already right, color fell back to generic gray) and cleaned up a stale docstring in `route_assignment.py`.

`driver-quality.tsx` and the Wave Lead module were already correctly calibrated — this closes out every mismatch found.

---

## 2. Wave Lead Team Focus — shipped (commit `7911316`, from 2026-07-30, needs Render redeploy)

Carried over from yesterday's close: `GET /wave-lead/team-focus?half=front|back` + `wave-lead-focus.tsx` — each Senior Wave Lead can see their team's per-driver metrics, tier, gap-to-next-tier, and improvement focus areas with suggested videos, sorted "biggest bang for the buck." **Confirmed deployed to Render as of this session's start** (per screenshot) — good, that one's live.

---

## 3. ECP screenshot reminder — shipped (commit `9331ced`)

New reminder in `mgt_reminders.py`: once Amazon's ECP message lands in `#dlv3-nday-info` (reuses `daily_notify.py`'s existing `scan_for_ecp_message()` detection), posts to `#nday-mgt` asking someone to screenshot Amazon's Scheduling page's "Unassigned" section before dispatch rosters. Gated by `ECP_SCREENSHOT_REMINDER_ACTIVE` (off by default). This is infrastructure for §4 below — the screenshot itself isn't ingested/parsed yet, just prompted for.

---

## 4. Wave/Rank Rostering — full design discussed, NOTHING BUILT YET

This was the bulk of today's conversation. The user wants drivers rostered by **rank within their wave, cascading to adjacent waves when a wave's slots run out**, with sweepers landing in whatever wave runs last, and a "Blake" apology message when a callout forces a route to depart late. This is a big, multi-part build — read this section fully before touching any of it.

### Org structure (confirmed, not yet built)
- **Senior Wave Leads (Spencer=Front, Gallo=Back) stay exactly as they are** — untouched, no involvement in this new system at all.
- **10 NEW dedicated Wave Leads**: Wave 1-4 FH/BH (real) **and Wave 5 FH/BH** (structural — Wave 5's two leads move from the current "2 concurrent leads, no half" model to the same uniform (wave_number × half) grid as every other wave; will likely sit empty most of the time).
- Mentoring always follows a driver's **standing team lead**, regardless of where they actually route that day.
- Day-of dispatch contact follows wherever the driver **actually lands** that shift — this part already works correctly in `rostering.py`'s `_resolve_wave_lead_for_driver()`.
- Cap ~10 drivers per lead where possible (not yet enforced anywhere).

### Ranking & cascade (confirmed, not yet built)
- One global rank: `driver_scoring.py`'s existing blended score — no new ranking system.
- Post-ECP (once dispatch is about to roster), Blake reads that day's real per-wave route counts and generates a suggested roster order — highest performers fill their own standing wave first; overflow cascades to the **nearest adjacent wave** (either direction) and re-competes there; repeats until everyone's placed.
- Sweepers land in whichever wave actually runs last that day (not a hardcoded "Wave 4").

### Where the per-wave capacity data comes from (resolved after a long back-and-forth — don't re-litigate)
- **Not** a new manual form, **not** a portal scraper (hard rule: no Playwright/browser automation against any Amazon portal — this was seriously considered and ruled out).
- **Answer: a manually-taken screenshot of Amazon's Scheduling page**, taken right after ECP (before dispatch rosters) — at that moment nothing is rostered yet, so the page's "Unassigned" section shows *every* block for the day broken out by wave time, which is exactly the per-wave capacity data needed. A second screenshot taken later (after dispatch rosters) becomes the gap-detection signal for late-coverage.
- This app already has a working precedent for this kind of thing: `ocr_parser.py` (semantic-label extraction, built for the Daily Screenshot Audit) — the same approach should be used here, bucketing by wave via the existing `wave_number_for_assignment()` function so it always agrees with how the rest of the app defines waves. **Not built yet** — only the reminder to take the screenshot (§3) exists so far.

### The draft → check → check → track workflow (confirmed, not yet built)
1. Dispatch rosters in **Amazon's own portal**, not this app — that's "the draft." NDAY_OM only sees it via Cortex re-ingest.
2. Blake compares the ingested draft to its suggestion and posts **specific named swaps** ("swap Driver A and Driver B") wherever it's out of compliance — has to be a message, not an in-app fix, since this app can't write back into Amazon's tool.
3. After dispatch has a chance to act, Blake re-checks the next Cortex ingest.
4. Whatever's **still** mismatched after that second check gets logged, not discarded.
5. Residual mismatches get tied to that day's actual outcomes — **route on-time/completion, DVIC/safety incidents, and driver_scoring.py metrics** — building a longitudinal record of whether dispatch's manual call or Blake's ranked suggestion tended to be right.

This overlaps heavily with the existing `WaveRosterSuggestion`/`WaveRosterDiscrepancy` pipeline in `wave_lead.py` (`generate_wave_roster_suggestion()`, `check_roster_discrepancies()`, `send_discrepancy_summary()`) — that pipeline's docstring currently says wave-mismatch is deliberately **not** flagged ("normal spillover, not an error"). Under this new mandate, that has to change: a mismatch is no longer automatically "normal," it's something to check and, if unresolved, track.

### Late coverage / the Blake notification (confirmed, not yet built)
- A callout that leaves no earlier-wave replacement, only a final-wave sweeper, is accepted as-is — no forced early clock-in.
- Blake posts a kind-but-firm, apologetic note to **`#dlv3-nday-info`** (user's explicit choice, made aware this channel is currently ingest-only/Amazon-adjacent in the code, not normally posted into) citing NDAY's legal inability to compel an earlier clock-in.

---

## 5. Outstanding / needs attention next session

- [ ] **Redeploy Render** — picks up `7911316` (Wave Lead Team Focus, if not already live), `f62ac83` (tier calibration), and `9331ced` (ECP screenshot reminder).
- [ ] Write the full implementation plan for §4 (Wave/Rank Rostering) before writing any code for it — this is a big enough build (org rework, cascade algorithm, screenshot OCR ingest, two-round Cortex-driven compliance checking, swap-finding logic, multi-signal outcome tracking, the Blake notification) that it needs a real file-by-file plan, not ad hoc edits.
- [ ] Once `ECP_SCREENSHOT_REMINDER_ACTIVE` is flipped on, confirm the reminder actually fires when Amazon's real ECP message lands (not yet tested against a live message).
- [ ] Everything still outstanding from `WHERE_WE_LEFT_OFF_2026-07-30.md` §7 remains outstanding (Austin/Pedro training-video replies, Blake text rename, various off-by-default flags, Wave Lead assignments not populated for real, Redeem Bonus / Invite-to-Website never click-tested).

---

## 6. How to Resume

1. Read `MEMORY.md` (auto-memory index) first.
2. For the Wave/Rank Rostering design, **this document's §4 is the only place it's written down** — it does not exist anywhere else yet. Read it fully before scoping any of that work.
3. Confirm current Render/Vercel deploy state before assuming any of today's or yesterday's fixes are live.
4. If asked to continue the Wave/Rank Rostering build, start with a formal plan (file-by-file), not direct edits — this was the explicit next step offered at session close.

---

## 7. Reference

- **Production API:** https://nday-om.onrender.com (Render, manual-deploy-only)
- **Production frontend:** https://nday-om.vercel.app (Vercel, auto-deploys on push)
- **Repo:** `C:\Users\chief\NDAY_OM_MODULAR` (going-forward repo — not the sibling `NDAY_OM`)
- **Memory index:** `C:\Users\chief\.claude\projects\c--Users-chief-NDAY-OM\memory\MEMORY.md`
- **Today's key commits:** `f62ac83` (tier calibration), `9331ced` (ECP screenshot reminder)

---

**Session closed: July 31, 2026. Wave/Rank Rostering design is fully captured above but entirely unbuilt — start there next time.**
