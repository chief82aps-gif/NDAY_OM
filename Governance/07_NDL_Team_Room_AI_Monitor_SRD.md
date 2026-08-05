# Team Room AI Monitor — Build Spec

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** AI monitor for #nday-team-room — equipment/injury/incident/dog-bite/customer-complaint detection, van-equipment flagging, drafted replies
**Status:** Built and live 2026-08-05. Draft-first (human-approval) mode only — see §5 before ever changing that.
**Code:** `api/src/routes/team_room_monitor.py`, `TeamRoomFlag` in `api/src/database.py`, hook in `api/src/routes/slack_interactions.py`'s `/events` handler, digest wiring in `api/src/routes/ops_daily_digest.py`.

---

## 1. Origin

User's own framing (2026-08-05), while looking at real #nday-team-room chatter: "you will see that j.rogers.ndl just posted that he needs a charger. We wanna look for things like this and track it specific to the VIN that he inspected, and we wanna flag that van as having lacked the equipment that is required. This would be something that gets reported up in the summary... we do wanna monitor all chat for things like this, things like injury, dog bites, care and customers... Blake should respond back on things like that... hey Rogers, I see that you're in van twenty one zero seven, are you needing a cable or a mount?"

Followed immediately by a second, related ask: when something's flagged, Blake should also check who had that van most recently and ask them too — "hey Snuffy, did you see that this charger was missing? I'm wondering why inspections didn't catch it."

## 2. What it does

1. Every real (non-bot) message posted in **#nday-team-room** is classified by Claude into one of five categories, or "none":
   - `equipment_issue` — missing/broken/malfunctioning van equipment
   - `injury` — driver mentions being hurt
   - `incident` — safety incident, near-miss, property damage (not a crash — that has its own report)
   - `dog_bite` — dog bite / aggressive-dog encounter
   - `customer_complaint` — a customer was rude, threatening, or caused a problem
2. For `equipment_issue`, the model also tries to extract a van/unit number and the specific equipment named, and drafts a clarifying reply in Blake's voice (e.g. "Hey Rogers, I see you're asking about van 2107 — are you needing the charger cable itself, or the mount?").
3. **Van resolution is always via that day's actual assignment, never a fixed lookup.** `van_number` is **not** a stable vehicle alias — `route_assignment.py`'s daily allocator can put a different physical VIN behind the same van number on a different day. The reporter's `DailyRouteAssignment.vin`/`van_number` for **today** is authoritative; the message's own mentioned van number is only a fallback if no assignment exists yet.
4. For equipment issues where a VIN is resolved, the system looks up the most recent **other** driver assigned that same VIN **before today** and drafts a second, separate message asking whether they noticed the issue during their own inspection — the "Snuffy" pattern from the origin request.
5. Both drafts post as a review card to **#nday-mgt** with **Approve / Dismiss** buttons. Nothing posts into #nday-team-room until a human clicks Approve.
6. Approved/flagged items surface in the existing Daily Ops Digest (`ops_daily_digest.py`): equipment issues in the Fleet section, the other four categories in the HR section — this is the "reported up in the summary" requirement.

## 3. Data model — `TeamRoomFlag`

One row per detected message. Key fields: `category`, `raw_text`, `van_number`/`vin`, `equipment_description`, `draft_reply_text` + `reply_status` (pending/approved/dismissed), and the parallel `prior_driver_name`/`prior_driver_draft_text` + `prior_driver_reply_status` for the van-history follow-up. `review_message_ts` holds the #nday-mgt card's Slack ts so it can be referenced/updated later if needed.

## 4. Why no new "grounded equipment" field on `Vehicle`

`Vehicle` already has `status` (active/grounded/maintenance) and a generic `notes` Text field, but nothing structured for "this specific equipment is missing." Rather than jamming free text into `notes` (unqueryable, no history), flagged equipment issues live in their own table (`TeamRoomFlag`), matching this session's established pattern of small dedicated tables over ad-hoc note fields (see `AppGlitchReport`, `AppSuggestion`). If equipment flags need their own admin view/resolution workflow later, that's a natural next step — not built yet.

## 5. Draft-first is a deliberate, explicit decision — do not change without asking

Confirmed directly with the user (2026-08-05): **"draft first for now, we can fully automate once we have a good feeling on how it is working."** Every other AI-drafted, outward-facing message in this codebase (the sentiment-survey "Respond as Blake" feature) already requires a human click before sending — there was, before this feature, **no precedent anywhere in this codebase for a fully-automatic bot reply into a public channel with zero human review.** A wrong VIN guess, a misread message, or an off-tone reply is far more visible and costly in a channel everyone reads than in a private DM.

**If asked to make this fully automatic:** that's a real, intentional escalation in trust level, not a small config flip — confirm explicitly which categories (the user's own alternative offered mid-conversation was "automatic for equipment questions only, injury/incident/customer-complaint always to a human") before wiring `_handle_approve`'s logic to fire straight from `handle_team_room_message()` instead of waiting for the #nday-mgt button click.

## 6. Known limitations / honest gaps

- Classification quality depends entirely on Claude's read of a short, casual chat message — no confidence threshold or "I'm not sure" path exists; anything the model doesn't classify as one of the five categories is silently dropped (no log of near-misses to review).
- The van-history follow-up only looks at `DailyRouteAssignment` rows — if a driver's most recent time with a VIN was more than the retained assignment history, or the assignment data has a gap, no prior driver will be found and that part is silently skipped (this is intentional — no prior driver to ask is not an error).
- No de-duplication if the same equipment issue gets mentioned twice in one day (e.g., a driver posts about the same charger issue in two separate messages) — each message is classified independently and would produce two separate flags/review cards.
- Requires `ANTHROPIC_API_KEY` to be set; if absent, `classify_team_room_message()` returns `None` and messages simply aren't flagged at all (no error, no alert that classification is off).
