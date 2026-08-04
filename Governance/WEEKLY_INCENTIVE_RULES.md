# Weekly Incentive Rules

> Discovery: Browse all governance docs in [Governance Index](README.md).

## Status: NOT YET IMPLEMENTED (confirmed 2026-08-04)

This document describes the target rules — none of it is live yet.
`api/src/ingest/weekly_incentive.py`'s `parse_weekly_incentive_pdf()` is
a stub (`return {}, []`) with no parsing, rate calculation, or storage
logic. `ops_ingest.py` recognizes and labels a weekly-incentive file by
type/filename, but nothing downstream actually extracts or validates
its contents against the rates below. This is the DSP's primary revenue
calculation (not the per-driver bonus — see `DRIVER_SCORING_RULES.md`
for that, a separate and unrelated NDAY-computed metric), so it's
tracked as a real priority in `UPGRADE_BACKLOG.md`, not just background
debt.

## Purpose
Store weekly incentive invoice values driven by DSP scorecard rating and total packages delivered.

## Source File
- Weekly incentive PDF (example: US_FLEXPRO.NDAY.DLV3.INC.2026.7_2.pdf)

## Reference Key
- `invoice_number` (from the PDF header)

## Inputs
- Total packages delivered (from WST Delivered Packages Report)
- Weekly scorecard rating (from DSP Scorecard)

## Rates by Scorecard
- Fantastic Plus: Total packages * $0.15
- Fantastic: Total packages * $0.07
- Great / Fair / Poor: Total packages * $0.00

## Store
- Week number / date range
- Rating
- Total packages
- Rate applied
- Calculated amount
- Invoice amount

## Validation (Later)
- Compare calculated amount vs invoice amount.
- If rating is not Fantastic/Fantastic Plus, amount should be $0.00.
