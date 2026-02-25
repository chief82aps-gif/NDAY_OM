"""Driver schedule report generation - creates formatted report with show times and sweepers."""
from typing import Dict, List, Tuple
from datetime import datetime
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from api.src.models import DriverScheduleSummary


class DriverScheduleReportGenerator:
    """Generates driver schedule reports with show times and sweepers."""
    
    # Colors for show time shading
    COLOR_BLUE = colors.HexColor("#003DA5")  # NDL primary blue
    COLOR_GREEN = colors.HexColor("#90EE90")  # Light green
    COLOR_PURPLE = colors.HexColor("#C8A2D0")  # Light purple
    COLOR_LIGHT_GRAY = colors.HexColor("#F5F5F5")
    COLOR_ORANGE = colors.HexColor("#FFB84D")  # Light orange
    
    def __init__(self):
        """Initialize report generator."""
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Define custom text styles for reports."""
        self.styles.add(ParagraphStyle(
            name='ReportTitle',
            parent=self.styles['Normal'],
            fontSize=14,
            textColor=self.COLOR_BLUE,
            fontName='Helvetica-Bold',
            spaceAfter=6,
            alignment=TA_CENTER,
        ))
        
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=self.COLOR_BLUE,
            fontName='Helvetica-Bold',
            spaceAfter=4,
            spaceBefore=8,
        ))
        
        self.styles.add(ParagraphStyle(
            name='TableHeader',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.whitesmoke,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
        ))
        
        self.styles.add(ParagraphStyle(
            name='TableCell',
            parent=self.styles['Normal'],
            fontSize=7,
            textColor=colors.black,
            fontName='Helvetica',
            alignment=TA_LEFT,
        ))
    
    def _build_show_time_color_map(self, show_times: List[str]) -> Dict[str, object]:
        """Build a consistent color mapping for all show times."""
        color_palette = [self.COLOR_GREEN, self.COLOR_PURPLE, self.COLOR_ORANGE, self.COLOR_LIGHT_GRAY]
        color_map = {}
        
        for i, show_time in enumerate(sorted(set(st for st in show_times if st))):
            color_map[show_time] = color_palette[i % len(color_palette)]
        
        return color_map
    
    def generate_schedule_report(
        self,
        schedule: DriverScheduleSummary,
        output_path: str,
    ) -> str:
        """
        Generate driver schedule report PDF.
        
        Args:
            schedule: DriverScheduleSummary with assignments and sweepers
            output_path: Path to save PDF
            
        Returns:
            Path to generated PDF
        """
        doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        story = []
        
        # Title
        title = Paragraph(
            f"<b>Driver Schedule Report</b>",
            self.styles['ReportTitle']
        )
        story.append(title)
        
        # Metadata
        metadata = Paragraph(
            f"<font size=7>Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')} | "
            f"File Timestamp: {schedule.timestamp} | Scheduled Date: {schedule.date}</font>",
            ParagraphStyle(
                name='Metadata',
                fontSize=7,
                textColor=colors.grey,
                alignment=TA_CENTER,
                spaceAfter=8,
            )
        )
        story.append(metadata)
        
        # Build consistent color map for all show times
        all_show_times = [a.show_time for a in schedule.assignments if a.show_time]
        if schedule.sweepers and schedule.show_times:
            all_show_times.extend([schedule.show_times.get(s) for s in schedule.sweepers if schedule.show_times.get(s)])
        show_time_color_map = self._build_show_time_color_map(all_show_times)
        
        # Group drivers by show time
        drivers_by_showtime = self._group_drivers_by_showtime(schedule)
        
        # Add assignment section
        story.extend(self._build_assignments_section(schedule, drivers_by_showtime, show_time_color_map))
        
        # Add sweepers section if any
        if schedule.sweepers:
            story.append(PageBreak())
            story.extend(self._build_sweepers_section(schedule, show_time_color_map))
        
        # Build PDF
        doc.build(story)
        return output_path
    
    def _group_drivers_by_showtime(self, schedule: DriverScheduleSummary) -> Dict[str, List[str]]:
        """Group drivers by show time."""
        grouped = {}
        for assignment in schedule.assignments:
            show_time = assignment.show_time or "Unknown"
            if show_time not in grouped:
                grouped[show_time] = []
            grouped[show_time].append(assignment.driver_name)
        
        # Sort each group alphabetically
        for show_time in grouped:
            grouped[show_time].sort()
        
        return grouped
    
    def _build_assignments_section(
        self,
        schedule: DriverScheduleSummary,
        drivers_by_showtime: Dict[str, List[str]],
        show_time_color_map: Dict[str, object],
    ) -> List[Table]:
        """Build assignments table section."""
        story = []
        
        # Section header
        header = Paragraph("<b>Driver Assignments by Show Time</b>", self.styles['SectionHeader'])
        story.append(header)
        
        # Build table with color-coded show times
        table_data = [[
            Paragraph("<b>Name</b>", self.styles['TableHeader']),
            Paragraph("<b>Show Time</b>", self.styles['TableHeader']),
            Paragraph("<b>Wave Time</b>", self.styles['TableHeader']),
        ]]
        
        row_colors = [self.COLOR_BLUE]  # Header

        def parse_wave_minutes(value: str) -> int:
            if not value:
                return 10**9

            trimmed = value.strip()
            match = re.match(r"^(\d{1,2}):(\d{2})(?:\s*([AaPp][Mm]))?$", trimmed)
            if not match:
                return 10**9

            hours = int(match.group(1))
            minutes = int(match.group(2))
            meridiem = match.group(3).upper() if match.group(3) else None

            if meridiem == "PM" and hours < 12:
                hours += 12
            if meridiem == "AM" and hours == 12:
                hours = 0

            return hours * 60 + minutes

        sorted_assignments = sorted(
            schedule.assignments,
            key=lambda assignment: (
                parse_wave_minutes(assignment.wave_time or ""),
                (assignment.driver_name or "").casefold(),
            ),
        )
        
        # Build color mapping for show times
        # Add rows for each driver
        for assignment in sorted_assignments:
            table_data.append([
                Paragraph(assignment.driver_name, self.styles['TableCell']),
                Paragraph(assignment.show_time or "", self.styles['TableCell']),
                Paragraph(assignment.wave_time or "", self.styles['TableCell']),
            ])
            
            # Determine color based on show time mapping
            show_time = assignment.show_time
            row_color = show_time_color_map.get(show_time, colors.white) if show_time else colors.white
            row_colors.append(row_color)
        
        # Create table
        table = Table(
            table_data,
            colWidths=[2.5*inch, 1.5*inch, 1.5*inch],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Name column left-aligned
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 7),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), row_colors),
            ])
        )
        
        story.append(table)
        return story
    def _build_sweepers_section(self, schedule: DriverScheduleSummary, show_time_color_map: Dict[str, object]) -> List:
        """Build sweepers section."""
        story = []
        
        # Section header
        header = Paragraph(
            f"<b>Sweepers ({len(schedule.sweepers)} drivers)</b>",
            self.styles['SectionHeader']
        )
        story.append(header)
        
        # Get sweeper show time
        sweeper_show_time = schedule.show_times.get(schedule.sweepers[0], "N/A") if schedule.sweepers else "N/A"
        
        subtitle = Paragraph(
            f"<font size=8>Show Time: <b>{sweeper_show_time}</b></font>",
            ParagraphStyle(
                name='SweeperSubtitle',
                fontSize=8,
                textColor=self.COLOR_BLUE,
                fontName='Helvetica-Bold',
                spaceAfter=4,
            )
        )
        story.append(subtitle)
        
        # Build sweeper table
        table_data = [[
            Paragraph("<b>Name</b>", self.styles['TableHeader']),
            Paragraph("<b>Show Time</b>", self.styles['TableHeader']),
        ]]
        
        # Get color for sweeper show time from the color map
        sweeper_color = show_time_color_map.get(sweeper_show_time, colors.white)
        
        row_colors = [self.COLOR_BLUE]  # Header
        
        # Add sweepers
        for sweeper in sorted(schedule.sweepers):
            table_data.append([
                Paragraph(sweeper, self.styles['TableCell']),
                Paragraph(sweeper_show_time, self.styles['TableCell']),
            ])
            row_colors.append(sweeper_color)
        
        # Create table
        table = Table(
            table_data,
            colWidths=[2.5*inch, 1.5*inch],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Name column left-aligned
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 7),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), row_colors),
            ])
        )
        
        story.append(table)
        return story
