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
    
    # Layout constants
    CARD_WIDTH = 4.5 * inch
    CARD_HEIGHT = 6.5 * inch
    MARGIN = 0.35 * inch
    CARD_SPACING = 0.2 * inch
    
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
            fontSize=11,
            textColor=self.COLOR_BLUE,
            fontName='Helvetica-Bold',
            spaceAfter=6,
        ))
        
        # Card title style (route code, large, bold)
        self.styles.add(ParagraphStyle(
            name='CardTitle',
            parent=self.styles['Normal'],
            fontSize=14,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            spaceAfter=4,
        ))
        
        # Card label style (small, gray)
        self.styles.add(ParagraphStyle(
            name='CardLabel',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor("#666666"),
            fontName='Helvetica',
            spaceAfter=2,
        ))
        
        # Card value style (regular, bold)
        self.styles.add(ParagraphStyle(
            name='CardValue',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=self.COLOR_BLACK,
            fontName='Helvetica-Bold',
            spaceAfter=3,
        ))
        
        # Table header style
        self.styles.add(ParagraphStyle(
            name='TableHeader',
            parent=self.styles['Normal'],
            fontSize=7,
            textColor=colors.whitesmoke,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
        ))
        
        # Table cell style
        self.styles.add(ParagraphStyle(
            name='TableCell',
            parent=self.styles['Normal'],
            fontSize=7,
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
        Generate driver handout PDF with 2x2 card layout.
        
        Args:
            assignments: Dictionary of route_code → RouteAssignment
            route_sheets: List of RouteSheet objects with load manifest
            output_path: Path to save PDF output
        
        Returns:
            Path to generated PDF
        """
        # Build route lookup
        route_lookup = {sheet.route_code: sheet for sheet in route_sheets}
        
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
        
        # Group assignments into rows of 2 for 2x2 layout
        assignment_list = list(assignments.items())
        for i in range(0, len(assignment_list), 2):
            if i > 0:  # Page break before new row (except first)
                story.append(PageBreak())
            
            row_assignments = assignment_list[i:i+2]
            story.append(self._build_card_row(row_assignments, route_lookup))
        
        # Build PDF
        doc.build(story)
        return output_path
    
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
        """Build a single 4.5\" x 6.5\" handout card."""
        card_elements = []
        
        # --- Header Section ---
        header_data = [
            [Paragraph(assignment.dsp or "NDAY", self.styles['CardHeader'])],
        ]
        header_table = Table(header_data, colWidths=[self.CARD_WIDTH - 0.1*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_LIGHT_GRAY),
            ('LINEBELOW', (0, 0), (-1, -1), 1.5, self.COLOR_BLUE),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        card_elements.append(header_table)
        card_elements.append(Spacer(1, 0.08*inch))
        
        # --- Route Code (Large) ---
        route_title = [
            [Paragraph(assignment.route_code, self.styles['CardTitle'])],
        ]
        route_table = Table(route_title, colWidths=[self.CARD_WIDTH - 0.1*inch])
        route_table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        card_elements.append(route_table)
        card_elements.append(Spacer(1, 0.06*inch))
        
        # --- Service Type ---
        service_text = f"<b>Service:</b> {assignment.service_type}"
        card_elements.append(Paragraph(service_text, self.styles['CardLabel']))
        card_elements.append(Spacer(1, 0.04*inch))
        
        # --- Driver & Vehicle ---
        driver_text = f"<b>Driver:</b> {assignment.driver_name or 'TBD'}"
        card_elements.append(Paragraph(driver_text, self.styles['CardLabel']))
        
        vehicle_text = f"<b>Vehicle:</b> {assignment.vehicle_name}"
        card_elements.append(Paragraph(vehicle_text, self.styles['CardLabel']))
        card_elements.append(Spacer(1, 0.08*inch))
        
        # --- Load Manifest (Bags & Overflow) ---
        if route_sheet:
            # Bags table (left side)
            if route_sheet.bags:
                card_elements.append(Paragraph("<b>Bags</b>", ParagraphStyle(
                    name='ManifestHeader',
                    fontSize=8,
                    textColor=self.COLOR_BLUE,
                    fontName='Helvetica-Bold',
                )))
                
                bag_data = [
                    [Paragraph("Zone", self.styles['TableHeader']),
                     Paragraph("Color", self.styles['TableHeader']),
                     Paragraph("Qty", self.styles['TableHeader'])],
                ]
                
                for bag in route_sheet.bags[:4]:  # Max 4 bags to fit
                    bag_data.append([
                        Paragraph(bag.sort_zone, self.styles['TableCell']),
                        Paragraph(bag.color_code[:3], self.styles['TableCell']),  # Truncate
                        Paragraph(str(bag.package_count), self.styles['TableCell']),
                    ])
                
                bags_table = Table(bag_data, colWidths=[0.9*inch, 1.2*inch, 0.7*inch])
                bags_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 7),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))
                card_elements.append(bags_table)
                card_elements.append(Spacer(1, 0.06*inch))
            
            # Overflow table (right side)
            if route_sheet.overflow:
                card_elements.append(Paragraph("<b>Overflow</b>", ParagraphStyle(
                    name='OverflowHeader',
                    fontSize=8,
                    textColor=self.COLOR_BLUE,
                    fontName='Helvetica-Bold',
                )))
                
                overflow_data = [
                    [Paragraph("Zone", self.styles['TableHeader']),
                     Paragraph("Items", self.styles['TableHeader'])],
                ]
                
                for overflow in route_sheet.overflow[:4]:  # Max 4 overflow to fit
                    overflow_data.append([
                        Paragraph(overflow.sort_zone, self.styles['TableCell']),
                        Paragraph(str(overflow.package_count), self.styles['TableCell']),
                    ])
                
                overflow_table = Table(overflow_data, colWidths=[1.2*inch, 0.9*inch])
                overflow_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_BLUE),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('BACKGROUND', (0, 1), (-1, -1), self.COLOR_LIGHT_GRAY),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 7),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
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
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        
        return card_table
