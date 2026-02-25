# Variable Invoice Rules

## Purpose
Store weekly Amazon variable invoice summary lines for later comparison against Work Summary uploads.

## Source File
- Variable invoice PDF (example: 9d4ac7ad-fe73-4f6e-996b-9920140e4ef5_3.pdf)

## Reference Key
- `invoice_number` (e.g., INV-FJ6T0C-0000000786)

## Stored Fields (Summary)
From the Summary section in the PDF:
- `description`
- `rate`
- `quantity`
- `amount`
- `instance_count` (number of summary lines combined for the same description+rate)

## Aggregation Rule
Summary lines with the same `description` and `rate` are aggregated:
- `quantity` = sum of quantities
- `amount` = sum of amounts
- `instance_count` = count of merged lines

## Validation (Later)
- Flag if `amount` does not match `rate * quantity`.
- Compare quantities to Work Summary weekly uploads for payment verification.

## Non-Required Fields
The following fields are not required for this ingest:
- Bill To
- Ship To
