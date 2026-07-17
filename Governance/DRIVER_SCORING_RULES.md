# Driver Individual Scoring Rules

> Discovery: Browse all governance docs in [Governance Index](README.md).

## 1. Purpose & Origin

Decided 2026-07-17: NDAY needs a per-driver percentage score that mirrors
the DSP Scorecard's own weighting, so drivers can be coached and
eventually paid a high-performer bonus based on the same metrics Amazon
already grades the company on — rather than trusting Amazon's own
per-driver `Overall Score`/`Overall Standing` as an opaque black box.

**Company-level context** (for reference, not implemented in code): NDAY
must hit above 90% overall and cannot be rated "Great" in Safety to earn
top incentive tier. This document is about the *individual driver* score,
which is a separate, NDAY-computed metric — not Amazon's own DSP-wide
scorecard rating (see `WEEKLY_INCENTIVE_RULES.md` for that).

---

## 2. Formula

The individual score mirrors the real scorecard's category weights
(Appendix A of the weekly PDF), computed from the per-metric scores
Amazon already supplies in the weekly per-driver CSV — **not** a
re-derivation from raw rates, and **not** Amazon's own `Overall Score`.

### 2.1 Two deliberate departures from the real scorecard

- **Team & Fleet is dropped entirely** (Tenured Workforce 0% + Fleet
  Execution 5%). Neither is something an individual driver's own
  behavior controls — Tenured Workforce is a DSP-wide workforce-tenure
  percentage, Fleet Execution is vehicle/VIN-level rotation compliance.
  That 5% is reassigned to a new **Attendance** component instead.
- **Driver tenure is not part of the weighted score at all.** It's a
  pass/fail eligibility gate (see §3), same as the 30-route floor.

### 2.2 Category weights

| Category | Weight | Components |
|---|---|---|
| **Safety** | 47.6% | Speeding 11.7 · Seatbelt 11.7 · Sign/Signal 11.7 · Distractions 7.5 · Following Distance 5.0 |
| **Quality** | 47.4% | DC DPMO 11.3 · DSB 11.3 · POD 2.8 · CDF DPMO 17.0 · PSB 5.0 |
| **Attendance** (new) | 5.0% | `100 - (trailing-60-day attendance points × 10)`, floored at 0 |

**Note on CDF:** the real scorecard splits Customer Delivery Experience
into Customer Delivery Feedback DPMO (5.7%) and Customer Escalation
Defect DPMO (11.3%). Amazon's per-driver CSV only gives one combined CDF
DPMO score, not split — so here CDF stands in for the full 17.0%
category. This is an honest data-availability constraint, not a policy
choice; revisit if Amazon ever splits it at the per-driver level.

### 2.3 Missing-metric handling

A `None`/missing component score (e.g. "Coming Soon") is dropped, and the
remaining weights in that category are renormalized among themselves —
the same handling the real scorecard documents in its own Appendix A.
One missing metric never unfairly zeroes out a category.

### 2.4 Attendance component

Reuses `attendance.py`'s existing HRM-023.1 points ladder — no new data
collection:

| Event | Points |
|---|---|
| No-show | 5.0 |
| Call-in | 2.0 |
| Late arrival | 1.0 |
| Early departure | 0.5 |
| Present / excused | 0.0 |

`attendance_score = max(0, 100 - trailing_60_day_points × 10)` — 10
points is that system's own existing termination threshold, so a driver
at the termination line scores 0 on Attendance, not an arbitrary cutoff.

---

## 3. Eligibility Gates (Ranking + High-Performer Bonus)

Two gates, **both** required — an ineligible driver still gets a score
computed and shown (useful for coaching/"here's what you're leaving on
the table"), just flagged as not eligible for ranking or bonus pay:

1. **Tenure**: `Tenure Status == "Tenured"` from the driver's most recent
   `TenuredWorkforceRecord` row (see §4 for the data source). Not a
   calendar-day hire-date check — uses Amazon's own already-computed
   Tenured/Not Tenured determination directly.
2. **Route volume**: `>= 30 routes` in the trailing 6 weeks, computed as
   `SUM(routes_in_week)` over a driver's 6 most recent
   `TenuredWorkforceRecord` rows (`get_trailing_route_count()`). Sum-based
   rather than a lifetime-routes delta so gap weeks (driver didn't work a
   given week) don't break the calculation.

---

## 4. Data Source: Tenured Workforce DAs Report

Amazon's own tenure/lifetime-route report, keyed by **Transporter ID**
(the stable, cross-system driver identifier — survives payroll-provider
or email changes, unlike `payroll_name` string matching used elsewhere in
this app today).

- **Where to find it**: `logistics.amazon.com` → Performance →
  Interactive Report → Supplementary Reports → **TWF Dashboard**.
- **How to export**: three-stacked-dots menu (⋮) → **Export to CSV**.
- **Format**: `.csv` or `.xls` — same "we don't control the file type"
  principle as `DOP_ROUTE_SHEET_INGEST_RULES.md` §1. Parsed via the same
  content-sniffing `read_tabular_file()`.
- **Cadence**: weekly, by **COB (5 PM) every Friday**. `mgt_reminders.py`
  nags `#nday-mgt` every 5 min from 5:00–11:59 PM PT on Fridays only
  (`weekday=4`) until detected, including the portal-navigation
  instructions above in the reminder text itself.
- **Column note**: the source file's own header literally reads
  `"Trabsporter ID"` — Amazon's typo, not ours. Kept as `transporter_id`
  in our schema; the parser matches the literal misspelling.
- **Re-export behavior**: the same file re-exports Amazon's **full
  history** every week (53+ weeks in the first real pull, 2026-07-17),
  not just the current week. Ingestion upserts by
  `(transporter_id, year, week)` rather than replacing a whole
  `source_file`'s rows the way DOP/Cortex do — past weeks are immutable,
  only the newest week is actually new data on a given Friday.
- **Auto-ingested**: included in the generalized safe-type auto-ingest
  list (pure DB storage, no Slack side effects) — no manual "click
  ingest" step needed once detected.

---

## 5. Color Thresholds

Applied uniformly to Overall, Safety, Quality, and Attendance:

| Color | Threshold |
|---|---|
| 🟢 Green | ≥ 92% |
| 🟡 Yellow | ≥ 90% and < 92% |
| 🔴 Red | < 90% |

High-performer bonus eligibility = Overall ≥ 92% **and** both eligibility
gates (§3) pass.

---

## 6. Status / Not Yet Built

- [x] Scoring formula (`compute_driver_scores()`) — implemented, unit-verified against hand-calculated values
- [x] Tenured Workforce ingest pipeline + Friday reminder
- [x] `GET /driver-scoring/scores` endpoint
- [ ] Visual list/report for reviewing all drivers at once (requested, not yet delivered — pending a production data pull)
- [ ] Home screen (Slack Home tab) display of a driver's own score + bonus-eligible indicator
- [ ] Actual bonus **dollar amount** calculation/messaging ("here's what you're leaving on the table")
- [ ] `DriverRosterEntry.transporter_id` column — doesn't exist yet; today's join to Tenured Workforce data goes through `QualityMetricDriver.transporter_id` instead. Worth adding once more of the app migrates off `payroll_name` string matching.
- [ ] Manual correction path for Tenured Workforce data (agreed: reuse the existing single-driver edit page pattern rather than a dedicated button, for the rare real discrepancy — not built yet, low priority since the report is authoritative)

---

## 7. This Document Governs

- Scoring formula, weights, renormalization: `compute_driver_scores()` (`api/src/routes/driver_scoring.py`)
- Tenured Workforce ingest: `_store_tenured_workforce()` (`api/src/routes/tenured_workforce.py`)
- Tenured Workforce schema + query helpers: `TenuredWorkforceRecord`, `get_latest_tenure_record()`, `get_trailing_route_count()` (`api/src/database.py`)
- Friday COB reminder: `mgt_reminders.py` (`tenured_workforce` key)
- Attendance points reused (not owned by this doc): `attendance.py`'s `POINT_VALUES`/`_driver_points_summary()`

**Any changes to these rules must**:

1. Update this document
2. Update relevant code comments
3. Include a version/date stamp in the commit message

---

## 8. Quick Reference: Rules Checklist

- [ ] Safety (47.6%), Quality (47.4%), Attendance (5.0%) — Team & Fleet dropped, not just zero-weighted
- [ ] CDF DPMO stands in for the full 17.0% Customer Delivery Experience category (per-driver data isn't split further)
- [ ] Missing metric → drop it, renormalize the category's remaining weights
- [ ] Attendance = `100 - (trailing 60-day points × 10)`, floored at 0
- [ ] Ranking/bonus eligibility requires Tenure Status == "Tenured" AND ≥30 trailing-6-week routes — both required
- [ ] A driver failing eligibility still gets a score shown, just flagged ineligible
- [ ] Green ≥92%, Yellow ≥90%, Red <90% — applied to all four displayed scores, not just Overall
- [ ] Tenured Workforce report: CSV or XLS, Fridays by COB, from TWF Dashboard, keyed by Transporter ID
