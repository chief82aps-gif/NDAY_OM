# ...existing code...

# Place this after Base and engine are defined

"""
SQLAlchemy ORM Models for NDAY Route Manager

Maps Python classes to PostgreSQL database tables.
"""

from datetime import datetime, date, time
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Date, Time,
    DECIMAL, ForeignKey, Text, TIMESTAMP, JSON, Index, func, text, Float
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import os

# Import permissions for role validation
from api.src.permissions import Role

# Database URL
# Priority:
# 1) Explicit DATABASE_URL from hosting platform (Render/managed DB)
# 2) Local fallback to sqlite for environments without a provisioned DB yet
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///./nday_om.db'

# Create base class for all models
Base = declarative_base()

# Create engine
if DATABASE_URL.startswith('sqlite'):
    engine = create_engine(
        DATABASE_URL,
        connect_args={'check_same_thread': False},
        pool_pre_ping=True,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
    )

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ============================================================================
# USER & AUTHENTICATION
# ============================================================================

class User(Base):
    """
    System users with role-based access control.
    
    Roles (hierarchy from most to least access):
    - admin: Full system access including code editing
    - manager: Financial data access, reporting, no code editing
    - dispatcher: Route/assignment management, no financial access
    - driver: Driver portal only, assignment viewing
    
    See api/src/permissions.py for detailed permission matrix.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100))
    email = Column(String(100))
    role = Column(String(20), nullable=False, default='driver')  
    # Valid roles: 'admin', 'manager', 'dispatcher', 'driver'
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime)

    # Relationships
    driver = relationship("Driver", back_populates="user", uselist=False)
    rescues_approved = relationship("Rescue", foreign_keys="Rescue.approved_by", back_populates="approver")
    inspections = relationship("VanInspection", back_populates="inspector")

    def __repr__(self):
        return f"<User(username={self.username}, role={self.role})>"
    
    def has_financial_access(self) -> bool:
        """Check if user can access financial data"""
        return self.role in {Role.ADMIN.value, Role.MANAGER.value}
    
    def can_manage_assignments(self) -> bool:
        """Check if user can manage vehicle assignments"""
        return self.role in {Role.ADMIN.value, Role.MANAGER.value, Role.DISPATCHER.value}


class Driver(Base):
    """Driver profiles"""
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    employee_id = Column(String(50), unique=True)
    phone = Column(String(20))
    hire_date = Column(Date)
    status = Column(String(20))  # 'active', 'inactive', 'on_leave'
    experience_level = Column(String(20))  # 'new', 'intermediate', 'experienced'
    preferred_zones = Column(JSON)  # ['A', 'B', 'E']
    license_expiry = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="driver")
    assignments = relationship("Assignment", back_populates="driver")
    metrics = relationship("PerformanceMetric", back_populates="driver")
    incidents = relationship("Incident", back_populates="driver")
    rescues = relationship("Rescue", back_populates="driver")

    def __repr__(self):
        return f"<Driver(employee_id={self.employee_id})>"


# ============================================================================
# FLEET & VEHICLES
# ============================================================================

class Vehicle(Base):
    """Fleet vehicles (vans)"""
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True)
    vin = Column(String(50), unique=True, nullable=False, index=True)
    vehicle_name = Column(String(100))  # e.g., "1901 XL"
    service_type = Column(String(50))  # 'Standard Parcel', 'Oversized', 'Electric'
    capacity_cubic_feet = Column(DECIMAL(10, 2))
    capacity_weight_lbs = Column(DECIMAL(10, 2))
    status = Column(String(20))  # 'active', 'grounded', 'maintenance'
    is_electric = Column(Boolean, default=False)
    acquisition_date = Column(Date)
    mileage_current = Column(Integer)
    last_maintenance_date = Column(Date)
    next_maintenance_due = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assignments = relationship("Assignment", back_populates="vehicle")
    incidents = relationship("Incident", back_populates="vehicle")
    inspections = relationship("VanInspection", back_populates="vehicle")

    __table_args__ = (
        Index('idx_vehicles_status', 'status'),
    )

    def __repr__(self):
        return f"<Vehicle(vin={self.vin}, status={self.status})>"


# ============================================================================
# ASSIGNMENTS & ROUTES
# ============================================================================

class Assignment(Base):
    """Route assignments to drivers and vehicles"""
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(String(50), unique=True, nullable=False)  # e.g., "CX139"
    route_code = Column(String(50), nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    service_type = Column(String(50))
    wave_time = Column(Time)
    scheduled_show_time = Column(Time)
    actual_show_time = Column(Time)
    scheduled_return_time = Column(Time)
    actual_return_time = Column(Time)
    zone = Column(String(10))
    is_sweeper = Column(Boolean, default=False)
    assignment_date = Column(Date, nullable=False, index=True)
    status = Column(String(20))  # 'pending', 'assigned', 'in_progress', 'completed', 'tbd'
    tbd_reason = Column(Text)  # Why it's TBD
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("Driver", back_populates="assignments")
    vehicle = relationship("Vehicle", back_populates="assignments")
    incidents = relationship("Incident", back_populates="assignment")

    __table_args__ = (
        Index('idx_assignment_lookup', 'driver_id', 'assignment_date', 'status'),
        Index('idx_vehicle_assignment', 'vehicle_id', 'assignment_date'),
    )

    def __repr__(self):
        return f"<Assignment(assignment_id={self.assignment_id}, status={self.status})>"


class PerformanceMetric(Base):
    """Driver KPIs and performance tracking"""
    __tablename__ = "performance_metrics"

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"))
    metric_date = Column(Date, nullable=False)
    assignments_scheduled = Column(Integer, default=0)
    assignments_completed = Column(Integer, default=0)
    on_time_count = Column(Integer, default=0)
    late_count = Column(Integer, default=0)
    on_time_percentage = Column(DECIMAL(5, 2))
    total_packages = Column(Integer, default=0)
    total_weight_lbs = Column(DECIMAL(12, 2))
    customer_rating_avg = Column(DECIMAL(3, 2))
    rescues_performed = Column(Integer, default=0)
    safety_incidents = Column(Integer, default=0)
    communication_score = Column(String(20))  # 'excellent', 'good', 'fair', 'poor'
    efficiency_score = Column(String(20))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("Driver", back_populates="metrics")

    __table_args__ = (
        Index('idx_metrics_lookup', 'driver_id', 'metric_date'),
    )

    def __repr__(self):
        return f"<PerformanceMetric(driver_id={self.driver_id}, date={self.metric_date})>"


# ============================================================================
# INCIDENTS & SAFETY
# ============================================================================

class Incident(Base):
    """Accident, damage, and safety reports"""
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(50), unique=True, nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    assignment_id = Column(Integer, ForeignKey("assignments.id"))
    incident_type = Column(String(50))  # 'accident', 'damage', 'safety', 'complaint'
    severity_level = Column(String(20))  # 'low', 'medium', 'high', 'critical'
    incident_date = Column(TIMESTAMP, nullable=False)
    location = Column(String(255))
    latitude = Column(DECIMAL(10, 8))
    longitude = Column(DECIMAL(11, 8))
    description = Column(Text)
    driver_statement = Column(Text)
    is_reported_to_insurance = Column(Boolean, default=False)
    requires_follow_up = Column(Boolean, default=False)
    status = Column(String(20))  # 'reported', 'under_review', 'resolved'
    resolution_notes = Column(Text)
    photos_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime)

    # Relationships
    driver = relationship("Driver", back_populates="incidents")
    vehicle = relationship("Vehicle", back_populates="incidents")
    assignment = relationship("Assignment", back_populates="incidents")
    photos = relationship("IncidentPhoto", back_populates="incident", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_incident_lookup', 'driver_id', 'incident_date'),
        Index('idx_incident_severity', 'severity_level'),
    )

    def __repr__(self):
        return f"<Incident(incident_id={self.incident_id}, type={self.incident_type})>"


class IncidentPhoto(Base):
    """Photo evidence for incidents"""
    __tablename__ = "incident_photos"

    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"))
    photo_url = Column(String(500))  # S3 URL
    photo_key = Column(String(255))  # S3 object key
    caption = Column(Text)
    upload_date = Column(DateTime, default=datetime.utcnow)

    # Relationships
    incident = relationship("Incident", back_populates="photos")

    def __repr__(self):
        return f"<IncidentPhoto(incident_id={self.incident_id})>"


class Rescue(Base):
    """Rescue tracking for bonuses"""
    __tablename__ = "rescues"

    id = Column(Integer, primary_key=True)
    rescue_id = Column(String(50), unique=True, nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    rescue_date = Column(Date, nullable=False)
    rescue_type = Column(String(50))  # 'traffic_control', 'assistance', etc.
    location = Column(String(255))
    description = Column(Text)
    bonus_amount = Column(DECIMAL(10, 2))
    bonus_status = Column(String(20))  # 'pending', 'approved', 'paid'
    approval_date = Column(Date)
    approved_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("Driver", back_populates="rescues")
    approver = relationship("User", foreign_keys=[approved_by], back_populates="rescues_approved")

    __table_args__ = (
        Index('idx_rescue_lookup', 'driver_id', 'rescue_date'),
    )

    def __repr__(self):
        return f"<Rescue(rescue_id={self.rescue_id}, status={self.bonus_status})>"


class VanInspection(Base):
    """Vehicle condition checks"""
    __tablename__ = "van_inspections"

    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    inspection_date = Column(TIMESTAMP, nullable=False)
    inspector_user_id = Column(Integer, ForeignKey("users.id"))
    inspection_type = Column(String(20))  # 'pre_shift', 'post_shift', 'maintenance'
    overall_condition = Column(String(20))  # 'excellent', 'good', 'fair', 'poor'
    fuel_level = Column(Integer)  # 0-100 percentage
    cleanliness_score = Column(Integer)  # 1-10
    damage_areas = Column(JSON)  # Array of damage descriptions
    notes = Column(Text)
    photos_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    vehicle = relationship("Vehicle", back_populates="inspections")
    inspector = relationship("User", back_populates="inspections")

    __table_args__ = (
        Index('idx_inspection_lookup', 'vehicle_id', 'inspection_date'),
    )

    def __repr__(self):
        return f"<VanInspection(vehicle_id={self.vehicle_id}, date={self.inspection_date})>"


class RouteSheet(Base):
    """Uploaded route sheets"""
    __tablename__ = "route_sheets"

    id = Column(Integer, primary_key=True)
    upload_date = Column(Date, nullable=False, index=True)
    file_name = Column(String(255))
    file_size = Column(Integer)
    s3_location = Column(String(500))
    total_routes = Column(Integer)
    total_assignments = Column(Integer)
    processing_status = Column(String(20))  # 'pending', 'processed', 'error'
    error_message = Column(Text)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)

    def __repr__(self):
        return f"<RouteSheet(file={self.file_name}, status={self.processing_status})>"


# ============================================================================
# INVOICES
# ============================================================================

class VariableInvoice(Base):
    """Weekly variable invoice from Amazon"""
    __tablename__ = "variable_invoices"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), unique=True, index=True, nullable=False)
    amazon_unique_id = Column(String(100), index=True)
    invoice_date = Column(Date)
    period_start = Column(Date)
    period_end = Column(Date)
    station = Column(String(50))
    subtotal = Column(DECIMAL(12, 2))
    tax_rate = Column(DECIMAL(6, 2))
    tax_due = Column(DECIMAL(12, 2))
    total_due = Column(DECIMAL(12, 2))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    line_items = relationship(
        "VariableInvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_variable_invoice_date', 'invoice_date'),
    )

    def __repr__(self):
        return f"<VariableInvoice(invoice_number={self.invoice_number})>"


class VariableInvoiceLineItem(Base):
    """Aggregated line items for weekly variable invoices"""
    __tablename__ = "variable_invoice_line_items"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("variable_invoices.id", ondelete="CASCADE"))
    description = Column(String(255), nullable=False)
    rate = Column(DECIMAL(12, 2))
    quantity = Column(DECIMAL(12, 2))
    amount = Column(DECIMAL(12, 2))
    instance_count = Column(Integer, default=1)

    invoice = relationship("VariableInvoice", back_populates="line_items")

    def __repr__(self):
        return f"<VariableInvoiceLineItem(desc={self.description}, amount={self.amount})>"


class InvoiceAuditMapping(Base):
    """Mapping from invoice line descriptions to WST audit metrics."""
    __tablename__ = "invoice_audit_mappings"

    id = Column(Integer, primary_key=True)
    description = Column(String(255), nullable=False)
    description_normalized = Column(String(255), nullable=False, unique=True, index=True)
    metric_key = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# WST (WORK SUMMARY TOOL) REPORTS
# ============================================================================

class WstDeliveredPackages(Base):
    """Daily delivered packages totals (WST)"""
    __tablename__ = "wst_delivered_packages"

    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False, index=True)
    station = Column(String(100))
    dsp_short_code = Column(String(20))
    package_count = Column(Integer)
    package_type = Column(String(100))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_wst_delivered_date_type', 'report_date', 'package_type'),
    )


class WstServiceDetails(Base):
    """Route-level daily work summary (WST)"""
    __tablename__ = "wst_service_details"

    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False, index=True)
    station = Column(String(100))
    dsp_short_code = Column(String(20))
    delivery_associate = Column(String(100))
    route_code = Column(String(50))
    service_type = Column(String(100))
    planned_duration = Column(String(20))
    log_in = Column(DateTime)
    log_out = Column(DateTime)
    total_distance_planned = Column(DECIMAL(12, 2))
    total_distance_allowance = Column(DECIMAL(12, 2))
    distance_unit = Column(String(20))
    shipments_delivered = Column(Integer)
    shipments_returned = Column(Integer)
    pickup_packages = Column(Integer)
    excluded = Column(Boolean, default=False)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_wst_service_date_route', 'report_date', 'route_code'),
    )


class WstTrainingWeekly(Base):
    """Training weekly summary (WST)"""
    __tablename__ = "wst_training_weekly"

    id = Column(Integer, primary_key=True)
    assignment_date = Column(Date, nullable=False, index=True)
    payment_date = Column(Date)
    station = Column(String(100))
    dsp_short_code = Column(String(20))
    delivery_associate = Column(String(100))
    service_type = Column(String(50))
    course_name = Column(String(255))
    dsp_payment_eligible = Column(Boolean)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class WstUnplannedDelay(Base):
    """Unplanned delay weekly summary (WST)"""
    __tablename__ = "wst_unplanned_delay"

    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False, index=True)
    station = Column(String(100))
    dsp_short_code = Column(String(20))
    delay_reason = Column(String(255))
    total_delay_minutes = Column(DECIMAL(10, 2))
    impacted_routes = Column(Integer)
    notes = Column(Text)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class WstWeeklyReport(Base):
    """Weekly summary of routes and cancellations (WST)"""
    __tablename__ = "wst_weekly_report"

    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False, index=True)
    station = Column(String(100))
    dsp_short_code = Column(String(20))
    service_type = Column(String(100))
    planned_duration = Column(String(20))
    total_distance_planned = Column(DECIMAL(12, 2))
    total_distance_allowance = Column(DECIMAL(12, 2))
    planned_distance_unit = Column(String(20))
    amzl_late_cancel = Column(DECIMAL(10, 2))
    dsp_late_cancel = Column(DECIMAL(10, 2))
    quick_coverage_accepted = Column(DECIMAL(10, 2))
    completed_routes = Column(Integer)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class ServiceTypeAlias(Base):
    """Canonical service type aliases across all ingest sources."""
    __tablename__ = "service_type_aliases"

    canonical_key = Column(String(100), primary_key=True)
    canonical_display = Column(String(255), nullable=False)

    dop_aliases = Column(JSON, default=list, nullable=True)
    fleet_aliases = Column(JSON, default=list, nullable=True)
    invoice_aliases = Column(JSON, default=list, nullable=True)
    wst_aliases = Column(JSON, default=list, nullable=True)
    cortex_aliases = Column(JSON, default=list, nullable=True)
    schedule_aliases = Column(JSON, default=list, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ServiceTypeAlias(canonical_key={self.canonical_key})>"


# ============================================================================
# WEEKLY INCENTIVE
# ============================================================================

class WeeklyIncentiveInvoice(Base):
    """Weekly incentive invoice based on scorecard rating"""
    __tablename__ = "weekly_incentive_invoices"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), unique=True, index=True, nullable=False)
    week_start = Column(Date)
    week_end = Column(Date)
    rating = Column(String(30))
    total_packages = Column(Integer)
    rate_applied = Column(DECIMAL(6, 2))
    calculated_amount = Column(DECIMAL(12, 2))
    invoice_amount = Column(DECIMAL(12, 2))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# FLEET INVOICE (MONTHLY)
# ============================================================================

class FleetInvoice(Base):
    """Monthly fleet invoice header"""
    __tablename__ = "fleet_invoices"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), unique=True, index=True, nullable=False)
    invoice_date = Column(Date)
    period_start = Column(Date)
    period_end = Column(Date)
    station = Column(String(50))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    entries = relationship(
        "FleetInvoiceEntry",
        back_populates="invoice",
        cascade="all, delete-orphan"
    )


class FleetInvoiceEntry(Base):
    """Fleet invoice line entries (prepayment, reconciliation, weekly)"""
    __tablename__ = "fleet_invoice_entries"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("fleet_invoices.id", ondelete="CASCADE"))
    category = Column(String(30))  # 'prepayment', 'reconciliation', 'weekly'
    description = Column(String(255))
    van_type = Column(String(100))
    rate = Column(DECIMAL(12, 2))
    quantity = Column(DECIMAL(12, 2))
    amount = Column(DECIMAL(12, 2))
    week_start = Column(Date)
    week_end = Column(Date)
    notes = Column(Text)

    invoice = relationship("FleetInvoice", back_populates="entries")


# ============================================================================
# DSP SCORECARD
# ============================================================================

class DspScorecardSummary(Base):
    """Weekly DSP scorecard summary"""
    __tablename__ = "dsp_scorecard_summaries"

    id = Column(Integer, primary_key=True)
    week_start = Column(Date)
    week_end = Column(Date)
    station = Column(String(50))
    overall_rating = Column(String(30))
    safety_rating = Column(String(30))
    delivery_quality_rating = Column(String(30))
    pickup_quality_rating = Column(String(30))
    team_fleet_rating = Column(String(30))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    drivers = relationship(
        "DspScorecardDriver",
        back_populates="summary",
        cascade="all, delete-orphan"
    )


class DspScorecardDriver(Base):
    """Weekly DSP scorecard driver metrics"""
    __tablename__ = "dsp_scorecard_drivers"

    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("dsp_scorecard_summaries.id", ondelete="CASCADE"))
    driver_name = Column(String(100))
    transporter_id = Column(String(50), index=True)
    packages_delivered = Column(Integer)
    seatbelt_off_rate = Column(DECIMAL(10, 4))
    speeding_event_rate = Column(DECIMAL(10, 4))
    distraction_rate = Column(DECIMAL(10, 4))
    following_distance_rate = Column(DECIMAL(10, 4))
    stop_sign_rate = Column(DECIMAL(10, 4))
    cdf_dpmo = Column(DECIMAL(12, 4))
    ced = Column(Integer)
    dcr = Column(DECIMAL(6, 3))
    dsb_dpmo = Column(DECIMAL(12, 4))
    pod_accept_rate = Column(DECIMAL(6, 3))
    pod_opportunities = Column(Integer)
    dsb_count = Column(Integer)
    psb_rate = Column(DECIMAL(6, 3))

    summary = relationship("DspScorecardSummary", back_populates="drivers")


# ============================================================================
# POD REPORT
# ============================================================================

class PodReportSummary(Base):
    """Weekly POD acceptance summary"""
    __tablename__ = "pod_report_summaries"

    id = Column(Integer, primary_key=True)
    week_start = Column(Date)
    week_end = Column(Date)
    station = Column(String(50))
    total_opportunities = Column(Integer)
    accepted_count = Column(Integer)
    rejected_count = Column(Integer)
    bypassed_count = Column(Integer)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    reasons = relationship(
        "PodReportRejectReason",
        back_populates="summary",
        cascade="all, delete-orphan"
    )
    drivers = relationship(
        "PodReportDriver",
        back_populates="summary",
        cascade="all, delete-orphan"
    )


class PodReportRejectReason(Base):
    """POD reject reason counts"""
    __tablename__ = "pod_report_reject_reasons"

    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("pod_report_summaries.id", ondelete="CASCADE"))
    reason = Column(String(100))
    reject_count = Column(Integer)

    summary = relationship("PodReportSummary", back_populates="reasons")


class PodReportDriver(Base):
    """POD driver-level detail"""
    __tablename__ = "pod_report_drivers"

    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("pod_report_summaries.id", ondelete="CASCADE"))
    driver_name = Column(String(100))
    total_opportunities = Column(Integer)
    accepted_count = Column(Integer)
    bypassed_count = Column(Integer)
    rejected_count = Column(Integer)
    rejected_reason_counts = Column(JSON)

    summary = relationship("PodReportSummary", back_populates="drivers")


# ============================================================================
# CORTEX (ROUTE ASSIGNMENTS)
# ============================================================================

class Cortex(Base):
    """Cortex route assignment data (planned/historical)"""
    __tablename__ = "cortex_routes"

    id = Column(Integer, primary_key=True)
    assignment_date = Column(Date, index=True)
    station = Column(String(50), index=True)
    dsp_code = Column(String(100), index=True)
    route_code = Column(String(50), index=True)
    wave = Column(String(20))
    packages = Column(Integer)
    commercial_pct = Column(DECIMAL(10, 2))
    zone = Column(String(50))
    service_type = Column(String(255))
    driver_name = Column(String(255), index=True)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# DOP (DAY OF PLAN)
# ============================================================================

class DOP(Base):
    """DOP (Day of Plan) route scheduling data"""
    __tablename__ = "dop_routes"

    id = Column(Integer, primary_key=True)
    schedule_date = Column(Date, index=True)
    station = Column(String(50), index=True)
    dsp_code = Column(String(100), index=True)
    route_code = Column(String(50), index=True)
    wave = Column(String(20))
    planned_packages = Column(Integer)
    commercial_pct = Column(DECIMAL(10, 2))
    zone = Column(String(50))
    service_type = Column(String(255))
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class UploadRetentionRecord(Base):
    """Archive metadata/payload for uploaded files with explicit retention window."""
    __tablename__ = "upload_retention_records"

    id = Column(Integer, primary_key=True)
    upload_type = Column(String(100), nullable=False, index=True)
    source_file = Column(String(255), nullable=False, index=True)
    record_count = Column(Integer, default=0)
    payload = Column(JSON)
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)
    retain_until = Column(DateTime, nullable=False, index=True)


class AuditMismatchReview(Base):
    """Manager review decisions for invoice audit mismatches."""
    __tablename__ = "audit_mismatch_reviews"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False, index=True)
    mismatch_key = Column(String(64), nullable=False, index=True)
    line_description = Column(Text)
    action_status = Column(String(40), nullable=False, default="pending", index=True)
    manager_note = Column(Text)
    dispute_portal_reference = Column(String(255))
    dispute_verified = Column(Boolean, default=False)
    reviewed_role = Column(String(40))
    reviewed_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class AuditRouteReview(Base):
    """User confirmation decisions for Cortex routes not found in DOP."""
    __tablename__ = "audit_route_reviews"

    id = Column(Integer, primary_key=True)
    audit_date = Column(Date, nullable=False, index=True)
    station = Column(String(50), index=True)
    invoice_number = Column(String(100), index=True)
    route_code = Column(String(50), nullable=False, index=True)
    action_status = Column(String(40), nullable=False, default="pending", index=True)
    manager_note = Column(Text)
    reviewed_role = Column(String(40))
    reviewed_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


# ============================================================================
# WEEKLY INVOICE AUDIT
# ============================================================================

class WeeklyInvoiceAudit(Base):
    """Weekly invoice audit comparing WST weekly export to invoice export."""
    __tablename__ = "weekly_invoice_audits"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False, index=True)
    audit_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    station = Column(String(50), nullable=False, index=True)
    dsp_short_code = Column(String(20), nullable=True, index=True)
    
    # WST snapshot
    wst_completed_routes = Column(Integer, nullable=True)
    wst_distance_planned = Column(DECIMAL(12, 2), nullable=True)
    wst_distance_allowance = Column(DECIMAL(12, 2), nullable=True)
    wst_amzl_late_cancel = Column(DECIMAL(10, 2), nullable=True)
    wst_dsp_late_cancel = Column(DECIMAL(10, 2), nullable=True)
    wst_quick_coverage_accepted = Column(DECIMAL(10, 2), nullable=True)
    
    # Invoice snapshot
    invoice_total_quantity = Column(DECIMAL(12, 2), nullable=True)
    invoice_subtotal = Column(DECIMAL(12, 2), nullable=True)
    invoice_total_due = Column(DECIMAL(12, 2), nullable=True)
    
    # Comparison results
    total_lines = Column(Integer, default=0)
    matched_lines = Column(Integer, default=0)
    variance_lines = Column(Integer, default=0)
    unmatched_lines = Column(Integer, default=0)
    aligned = Column(Boolean, default=False)
    
    # Issues
    critical_issues = Column(JSON, default=[], nullable=True)
    warnings = Column(JSON, default=[], nullable=True)
    
    # Detailed comparison results
    comparison_details = Column(JSON, default={}, nullable=True)
    
    # Approval tracking
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    approval_status = Column(String(40), default="pending", index=True)  # pending, approved, disputed
    approval_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_weekly_audit_invoice_date', 'invoice_number', 'audit_date'),
        Index('idx_weekly_audit_period', 'period_start', 'period_end', 'station'),
    )

    def __repr__(self):
        return f"<WeeklyInvoiceAudit(invoice={self.invoice_number}, period={self.period_start}...{self.period_end})>"


class WeeklyAuditLineItem(Base):
    """Detailed comparison for each invoice line in weekly audit."""
    __tablename__ = "weekly_audit_line_items"

    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("weekly_invoice_audits.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Invoice line
    invoice_description = Column(String(255), nullable=False)
    invoice_quantity = Column(DECIMAL(12, 2), nullable=False)
    invoice_rate = Column(DECIMAL(12, 2), nullable=True)
    invoice_amount = Column(DECIMAL(12, 2), nullable=True)
    
    # Classification
    category = Column(String(50), nullable=False)  # route, service_type, cancellation, distance, other
    subcategory = Column(String(100), nullable=True)
    
    # Matching
    matched_metric = Column(String(100), nullable=True)
    wst_expected_value = Column(DECIMAL(12, 2), nullable=True)
    variance = Column(DECIMAL(12, 2), nullable=True)
    
    # Status
    status = Column(String(40), default="pending", nullable=False)  # matched, variance, unmatched
    issues = Column(JSON, default=[], nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_weekly_audit_line_audit', 'audit_id'),
        Index('idx_weekly_audit_line_status', 'audit_id', 'status'),
    )

    def __repr__(self):
        return f"<WeeklyAuditLineItem(audit={self.audit_id}, desc={self.invoice_description}, status={self.status})>"


class ApprovedAudit(Base):
    """Stores approved audit submissions for daily screenshot audits."""
    __tablename__ = "approved_audits"

    id = Column(Integer, primary_key=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    station = Column(String(50), index=True)
    audit_date = Column(Date, nullable=False, index=True)
    cortex_raw = Column(Text, nullable=False)
    wst_raw = Column(Text, nullable=False)
    submitted_by = Column(String(100), default="", nullable=True)
    notes = Column(Text, default="", nullable=True)
    variance_responses = Column(JSON, default={}, nullable=True)
    
    # New fields for enhanced audit tracking
    cortex_route_count = Column(Integer, nullable=True)
    wst_route_count = Column(Integer, nullable=True)
    cortex_package_count = Column(Integer, nullable=True)
    wst_package_count = Column(Integer, nullable=True)
    training_routes_count = Column(Integer, default=0, nullable=True)
    excluded_services = Column(JSON, default=[], nullable=True)
    disputes_json = Column(JSON, default=[], nullable=True)
    dispute_summary = Column(Text, nullable=True, default="")
    da_route_stats = Column(JSON, default={}, nullable=True)

    def __repr__(self):
        return f"<ApprovedAudit(id={self.id}, audit_date={self.audit_date}, station={self.station})>"


class AuditDispute(Base):
    """Tracks disputes raised during daily screenshot audits."""
    __tablename__ = "audit_disputes"

    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("approved_audits.id"), index=True)
    approved_audit_date = Column(Date, nullable=False, index=True)
    
    # Dispute details
    dispute_type = Column(String(50), nullable=False)  # route_count, package_count, excluded_service, training, etc.
    variance_metric = Column(String(100), nullable=True)  # e.g., "route_count", "delivered_packages"
    cortex_value = Column(Integer, nullable=True)
    wst_value = Column(Integer, nullable=True)
    variance_amount = Column(Integer, nullable=True)
    
    # User-provided response
    user_input_reason = Column(Text, nullable=False)
    dispute_status = Column(String(40), nullable=False)  # acknowledged, dispute_submitted
    
    # System-generated
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_by = Column(String(100), default="", nullable=True)
    
    def __repr__(self):
        return f"<AuditDispute(id={self.id}, type={self.dispute_type}, date={self.approved_audit_date})>"


class AuditDARouteStats(Base):
    """Tracks DA name, route code, and average stops per hour."""
    __tablename__ = "audit_da_route_stats"

    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("approved_audits.id"), index=True)
    approved_audit_date = Column(Date, nullable=False, index=True)
    
    # DA and Route Information
    driver_name = Column(String(255), nullable=False, index=True)
    route_code = Column(String(100), nullable=False, index=True)
    service_type = Column(String(255), nullable=True)
    
    # Performance metrics
    completed_stops = Column(Integer, nullable=True)
    total_stops = Column(Integer, nullable=True)
    completed_deliveries = Column(Integer, nullable=True)
    total_deliveries = Column(Integer, nullable=True)
    
    # Calculated
    avg_stops_per_hour = Column(Float, nullable=True)
    route_efficiency = Column(Float, nullable=True)  # percentage
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<AuditDARouteStats(driver={self.driver_name}, route={self.route_code}, stops/hr={self.avg_stops_per_hour})>"


class ServiceTypeLibrary(Base):
    """Stores canonical service type definitions for matching."""
    __tablename__ = "service_type_library"

    id = Column(Integer, primary_key=True)
    service_key = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=True)
    
    # Pattern matching
    regex_patterns = Column(JSON, default=[], nullable=True)  # List of regex patterns for matching
    code_pattern = Column(String(255), nullable=True)
    
    # Metadata
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<ServiceTypeLibrary(key={self.service_key}, name={self.display_name})>"


# ============================================================================
# WEEKLY AUDIT UPLOAD & VALIDATION
# ============================================================================

class UploadedFile(Base):
    """Tracks uploaded WST and Invoice files."""
    __tablename__ = "uploaded_files"
    
    id = Column(Integer, primary_key=True)
    file_type = Column(String(50), nullable=False)  # 'wst' or 'invoice'
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)  # bytes
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    station = Column(String(50), nullable=True, index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    uploaded_by = Column(String(100), nullable=True)
    parse_status = Column(String(50), default='pending')  # pending, completed, failed
    parse_error = Column(Text, nullable=True)
    record_count = Column(Integer, nullable=True)
    
    __table_args__ = (
        Index('idx_uploaded_file_period', 'period_start', 'period_end'),
        Index('idx_uploaded_file_type_date', 'file_type', 'uploaded_at'),
    )
    
    def __repr__(self):
        return f"<UploadedFile(type={self.file_type}, file={self.filename}, period={self.period_start}..{self.period_end})>"


class ParsedInvoiceData(Base):
    """Temporary storage of parsed invoice data before validation."""
    __tablename__ = "parsed_invoice_data"
    
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey("uploaded_files.id"), nullable=True)
    invoice_number = Column(String(100), nullable=False, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    station = Column(String(50), nullable=True, index=True)
    
    # Invoice totals
    subtotal = Column(DECIMAL(12, 2), nullable=True)
    tax_amount = Column(DECIMAL(12, 2), nullable=True)
    total_amount = Column(DECIMAL(12, 2), nullable=True)
    
    # Line items (stored as JSON for flexibility)
    line_items_json = Column(JSON, default=[], nullable=True)
    
    # Status tracking
    is_validated = Column(Boolean, default=False)
    validation_notes = Column(Text, nullable=True)
    validated_at = Column(DateTime, nullable=True)
    validated_by = Column(String(100), nullable=True)
    
    # Audit reference
    audit_id = Column(Integer, ForeignKey("weekly_invoice_audits.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_parsed_invoice_number', 'invoice_number'),
        Index('idx_parsed_invoice_period', 'period_start', 'period_end'),
    )
    
    def __repr__(self):
        return f"<ParsedInvoiceData(invoice={self.invoice_number}, period={self.period_start}..{self.period_end}, validated={self.is_validated})>"


class AuditCorrection(Base):
    """Records corrections made by users during audit validation."""
    __tablename__ = "audit_corrections"
    
    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("weekly_invoice_audits.id"), nullable=False, index=True)
    line_item_id = Column(Integer, ForeignKey("weekly_audit_line_items.id"), nullable=True)
    
    # What was corrected
    field_name = Column(String(100), nullable=False)  # e.g., 'quantity', 'description', 'amount'
    original_value = Column(String(500), nullable=True)
    corrected_value = Column(String(500), nullable=False)
    correction_reason = Column(Text, nullable=False)
    
    # Tracking
    correction_status = Column(String(50), default='pending')  # pending, applied, disputed
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(100), nullable=True)
    
    __table_args__ = (
        Index('idx_audit_correction_audit', 'audit_id'),
        Index('idx_audit_correction_status', 'correction_status'),
    )
    
    def __repr__(self):
        return f"<AuditCorrection(audit={self.audit_id}, field={self.field_name}, status={self.correction_status})>"


class WeeklyAuditDispute(Base):
    """Records disputes raised during weekly invoice audit."""
    __tablename__ = "weekly_audit_disputes"
    
    id = Column(Integer, primary_key=True)
    audit_id = Column(Integer, ForeignKey("weekly_invoice_audits.id"), nullable=False, index=True)
    line_item_id = Column(Integer, ForeignKey("weekly_audit_line_items.id"), nullable=True)
    
    # Dispute details
    dispute_category = Column(String(100), nullable=False)  # e.g., 'missing_payment', 'quantity_mismatch', 'rate_error'
    dispute_description = Column(Text, nullable=False)
    amount_disputed = Column(DECIMAL(12, 2), nullable=True)
    
    # Evidence
    wst_expected = Column(String(500), nullable=True)
    invoice_billed = Column(String(500), nullable=True)
    evidence_notes = Column(Text, nullable=True)
    
    # Status
    dispute_status = Column(String(50), default='pending')  # pending, submitted, resolved
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, nullable=True)
    
    __table_args__ = (
        Index('idx_weekly_dispute_audit', 'audit_id'),
        Index('idx_weekly_dispute_status', 'dispute_status'),
        Index('idx_weekly_dispute_category', 'dispute_category'),
    )
    
    def __repr__(self):
        return f"<WeeklyAuditDispute(audit={self.audit_id}, category={self.dispute_category}, status={self.dispute_status})>"


# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)
    ensure_cortex_driver_name_column()
    print("✓ Database initialized")


def ensure_cortex_driver_name_column():
    """Ensure cortex_routes.driver_name exists for historical environments."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE cortex_routes ADD COLUMN IF NOT EXISTS driver_name VARCHAR(255)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cortex_routes_driver_name ON cortex_routes(driver_name)"))


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
