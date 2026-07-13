# Rostering & Driver DM Rules

## Testing Gate (effective 2026-07-11)

**Driver-facing Slack DMs must not go live until the rostering pipeline has been fully tested end-to-end. The Slack assignment matrix stays active and continues posting as normal — it is not affected by this gate.**

Do not set `DRIVER_DM_ACTIVE=true` on Render until testing is signed off.

### Why this matters

`api/src/routes/rostering.py` uses two independent feature flags:

```python
_ACTIVE    = os.getenv("ROSTERING_ACTIVE", "false").lower() == "true"
_DM_ACTIVE = os.getenv("DRIVER_DM_ACTIVE", "false").lower() == "true"
```

- **`ROSTERING_ACTIVE`** gates ops/lead-facing sends that are already trusted and stay live: `post_assignment_matrix()` (the Slack summary matrix), `post_mgt_summary()`, `send_nightly_roster_reminder()` (DMs to Spencer/Luis/Fabian), `send_wave_lead_pre_wave_dm()`, `notify_wave_lead_driver_arrived()`, `send_missing_drivers_summary()`.
- **`DRIVER_DM_ACTIVE`** (defaults to `false`) gates every DM sent directly to a driver: `send_driver_shift_dms()` (pre-shift DM), `send_day_of_dms()` (morning assignment DM), `send_eod_checklist_dms()` (end-of-day checklist DM). These stay off until explicitly enabled.

### Ingest never auto-posts (fixed 2026-07-12)

`api/src/routes/ops_ingest.py`'s Cortex-file dispatch used to automatically
call both `send_day_of_dms()` and `post_assignment_matrix()` immediately
after any Cortex file was auto-ingested from Slack — a silent coupling
that reposted the matrix (and would have DM'd drivers, had the gate above
not existed) as a side effect of just loading corrected data. This was
removed: ingesting a file now only updates data. Posting the matrix or
sending driver DMs is always a separate, explicit call
(`POST /rostering/assignment-matrix/{date}?force=true` and
`POST /rostering/day-of-dms/{date}` respectively). Don't reintroduce an
auto-post-on-ingest coupling anywhere in this codebase — it's the same
mistake in a different module every time.

### Before flipping `DRIVER_DM_ACTIVE=true`

- Confirm the full pipeline (DOP → Cortex → DailyRouteAssignment → driver DM) has been tested end-to-end on real or representative data.
- Confirm `DailyRouteAssignment.route_duration` is populated correctly (see the 2026-07 `route_duration` null-value fix).
- Get explicit sign-off before changing the flag in the Render dashboard — this is a production-visible change affecting real drivers.

See also: [Van Assignment Rules](VAN_INGEST_RULES.md), [DSP Route Manager: Software Manual & System Blueprint](DSP_Route_Manager_Software_Manual.md).
