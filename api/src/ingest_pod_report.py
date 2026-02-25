"""POD report PDF ingest parser (Photo on Delivery acceptance)."""
from typing import Dict, List, Tuple
import pdfplumber


def parse_pod_report_pdf(file_path: str) -> Tuple[Dict, List[Dict], List[str]]:
    errors: List[str] = []
    summary: Dict = {}
    driver_rows: List[Dict] = []

    with pdfplumber.open(file_path) as pdf:
        if not pdf.pages:
            return {}, [], ["POD report has no pages."]

        # Basic text capture for future rule mapping
        summary_text = pdf.pages[0].extract_text() or ""
        summary["raw_text"] = summary_text

        if len(pdf.pages) > 1:
            driver_text = pdf.pages[1].extract_text() or ""
            summary["driver_section_raw_text"] = driver_text

    return summary, driver_rows, errors
