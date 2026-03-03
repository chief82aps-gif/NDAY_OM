# Cortex & WST Validation Guide

## Overview

This document specifies the exact formats and extraction rules for **Cortex** and **WST** (Workforce System Timeline) daily operation metrics. It is the authoritative reference for parsing, validation, and dispute resolution in the Daily Screenshot Audit system.

---

## Quick Reference

| Metric | Cortex | WST | Validation |
|--------|--------|-----|-----------|
| **Routes** | Sum of X in `X/Y deliveries` lines (39 cards) | Number before "completed routes" (39) | Must match exactly (0 tolerance) |
| **Packages** | Raw sum MINUS package status PLUS customer returns | Number before "Delivered Packages" | ≤ 25 package difference allowed |

---

## Cortex Data Extraction

### 1. Routes Count

**Location**: Main routes header summary
**Format**: Individual route cards (CX114, CX115, ... CX171)
**Count**: 39 total routes

```
Routes
39 Total  ← This is your route count
```

**Extraction**: Count or find the "Total" label next to the routes section.

**Value**: `cortex_routes = 39`

---

### 2. Packages Delivered (Step-by-Step)

#### Step A: Extract Raw Total from All Route Cards

Each route card ends with a deliveries fraction line:

```
CX114
[route details...]
268/268 deliveries  ← Extract 268
---

CX115
[route details...]
257/257 deliveries  ← Extract 257
---

CX154
[route details...]
193/193 deliveries  ← Extract 193
---

[39 routes total]
```

**Algorithm**:
1. Find all lines matching: `(\d+)/(\d+) deliveries`
2. Extract the **first number (numerator)** from each match
3. Sum all numerators

**Numerators from Feb 28, 2026 dataset**:
```
268 + 257 + 194 + 242 + 245 + 148 + 225 + 126 + 104 + 95 + 115 
+ 236 + 285 + 323 + 269 + 268 + 260 + 310 + 325 + 367 + 317 
+ 349 + 352 + 357 + 326 + 274 + 311 + 321 + 332 + 260 + 236 
+ 193 + 182 + 181 + 142 + 138 + 191 + 185 + 176 = 9,485
```

**Raw Total**: `9,485 packages`

---

#### Step B: Adjust for Package Status (SUBTRACT)

Cortex displays a "Package status" section showing undelivered/unreceived items:

```
Package status
4 Remaining
0 Reattemptable
22 Undeliverable
0 Missing
43 Returned to station
34 Pickup failed
0 Pending containers pickup
0 Pending packages pickup
```

**Rule**: These items were NOT delivered, so subtract them from the raw total.

**Items to subtract**:
- Remaining
- Undeliverable
- Returned to station
- Pickup failed

**Calculation**:
```
Subtraction = Remaining + Undeliverable + Returned + Pickup_failed
Subtraction = 4 + 22 + 43 + 34 = 103

Adjusted Total = 9,485 - 103 = 9,382
```

---

#### Step C: Adjust for Customer Returns (ADD)

Cortex also tracks customer returns in a separate section:

```
Customer returns
1 Total
0 Remaining
1 Complete  ← Add this
```

**Rule**: Customer return "completes" count as delivered, so add to total.

**Calculation**:
```
Addition = Customer_returns_complete
Addition = 1

Final Cortex Packages = 9,382 + 1 = 9,383
```

**Value**: `cortex_packages = 9,383` ✓

---

## WST Data Extraction

### 1. Routes Count

**Location**: Header metrics section
**Format**: Number precedes "completed routes" label (case-insensitive)

**IMPORTANT DISTINCTION**:
- **Total Routes** = Number preceding the **FIRST** instance of "completed routes"
- **Validation Check** = Sum of all **subsequent** instances should match the first number

```
39
completed routes  ← FIRST instance: Extract 39 (this is your total)

1
completed routes

2
completed routes

[etc... sum these subsequent: 1 + 2 + ... should = 39]
```

**Extraction Algorithm**:
1. Iterate through all lines
2. On first "completed routes" found: extract preceding number = `total_routes`
3. On each subsequent "completed routes" found: sum their preceding numbers = `validation_sum`
4. Verify: `validation_sum` should equal `total_routes`

**Value**: `wst_routes = 39` ✓
**Validation**: Sum of remaining completed routes = 39 ✓

---

### 2. Delivered Packages

**Location**: Header metrics section
**Format**: Number precedes "Delivered Packages" label (case-insensitive, note the "Delivered" qualifier!)

**CRITICAL DISTINCTION**:
- Include: `X Delivered Packages` ← USE THIS (extract from **FIRST** instance only)
- Exclude: `X Pickup Packages` ← DO NOT USE
- Exclude: Subsequent occurrences of "Delivered Packages"

WST also displays:
```
1
Pickup Packages  ← EXPLICITLY EXCLUDE - not customer-facing deliveries
```

**Extraction Algorithm**:
1. Iterate through all lines
2. On **FIRST** "Delivered Packages" found (case-insensitive): extract preceding number
3. Stop searching after first match
4. Remove commas if present (9,385 → 9385)
5. Convert to integer

**Example**:
```
9,385
Delivered Packages  ← FIRST instance: Extract 9,385

[other metrics...]

50
Delivered Packages  ← SKIP: don't extract from subsequent instances
```

**Value**: `wst_packages = 9,385` ✓

---

## Validation Algorithm

### Input Values
```
cortex_routes = 39
cortex_packages = 9,383
wst_routes = 39
wst_packages = 9,385
```

### Validation Step 1: Routes

**Rule**: Routes must match exactly (no tolerance)

```
IF cortex_routes == wst_routes
  RESULT: ✅ PASS
ELSE
  RESULT: ❌ DISPUTE - routes mismatch
```

**Check**: 39 == 39 ✅ **PASS**

---

### Validation Step 2: Packages

**Rule**: Absolute difference between cortex and wst must be ≤ 25 packages

```
difference = ABS(cortex_packages - wst_packages)

IF difference <= 25
  RESULT: ✅ PASS
ELSE
  RESULT: ❌ DISPUTE - packages exceed tolerance
```

**Check**:
```
difference = ABS(9,383 - 9,385) = ABS(-2) = 2
2 <= 25 ✅ PASS
```

---

## Validation Rules Summary

| Metric | Must Match? | Tolerance | Reason |
|--------|-------------|-----------|--------|
| **Routes** | Exactly (0 difference) | None | Routes represent DA assignments - must be identical |
| **Packages** | Within range | ±25 packages (absolute) | Timing differences, in-transit deliveries, OCR variance |

---

## Why These Rules?

### Routes: Zero Tolerance
- Routes represent **specific driver assignments** between Cortex (assignment system) and WST (execution system)
- These should always match exactly
- Any difference indicates a structural problem requiring investigation
- Example: If Cortex shows 39 routes but WST shows 38, a route assignment failed in Cortex

### Packages: 25-Package Tolerance
Absolute differences occur due to:
- **In-transit deliveries**: Picked up in Cortex but not yet recorded as delivered in WST
- **Timing**: Screenshots taken at different times during same-day operations
- **System sync delays**: Brief latency (~5-10 min) between Cortex and WST updates
- **OCR accuracy**: Minor extraction variance on large numbers

25 packages represents ~0.25% variance of typical ~10,000 daily package volume - tight tolerance that ensures accuracy while accommodating minor operational delays.

---

## Detailed Example: Feb 28, 2026

### Cortex Processing

**Routes:**
```
Routes section header shows: 39 routes
```

**Raw package total** (sum of all X from `X/Y deliveries`):
```
CX114: 268
CX115: 257
CX116: 194
... [33 more routes]
CX171: 176
─────────────
TOTAL: 9,485
```

**Package Status adjustment** (subtract):
```
Remaining:        4
Undeliverable:   22
Returned:        43
Pickup Failed:   34
─────────────
Total subtract:  103

9,485 - 103 = 9,382
```

**Customer Returns adjustment** (add):
```
Complete returns: 1

9,382 + 1 = 9,383 ✓
```

**Cortex Final**: 39 routes, 9,383 packages

---

### WST Processing

**Routes:**
```
39
completed routes
→ Extract: 39 ✓
```

**Delivered Packages:**
```
9,385
Delivered Packages
→ Extract: 9,385 ✓
(Not "1 Pickup Packages")
```

**WST Final**: 39 routes, 9,385 packages

---

### Validation

```
Routes:   39 == 39        ✅ PASS (exact match)
Packages: |9,383 - 9,385| = 2 ≤ 25  ✅ PASS (within tolerance)

OVERALL RESULT: ✅ ALL METRICS PASS
```

---

## Edge Cases & Troubleshooting

### Missing Package Status Section
**Scenario**: Package status header not found in Cortex

**Solution**: 
- Use raw route total (9,485) as cortex_packages value
- Log warning: "Package status section not found - using unadjusted total"
- Flag for manual review in dispute step

### Partial Route Cards (X < Y)
**Scenario**: Route shows `185/190 deliveries` (5 incomplete deliveries)

**Solution**:
- Still extract 185 as delivered
- The undelivered count (5) is implicitly captured in "Package status" totals
- No special handling needed

### Zero or Missing Customer Returns
**Scenario**: No customer returns section found, or shows zeros

**Solution**:
- Add 0 (no change to total)
- This is normal and expected most days

### Commas in Numbers
**Scenario**: WST shows "9,385" with comma separator

**Solution**:
- Remove comma before conversion: "9,385" → "9385"
- Convert to integer

---

## Implementation Checklist

**CORTEX Parsing:**
- [ ] Extract routes: Find \"Total\" label in routes section (should = 39)
- [ ] Iterate all route cards, extract numerators from `X/Y deliveries` lines
- [ ] Sum all extracted numerators (should ≈ 9,485)
- [ ] Extract Package Status section: Remaining, Undeliverable, Returned, Pickup_failed values
- [ ] Subtract package status sum from raw total (should ≈ 9,382)
- [ ] Extract Customer Returns \"Complete\" count
- [ ] Add customer returns to adjusted total (should = 9,383)

**WST Parsing:**
- [ ] Extract FIRST \"completed routes\" number = total routes (should = 39)
- [ ] Extract sum of ALL subsequent \"completed routes\" numbers for validation (should also = 39)
- [ ] Extract FIRST \"Delivered Packages\" number (should = 9,385)
- [ ] Explicitly skip: \"Pickup Packages\" (do not extract)

**Validation:**
- [ ] Validate routes match exactly (0 tolerance): cortex_routes == wst_routes
- [ ] Validate packages difference ≤ 25 (absolute): |cortex_packages - wst_packages| ≤ 25
- [ ] Log all extracted values and calculation steps
- [ ] Log validation checks and results
- [ ] Record any disputes with specific reason and timestamp

---

## References

- Cortex system: Daily operational dashboard for route assignments and delivery tracking
- WST system: Work execution tracking for driver hour and delivery records
- DLV3-Reno station data: Sample dataset from Feb 28, 2026
- Implementation: Frontend [daily-screenshot-audit-simple.tsx](frontend/pages/daily-screenshot-audit-simple.tsx)

