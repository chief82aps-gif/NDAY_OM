"""Cortex Excel ingest parser."""
import pandas as pd
from typing import List, Tuple, Optional
from dataclasses import dataclass
from api.src.column_mapping import build_column_map


CORTEX_COLUMN_ALIASES = {
    "route_code": ["route", "route code", "routecode", "route id"],
    "dsp": ["dsp", "company"],
    "transporter_id": ["transporter", "transporter id", "driver id"],
    "driver_name": ["driver", "driver name", "associate"],
    "progress_status": ["progress", "route progress", "status"],
    "service_type": ["service", "delivery service type", "service type"],
}

CORTEX_FALLBACK_COLUMNS = {
    "route_code": 0,
    "dsp": 1,
    "transporter_id": 2,
    "driver_name": 3,
    "progress_status": 4,
    "service_type": 5,
}


def _safe_cell(row: pd.Series, col_idx: int):
    if col_idx < 0 or col_idx >= len(row):
        return None
    return row.iloc[col_idx]


@dataclass
class CortexRoute:
    """Cortex route assignment record."""
    transporter_id: str
    driver_name: str
    dsp: str
    route_code: str
    delivery_service_type: str
    cortex_vin_number: Optional[str] = None
    progress_status: Optional[str] = None
    projected_return: Optional[str] = None


def parse_cortex_excel(file_path: str) -> Tuple[List[CortexRoute], List[str]]:
    """Parse Cortex Excel file and return route assignment records and validation errors."""
    errors = []
    records = []
    
    try:
        # Read first sheet, no headers assumption (data-driven validation)
        df = pd.read_excel(file_path, sheet_name=0, header=None)
        
        if df.shape[0] < 1 or df.shape[1] < 6:
            errors.append("Cortex file has insufficient columns or rows. Expected at least 6 columns.")
            return records, errors
        
        column_map, start_idx = build_column_map(df, CORTEX_COLUMN_ALIASES, CORTEX_FALLBACK_COLUMNS)
        
        from api.src.normalization import normalize_route_code, normalize_service_type
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                route_cell = _safe_cell(row, column_map["route_code"])
                dsp_cell = _safe_cell(row, column_map["dsp"])
                transporter_cell = _safe_cell(row, column_map["transporter_id"])
                driver_cell = _safe_cell(row, column_map["driver_name"])
                progress_cell = _safe_cell(row, column_map["progress_status"])
                service_cell = _safe_cell(row, column_map["service_type"])

                route_code_raw = str(route_cell).strip() if pd.notna(route_cell) else ""
                dsp = str(dsp_cell).strip() if pd.notna(dsp_cell) else ""
                transporter_id = str(transporter_cell).strip() if pd.notna(transporter_cell) else ""
                driver_name = str(driver_cell).strip() if pd.notna(driver_cell) else ""
                progress_status = str(progress_cell).strip() if pd.notna(progress_cell) else None
                service_type_raw = str(service_cell).strip() if pd.notna(service_cell) else ""
                
                # Normalize and validate
                if not transporter_id:
                    errors.append(f"Row {idx+1}: Transporter ID is empty.")
                    continue
                
                if not driver_name or driver_name.lower() == "missing":
                    errors.append(f"Row {idx+1}: Driver name is empty or missing.")
                    continue
                
                route_code = normalize_route_code(route_code_raw)
                if not route_code:
                    errors.append(f"Row {idx+1}: Route code '{route_code_raw}' is invalid or empty.")
                    continue
                
                service_type = normalize_service_type(service_type_raw)
                if not service_type:
                    errors.append(f"Row {idx+1}: Service type '{service_type_raw}' is unrecognized.")
                    continue
                
                record = CortexRoute(
                    transporter_id=transporter_id,
                    driver_name=driver_name,
                    dsp=dsp,
                    route_code=route_code,
                    delivery_service_type=service_type,
                    cortex_vin_number=None,  # Not in current Cortex file
                    progress_status=progress_status,
                    projected_return=None,  # Not in current Cortex file
                )
                records.append(record)
            except Exception as e:
                errors.append(f"Row {idx+1}: Error parsing row - {str(e)}")
                continue
    
    except Exception as e:
        errors.append(f"Failed to read Cortex Excel file: {str(e)}")
    
    return records, errors
