"""Driver handout PDF generation - creates 2x2 card layout with route/driver/vehicle/load info."""
from typing import List, Optional, Tuple
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from api.src.models import RouteSheet
from api.src.assignment import RouteAssignment


class DriverHandoutGenerator:
    """Generates driver handout PDFs with 2x2 card layout."""
    
    # Layout constants optimized for 8.5"x11" portrait (2x2 layout per page)
    CARD_WIDTH = 3.75 * inch  # Reduced from 4.5" to fit 2 across with margins
    CARD_HEIGHT = 5.0 * inch  # Reduced from 6.5" to fit 2 rows on portrait page
    CARD_SPACING = 0.25 * inch  # Vertical spacing between card rows
    MARGIN = 0.4 * inch
    
    # Colors per governance (NDL branding: blue + white)
    COLOR_BLUE = colors.HexColor("#003DA5")  # NDL primary blue
    COLOR_LIGHT_GRAY = colors.HexColor("#F5F5F5")
    COLOR_BLACK = colors.HexColor("#000000")
    
    def __init__(self):
        """Initialize PDF generator."""
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Define custom text styles for handouts."""
        # Card header style (bold, blue)
        self.styles.add(ParagraphStyle(
            name='CardHeader',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=self.COLOR_BLUE,
            fontName='Helvetica-Bold',
            spaceAfter=3,
        ))
        
        # Card title style (route code, large, bold)
        self.styles.add(ParagraphStyle(
            name='CardTitle',
            parent=self.styles['Normal'],
            fontSize=12,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            spaceAfter=2,
        ))
        
        # Card label style (small, gray)
        self.styles.add(ParagraphStyle(
            name='CardLabel',
            parent=self.styles['Normal'],
            fontSize=7,
            textColor=colors.HexColor("#666666"),
            fontName='Helvetica',
            spaceAfter=1,
        ))
        
        # Card value style (regular, bold)
        self.styles.add(ParagraphStyle(
            name='CardValue',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            spaceAfter=2,
        ))
        
        # Table header style
        self.styles.add(ParagraphStyle(
            name='TableHeader',
            parent=self.styles['Normal'],
            fontSize=6,
            textColor=colors.whitesmoke,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
        ))
        
        # Table cell style
        self.styles.add(ParagraphStyle(
            name='TableCell',
            parent=self.styles['Normal'],
            fontSize=6,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_CENTER,
        ))
    
    def generate_handouts(
        self,
        assignments: dict,  # route_code -> RouteAssignment
        route_sheets: List[RouteSheet],
        output_path: str,
    ) -> str:
        """
        Generate driver handout PDF with 2x2 card layout on portrait.
        
        Sorts by wave_time (ascending) then route_code, fits 4 cards per page (2x2).
        Spillover routes resort to 2x1 (2 columns, 1 row).
        
        Args:
            assignments: Dictionary of route_code → RouteAssignment
            route_sheets: List of RouteSheet objects with load manifest
            output_path: Path to save PDF output
        
        Returns:
            Path to generated PDF
        """
        # Build route lookup
        route_lookup = {sheet.route_code: sheet for sheet in route_sheets}
        
        # Sort assignments by wave_time (ascending) then route_code
        assignment_list = sorted(
            assignments.items(),
            key=lambda x: (
                self._parse_wave_time(x[1].wave_time) if x[1].wave_time else "",
                x[0]  # route_code
            )
        )
        
        # Create document
        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            leftMargin=self.MARGIN,
            rightMargin=self.MARGIN,
            topMargin=self.MARGIN,
            bottomMargin=self.MARGIN,
        )
        
        # Build story (content elements)
        story = []
        
        # Add summary page with all assignments sorted by wave time then route code
        story.extend(self._build_summary_page(assignment_list, route_lookup))
        story.append(PageBreak())
        
        # Group assignments into 2x2 grids (4 per page)
        for page_idx in range(0, len(assignment_list), 4):
            if page_idx > 0:  # Page break before new page (except first)
                story.append(PageBreak())
            
            # Get up to 4 assignments for this page
            page_assignments = assignment_list[page_idx:page_idx+4]
            
            # Create 2x2 grid, or 2x1 if fewer than 4 cards
            if len(page_assignments) == 4:
                # Full 2x2 grid
                row1 = page_assignments[0:2]
                row2 = page_assignments[2:4]
                story.append(self._build_card_row(row1, route_lookup))
                story.append(Spacer(1, self.CARD_SPACING))
                story.append(self._build_card_row(row2, route_lookup))
            elif len(page_assignments) == 3:
                # Row 1: 2 cards, Row 2: 1 card (becomes 2x1)
                row1 = page_assignments[0:2]
                row2 = page_assignments[2:3]
                story.append(self._build_card_row(row1, route_lookup))
                story.append(Spacer(1, 0.15*inch))
                story.append(self._build_card_row(row2, route_lookup))
            elif len(page_assignments) == 2:
                # Single row: 2x1 layout
                story.append(self._build_card_row(page_assignments, route_lookup))
            else:
                # Last card only: 2x1 layout with 1 card
                story.append(self._build_card_row(page_assignments, route_lookup))
        
        # Build PDF
        doc.build(story)
        return output_path
    
    def _color_to_abbreviation(self, color_name: str) -> str:
        """Convert full color name to 3-letter abbreviation."""
        color_map = {
            "yellow": "YEL",
            "black": "BLK",
            "orange": "ORG",
            "green": "GRN",
            "navy": "NAV",
            "red": "RED",
            "blue": "BLU",
            "white": "WHT",
            "gray": "GRY",
            "grey": "GRY",
            "brown": "BRN",
            "pink": "PNK",
            "purple": "PUR",
        }
        return color_map.get(color_name.lower(), color_name[:3].upper())
    
    def _parse_wave_time(self, wave_time_str: Optional[str]) -> str:
        """
        Parse wave time string to sortable format.
        Expects format like "10:20 AM" or "2:45 PM".
        Returns 24-hour format for proper sorting.
        """
        if not wave_time_str:
            return "00:00"
        
        try:
            # Remove extra whitespace
            wave_str = wave_time_str.strip()
            
            # Handle "10:20 AM" format
            if "AM" in wave_str.upper() or "PM" in wave_str.upper():
                # Extract time and period
                time_part = wave_str.replace("AM", "").replace("PM", "").replace("am", "").replace("pm", "").strip()
                is_pm = "PM" in wave_str.upper() or "pm" in wave_str
                
                # Parse hours:minutes
                parts = time_part.split(":")
                if len(parts) == 2:
                    hour = int(parts[0])
                    minute = parts[1]
                    
                    # Convert to 24-hour format
                    if is_pm and hour != 12:
                        hour += 12
                    elif not is_pm and hour == 12:
                        hour = 0
                    
                    return f"{hour:02d}:{minute}"
        except Exception:
            pass
        
        return wave_time_str
    
    def _calculate_expected_return(
        self,
        wave_time: Optional[str],
        route_duration_minutes: Optional[int],
    ) -> str:
        """
        Calculate expected return time from wave time + route duration - 30 minutes.
        
        Args:
            wave_time: Wave time string (e.g., "10:20 AM")
            route_duration_minutes: Route duration in minutes
        
        Returns:
            Expected return time as string (e.g., "6:41 PM") or "TBD" if cannot calculate
        """
        if not wave_time or not route_duration_minutes:
            return "TBD"
        
        try:
            from datetime import datetime, timedelta
            
            # Parse wave time
            wave_str = wave_time.strip()
            time_part = wave_str.replace("AM", "").replace("PM", "").replace("am", "").replace("pm", "").strip()
            is_pm = "PM" in wave_str.upper() or "pm" in wave_str
            
            parts = time_part.split(":")
            if len(parts) != 2:
                return "TBD"
            
            hour = int(parts[0])
            minute = int(parts[1])
            
            # Convert to 24-hour format
            if is_pm and hour != 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0
            
            # Create base time (arbitrary date for calculation)
            base_time = datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M")
            
            # Add route duration and subtract 30 minutes
            return_time = base_time + timedelta(minutes=route_duration_minutes - 30)
            
            # Format as "H:MM AM/PM"
            hour_ret = return_time.hour
            minute_ret = return_time.minute
            period = "AM" if hour_ret < 12 else "PM"
            
            if hour_ret > 12:
                hour_ret -= 12
            elif hour_ret == 0:
                hour_ret = 12
            
            return f"{hour_ret}:{minute_ret:02d} {period}"
        except Exception:
            return "TBD"
    
    def _build_summary_page(self, assignment_list: List[Tuple[str, RouteAssignment]], route_lookup: dict) -> List:
        """Build summary page with all drivers sorted by wave time then route code."""
        story = []
        
        # Title
        title = Paragraph(
            "<b>NDAY Daily Driver Assignment Summary</b>",
            ParagraphStyle(
                name='SummaryTitle',
                fontSize=14,
                textColor=self.COLOR_BLUE,
                fontName='Helvetica-Bold',
                alignment=TA_CENTER,
                spaceAfter=8,
            )
        )
        story.append(title)
        
        # Date
        date_text = Paragraph(
            f"Date: {datetime.now().strftime('%B %d, %Y')}",
            ParagraphStyle(
                name='SummaryDate',
                fontSize=9,
                textColor=self.COLOR_BLACK,
                alignment=TA_CENTER,
                spaceAfter=12,
            )
        )
        story.append(date_text)
        
        # Build table data
        table_data = [[
            Paragraph("<b>Name</b>", self.styles['TableHeader']),
            Paragraph("<b>Van</b>", self.styles['TableHeader']),
            Paragraph("<b>Route</b>", self.styles['TableHeader']),
            Paragraph("<b>Wave</b>", self.styles['TableHeader']),
            Paragraph("<b>Expected RTS</b>", self.styles['TableHeader']),
            Paragraph("<b>Service Type</b>", self.styles['TableHeader']),
            Paragraph("<b>DSP</b>", self.styles['TableHeader']),
        ]]
        
        # Add rows for each assignment (already sorted by wave_time then route_code)
        for route_code, assignment in assignment_list:
            route_sheet = route_lookup.get(route_code)
            expected_rts = ""
            if route_sheet and route_sheet.expected_return:
                expected_rts = route_sheet.expected_return
            elif assignment.wave_time and assignment.route_duration:
                expected_rts = self._calculate_expected_return(assignment.wave_time, assignment.route_duration)
            
            table_data.append([
                Paragraph(assignment.driver_name or "", self.styles['TableCell']),
                Paragraph(assignment.vehicle_name or "", self.styles['TableCell']),
                Paragraph(route_code or "", self.styles['TableCell']),
                Paragraph(assignment.wave_time or "", self.styles['TableCell']),
                Paragraph(expected_rts or "", self.styles['TableCell']),
                Paragraph(assignment.service_type[:20] if assignment.service_type else "", self.styles['TableCell']),
                Paragraph(assignment.dsp or "", self.styles['TableCell']),
            ])
        
        # Create table
        table = Table(
            table_data,
            colWidths=[1.0*inch, 0.9*inch, 0.65*inch, 0.65*inch, 0.85*inch, 1.1*inch, 0.65*inch],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 6),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9F9F9')]),
                ('TOPPADDING', (0, 1), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
            ])
        )
        
        story.append(table)
        return story
    
    def _build_card_row(
        self,
        row_assignments: List[Tuple[str, RouteAssignment]],
        route_lookup: dict,
    ) -> Table:
        """Build a row of 1-2 handout cards."""
        cards = []
        
        for route_code, assignment in row_assignments:
            route_sheet = route_lookup.get(route_code)
            card_content = self._build_card(assignment, route_sheet)
            cards.append(card_content)
        
        # Pad with empty cell if only 1 card (for even layout)
        if len(cards) == 1:
            cards.append(Spacer(self.CARD_WIDTH, self.CARD_HEIGHT))
        
        # Create 1x2 table (2 columns)
        row_table = Table([cards], colWidths=[self.CARD_WIDTH, self.CARD_WIDTH])
        row_table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        
        return row_table
    
    def _build_card(
        self,
        assignment: RouteAssignment,
        route_sheet: Optional[RouteSheet],
    ) -> Table:
        """Build a single 3.75\" x 5.0\" handout card with two-column layout."""
        # --- Build Left Column: Route Code, Driver, Expected Return, Bags ---
        left_elements = []
        
        # Large route code (left justified)
        route_style = ParagraphStyle(
            name='LargeRouteCode',
            fontSize=18,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph(assignment.route_code, route_style))
        left_elements.append(Spacer(1, 0.04*inch))
        
        # Driver name (left justified)
        driver_text = assignment.driver_name or "TBD"
        driver_style = ParagraphStyle(
            name='DriverName',
            fontSize=8,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph(driver_text, driver_style))
        left_elements.append(Spacer(1, 0.02*inch))
        
        # Expected return time (left justified, under driver)
        expected_return = route_sheet.expected_return if route_sheet else "TBD"
        expected_style = ParagraphStyle(
            name='ExpectedReturn',
            fontSize=7,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph("<b>Expected Return</b>", expected_style))
        left_elements.append(Paragraph(f"<b>{expected_return}</b>", expected_style))
        left_elements.append(Spacer(1, 0.06*inch))
        
        # Bags table
        if route_sheet and route_sheet.bags:
            bag_data = [
                [Paragraph("<b>Bag</b>", self.styles['TableHeader'])],
            ]
            
            for bag in route_sheet.bags[:8]:  # Max 8 bags to fit
                color_abbr = self._color_to_abbreviation(bag.color_code)
                bag_num = ''.join(c for c in bag.bag_id if c.isdigit()) or bag.bag_id
                bag_display = f"{color_abbr} {bag_num}"
                bag_data.append([
                    Paragraph(bag_display, self.styles['TableCell']),
                ])
            
            bags_table = Table(bag_data, colWidths=[1.4*inch])
            bags_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 6),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            left_elements.append(bags_table)
        
        # --- Build Right Column: Staging, Wave, Service, Vehicle, OV Table ---
        right_elements = []
        
        # Top right info (right justified)
        info_style = ParagraphStyle(
            name='RightInfo',
            fontSize=7,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_RIGHT,
        )
        
        # Staging location at top
        staging_text = route_sheet.staging_location if route_sheet else "TBD"
        right_elements.append(Paragraph(f"<b>{staging_text}</b>", info_style))
        
        # Wave time (right justified)
        wave_text = assignment.wave_time or "TBD"
        right_elements.append(Paragraph(f"<b>{wave_text}</b>", info_style))
        
        # Service type (right justified)
        service_text = assignment.service_type or "TBD"
        right_elements.append(Paragraph(service_text, info_style))
        
        # Vehicle name (right justified)
        vehicle_text = assignment.vehicle_name or "TBD"
        right_elements.append(Paragraph(f"<b>{vehicle_text}</b>", info_style))
        
        right_elements.append(Spacer(1, 0.08*inch))
        
        # Overflow table (right side)
        if route_sheet and route_sheet.overflow:
            overflow_data = [
                [Paragraph("Zone", self.styles['TableHeader']),
                 Paragraph("Qty", self.styles['TableHeader'])],
            ]
            
            for overflow in route_sheet.overflow[:8]:  # Max 8 overflow to fit
                overflow_data.append([
                    Paragraph(overflow.sort_zone, self.styles['TableCell']),
                    Paragraph(str(overflow.package_count), self.styles['TableCell']),
                ])
            
            overflow_table = Table(overflow_data, colWidths=[0.6*inch, 0.5*inch])
            overflow_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 6),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            right_elements.append(overflow_table)
        
        # --- Create two-column table for left and right content ---
        left_col = Table(
            [[elem] for elem in left_elements],
            colWidths=[1.7*inch]
        )
        left_col.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ]))
        
        right_col = Table(
            [[elem] for elem in right_elements],
            colWidths=[1.85*inch]
        )
        right_col.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ]))
        
        # Create main two-column card layout
        card_layout = Table(
            [[left_col, right_col]],
            colWidths=[1.7*inch, 1.85*inch]
        )
        card_layout.setStyle(TableStyle([
            ('BORDER', (0, 0), (-1, -1), 1.5, self.COLOR_BLUE),
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        
        return card_layout
