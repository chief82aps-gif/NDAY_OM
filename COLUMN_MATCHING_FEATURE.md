# Dynamic Column Matching Feature

## Overview
The ingestion system now automatically detects column positions by their semantic meaning rather than fixed column indices. This means if your data normally uses:
- **Column A** for Route Code
- **Column B** for Service Type  
- **Column C** for Wave Time

But in a new file they're rearranged to:
- **Column A** for Service Type
- **Column B** for Route Code
- **Column C** for Wave Time (moved to D)

The tool will **still find the right columns and ingest the data correctly** into the database.

## How It Works

### 1. **Header Detection**
When a file is ingested, the parser scans the first few rows looking for semantic header keywords:
- For DOP files: "dsp", "route", "service", "wave", "staging", "duration"
- For Fleet files: "vin", "service", "vehicle", "operational status"
- For Cortex files: "route code", "transporter", "driver", "service"

### 2. **Alias Matching**
Once a header row is detected, each column is matched against a list of semantic aliases. For example:
- A column labeled "Delivery Service Type" matches the "service_type" field via the alias "delivery service type"
- A column labeled "Wave Time" matches the "wave" field via the alias "wave time"

### 3. **Dynamic Column Mapping**
The parser builds a map of field names to actual column indices:
```
{
    "dsp": 0,           # Found in column 0
    "route_code": 1,    # Found in column 1
    "service_type": 3,  # Found in column 3 (shifted!)
    "wave": 2,          # Found in column 2
    ...
}
```

### 4. **Fallback to Defaults**
If no header is detected (file has no header row), the parser falls back to the original positional mapping:
```
DOP: 0=DSP, 1=Route Code, 2=Service Type, 3=Wave, 4=Staging, 5=Duration
Fleet: 0=VIN, 1=Service, 2=Vehicle, 11=Operational Status
Cortex: 0=Route Code, 1=DSP, 2=Transporter ID, 3=Driver, 4=Progress, 5=Service
```

## Code Structure

### New Module: `api/src/column_mapping.py`
- `detect_header_row()` – Finds the header row by analyzing cell content
- `build_column_map()` – Maps semantic fields to actual column indices
- `_normalize_text()` – Cleans headers for loose text matching
- `_cell_matches_alias()` – Tests if a cell matches a semantic alias

### Updated Parsers
Each ingest parser now uses semantic field definitions:

**DOP Parser** (`api/src/ingest_dop.py`):
```python
DOP_COLUMN_ALIASES = {
    "dsp": ["dsp", "company"],
    "route_code": ["route", "route code", "routecode"],
    "service_type": ["service", "service type", "delivery service type"],
    ...
}
```

**Fleet Parser** (`api/src/ingest_fleet.py`):
```python
FLEET_COLUMN_ALIASES = {
    "vin": ["vin", "vin number", "vehicle vin"],
    "service_type": ["service", "service type"],
    "vehicle_name": ["vehicle", "vehicle name", "truck", "unit"],
    ...
}
```

**Cortex Parser** (`api/src/ingest_cortex.py`):
```python
CORTEX_COLUMN_ALIASES = {
    "route_code": ["route", "route code", "routecode"],
    "dsp": ["dsp", "company"],
    "driver_name": ["driver", "driver name", "associate"],
    ...
}
```

## Testing

All existing ingest tests pass with the new column mapping enabled:
```
DOP records: 35 ✓
Fleet records: 57 ✓
Cortex records: 35 ✓
Route sheets: 35 ✓
Assignments: 35/35 successful ✓
PDF generation: Success ✓
```

## Example Scenarios

### Scenario 1: Column Shift in DOP File
**Original file:**
| DSP | Route Code | Service Type | Wave | ... |
| ABC | RX001 | NEXT_D | 08:00 | ... |

**New file with Service Type moved:**
| Route Code | Service Type | DSP | Wave | ... |
| RX001 | NEXT_D | ABC | 08:00 | ... |

**Result:** Tool auto-detects headers and remaps columns ✓

### Scenario 2: Header Variations
**File version 1:** "Service Type"  
**File version 2:** "Delivery Service Type"  
**File version 3:** "service type" (lowercase)

**Result:** All variations match the same field via alias matching ✓

### Scenario 3: No Header Row
**File has no header, just data:**
| ABC | RX001 | NEXT_D | 08:00 | ... |

**Result:** Falls back to original positional mapping ✓

## Benefits

1. **Robustness** – Files with shifted or renamed columns still ingest correctly
2. **Flexibility** – Handles minor variations in column naming
3. **Backward Compatible** – Files without headers use fallback defaults
4. **No Manual Intervention** – Automatic detection, no config changes needed
5. **Audit Trail** – Column mapping is transparent and debuggable

## Future Enhancements

- Add confidence scoring for header detection
- Support partial header rows (some columns labeled, others not)
- Enable user-defined alias patterns for custom variations
- Log detected column mappings for debugging
