#!/usr/bin/env python
"""Test the updated two-column layout and expected return time"""
import json
from pathlib import Path
from api.src.orchestrator import IngestOrchestrator

# Initialize orchestrator
orch = IngestOrchestrator()

# Use correct files from Ingest directory
dop_file = Path("Ingest/DOP_ingest/NDAY DOP 2.17.26.xlsx")
fleet_file = Path("Ingest/Fleet_ingest/VehiclesData (21).xlsx")
cortex_file = Path("Ingest/Cortex_ingest/Routes_DLV3_2026-02-17_09_43 (PST).xlsx")
route_sheets = list(Path("api/uploads").glob("route_sheet_*.pdf"))

print(f"DOP: {dop_file.exists()}")
print(f"Fleet: {fleet_file.exists()}")
print(f"Cortex: {cortex_file.exists()}")
print(f"Route Sheets: {len(route_sheets)} files")

if dop_file.exists() and fleet_file.exists() and cortex_file.exists():
    # Ingest files
    print("\nIngesting files...")
    orch.ingest_dop(str(dop_file))
    print(f"DOP records: {len(orch.status.dop_records)}")
    
    orch.ingest_fleet(str(fleet_file))
    print(f"Fleet records: {len(orch.status.fleet_records)}")
    
    orch.ingest_cortex(str(cortex_file))
    print(f"Cortex records: {len(orch.status.cortex_records)}")
    
    if route_sheets:
        orch.ingest_route_sheets([str(rs) for rs in route_sheets])
        print(f"Route sheets: {len(orch.status.route_sheets)}")
    
        # Check expected_return before validation
        print(f"\nBefore validation:")
        if orch.status.route_sheets:
            rs = orch.status.route_sheets[0]
            print(f"  Route: {rs.route_code}")
            print(f"  Wave time: {rs.wave_time}")
            print(f"  Expected return: {rs.expected_return}")
        
        # Validate
        print(f"\nValidating...")
        orch.validate_cross_file_consistency()
        
        # Check expected_return after validation
        print(f"\nAfter validation:")
        if orch.status.route_sheets:
            rs = orch.status.route_sheets[0]
            print(f"  Route: {rs.route_code}")
            print(f"  Wave time: {rs.wave_time}")
            print(f"  Expected return: {rs.expected_return}")
        
        # Assign vehicles
        print(f"\nAssigning vehicles...")
        assign_result = orch.assign_vehicles()
        print(f"Assignment result: {assign_result}")
        
        # Generate PDF
        output_path = Path("api/uploads/test_format_handouts.pdf")
        print(f"\nGenerating PDF to {output_path}...")
        result = orch.generate_handouts(str(output_path))
        
        if result["success"]:
            print("PDF generated successfully!")
            print(f"  Cards: {result['cards_generated']}")
            print(f"  Path: {result['output_path']}")
        else:
            print(f"PDF generation failed: {result['message']}")
    else:
        print("WARNING: No route sheets found")
else:
    print("ERROR: Sample files not found")

