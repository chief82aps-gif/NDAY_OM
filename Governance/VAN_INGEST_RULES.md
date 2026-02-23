# Fleet Vehicle Ingest Rules & Van Assignment Governance

## 1. Fleet File Requirements

### 1.1 File Format
- **Accepted Formats**: `.xlsx` (Excel) or `.csv` (CSV)
- **Required Columns** (must be detectable in first 25 rows):
  - **VIN**: Vehicle Identification Number (aliases: "vin", "vin number", "vehicle vin")
  - **Vehicle Name**: Display name/unit number (aliases: "vehicle", "vehicle name", "truck", "unit", "truck id")
  - **Service Type**: Vehicle service classification (aliases: "service", "service type", "delivery service type")
  - **Operational Status**: Vehicle availability status (aliases: "operational status", "operationalstatus", "operational_status", "status", "availability")

### 1.2 File Structure
- No header row requirement; column detection is automatic via header mapping
- Data rows must follow column header detection (header search limited to first 25 rows)
- Minimum file size: 4 columns, at least 1 data row

---

## 2. Vehicle Eligibility Rules

### 2.1 Operational Status Filter
**RULE**: Skip any vehicle row containing the word "GROUNDED" anywhere in the row

- ✅ **INCLUDED**: All vehicles with operational_status values other than "GROUNDED" (e.g., "OPERATIONAL", "PORT AVAILABLE", "IN SERVICE", etc.)
- ❌ **EXCLUDED**: 
  - Any row where operational_status field contains "GROUNDED"
  - Any row where ANY cell contains "GROUNDED" (scope: entire row)
  
**Implementation**: 
   - Row-level scan: concatenate all non-null cells to uppercase, check for "GROUNDED" substring
   - If found, skip row silently (not recorded as error, just ineligible)

### 2.2 Service Type Validation
**RULE**: Service type must be recognized and normalized

- ✅ **INCLUDED**: Service types that normalize to a known fleet category (see Service Type Mapping below)
- ❌ **EXCLUDED**: 
  - Empty or null service_type fields → error: "Service type is empty"
  - Unrecognized service types that don't normalize → error: "Service type '{value}' is unrecognized"

### 2.3 Required Fields Validation
**RULE**: Each vehicle record must have all required fields populated

- ✅ **INCLUDED**: Rows with non-empty VIN, Vehicle Name, Service Type, Operational Status
- ❌ **EXCLUDED**:
  - Empty VIN → error: "VIN is empty"
  - Empty Vehicle Name → error: "Vehicle name is empty"
  - Empty Service Type → error: "Service type is empty"
  - Missing Operational Status → defaults to "OPERATIONAL" (no error)

---

## 3. Service Type Mapping & Fallback Chains

### 3.1 Canonical Service Types
Fleet vehicles must normalize to one of these canonical service types:

| Canonical Name | Primary Use | Supports Fallback |
|---|---|---|
| `Standard Parcel - Custom Delivery Van 14ft` (CDV14) | Standard parcel delivery | Yes → CDV16, Extra Large Van |
| `Standard Parcel - Custom Delivery Van 16ft` (CDV16) | Large parcel delivery | Yes → Extra Large Van |
| `Standard Parcel - Extra Large Van - US` (XL) | Extra large/oversized | Yes → CDV16 |
| `Standard Parcel Electric - Rivian MEDIUM` | Electric medium delivery | No fallback (electric-only) |
| `Standard Parcel Electric - Rivian LARGE` | Electric large delivery | No fallback (electric-only) |
| `Electric Step Van - XL` | Electric extra large | No fallback (electric-only) |
| `Electric Cargo Van - M` | Electric medium cargo | No fallback (electric-only) |
| `Electric Cargo Van - L` | Electric large cargo | No fallback (electric-only) |
| `4WD P31 Delivery Truck` | 4WD truck delivery | Yes → AmFlex Large Vehicle |
| `AmFlex Large Vehicle` | AmFlex large delivery | No fallback |

### 3.2 Service Type Aliases
Recognized service type inputs that map to canonical names:

- `Standard Parcel - Custom Delivery Van 14ft`, `CDV14`, `Custom Delivery Van 14`, `delivery van 14`
- `Standard Parcel - Custom Delivery Van 16ft`, `CDV16`, `Custom Delivery Van 16`, `delivery van 16`
- `Standard Parcel - Extra Large Van - US`, `Extra Large Van`, `XL Van`, `Extra Large`
- `Rivian MEDIUM`, `Electric - Rivian MEDIUM`, `Standard Parcel Electric - Rivian MEDIUM`
- `Rivian LARGE`, `Electric - Rivian LARGE`, `Standard Parcel Electric - Rivian LARGE`
- `Electric Step Van - XL`, `Electric Step Van`
- `Electric Cargo Van - M`, `Electric Cargo Van Medium`
- `Electric Cargo Van - L`, `Electric Cargo Van Large`
- `4WD P31 Delivery Truck`, `4WD Truck`, `P31`
- `AmFlex Large Vehicle`, `AmFlex`

---

## 4. Vehicle Assignment Rules

### 4.1 Auto-Assignment by Service Type (Primary Fallback Chain)

When routes are auto-assigned to vehicles, they automatically match vehicles based on route service type. The assignment engine follows this fallback hierarchy:

```
Route Service Type → Fallback Chain (priority order)
CDV14             → [CDV14, CDV16, Extra Large Van, DEFAULT]
CDV16             → [CDV16, Extra Large Van, DEFAULT]
Extra Large Van   → [Extra Large Van, CDV16, DEFAULT]
Electric - Rivian MEDIUM → [Electric - Rivian MEDIUM] (NO FALLBACK)
Electric - Rivian LARGE  → [Electric - Rivian LARGE] (NO FALLBACK)
4WD P31           → [AmFlex] (SPECIFIC FALLBACK)
AmFlex            → [AmFlex] (NO FALLBACK)
```

**Rationale**:
- Standard gas vehicles (CDV14/16/XL) share elastic fallback chains
- Electric routes MUST use electric vehicles (no gas fallback allowed)
- 4WD routes map to AmFlex when no 4WD available
- Electric van constraint enforced at assignment time

### 4.2 Electric Van Constraint (CRITICAL)
**RULE**: Electric vans can ONLY be assigned to electric routes (unless explicitly user-authorized)

- ✅ **ALLOWED**: Electric van (Rivian, Electric Step Van, Electric Cargo Van) → Electric route
- ✅ **ALLOWED WITH AUTHORIZATION**: Electric van → Non-electric route (only if user approves violation warning)
- ❌ **BLOCKED**: Electric van → Non-electric route (generates violation warning)

**Implementation**:
   - Assignment engine checks `is_van_electric()` and `is_route_electric()`
   - If van is electric AND route is NOT electric AND not user-authorized → record violation, skip van
   - Violations reported in UI for user approval before final assignment

### 4.3 Driver-Van Affinity (Secondary Assignment Strategy)
**RULE**: If driver name available, prefer previously-assigned vehicle from last 7 days

- ✅ **PRIORITY**: Assign driver's preferred vehicle (from affinity tracker) if available and eligible
- Affinity takes precedence over normal fallback chain
- Falls back to normal chain if affinity vehicle unavailable

### 4.4 Van Capacity Limits (Tertiary Constraint)
**RULE**: Routes cannot exceed van capacity limits (bags, cubic footage)

- ✅ **INCLUDED**: Assignment only if route packages ≤ van max_bags AND cubic footage ≤ van max_cubic_feet
- ⚠️ **CAPACITY WARNING**: If van exceeds 85% capacity after assignment, flag warning (non-blocking)
- ❌ **CAPACITY VIOLATION**: If van exceeds 100% capacity, flag violation (may block assignment on user review)

---

## 5. Manual Vehicle Assignment (User Fallback)

When auto-assignment fails (0 eligible vehicles for a route), user manual assignment dialog shows:

- **Available Vehicles**: All non-grounded vehicles from fleet FILE (ignores service type rules)
- **Already-Selected Filter**: Hides vehicles already assigned to other routes in this batch
- **User Choice**: User can manually assign ANY available vehicle to the failed route

**Rules for Manual Assignment**:
- ✅ User CAN override service type mismatches (CDV14 route → use Extra Large Van)
- ⚠️ User warned if assigning electric van to non-electric route
- ✅ User CAN assign electric van to non-electric route if they dismiss warning (authorization recorded)

---

## 6. Error Handling & Validation Reporting

### 6.1 Error Categories
| Category | HTTP Code | Action |
|---|---|---|
| **File Format** | 400 | Invalid file type (not .xlsx/.csv) - reject before parsing |
| **File Structure** | 400 | Missing columns, insufficient rows |
| **Column Detection** | 400 | Cannot detect required columns in first 25 rows |
| **Data Validation** | 400 | Missing/invalid fields (VIN, Vehicle Name, Service Type) |
| **Service Type** | 400 | Unrecognized service type (not in canonical mapping) |
| **Parsing Exception** | 500 | Unexpected error during row parsing |

### 6.2 Error Reporting
- Errors returned with fleet upload response (last 5 errors)
- Error includes row number and specific failure reason
- Non-blocking skips (e.g., GROUNDED rows) not recorded as errors

---

## 7. Data Flow Example

**Input Fleet File (fleet.xlsx)**:
```
Row 1 (Header):   VIN        | Service Type          | Vehicle Name | Op Status
Row 2 (Data):     ABC123     | CDV16                 | Van-5001     | OPERATIONAL
Row 3 (Data):     DEF456     | Standard Parcel - CDV16 | Van-5002    | OPERATIONAL
Row 4 (Data):     GHI789     | Electric - Rivian Med | EV-2001      | GROUNDED      ← SKIP (GROUNDED)
Row 5 (Data):     JKL012     | (empty)               | Van-5003     | OPERATIONAL   ← ERROR (no service type)
Row 6 (Data):     MNO345     | CDV14                 | Van-5004     | PORT AVAILABLE
```

**Expected Output**:
```
Records Parsed: 3
  - ABC123 (CDV16, Van-5001, OPERATIONAL)
  - DEF456 (CDV16, Van-5002, OPERATIONAL)
  - MNO345 (CDV14, Van-5004, PORT AVAILABLE)

Errors:
  - Row 4: Skipped (contains GROUNDED)
  - Row 5: Service type is empty
```

---

## 8. Version Control & Updates

**This document governs**:
- Fleet file parsing rules (ingest_fleet.py)
- Vehicle eligibility filtering (operational status, service type)
- Auto-assignment fallback chains (assignment.py FALLBACK_CHAIN)
- Electric van constraints (assignment.py electric constraint logic)
- Manual assignment rules (orchestrator.py _get_available_vehicles_for_route)

**Any changes to these rules must**:
1. Update this document
2. Update relevant code comments
3. Include version/date stamp in commit message
4. Test all affected test cases

---

## 9. Quick Reference: Rules Checklist

- [ ] Fleet file is .xlsx or .csv
- [ ] Fleet file has ≥4 columns and ≥1 data row
- [ ] Column headers detected in first 25 rows (VIN, Vehicle Name, Service Type, Operational Status)
- [ ] Skip rows containing "GROUNDED" anywhere
- [ ] VIN, Vehicle Name, Service Type are non-empty
- [ ] Service Type normalizes to canonical name
- [ ] Operational Status defaults to "OPERATIONAL" if missing
- [ ] Auto-assignment respects fallback chains per service type
- [ ] Electric van constraint enforced (no electric van on non-electric route without authorization)
- [ ] Manual assignment shows all non-grounded vehicles, filters already-selected
- [ ] Errors reported with row numbers and specific failure reasons
