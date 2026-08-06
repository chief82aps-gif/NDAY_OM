# Package RTS / Non-Delivered-Marking Resolution Module — Planning Doc

**Status: DRAFT — offline, not to be built or activated.** This is a
placeholder for a real conversation with dispatch, not a spec to
implement from. Nothing in this doc should be turned into code or have
any feature flag flipped on until that conversation happens and this
doc is rewritten with real answers.

## Why this exists

The offender scrub (`api/src/routes/packages.py`) already tracks, per
driver, every non-delivered package marking (Reattemptable /
Undeliverable / Missing / Returned to station / Pickup failed) with
Amazon's own exact reason code. A direct-to-driver DM
(`send_offender_dm()`) was built on top of it — asking the driver why
they marked a package that way, whether they contacted the customer
first, etc. — but it shipped by guessing at tone and process rather
than from a real defined workflow, and has been paused
(`PACKAGE_OFFENDER_DM_ACTIVE`, default off) as of 2026-08-06 for
exactly that reason: **the data exists, but the resolution process
doesn't.**

## What's actually needed from dispatch (Luis, Spencer, and whoever else
should be in the room — per the user's own framing) before anything
here gets built or re-enabled

1. **When does a marking actually warrant contacting the driver?**
   Every single Reattemptable/Undeliverable marking, or only certain
   reason codes / certain frequency (e.g. 3+ in a day, a repeat pattern
   over several days)? The trailing-7-day habitual view already exists
   (`get_trailing_offender_report()`) — should that be the real trigger
   instead of "any new marking"?
2. **What should the driver actually be asked?** Did they contact the
   customer? Did they attempt delivery? Is there a specific
   confirmation step Blake should be walking them through before they're
   allowed to mark a package this way (see the related, not-yet-built
   "package marking permission" idea — separate backlog item)?
3. **What counts as resolved, and who decides?** Does dispatch review
   the driver's answer and close it out, or does anything the driver
   says get accepted at face value? Is there a dispute path if a driver
   says the marking was legitimate (customer genuinely unavailable,
   business genuinely closed, etc.)?
4. **Tone.** The existing #nday-mgt alert text already says "for human
   review, not an automatic write-up" — does the same framing apply to
   a driver-facing message, or does dispatch want something firmer once
   a real pattern is confirmed?
5. **Escalation.** At what point (if any) does a pattern of markings
   turn into an actual write-up / points event, distinct from just a
   conversation? If ever, that almost certainly belongs in the existing
   points/discipline ladder (`attendance.py`'s HRM-023.1), not a new
   parallel one — see CLAUDE.md's existing rule against building a
   second consequence ladder.

## What already exists and should NOT be rebuilt

- `get_driver_status_counts()`, `get_new_unable_to_deliver_since_last_snapshot()`,
  `get_trailing_offender_report()` — all in `packages.py`, already
  correct and already feeding the #nday-mgt alert (which is unaffected
  by this pause — that alert keeps posting).
- `send_offender_dm()` — the DM-sending mechanics (Slack DM, per-driver
  try/except) are fine as infrastructure. What's missing is the actual
  message content and trigger logic, which is exactly what this
  conversation needs to define.

## Next step

Get this meeting on the calendar with dispatch. Once real answers exist
for the five questions above, rewrite this doc with them and only then
consider re-enabling `PACKAGE_OFFENDER_DM_ACTIVE`.
