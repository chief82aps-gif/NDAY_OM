"""DOP (Day of Plan) Excel ingest parser."""
import pandas as pd
from typing import List, Tuple
from api.src.models import RouteDOP
from api.src.normalization import normalize_route_code, normalize_service_type, validate_route_code


def parse_dop_excel(file_path: str) -> Tuple[List[RouteDOP], List[str]]:
    """Parse DOP Excel file and return route records and validation errors."""
    errors = []
    records = []
    
    try:
        # Read first sheet, no headers assumption (data-driven validation)
        df = pd.read_excel(file_path, sheet_name=0, header=None)
        
        if df.shape[0] < 1 or df.shape[1] < 7:
            errors.append("DOP file has insufficient columns or rows. Expected at least 7 columns.")
            return records, errors
        
        # Detect if first row is a header by checking if it contains known header names
        first_row_text = str(df.iloc[0, 0]).lower() if df.shape[0] > 0 else ""
        start_idx = 0
        header_keywords = ["dsp", "route", "service", "wave", "staging", "duration"]
        if any(keyword in first_row_text for keyword in header_keywords):
            start_idx = 1  # Skip header row
        
        # Expected column order per governance:
        # 0: DSP, 1: Route Code, 2: Service Type, 3: Wave, 4: Staging Location, 
        # 5: Route Duration, 6: Num Zones, 7: Num Packages, 8: Num Commercial Pkgs
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                dsp = str(row[0]).strip() if pd.notna(row[0]) else ""
                route_code_raw = str(row[1]).strip() if pd.notna(row[1]) else ""
                service_type = str(row[2]).strip() if pd.notna(row[2]) else ""
                wave = str(row[3]).strip() if pd.notna(row[3]) else ""
                staging_location = str(row[4]).strip() if pd.notna(row[4]) else ""
                route_duration_raw = row[5] if pd.notna(row[5]) else None
                num_zones = int(row[6]) if pd.notna(row[6]) and isinstance(row[6], (int, float)) else None
                num_packages = int(row[7]) if len(row) > 7 and pd.notna(row[7]) and isinstance(row[7], (int, float)) else None
                num_commercial_pkgs = int(row[8]) if len(row) > 8 and pd.notna(row[8]) and isinstance(row[8], (int, float)) else None
                
                # Normalize and validate
                if not dsp:
                    errors.append(f"Row {idx+1}: DSP is empty.")
                    continue
                
                route_code = normalize_route_code(route_code_raw)
                if not validate_route_code(route_code_raw):
                    errors.append(f"Row {idx+1}: Route code '{route_code_raw}' is invalid or exceeds 5 characters.")
                    continue
                
                service_type_norm = normalize_service_type(service_type)
                if not service_type_norm:
                    errors.append(f"Row {idx+1}: Service type '{service_type}' is unrecognized.")
                    continue
                
                # Parse route duration (convert if numeric, else keep as-is)
                try:
                    if isinstance(route_duration_raw, (int, float)):
                        route_duration = int(route_duration_raw)
                    else:
                        route_duration = int(str(route_duration_raw).strip())
                except (ValueError, TypeError):
                    errors.append(f"Row {idx+1}: Route duration '{route_duration_raw}' is not a valid number.")
                    continue
                
                record = RouteDOP(
                    dsp=dsp,
                    route_code=route_code,
                    service_type=service_type_norm,
                    wave=wave,
                    staging_location=staging_location,
                    route_duration=route_duration,
                    num_zones=num_zones,
                    num_packages=num_packages,
                    num_commercial_pkgs=num_commercial_pkgs,
                )
                records.append(record)
            except Exception as e:
                errors.append(f"Row {idx+1}: Error parsing row - {str(e)}")
                continue
    
    except Exception as e:
        errors.append(f"Failed to read DOP Excel file: {str(e)}")
    
    return records, errors
