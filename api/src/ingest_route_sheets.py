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
            pages_to_skip = set()  # Track pages already processed as continuations
            
            for page_idx, page in enumerate(pdf.pages):
                # Skip pages that were already processed as continuations
                if page_idx in pages_to_skip:
                    continue
                    
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
                    
                    # Extract route code (format: CX/AX followed by numbers)
                    # Try multiple patterns to handle various PDF text extraction quirks
                    route_code = None
                    # Pattern 1: Word boundary pattern (standard) - CX or AX followed by digits
                    route_code_match = re.search(r'\b([CA]X\d+)\b', text, re.IGNORECASE)
                    if route_code_match:
                        route_code = normalize_route_code(route_code_match.group(1))
                    else:
                        # Pattern 2: Without word boundary (handles cases where digits touch special chars)
                        route_code_match = re.search(r'([CA]X\d+)', text, re.IGNORECASE)
                        if route_code_match:
                            route_code = normalize_route_code(route_code_match.group(1))
                        else:
                            # Pattern 3: Look for CX/AX at start of line
                            route_code_match = re.search(r'(?:^|\n)([CA]X\d+)', text, re.IGNORECASE | re.MULTILINE)
                            if route_code_match:
                                route_code = normalize_route_code(route_code_match.group(1))
                    
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
                    
                    # Process tables to extract bags and overflow
                    for table in tables:
                        if not table or len(table) < 2:
                            continue
                        
                        # Check if this is the bags table (has "Bag" or "Sort Zone" in header)
                        header_row = [str(cell).strip().lower() if cell else "" for cell in table[0]]
                        
                        # Look for bags table columns: Sort Zone | Bag | Pkgs
                        # This table has 3+ columns and includes "bag" in headers
                        if len(header_row) >= 3 and any("bag" in h for h in header_row):
                            # This is the bags table
                            for row in table[1:]:  # Skip header
                                if not row or len(row) < 3:
                                    continue
                                
                                try:
                                    sort_zone = str(row[0]).strip() if row[0] else ""
                                    bag_id = str(row[1]).strip() if row[1] else ""
                                    pkgs_cell = str(row[2]).strip() if row[2] else "0"
                                    
                                    # Skip empty rows or header repeats
                                    if not sort_zone or not bag_id or "sort" in sort_zone.lower() or "zone" in sort_zone.lower():
                                        continue
                                    
                                    # Extract package count
                                    pkgs = int(pkgs_cell) if pkgs_cell.isdigit() else 0
                                    if pkgs == 0:
                                        continue
                                    
                                    # Extract color from bag_id (format: "Orange 546")
                                    bag_parts = bag_id.split()
                                    if len(bag_parts) >= 2:
                                        color_name = bag_parts[0]
                                        # Map color to abbreviation
                                        color_map = {
                                            "navy": "NAV", "black": "BLK", "yellow": "YEL", "orange": "ORG",
                                            "green": "GRN", "white": "WHI", "red": "RED", "blue": "BLU",
                                            "gray": "GRY", "grey": "GRY", "pink": "PNK", "purple": "PUR"
                                        }
                                        color_code = color_map.get(color_name.lower(), color_name[:3].upper())
                                    else:
                                        color_code = ""
                                    
                                    bags.append(RouteSheetBag(
                                        bag_id=bag_id,
                                        sort_zone=sort_zone,
                                        color_code=color_code,
                                        package_count=pkgs,
                                    ))
                                    total_packages += pkgs
                                except (ValueError, IndexError) as e:
                                    continue
                        
                        # Look for overflow table: Zone | Pkgs (2 columns)
                        # This table typically has "overflow" in title or just "Zone" and "Pkgs" headers
                        elif len(header_row) == 2:
                            has_zone = any("zone" in h for h in header_row)
                            has_pkgs = any("pkg" in h or "qty" in h for h in header_row)
                            
                            if has_zone and has_pkgs:
                                # This is likely the overflow table
                                for row in table[1:]:  # Skip header
                                    if not row or len(row) < 2:
                                        continue
                                    
                                    try:
                                        zone = str(row[0]).strip() if row[0] else ""
                                        pkgs_cell = str(row[1]).strip() if row[1] else "0"
                                        
                                        # Skip empty rows or header repeats
                                        if not zone or "zone" in zone.lower() or "sort" in zone.lower():
                                            continue
                                        
                                        pkgs = int(pkgs_cell) if pkgs_cell.isdigit() else 0
                                        if pkgs == 0:
                                            continue
                                        
                                        overflow.append(RouteSheetOverflow(
                                            sort_zone=zone,
                                            bag_code=zone,
                                            package_count=pkgs,
                                        ))
                                        total_packages += pkgs
                                    except (ValueError, IndexError) as e:
                                        continue
                    
                    # Fallback: Use regex-based extraction from full page text if tables didn't work
                    if len(bags) == 0:
                        # Pattern for bags WITH sort zones: "1 E-11.3C Orange 546 10", "1 G-22.2G Black 225 7", "1 B-5.1E Orange 1224 12", "1 A-5.1G Orange 599 15"
                        # Format: [optional number] [Zone] [Color] [BagNumber] [Qty]
                        bag_with_zone_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+([A-Za-z]+)\s+(\d+)\s+(\d+)'
                        for m in re.finditer(bag_with_zone_pattern, text):
                            sort_zone = m.group(1)
                            color_name = m.group(2)
                            bag_number = m.group(3)
                            qty = int(m.group(4))
                            
                            color_map = {
                                "Navy": "NAV", "Black": "BLK", "Yellow": "YEL", "Orange": "ORG",
                                "Green": "GRN", "White": "WHI", "Red": "RED", "Blue": "BLU",
                                "Gray": "GRY", "Grey": "GRY", "Pink": "PNK", "Purple": "PUR"
                            }
                            color_code = color_map.get(color_name, color_name[:3].upper())
                            bag_id = f"{color_name} {bag_number}"
                            
                            if not any(b.bag_id == bag_id for b in bags):
                                bags.append(RouteSheetBag(
                                    bag_id=bag_id,
                                    sort_zone=sort_zone,
                                    color_code=color_code,
                                    package_count=qty,
                                ))
                                total_packages += qty
                        
                        # Pattern for bags WITHOUT sort zones: "2 Black 071 10"
                        # Format: [number] [Color] [BagNumber] [Qty]
                        # Look for lines that DON'T have the E- zone prefix
                        bag_no_zone_pattern = r'\d+\s+([A-Z][a-z]+)\s+(\d+)\s+(\d+)(?:\s|$)'
                        for m in re.finditer(bag_no_zone_pattern, text):
                            color_name = m.group(1)
                            bag_number = m.group(2)
                            qty = int(m.group(3))
                            
                            # Skip if this looks like it's part of a zone pattern
                            if color_name.startswith('E'):
                                continue
                            
                            color_map = {
                                "Navy": "NAV", "Black": "BLK", "Yellow": "YEL", "Orange": "ORG",
                                "Green": "GRN", "White": "WHI", "Red": "RED", "Blue": "BLU",
                                "Gray": "GRY", "Grey": "GRY", "Pink": "PNK", "Purple": "PUR"
                            }
                            color_code = color_map.get(color_name, color_name[:3].upper())
                            bag_id = f"{color_name} {bag_number}"
                            
                            if not any(b.bag_id == bag_id for b in bags):
                                bags.append(RouteSheetBag(
                                    bag_id=bag_id,
                                    sort_zone="",  # No zone for these bags
                                    color_code=color_code,
                                    package_count=qty,
                                ))
                                total_packages += qty
                    
                    # Fallback: Extract overflow from text if not found in tables
                    if len(overflow) == 0:
                        # OV zones pattern: "1 E-11.3C 1", "1 G-22.5Z 1", "1 B-5.2U 1", "1 A-5.3Z 4" (number, zone, quantity)
                        # Must ensure it's NOT followed by a color word (which would indicate a bag line)
                        ov_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+(\d+)(?:\s|$)'
                        for m in re.finditer(ov_pattern, text):
                            zone = m.group(1)
                            qty = int(m.group(2))
                            
                            # Check if this zone is already captured as a bag's sort_zone
                            # Only add to overflow if it's a standalone zone entry
                            if not any(b.sort_zone == zone for b in bags) and not any(o.sort_zone == zone for o in overflow):
                                overflow.append(RouteSheetOverflow(
                                    sort_zone=zone,
                                    bag_code=zone,
                                    package_count=qty,
                                ))
                                total_packages += qty
                    
                    # If no bags/overflow found, check the next page for continuation
                    if len(bags) == 0 and len(overflow) == 0 and route_code:
                        # Look ahead to next page
                        if page_idx + 1 < len(pdf.pages):
                            next_page = pdf.pages[page_idx + 1]
                            next_text = next_page.extract_text()
                            
                            if next_text:
                                # Check if next page has a route code header
                                next_route_match = re.search(r'\b([CA]X\d+)\b', next_text, re.IGNORECASE)
                                
                                # If next page has NO route code, it's likely a continuation of current route
                                if not next_route_match:
                                    # Extract bags from next page using same patterns
                                    next_bags = []
                                    next_overflow = []
                                    next_total_packages = 0
                                    
                                    # Try regex-based extraction on next page
                                    # Pattern for bags WITH sort zones (handles A-, B-, E-, G- zones)
                                    bag_with_zone_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+([A-Za-z]+)\s+(\d+)\s+(\d+)'
                                    for m in re.finditer(bag_with_zone_pattern, next_text):
                                        sort_zone = m.group(1)
                                        color_name = m.group(2)
                                        bag_number = m.group(3)
                                        qty = int(m.group(4))
                                        
                                        color_map = {
                                            "Navy": "NAV", "Black": "BLK", "Yellow": "YEL", "Orange": "ORG",
                                            "Green": "GRN", "White": "WHI", "Red": "RED", "Blue": "BLU",
                                            "Gray": "GRY", "Grey": "GRY", "Pink": "PNK", "Purple": "PUR"
                                        }
                                        color_code = color_map.get(color_name, color_name[:3].upper())
                                        bag_id = f"{color_name} {bag_number}"
                                        
                                        if not any(b.bag_id == bag_id for b in next_bags):
                                            next_bags.append(RouteSheetBag(
                                                bag_id=bag_id,
                                                sort_zone=sort_zone,
                                                color_code=color_code,
                                                package_count=qty,
                                            ))
                                            next_total_packages += qty
                                    
                                    # Pattern for overflow zones on next page (handles A-, B-, E-, G- zones)
                                    ov_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+(\d+)(?:\s|$)'
                                    for m in re.finditer(ov_pattern, next_text):
                                        zone = m.group(1)
                                        qty = int(m.group(2))
                                        
                                        # Only add if not already in bags
                                        if not any(b.sort_zone == zone for b in next_bags) and not any(o.sort_zone == zone for o in next_overflow):
                                            next_overflow.append(RouteSheetOverflow(
                                                sort_zone=zone,
                                                bag_code=zone,
                                                package_count=qty,
                                            ))
                                            next_total_packages += qty
                                    
                                    # If we found bags/overflow on next page, use them for current route
                                    if len(next_bags) > 0 or len(next_overflow) > 0:
                                        bags = next_bags
                                        overflow = next_overflow
                                        total_packages = next_total_packages
                                        pages_to_skip.add(page_idx + 1)  # Skip next page in main loop
                                        errors.append(f"Route {route_code}: Found header on page {page_idx+1}, bag data on page {page_idx+2}")
                    
                    # Warn if still no bags or overflow were extracted (after look-ahead)
                    if len(bags) == 0 and len(overflow) == 0:
                        errors.append(f"Route {route_code} (Page {page_idx+1}): No bag or overflow data extracted. Route will be tracked but load details are missing.")
                    
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
