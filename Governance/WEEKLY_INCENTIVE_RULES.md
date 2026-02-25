# Weekly Incentive Rules

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
