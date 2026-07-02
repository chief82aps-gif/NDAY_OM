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
            pages_to_skip = set()

            for page_idx, page in enumerate(pdf.pages):
                if page_idx in pages_to_skip:
                    continue

                try:
                    text = page.extract_text()
                    if not text:
                        continue

                    if "route sheet" in text.lower() and len(text) < 500:
                        continue

                    staging_match = re.search(r'(STG\.[A-Z0-9\.]+)', text, re.IGNORECASE)
                    staging_location = staging_match.group(1).strip() if staging_match else None

                    route_code = None
                    route_code_match = re.search(r'\b([CA]X\d+)\b', text, re.IGNORECASE)
                    if route_code_match:
                        route_code = normalize_route_code(route_code_match.group(1))
                    else:
                        route_code_match = re.search(r'([CA]X\d+)', text, re.IGNORECASE)
                        if route_code_match:
                            route_code = normalize_route_code(route_code_match.group(1))
                        else:
                            route_code_match = re.search(r'(?:^|\n)([CA]X\d+)', text, re.IGNORECASE | re.MULTILINE)
                            if route_code_match:
                                route_code = normalize_route_code(route_code_match.group(1))

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

                    wave_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', text, re.IGNORECASE)
                    wave_time = wave_match.group(1).strip() if wave_match else None

                    dsp = "NDAY"

                    if not route_code:
                        continue

                    bags = []
                    overflow = []
                    tables = page.extract_tables()
                    total_packages = 0

                    for table in tables:
                        if not table or len(table) < 2:
                            continue

                        header_row = [str(cell).strip().lower() if cell else "" for cell in table[0]]

                        if len(header_row) >= 3 and any("bag" in h for h in header_row):
                            for row in table[1:]:
                                if not row or len(row) < 3:
                                    continue
                                try:
                                    sort_zone = str(row[0]).strip() if row[0] else ""
                                    bag_id = str(row[1]).strip() if row[1] else ""
                                    pkgs_cell = str(row[2]).strip() if row[2] else "0"

                                    if not sort_zone or not bag_id or "sort" in sort_zone.lower() or "zone" in sort_zone.lower():
                                        continue

                                    pkgs = int(pkgs_cell) if pkgs_cell.isdigit() else 0
                                    if pkgs == 0:
                                        continue

                                    bag_parts = bag_id.split()
                                    if len(bag_parts) >= 2:
                                        color_name = bag_parts[0]
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
                                except (ValueError, IndexError):
                                    continue

                        elif len(header_row) == 2:
                            has_zone = any("zone" in h for h in header_row)
                            has_pkgs = any("pkg" in h or "qty" in h for h in header_row)

                            if has_zone and has_pkgs:
                                for row in table[1:]:
                                    if not row or len(row) < 2:
                                        continue
                                    try:
                                        zone = str(row[0]).strip() if row[0] else ""
                                        pkgs_cell = str(row[1]).strip() if row[1] else "0"

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
                                    except (ValueError, IndexError):
                                        continue

                    if len(bags) == 0:
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

                        bag_no_zone_pattern = r'\d+\s+([A-Z][a-z]+)\s+(\d+)\s+(\d+)(?:\s|$)'
                        for m in re.finditer(bag_no_zone_pattern, text):
                            color_name = m.group(1)
                            bag_number = m.group(2)
                            qty = int(m.group(3))

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
                                    sort_zone="",
                                    color_code=color_code,
                                    package_count=qty,
                                ))
                                total_packages += qty

                    if len(overflow) == 0:
                        ov_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+(\d+)(?:\s|$)'
                        for m in re.finditer(ov_pattern, text):
                            zone = m.group(1)
                            qty = int(m.group(2))

                            if not any(b.sort_zone == zone for b in bags) and not any(o.sort_zone == zone for o in overflow):
                                overflow.append(RouteSheetOverflow(
                                    sort_zone=zone,
                                    bag_code=zone,
                                    package_count=qty,
                                ))
                                total_packages += qty

                    if len(bags) == 0 and len(overflow) == 0 and route_code:
                        if page_idx + 1 < len(pdf.pages):
                            next_page = pdf.pages[page_idx + 1]
                            next_text = next_page.extract_text()

                            if next_text:
                                next_route_match = re.search(r'\b([CA]X\d+)\b', next_text, re.IGNORECASE)

                                if not next_route_match:
                                    next_bags = []
                                    next_overflow = []
                                    next_total_packages = 0

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

                                    ov_pattern = r'\d+\s+([ABEG]-[\d\.]+[A-Z])\s+(\d+)(?:\s|$)'
                                    for m in re.finditer(ov_pattern, next_text):
                                        zone = m.group(1)
                                        qty = int(m.group(2))

                                        if not any(b.sort_zone == zone for b in next_bags) and not any(o.sort_zone == zone for o in next_overflow):
                                            next_overflow.append(RouteSheetOverflow(
                                                sort_zone=zone,
                                                bag_code=zone,
                                                package_count=qty,
                                            ))
                                            next_total_packages += qty

                                    if len(next_bags) > 0 or len(next_overflow) > 0:
                                        bags = next_bags
                                        overflow = next_overflow
                                        total_packages = next_total_packages
                                        pages_to_skip.add(page_idx + 1)
                                        errors.append(f"Route {route_code}: Found header on page {page_idx+1}, bag data on page {page_idx+2}")

                    if len(bags) == 0 and len(overflow) == 0:
                        errors.append(f"Route {route_code} (Page {page_idx+1}): No bag or overflow data extracted. Route will be tracked but load details are missing.")

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
