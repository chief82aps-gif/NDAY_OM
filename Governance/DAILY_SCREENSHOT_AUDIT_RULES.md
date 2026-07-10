# Daily Screenshot Audit Rules (Cortex vs WST)

> Discovery: Browse all governance docs in [Governance Index](README.md).

## Purpose
Define the daily, screenshot-based audit process used to validate operational consistency between Cortex and WST before downstream weekly invoice review.

## Scope
- Data source 1: Cortex daily operations page (map/list view)
- Data source 2: WST daily work summary page
- Frequency: Daily (prior day review)
- Primary focus: Route count, delivered packages, and critical exception signals

## Required Inputs
1. Cortex screenshot for the same service day
2. WST screenshot for the same service day
3. Both screenshots must show top summary totals and route/line sections

## Process Flow
1. Open prior day in Cortex.
2. Open prior day in WST.
3. In Daily Screenshot Audit page, capture Cortex screenshot.
4. Capture WST screenshot.
5. System OCR extracts totals and line patterns.
6. System compares values and returns PASS, PARTIAL, or FAIL.

Note: Browser security requires user-approved tab capture. Capture cannot be fully silent/forced.

## Core Comparison Rules

### Rule 1: Total Routes Match
- Compare Cortex total routes to WST completed routes.
- Expected: exact match.
- Result:
  - PASS if equal
  - FAIL if different

### Rule 2: Delivered Packages Match
- Compare Cortex delivered packages to WST delivered packages.
- Expected: exact match.
- Result:
  - PASS if equal
  - FAIL if different

### Rule 3: Line Consistency Check
- Sum of visible line-level route counts should match the page-level route total.
- Sum of visible line-level delivered packages should match the page-level delivered package total.
- If mismatch exists, mark as discrepancy for manager review (possible hidden/collapsed row or OCR miss).

### Rule 4: DSP Late Cancel Alarm (Never Above 0)
- Monitor DSP Late Cancel values parsed from screenshots.
- Policy: Any detected value above 0 is an immediate alarm.
- Result:
  - FAIL if max DSP Late Cancel > 0 on either screenshot
  - PASS only when all detected DSP Late Cancel values are 0

### Rule 5: Training Handling
- Training rows may appear in WST views.
- Training must not be used to inflate Cortex-to-WST operational route parity checks.
- If Training impacts top-level WST route totals, flag as review-required condition.

## Audit Status Logic
- PASS: Routes match, delivered packages match, DSP Late Cancel max = 0
- PARTIAL: One of routes/packages matches, DSP Late Cancel max = 0
- FAIL: Routes mismatch, packages mismatch, or DSP Late Cancel max > 0

## Variance Response and Dispute Handling
When a variance (mismatch) is detected between Cortex and WST for any of the following metrics, the auditor **must** provide a response:
- Completed Routes variance
- Delivered Packages variance

### Variance Response Requirements
For each detected variance, the auditor must:
1. **Enter a Justification**: Provide a written explanation for the variance (e.g., "Data entry error in WST", "Route cancellation after Cortex capture", "Incomplete route in system")
2. **Select a Dispute Status**: Classify the variance as either:
   - **Acknowledged**: Variance is explained and accepted as operational variance
   - **Dispute Submitted**: Variance requires escalation; a formal dispute has been submitted to the relevant party

### Variance Validation
- The audit **cannot be submitted** until all detected variances receive both:
  - A non-empty justification
  - A selected dispute status
- System will return error message: "Please review and respond to all variances before submitting."

### Required Operator Response
- On FAIL due to DSP Late Cancel > 0: escalate immediately to operations manager.
- On route/package variance: enter justification and select appropriate dispute status before submission.
- On persistent mismatch after recapture: classify as dispute if escalation is required, or acknowledged if variance is expected/explained.

## Recordkeeping
Each daily audit should store:
- Audit date
- Cortex screenshot timestamp
- WST screenshot timestamp
- Extracted totals
- Deltas
- Variance justifications and dispute status (if applicable)
- DSP Late Cancel max value
- Final status
- Reviewer name and notes

## Governance Notes
- This screenshot audit is the daily operational gate.
- Weekly invoice dispute preparation occurs after daily screenshot parity checks are complete.
