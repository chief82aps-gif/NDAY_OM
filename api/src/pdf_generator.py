"""Driver handout PDF generation - creates 2x2 card layout with route/driver/vehicle/load info."""
from typing import List, Optional, Tuple
from datetime import datetime
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from api.src.models import RouteSheet
from api.src.assignment import RouteAssignment


class DriverHandoutGenerator:
    """Generates driver handout PDFs with 2x2 card layout."""
    
    # Layout constants optimized for 8.5"x11" portrait (2x2 layout per page)
    CARD_WIDTH = 3.9 * inch  # Increased to 3.9" to fit 2 across with minimal margins
    CARD_HEIGHT = 5.25 * inch  # Increased to 5.25" to fit 2 rows on full page without header
    CARD_SPACING = 0.05 * inch  # Minimal spacing between card rows
    MARGIN = 0.1 * inch  # Minimized to 0.1" since no header/footer
    
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
    
    def _build_header_with_logo(self) -> List:
        """Build header with company logo and title."""
        header_elements = []
        
        # Try to load logo
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'Logo', 'NDL LogoShirt.png'
        )
        
        header_table_data = []
        
        if os.path.exists(logo_path):
            # Load logo image
            try:
                logo_img = Image(logo_path, width=0.5*inch, height=0.5*inch)
                header_table_data.append([
                    logo_img,
                    Paragraph('<b>NDL Driver Handout</b>', ParagraphStyle(
                        'CompactHeading',
                        parent=self.styles['Heading2'],
                        fontSize=12,
                        textColor=colors.whitesmoke,
                    )),
                    Paragraph(f'{datetime.now().strftime("%m/%d/%Y")}', ParagraphStyle(
                        'DateSmall',
                        parent=self.styles['Normal'],
                        fontSize=8,
                        textColor=colors.whitesmoke,
                    ))
                ])
            except:
                # Fallback if logo fails to load
                header_table_data.append([
                    Paragraph('<b>NDL Handout</b>', ParagraphStyle(
                        'CompactHeading',
                        parent=self.styles['Heading2'],
                        fontSize=12,
                        textColor=colors.whitesmoke,
                    )),
                ])
        else:
            # Fallback if logo file not found
            header_table_data.append([
                Paragraph('<b>NDL Handout</b>', ParagraphStyle(
                    'CompactHeading',
                    parent=self.styles['Heading2'],
                    fontSize=12,
                    textColor=colors.whitesmoke,
                )),
            ])
        
        # Create compact header table with blue background
        header_table = Table(header_table_data, colWidths=[0.6*inch, 3.5*inch, 1*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        
        header_elements.append(header_table)
        header_elements.append(Spacer(1, 0.1*inch))  # Reduced from 0.2"
        
        return header_elements
    
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
        
        # Add header only on summary page
        story.extend(self._build_header_with_logo())
        story.extend(self._build_summary_page(assignment_list, route_lookup))
        story.append(PageBreak())
        
        def route_has_overflow(route_code: str) -> bool:
            route_sheet = route_lookup.get(route_code)
            return bool(route_sheet and route_sheet.overflow)

        # Build pages with overflow-aware layout:
        # any page containing overflow routes is forced to 2x1 portrait (max 2 cards)
        page_idx = 0
        while page_idx < len(assignment_list):
            if page_idx > 0:  # Page break before new page (except first)
                story.append(PageBreak())

            # Check if the first route at this position has overflow
            # If it does, use 2x1 layout to give it more space
            first_route = assignment_list[page_idx][0] if page_idx < len(assignment_list) else None
            has_overflow_in_first = first_route and route_has_overflow(first_route)

            # Use 2x1 layout only if first route has overflow, otherwise use 2x2 layout
            page_size = 2 if has_overflow_in_first else 4

            page_assignments = assignment_list[page_idx:page_idx + page_size]

            if page_size == 4 and len(page_assignments) == 4:
                row1 = page_assignments[0:2]
                row2 = page_assignments[2:4]
                story.append(self._build_card_row(row1, route_lookup))
                story.append(Spacer(1, self.CARD_SPACING))
                story.append(self._build_card_row(row2, route_lookup))
            else:
                story.append(self._build_card_row(page_assignments[0:2], route_lookup))

            page_idx += page_size
        
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
    
    def _extract_wave_number(self, wave_time: Optional[str]) -> int:
        """
        Extract wave number (1-4) from wave time string.
        
        Maps wave times to wave numbers:
        - Early morning (before 8 AM) -> Wave 1
        - Morning (8-10 AM) -> Wave 2
        - Late morning (10 AM-12 PM) -> Wave 3
        - Afternoon (after 12 PM) -> Wave 4
        
        Args:
            wave_time: Wave time string (e.g., "10:20 AM")
        
        Returns:
            Wave number (1-4), defaults to 1 if cannot parse
        """
        if not wave_time:
            return 1
        
        try:
            wave_str = wave_time.strip().upper()
            time_part = wave_str.replace("AM", "").replace("PM", "").strip()
            is_pm = "PM" in wave_str
            
            parts = time_part.split(":")
            if len(parts) != 2:
                return 1
            
            hour = int(parts[0])
            
            # Convert to 24-hour format for comparison
            if is_pm and hour != 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0
            
            # Map wave based on time
            if hour < 8:  # Before 8 AM
                return 1
            elif hour < 10:  # 8-10 AM
                return 2
            elif hour < 12:  # 10 AM-12 PM
                return 3
            else:  # After 12 PM
                return 4
        except Exception:
            return 1
    
    def _build_summary_page(self, assignment_list: List[Tuple[str, RouteAssignment]], route_lookup: dict) -> List:
        """Build summary page with all drivers sorted by wave time then route code.
        
        If < 50 routes, fits on single page. Otherwise paginate to next page.
        """
        story = []
        
        # Title
        title = Paragraph(
            "<b>NDAY Daily Driver Assignment Summary</b>",
            ParagraphStyle(
                name='SummaryTitle',
                fontSize=12,
                textColor=self.COLOR_BLUE,
                fontName='Helvetica-Bold',
                alignment=TA_CENTER,
                spaceAfter=2,
            )
        )
        story.append(title)
        
        # Date
        date_text = Paragraph(
            f"Date: {datetime.now().strftime('%B %d, %Y')}",
            ParagraphStyle(
                name='SummaryDate',
                fontSize=8,
                textColor=self.COLOR_BLACK,
                alignment=TA_CENTER,
                spaceAfter=2,
            )
        )
        story.append(date_text)
        
        # Build table data with wave tracking
        table_data = [[
            Paragraph("<b>Name</b>", self.styles['TableHeader']),
            Paragraph("<b>Van</b>", self.styles['TableHeader']),
            Paragraph("<b>Route</b>", self.styles['TableHeader']),
            Paragraph("<b>Wave</b>", self.styles['TableHeader']),
            Paragraph("<b>Expected RTS</b>", self.styles['TableHeader']),
        ]]
        
        # Build row background colors based on wave timing
        row_backgrounds = [self.COLOR_BLUE]  # Header row is blue
        
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
            ])
            
            # Determine background color based on wave or assignment status
            if assignment.vehicle_name == "UNASSIGNED":
                # Highlight failed assignments in light red
                row_backgrounds.append(colors.HexColor('#FFD4D4'))
            elif assignment.wave_time:
                wave_num = self._extract_wave_number(assignment.wave_time)
                if wave_num == 2:
                    row_backgrounds.append(colors.HexColor('#D3D3D3'))  # Medium gray
                elif wave_num == 4:
                    row_backgrounds.append(colors.HexColor('#F0F0F0'))  # Light gray
                else:
                    row_backgrounds.append(colors.white)  # Wave 1 and 3: white
            else:
                row_backgrounds.append(colors.white)
        
        # Create table with wave-based row shading
        table_style_list = [
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 0.25),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 0.25),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 4),
            ('TOPPADDING', (0, 1), (-1, -1), 0.5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 0.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 1),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), row_backgrounds),
        ]
        
        table = Table(
            table_data,
            colWidths=[1.2*inch, 0.70*inch, 0.55*inch, 0.65*inch, 0.75*inch],
            style=TableStyle(table_style_list)
        )
        
        story.append(table)
        
        # If >= 50 routes, add page break to keep summary on one page
        if len(assignment_list) >= 50:
            from reportlab.platypus import PageBreak
            story.append(PageBreak())
        
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
            ('BOX', (0, 0), (0, 0), 1.5, self.COLOR_BLUE),
            ('BOX', (1, 0), (1, 0), 1.5, self.COLOR_BLUE),
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
            fontSize=16,
            leading=16,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph(assignment.route_code, route_style))
        left_elements.append(Spacer(1, 0*inch))
        
        # Staging location directly under route code (left justified)
        staging_text = route_sheet.staging_location if route_sheet else "TBD"
        staging_style = ParagraphStyle(
            name='StagingLocation',
            fontSize=8,
            leading=8,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph(f"Staging: {staging_text}", staging_style))
        left_elements.append(Spacer(1, 0*inch))
        
        # Expected return time (left justified, under driver)
        expected_return = route_sheet.expected_return if route_sheet else "TBD"
        expected_style = ParagraphStyle(
            name='ExpectedReturn',
            fontSize=7,
            leading=7,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_LEFT,
        )
        left_elements.append(Paragraph("<b>Expected Return</b>", expected_style))
        left_elements.append(Paragraph(f"<b>{expected_return}</b>", expected_style))
        left_elements.append(Spacer(1, 0.01*inch))
        
        # Bags table - 3 column layout
        if route_sheet and route_sheet.bags:
            num_bags = len(route_sheet.bags)
            
            # Create 3-column layout with left-to-right, top-to-bottom ordering
            bag_data = [
                [Paragraph("<b>Top</b>", self.styles['TableHeader']),
                 Paragraph("<b>Middle</b>", self.styles['TableHeader']),
                 Paragraph("<b>Bottom</b>", self.styles['TableHeader'])],
            ]
            
            # Fill rows with up to 3 bags each
            for i in range(0, num_bags, 3):
                row = []
                for j in range(3):
                    if i + j < num_bags:
                        bag = route_sheet.bags[i + j]
                        row.append(Paragraph(bag.bag_id, self.styles['TableCell']))
                    else:
                        row.append("")
                bag_data.append(row)
            
            bags_table = Table(bag_data, colWidths=[0.65*inch, 0.65*inch, 0.65*inch])
            bags_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0.5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0.5),
                ('TOPPADDING', (0, 0), (-1, -1), 0.25),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0.25),
            ]))
            left_elements.append(bags_table)
        
        # --- Build Right Column: Staging, Wave, Service, Vehicle, OV Table ---
        right_elements = []
        
        # Top right info (right justified)
        info_style = ParagraphStyle(
            name='RightInfo',
            fontSize=7,
            leading=7,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica',
            alignment=TA_RIGHT,
        )

        # Driver name opposite route code, same size as route code
        driver_text = assignment.driver_name or "TBD"
        driver_style = ParagraphStyle(
            name='DriverNameTopRight',
            fontSize=16,
            leading=16,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            alignment=TA_RIGHT,
        )
        right_elements.append(Paragraph(driver_text, driver_style))
        right_elements.append(Spacer(1, 0*inch))
        
        # Wave time (right justified)
        wave_text = assignment.wave_time or "TBD"
        right_elements.append(Paragraph(f"<b>Wave {wave_text}</b>", info_style))
        
        # Vehicle name (right justified)
        vehicle_text = assignment.vehicle_name or "TBD"
        right_elements.append(Paragraph(f"<b>{vehicle_text}</b>", info_style))
        
        right_elements.append(Spacer(1, 0.005*inch))
        
        # Overflow table - 2 column layout with zone grid
        if route_sheet and route_sheet.overflow:
            num_zones = len(route_sheet.overflow)
            
            # Create 2-column layout with left-to-right, top-to-bottom ordering
            overflow_data = [
                [Paragraph("<b>Zone</b>", self.styles['TableHeader']),
                 Paragraph("<b>Zone</b>", self.styles['TableHeader'])],
            ]
            
            # Fill rows with up to 2 zones each
            for i in range(0, num_zones, 2):
                row = []
                for j in range(2):
                    if i + j < num_zones:
                        overflow = route_sheet.overflow[i + j]
                        zone_text = "{} ({})".format(overflow.sort_zone, overflow.package_count)
                        row.append(Paragraph(zone_text, self.styles['TableCell']))
                    else:
                        row.append("")
                overflow_data.append(row)
            
            overflow_table = Table(overflow_data, colWidths=[0.65*inch, 0.65*inch])
            overflow_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0.5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0.5),
                ('TOPPADDING', (0, 0), (-1, -1), 0.25),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0.25),
            ]))
            right_elements.append(overflow_table)
        
        # --- Create two-column table for left and right content ---
        left_col = Table(
            [[elem] for elem in left_elements],
            colWidths=[2.15*inch]
        )
        left_col.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ]))
        
        right_col = Table(
            [[elem] for elem in right_elements],
            colWidths=[1.40*inch]
        )
        right_col.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ]))
        
        # Create main two-column body layout
        card_body = Table(
            [[left_col, right_col]],
            colWidths=[2.15*inch, 1.40*inch]
        )
        card_body.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.5),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))

        footer_style = ParagraphStyle(
            name='CardFooter',
            parent=self.styles['Normal'],
            fontSize=6,
            leading=7,
            textColor=colors.HexColor("#444444"),
            fontName='Helvetica-Oblique',
            alignment=TA_CENTER,
        )
        footer_text = self._get_footer_message(assignment, route_sheet)
        footer_paragraph = Paragraph(f'"{footer_text}"', footer_style)

        # Wrap body + footer so footer stays at bottom area of each card
        card_layout = Table(
            [[card_body], [footer_paragraph]],
            colWidths=[3.55*inch],
            rowHeights=[4.72*inch, 0.18*inch]
        )
        card_layout.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('VALIGN', (0, 0), (0, 0), 'TOP'),
            ('VALIGN', (0, 1), (0, 1), 'BOTTOM'),
            ('ALIGN', (0, 1), (0, 1), 'CENTER'),
        ]))
        
        return card_layout

    def _get_footer_message(
        self,
        assignment: RouteAssignment,
        route_sheet: Optional[RouteSheet],
    ) -> str:
        """Return a fixed motivational/safety footer message for each card."""
        return "One stop at a time — finish strong."
