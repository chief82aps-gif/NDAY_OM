"""Weekly incentive invoice PDF ingest parser."""
from typing import Dict, List, Tuple
import re
import pdfplumber


def parse_weekly_incentive_pdf(file_path: str) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    data: Dict = {}

    with pdfplumber.open(file_path) as pdf:
        if not pdf.pages:
            return {}, ["Weekly incentive PDF has no pages."]
        text = pdf.pages[0].extract_text() or ""

    invoice_number_match = re.search(r"INV-[A-Z0-9\-]+", text)
    if invoice_number_match:
        data["invoice_number"] = invoice_number_match.group(0)
    else:
        errors.append("Invoice number not found.")

    data["raw_text"] = text
    return data, errors
