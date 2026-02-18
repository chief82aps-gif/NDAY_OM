# NDAY_OM API Backend

FastAPI backend for NDAY route management system. Ingests Excel and PDF files from multiple Amazon supply chain data sources and provides unified route management with driver assignments and load planning.

## Features

- **4-File Ingest Pipeline**: DOP (routes), Fleet (vehicles), Cortex (drivers), Route Sheets (load manifest)
- **Automatic Header Detection**: Handles Excel files with or without headers
- **Service Type Normalization**: Maps 25+ vendor aliases to 13 canonical service types
- **Route Code Validation**: Enforces 4-5 character uppercase normalization
- **Cross-File Validation**: Validates route codes, service types, and driver assignments across all ingest types
- **RESTful Upload API**: Endpoints for all 4 file types plus real-time status
- **Error Reporting**: Detailed validation errors and warnings for troubleshooting

## Quick Start

### Setup

```bash
cd c:\Users\chief\NDAY_OM
python -m venv .venv
.venv\Scripts\activate
pip install -r api\requirements.txt
```

### Run Backend

```bash
cd c:\Users\chief\NDAY_OM
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

API available at: `http://127.0.0.1:8000`

## File Format Specifications

### 1. DOP (Day of Plan) - Excel

Columns (data-driven, no header required):
- **0**: DSP (company name)
- **1**: Route Code (2-5 chars, e.g., CX105)
- **2**: Service Type (vendor name, normalized)
- **3**: Wave (delivery wave, e.g., DLV3)
- **4**: Staging Location (staging code, e.g., STG.Q12.1)
- **5**: Route Duration (minutes)
- **6-8**: Optional (num_zones, num_packages, num_commercial_pkgs)

**Validation Rules**:
- Route code: 4-5 characters after normalization (uppercase, apostrophes/spaces stripped)
- Service type: Must match canonical form or alias in SERVICE_TYPE_MAP
- All required fields must be non-empty

**Sample File**: `Ingest/DOP_ingest/NDAY DOP 2.17.26.xlsx` (35 routes)

### 2. Fleet (Vehicle Inventory) - Excel

Columns (data-driven, no header required):
- **0**: VIN (vehicle identification number)
- **1**: Vehicle Name (friendly name, e.g., "VEH-NDL-0451")
- **2-10**: Optional metadata
- **11**: Operational Status (must not be "Grounded")

**Validation Rules**:
- Skip rows where operational_status is "Grounded"
- Service type must be recognized (normalized from column 2)

**Sample File**: `Ingest/Fleet_ingest/VehiclesData (21).xlsx` (43/46 vehicles active)

### 3. Cortex (Driver Assignments) - Excel

Columns (data-driven, no header required):
- **0**: Route Code (e.g., CX97)
- **1**: DSP (company name, e.g., "New Day Logistics LLC")
- **2**: Transporter ID (driver identifier)
- **3**: Driver Name (assigned driver)
- **4**: Progress Status (e.g., ON_TIME, DELAYED)
- **5**: Delivery Service Type (vehicle type)

**Validation Rules**:
- Route code must be 4-5 characters
- Service type must match canonical form
- Transporter ID and Driver Name must be non-empty

**Cross-File Validation**:
- Routes in Cortex must exist in DOP
- Service type in Cortex must match DOP service type for same route

**Sample File**: `Ingest/Cortex_ingest/Routes_DLV3_2026-02-17_09_43 (PST).xlsx` (35 assignments)

### 4. Route Sheets (Load Manifest) - PDF

Text-based extraction using regex patterns:

**Route Header Format**:
```
STG.Q12.1
CX105 NDAY • 4WD P31 Delivery Truck
DLV3 • TUE, FEB 17, 2026 • CYCLE_1 • 10:20 AM
9 bags 28 overflow
```

**Bag Table** (left column):
```
Sort Zone | Bag Code | Color Name | Bag ID | Qty
1         | B-7.3B   | Navy       | 4564   | 3
```

**Overflow Table** (right column):
```
Sort Zone | Bag Code | Qty
1         | B-7.2X   | 1
```

**Extraction Patterns**:
- Staging: `(STG\.[A-Z0-9\.]+)`
- Route code: `\b(CX\d{3}|CX\d{2})\b`
- Bags: `([BE]-[\d\.]+[A-Z])\s+([A-Z]+)\s+(\d{4})\s+(\d+)`
- Overflow: `(\d+)\s+([BE]-[\d\.]+[A-Z])\s+(\d+)`

**Sample File**: `Ingest/Route_Sheet_ingest/NDAY_Route_Sheets_Feb_17_2026.pdf` (29 routes)

## API Endpoints

### Upload Endpoints

#### POST `/upload/dop`
Upload DOP Excel file.

**Request**:
```bash
curl -X POST "http://127.0.0.1:8000/upload/dop" -F "file=@NDAY DOP 2.17.26.xlsx"
```

**Response**:
```json
{
  "filename": "NDAY DOP 2.17.26.xlsx",
  "status": "uploaded",
  "records_parsed": 35,
  "errors": []
}
```

#### POST `/upload/fleet`
Upload Fleet Excel file.

**Request**:
```bash
curl -X POST "http://127.0.0.1:8000/upload/fleet" -F "file=@VehiclesData (21).xlsx"
```

#### POST `/upload/cortex`
Upload Cortex Excel file (driver assignments).

**Request**:
```bash
curl -X POST "http://127.0.0.1:8000/upload/cortex" -F "file=@Routes_DLV3_2026-02-17_09_43 (PST).xlsx"
```

#### POST `/upload/route-sheets`
Upload one or more Route Sheet PDFs.

**Request**:
```bash
curl -X POST "http://127.0.0.1:8000/upload/route-sheets" -F "files=@NDAY_Route_Sheets_Feb_17_2026.pdf"
```

#### GET `/upload/status`
Get current ingest status and validation results.

**Request**:
```bash
curl "http://127.0.0.1:8000/upload/status"
```

**Response**:
```json
{
  "dop_uploaded": true,
  "fleet_uploaded": true,
  "cortex_uploaded": true,
  "route_sheets_uploaded": true,
  "dop_record_count": 35,
  "fleet_record_count": 43,
  "cortex_record_count": 35,
  "route_sheets_count": 29,
  "validation_errors": [
    "Row 28: Vehicle name is empty.",
    "Row 32: Vehicle name is empty.",
    "Row 43: Vehicle name is empty."
  ],
  "validation_warnings": [
    "Routes in DOP but not in Route Sheets: CX98, CX149, CX99, CX97, CX148, CX100",
    "Route CX104: Service type 'Standard Parcel - Custom Delivery Van 14ft' not available in Fleet."
  ],
  "last_updated": "2026-02-17T15:30:45.123456"
}
```

## Validation Rules

### Route Code Normalization
- Uppercase all characters
- Remove leading/trailing spaces
- Remove apostrophes
- Valid after normalization: 4-5 characters
- Example: `"cx 105"` → `"CX105"` ✓, `"CX1"` → ✗

### Service Type Mapping
Canonical types (13 total):
1. Standard Parcel - Extra Large Van - US
2. Standard Parcel - Custom Delivery Van 14ft
3. Standard Parcel - Custom Delivery Van 16ft
4. 4WD P31 Delivery Truck
5. Rivian MEDIUM
6. Rivian LARGE
7. Electric Step Van - XL
8. Electric Cargo Van - M
9. Electric Cargo Van - L
... (9 more types)

**Alias examples**:
- "Rivian MEDIUM" → canonical
- "4WD P31" → "4WD P31 Delivery Truck"
- "Extra Large Van" → "Standard Parcel - Extra Large Van - US"

### Cross-File Validation
1. **DOP vs Route Sheets**: All routes in DOP should have corresponding Route Sheets (warning if missing)
2. **DOP vs Fleet**: All service types in DOP should exist in Fleet vehicle inventory (warning if missing)
3. **Cortex vs DOP**: All routes in Cortex should exist in DOP; service types must match (warning if mismatch)

## Troubleshooting

### Common Errors

**Error: "Route code is invalid"**
- Cause: Route code not 4-5 characters after normalization
- Solution: Verify route codes in source file

**Error: "Service type is unrecognized"**
- Cause: Service type not in SERVICE_TYPE_MAP or canonical list
- Solution: Check SERVICE_TYPE_MAP in `api/src/normalization.py`

**Error: "Failed to read Excel file"**
- Cause: File format or structure issue
- Solution: Verify file is valid Excel (.xlsx format)

**Error: "Vehicle name is empty"**
- Cause: Fleet file row missing vehicle name column
- Solution: Check Fleet file row structure

### Validation Warnings

**"Routes in DOP but not in Route Sheets"**
- 6 routes (CX97, CX98, etc.) have no matching Route Sheets PDF
- Action: Ensure all PDFs are uploaded or routes should be removed from DOP

**"Service type not available in Fleet"**
- Route (CX104) requires vehicle type that Fleet doesn't supply
- Action: Either purchase/add vehicle to Fleet or change service type in DOP

## Architecture

### Key Files

- `api/main.py` - FastAPI application, CORS middleware, route definitions
- `api/src/models.py` - Data classes (RouteDOP, Vehicle, CortexRoute, RouteSheet, etc.)
- `api/src/normalization.py` - Service type mapping and route code normalization
- `api/src/ingest_dop.py` - DOP Excel parser
- `api/src/ingest_fleet.py` - Fleet Excel parser
- `api/src/ingest_cortex.py` - Cortex Excel parser (driver assignments)
- `api/src/ingest_route_sheets.py` - Route Sheet PDF parser (regex-based)
- `api/src/orchestrator.py` - Ingest orchestration and cross-file validation
- `api/src/routes/uploads.py` - Upload endpoint handlers

### Data Flow

```
[DOP File] ──┐
[Fleet File]─┼─→ [Parsers] ──→ [Orchestrator] ──→ [Validation] ──→ [Status API]
[Cortex File]┤
[Route PDFs]─┘
```

### Design Patterns

1. **Data-Driven Validation**: Column positions, not header names, drive parsing
2. **Automatic Header Detection**: Gracefully handles files with or without headers
3. **Regex-Based PDF Extraction**: Robust text pattern matching for complex table layouts
4. **Normalization Engine**: Centralized mapping for service types and route codes
5. **Global Orchestrator**: Single instance tracks cross-file validation state

## Testing

### Unit Tests

Run individual parser tests:

```bash
python -m pytest api/test_main.py -v
```

### Integration Tests

```bash
# Test full pipeline with sample files
python -c "
from api.src.orchestrator import IngestOrchestrator
orch = IngestOrchestrator()
orch.ingest_dop('Ingest/DOP_ingest/NDAY DOP 2.17.26.xlsx')
orch.ingest_fleet('Ingest/Fleet_ingest/VehiclesData (21).xlsx')
orch.ingest_cortex('Ingest/Cortex_ingest/Routes_DLV3_2026-02-17_09_43 (PST).xlsx')
orch.ingest_route_sheets(['Ingest/Route_Sheet_ingest/NDAY_Route_Sheets_Feb_17_2026.pdf'])
orch.validate_cross_file_consistency()
print(f'DOP: {len(orch.status.dop_records)}, Fleet: {len(orch.status.fleet_records)}, Cortex: {len(orch.status.cortex_records)}, Sheets: {len(orch.status.route_sheets)}')
"
```

## Future Development

- [ ] Vehicle assignment engine (match routes to fleet by service type)
- [ ] Driver handout PDF generation (2×2 card layout)
- [ ] Frontend UI (Next.js with upload dropzones)
- [ ] Persistent audit trail / data retention
- [ ] Production deployment (AWS Lambda/EC2)

## Dependencies

- fastapi (≥0.95.0) - Web framework
- uvicorn (≥0.21.0) - ASGI server
- pandas (≥1.5.0) - Excel parsing
- openpyxl (≥3.9.0) - Excel format support
- pdfplumber (≥0.7.0) - PDF text extraction
- python-multipart (≥0.0.5) - Form file handling

See `requirements.txt` for full list.

## License

Copyright © 2026 New Day Logistics. All rights reserved.
