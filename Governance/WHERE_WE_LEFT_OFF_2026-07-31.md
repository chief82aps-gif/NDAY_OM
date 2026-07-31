# Governance: Where We Left Off (July 31, 2026)

## Summary
Long session. Started with a tier-calibration fix and a Wave/Rank Rostering design conversation, then pivoted hard into a long build stretch: daily ingest expansion, a full reward-points system, a live feature-flag admin system (with a real migration across ~18 files), callout/DVIC escalation logic, and confirmation that Render auto-deploy is genuinely working. All commits below are pushed to `origin/main` and — as of this session — **auto-deploying**, confirmed live via direct production checks. Full status audit published as an artifact at session close (see §8) — read that first if you want the fastest "what's real" answer.

---

## 1. Tier calibration fix — shipped (`f62ac83`)

`quality.py`'s `/rankings` and `rostering.py`'s roster/matrix helpers were still sorting/labeling drivers off Amazon's raw 4-tier `overall_standing`, disagreeing with `driver_scoring.py`'s real Tin/Lead/Sawdust tiers. Both now source from `driver_scoring.compute_driver_scores()`. Also fixed `route-assignment.tsx`'s chip colors and a stale docstring.

**Follow-up same day (`c02ee1e`):** per explicit request, Tin/Lead/Sawdust now all collapse to a single **"Does Not Meet Minimum"** label everywhere a tier is shown — internal thresholds/sorting unchanged, display-only. Caught and fixed a real latent bug this surfaced: `rostering.py`'s `_STANDING_RANK` was keyed off the capitalized display string, so collapsing three strings into one would've silently broken sort order.

---

## 2. ECP screenshot reminder — shipped (`9331ced`)

New reminder in `mgt_reminders.py`: once Amazon's ECP message lands in `#dlv3-nday-info`, posts to `#nday-mgt` asking for a screenshot of the Scheduling page's Unassigned section (the per-wave capacity data a future rank-based rostering pass would need). Gated by `ECP_SCREENSHOT_REMINDER_ACTIVE` (off).

---

## 3. Wave/Rank Rostering — fully designed, still zero code

Long design conversation, not built. Key decisions, all recorded in memory (`project_wave_lead_roster_module.md`) and not re-litigated:
- Senior Wave Leads (Spencer/Gallo) stay exactly as-is, untouched.
- **10 new dedicated wave leads** (Wave 1-4 FH/BH + Wave 5 FH/BH) — a real reversal of the earlier "remove Standing Wave Lead" decision, not yet built.
- Cascading rank-based assignment: highest performers fill their own wave first, overflow spills to the *nearest* adjacent wave (either direction), sweepers land in whatever wave runs last.
- Per-wave capacity comes from Cortex (already ingested) once ECP passes, not a new manual source.
- Draft happens in Amazon's own portal (not this app); Blake compares the Cortex-ingested result to its suggestion and names specific swaps; residual mismatches after a second check get tracked against real outcomes (on-time, DVIC, driver-scoring metrics).
- Late-coverage apology message goes to `#dlv3-nday-info` (user's explicit, informed choice — that channel is otherwise ingest-only).

**Start here next time:** this needs a real file-by-file implementation plan before any code — nothing below builds on it yet.

---

## 4. "Report an App Glitch" — shipped (`9a0765d`)

New `AppGlitchReport` table + button on every Home tab (driver, Dispatch, HR) → one shared modal → persists to a real open/resolved list (unlike the older generic injury/incident quick-report, which only DMs and never persists) + DMs the "owner" role directly. Admin page at `/glitch-reports`.

---

## 5. Coaching highlights in the morning DM — shipped (`bd19a2b`)

Direct answer to "does the morning DM give improvement hints?" — it didn't. Added `driver_scoring.get_driver_metric_highlights()` (best/worst-scoring sub-metrics for one driver) and a "🌟 Doing great on / 🎯 Room to grow" block in `rostering.py`'s `_build_driver_dm()`. Framing kept positive throughout per the standing "coaching DMs must never read negative" rule. Gated by `DM_COACHING_HIGHLIGHTS_ACTIVE` (off) — `DRIVER_DM_ACTIVE` itself is confirmed **on** in production, so this is one flag away from live.

---

## 6. Daily Quality Overview ingest — shipped (`8d758fc`)

Amazon's per-day performance export (packages/routes volume, RTS-controllable count, POD%, DSB count — released ~30-48h after delivery completion) now has a real ingest: new `DailyQualitySnapshot`/`DailyQualityRecord` tables, parser verified against a real exported file, wired into `ops_ingest.py`'s existing auto-detect pipeline. Deliberately does **not** feed `driver_scoring.py`'s blended score — narrower metric set than the weekly Scorecard, kept as a separate supplementary signal. Confirmed the existing Safety Dashboard daily ingest (built 2026-07-14) still works correctly against a fresh real file too — no changes needed there.

---

## 7. NDAY Points + Swag Store — shipped (`5634332`)

New reward-only points currency, deliberately named/coded apart from `attendance.py`'s punitive HRM-023.1 points per explicit direction to move away from that style of program.
- **v1 earning source:** perfect safety day (zero `SafetyEvent` rows for a driver who actually drove), auto-awarded off the existing daily Safety Dashboard ingest, idempotent per driver/day. Point value (10/day) is a **placeholder** — needs a real number.
- **Redemption:** catalog (swag/gift cards) — always on, but the catalog is currently **empty**, needs items added via `/swag-store-admin`.
- **Cash-out:** built into the data model, hard-gated off (`NDAY_POINTS_CASH_OUT_ACTIVE`) pending real legal review of converting a points balance to cash — flagged plainly, not something to flip without that review.
- Slack Home tab shows balance + redeem button once balance > 0; fulfillment is manual (HR marks it done), same "identify, don't execute" idiom as Rescue Bonus/Okami.

---

## 8. Feature Flags admin system — shipped (`8be8f52`, `d70b2ec`, `898f89b`, `aaad3fe`)

The big infrastructure piece. New `/feature-flags` page (admin-only) toggles **every** `_ACTIVE` flag in the app live, no redeploy needed — a `FeatureFlag` DB row overrides the env var when present, falls back to the env var otherwise. All ~30 flags across ~18 files were migrated from cached module-level constants to live `get_flag()` calls.

**Two real bugs found and fixed same day (`aaad3fe`):** two bare `_ACTIVE` references (in `dvic.py` and `rostering.py`) survived the migration inside function bodies, so they passed the import check but would have thrown `NameError` the moment those specific code paths ran. Found via a full-codebase audit, fixed, verified by directly exercising both previously-broken functions.

`SLACK_NOTIFICATIONS_ACTIVE` is deliberately **excluded** — it's a one-time process-startup monkeypatch of the Slack SDK itself, architecturally incompatible with a live per-request DB check, stays Render-only on purpose.

Also built: a dedicated **Admin Home page** (`/admin-home`, admin-only) — quick links to Feature Flags/Admin Panel/Swag Store/Glitch Reports plus a live system-overview (flags on, open glitches, pending redemptions). Reached via a new tile on the shared dashboard (which stays everyone's default landing page, per explicit choice) — not an auto-redirect.

---

## 9. Callout auto-block — shipped (`97cc2c6`)

Per explicit request: once the night-before Showtime DM batch has gone out for a shift date **and** the roster is already tight (reusing the exact existing `_get_replacement_pool()`/`MIN_REPLACEMENT_POOL` definition, not a new one), the automated callout path stops being a one-tap convenience for that shift. The callout is still logged as always — the driver just gets sent to a real phone call (`775-467-2283`, shown as a big `tel:` link) instead of a confirmation screen, and the #nday-mgt tight-roster alert notes it happened so dispatch expects the call.

---

## 10. DVIC weekly-frequency escalation — shipped (`0a068cc`)

New third tier on top of the existing Stage 1/Stage 2 ladder: a driver's **3rd under-90-second inspection within a rolling 7 days** triggers a harder message and drops the self-service Acknowledge/video button entirely. Instead, the driver gets a modal to enter a code — the system generates it and gives it only to dispatch, with an explicit instruction not to hand it over until the conversation has actually happened. One-time, per-occurrence trigger (not a sticky ladder) per explicit direction. Verified end-to-end with synthetic data: threshold detection, code generation, wrong-code rejection, correct-code clearing.

---

## 11. Render auto-deploy — confirmed working

Enabled via Render's native "Auto-Deploy: On Commit" setting. Verified directly by curling three brand-new production endpoints (`/glitch-reports`, `/daily-quality/snapshots`, `/nday-points/catalog`) and confirming all three respond live — auto-deploy is genuinely picking up pushes now, no more manual redeploy step needed going forward.

---

## 12. Full status audit — published

A complete "what's fully working / what needs testing / future backlog" artifact was generated and shared at session close — the fastest way to answer "where do things stand" without re-reading this whole document. Ask for it again if the link is lost; the content is also captured in outline form in this doc's sections above.

---

## 13. Outstanding / needs attention next session

- [ ] **Wave/Rank Rostering** — needs a real implementation plan before any code (§3).
- [ ] **NDAY Points** — needs a real per-perfect-day point value (currently placeholder 10) and at least one real catalog item before it's meaningfully usable.
- [ ] **Wave teams/leads** — still not actually populated with real members/assignments on the admin page. This is now the single blocker for Wave Competition, "Talk to My Lead," and the Senior Wave Lead flow all at once.
- [ ] Flip on when ready (all currently off, all independent): `DM_COACHING_HIGHLIGHTS_ACTIVE`, `NDAY_POINTS_ACTIVE`, all 4 sentiment-survey flags, `CALLOUT_SUMMARY_ACTIVE`, `OKAMI_FINALIZE_REMINDER_ACTIVE`, `TIMECARD_REPORT_NUDGE_ACTIVE`, `RESCUE_PAYROLL_REPORT_ACTIVE`, `WAVE_PTT_CHANNELS_ACTIVE` — now toggleable from `/feature-flags`, no Render dashboard needed.
- [ ] Callout auto-block and DVIC weekly-frequency escalation are both real, live code paths now — watch for their first real-world trigger and confirm they behave as designed.
- [ ] Admin Home page, Redeem Bonus, Invite-to-Website, Wave Lead Team Focus — all built and deployed, none ever click-tested by a real user.
- [ ] Everything still outstanding from earlier session recaps remains outstanding (Austin/Pedro training-video replies, "Blake" text rename across ~35 files, WHC hours-tracking gap).

---

## 14. How to Resume

1. Read `MEMORY.md` (auto-memory index) first.
2. For the Wave/Rank Rostering design, this document's §3 (and `project_wave_lead_roster_module.md` in memory) is the source of truth — nothing else exists yet.
3. Check `/feature-flags` directly to see current real flag state rather than assuming from this doc — it's now live and authoritative in a way a static doc can't be.
4. Auto-deploy is on — assume the latest push is live in production; no separate "confirm deploy" step needed anymore.

---

## 15. Reference

- **Production API:** https://nday-om.onrender.com (Render, **auto-deploys on push** as of today)
- **Production frontend:** https://nday-om.vercel.app (Vercel, auto-deploys on push)
- **Repo:** `C:\Users\chief\NDAY_OM_MODULAR`
- **Memory index:** `C:\Users\chief\.claude\projects\c--Users-chief-NDAY-OM\memory\MEMORY.md`
- **Today's key commits:** `f62ac83`, `c02ee1e`, `9331ced`, `9a0765d`, `bd19a2b`, `8d758fc`, `5634332`, `8be8f52`, `d70b2ec`, `898f89b`, `aaad3fe`, `97cc2c6`, `0a068cc`

---

**Session closed: July 31, 2026. Pick back up Monday.**
