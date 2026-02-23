"""Fleet Excel ingest parser."""
import pandas as pd
from typing import List, Tuple
from api.src.models import Vehicle
from api.src.normalization import normalize_service_type
from api.src.column_mapping import build_column_map


FLEET_COLUMN_ALIASES = {
    "vin": ["vin", "vin number", "vehicle vin"],
    "service_type": ["service", "service type", "delivery service type"],
    "vehicle_name": ["vehicle", "vehicle name", "truck", "unit", "truck id"],
    "operational_status": ["operational status", "status", "availability"],
}

FLEET_FALLBACK_COLUMNS = {
    "vin": 0,
    "service_type": 1,
    "vehicle_name": 2,
    "operational_status": 11,
}


def _safe_cell(row: pd.Series, col_idx: int):
    if col_idx < 0 or col_idx >= len(row):
        return None
    return row.iloc[col_idx]


def parse_fleet_excel(file_path: str) -> Tuple[List[Vehicle], List[str]]:
    """Parse Fleet Excel or CSV file and return vehicle records and validation errors."""
    errors = []
    records = []
    
    try:
        # Detect file type and read accordingly
        if file_path.lower().endswith('.csv'):
            df = pd.read_csv(file_path, header=None)
        else:
            # Read first sheet for Excel files, no headers assumption (data-driven validation)
            df = pd.read_excel(file_path, sheet_name=0, header=None)
        
        if df.shape[0] < 1 or df.shape[1] < 4:
            errors.append("Fleet file has insufficient columns or rows. Expected at least 4 columns.")
            return records, errors
        
        column_map, start_idx = build_column_map(df, FLEET_COLUMN_ALIASES, FLEET_FALLBACK_COLUMNS)
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                vin_cell = _safe_cell(row, column_map["vin"])
                service_cell = _safe_cell(row, column_map["service_type"])
                vehicle_cell = _safe_cell(row, column_map["vehicle_name"])
                status_cell = _safe_cell(row, column_map["operational_status"])

                vin = str(vin_cell).strip() if pd.notna(vin_cell) else ""
                service_type = str(service_cell).strip() if pd.notna(service_cell) else ""
                vehicle_name = str(vehicle_cell).strip() if pd.notna(vehicle_cell) else ""
                operational_status = str(status_cell).strip() if pd.notna(status_cell) else "OPERATIONAL"
                
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
        errors.append(f"Failed to read Fleet file: {str(e)}")
    
    return records, errors
