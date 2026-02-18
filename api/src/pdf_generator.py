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
        
        # Add title page
        story.extend(self._build_title_page())
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
    
    def _build_title_page(self) -> List:
        """Build title page content."""
        story = []
        story.append(Spacer(1, 2 * inch))
        
        # Title
        title = Paragraph(
            "<b>NDAY Driver Handouts</b>",
            ParagraphStyle(
                name='Title',
                fontSize=24,
                textColor=self.COLOR_BLUE,
                fontName='Helvetica-Bold',
                alignment=TA_CENTER,
                spaceAfter=12,
            )
        )
        story.append(title)
        
        # Date
        date_text = Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            ParagraphStyle(
                name='Subtitle',
                fontSize=12,
                textColor=colors.grey,
                alignment=TA_CENTER,
                spaceAfter=24,
            )
        )
        story.append(date_text)
        
        # Instructions
        instructions = Paragraph(
            "<b>Instructions:</b><br/>"
            "1. Verify route assignment and vehicle details on each card<br/>"
            "2. Confirm load manifest (bags and overflow)<br/>"
            "3. Proceed with delivery execution<br/>",
            ParagraphStyle(
                name='Instructions',
                fontSize=10,
                textColor=self.COLOR_BLACK,
                spaceAfter=12,
            )
        )
        story.append(instructions)
        
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
        """Build a single 3.75\" x 5.0\" handout card."""
        card_elements = []
        
        # --- Header Section with Staging Location ---
        staging_text = route_sheet.staging_location if route_sheet else "TBD"
        header_data = [
            [Paragraph(staging_text, self.styles['CardHeader'])],
        ]
        header_table = Table(header_data, colWidths=[self.CARD_WIDTH - 0.1*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_LIGHT_GRAY),
            ('LINEBELOW', (0, 0), (-1, -1), 1.5, self.COLOR_BLUE),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        card_elements.append(header_table)
        card_elements.append(Spacer(1, 0.04*inch))
        
        # --- Route Code (Large) ---
        route_title = [
            [Paragraph(assignment.route_code, self.styles['CardTitle'])],
        ]
        route_table = Table(route_title, colWidths=[self.CARD_WIDTH - 0.1*inch])
        route_table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        card_elements.append(route_table)
        card_elements.append(Spacer(1, 0.02*inch))
        
        # --- Wave Time (in bold, larger) ---
        wave_text = f"<b>{assignment.wave_time or 'TBD'}</b>"
        card_elements.append(Paragraph(wave_text, self.styles['CardLabel']))
        card_elements.append(Spacer(1, 0.01*inch))
        
        # --- Service Type (condensed) ---
        service_text = f"{assignment.service_type}"
        card_elements.append(Paragraph(service_text, self.styles['CardLabel']))
        card_elements.append(Spacer(1, 0.02*inch))
        
        # --- Driver & Vehicle (condensed) ---
        driver_text = f"<b>Driver:</b> {assignment.driver_name or 'TBD'}"
        card_elements.append(Paragraph(driver_text, self.styles['CardLabel']))
        
        vehicle_text = f"<b>Vehicle:</b> {assignment.vehicle_name}"
        card_elements.append(Paragraph(vehicle_text, self.styles['CardLabel']))
        card_elements.append(Spacer(1, 0.02*inch))
        
        # --- Load Manifest Header with Totals ---
        load_header = ""
        if route_sheet:
            total_bags = len(route_sheet.bags) if route_sheet.bags else 0
            total_overflow = len(route_sheet.overflow) if route_sheet.overflow else 0
            load_header = f"<b>{total_bags} Bags</b> | <b>{total_overflow} OV</b>"
            if load_header:
                card_elements.append(Paragraph(load_header, ParagraphStyle(
                    name='LoadHeader',
                    fontSize=7,
                    textColor=self.COLOR_BLUE,
                    fontName='Helvetica-Bold',
                    alignment=TA_CENTER,
                )))
                card_elements.append(Spacer(1, 0.02*inch))
            # Bags table (left side)
            if route_sheet.bags:
                card_elements.append(Paragraph("<b>Bags</b>", ParagraphStyle(
                    name='ManifestHeader',
                    fontSize=7,
                    textColor=self.COLOR_BLUE,
                    fontName='Helvetica-Bold',
                )))
                
                bag_data = [
                    [Paragraph("Bag ID", self.styles['TableHeader'])],
                ]
                
                for bag in route_sheet.bags[:8]:  # Max 8 bags to fit
                    # Format: "GRN 056" (color abbreviation + last 3 digits of bag code)
                    color_abbr = self._color_to_abbreviation(bag.color_code)
                    # Extract numeric part from bag_id (e.g., "GRN056" -> "056")
                    bag_num = ''.join(c for c in bag.bag_id if c.isdigit()) or bag.bag_id
                    bag_display = f"{color_abbr} {bag_num}"
                    bag_data.append([
                        Paragraph(bag_display, self.styles['TableCell']),
                    ])
                
                bags_table = Table(bag_data, colWidths=[2.0*inch])
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
                card_elements.append(bags_table)
                card_elements.append(Spacer(1, 0.03*inch))
            
            # Overflow table (right side)
            if route_sheet.overflow:
                card_elements.append(Paragraph("<b>OV</b>", ParagraphStyle(
                    name='OverflowHeader',
                    fontSize=7,
                    textColor=self.COLOR_BLUE,
                    fontName='Helvetica-Bold',
                )))
                
                overflow_data = [
                    [Paragraph("Zone", self.styles['TableHeader']),
                     Paragraph("Qty", self.styles['TableHeader'])],
                ]
                
                for overflow in route_sheet.overflow[:8]:  # Max 8 overflow to fit
                    overflow_data.append([
                        Paragraph(overflow.sort_zone, self.styles['TableCell']),
                        Paragraph(str(overflow.package_count), self.styles['TableCell']),
                    ])
                
                overflow_table = Table(overflow_data, colWidths=[0.7*inch, 0.6*inch])
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
                card_elements.append(overflow_table)
        
        # Create card container table
        card_data = [[Spacer(1, 0.04*inch)] for _ in card_elements]
        for i, element in enumerate(card_elements):
            card_data[i] = [element]
        
        card_table = Table(card_data, colWidths=[self.CARD_WIDTH - 0.1*inch])
        card_table.setStyle(TableStyle([
            ('BORDER', (0, 0), (-1, -1), 1.5, self.COLOR_BLUE),
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        
        return card_table
