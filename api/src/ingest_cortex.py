"""Cortex Excel ingest parser."""
import pandas as pd
from typing import List, Tuple, Optional
from dataclasses import dataclass


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
        
        # Detect if first row is a header by checking for known header names
        first_row_text = str(df.iloc[0, 0]).lower() if df.shape[0] > 0 else ""
        start_idx = 0
        header_keywords = ["route code", "transporter", "driver", "service"]
        if any(keyword in first_row_text for keyword in header_keywords):
            start_idx = 1  # Skip header row
        
        # Actual column order from sample file:
        # 0: Route code, 1: DSP, 2: Transporter Id, 3: Driver name, 4: Route progress,
        # 5: Delivery Service Type, 6: Route Duration, 7+: Optional (All stops, etc.)
        
        from api.src.normalization import normalize_route_code, normalize_service_type
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                route_code_raw = str(row[0]).strip() if pd.notna(row[0]) else ""
                dsp = str(row[1]).strip() if pd.notna(row[1]) else ""
                transporter_id = str(row[2]).strip() if pd.notna(row[2]) else ""
                driver_name = str(row[3]).strip() if pd.notna(row[3]) else ""
                progress_status = str(row[4]).strip() if pd.notna(row[4]) else None
                service_type_raw = str(row[5]).strip() if len(row) > 5 and pd.notna(row[5]) else ""
                
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
