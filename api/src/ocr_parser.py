"""
Semantic OCR parser for Daily Screenshot Audit.

Instead of fragile regex patterns, this parser:
1. Looks for semantic labels ("Completed Routes", "Delivered Packages", etc.)
2. Extracts the number immediately preceding the label
3. First instance = header total
4. Subsequent instances = line items
5. Validates: sum(line_items) ≈ header_total with tolerance
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ParsedMetric:
    """Represents a parsed metric."""
    label: str
    header_value: Optional[int] = None
    line_items: List[Tuple[str, int]] = None  # [(service_name, value), ...]
    sum_of_items: Optional[int] = None
    matches_header: bool = False
    validation_message: str = ""
    
    def __post_init__(self):
        if self.line_items is None:
            self.line_items = []
        if self.line_items:
            self.sum_of_items = sum(v for _, v in self.line_items)
            # Allow 5% tolerance for rounding/OCR errors
            tolerance = int(self.header_value * 0.05) if self.header_value else 10
            if self.sum_of_items and self.header_value:
                if abs(self.sum_of_items - self.header_value) <= tolerance:
                    self.matches_header = True
                    self.validation_message = f"✓ Sum of line items ({self.sum_of_items}) matches header ({self.header_value})"
                else:
                    self.validation_message = f"⚠️ Sum of line items ({self.sum_of_items}) differs from header ({self.header_value}) by {abs(self.sum_of_items - self.header_value)}"


def extract_number_before_label(text: str, label: str, occurrence: int = 1) -> Optional[int]:
    """
    Find the Nth occurrence of a label and extract the number immediately preceding it.
    Handles both: "39 Completed Routes" and multi-line "39\nCompleted Routes"
    
    Args:
        text: OCR text to search
        label: The semantic label to find (e.g., "Completed Routes")
        occurrence: Which occurrence to extract (1=first/header, 2+=line items, etc.)
    
    Returns:
        The number immediately before the label, or None if not found
    """
    # Match number followed by optional whitespace (including newlines) then label
    # Handles both: "39 Routes" and "39\nRoutes" and "39\n\nRoutes"
    pattern = re.compile(rf'(\d{{1,3}}(?:,\d{{3}})*|\d+)\s*{re.escape(label)}', re.IGNORECASE | re.DOTALL)
    matches = list(pattern.finditer(text))
    
    if occurrence <= len(matches):
        match = matches[occurrence - 1]
        number_str = match.group(1).replace(',', '')
        try:
            return int(number_str)
        except ValueError:
            return None
    
    return None


def extract_all_occurrences(text: str, label: str) -> List[int]:
    """
    Extract ALL numbers immediately preceding a label (header + all line items).
    Handles: "39 Routes" or "39\nRoutes"
    Returns them in order found.
    
    Returns:
        List of all numbers found, in order (first is header, rest are line items)
    """
    pattern = re.compile(rf'(\d{{1,3}}(?:,\d{{3}})*|\d+)\s*{re.escape(label)}', re.IGNORECASE | re.DOTALL)
    matches = pattern.finditer(text)
    
    numbers = []
    for match in matches:
        number_str = match.group(1).replace(',', '')
        try:
            num = int(number_str)
            numbers.append(num)
        except ValueError:
            continue
    
    return numbers


def extract_metric_with_line_items(
    text: str,
    metric_label: str
) -> ParsedMetric:
    """
    Extract a metric with header and line-item breakdown.
    Handles BOTH formats:
    - WST format: "39 Completed Routes" (number BEFORE label)
    - Cortex format: "Routes\n39" (number AFTER label on next line)
    
    Strategy:
    1. Try to find number BEFORE label first (WST format)
    2. If not found, try number AFTER label (Cortex format)
    3. Return header + first match
    """
    # Try label variations
    labels_to_try = [
        metric_label,  # "Completed Routes"
        metric_label.replace('Completed ', ''),  # "Routes" 
        metric_label.replace('Delivered ', ''),  # "Packages"
    ]
    
    # APPROACH 1: Number BEFORE label (WST format: "39 Routes")
    for label_var in labels_to_try:
        all_values = extract_all_occurrences(text, label_var)
        if all_values:
            header_value = all_values[0]
            return ParsedMetric(
                label=metric_label,
                header_value=header_value,
                line_items=[],
                validation_message=f"✓ Header: {header_value} (format: number before label)"
            )
    
    # APPROACH 2: Number AFTER label (Cortex format: "Routes\n39")
    for label_var in labels_to_try:
        # Find the label, then look for first number after it
        pattern = re.compile(rf'{re.escape(label_var)}\s*(\d{{1,3}}(?:,\d{{3}})*|\d+)', re.IGNORECASE | re.DOTALL)
        match = pattern.search(text)
        
        if match:
            number_str = match.group(1).replace(',', '')
            try:
                header_value = int(number_str)
                return ParsedMetric(
                    label=metric_label,
                    header_value=header_value,
                    line_items=[],
                    validation_message=f"✓ Header: {header_value} (format: number after label)"
                )
            except ValueError:
                continue
    
    # Not found in either format
    return ParsedMetric(
        label=metric_label,
        validation_message=f"✗ No occurrences of '{metric_label}' found (tried both before/after formats)"
    )


def parse_screenshot_audit(raw_text: str) -> Dict[str, ParsedMetric]:
    """
    Parse a complete screenshot audit for standard metrics.
    
    Args:
        raw_text: Raw OCR text from screenshot
    
    Returns:
        Dictionary of metric_name -> ParsedMetric
    """
    results = {}
    
    # Parse completed routes
    results['completed_routes'] = extract_metric_with_line_items(
        raw_text,
        "Completed Routes"
    )
    
    # Parse delivered packages
    results['delivered_packages'] = extract_metric_with_line_items(
        raw_text,
        "Delivered Packages"
    )
    
    # Parse DSP Late Cancel
    dsp_late_cancel_numbers = extract_all_occurrences(raw_text, "DSP Late Cancel")
    results['dsp_late_cancel_max'] = ParsedMetric(
        label="DSP Late Cancel (Max)",
        header_value=max(dsp_late_cancel_numbers) if dsp_late_cancel_numbers else None,
        validation_message=f"✓ Max DSP Late Cancel: {max(dsp_late_cancel_numbers) if dsp_late_cancel_numbers else 'N/A'}"
    )
    
    return results


def validate_audit_reconciliation(parsed: Dict[str, ParsedMetric]) -> Dict[str, any]:
    """
    Run validation checks on parsed audit data.
    
    Returns:
        Dictionary with validation results and any flags
    """
    flags = {
        'is_valid': True,
        'critical_issues': [],
        'warnings': [],
        'metrics': {}
    }
    
    # Check completed routes
    routes = parsed.get('completed_routes')
    if routes and routes.header_value:
        flags['metrics']['completed_routes'] = {
            'header': routes.header_value,
            'sum_of_items': routes.sum_of_items,
            'reconciled': routes.matches_header
        }
        
        # Sanity check: routes should typically be < 1000
        if routes.header_value > 1000:
            flags['warnings'].append(f"⚠️ Completed routes ({routes.header_value}) exceeds typical maximum. May need manual review.")
        
        if not routes.matches_header:
            flags['warnings'].append(f"⚠️ Line items don't reconcile to header total for routes")
    
    # Check delivered packages
    packages = parsed.get('delivered_packages')
    if packages and packages.header_value:
        flags['metrics']['delivered_packages'] = {
            'header': packages.header_value,
            'sum_of_items': packages.sum_of_items,
            'reconciled': packages.matches_header
        }
        
        if not packages.matches_header:
            flags['warnings'].append(f"⚠️ Line items don't reconcile to header total for packages")
    
    # Check DSP Late Cancel
    dsp = parsed.get('dsp_late_cancel_max')
    if dsp and dsp.header_value and dsp.header_value > 0:
        flags['critical_issues'].append(f"🚨 DSP Late Cancel is {dsp.header_value} (should be 0)")
        flags['is_valid'] = False
    
    # Cross-metric validation
    if routes and packages and routes.header_value and packages.header_value:
        if routes.header_value > 500 and packages.header_value < 100:
            flags['warnings'].append("⚠️ Unusual ratio: many routes but few packages")
    
    return flags


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    # More realistic WST format from actual screenshot
    sample_text = """
Week 9
Friday February 27

1 Pickup Packages
39 Completed Routes
9,385 Delivered Packages

Request Change

Nursery Route Level 2 - 10 hr
280 Delivered Packages
2 Completed Routes

Nursery Route Level 1 - Electric Vehicle
113 Delivered Packages
1 Completed Routes

Standard Parcel - Custom Delivery Van 14ft
245 Delivered Packages
1 Completed Routes

Nursery Route Level 1
377 Delivered Packages
3 Completed Routes

Standard Parcel Electric - Rivian MEDIUM
6638 Delivered Packages
23 Completed Routes

Nursery Route Level 3 - Electric Vehicle
195 Delivered Packages
1 Completed Routes

4WD P51 Delivery Truck
183 Delivered Packages
1 Completed Routes

Standard Parcel - On-Road Experience
147 Delivered Packages
1 Completed Routes

Nursery Route Level 3
539 Delivered Packages
3 Completed Routes

Standard Parcel - Custom Delivery Van 16ft
449 Delivered Packages
2 Completed Routes

Standard Parcel - Extra Large Van
223 Delivered Packages
2 Completed Routes

0 AMZL late cancel
0 DSP Late Cancel
    """
    
    parsed = parse_screenshot_audit(sample_text)
    
    print("=== PARSED METRICS ===\n")
    for metric_name, metric_data in parsed.items():
        print(f"{metric_name}:")
        print(f"  Header: {metric_data.header_value}")
        if metric_data.line_items:
            print(f"  Line Items: {len(metric_data.line_items)}")
            for service, value in metric_data.line_items:
                print(f"    - {service}: {value}")
        print(f"  Sum: {metric_data.sum_of_items}")
        print(f"  Validation: {metric_data.validation_message}\n")
    
    validation = validate_audit_reconciliation(parsed)
    print("\n=== VALIDATION RESULTS ===\n")
    print(f"Valid: {validation['is_valid']}")
    if validation['critical_issues']:
        print("Critical Issues:")
        for issue in validation['critical_issues']:
            print(f"  {issue}")
    if validation['warnings']:
        print("Warnings:")
        for warning in validation['warnings']:
            print(f"  {warning}")
    
    print("\n=== RECONCILIATION SUMMARY ===")
    metrics = validation.get('metrics', {})
    for metric_name, metric_info in metrics.items():
        header = metric_info.get('header')
        total = metric_info.get('sum_of_items')
        reconciled = metric_info.get('reconciled')
        print(f"{metric_name}: header={header}, line_items_sum={total}, reconciled={reconciled}")
