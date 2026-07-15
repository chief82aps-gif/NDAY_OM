"""Crash Report PDF generation — renders a CrashReport row into a PDF laid
out in the same section order as Amazon's "DA Incident Packet v3.3" paper
form, using the same reportlab draw-from-scratch approach as
pdf_generator.py's DriverHandoutGenerator (no fillable-template library in
this repo — see the crash-report build plan for why).

Generated once, at initial submission (see crash_report.py's
submit_crash_report), and re-used as the attachment on every stage of the
Slack approval chain.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from api.src import storage
from api.src.database import CrashReport

logger = logging.getLogger(__name__)

COLOR_BLUE = colors.HexColor("#003DA5")
COLOR_LIGHT_GRAY = colors.HexColor("#F5F5F5")
COLOR_BLACK = colors.HexColor("#000000")

# (photo column attribute, section title) — appendix render order.
_PHOTO_SECTIONS = [
    ("photo_vehicle_damage", "NDAY Vehicle Damage"),
    ("photo_urls", "360° Scene Photos"),
    ("photo_other_vehicle", "Third Party Vehicle"),
    ("photo_dl_driver", "Driver's License — NDAY Driver"),
    ("photo_dl_other", "Driver's License — Third Party"),
    ("photo_insurance_other", "Third Party Insurance"),
    ("photo_license_plate_other", "Third Party License Plate"),
]


def _fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


class CrashReportPdfGenerator:
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name="CRTitle", fontSize=16, textColor=colors.whitesmoke,
            fontName="Helvetica-Bold", alignment=TA_CENTER,
        ))
        self.styles.add(ParagraphStyle(
            name="CRSection", fontSize=11, textColor=COLOR_BLUE,
            fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=4,
        ))
        self.styles.add(ParagraphStyle(
            name="CRLabel", fontSize=7, textColor=colors.HexColor("#666666"), fontName="Helvetica",
        ))
        self.styles.add(ParagraphStyle(
            name="CRValue", fontSize=9, textColor=COLOR_BLACK, fontName="Helvetica-Bold",
        ))
        self.styles.add(ParagraphStyle(
            name="CRBody", fontSize=9, textColor=COLOR_BLACK, fontName="Helvetica", leading=13,
        ))
        self.styles.add(ParagraphStyle(
            name="CRCaption", fontSize=7, textColor=colors.HexColor("#444444"),
            fontName="Helvetica-Oblique", alignment=TA_CENTER,
        ))

    # ── field grid helper ──────────────────────────────────────────────
    def _field_grid(self, pairs: list[tuple[str, str]], cols: int = 2) -> Table:
        """pairs: [(label, value), ...] laid out `cols` label/value pairs per row."""
        cells = []
        for label, value in pairs:
            cells.append([
                Paragraph(label, self.styles["CRLabel"]),
                Paragraph(_fmt(value), self.styles["CRValue"]),
            ])
        rows = []
        for i in range(0, len(cells), cols):
            row = []
            for c in cells[i:i + cols]:
                row.extend(c)
            while len(row) < cols * 2:
                row.append("")
            rows.append(row)
        col_width = (7.0 * inch) / (cols * 2)
        table = Table(rows, colWidths=[col_width] * (cols * 2))
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    def _header(self, report: CrashReport) -> Table:
        t = Table(
            [[
                Paragraph("DA Incident Packet — Crash Report", self.styles["CRTitle"]),
                Paragraph(f"Report {report.report_number}", ParagraphStyle(
                    "CRHeaderRight", fontSize=10, textColor=colors.whitesmoke,
                    fontName="Helvetica", alignment=TA_CENTER,
                )),
            ]],
            colWidths=[5.5 * inch, 1.5 * inch],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COLOR_BLUE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        return t

    def _photo_appendix(self, report: CrashReport) -> list:
        story: list = [PageBreak(), Paragraph("Photo Evidence", self.styles["CRSection"])]
        any_photos = False
        for attr, title in _PHOTO_SECTIONS:
            keys = getattr(report, attr, None) or []
            if not keys:
                continue
            any_photos = True
            story.append(Paragraph(title, ParagraphStyle(
                "CRPhotoSection", fontSize=9, textColor=COLOR_BLACK,
                fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4,
            )))
            thumbs = []
            for key in keys:
                img_flowable = self._image_thumbnail(key)
                if img_flowable:
                    thumbs.append(img_flowable)
            if thumbs:
                # 3 photos per row
                rows = [thumbs[i:i + 3] for i in range(0, len(thumbs), 3)]
                for row in rows:
                    while len(row) < 3:
                        row.append("")
                    row_table = Table([row], colWidths=[2.2 * inch] * 3)
                    row_table.setStyle(TableStyle([
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ]))
                    story.append(row_table)
                    story.append(Spacer(1, 0.1 * inch))
        if report.diagram_url:
            any_photos = True
            story.append(Paragraph("Diagram of Accident Scene", ParagraphStyle(
                "CRDiagramSection", fontSize=9, textColor=COLOR_BLACK,
                fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4,
            )))
            img = self._image_thumbnail(report.diagram_url, width=4 * inch)
            if img:
                story.append(img)
        if not any_photos:
            story.append(Paragraph("No photos on file.", self.styles["CRBody"]))
        return story

    def _image_thumbnail(self, key: str, width: float = 2.1 * inch):
        try:
            data = storage.download_bytes(key)
            buf = io.BytesIO(data)
            img = Image(buf, width=width, height=width)
            img.hAlign = "CENTER"
            return img
        except Exception as exc:
            logger.warning("Crash report PDF: failed to load photo %s: %s", key, exc)
            return Paragraph("[photo unavailable]", self.styles["CRCaption"])

    def generate(self, report: CrashReport, dest) -> None:
        """dest: a filename or a file-like object (e.g. io.BytesIO) — reportlab
        accepts either. Report data + evidence photos already live durably in
        Postgres/S3, so the PDF is regenerated on demand rather than cached as
        its own stored file — always reflects current data, no extra storage
        column to keep in sync."""
        doc = SimpleDocTemplate(
            dest, pagesize=letter,
            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        )
        story: list = [self._header(report), Spacer(1, 0.15 * inch)]

        # Safety checklist
        story.append(Paragraph("Safety Checklist", self.styles["CRSection"]))
        story.append(self._field_grid([
            ("Emergency flashers on", report.flashers_on),
            ("Vehicle secured", report.vehicle_secured),
            ("Police called", report.police_called),
            ("Medical assistance requested", report.medical_requested),
            ("On-Road Hotline called", report.hotline_called),
            ("Hotline call time", report.hotline_call_at),
            ("DSP Owner/Dispatcher notified", report.dsp_owner_notified),
            ("360° photos taken", report.photos_360_taken),
        ]))

        # General information
        story.append(Paragraph("General Information", self.styles["CRSection"]))
        story.append(self._field_grid([
            ("Accident Date", report.accident_date),
            ("Time of Accident", f"{report.accident_time or ''} {report.accident_ampm or ''}".strip()),
            ("Location", report.location_address),
            ("City/State/Zip", report.city_state_zip),
            ("Driver's Name", report.driver_name),
            ("Driver License #", report.driver_license_number),
            ("Driver License State", report.driver_license_state),
            ("DSP Code", report.dsp_code),
        ]))

        # Vehicle information
        story.append(Paragraph("Vehicle Information", self.styles["CRSection"]))
        story.append(self._field_grid([
            ("Year", report.vehicle_year),
            ("Make & Model", report.vehicle_make_model),
            ("License Plate & State", report.license_plate_state),
            ("Equipment # (Van)", report.equipment_number),
            ("VIN", report.vin),
            ("AMZL Station (Origin)", report.amzl_station_origin),
            ("Destination", report.destination_type),
        ]))

        # Third party
        if report.third_party_involved:
            story.append(Paragraph("Third Party Information", self.styles["CRSection"]))
            story.append(self._field_grid([
                ("Driver's Name", report.third_party_driver_name),
                ("Driver's Address", report.third_party_driver_address),
                ("Driver's Phone #", report.third_party_driver_phone),
                ("Insurance Co. & Policy No.", report.third_party_insurance),
                ("Vehicle Year", report.third_party_vehicle_year),
                ("Make & Model", report.third_party_vehicle_make_model),
                ("License Plate & State", report.third_party_license_plate_state),
                ("Driver License No.", report.third_party_license_no),
                ("License State", report.third_party_license_state),
            ]))

        # Narrative / statements — sanitized text (see crash_report.py's
        # sanitize-statement endpoint); *_raw columns hold the verbatim
        # originals and are intentionally NOT rendered onto this document.
        story.append(Paragraph("Driver's Statement", self.styles["CRSection"]))
        story.append(Paragraph(_fmt(report.accident_description), self.styles["CRBody"]))

        if report.third_party_involved:
            story.append(Paragraph("Third Party Statement", self.styles["CRSection"]))
            if report.third_party_statement_declined:
                story.append(Paragraph("Third party declined to provide a statement.", self.styles["CRBody"]))
            else:
                story.append(Paragraph(_fmt(report.third_party_statement), self.styles["CRBody"]))

        # Conditions / other
        story.append(Paragraph("Conditions / Other", self.styles["CRSection"]))
        story.append(self._field_grid([
            ("Number of Lanes", report.num_lanes),
            ("Road Construction", report.road_construction),
            ("Road Attitude", report.road_attitude),
            ("Traffic Conditions", report.traffic_conditions),
            ("Light Conditions", report.light_conditions),
            ("Road Conditions", report.road_conditions),
            ("Weather Conditions", report.weather_conditions),
        ]))

        # Police
        if report.police_called:
            story.append(Paragraph("Police Report", self.styles["CRSection"]))
            story.append(self._field_grid([
                ("Police Department", report.police_department),
                ("Officer's Name", report.officer_name),
                ("Phone No.", report.police_phone),
                ("Report No.", report.police_report_no),
                ("Citation Issued", report.citation_issued),
            ]))

        # Photo appendix
        story.extend(self._photo_appendix(report))

        doc.build(story)


def generate_crash_report_pdf_bytes(report: CrashReport) -> bytes:
    """Renders the report to an in-memory PDF — used both by the
    GET /crash-report/{id}/pdf endpoint and by the Slack approval-chain
    notifications (crash_report.py), which attach it fresh at each stage."""
    buf = io.BytesIO()
    CrashReportPdfGenerator().generate(report, buf)
    return buf.getvalue()
