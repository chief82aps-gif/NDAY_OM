# Showtime Module — Rules

## RULE ONE (do not violate this again)

**Showtimes ALWAYS use the NEXT day's information — never the current/same day.**

The nightly driver-schedule file is uploaded in the evening (5:30–8:00 PM
Pacific window) specifically to set up **tomorrow's** shift. Every
Showtime DM, the Showtime Summary (`#nday-mgt` + `#nday-team-room`), and
every wave/show-time annotation must resolve to that next calendar day,
computed from **when the file was uploaded**, not from any date embedded
in the file's own filename or a wall-clock "today" captured at the wrong
point in the pipeline. If a change to this logic is not obviously and
directly resolving to "tomorrow relative to upload time," it is wrong —
stop and re-derive it from this rule before shipping.

This has broken in production **repeatedly** (see Incident History
below) because the same underlying bug was fixed in one place while a
second, independent copy of the same logic kept the bug alive elsewhere.
**Before touching any date-resolution code in this module, grep for
every place `schedule_date`, `_resolve_selected_schedule_date`, or
`primary_date_obj` is computed or consumed — fix all of them together,
not one at a time.**

## Where this logic actually lives

- `api/src/ingest/driver_schedule.py` — `_resolve_selected_schedule_date()`
  picks which date-column header in the uploaded Excel file is "the"
  schedule this file establishes. This is the ONE place that decides
  what date the whole rest of the pipeline treats as "the schedule
  date." Anchor it to `(file's own upload timestamp) + 1 day`, matching
  a header column for that date — never to the timestamp's own date.
- `api/src/routes/ops_ingest.py` — after ingest, uses the ingest's
  `schedule_date` result (`primary_date_obj`) to call, in order:
  `rostering.send_driver_shift_dms()`, `rostering.post_mgt_summary()`,
  `rostering.post_showtime_summary()`. All three fire for whatever date
  the ingest resolved — if that's wrong, everything downstream is wrong
  for the same reason, at the same time.
- `api/src/routes/rostering.py` — `run_showtime_watchdog()` is a
  SEPARATE, independent safety net: every 60s from 6 PM Pacific, it
  computes "tomorrow" directly from the wall clock (`now + 1 day`), not
  from the ingest's resolved date at all, and retries
  `send_driver_shift_dms()` against that correct date if the ingest's
  own attempt was wrong or incomplete. This is why a wrong-date ingest
  doesn't always cause visibly missing Showtime DMs — the watchdog
  quietly does the right thing in parallel. **Do not mistake the
  watchdog papering over the symptom for the underlying ingest bug being
  fixed** — both must independently compute the correct date; one
  covering for the other's bug is not a fix.

## Incident History

- **2026-08-05 (found):** `_resolve_selected_schedule_date()` matched
  the file's header columns against its OWN upload-timestamp date
  directly — i.e., always resolved to "today" (the upload day), never
  "tomorrow." This had been true since the function was written
  (2026-07-15) and was even called out as a known limitation in that
  commit's own message, but never fixed for the value the rest of the
  pipeline actually uses. Fixed by anchoring the match to
  `timestamp_date + 1 day` instead.
- **2026-08-05/06 (fix landed, but the very next real ingest still showed
  the bug):** The fix was correct going forward, but the ingest that ran
  the same evening (`Week-32-Schedule (3).xlsx`, detected ~6:19 PM
  Pacific) had already completed **before** the fix deployed a few hours
  later. Its stored result (`schedule_date: 08/05/2026`, i.e. same-day,
  not next-day) was never re-computed — nothing re-runs a past ingest's
  date resolution after a code fix ships. That stale, already-wrong
  result is what drove that night's actual Showtime DM/summary sends,
  which is why the bug appeared to "come right back" the next morning
  even though the code was already fixed. **Lesson: a date-resolution
  fix is not actually verified until a REAL subsequent ingest, occurring
  entirely after the fix is live, produces the correct date — a
  synthetic unit test against constructed data is necessary but not
  sufficient.**

## Verification checklist for any future change here

1. Unit-test `_resolve_selected_schedule_date()` against the real header
   format (`"Sun, 22/Feb"` style) and a real timestamp string format
   (`"8/5/2026, 6:15:23 PM"` style) — confirm it resolves to
   `timestamp_date + 1 day`, not `timestamp_date`.
2. Deploy, then **wait for the next real evening ingest** (do not
   consider this closed on unit tests + deploy alone). Check
   `GET /ops-ingest/jobs` for the new `driver_schedule` job's
   `result.schedule_date` and confirm it's tomorrow relative to that
   job's own `detected_at`, not today.
3. Cross-check `GET /rostering/shift-dms/{that resolved date}` shows
   real `dm_sent_at` timestamps from that evening, for the correct
   (next) date — not a same-day repeat of an already-sent date.
