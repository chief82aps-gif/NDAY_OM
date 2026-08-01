"""
Parser for Amazon's "Coaching Notifications Weekly Digest" email --
added 2026-08-01. No attachment; the data is an inline HTML table in
the email body. Real columns observed (Weeks 2026-28 through 2026-30):
  DA Name, Transporter ID, Station, Case Number, Week of Occurrence,
  Supporting Information, Behavior, Coaching Tip

Behavior/Coaching Tip are blank on some rows in the real export (only
some case rows carry the full explanation) -- rows are parsed either
way, but only rows with a populated Behavior are meant to be DMed (see
coaching_notifications.py route module), since there's nothing
actionable to tell a driver about otherwise.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional


class _TableRowParser(HTMLParser):
    """Extracts every <tr> inside <tbody> as a list of cell text values,
    in document order. Deliberately minimal -- this email's table has no
    nested tables/rowspans to worry about, confirmed against two real
    weeks' exports."""

    def __init__(self):
        super().__init__()
        self.in_tbody = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self._current_row = []
        elif tag == "td" and self.in_tbody:
            self.in_cell = True
            self._current_cell_parts = []
        elif tag == "br" and self.in_cell:
            self._current_cell_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "tbody":
            self.in_tbody = False
        elif tag == "tr" and self.in_tbody:
            if self._current_row:
                self.rows.append(self._current_row)
        elif tag == "td" and self.in_tbody:
            self.in_cell = False
            self._current_row.append("".join(self._current_cell_parts).strip())

    def handle_data(self, data):
        if self.in_cell:
            self._current_cell_parts.append(data)


def parse_coaching_notifications_html(html_content: str, source_email_id: str = "") -> tuple[dict, list]:
    """Returns (summary, records). summary: {"week": str, "row_count": int}.
    records: list of dicts matching CoachingNotification columns."""
    parser = _TableRowParser()
    parser.feed(html_content)

    week_match = re.search(r"Week (20\d{2}-\d{1,2})", html_content)
    week = week_match.group(1) if week_match else ""

    records = []
    for row in parser.rows:
        if len(row) < 6:
            continue  # not a real data row (e.g. a stray/malformed <tr>)
        da_name, tid, station, case_number, week_of_occurrence, occurrence_info = row[:6]
        if not da_name or not case_number:
            continue
        behavior = row[6] if len(row) > 6 else None
        coaching_tip = row[7] if len(row) > 7 else None
        records.append({
            "week": week_of_occurrence.strip() or week,
            "da_name": da_name,
            "transporter_id": tid,
            "station": station,
            "case_number": case_number,
            "occurrence_info": occurrence_info,
            "behavior": behavior or None,
            "coaching_tip": coaching_tip or None,
            "source_email_id": source_email_id,
        })

    return {"week": week, "row_count": len(records)}, records
