"""Route Sheet PDF ingest parser."""
import pdfplumber
import re
from typing import List, Tuple
from api.src.models import RouteSheet, RouteSheetBag, RouteSheetOverflow
from api.src.normalization import normalize_route_code, normalize_service_type


def parse_route_sheet_pdf(file_path: str) -> Tuple[List[RouteSheet], List[str]]:
    """Parse Route Sheet PDF and extract route records with bags and overflow."""
    errors = []
    records = []
    
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text()
                    if not text:
                        continue
                    
                    # Skip header/footer pages (look for "Route Sheets" or "Header" patterns)
                    if "route sheet" in text.lower() and len(text) < 500:
                        continue  # Skip cover/header page
                    
                    # Extract route metadata from page text using actual PDF format
                    # Format: STG.Q12.1\nCX105 NDAY • 4WD P31 Delivery Truck\nDLV3 • TUE, FEB 17, 2026 • CYCLE_1 • 10:20 AM
                    
                    # Extract staging location (format: STG.Qxx.x)
                    staging_match = re.search(r'(STG\.[A-Z0-9\.]+)', text, re.IGNORECASE)
                    staging_location = staging_match.group(1).strip() if staging_match else None
                    
                    # Extract route code (format: CXxxx or similar)
                    route_code_match = re.search(r'\b(CX\d{3}|CX\d{2})\b', text)
                    route_code = normalize_route_code(route_code_match.group(1)) if route_code_match else None
                    
                    # Extract service type: After route code, after NDAY/DSP, before newline or bullet
                    # Format: "CX105 NDAY • 4WD P31 Delivery Truck"
                    if route_code:
                        service_match = re.search(
                            route_code.replace('CX', 'CX') + r'\s+NDAY\s*(?:•|–)?\s*([^•–\n]+?)(?:\n|$)',
                            text, re.IGNORECASE
                        )
                        if service_match:
                            raw_service = service_match.group(1).strip()
                            service_type = normalize_service_type(raw_service)
                        else:
                            service_type = None
                    else:
                        service_type = None
                    
                    # Extract wave time (format: 10:20 AM)
                    wave_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', text, re.IGNORECASE)
                    wave_time = wave_match.group(1).strip() if wave_match else None
                    
                    # DSP is usually NDAY
                    dsp = "NDAY"
                    
                    if not route_code:
                        continue  # Skip pages without route codes
                    
                    # Try to extract tables for structured data
                    bags = []
                    overflow = []
                    tables = page.extract_tables()
                    total_packages = 0
                    
                    # Use regex-based extraction from full page text
                    # Pattern for bags with colors: "[BagCode] [ColorName] [BagID] [Qty]"
                    # Example: "1 B-7.3B Navy 4564 3" where B-7.3B is bag, Navy is color, 4564 is ID, 3 is qty
                    color_map = {
                        "NAVY": "NAV", "BLACK": "BLK", "YELLOW": "YEL", "ORANGE": "ORG",
                        "GREEN": "GRN", "WHITE": "WHI", "RED": "RED", "BLUE": "BLU",
                        "GRAY": "GRY", "GREY": "GRY", "PINK": "PNK", "PURPLE": "PUR"
                    }
                    
                    # Extract bags from text pattern: "B-7.3B Navy 4564 4" format
                    # Pattern: BagCode ColorName 4-digit-ID Quantity
                    bag_pattern = r'\b([BE]-[\d\.]+[A-Z])\s+([A-Za-z]+)\s+(\d{4})\s+(\d+)'
                    for m in re.finditer(bag_pattern, text):
                        bag_id = m.group(1)
                        color_name = m.group(2).upper()
                        qty = int(m.group(4))
                        color_code = color_map.get(color_name, color_name[:3])
                        
                        if not any(b.bag_id == bag_id for b in bags):
                            bags.append(RouteSheetBag(
                                bag_id=bag_id,
                                sort_zone="",
                                color_code=color_code,
                                package_count=qty,
                            ))
                            total_packages += qty
                    
                    # Extract overflow from text (right-side column)
                    # OV zones are alphanumeric like "A-16.1T", "A-29.7T" with quantity
                    ov_pattern = r'([A-Z]-[\d\.]+[A-Z])\s+(\d+)'
                    for m in re.finditer(ov_pattern, text, re.MULTILINE):
                        zone = m.group(1)  # e.g., "A-16.1T"
                        qty = int(m.group(2))
                        # Only add if not already in bags
                        if not any(b.bag_id == zone for b in bags) and not any(o.sort_zone == zone for o in overflow):
                            overflow.append(RouteSheetOverflow(
                                sort_zone=zone,
                                bag_code=zone,  # Use zone as bag_code for overflow
                                package_count=qty,
                            ))
                            total_packages += qty
                    
                    # Skip empty routes
                    if len(bags) == 0 and len(overflow) == 0:
                        continue
                    
                    # Build route sheet record
                    record = RouteSheet(
                        route_code=route_code,
                        staging_location=staging_location or "",
                        service_type=service_type or "",
                        wave_time=wave_time or "",
                        dsp=dsp,
                        bags=bags,
                        overflow=overflow,
                        total_packages=total_packages,
                        total_bags=len(bags),
                    )
                    records.append(record)
                
                except Exception as e:
                    errors.append(f"PDF page {page_idx+1}: Error parsing page - {str(e)}")
                    continue
    
    except Exception as e:
        errors.append(f"Failed to read Route Sheet PDF: {str(e)}")
    
    return records, errors
