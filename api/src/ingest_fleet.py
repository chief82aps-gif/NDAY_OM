"""Fleet Excel ingest parser."""
import pandas as pd
from typing import List, Tuple
from api.src.models import Vehicle
from api.src.normalization import normalize_service_type


def parse_fleet_excel(file_path: str) -> Tuple[List[Vehicle], List[str]]:
    """Parse Fleet Excel file and return vehicle records and validation errors."""
    errors = []
    records = []
    
    try:
        # Read first sheet, no headers assumption (data-driven validation)
        df = pd.read_excel(file_path, sheet_name=0, header=None)
        
        if df.shape[0] < 1 or df.shape[1] < 4:
            errors.append("Fleet file has insufficient columns or rows. Expected at least 4 columns.")
            return records, errors
        
        # Detect if first row is a header by checking for known header names
        first_row_text = str(df.iloc[0, 0]).lower() if df.shape[0] > 0 else ""
        start_idx = 0
        vin_keywords = ["vin", "vehicle"]
        if any(keyword in first_row_text for keyword in vin_keywords) or first_row_text.startswith("vin"):
            start_idx = 1  # Skip header row
        
        # Expected column order per governance:
        # 0: VIN, 1: serviceType, 2: vehicleName, 3-11: (ignored), 12: operationalStatus
        # Note: Column L is operational_status in 0-indexed terms (column 11)
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                vin = str(row[0]).strip() if pd.notna(row[0]) else ""
                service_type = str(row[1]).strip() if pd.notna(row[1]) else ""
                vehicle_name = str(row[2]).strip() if pd.notna(row[2]) else ""
                
                # Operational status is in column L (index 11)
                operational_status = str(row[11]).strip() if len(row) > 11 and pd.notna(row[11]) else "OPERATIONAL"
                
                # Normalize and validate
                if not vin:
                    errors.append(f"Row {idx+1}: VIN is empty.")
                    continue
                
                if not service_type:
                    errors.append(f"Row {idx+1}: Service type is empty.")
                    continue
                
                service_type_norm = normalize_service_type(service_type)
                if not service_type_norm:
                    errors.append(f"Row {idx+1}: Service type '{service_type}' is unrecognized.")
                    continue
                
                if not vehicle_name:
                    errors.append(f"Row {idx+1}: Vehicle name is empty.")
                    continue
                
                # Skip grounded vehicles
                if operational_status.upper() == "GROUNDED":
                    continue  # Skip, not an error - just ineligible
                
                record = Vehicle(
                    vin=vin,
                    service_type=service_type_norm,
                    vehicle_name=vehicle_name,
                    operational_status=operational_status,
                )
                records.append(record)
            except Exception as e:
                errors.append(f"Row {idx+1}: Error parsing row - {str(e)}")
                continue
    
    except Exception as e:
        errors.append(f"Failed to read Fleet Excel file: {str(e)}")
    
    return records, errors
