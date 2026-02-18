# DSP Route Manager: Software Manual & System Blueprint

## 1. System Overview
The DSP Route Manager is a full-stack web application for ingesting, validating, and generating driver handout PDFs for Amazon Delivery Service Providers (DSPs). It automates the process of merging operational data from multiple sources, applies business rules, and produces ready-to-print handouts for drivers.

---

## 2. High-Level Architecture
- **Frontend:** Next.js (React) web app for file upload, status, and PDF download.
- **Backend:** FastAPI (Python) API for ingest, validation, and PDF generation.
- **Data Processing:** Python scripts for merging, validation, and rendering PDFs.
- **Storage:** Files are processed in-memory or in temp directories; no persistent DB required for MVP.

---

## 3. Suggested File Structure
```
DSP_OM/
├── api/
│   ├── main.py                # FastAPI entrypoint
│   ├── requirements.txt       # Backend dependencies
│   └── src/
│       ├── routes/
│       │   ├── uploads.py     # File upload endpoints
│       │   └── driver_handouts.py # PDF generation endpoint
│       └── ...
├── driver_handout_tool/
│   ├── run_driver_handouts.py # Main PDF generation script
│   ├── ingest/
│   │   ├── merge.py          # Data merging/validation logic
│   │   └── driver_prompt.py  # Driver assignment logic
│   └── pdf/
│       └── render_handout.py # PDF rendering logic
├── frontend/
│   ├── pages/
│   │   ├── handouts.tsx      # Main UI for uploads/generation
│   │   └── index.tsx         # Redirects to /handouts
│   └── ...
├── db/                       # (Optional) SQL schema/migrations
├── scripts/                  # Utility scripts
└── test_*.xlsx/.pdf          # Example ingest files
```

---

## 4. Ingestion File Requirements & Headers

### 4.1 Cortex File (cortex.xlsx)
- **Purpose:** Primary source for route, driver, service type, wave time, duration, and staging location.
- **Required Columns:**
  - Route Code (e.g., "Route", "Route Code", "RouteCode", "route_code")
  - Driver Name (e.g., "Driver", "Driver Name", "driver_name")
  - Service Type (e.g., "Service Type", "service_type", "ServiceType")
  - Wave Time (e.g., "Wave Time", "Wave", "wave_time")
  - Route Duration (e.g., "Route Duration", "Duration", "route_duration", "duration_minutes")
  - Staging Location (e.g., "Staging Location", "Staging", "staging_location")

### 4.2 DOP File (dop.xlsx or dop.csv)
- **Purpose:** Provides authoritative wave times and durations for each route.
- **Required Columns:**
  - Route Code (same as above)
  - Wave Time (same as above)
  - Route Duration (same as above)
  - Service Type (optional, for cross-check)

### 4.3 Fleet File (fleet.xlsx)
- **Purpose:** Maps service types to available vehicles and VINs.
- **Required Columns:**
  - Vehicle Name (e.g., "vehicleName", "Vehicle Name", "name")
  - VIN (e.g., "vin", "VIN")
  - Service Type (e.g., "serviceType", "Service Type")
  - Operational Status (e.g., "operationalStatus", "Operational Status")

### 4.4 Route Sheets (route_sheets.pdf)
- **Purpose:** Provides bag/overflow details and staging locations for each route.
- **Format:** PDF, optionally referenced via a manifest JSON.

---

## 5. Ingestion & Validation Rules
- **Route Code Normalization:** Uppercase, strip whitespace, remove leading apostrophes, remove internal spaces.
- **Service Type Mapping:**
  - Canonicalized using alias map and heuristics (see _SERVICE_TYPE_ALIASES in merge.py).
  - Must match a known Fleet service type.
- **Wave Time & Duration:**
  - Pulled from DOP if available, else from Cortex.
  - Multiple time formats supported (e.g., 09:30, 9:30 AM, 480).
- **Vehicle Assignment:**
  - Auto-assigned by service type and operational status.
  - Fallbacks: CDV14 → CDV16, Electric → any operational electric.
  - If no eligible vehicle, user is prompted (409 error).
- **Driver Assignment:**
  - If ambiguous or missing, user is prompted (409 error).
- **Bag/Overflow Parsing:**
  - Extracted from Route Sheets PDF using regex and mapped to route codes.
- **Error Handling:**
  - 409: User input required (van/driver assignment, missing wave time).
  - 500: Hard failure (invalid file, missing columns, etc).

---

## 6. API Endpoints
- **POST /upload/cortex**: Upload Cortex file
- **POST /upload/dop**: Upload DOP file
- **POST /upload/fleet**: Upload Fleet file
- **POST /upload/route-sheets**: Upload Route Sheets PDF
- **POST /driver-handouts/generate**: Generate handouts PDF (requires all files)
- **GET /upload/status**: Get status of uploaded files

---

## 7. Driver Handout Sheet (PDF) Layout
- **One card per route, 4 per page**
- **Fields:**
  - Route Code
  - Driver Name
  - Vehicle Name
  - Wave Time (formatted)
  - Duration (minutes)
  - Staging Location
  - Bags (table)
  - Overflow (table)
  - Expected Return Time
  - Motivational Quote

---

## 8. End-to-End Workflow
1. User uploads all required files via the web UI.
2. Backend validates and stores files in temp directories.
3. User clicks "Generate Driver Handouts".
4. Backend merges data, applies rules, and generates PDF.
5. If user input is required (van/driver assignment, missing wave), a prompt is shown in the UI.
6. User resolves prompts and re-submits if needed.
7. On success, PDF is returned for download/print.

---

## 9. Key Business Rules
- All routes must have a valid service type, wave time, duration, and assigned vehicle.
- If any required data is missing or ambiguous, the system prompts the user for resolution.
- All file headers must match or be mapped to the expected columns (see above).
- PDF output must be formatted for easy driver use and printing.

---

## 10. Versioning & Governance
- All ingestion rules, headers, and mappings should be version-controlled.
- Any changes to file formats or business rules must be documented and reflected in this manual.

---

## 11. Example: Minimal Driver Handout Card
| Route | Driver | Vehicle | Wave | Duration | Staging | Bags | Overflow | Return | Quote |
|-------|--------|---------|------|----------|---------|------|----------|--------|-------|
| CX123 | J. Doe | 3355 CDV 16' | 8:30 AM | 480 | STG.1A | [Bag1, Bag2] | [Zone1:2] | 3:30 PM | "Smooth is fast. Fast is smooth." |

---

## 12. Quickstart
1. Clone repo and install dependencies (npm install, pip install -r requirements.txt).
2. Start backend: `python -m uvicorn api.main:app --reload`
3. Start frontend: `npm run dev` (from frontend directory)
4. Open http://localhost:3000 and upload files.
5. Click "Generate Driver Handouts" and download the PDF.

---

## 13. Troubleshooting
- 409 errors: Check for missing/ambiguous driver or vehicle assignments, or missing wave times.
- 500 errors: Check file formats, required columns, and logs for details.
- Next.js not starting: Ensure no .lnk or shortcut files in pages/, clean node_modules and .next, and run npm install.

---

This manual is designed to be a complete blueprint for building, running, and maintaining the DSP Route Manager system. Hand this to any developer and they should be able to get the system online in minutes.
