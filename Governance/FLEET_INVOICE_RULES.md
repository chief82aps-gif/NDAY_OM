# Monthly Fleet Invoice Rules

## Purpose
Capture monthly fleet invoice data for forward-looking prepayment and prior-month reconciliation.

## Source File
- Monthly fleet invoice PDF (example: US_FLEXPRO.NDAY.1d49942f-2dd3-4a85-8a97-79c3a3e07be1.2026FEB_AP600001_2 (1).pdf)

## Reference Key
- `invoice_number` (from the PDF header)

## Sections to Store
### 1) Prepayment (forward-looking month)
- Branded van prepayment (rate * branded van count)
- Rental forecast prepayment (if listed)
- AFCS (Authorized Fleet Size) values for comparison

### 2) Reconciliation (prior month)
- Adjustments for forecast vs actual fleet usage
- Separate counts for self-owned vs rental (if listed)

### 3) Weekly Breakdown (lower section)
- Week-by-week high count by van type
- Store the highest count per week for each type

## Matching and Comparison (Later)
- Compare branded van count against daily fleet inventory file
- Compare prepayment totals to actual route commitments and AFCS
- Reconciliation adjustments validated against actual fleet usage

## Non-Required Fields
- Bill To
- Ship To
