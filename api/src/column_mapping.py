"""Utilities for resilient spreadsheet column detection."""
from typing import Dict, Iterable, List, Optional, Tuple
import re

import pandas as pd


def _normalize_text(value) -> str:
    """Normalize a header cell for loose matching."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    cleaned_chars = []
    for char in text:
        if char.isalnum() or char.isspace():
            cleaned_chars.append(char)
    return " ".join("".join(cleaned_chars).split())


def _cell_matches_alias(cell_text: str, alias: str) -> bool:
    """Return True when a normalized cell roughly matches an alias."""
    if not cell_text or not alias:
        return False
    return alias in cell_text or cell_text in alias


def detect_header_row(
    df: pd.DataFrame,
    aliases_by_field: Dict[str, Iterable[str]],
    search_rows: int = 10,
    min_hits: int = 2,
) -> Optional[int]:
    """Find a likely header row by counting semantic alias matches."""
    if df.empty:
        return None

    normalized_aliases = {
        field: [_normalize_text(alias) for alias in aliases]
        for field, aliases in aliases_by_field.items()
    }

    best_row = None
    best_hits = 0
    rows_to_scan = min(search_rows, len(df))

    for row_idx in range(rows_to_scan):
        row_values = [_normalize_text(value) for value in df.iloc[row_idx].tolist()]
        row_hits = 0
        for aliases in normalized_aliases.values():
            if any(
                any(_cell_matches_alias(cell_text, alias) for alias in aliases)
                for cell_text in row_values
            ):
                row_hits += 1

        if row_hits > best_hits:
            best_hits = row_hits
            best_row = row_idx

    if best_hits >= min_hits:
        return best_row
    return None


def build_column_map(
    df: pd.DataFrame,
    aliases_by_field: Dict[str, Iterable[str]],
    fallback_columns: Dict[str, int],
    search_rows: int = 10,
    min_hits: int = 2,
) -> Tuple[Dict[str, int], int]:
    """
    Build semantic field -> dataframe column index map.

    Returns:
        (column_map, data_start_row)
    """
    header_row_idx = detect_header_row(df, aliases_by_field, search_rows=search_rows, min_hits=min_hits)
    column_map = dict(fallback_columns)

    if header_row_idx is None:
        return column_map, 0

    header_values = [_normalize_text(value) for value in df.iloc[header_row_idx].tolist()]
    normalized_aliases = {
        field: [_normalize_text(alias) for alias in aliases]
        for field, aliases in aliases_by_field.items()
    }

    for field, aliases in normalized_aliases.items():
        for col_idx, cell_text in enumerate(header_values):
            if any(_cell_matches_alias(cell_text, alias) for alias in aliases):
                column_map[field] = col_idx
                break

    return column_map, header_row_idx + 1

# ============================================================================
# Format-Based Detection (Pattern Matching)
# ============================================================================

def _matches_route_code_pattern(value: str) -> bool:
    """Check if value matches route code format: 2-3 alpha chars + 2-5 digits."""
    if not value:
        return False
    # Remove whitespace
    value = str(value).strip()
    # Pattern: CX001, CX97, AX1234, RX12345, etc.
    return bool(re.match(r"^[A-Z]{2,3}\d{2,5}$", value))


def _matches_driver_name_pattern(value: str) -> bool:
    """Check if value looks like a proper name: First Last format, all alpha."""
    if not value:
        return False
    value = str(value).strip()
    
    # Must have at least one space (first and last name separated)
    if " " not in value:
        return False
    
    # Split by spaces
    parts = value.split()
    
    # Typically 2-3 parts (first middle/middle last, or first last, etc.)
    # Don't match if more than 3 parts (likely company names with many words)
    if len(parts) > 3:
        return False
    
    # Each part must:
    # - Start with uppercase letter
    # - Be purely alphabetic (no numbers, no hyphens in middle)
    for part in parts:
        if not part:
            continue
        # Allow hyphens in names like "Van Dyke" but not "ABC-Corp"
        # Just check that it's mostly letters
        if not (part[0].isupper() and all(c.isalpha() or c == '-' for c in part)):
            return False
    
    return True


def _matches_numeric_pattern(value: str) -> bool:
    """Check if value is a number (duration, counts, etc.)."""
    if not value:
        return False
    try:
        float(str(value).strip())
        return True
    except (ValueError, TypeError):
        return False


def _score_column_for_field(
    df: pd.DataFrame,
    col_idx: int,
    field_name: str,
    sample_size: int = 5,
) -> float:
    """Score how well a column matches a field type based on data patterns."""
    if col_idx < 0 or col_idx >= len(df.columns):
        return 0.0
    
    sample_rows = min(sample_size, len(df) - 1)
    if sample_rows <= 0:
        return 0.0
    
    matches = 0
    
    # Sample data rows (skip first row which might be header)
    for row_idx in range(1, 1 + sample_rows):
        if row_idx >= len(df):
            break
        
        value = df.iloc[row_idx, col_idx]
        if pd.isna(value) or value == "":
            continue
        
        cell_str = str(value).strip()
        
        if field_name == "route_code" and _matches_route_code_pattern(cell_str):
            matches += 1
        elif field_name == "driver_name" and _matches_driver_name_pattern(cell_str):
            matches += 1
        elif field_name in ["route_duration", "num_zones", "num_packages"] and _matches_numeric_pattern(cell_str):
            matches += 1
    
    return matches / sample_rows if sample_rows > 0 else 0.0


def detect_columns_by_format(
    df: pd.DataFrame,
    column_map: Dict[str, int],
    fields_to_detect: List[str],
) -> Dict[str, int]:
    """
    Try to detect columns by data format/pattern matching.
    Only updates column_map for fields that have strong matches.
    """
    if df.empty or len(df) < 2:
        return column_map
    
    updated_map = dict(column_map)
    
    # For each field we want to detect
    for field in fields_to_detect:
        best_col_idx = None
        best_score = 0.5  # Require at least 50% confidence
        
        # Try each column
        for col_idx in range(len(df.columns)):
            score = _score_column_for_field(df, col_idx, field)
            
            if score > best_score:
                best_score = score
                best_col_idx = col_idx
        
        # Update map if we found a strong match
        if best_col_idx is not None and best_score > 0.5:
            updated_map[field] = best_col_idx
    
    return updated_map