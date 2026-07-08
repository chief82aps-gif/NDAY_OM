# Manager Accountability Module — Rules & Configuration

## Purpose
Tracks manager on-duty schedules and automatically creates accountability
notices when required reviews are not completed by end of shift.

---

## Manager Schedule

| Manager | Schedule | Slack Username | Slack ID |
|---|---|---|---|
| Spencer Colby | Sun · Mon · Tue · Wed (Front Half) | `s.colby.ndl` | `U0BE493C5K9` |
| Galo (Fabian Marcillo) | Wed · Thu · Fri · Sat (Back Half) | `fabian` | `U0AJPQALDLL` |

> Wednesday is a shared day — both managers are scheduled.
> The system will notify **both** on overlapping days until further configured.

---

## Writeup Triggers (Catalysts)

A manager accountability notice is generated when ANY of the following
are not reviewed/actioned by end of shift:

| Code | Trigger | Source Module |
|---|---|---|
| `unsigned_callout` | Driver absence writeup not countersigned | `attendance` |
| `crash_unreported` | Vehicle incident report not reviewed | `incidents` *(future)* |
| `scorecard_flag` | DSP scorecard metric below threshold | `dsp_scorecard_weekly` *(future)* |
| `uniform_violation` | Uniform non-compliance not addressed | `uniforms` *(future)* |
| `dvic_unremediated` | DVIC violation not acknowledged | `dvic` *(future)* |

---

## EOD Review Deadline

- **Cut-off time:** 11:00 PM Pacific
- Any open items still unreviewed at cut-off trigger a next-morning notice

---

## Morning DM Schedule

- **Send time:** 6:00 AM Pacific (day after the unreviewed shift)
- **Recipient:** On-duty manager for the day the item was created
- **Channel:** Direct Message to manager's Slack account
- **Format:** Formatted accountability notice listing each unreviewed item
  with a direct link to review and sign

---

## Accountability Notice Contents

Each DM includes:
1. Date of the shift in question
2. List of items that were not reviewed (driver name, type, direct link)
3. Reminder of review policy
4. Acknowledgment button that records manager's acceptance of the notice

---

## Database Tables

### `manager_schedule`
Stores the recurring weekly schedule (day_of_week → manager name + Slack ID).
Can be updated via `PATCH /manager-accountability/schedule/{day}` without a code deploy.

### `manager_accountability_events`
One row per accountability notice issued. Tracks:
- `shift_date` — the date the original items were due
- `manager_name` — who was on duty
- `writeup_type` — see trigger table above
- `source_event_id` — ID of the original unreviewed item
- `dm_sent_at` — when the morning DM was dispatched
- `acknowledged_at` — when/if the manager acknowledged

---

## Adding New Writeup Catalysts

To add a new trigger (e.g., uniform violations):
1. Add a row to the Triggers table above with the new `code`
2. At EOD, call `POST /manager-accountability/eod-scan?type=uniform_violation`
   passing the list of unreviewed event IDs
3. The module handles scheduling the morning DM — no new loop needed

The `writeup_type` field is a free string — no enum change required.
