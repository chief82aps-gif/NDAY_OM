# NDAY Route Manager Frontend

Next.js-based web interface for the NDAY route management system.

## Features

- **File Uploads**: Drag-and-drop interface for all 4 ingest types (DOP, Fleet, Cortex, Route Sheets)
- **NDL Branding**: Blue/white color scheme with custom styling
- **Real-time Status**: Live upload status and validation results
- **Vehicle Assignment**: One-click vehicle-to-route assignment
- **PDF Generation**: Generate driver handout PDFs with route details

## Setup

### Prerequisites

- Node.js 18+ (LTS recommended)
- npm or yarn

### Installation

```bash
cd frontend
npm install
```

### Development

```bash
npm run dev
```

Frontend will be available at: `http://localhost:3000`

Backend API must be running on `http://127.0.0.1:8000`

### Environment Variables

Create `.env.local` for development:

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

### Build & Production

```bash
npm run build
npm start
```

## Component Structure

- **pages/index.tsx** - Main upload and management interface
- **components/UploadZone.tsx** - Drag-and-drop file upload component
- **components/StatusDisplay.tsx** - Status and validation display
- **styles/globals.css** - Global styles with Tailwind CSS + custom utilities

## API Integration

Frontend communicates with backend FastAPI via HTTP:

### Endpoints

- `POST /upload/dop` - Upload DOP Excel file
- `POST /upload/fleet` - Upload Fleet Excel file
- `POST /upload/cortex` - Upload Cortex Excel file (drivers)
- `POST /upload/route-sheets` - Upload Route Sheet PDFs
- `GET /upload/status` - Get ingest status
- `POST /upload/assign-vehicles` - Assign vehicles to routes
- `POST /upload/generate-handouts` - Generate driver handout PDF

## Styling

- **Color Scheme**: NDL Blue (#003DA5) + White
- **Framework**: Tailwind CSS 3
- **Typography**: System fonts + custom utilities
- **Responsive**: Mobile-first design, grid-based layout

## Features in Detail

### Upload Interface

Four dropzones for different ingest types:
- **DOP**: Excel file with daily route plan
- **Fleet**: Excel file with vehicle inventory
- **Cortex**: Excel file with driver assignments
- **Route Sheets**: PDF file(s) with load manifests

### Status Display

Real-time feedback showing:
- Upload success/failure
- Record counts for each ingest type
- Validation errors (blocking issues)
- Validation warnings (information only)
- Assignment and PDF generation progress

### Vehicle Assignment

Automatically matches routes to fleet vehicles:
- Service type matching
- Fallback handling (e.g., CDV14 → CDV16)
- Driver enrichment from Cortex data
- One-click assignment workflow

### Driver Handout Generation

Creates professional PDF handouts:
- 2×2 card layout per page
- Route details, driver, vehicle info
- Load manifest (bags + overflow)
- Blue branding throughout
- Direct download to browser

## Development Notes

- Built with TypeScript for type safety
- React 18 with Next.js 15 (App Router compatible)
- Tailwind CSS for responsive styling
- Form handling via HTML FileInput
- Fetch API for backend communication

## Troubleshooting

### "Cannot connect to API"
- Ensure backend is running on `http://127.0.0.1:8000`
- Check `NEXT_PUBLIC_API_URL` environment variable
- Verify CORS headers on backend

### "Uploads fail with 422"
- Check file format matches expected type (.xlsx for Excel, .pdf for PDFs)
- Verify file is not corrupted
- Check backend logs for parsing errors

### "PDF generation fails"
- Ensure all 4 ingest types are uploaded first
- Run "Assign Vehicles" before generating handouts
- Check that Route Sheets are uploaded with valid data

## Future Development

- [ ] Backend auth/authorization
- [ ] File download directly from web
- [ ] Batch upload management
- [ ] Advanced filtering and search
- [ ] Data export features
- [ ] Mobile app version

## License

Copyright © 2026 New Day Logistics. All rights reserved.
