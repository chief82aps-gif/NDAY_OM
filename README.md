# NDAY_OM Project

**Last updated: 2026-02-20**

DSP Route Manager backend for ingesting, validating, and generating driver handout PDFs. This system automates the process of merging operational data from multiple Amazon-provided sources (DOP, Fleet, Cortex, Route Sheets) and produces ready-to-print driver handouts.

## Architecture
- **Backend:** FastAPI (Python 3.14)
- **Data Processing:** Pandas, pdfplumber, openpyxl
- **Data Storage:** In-memory ingest status + temp file uploads

## Features
- **DOP Ingest:** Parse Excel "Day of Plan" files (columns: DSP, Route Code, Service Type, Wave, Staging Location, Route Duration, Num Zones, Num Packages, Num Commercial Pkgs)
- **Fleet Ingest:** Parse Excel Fleet inventory files (VIN, Service Type, Vehicle Name, Operational Status)
- **Cortex Ingest:** Placeholder for Cortex route assignments (parsing to be implemented)
- **Route Sheets Ingest:** Parse multiple PDF route loadouts (extracts bags, overflow zones, package counts)
- **Normalization:** Service-type canonicalization, route code normalization (uppercase, strip spaces, validate 4-5 chars)
- **Validation:** Cross-file consistency checks, operational status filtering (skip "Grounded"), service type matching
- **Status Endpoint:** Real-time ingest status and validation errors/warnings

## Planning & Roadmap
See [UPGRADE_BACKLOG.md](UPGRADE_BACKLOG.md) for the canonical list of features, improvements, and integration opportunities. ✓ Single source of truth for product direction.

## API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload/dop` | Upload DOP Excel file |
| POST | `/upload/fleet` | Upload Fleet Excel file |
| POST | `/upload/cortex` | Upload Cortex Excel file |
| POST | `/upload/route-sheets` | Upload one or more Route Sheet PDFs |
| GET | `/upload/status` | Get current ingest status and validation results |

## Project Structure
```
NDAY_OM/
├── api/
│   ├── main.py                          # FastAPI app entrypoint
│   ├── requirements.txt                 # Dependencies
│   └── src/
│       ├── models.py                    # Data classes
│       ├── normalization.py             # Service type mapping & normalization
│       ├── ingest_dop.py                # DOP parser
│       ├── ingest_fleet.py              # Fleet parser
│       ├── ingest_route_sheets.py       # Route Sheet PDF parser
│       ├── orchestrator.py              # Ingest orchestration & validation
│       └── routes/
│           └── uploads.py               # Upload endpoints
├── governance/                          # Business rules & governance docs
│   ├── DSP_Route_Manager_Software_Manual.md
│   ├── Route Sheet Definition.pptx
│   ├── Driver Handout Sample page with Data tables description.pptx
│   ├── Website Branding and appearance guide.pptx
│   ├── Ingest Matrix.xlsx
│   └── Load Out Notes Format sample.pptx
├── Ingest/                              # Ingest sample files (user-uploaded)
│   ├── Cortex/
│   ├── Fleet/
│   ├── DOP/
│   └── Route Sheets/
├── README.md                            # This file
└── .venv/                               # Python virtual environment
```

## Quickstart

### 1. Install Dependencies
```bash
cd c:\Users\chief\NDAY_OM
pip install -r api/requirements.txt
```

### 2. Start the Backend
```bash
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

The API will be available at http://127.0.0.1:8000

### 3. Check API Status
```bash
curl http://127.0.0.1:8000/
# Response: {"message":"NDAY_OM API is running."}
```

### 4. Upload Files & Check Status
```bash
# Upload DOP
curl -X POST -F "file=@path/to/dop.xlsx" http://127.0.0.1:8000/upload/dop

# Upload Fleet
curl -X POST -F "file=@path/to/fleet.xlsx" http://127.0.0.1:8000/upload/fleet

# Upload Route Sheets (multiple files)
curl -X POST -F "files=@path/to/route1.pdf" -F "files=@path/to/route2.pdf" http://127.0.0.1:8000/upload/route-sheets

# Check ingest status
curl http://127.0.0.1:8000/upload/status
```

## Validation Rules (Governance-Enforced)

### Route Code
- Must be 4-5 characters after normalization
- Normalized to uppercase, trailing spaces removed, apostrophes stripped, internal spaces removed
- Example: `cx123` → `CX123`

### Service Type Mapping
- Canonicalized using alias map from governance documents
- Examples:
  - "Rivian MEDIUM" → "Standard Parcel Electric - Rivian MEDIUM"
  - "AmFlex Large Vehicle" → "4WD P31 Delivery Truck"
  - "Nursery Route Level 1" → "Standard Parcel - Extra Large Van - US"

### Fleet Vehicles
- Only vehicles with `operationalStatus != "Grounded"` are eligible
- Primary key: VIN
- Display name: Vehicle Name
- Service Type must match a known canonical type

### DOP (Day of Plan)
- All routes must have a DSP, Route Code, Service Type, Wave, Staging Location, and Route Duration
- Missing or invalid data triggers validation warnings/errors

### Cross-File Consistency
- Routes in DOP but not in Route Sheets: Warning issued
- Routes in Route Sheets but not in DOP: Warning issued
- Service types in DOP must exist in Fleet: Warning issued if mismatch

## Output Status
Example `/upload/status` response:
```json
{
  "dop_uploaded": true,
  "fleet_uploaded": true,
  "cortex_uploaded": false,
  "route_sheets_uploaded": true,
  "dop_record_count": 12,
  "fleet_record_count": 45,
  "route_sheets_count": 3,
  "validation_errors": [],
  "validation_warnings": [
    "Routes in DOP but not in Route Sheets: CX999"
  ],
  "last_updated": "2026-02-17T10:30:45.123456"
}
```

## Next Steps (Future Implementation)
- Cortex file parsing (driver assignment data)
- Vehicle assignment logic (matching route service types to fleet)
- Driver handout PDF generation (layout per governance)
- Frontend UI (Next.js with upload dropzones)
- Persistent data storage / audit trail

## Troubleshooting

### Backend won't start
- Ensure Python 3.14+ is installed
- Check that virtual environment is activated: `c:\Users\chief\NDAY_OM\.venv\Scripts\activate`
- Verify dependencies: `pip list`

### Upload endpoints return 500 errors
- Check `/upload/status` for detailed validation errors
- Ensure Excel files have correct column counts and data types
- PDF files must be valid PDFs with extractable text

### File path issues
- All uploaded files are stored in `uploads/` directory (auto-created)
- Temporary files can be cleaned up safely

## References
- See [Governance](Governance/) folder for full requirements and PDF format specifications
- See [DSP_Route_Manager_Software_Manual.md](Governance/DSP_Route_Manager_Software_Manual.md) for system blueprint
- Service type mappings: [Ingest Matrix.xlsx](governance/Ingest%20Matrix.xlsx)
