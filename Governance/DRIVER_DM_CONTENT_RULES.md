# Daily Driver DM — Content Rules

This governs what information the daily driver assignment DM (`send_day_of_dms()`,
`api/src/routes/rostering.py`) contains. It replaces the physical driver
handout card as the primary way a driver gets their daily assignment —
see `Governance/DSP_Route_Manager_Software_Manual.md` Section 7 for the
legacy printed-card spec this supersedes. Not every field from the
printed card carries over; this doc is the explicit record of what does,
what doesn't, and why, so the DM's content doesn't drift field-by-field
without a documented decision.

Driver DMs are gated by `DRIVER_DM_ACTIVE` — see
`Governance/ROSTERING_DM_RULES.md`. This doc governs *content*, that one
governs *whether anything sends at all*.

## Current fields (live today)

Sent as Slack Block Kit fields, only included when data is available
(no blank/dash placeholders):

| Field | Source | Notes |
|---|---|---|
| Route | `DailyRouteAssignment.route_code` | |
| Van | `DailyRouteAssignment.van_number` | |
| Staging | `DailyRouteAssignment.stage_location` | |
| Showtime | `_calc_showtime(wave)` | Wave time minus prep buffer |
| Wave | `DailyRouteAssignment.wave` | |
| Est. Return | `_calc_return_time(wave, route_duration)` | See "Duration" below |
| Wave Lead | `_wave_lead_name(shift_date)` | Who to contact with questions |

Plus a non-content element: an **Acknowledge / arrival-confirmation
button** (Block Kit `actions` block) the driver taps on arrival — not a
data field, but part of every DM's structure.

## Fields intentionally excluded (carried over from the printed card, decided out)

- **Packages / Stops** — removed 2026-07-11 per explicit decision to
  reduce DM clutter. Do not re-add without a new explicit decision — see
  git history on `rostering.py`'s `send_day_of_dms()` around that date.
- **Bags / Overflow tables** — these were a printed-card-only concept
  (physical loadout detail for handling at the station); they don't
  belong in a driver-facing daily DM and were never carried over.
- **Route Duration (as a visible field)** — decided 2026-07-13: Duration
  stays **computed-only**, feeding `Est. Return`, and is never shown to
  the driver directly as its own field. Do not add a visible "Duration"
  field without revisiting this decision.
- **Static motivational quote** — the printed card had one fixed quote
  ("One stop at a time — finish strong.") on every card, unconditional.
  Decided 2026-07-13: this does **not** carry over as-is. It's being
  replaced by the personalized coaching message below instead of a
  generic quote.

## Planned, not yet implemented: Performance Coaching Message

Decided 2026-07-13, **not yet built** — governed by a future, separate
module (not part of `rostering.py`). When built, the DM will include:

- One **improvement area**, softly worded (coaching tone, not punitive —
  consistent with the DVIC escalation ladder's stage-1 tone; see
  `api/src/routes/dvic.py`'s `_counseling_message()` for the established
  house style of "encourage, don't scold").
- One **positive reinforcement** ("Atta Boy") — something the driver is
  doing well, individually, not generic praise.

This requires per-driver performance data + selection logic that doesn't
exist yet. **Do not implement ad hoc inside `rostering.py`** — this is
explicitly reserved for its own module once the criteria are defined,
same pattern as DVIC/discipline-tracker/document-routing were each given
their own module this session rather than bolted onto an existing file.

## Planned, partially implemented: ACE Eligibility

Decided 2026-07-13: the DM will include an **"ACE Eligibility"** field.
Eligibility criteria are **not yet defined** — a future module will own
the actual determination (same module as the coaching message above, or
a related one). **Until that module exists, every driver's DM shows
`ACE Eligibility: TBD`** — a static placeholder, not a real
computation. Do not attempt to derive real eligibility logic without a
new explicit decision; the placeholder is intentional, not a bug.

## Interim stand-in: Driver Summary Matrix (while Slack-linking is incomplete)

As of 2026-07-14, **0 of 102 drivers have a linked Slack account**
(`DriverRosterEntry.slack_member_id`) — checked via `/drivers`. Real
per-driver DMs can't reach anyone until that's resolved (see
`scripts/import_ssn_slack.py`, not yet run/wrapped as an endpoint).

Until then, `post_driver_summary_matrix()` (`api/src/routes/rostering.py`,
`POST /rostering/driver-summary-matrix/{shift_date}`) posts a table to
`#nday-mgt`, grouped by wave with the wave lead noted per group. Per
explicit request 2026-07-14, this table is **not** a 1:1 mirror of the
driver DM — it's management-facing, so it also includes two fields the
DM itself does not show:

- **Perf** — quality standing (Platinum/Gold/Silver/Bronze), same source
  as the route summary matrix's Perf column (`_latest_quality_map()`).
- **Safety** — average pre-trip inspection time + instance count from the
  **most recently ingested DVIC week only** (e.g. `71s/2x`), per explicit
  request 2026-07-14: "use the avg and the most recent previous dvic."
  Reuses `dvic.py`'s own `_mgt_summary_rows()` so this and the DVIC MGT
  summary can never compute it differently. Deliberately **not** the
  persistent counseling stage (`DvicCounselingRecord`) — that's a
  different, longer-horizon signal; this column is this-week-only. Shows
  `—` if the driver has no DVIC data for the latest week.

Full column set: Driver, Route, Van, Staging, Showtime, Est. Return,
Perf, Safety, ACE Eligibility (`TBD`). This is a management-visibility
stand-in, **not** a driver-facing send — it's gated by `ROSTERING_ACTIVE`
(management-facing), not `DRIVER_DM_ACTIVE`, since nothing goes to an
individual driver. Meant to run alongside `post_assignment_matrix()`
(the route summary matrix) each morning. If Perf/Safety are ever wanted
in the *driver's own* DM too, that's a separate decision — don't assume
parity between this table and the DM content above just because they
share most fields.

**Once driver Slack-linking is resolved and real per-driver DMs go live**,
re-evaluate whether this stand-in should keep running — decide explicitly
rather than leaving two overlapping mgt-channel posts by default.

## Adding or changing a field

Any change to what this DM contains — adding, removing, or changing how
a field is computed — should update this doc in the same commit. If it's
not listed here, it's not an intentional part of the DM's content.
