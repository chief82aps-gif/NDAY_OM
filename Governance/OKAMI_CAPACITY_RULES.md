# Okami Capacity — Business Rules

Governs `/okami-capacity` (frontend) and `api/src/routes/okami_capacity.py`
(backend). This module replaced the original plan to detect an Okami
file upload (see `mgt_reminders.py`'s "okami" reminder key) — Okami
numbers arrive as a short Slack message ops types by hand, not a file,
so they're entered directly via a dashboard form instead.

## Fields (as of 2026-07-15)

| Field | Source | Notes |
| --- | --- | --- |
| DAs | Ops, from the day's roster | Delivery Associates available |
| Okami | Ops, from Amazon's Okami tool | Amazon's own forecasted route count |
| Capacity (base) | Ops | Station's own committed route count |
| 4x4 | Ops | Count of 4x4-eligible add-on routes |
| Capacity total | Computed | `capacity_base + capacity_4x4` |
| Vans | Ops | Vans available today |
| FRT | Ops, from Amazon's scheduling page | **Optional** — see below |

## FRT (Flex Up Route Target)

FRT is Amazon's own daily ask, shown on their scheduling page as a
"Flex up target" row **below** the normal "Route target" row — it only
appears some weeks (the week this was built, there was no flex-up row
at all). It is **not** a fixed station constant and is **not** always
present — it's entered per-day, left blank when Amazon isn't asking for
one.

**Check**: if `frt` is set and `capacity_total < frt`, that's an FRT
miss. Remediation (per explicit 2026-07-14 decision):

1. Flag for a human to review the schedule for drivers who could pick
   up extra shifts. **Phase 1 (built): alert only** — the auto-suggested
   driver list itself is deferred to a follow-up pass.
2. DM every member of `#nday-mgt` (`SLACK_MGT_CHANNEL`, default
   `C0BCYAW7QP3`) — per the user, all but one member of that channel are
   drivers, so this channel doubles as the flex-up driver pool.
3. DM Jayson and Tamra specifically — via `RoleDirectory`'s `owner` /
   `hr` roles (`document_routing.py`), reusing the existing Slack-ID
   mapping rather than a new one.

If `frt` is left blank, the check is skipped entirely — no false alarm
on a normal week.

## DA coverage (informational)

Target ≈ 110% of `capacity_total` by default — the driver-to-route
ratio needed to cover callouts and provide sweepers. Tunable via
`driver_buffer_pct` in `OkamiSettings` (default `10`, meaning 110%).
`required_da_count = ceil(capacity_total * (1 + driver_buffer_pct/100))`.

No notification target was specified for a DA shortfall — it's surfaced
in the finalize summary posted to `#nday-mgt` only.

## Van coverage

Target = `capacity_total * (1 + van_buffer_pct/100)` — `van_buffer_pct`
is a separately-tunable knob from the driver buffer (explicit
2026-07-14 decision: two knobs, not one shared percentage), default `0`.

`effective_available_vans = van_count + available_non_okami_vehicles - 1`
— the `-1` is a standing conservative assumption ("we assume one van
will be grounded upon return to station"), applied every time regardless
of what's currently flagged grounded.

If `effective_available_vans < required_van_count`, that's a van
shortfall:

- DM every member of `#nday-mgt` **and** every member of `#nday-fleet`
  (`SLACK_FLEET_CHANNEL`, default `C0BJ8J5LGAU`, same hardcoded-default
  pattern as `SLACK_MGT_CHANNEL` — channel IDs aren't treated as
  sensitive the way personal Slack member IDs are in this repo).
- The message includes the live list of currently-grounded vans, pulled
  from `Vehicle.status == 'grounded'` (already populated by the
  existing Fleet-file ingest pipeline — no new data source needed) and
  the shortfall count.
- Recipients are deduped if someone is in both channels.

## Available_Non_OKAMI_Vehicles

A tunable count (`OkamiSettings.available_non_okami_vehicles`, default
`0`) for vehicles that don't flow through the normal Okami/Fleet-ingest
channel — e.g. the one 4x4 mentioned 2026-07-14 that's temporary and
off the books. Added to `van_count` when computing effective available
vans (see above).

## Buffer percentages

`driver_buffer_pct` and `van_buffer_pct` live in `OkamiSettings` (singleton
row, `GET`/`PUT /okami-capacity/settings`) — explicitly meant to be
tuned over time as real experience shows the right cost/risk tradeoff
(higher % = more buffer, higher cost; lower % = less buffer, higher
risk of a miss). No UI for editing them was built yet beyond the raw
endpoints — add one if/when this needs to be adjusted more than
occasionally via a direct API call.

## Draft vs. Finalize (explicit 2026-07-14 decision)

Two-step, not one:

1. **Draft** — `POST /okami-capacity` any number of times a day.
   Append-only (same philosophy as ingest elsewhere in this repo): a
   correction is just a fresh row, not an edit. This alone satisfies
   `mgt_reminders.py`'s "did ops engage with Okami today" check — a
   draft counts, it doesn't need to be finalized.
2. **Finalize** — `POST /okami-capacity/finalize`. Locks in the latest
   (or a specified) submission, computes and snapshots all the checks
   above, posts the `#nday-mgt` summary, and fires any threshold DMs.
   **Re-finalizing after a correction is intentional** — it re-runs
   every check and re-sends every notification. This is not a dedup bug;
   if a number was wrong and already alerted people, re-finalizing after
   fixing it is how you correct the record (and re-notify if the
   correction changes the outcome).

## Known gaps / deferred

- The FRT-miss "list of drivers who can work extra shifts" is not
  auto-generated yet — phase 1 is alert-only. A real version needs to
  query the driver schedule for who's off/available, which is its own
  follow-up.
- No UI exists yet for editing `driver_buffer_pct` / `van_buffer_pct` /
  `available_non_okami_vehicles` — only the raw `GET`/`PUT
  /okami-capacity/settings` endpoints.
