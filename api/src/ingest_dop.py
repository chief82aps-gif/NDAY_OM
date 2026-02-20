"""DOP (Day of Plan) Excel ingest parser."""
import pandas as pd
from typing import List, Tuple
from api.src.models import RouteDOP
from api.src.normalization import normalize_route_code, normalize_service_type, validate_route_code
from api.src.column_mapping import build_column_map


DOP_COLUMN_ALIASES = {
    "dsp": ["dsp", "company"],
    "route_code": ["route", "route code", "routecode", "route id"],
    "service_type": ["service", "service type", "delivery service type", "servicetype"],
    "wave": ["wave", "wave time", "departure wave"],
    "staging_location": ["staging", "staging location", "station"],
    "route_duration": ["duration", "route duration", "route mins", "minutes"],
    "num_zones": ["zones", "num zones", "number of zones"],
    "num_packages": ["packages", "num packages", "package count", "total packages"],
    "num_commercial_pkgs": [
        "commercial",
        "commercial pkgs",
        "num commercial pkgs",
        "commercial packages",
    ],
}

DOP_FALLBACK_COLUMNS = {
    "dsp": 0,
    "route_code": 1,
    "service_type": 2,
    "wave": 3,
    "staging_location": 4,
    "route_duration": 5,
    "num_zones": 6,
    "num_packages": 7,
    "num_commercial_pkgs": 8,
}


def _safe_cell(row: pd.Series, col_idx: int):
    if col_idx < 0 or col_idx >= len(row):
        return None
    return row.iloc[col_idx]


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
        
        column_map, start_idx = build_column_map(df, DOP_COLUMN_ALIASES, DOP_FALLBACK_COLUMNS)
        
        for idx, row in df.iloc[start_idx:].iterrows():
            try:
                dsp_cell = _safe_cell(row, column_map["dsp"])
                route_code_cell = _safe_cell(row, column_map["route_code"])
                service_type_cell = _safe_cell(row, column_map["service_type"])
                wave_cell = _safe_cell(row, column_map["wave"])
                staging_cell = _safe_cell(row, column_map["staging_location"])
                route_duration_cell = _safe_cell(row, column_map["route_duration"])
                num_zones_cell = _safe_cell(row, column_map["num_zones"])
                num_packages_cell = _safe_cell(row, column_map["num_packages"])
                num_commercial_cell = _safe_cell(row, column_map["num_commercial_pkgs"])

                dsp = str(dsp_cell).strip() if pd.notna(dsp_cell) else ""
                route_code_raw = str(route_code_cell).strip() if pd.notna(route_code_cell) else ""
                service_type = str(service_type_cell).strip() if pd.notna(service_type_cell) else ""
                wave = str(wave_cell).strip() if pd.notna(wave_cell) else ""
                staging_location = str(staging_cell).strip() if pd.notna(staging_cell) else ""
                route_duration_raw = route_duration_cell if pd.notna(route_duration_cell) else None
                num_zones = int(num_zones_cell) if pd.notna(num_zones_cell) and isinstance(num_zones_cell, (int, float)) else None
                num_packages = int(num_packages_cell) if pd.notna(num_packages_cell) and isinstance(num_packages_cell, (int, float)) else None
                num_commercial_pkgs = int(num_commercial_cell) if pd.notna(num_commercial_cell) and isinstance(num_commercial_cell, (int, float)) else None
                
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
