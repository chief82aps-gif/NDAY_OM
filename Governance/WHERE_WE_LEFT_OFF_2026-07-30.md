# Governance: Where We Left Off (July 30, 2026)

## Summary
This document captures what changed in the NDAY Route Manager system today, why, and what's still open — so the next session (human or Claude) can pick up cleanly without re-deriving context. Today was a long, high-volume session touching login architecture, the sentiment survey, the callout system, DVIC, and the Wave Lead module, plus a personal tribute rename ("Blake"). All commits below are pushed to `origin/main`; Render/Vercel deploy status should be confirmed at the start of the next session, not assumed.

---

## 1. Login & Authentication — real architecture fix, not a patch

**Problem found:** the Slack OAuth callback (`auth.py`) rejected any Slack ID not already linked to a pre-created website `User` row as `not_linked` — this was blocking nearly everyone from ever completing website login, since almost no one had been manually invited.

**Fix (commit `aa25701`):** `get_or_create_user_for_slack()` now auto-creates a `driver`-role account (lowest privilege, random unusable password) on first successful Slack login instead of rejecting. Role escalation (dispatcher/HR/owner) still only ever happens deliberately (Add New Hire modal, `run_website_user_sync`'s channel-membership sync).

**Deeper fix (commit `689c1ab`):** dashboard buttons (Dispatch Home, HR Home) previously always forced a fresh `/auth/slack/login` → Slack OAuth redirect on every single click, discarding any valid existing session — this is why drivers/staff kept hitting Slack's "Sign in to New Day Logistics" wall repeatedly on mobile (Slack's in-app browser doesn't reliably persist Slack's own login cookies between separate link opens). Per explicit direction ("Slack login should be sufficient, lean on user levels for access"), `_build_combined_home_blocks()` now mints a real, ready-to-use JWT session directly at Home-tab-render time (trusting the already-live `is_dispatch_staff`/`is_hr_staff` channel-membership check as identity proof) and bakes it into every dashboard button URL. No OAuth handshake needed anymore, not even on the first click.

**Confirmed working:** Collin and Luis both tested successfully after this shipped.

**Related fix (commit `7e0391b`, `1666ab1`):** the Slack Home tab is a *static* server-published view — Slack never auto-refreshes it. Anyone whose tab was published before a flag change stays stuck (this is what caused the "Coming Soon" placeholder some drivers saw even after `DRIVER_DM_ACTIVE` was confirmed on). New **"🔄 Refresh All Driver Homes"** button on Dispatch Home force-republishes everyone's tab in one shot — reusable any time a similar flag flip needs to propagate.

---

## 2. Sentiment Survey — full rebuild

- **6 real Amazon DSP rating questions** added (recognition, practical solutions, leadership info, clear expectations, feel valued, easy to reach) — 1-5 scale + per-question free-text note each, alongside the original general free-text fields. These were part of the original spec and had been omitted from the first build.
- **Admin report is monthly, not daily** (`sentiment-survey-admin.tsx`) — a single day was usually near-empty since drivers take days to respond. Shows per-question stats (avg, % favorable) + full response list.
- **Monthly auto-send** (`SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE`, off) — fires the Sunday of the last full week of the month, ahead of Amazon's own survey window (their first two weeks of the month).
- **Morning DM hints + Home tab button** (`SENTIMENT_SURVEY_DM_HINTS_ACTIVE`, off) — every hint now includes a "Share Feedback" button; overdue-nudge threshold is 3 days.
- **Weekly summary to #nday-hr** (`SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE`, off) — Mondays 8 AM, trailing 7 days, stats + AI-flagged themes, so it's actually reviewed by a group, not just sitting in one person's DMs.
- **"Respond as Blake"** (`sentiment-survey-admin.tsx`) — HR can reply directly to the specific driver behind a suggestion, in Blake's voice (see §5). Deliberate, human-supervised exception to this module's anonymity — HR already sees identity in the admin report; this just lets them act on it. Three modes: `noted` (bare), `noted_with_reason`, `decline_with_reason`. Tracked on `SentimentSurveyResponse.responded_at/response_mode/response_text` so nothing gets double-replied.

All four new flags above are still **off** — flip on Render when ready to test live.

---

## 3. Callout System — tightened per explicit HR request

Full detail in the commit messages (`2c624f3`, `269fe96`); summary:
- New reason codes `doctor_appointment`, `childcare` — alongside `sick`/`personal`/`family`/`weather`/`transportation`/`other`.
- `personal`, non-emergency doctor's appointments, and childcare/school issues are **no longer valid on their own** — the callout page (`callout.tsx`) shows an immediate pushback explanation and requires the driver to either pick a real reason or explicitly acknowledge they're submitting anyway (re-validated server-side, can't be bypassed by a modified client).
- Family emergency now also requires confirming the driver **currently lives with** the family member (spouse/child/mother/father) — doesn't extend to extended family.
- Acknowledged-anyway submissions are flagged `AttendanceEvent.reason_valid = False` ("unauthorized") — still logged (can't force attendance), but visibly distinct to dispatch/HR and to the driver ("This is not a valid reason for a callout, so this has been logged as UNAUTHORIZED — you are expected to report to work").
- **New recurring summary** (`CALLOUT_SUMMARY_ACTIVE`, off) to **both** #nday-mgt and #nday-hr, every 15 minutes from 9:30 AM to 12:30 PM Pacific — the existing 8:30 AM one-time digest (#nday-mgt only) is unchanged and stays alongside it.

---

## 4. DVIC — two real bugs found and fixed, plus made hands-off

- **Hands-off counseling DMs** (commit `6e1e02d`): per-driver counseling DMs were built back in the per-violation redesign but never actually auto-triggered — required a manual hit on `/dvic/send-all-dms` after every ingest. Per explicit "I want it hands off" request, `ops_ingest.py`'s post-ingest hook now calls `_process_week()` automatically. Still respects `DRIVER_DM_ACTIVE` internally (safe no-op if off) and the existing per-violation dedup.
- **Naughty List was silently failing** (commit `5386740`): the old version built one Slack block with a row per driver flagged across the whole rolling 7-day window — for a 57-driver week that's ~3,480 characters, over Slack's 3,000-char-per-block limit, so `chat_postMessage` was rejecting it outright. The code swallowed that exception silently (logged a warning, no visible failure) — so the API kept reporting "posted" while nothing ever appeared in #nday-mgt. **Confirmed this had been failing for a while**, unrelated to any of today's changes. Fixed by simplifying to just the report's most recent day's violator names (small, reliable, and it's the information actually wanted) — confirmed live and working in #nday-mgt.
- **`DVIC_TRAINING_VIDEO_ACTIVE` flipped to `true`** on Render (user's own change, confirmed via screenshot). Stage 2+ (repeat) violations now require actually watching the training video before acknowledging; Stage 1 (first-time) is unaffected, still just "Acknowledge." Video source is a hardcoded YouTube embed (`dvic-training.tsx`, ID `FLtjCc1JZqw`) — confirmed playable, not dependent on the separate (unused) S3-upload path.

---

## 5. "Blake" — personal tribute rename, now a real feature too

The Slack app was renamed from "NDAY Route Manager" to **"Blake"**, in memory of the user's late friend Chief Blake Cooke. A custom avatar (Grok-generated, stylized/non-photorealistic) was set as the app icon. This is not cosmetic cleanup — treat with care in future sessions, see `project_blake_rename_and_responses.md` in memory.

Two real features came out of it:
1. **"Respond as Blake"** (§2 above) — three response templates built from Blake's actual signature phrases ("Noted," "and here's why").
2. **Training-video library idea** (backlog, not built) — logged in `Governance/06_NDL_CAP_Compliance_Monitoring_SRD.md` §3 and `project_uzio_clock_video_gate.md`. Today, DMed **Austin Spitzer** and **Pedro Ibarra** directly asking if they'd help create these videos — explicitly reframed as **non-punitive**, positive/growth-oriented (a tonal evolution from the earlier "cheesy/over-the-top" direction — worth clarifying which tone wins if this gets built for real). Austin was also given a drone-footage suggestion. Both were asked to reply to the user or back to Claude. **Awaiting responses as of session close.**

Also expanded scope: the video list now includes general quality/how-to content (locker delivery, checking customer delivery notes), not just the original 11 compliance-corrective videos.

**Not yet done:** ~35 files still say "NDAY Route Manager" as literal text (a handful are genuinely user-facing — `rostering.py`'s DM footers, `rescue.py`'s test message, `drivers.py`'s onboarding instructions, the website's login/about/terms/privacy pages). Renaming these to "Blake" was offered but not confirmed/started.

---

## 6. Other fixes shipped today

- **Van issues** now reach #nday-fleet immediately on EOD survey submission (unconditional, no flag), not just via the once-daily digest that requires `EOD_CATEGORY_DIGEST_ACTIVE` (off by default) — that digest previously was the *only* delivery path, so if left off, van issues never reached Slack at all.
- **Wave Lead correction**: removed the incorrect per-wave (1-4) "Standing Wave Lead" concept (never a real feature) — replaced with a Senior Wave Lead scoped by half (Spencer = Front Half, Gallo = Back Half), roving across all of waves 1-4. Full detail in `Governance/05_NDL_Wave_Lead_Module_SRD.md`.
- **Rescue Tracker**: Senior Wave Leads (Spencer/Gallo) added to the driver dropdown, since they act as daily sweepers.
- **EOD Survey**: now shows the day's assigned vehicle VIN.
- **CAP/BOC Compliance Monitoring**: a full real-world CAP list (Fleet/WHC/Onboarding/Payroll/Admin) was battle-tested against actual coverage — see `Governance/06_NDL_CAP_Compliance_Monitoring_SRD.md`. **WHC (Working Hours Compliance) hours-tracking is the single biggest confirmed gap** — nothing in this system tracks actual hours worked against Amazon's 4 thresholds (12hr/day, 10hr rest, 6 consecutive days, 60hr/week). This shares a root data dependency (real hours-worked data — UZIO API or the still-stubbed WST ingest) with two other backlog ideas: the driver-scoring efficiency factor, and the "clock-in/out missing vs. WST" CAP item.
- **Timecard report nudge**: tuned to Amanda's confirmed 9-10 AM audit schedule (fires 10 AM).

---

## 7. Outstanding / needs attention next session

- [ ] Confirm Chris Espejo's Home tab is actually fixed now (his last screenshot showed stale "Coming Soon"; the Refresh-All-Homes button should have fixed it, not yet re-confirmed by him)
- [ ] Check for replies from Austin Spitzer / Pedro Ibarra on the training-video ask
- [ ] Decide on the "NDAY Route Manager" → "Blake" text rename across the ~35 files that still say the old name
- [ ] Flip on when ready (all currently off): `SENTIMENT_SURVEY_MONTHLY_PUSH_ACTIVE`, `SENTIMENT_SURVEY_DM_HINTS_ACTIVE`, `SENTIMENT_SURVEY_WEEKLY_SUMMARY_ACTIVE`, `CALLOUT_SUMMARY_ACTIVE`, `EOD_CATEGORY_DIGEST_ACTIVE`, `WAVE_PTT_CHANNELS_ACTIVE`, `WAVE_COMPETITION_ACTIVE`, `LEAD_ROUTING_ACTIVE`
- [ ] Wave Lead / Senior Wave Lead assignments still not actually populated in the admin page (Spencer/Gallo need to be assigned for real)
- [ ] Never click-tested: Redeem Bonus, Invite-to-Website
- [ ] Amanda's naming suggestions for the incentive points backlog (Route Rewards / Fleet Coins / Driver Bucks) — logged, not decided
- [ ] WHC hours-tracking (§6) is the top-priority real gap if the CAP/BOC backlog gets picked up

---

## 8. How to Resume

1. Read `MEMORY.md` (auto-memory index) first — it's kept current and links to every topic below in more depth.
2. For **today's specific changes**, this document is the source of truth — re-read before assuming any behavior.
3. For **Wave Lead / Senior Wave Lead**, read `Governance/05_NDL_Wave_Lead_Module_SRD.md`.
4. For **CAP/BOC compliance monitoring / the video-gate idea / WHC gap**, read `Governance/06_NDL_CAP_Compliance_Monitoring_SRD.md`.
5. Confirm current Render/Vercel deploy state before assuming any of today's fixes are live — check the latest pushed commit hash against what's actually deployed.
6. Check Slack DMs (Austin Spitzer, Pedro Ibarra, Chris Espejo) for replies before re-asking about them.

---

## 9. Reference

- **Production API:** https://nday-om.onrender.com (Render, manual-deploy-only)
- **Production frontend:** https://nday-om.vercel.app (Vercel, auto-deploys on push)
- **Repo:** `C:\Users\chief\NDAY_OM_MODULAR` (going-forward repo — not the sibling `NDAY_OM`)
- **Memory index:** `C:\Users\chief\.claude\projects\c--Users-chief-NDAY-OM\memory\MEMORY.md`
- **Today's key commits:** `aa25701`, `689c1ab`, `1666ab1`, `d7e4d53`, `3c9c246`–`b833fe0` (sentiment survey series), `2c624f3`, `269fe96` (callout), `6e1e02d`, `5386740` (DVIC)

---

**Session closed: July 30, 2026. Ready for clean restart next session.**
