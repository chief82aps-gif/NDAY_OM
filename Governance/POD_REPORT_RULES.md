# POD Report Rules

## Purpose
Track Photo on Delivery (POD) acceptance rates and identify drivers with bypassed or rejected photos.

## Source File
- POD report (example: US-NDAY-DLV3-Week7-2026NA-DA-POD-Details (1))

## Sections to Store
### 1) Summary (page 1)
- Total POD opportunities
- Accepted count
- Rejected count
- Bypassed count
- Rejection reasons summary (blurry, human in photo, no package detected, in hand, too close, too dark, other)

### 2) Driver Detail (page 2 or section 2)
Per driver:
- Driver name
- Total opportunities
- Accepted count
- Bypassed count
- Rejected count
- Rejected by reason (if present)

## Flags
- Any bypassed photos (should not happen) -> flag driver.
- High reject rate or repeated reason -> coaching priority.

## Time Window
- Weekly (Sunday through Saturday).
