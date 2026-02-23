"""Fleet Excel ingest parser."""
import pandas as pd
import logging
from typing import List, Tuple
from api.src.models import Vehicle
from api.src.normalization import normalize_service_type
from api.src.column_mapping import build_column_map

logger = logging.getLogger(__name__)


FLEET_COLUMN_ALIASES = {
    "vin": ["vin", "vin number", "vehicle vin"],
    "service_type": ["service", "service type", "delivery service type"],
    "vehicle_name": ["vehicle", "vehicle name", "truck", "unit", "truck id"],
    "operational_status": [
        "operational status",
        "operationalstatus",
        "operational_status",
        "status",
        "availability",
    ],
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
            logger.info(f"Fleet: Loaded CSV file with shape {df.shape}")
        else:
            # Read first sheet for Excel files, no headers assumption (data-driven validation)
            df = pd.read_excel(file_path, sheet_name=0, header=None)
            logger.info(f"Fleet: Loaded Excel file with shape {df.shape}")
        
        if df.shape[0] < 1 or df.shape[1] < 4:
            error_msg = "Fleet file has insufficient columns or rows. Expected at least 4 columns."
            errors.append(error_msg)
            logger.warning(f"Fleet: {error_msg}")
            return records, errors
        
        logger.info(f"Fleet: File dimensions OK. Attempting column detection with search_rows=25, min_hits=1")
        column_map, start_idx = build_column_map(
            df,
            FLEET_COLUMN_ALIASES,
            FLEET_FALLBACK_COLUMNS,
            search_rows=25,
            min_hits=1,
        )
        logger.info(f"Fleet: Column map detected: {column_map}, data starts at row {start_idx}")
        
        if start_idx >= len(df):
            error_msg = f"Fleet: No data rows found (header detected at row {start_idx}, but file only has {len(df)} rows)"
            errors.append(error_msg)
            logger.warning(error_msg)
            return records, errors
        
        parsed_count = 0
        skipped_grounded = 0
        skipped_validation = 0
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                # Skip any row that contains the word GROUNDED anywhere
                row_text = " ".join(
                    str(cell).strip().upper()
                    for cell in row.tolist()
                    if pd.notna(cell)
                )
                if "GROUNDED" in row_text:
                    skipped_grounded += 1
                    continue

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
                    skipped_validation += 1
                    continue
                
                if not service_type:
                    errors.append(f"Row {idx+1}: Service type is empty.")
                    skipped_validation += 1
                    continue
                
                service_type_norm = normalize_service_type(service_type)
                if not service_type_norm:
                    errors.append(f"Row {idx+1}: Service type '{service_type}' is unrecognized.")
                    skipped_validation += 1
                    continue
                
                if not vehicle_name:
                    errors.append(f"Row {idx+1}: Vehicle name is empty.")
                    skipped_validation += 1
                    continue
                
                # Skip grounded vehicles only
                status_norm = operational_status.strip().upper()
                if status_norm == "GROUNDED":
                    skipped_grounded += 1
                    continue  # Skip, not an error - just ineligible
                
                record = Vehicle(
                    vin=vin,
                    service_type=service_type_norm,
                    vehicle_name=vehicle_name,
                    operational_status=operational_status,
                )
                records.append(record)
                parsed_count += 1
            except Exception as e:
                errors.append(f"Row {idx+1}: Error parsing row - {str(e)}")
                skipped_validation += 1
                continue
        
        logger.info(f"Fleet: Parsed {parsed_count} records, skipped {skipped_grounded} (GROUNDED), {skipped_validation} (validation errors)")
    
    except Exception as e:
        error_msg = f"Failed to read Fleet file: {str(e)}"
        errors.append(error_msg)
        logger.error(error_msg)
    
    return records, errors
