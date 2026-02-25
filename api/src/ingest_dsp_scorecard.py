"""DSP scorecard PDF ingest parser."""
from typing import Dict, List, Tuple
import pdfplumber


def parse_dsp_scorecard_pdf(file_path: str) -> Tuple[Dict, List[Dict], List[str]]:
    errors: List[str] = []
    summary: Dict = {}
    driver_rows: List[Dict] = []

    with pdfplumber.open(file_path) as pdf:
        if not pdf.pages:
            return {}, [], ["DSP scorecard PDF has no pages."]

        summary_text = pdf.pages[0].extract_text() or ""
        summary["raw_text"] = summary_text

        if len(pdf.pages) > 1:
            driver_text = pdf.pages[1].extract_text() or ""
            summary["driver_section_raw_text"] = driver_text

    return summary, driver_rows, errors
