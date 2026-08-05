"""
Parser for Amazon's "Coaching Notifications Weekly Digest" email --
added 2026-08-01, rewritten 2026-08-05 to be header-driven instead of
position-driven. Confirmed real-world reason for the rewrite: Amazon's
own template changed shape between the original two observed weeks
(2026-28 through 2026-30: DA Name, Transporter ID, Station, Case Number,
Week of Occurrence, Supporting Information, Behavior, Coaching Tip -- 8
columns) and the 2026-08-05 email (DA Name, Transporter ID, Case Number,
Behavior, Incident Date, Status -- 6 columns, different order, a new
Status field, no Station/Coaching Tip). A fixed-position parser silently
scrambled every field except the first two when fed the newer format
(Case Number landing in the old `station` slot, Behavior landing in
`case_number`, etc.) -- caught before it ever ran against real data.
Matching by header name instead of position survives the next template
change too, rather than needing another manual fix.

No attachment; the data is an inline HTML table in the email body.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional


class _TableRowParser(HTMLParser):
    """Extracts every header (<th>) row and data (<td>) row inside
    <tbody> as lists of cell text values, in document order. Deliberately
    minimal -- this email's table has no nested tables/rowspans to worry
    about, confirmed against three real weeks' exports across two
    different template shapes."""

    def __init__(self):
        super().__init__()
        self.in_tbody = False
        self.in_cell = False
        self._current_cell_tag: Optional[str] = None
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_row_is_header = False
        self._current_cell_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self._current_row = []
            self._current_row_is_header = False
        elif tag in ("td", "th") and self.in_tbody:
            self.in_cell = True
            self._current_cell_tag = tag
            self._current_cell_parts = []
            if tag == "th":
                self._current_row_is_header = True
        elif tag == "br" and self.in_cell:
            self._current_cell_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "tbody":
            self.in_tbody = False
        elif tag == "tr" and self.in_tbody:
            if self._current_row:
                if self._current_row_is_header and not self.header:
                    self.header = self._current_row
                else:
                    self.rows.append(self._current_row)
        elif tag in ("td", "th") and self.in_tbody:
            self.in_cell = False
            self._current_row.append("".join(self._current_cell_parts).strip())

    def handle_data(self, data):
        if self.in_cell:
            self._current_cell_parts.append(data)


# Header text (lowercased) -> our field name. Covers both observed
# template shapes; add new aliases here if Amazon changes the wording
# again rather than touching the row-mapping logic below.
_HEADER_ALIASES = {
    "da name": "da_name",
    "transporter id": "transporter_id",
    "station": "station",
    "case number": "case_number",
    "week of occurrence": "occurrence_date_text",
    "incident date": "occurrence_date_text",
    "supporting information": "occurrence_info",
    "behavior": "behavior",
    "coaching tip": "coaching_tip",
    "status": "status",
}


def parse_coaching_notifications_html(html_content: str, source_email_id: str = "") -> tuple[dict, list]:
    """Returns (summary, records). summary: {"week": str, "row_count": int}.
    records: list of dicts matching CoachingNotification columns."""
    parser = _TableRowParser()
    parser.feed(html_content)

    week_match = re.search(r"Week (20\d{2}-\d{1,2})", html_content)
    if week_match:
        week = week_match.group(1)
    else:
        # Newer template has no "Week 20XX-NN" string anywhere -- fall
        # back to the covered date range from the intro paragraph
        # ("between 07/26/2026 and 08/01/2026") so notifications from
        # different weeks are still distinguishable.
        range_match = re.search(r"between\s+([\d/]+)\s+and\s+([\d/]+)", html_content, re.IGNORECASE)
        week = f"{range_match.group(1)}_to_{range_match.group(2)}" if range_match else ""

    col_index = {}
    for i, h in enumerate(parser.header):
        field = _HEADER_ALIASES.get(h.strip().lower())
        if field:
            col_index[field] = i

    records = []
    for row in parser.rows:
        def cell(field: str) -> Optional[str]:
            idx = col_index.get(field)
            if idx is None or idx >= len(row):
                return None
            return row[idx].strip() or None

        da_name = cell("da_name")
        case_number = cell("case_number")
        if not da_name or not case_number:
            continue  # not a real data row (e.g. a stray/malformed <tr>)

        records.append({
            "week": cell("occurrence_date_text") or week,
            "da_name": da_name,
            "transporter_id": cell("transporter_id"),
            "station": cell("station"),
            "case_number": case_number,
            "occurrence_info": cell("occurrence_info") or cell("occurrence_date_text"),
            "behavior": cell("behavior"),
            "coaching_tip": cell("coaching_tip"),
            "status": cell("status"),
            "source_email_id": source_email_id,
        })

    return {"week": week, "row_count": len(records)}, records
