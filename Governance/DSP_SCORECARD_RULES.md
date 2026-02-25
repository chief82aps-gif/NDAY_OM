# DSP Scorecard Rules

## Purpose
Store weekly DSP scorecard metrics, track trends, and identify coaching priorities.

## Source File
- DSP scorecard PDF (example: US_NDAY_DLV3_Week7_2026_en_DSPScorecardPreview.pdf)

## Sections to Store
### 1) Summary (page 1)
- Overall rating (Fantastic Plus, Fantastic, Great, Fair, Poor)
- Category ratings:
  - Safety and Compliance
  - Delivery Quality
  - Pickup Quality (if applicable)
  - Team and Fleet
- Each category rating maxes at Fantastic (no Fantastic Plus)

### 2) Driver Summary (page 2)
Per driver (use transporter ID as primary key):
- Driver name
- Transporter ID
- Packages delivered
- Per-100-trip safety metrics:
  - Seatbelt-off rate
  - Speeding event rate
  - Distraction rate
  - Following distance
  - Stop sign / signal violations
- Delivery Quality metrics:
  - CDF DPMO
  - CED (Customer Escalation Defect)
  - DCR (Delivery Completion Rate)
  - DSB DPMO
  - POD acceptance rate
  - POD opportunities
  - DSB count
- Pickup Quality metrics (store if present)

### 3) Rules / Appendix A (definitions and weights)
Store the definitions and weights to drive future prioritization and coaching logic.

## Key Notes
- Per-100-trip metrics scale by total trips.
- CED is highly sensitive; a single event can drop weekly rating to Poor.
- DCR target for Fantastic is 99.6% or higher.
- DSB measures DA-controllable delivery errors.
- POD acceptance rate is percent of valid delivery photos.

## Time Window
- Weekly (Sunday through Saturday).
