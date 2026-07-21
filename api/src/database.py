# ...existing code...

# Place this after Base and engine are defined

"""
SQLAlchemy ORM Models for NDAY Route Manager

Maps Python classes to PostgreSQL database tables.
"""

from datetime import datetime, date, time, timedelta
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Date, Time,
    DECIMAL, ForeignKey, Text, TIMESTAMP, JSON, Index, func, text, Float,
    UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import logging
import os

logger = logging.getLogger(__name__)

# Import permissions for role validation
from api.src.permissions import Role

# Database URL
# Priority:
# 1) Explicit DATABASE_URL from hosting platform (Render/managed DB)
# 2) Local fallback to sqlite for environments without a provisioned DB yet
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # Anchor to the repo root (two levels up from this file) so the DB path
    # is the same regardless of which directory the process starts from.
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATABASE_URL = f'sqlite:///{os.path.join(_repo_root, "nday_om.db")}'

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
    # Added 2026-07-17 for the invite/reset-password flow (auth.py):
    # slack_user_id is who to DM; reset_token/expiry back a one-time
    # set-password link used for both first-time invites (is_active=False,
    # password_hash=PENDING_PASSWORD_HASH until completed) and later resets.
    slack_user_id = Column(String(20))
    reset_token = Column(String(64), unique=True, index=True)
    reset_token_expires_at = Column(DateTime)

    # Relationships
    driver = relationship("Driver", back_populates="user", uselist=False)
    # Rescue relationships managed via RescueEvent / RescueContribution (string fields, not FK)
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
    license_number = Column(String(50))   # for crash-report prefill
    license_state = Column(String(10))    # for crash-report prefill
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="driver")
    assignments = relationship("Assignment", back_populates="driver")
    metrics = relationship("PerformanceMetric", back_populates="driver")
    incidents = relationship("Incident", back_populates="driver")
    # Rescue relationships managed via RescueEvent / RescueContribution (string fields, not FK)

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
    license_plate = Column(String(20))         # for crash-report prefill
    license_plate_state = Column(String(10))   # for crash-report prefill
    vehicle_year = Column(String(10))          # for crash-report prefill
    vehicle_make_model = Column(String(100))   # for crash-report prefill
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


class RescueEvent(Base):
    """Stage 1 + Stage 3: Dispatch opens and closes a rescue event."""
    __tablename__ = "rescue_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(30), unique=True, nullable=False, index=True)  # YYYYMMDD-HHMMSS
    event_date = Column(Date, nullable=False, index=True)
    event_type = Column(String(20), nullable=False)  # Pad Sweep | Full Pull | Rescue

    # Rescued driver — looked up from morning assignment via route code
    rescued_route_id = Column(String(20), nullable=False)
    rescued_driver_name = Column(String(100))
    rescued_van = Column(String(50))
    rescued_driver_tier = Column(String(20))  # NL1 | NL2 | NL3 | Tenured (for coaching records)

    # Rescuing driver — identified by dispatch at Stage 1
    rescuing_route_id = Column(String(20))   # null for Pad Sweeps
    rescuing_driver_name = Column(String(100))
    rescuing_van = Column(String(50))

    # Reason
    reason_code = Column(String(50))         # dropdown value
    reason_notes = Column(Text)              # populated when reason_code = 'Other'

    # Pad Sweep package count (entered at Stage 1 by dispatch — no Stage 2 for sweeps)
    pad_sweep_package_count = Column(Integer)

    # Expected packages for Full Pull and Full Pull Assist (entered by dispatch at Stage 1)
    expected_packages = Column(Integer)

    # Meeting address entered by dispatch at Stage 1 (used in Slack DMs as GPS link)
    meeting_address = Column(String(255))

    # Driver phones captured from roster at Stage 1 (snapshot so DMs work even if roster changes)
    rescued_driver_phone  = Column(String(30))
    rescuing_driver_phone = Column(String(30))

    # Stage 1 metadata
    opened_by = Column(String(100))          # dispatcher username from JWT
    status = Column(String(20), default='Open', index=True)  # Open | Closed

    # Stage 3 close fields
    closed_by = Column(String(100))
    close_notes = Column(Text)
    closed_at = Column(DateTime)

    # Slack notification flag
    slack_notified = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contributions = relationship(
        "RescueContribution", back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_rescue_event_date_status', 'event_date', 'status'),
    )

    def __repr__(self):
        return f"<RescueEvent(event_id={self.event_id}, type={self.event_type}, status={self.status})>"


class RescueContribution(Base):
    """Stage 2: Rescuing driver confirms package count for Full Pull / Rescue events."""
    __tablename__ = "rescue_contributions"

    id = Column(Integer, primary_key=True)
    contribution_id = Column(String(40), unique=True, nullable=False, index=True)
    event_id = Column(String(30), ForeignKey("rescue_events.event_id"), nullable=False, index=True)

    rescuing_driver_name = Column(String(100), nullable=False)
    packages_taken = Column(Integer, nullable=False)

    # Stage 2 confirmation gate
    confirmed_all_taken = Column(Boolean, nullable=False)  # driver's Yes/No answer
    bonus_eligible = Column(Boolean, default=False)        # True only if confirmed_all_taken = True

    observations = Column(Text)

    # Admin bonus reinstatement
    bonus_reinstated = Column(Boolean, default=False)
    reinstated_by = Column(String(100))
    reinstated_at = Column(DateTime)
    reinstatement_reason = Column(Text)

    # Payroll confirmation — set when admin marks bonus as paid on payroll report
    bonus_paid = Column(Boolean, default=False)
    bonus_paid_by = Column(String(100))
    bonus_paid_at = Column(DateTime)

    # Stage 3 verification
    verified = Column(String(20), default='Pending')  # Pending | Verified
    verified_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("RescueEvent", back_populates="contributions")

    __table_args__ = (
        Index('idx_rescue_contribution_event', 'event_id'),
        Index('idx_rescue_contribution_driver', 'rescuing_driver_name'),
        Index('idx_rescue_contribution_bonus', 'bonus_eligible', 'bonus_reinstated'),
    )

    def __repr__(self):
        return f"<RescueContribution(event_id={self.event_id}, driver={self.rescuing_driver_name}, pkgs={self.packages_taken}, eligible={self.bonus_eligible})>"


class DriverRosterEntry(Base):
    """Driver roster imported from ADP export — source of truth for driver dropdowns."""
    __tablename__ = "driver_roster"

    id = Column(Integer, primary_key=True)
    payroll_name = Column(String(100), nullable=False, index=True)  # Last, First
    position_id = Column(String(20), unique=True, nullable=True)    # UDXxxxxxx — nullable for schedule-seeded entries
    hire_date = Column(Date)
    home_department = Column(String(100))
    rate_type = Column(String(50))
    position_code = Column(String(20))  # 000004-Driver, 000005-Helper, etc.
    is_active = Column(Boolean, default=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Contact info
    phone = Column(String(30))               # entered by dispatcher on the roster page
    ssn_last4 = Column(String(4))            # last 4 SSN digits — used as callout page PIN

    # Slack integration
    slack_member_id   = Column(String(20))   # Slack User ID, e.g. U012AB3CD
    slack_display_name = Column(String(100)) # fetched from Slack on verify, not user-entered
    slack_verified    = Column(Boolean, default=False)
    slack_verified_at = Column(DateTime)

    # Profile provenance — this table is the interim source of truth, fed by
    # schedule uploads, until a future HR module owns creation/termination.
    source = Column(String(30), default="adp_import")   # adp_import | schedule_upload | hr_module
    last_seen_on_schedule = Column(Date)                  # most recent schedule date this driver appeared on
    flagged_inactive = Column(Boolean, default=False)     # not seen on any schedule in 30+ days — review, not auto-deactivated
    flagged_inactive_at = Column(DateTime)

    __table_args__ = (
        Index('idx_roster_active_position', 'is_active', 'position_code'),
    )

    def __repr__(self):
        return f"<DriverRosterEntry(name={self.payroll_name}, position={self.position_code}, active={self.is_active})>"


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


class DspScorecardWeeklySnapshot(Base):
    """One ingested DSP Scorecard PDF — overall + category standings."""
    __tablename__ = "dsp_scorecard_weekly_snapshots"

    id = Column(Integer, primary_key=True)
    week = Column(String(20), nullable=False, unique=True, index=True)   # "2026-W26"
    source_file = Column(String(255))
    slack_file_id = Column(String(50), unique=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    overall_score = Column(DECIMAL(5, 1))
    overall_standing = Column(String(30))

    safety_standing = Column(String(30))
    delivery_quality_standing = Column(String(30))
    pickup_quality_standing = Column(String(30))
    team_fleet_standing = Column(String(30))

    focus_areas = Column(JSON)          # ["PSB", "DSB", ...]
    dc_adjustment_note = Column(Text)   # RTS exemption note from page 1 if present
    slack_posted = Column(Boolean, default=False)

    metrics = relationship(
        "DspScorecardWeeklyMetric",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class DspScorecardWeeklyMetric(Base):
    """Individual metric row from a DSP Scorecard weekly snapshot."""
    __tablename__ = "dsp_scorecard_weekly_metrics"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(
        Integer, ForeignKey("dsp_scorecard_weekly_snapshots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    week = Column(String(20), nullable=False, index=True)
    slug = Column(String(60), nullable=False)        # e.g. "seatbelt_off_rate"
    label = Column(String(100), nullable=False)      # human label
    category = Column(String(40))                    # safety / delivery_quality / ...
    value_numeric = Column(DECIMAL(12, 4))
    standing = Column(String(30))                    # Fantastic / Great / Fair / Poor
    weight_pct = Column(DECIMAL(5, 1))              # Amazon weighting %
    is_disputable = Column(Boolean, default=False)
    dispute_note = Column(Text)

    snapshot = relationship("DspScorecardWeeklySnapshot", back_populates="metrics")

    __table_args__ = (
        Index("idx_dsp_weekly_metric_snap_slug", "snapshot_id", "slug"),
    )


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
    transporter_id = Column(String(50), index=True)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# DOP (DAY OF PLAN)
# ============================================================================

class RouteSheetEntry(Base):
    """Per-route data (van_number/wave/stage) parsed from a Route Sheet PDF.

    Added 2026-07-16: parse_route_sheet_pdf()'s output previously only ever
    existed as an in-memory list for the single check_and_notify() call that
    parsed it, then was discarded — the pre-existing RouteSheet table only
    stores file-level metadata (name, status), not per-route data. If DOP
    arrives later than the Route Sheet on a given day (as happened
    2026-07-16, when a detection bug delayed DOP for hours), the van/wave/
    stage data was silently unavailable to merge in at all once DOP finally
    landed, since SlackIngestLog prevents ever re-parsing the same Route
    Sheet file again. Persisting these rows the same way DOP/Cortex already
    do fixes that — see get_latest_route_sheet_rows().
    """
    __tablename__ = "route_sheet_entries"

    id = Column(Integer, primary_key=True)
    upload_date = Column(Date, nullable=False, index=True)
    route_code = Column(String(50), index=True)
    van_number = Column(String(50))
    wave_time = Column(String(20))
    stage = Column(String(100))
    driver_name = Column(String(255))
    source_file = Column(String(255))
    # Load-size signal for electric-van-shortage substitution (added
    # 2026-07-20) — total_bags = tote count, oversized_count = overflow
    # (oversize package) entry count. See assign_vans_for_routes()'s
    # electric-shortage handling in route_assignment.py.
    total_bags = Column(Integer)
    oversized_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_route_sheet_entry_date_route', 'upload_date', 'route_code'),
    )


def get_latest_route_sheet_rows(db, upload_date):
    """Return all RouteSheetEntry rows from the most recently ingested
    source_file for upload_date. See get_latest_dop_rows() for why scoping
    to a single source_file matters."""
    latest_file = _latest_source_file(db, RouteSheetEntry, RouteSheetEntry.upload_date, upload_date)
    if latest_file is None:
        return []
    return (
        db.query(RouteSheetEntry)
        .filter(RouteSheetEntry.upload_date == upload_date, RouteSheetEntry.source_file == latest_file)
        .all()
    )


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
    route_duration = Column(Integer)    # minutes from DOP; drives expected-return calculation
    commercial_pct = Column(DECIMAL(10, 2))
    zone = Column(String(50))
    service_type = Column(String(255))
    driver_name = Column(String(255))   # populated when DOP file includes driver assignments
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


def _latest_source_file(db, model, date_column, date_value):
    """Return the source_file of the most recently ingested row (highest id)
    for the given date, or None if nothing has been ingested yet."""
    return (
        db.query(model.source_file)
        .filter(date_column == date_value)
        .order_by(model.id.desc())
        .limit(1)
        .scalar()
    )


def get_latest_dop_rows(db, schedule_date):
    """Return all DOP rows from the most recently ingested source_file for
    schedule_date — a full snapshot of the latest upload, not a per-route
    merge across multiple same-day uploads.

    Ingestion is append-only (Amazon's same-day corrections often arrive
    under a different filename), so a naive `.filter(schedule_date==...).all()`
    would mix rows from an earlier upload with the current one — and a
    per-route "most recent row wins" merge would still incorrectly keep a
    route that was dropped entirely from a later, corrected file. Scoping
    to the single latest source_file avoids both.
    """
    latest_file = _latest_source_file(db, DOP, DOP.schedule_date, schedule_date)
    if latest_file is None:
        return []
    return (
        db.query(DOP)
        .filter(DOP.schedule_date == schedule_date, DOP.source_file == latest_file)
        .all()
    )


def get_latest_cortex_rows(db, assignment_date):
    """Return all Cortex rows from the most recently ingested source_file
    for assignment_date. See get_latest_dop_rows() for why this matters."""
    latest_file = _latest_source_file(db, Cortex, Cortex.assignment_date, assignment_date)
    if latest_file is None:
        return []
    return (
        db.query(Cortex)
        .filter(Cortex.assignment_date == assignment_date, Cortex.source_file == latest_file)
        .all()
    )


def purge_old_dop_cortex_rows(db, days=90):
    """Delete DOP/Cortex rows older than `days` days (by created_at).
    Ingestion is append-only, so historical rows accumulate — call this
    periodically (e.g. a scheduled admin task) to bound table growth.
    Returns {"dop_deleted": n, "cortex_deleted": n}.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    dop_deleted = db.query(DOP).filter(DOP.created_at < cutoff).delete(synchronize_session=False)
    cortex_deleted = db.query(Cortex).filter(Cortex.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return {"dop_deleted": dop_deleted, "cortex_deleted": cortex_deleted}


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


class DailyRouteAssignment(Base):
    """Per-driver route detail built each morning from DOP + Route Sheet + Cortex.
    One row per driver per day. Tracks DM delivery and attendance acknowledgment."""
    __tablename__ = "daily_route_assignments"

    id = Column(Integer, primary_key=True)
    assignment_date = Column(Date, nullable=False, index=True)
    route_code = Column(String(50), index=True)
    driver_name = Column(String(255), index=True)
    van_number = Column(String(50))
    stage_location = Column(String(100))
    wave = Column(String(50))
    packages = Column(Integer)
    route_duration = Column(Integer)    # minutes; copied from dop_routes for show/return time calc
    service_type = Column(String(255))

    # Route assignment board columns (added via ensure_assignment_board_columns for existing DBs)
    transporter_id = Column(String(50), index=True)
    vin = Column(String(50))
    quality_rank = Column(Integer)
    quality_standing = Column(String(20))
    is_callout_coverage = Column(Boolean, default=False)
    departure_time = Column(String(20))
    stops = Column(Integer)
    assignment_status = Column(String(20), default='pending')  # pending|confirmed|finalized

    dm_sent = Column(Boolean, default=False)
    dm_sent_at = Column(DateTime)
    dm_message_ts = Column(String(50))
    dm_channel = Column(String(30))

    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime)
    ack_token = Column(String(40), unique=True, index=True)

    # Snapshot of {driver_name, van_number, stage_location, wave, packages,
    # route_duration, service_type} at the moment the driver was last
    # actually told about this assignment (initial DM or a later "changed"
    # DM). Added 2026-07-16 for the "Re-Run Route Assignments" Dispatch
    # Home button — comparing current values against this (not against
    # whatever the automatic background rebuild silently changed the row
    # to) is what lets a rerun tell whether the driver's last DM is now
    # stale, without needing to track every intermediate silent update.
    notified_snapshot = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_daily_assign_date_driver', 'assignment_date', 'driver_name'),
    )


class DriverScheduleEntry(Base):
    """One row per driver per scheduled date from the weekly Excel schedule file.
    Populated on every ingest; old rows for the same date are replaced."""
    __tablename__ = "driver_schedule_entries"

    id = Column(Integer, primary_key=True)
    schedule_date = Column(Date, nullable=False, index=True)
    driver_name = Column(String(255), nullable=False, index=True)
    wave_time = Column(String(20))
    show_time = Column(String(20))
    service_type = Column(String(255))
    is_sweeper = Column(Boolean, default=False)
    dm_sent = Column(Boolean, default=False)
    dm_sent_at = Column(DateTime)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_schedule_date_driver', 'schedule_date', 'driver_name'),
    )


class DailyLeadAssignment(Base):
    """One row per lead, per scope, per day — the data-driven replacement
    for rostering.py's hardcoded _wave_lead_name() weekday dict. Resolution
    always checks the most recent row for (schedule_date, scope_type):
    a manual_override always wins because it's written after any
    default_rotation fallback the same day. See
    Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md §5.1/§6."""
    __tablename__ = "daily_lead_assignments"

    id = Column(Integer, primary_key=True)
    schedule_date = Column(Date, nullable=False, index=True)
    scope_type = Column(String(20), nullable=False, default="global")   # wave|route_group|station|global — only "global" is resolved against in Phase 1
    scope_key = Column(String(100), default="")                        # e.g. "Wave 1" — unused while scope_type=global
    driver_name = Column(String(255), nullable=False)
    transporter_id = Column(String(50))
    effective_start_time = Column(String(20))  # HH:MM, null = all day
    effective_end_time = Column(String(20))
    source = Column(String(20), default="manual_override")  # manual_override|schedule_ingest|default_rotation
    created_by = Column(String(150))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_lead_date_scope', 'schedule_date', 'scope_type', 'scope_key'),
    )


class CalloutQueue(Base):
    """One row per submitted callout. Controls when #nday-mgt is notified.

    Tight-roster callouts (no available replacement) are sent immediately.
    Normal callouts are batched and sent as an 8:30 AM digest.
    """
    __tablename__ = "callout_queue"

    id              = Column(Integer, primary_key=True)
    event_id        = Column(Integer, nullable=False, unique=True, index=True)
    shift_date      = Column(Date, nullable=False, index=True)
    driver_name     = Column(String(255), nullable=False)
    wave_time       = Column(String(20))
    replacement_pool = Column(Text)             # JSON list of available driver names
    roster_tight    = Column(Boolean, default=False, index=True)
    queued_at       = Column(DateTime, default=datetime.utcnow)
    digest_sent_at  = Column(DateTime)          # set when included in 8:30 digest
    alert_slack_ts  = Column(String(50))        # ts of the tight-roster Slack message
    acknowledged_at = Column(DateTime)
    acknowledged_by = Column(String(150))
    reminder_count  = Column(Integer, default=0)
    last_reminder_at = Column(DateTime)


class TimeOffRequest(Base):
    """One row per driver-submitted RTO/PTO request via the Slack Home tab.
    Bare-bones v1: no approval workflow, just a pending record + Slack notification."""
    __tablename__ = "time_off_requests"

    id              = Column(Integer, primary_key=True)
    driver_name     = Column(String(255), nullable=False, index=True)
    slack_member_id = Column(String(20))
    request_type    = Column(String(20))        # PTO | UTO | Unpaid
    start_date      = Column(Date, nullable=False)
    end_date        = Column(Date, nullable=False)
    reason          = Column(Text)
    status          = Column(String(20), default="pending", index=True)  # pending | reviewed
    created_at      = Column(DateTime, default=datetime.utcnow)


class SlackIngestLog(Base):
    """Tracks files detected and processed from the Slack channel.
    slack_file_id unique constraint prevents re-processing the same file."""
    __tablename__ = "slack_ingest_log"

    id = Column(Integer, primary_key=True)
    ingest_date = Column(Date, nullable=False, index=True)
    file_type = Column(String(20))           # "dop" | "route_sheet"
    slack_file_id = Column(String(50), unique=True, nullable=False, index=True)
    filename = Column(String(255))
    detected_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    status = Column(String(20), default="pending")  # pending|success|failed
    error = Column(String(500))
    records_processed = Column(Integer)


class EcpRosterPrompt(Base):
    """Tracks nightly ECP detection and roster prompts sent to #nday-operations-management."""
    __tablename__ = "ecp_roster_prompts"

    id = Column(Integer, primary_key=True)
    prompt_date = Column(Date, nullable=False, index=True)
    ecp_message_ts = Column(String(50))
    ecp_message_text = Column(String(500))
    prompted_at = Column(DateTime, default=datetime.utcnow)
    prompt_message_ts = Column(String(50))


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
# QUALITY METRICS (TRAILING 6-WEEK DSP OVERVIEW DASHBOARD)
# ============================================================================

class QualityMetricSnapshot(Base):
    """One upload of the DSP Overview Dashboard Trailing Six Week CSV."""
    __tablename__ = "quality_metric_snapshots"

    id = Column(Integer, primary_key=True)
    week = Column(String(10), nullable=False, index=True)   # "2026-W26"
    source_file = Column(String(255))
    slack_file_id = Column(String(50), unique=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    driver_count = Column(Integer, default=0)

    drivers = relationship(
        "QualityMetricDriver",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class QualityMetricDriver(Base):
    """Per-driver row from the DSP Overview Dashboard trailing report."""
    __tablename__ = "quality_metric_drivers"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("quality_metric_snapshots.id", ondelete="CASCADE"), nullable=False)
    week = Column(String(10), nullable=False, index=True)
    driver_name = Column(String(200), nullable=False, index=True)
    transporter_id = Column(String(50), index=True)

    # Amazon computed overall
    overall_standing = Column(String(20))   # Platinum / Gold / Silver / Bronze
    overall_score = Column(DECIMAL(6, 2))   # 0.00 – 100.00

    # Safety metrics (rate per trip + component score)
    speeding_rate = Column(DECIMAL(10, 4))
    speeding_score = Column(DECIMAL(6, 2))
    seatbelt_rate = Column(DECIMAL(10, 4))
    seatbelt_score = Column(DECIMAL(6, 2))
    distraction_rate = Column(DECIMAL(10, 4))
    distraction_score = Column(DECIMAL(6, 2))
    sign_violation_rate = Column(DECIMAL(10, 4))
    sign_violation_score = Column(DECIMAL(6, 2))
    following_distance_rate = Column(DECIMAL(10, 4))
    following_distance_score = Column(DECIMAL(6, 2))

    # Quality metrics (raw value + component score)
    cdf_dpmo = Column(DECIMAL(12, 2))
    cdf_dpmo_score = Column(DECIMAL(6, 2))
    dc_dpmo = Column(DECIMAL(12, 2))
    dc_dpmo_score = Column(DECIMAL(6, 2))
    dsb_count = Column(Integer)
    dsb_score = Column(DECIMAL(6, 2))
    pod_pct = Column(DECIMAL(6, 3))         # e.g. 0.991 from "99.1%"
    pod_score = Column(DECIMAL(6, 2))
    psb_rate = Column(DECIMAL(10, 4))
    psb_score = Column(DECIMAL(6, 2))

    packages_delivered = Column(Integer)

    snapshot = relationship("QualityMetricSnapshot", back_populates="drivers")

    __table_args__ = (
        Index("idx_qmd_week_driver", "week", "driver_name"),
        Index("idx_qmd_standing_score", "overall_standing", "overall_score"),
    )


class TenuredWorkforceRecord(Base):
    """Per-driver per-week row from Amazon's "Tenured Workforce DAs Report"
    (logistics.amazon.com -> Performance -> Interactive Report ->
    Supplementary Reports -> TWF Dashboard). Added 2026-07-17 to back the
    driver-score tenure gate and the trailing-6-week route-count
    eligibility gate. Uploaded weekly as a full historical re-export (the
    same file each week contains every prior week too), so ingestion
    upserts by (transporter_id, year, week) rather than replacing by
    source_file the way DOP/Cortex do — past weeks don't change, only the
    newest week is actually new data each time.

    Column name note: "Trabsporter ID" is Amazon's own typo in the source
    file, not ours — kept as transporter_id here, just documenting why the
    parser matches on that literal misspelling.
    """
    __tablename__ = "tenured_workforce_records"

    id = Column(Integer, primary_key=True)
    dsp = Column(String(20))
    station = Column(String(20), index=True)
    year = Column(Integer, index=True)
    week = Column(Integer, index=True)
    employee_id = Column(String(50))
    transporter_id = Column(String(50), index=True)
    da_name = Column(String(255))
    days_since_last_delivery = Column(Integer)
    delivery_status = Column(String(50))
    driver_status = Column(String(20))
    tenure_status = Column(String(20))       # "Tenured" | "Not Tenured"
    lifetime_routes = Column(Integer)
    routes_in_week = Column(Integer)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("transporter_id", "year", "week", name="uq_twf_driver_week"),
        Index("idx_twf_transporter_year_week", "transporter_id", "year", "week"),
    )


def get_latest_tenure_record(db, transporter_id: str):
    """Most recent (highest year, week) TenuredWorkforceRecord for a
    driver, or None if they've never appeared in a Tenured Workforce
    report."""
    return (
        db.query(TenuredWorkforceRecord)
        .filter(TenuredWorkforceRecord.transporter_id == transporter_id)
        .order_by(TenuredWorkforceRecord.year.desc(), TenuredWorkforceRecord.week.desc())
        .first()
    )


def get_trailing_route_count(db, transporter_id: str, weeks: int = 6) -> int:
    """Sum of routes_in_week over a driver's N most recent Tenured
    Workforce report rows — the trailing-N-week route count used for the
    30-route ranking/bonus eligibility gate. Naturally handles gap weeks
    (a week the driver didn't work just doesn't contribute), unlike
    subtracting lifetime_routes N weeks back, which would require an
    exact-week match."""
    rows = (
        db.query(TenuredWorkforceRecord.routes_in_week)
        .filter(TenuredWorkforceRecord.transporter_id == transporter_id)
        .order_by(TenuredWorkforceRecord.year.desc(), TenuredWorkforceRecord.week.desc())
        .limit(weeks)
        .all()
    )
    return sum(r[0] or 0 for r in rows)


# ============================================================================
# ATTENDANCE TRACKING
# ============================================================================

class AttendanceEvent(Base):
    """Logs every attendance event — call-ins, no-shows, late arrivals, early departures.
    Per SRD HR-02/HR-03: 4-hour call-in rule compliance + missed-shift escalation."""
    __tablename__ = "attendance_events"

    id = Column(Integer, primary_key=True)
    driver_name = Column(String(200), nullable=False, index=True)
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=True)
    event_date = Column(Date, nullable=False, index=True)

    # Event classification
    event_type = Column(String(50), nullable=False)
    # call_in | no_show | late_arrival | early_departure | present | excused

    reason_code = Column(String(50))
    # sick | personal | family | weather | transportation | no_call | other

    # Call-in timing (for 4-hour rule compliance)
    call_time = Column(DateTime)           # when driver actually called
    scheduled_wave = Column(String(20))    # e.g. "1020" or "1025"
    shift_start = Column(DateTime)         # absolute datetime of wave
    hours_before_shift = Column(DECIMAL(5, 2))  # calculated at log time
    compliant = Column(Boolean)            # True = called 4+ hrs before shift

    # Missed shift tracking (HR-03)
    is_missed = Column(Boolean, default=False)  # no_show or call_in same day
    missed_shift_count = Column(Integer, default=0)  # running count at time of event
    voluntary_resign_flag = Column(Boolean, default=False)  # auto-set at count=2

    # Narrative / notes
    notes = Column(Text)
    logged_by = Column(String(100))        # OM/dispatch who logged it

    # Driver electronic signature (callout page — driver types full name to sign)
    signature_name = Column(String(150))
    signature_at = Column(DateTime)

    # Manager review & countersignature
    manager_signature_name = Column(String(150))
    manager_signature_at = Column(DateTime)
    manager_id = Column(String(100))        # username of manager who signed

    # RingCentral link (populated when event auto-detected from call)
    ringcentral_call_id = Column(String(100), index=True)
    caller_number = Column(String(20))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_att_driver_date", "driver_name", "event_date"),
        Index("idx_att_date_type", "event_date", "event_type"),
    )


class RingCentralCallLog(Base):
    """Raw call records received from RingCentral webhook.
    Each inbound call to the dispatch line is logged here; matched to driver by phone."""
    __tablename__ = "ringcentral_call_logs"

    id = Column(Integer, primary_key=True)
    call_id = Column(String(100), unique=True, index=True)
    caller_number = Column(String(20), index=True)
    called_number = Column(String(20))         # dispatch number called
    received_at = Column(DateTime, index=True)
    duration_seconds = Column(Integer)
    call_direction = Column(String(10))        # "Inbound" | "Outbound"
    call_result = Column(String(30))           # "Call connected" | "Missed" etc.

    # Driver match
    matched_driver = Column(String(200))
    matched_roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=True)
    attendance_event_id = Column(Integer, ForeignKey("attendance_events.id"), nullable=True)

    processed = Column(Boolean, default=False)
    raw_payload = Column(Text)                 # full JSON from webhook for audit

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_rc_caller_date", "caller_number", "received_at"),
    )


# ============================================================================
# HIRING / CANDIDATE PIPELINE
# ============================================================================

class Candidate(Base):
    """Job applicant captured from Indeed via the hiring Chrome extension.

    Dedupe key is indeed_candidate_id — a list-page sync creates the row,
    a later detail-page sync enriches the same row (contact info, tenure,
    keyword tags) rather than creating a duplicate.
    """
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True)
    indeed_candidate_id = Column(String(100), unique=True, index=True, nullable=False)
    first_name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(20))
    email = Column(String(150))
    location = Column(String(150))
    resume_url = Column(Text)
    indeed_profile_url = Column(Text)
    indeed_match_score = Column(Integer)
    recruiting_summary_text = Column(Text)
    avg_tenure_months = Column(Float)
    status = Column(String(50), default="undecided")  # mirrors Asana column
    asana_task_gid = Column(String(50))
    google_contact_resource_name = Column(String(150))
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    keyword_tags = relationship("CandidateKeywordTag", back_populates="candidate")

    def __repr__(self):
        return f"<Candidate(name={self.first_name} {self.last_name}, status={self.status})>"


class CandidateKeywordTag(Base):
    """A keyword hit found in a candidate's resume/work-history text."""
    __tablename__ = "candidate_keyword_tags"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(100), nullable=False)
    category = Column(String(50))  # prior_employer | certification | disqualifier | local_dsp | nonlocal_dsp
    matched_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="keyword_tags")


class KeywordRule(Base):
    """Admin-editable keyword dictionary used to tag candidates at intake."""
    __tablename__ = "keyword_rules"

    id = Column(Integer, primary_key=True)
    keyword = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)
    ensure_cortex_driver_name_column()
    ensure_dop_driver_name_column()
    ensure_route_duration_columns()
    seed_keyword_rules()
    print("✓ Database initialized")


def seed_keyword_rules():
    """Seed the candidate keyword dictionary with starter terms if empty.
    Admin-editable from there via the keyword_rules table — this only runs
    the first time (skips if any rows already exist)."""
    default_rules = [
        ("FedEx", "prior_employer"),
        ("DoorDash", "prior_employer"),
        ("UPS", "prior_employer"),
        ("Amazon", "prior_employer"),
        ("CDL", "certification"),
        ("Tow truck", "disqualifier"),
    ]
    db = SessionLocal()
    try:
        if db.query(KeywordRule).first():
            return  # already seeded / admin-managed from here
        for keyword, category in default_rules:
            db.add(KeywordRule(keyword=keyword, category=category, active=True))
        db.commit()
    finally:
        db.close()


def ensure_cortex_driver_name_column():
    """Ensure cortex_routes.driver_name exists for historical environments."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE cortex_routes ADD COLUMN driver_name VARCHAR(255)"))
            else:
                conn.execute(text("ALTER TABLE cortex_routes ADD COLUMN IF NOT EXISTS driver_name VARCHAR(255)"))
    except Exception:
        pass  # Column already exists
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cortex_routes_driver_name ON cortex_routes(driver_name)"))
    except Exception:
        pass


def ensure_dvic_raw_fields_column():
    """Ensure dvic_violations.raw_fields exists for historical environments."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE dvic_violations ADD COLUMN raw_fields JSON"))
            else:
                conn.execute(text("ALTER TABLE dvic_violations ADD COLUMN IF NOT EXISTS raw_fields JSON"))
    except Exception:
        pass  # Column already exists


def ensure_dop_driver_name_column():
    """Ensure dop_routes.driver_name exists — added 2026-06-30."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE dop_routes ADD COLUMN driver_name VARCHAR(255)"))
            else:
                conn.execute(text("ALTER TABLE dop_routes ADD COLUMN IF NOT EXISTS driver_name VARCHAR(255)"))
    except Exception:
        pass  # Column already exists


def ensure_route_duration_columns():
    """Add route_duration to dop_routes and daily_route_assignments — added 2026-07-01."""
    for table in ("dop_routes", "daily_route_assignments"):
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN route_duration INTEGER"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS route_duration INTEGER"))
        except Exception:
            pass  # Column already exists


def ensure_route_sheet_load_size_columns():
    """Add total_bags/oversized_count to route_sheet_entries — added
    2026-07-20 for electric-van-shortage substitution (see
    assign_vans_for_routes() in route_assignment.py)."""
    for col in ("total_bags", "oversized_count"):
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE route_sheet_entries ADD COLUMN {col} INTEGER"))
                else:
                    conn.execute(text(f"ALTER TABLE route_sheet_entries ADD COLUMN IF NOT EXISTS {col} INTEGER"))
        except Exception:
            pass  # Column already exists


def ensure_driver_shift_dm_decline_column():
    """Add declined_at to driver_shift_dms — added 2026-07-21 for the
    Showtime DM's "Can't Make It" button (see send_driver_shift_dms() in
    rostering.py)."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE driver_shift_dms ADD COLUMN declined_at DATETIME"))
            else:
                conn.execute(text("ALTER TABLE driver_shift_dms ADD COLUMN IF NOT EXISTS declined_at TIMESTAMP"))
    except Exception:
        pass  # Column already exists


def ensure_user_auth_columns():
    """Add slack_user_id/reset_token/reset_token_expires_at to users —
    added 2026-07-17 for the invite/reset-password Dispatch Home flow."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE users ADD COLUMN slack_user_id VARCHAR(20)"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS slack_user_id VARCHAR(20)"))
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE users ADD COLUMN reset_token VARCHAR(64)"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(64)"))
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires_at DATETIME"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMP"))
    except Exception:
        pass


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username.lower().strip()).first()


def get_user_by_reset_token(db, token: str):
    return db.query(User).filter(User.reset_token == token).first()


def ensure_daily_route_assignment_notified_snapshot_column():
    """Add notified_snapshot to daily_route_assignments — added 2026-07-16
    for the 'Re-Run Route Assignments' Dispatch Home button."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE daily_route_assignments ADD COLUMN notified_snapshot JSON"))
            else:
                conn.execute(text("ALTER TABLE daily_route_assignments ADD COLUMN IF NOT EXISTS notified_snapshot JSON"))
    except Exception:
        pass  # Column already exists


def dedupe_daily_route_assignments(db=None) -> int:
    """Remove duplicate DailyRouteAssignment rows sharing the same
    (assignment_date, route_code), keeping the lowest id. Returns count deleted.

    Root cause (found 2026-07-13): build_daily_assignments() (daily_notify.py)
    does an unprotected delete-then-insert with no DB constraint to stop a
    concurrent second call (e.g. the automatic 8-10am background loop
    racing a manual re-ingest) from inserting a second full batch. See
    ensure_daily_route_assignment_unique_index() for the permanent fix.
    """
    close_after = False
    if db is None:
        db = SessionLocal()
        close_after = True
    try:
        dupe_keys = (
            db.query(DailyRouteAssignment.assignment_date, DailyRouteAssignment.route_code)
            .filter(DailyRouteAssignment.route_code != None)
            .group_by(DailyRouteAssignment.assignment_date, DailyRouteAssignment.route_code)
            .having(func.count(DailyRouteAssignment.id) > 1)
            .all()
        )
        deleted = 0
        for adate, rcode in dupe_keys:
            rows = (
                db.query(DailyRouteAssignment)
                .filter(
                    DailyRouteAssignment.assignment_date == adate,
                    DailyRouteAssignment.route_code == rcode,
                )
                .order_by(DailyRouteAssignment.id)
                .all()
            )
            for extra in rows[1:]:
                db.delete(extra)
                deleted += 1
        if deleted:
            db.commit()
        return deleted
    finally:
        if close_after:
            db.close()


def ensure_daily_route_assignment_unique_index():
    """Prevent the 2026-07-13 duplicate-row bug from recurring: add a unique
    index on (assignment_date, route_code). Must dedupe existing rows first
    or index creation fails on the existing duplicates."""
    dedupe_daily_route_assignments()
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_route_assignment_date_route "
                    "ON daily_route_assignments(assignment_date, route_code)"
                ))
            else:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_route_assignment_date_route "
                    "ON daily_route_assignments(assignment_date, route_code) WHERE route_code IS NOT NULL"
                ))
    except Exception as exc:
        logger.warning("Could not create daily_route_assignments unique index: %s", exc)


def ensure_ssn_last4_column():
    """Add ssn_last4 to driver_roster for callout page PIN auth — added 2026-07-02.
    Seeds default PIN 1234 for any driver without one so all drivers can log in immediately."""
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("ALTER TABLE driver_roster ADD COLUMN ssn_last4 VARCHAR(4)"))
            else:
                conn.execute(text("ALTER TABLE driver_roster ADD COLUMN IF NOT EXISTS ssn_last4 VARCHAR(4)"))
    except Exception:
        pass  # Column already exists
    # Seed default PIN for any driver that doesn't have one yet
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE driver_roster SET ssn_last4 = '1234' WHERE ssn_last4 IS NULL"))
    except Exception:
        pass


def ensure_driver_roster_tracking_columns():
    """Add source/last_seen_on_schedule/flagged_inactive(_at) to driver_roster
    for the schedule-driven driver profile module — added 2026-07-12."""
    for col, typedef in [
        ("source", "VARCHAR(30) DEFAULT 'adp_import'"),
        ("last_seen_on_schedule", "DATE"),
        ("flagged_inactive", "BOOLEAN DEFAULT FALSE"),
        ("flagged_inactive_at", "TIMESTAMP"),
    ]:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE driver_roster ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE driver_roster ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists


def ensure_okami_capacity_finalize_columns():
    """Add frt + finalize-snapshot columns to okami_capacity_logs — the
    table shipped first without them, then finalize/FRT support was added
    in a follow-up pass the same day (2026-07-14/15). Safe no-op if the
    table doesn't exist yet (create_all handles that case fresh)."""
    for col, typedef in [
        ("frt", "INTEGER"),
        ("finalized_at", "TIMESTAMP"),
        ("finalized_by", "VARCHAR(100)"),
        ("required_da_count", "INTEGER"),
        ("da_status", "VARCHAR(20)"),
        ("required_van_count", "INTEGER"),
        ("effective_available_vans", "INTEGER"),
        ("van_status", "VARCHAR(20)"),
        ("van_deficit", "INTEGER"),
        ("grounded_vans_snapshot", "JSON" if not DATABASE_URL.startswith("sqlite") else "TEXT"),
        ("frt_breached", "BOOLEAN"),
    ]:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE okami_capacity_logs ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE okami_capacity_logs ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists, or table doesn't exist yet (create_all will make it fresh)


def flag_stale_driver_profiles(db, days: int = 30) -> int:
    """Flag (never deactivate) driver_roster rows not seen on any schedule
    in `days` days. Only considers rows that have a last_seen_on_schedule
    value at all, so ADP-only profiles never tracked via schedule uploads
    aren't falsely flagged. Returns the number newly flagged."""
    cutoff = datetime.utcnow().date() - timedelta(days=days)
    rows = (
        db.query(DriverRosterEntry)
        .filter(
            DriverRosterEntry.is_active == True,
            DriverRosterEntry.flagged_inactive == False,
            DriverRosterEntry.last_seen_on_schedule != None,
            DriverRosterEntry.last_seen_on_schedule < cutoff,
        )
        .all()
    )
    for r in rows:
        r.flagged_inactive = True
        r.flagged_inactive_at = datetime.utcnow()
    if rows:
        db.commit()
    return len(rows)


def ensure_callout_signature_column():
    """Add signature_name + signature_at to attendance_events for callout signed acknowledgment — added 2026-07-02."""
    for col, typedef in [("signature_name", "VARCHAR(150)"), ("signature_at", "TIMESTAMP")]:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE attendance_events ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists


def _ensure_manager_signature_columns():
    """Add manager countersignature columns to attendance_events — added 2026-07-08."""
    for col, typedef in [
        ("manager_signature_name", "VARCHAR(150)"),
        ("manager_signature_at", "TIMESTAMP"),
        ("manager_id", "VARCHAR(100)"),
    ]:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE attendance_events ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass


def _ensure_position_id_nullable():
    """Drop NOT NULL constraint on driver_roster.position_id — added 2026-07-08.
    Schedule-seeded drivers have no ADP position_id so the column must be nullable."""
    if DATABASE_URL.startswith("sqlite"):
        return  # SQLite ignores NOT NULL changes; no action needed
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE driver_roster ALTER COLUMN position_id DROP NOT NULL"
            ))
    except Exception:
        pass  # already nullable or table doesn't exist yet


# ============================================================================
# DVIC PRE-TRIP INSPECTION (Under-90-Second Violations)
# ============================================================================

class DvicSnapshot(Base):
    """One row per weekly DVIC Under-90s Excel upload."""
    __tablename__ = "dvic_snapshots"

    id = Column(Integer, primary_key=True)
    week = Column(String(20), nullable=False, index=True)
    source_file = Column(String(255))
    slack_file_id = Column(String(50), unique=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_violations = Column(Integer, default=0)
    unique_drivers = Column(Integer, default=0)
    date_range_start = Column(Date)
    date_range_end = Column(Date)

    violations = relationship("DvicViolation", back_populates="snapshot", cascade="all, delete-orphan")


class DvicViolation(Base):
    """Individual pre-trip inspection completed in under 90 seconds."""
    __tablename__ = "dvic_violations"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("dvic_snapshots.id"), nullable=False, index=True)
    week = Column(String(20), index=True)
    start_date = Column(Date)
    dsp = Column(String(20))
    station = Column(String(20))
    transporter_id = Column(String(50), nullable=False, index=True)
    transporter_name = Column(String(150))
    vin = Column(String(50))
    fleet_type = Column(String(20))
    inspection_type = Column(String(50))
    inspection_status = Column(String(20))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_seconds = Column(Integer)
    raw_fields = Column(JSON)   # any columns from the source file not mapped above, keyed by header

    snapshot = relationship("DvicSnapshot", back_populates="violations")


class DvicAcknowledgment(Base):
    """Driver digital acknowledgment of their DVIC violations for a given week."""
    __tablename__ = "dvic_acknowledgments"

    id = Column(Integer, primary_key=True)
    transporter_id = Column(String(50), nullable=False, index=True)
    transporter_name = Column(String(150))
    week = Column(String(20), nullable=False, index=True)
    violation_count = Column(Integer)
    signature_name = Column(String(150), nullable=False)
    acknowledged_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    dm_sent_at = Column(DateTime)

    __table_args__ = (
        Index("idx_dvic_ack_tid_week", "transporter_id", "week", unique=True),
    )


class DvicCounselingRecord(Base):
    """Progressive-discipline stage tracker, one row per driver.

    Advances one stage the first time a driver appears on a NEW week's DVIC
    report — a driver whose name persists on subsequent reports (already
    counseled) does not get re-actioned for the same week twice. instance
    count from the triggering week is kept only to word the message
    ("...completed 3 DVICs in under 90 seconds...") — it does not itself
    determine the stage.
    """
    __tablename__ = "dvic_counseling_records"

    id = Column(Integer, primary_key=True)
    transporter_id = Column(String(50), nullable=False, unique=True, index=True)
    transporter_name = Column(String(150))
    stage = Column(Integer, default=0, nullable=False)   # 0=none yet; 1-4 current ladder stage
    last_week = Column(String(20))                        # most recent week actioned (dedupe key)
    last_instance_count = Column(Integer)
    last_actioned_at = Column(DateTime)
    ack_status = Column(String(20), default="pending")    # pending | acknowledged
    acknowledged_at = Column(DateTime)
    dm_channel = Column(String(50))                       # Slack DM channel id, for chat_update on ack
    dm_ts = Column(String(50))                             # Slack message ts, for chat_update on ack
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentRoutingRule(Base):
    """Which roles/recipients a given document type routes to.

    e.g. document_type="crash_report" -> recipient_roles=["dispatch","ops_manager","owner"]
    Admin-editable; consulted whenever a document-generating flow (crash
    report, injury report, etc.) needs to know who to notify.
    """
    __tablename__ = "document_routing_rules"

    id = Column(Integer, primary_key=True)
    document_type = Column(String(50), nullable=False, unique=True, index=True)
    recipient_roles = Column(JSON, nullable=False, default=list)   # list[str], e.g. ["dispatch","ops_manager","owner"]
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentRequirementRule(Base):
    """Defines the fields/tasks a document type must have completed before
    it's eligible for submission — e.g. crash_report requires a police
    report number, photos, and a signature. Admin-editable; the frontend
    wizard for a document type renders itself from this list and blocks
    submission until every is_required field is filled.
    """
    __tablename__ = "document_requirement_rules"

    id = Column(Integer, primary_key=True)
    document_type = Column(String(50), nullable=False, index=True)
    field_key = Column(String(100), nullable=False)         # e.g. "police_report_number"
    field_label = Column(String(200), nullable=False)       # e.g. "Police Report Number"
    field_type = Column(String(30), default="text")         # text | number | photo | signature | boolean | select | date
    is_required = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    options = Column(JSON)                                   # choices for "select" fields
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_doc_req_type_key", "document_type", "field_key", unique=True),
    )


class RoleDirectory(Base):
    """Maps an abstract role name (referenced by DocumentRoutingRule,
    e.g. "dispatch", "ops_manager", "owner", "hr") to actual Slack
    recipient IDs (user IDs for DMs, or a channel ID). Admin-editable via
    document_routing.py — a role with an empty list is simply skipped
    when routing, rather than erroring.
    """
    __tablename__ = "role_directory"

    id = Column(Integer, primary_key=True)
    role_name = Column(String(50), nullable=False, unique=True, index=True)
    slack_ids = Column(JSON, nullable=False, default=list)   # list[str]
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CrashReport(Base):
    """Digital version of Amazon's 'DA Incident Packet v3.3' crash report form."""
    __tablename__ = "crash_reports"

    id = Column(Integer, primary_key=True)
    report_number = Column(String(30), unique=True, nullable=False, index=True)
    submitted_by = Column(String(150))
    submitted_at = Column(DateTime)
    status = Column(String(20), default="draft", index=True)   # draft | submitted
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Safety checklist
    flashers_on = Column(Boolean, default=False)
    vehicle_secured = Column(Boolean, default=False)
    police_called = Column(Boolean, default=False)
    medical_requested = Column(Boolean, default=False)
    vehicle_not_moved = Column(Boolean, default=False)
    hotline_called = Column(Boolean, default=False)
    hotline_call_at = Column(DateTime)
    dsp_owner_notified = Column(Boolean, default=False)
    info_provided_to_leo = Column(Boolean, default=False)
    photos_360_taken = Column(Boolean, default=False)
    police_report_provided = Column(Boolean, default=False)

    # General information
    accident_date = Column(Date)
    accident_time = Column(String(20))
    accident_ampm = Column(String(2))
    location_address = Column(String(255))
    city_state_zip = Column(String(150))
    driver_name = Column(String(150))
    driver_license_number = Column(String(50))
    driver_license_state = Column(String(10))
    dsp_code = Column(String(20))

    # Vehicle information
    vehicle_year = Column(String(10))
    vehicle_make_model = Column(String(100))
    license_plate_state = Column(String(50))
    equipment_number = Column(String(50))
    vin = Column(String(50))
    amzl_station_origin = Column(String(50))
    destination_type = Column(String(20))   # delivery | amzl_station | vehicle_service

    # Third party (only applicable if another vehicle was involved)
    third_party_involved = Column(Boolean, default=False)
    third_party_driver_name = Column(String(150))
    third_party_driver_address = Column(String(255))
    third_party_driver_phone = Column(String(30))
    third_party_insurance = Column(String(150))
    third_party_vehicle_year = Column(String(10))
    third_party_vehicle_make_model = Column(String(100))
    third_party_license_plate_state = Column(String(50))
    third_party_license_no = Column(String(50))
    third_party_license_state = Column(String(10))

    # Narrative / statements
    accident_description = Column(Text)               # driver's own statement
    accident_description_raw = Column(Text)            # verbatim, pre-sanitization
    third_party_statement = Column(Text)
    third_party_statement_raw = Column(Text)            # verbatim, pre-sanitization
    third_party_statement_declined = Column(Boolean, default=False)

    # Conditions / other (all optional — police report typically covers these)
    num_lanes = Column(Integer)
    road_construction = Column(String(20))
    road_attitude = Column(String(20))
    traffic_conditions = Column(String(20))
    light_conditions = Column(String(30))
    road_conditions = Column(String(20))
    weather_conditions = Column(String(20))
    weather_other = Column(String(50))

    # Additional information — only applicable if police were dispatched
    police_department = Column(String(150))
    officer_name = Column(String(150))
    police_phone = Column(String(30))
    police_report_no = Column(String(50))
    citation_issued = Column(Boolean)

    # Attachments — each JSON column is a list[str] of S3 keys (see storage.py).
    photo_urls = Column(JSON)                  # 360 scene photos (min 6 enforced at submit)
    photo_vehicle_damage = Column(JSON)        # NDAY vehicle damage
    photo_other_vehicle = Column(JSON)         # third-party vehicle (conditional)
    photo_dl_driver = Column(JSON)             # NDAY driver's license
    photo_dl_other = Column(JSON)              # third-party driver's license (conditional)
    photo_insurance_other = Column(JSON)       # third-party insurance card (conditional)
    photo_license_plate_other = Column(JSON)   # third-party license plate (conditional)
    diagram_url = Column(String(500))          # photo of the hand-drawn (or digital) accident diagram

    # Drug screen — set to "pending" on submit; dispatch marks "scheduled"/
    # "completed" (see rts.py's drug-screen hook on driver Return to Station).
    drug_screen_status = Column(String(20))


class CrashReportApproval(Base):
    """One row per stage of a crash report's sequential approval chain
    (dispatch -> ops_manager -> owner). HR is notified automatically on owner
    approval (see crash_report.py) but isn't a gating stage here, so it has no
    row of its own."""
    __tablename__ = "crash_report_approvals"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("crash_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_order = Column(Integer, nullable=False)   # 1=dispatch, 2=ops_manager, 3=owner
    role = Column(String(30), nullable=False)
    status = Column(String(20), default="pending")  # pending | notified | approved
    notified_at = Column(DateTime)
    approved_at = Column(DateTime)
    approved_by = Column(String(100))               # Slack user id of the approver
    slack_channel = Column(String(50))               # for chat_update on approve
    slack_ts = Column(String(50))                     # for chat_update on approve

    __table_args__ = (
        Index("idx_crash_report_approval_report_stage", "report_id", "stage_order", unique=True),
    )


class EmployeeDocument(Base):
    """Per-employee document archive — populated automatically when a crash
    report's owner-approval stage completes (see crash_report.py)."""
    __tablename__ = "employee_documents"

    id = Column(Integer, primary_key=True)
    driver_name = Column(String(150), nullable=False, index=True)
    employee_id = Column(String(50))
    document_type = Column(String(50), nullable=False)   # e.g. "crash_report"
    related_record_id = Column(Integer)
    file_url = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Routing
    routed_at = Column(DateTime)
    routed_to = Column(JSON)           # list of role names actually notified


class EodSurveyResponse(Base):
    """Driver end-of-day check-out survey. One row per driver per calendar day."""
    __tablename__ = "eod_survey_responses"

    id = Column(Integer, primary_key=True)
    survey_date = Column(Date, nullable=False, index=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Identity (resolved from PIN auth)
    driver_name = Column(String(200), nullable=False, index=True)
    transporter_id = Column(String(50), index=True)
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=True)

    # Pre-populated from daily assignment
    van_number = Column(String(50))
    wave = Column(String(50))
    role = Column(String(50))           # Driver / Helper / Shift Lead / Trainer

    # Clock-in
    clocked_in_on_time = Column(Boolean)
    actual_clock_in_time = Column(String(20))
    clock_in_reason = Column(Text)

    # Van
    van_issues = Column(Boolean, default=False)
    van_issue_description = Column(Text)

    # Incident / damage
    incident_occurred = Column(Boolean, default=False)
    incident_report_filed = Column(Boolean)

    # Injury
    injury_occurred = Column(Boolean, default=False)
    injury_report_submitted = Column(Boolean)
    medical_review_completed = Column(Boolean)

    # Post-trip
    post_trip_dvic_completed = Column(Boolean)
    gas_level = Column(String(50))
    packages_rts = Column(Integer, default=0)

    # Route
    route_issues = Column(Boolean, default=False)
    route_issue_description = Column(Text)

    # Sweep
    performed_sweep = Column(Boolean, default=False)
    sweep_details = Column(Text)

    # Lunch
    took_lunch = Column(Boolean, default=False)
    lunch_clock_out = Column(String(20))
    lunch_clock_in = Column(String(20))

    # Clock-out
    clock_out_time = Column(String(20))
    pockets_checked = Column(Boolean)

    # HR
    needs_management_contact = Column(Boolean, default=False)

    # Equipment
    all_equipment_present = Column(Boolean)
    missing_equipment = Column(Text)

    # Reminder tracking
    reminder_sent = Column(Boolean, default=False)
    reminder_sent_at = Column(DateTime)

    __table_args__ = (
        Index("idx_eod_date_driver", "survey_date", "driver_name"),
        Index("idx_eod_submitted", "survey_date", "submitted_at"),
        Index("idx_eod_transporter", "transporter_id", "survey_date"),
    )


class ReminderThrottleState(Base):
    """Persisted throttle/dedup state for periodic Slack reminder loops.

    Replaces in-memory module-level dicts (mgt_reminders.py's `_state`,
    dvic.py's and dsp_scorecard_weekly.py's `_reminder_state`) — root cause
    of a 2026-07-13 production incident: those dicts reset to empty on
    every process restart, so a redeploy wiped the "already sent"/"resolved
    today" memory and reminders re-fired in a tight spam loop the moment
    the background loop's next tick ran. State must survive a restart to
    throttle correctly; a JSON blob per reminder_key lets each caller keep
    its own shape (serialize date/datetime fields to ISO strings itself).
    """
    __tablename__ = "reminder_throttle_state"

    id = Column(Integer, primary_key=True)
    reminder_key = Column(String(100), nullable=False, unique=True, index=True)
    state = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SafetyEvent(Base):
    """Netradyne driving-safety event (speeding, roadside parking, etc.)
    from the "Safety Dashboard" CSV export — added 2026-07-14, first
    ingested file: Safety_Dashboard_NDAY_DLV3_2026-07-13.csv.

    One row per real-world safety event, deduped by Netradyne's own
    event_id (unique) — the export is a rolling window, so the same event
    can appear in multiple overlapping uploads; event_id is the natural
    dedup key rather than date+driver (a driver can have multiple events
    on the same day).
    """
    __tablename__ = "safety_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(50), nullable=False, unique=True, index=True)   # Netradyne's own ID
    report_date = Column(Date, nullable=False, index=True)                    # "Date" column — export date
    driver_name = Column(String(150), index=True)                            # "Delivery Associate"
    transporter_id = Column(String(50), index=True)
    event_at = Column(DateTime)                                              # "Date (Station Local Time)"
    vin = Column(String(50))
    program_impact = Column(String(200))       # raw, e.g. "Scorecard, ORCAS" — comma-separated, not split
    metric_type = Column(String(100), index=True)     # e.g. "Speeding", "Roadside Parking"
    metric_subtype = Column(String(150))               # e.g. "Above Posted Speed Limit"
    source = Column(String(50))                # e.g. "Netradyne", "Netradyne on Fleet Edge"
    video_link = Column(String(500))
    review_details = Column(Text)
    source_file = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OkamiCapacityLog(Base):
    """Daily Okami capacity planning numbers — added 2026-07-14. Posted
    today as free-text in Slack by ops (e.g. "61 DAs / Okami 44 /
    Capacity at 50 plus 4x4 = 51 / Vans - 55"), not a file upload, so
    there is no ingest parser for it — it's captured directly via a
    dashboard form (POST /okami-capacity) instead of Slack-message
    scanning. Append-only, one row per submission: mgt_reminders.py
    reads "does a row exist for today" rather than "is there exactly
    one", so a same-day correction is just a fresh submission — the
    latest row for the date wins for display purposes.

    frt (Flex Up Route Target) is Amazon's own daily ask, read off their
    scheduling page as a "Flex up target" row that only appears some
    weeks (a row below the normal "Route target" row) — not every
    station-day has one, hence nullable. When present it's compared
    against capacity_total; when null, the flex-up check is skipped
    entirely rather than treated as a miss.

    Finalization is a separate, explicit step (POST
    .../okami-capacity/finalize) from raw entry/correction — it locks in
    one submission for the day, snapshots the computed checks at that
    moment (so later buffer-% tuning doesn't rewrite history), and is
    what actually posts the #nday-mgt summary / fires threshold DMs.
    """
    __tablename__ = "okami_capacity_logs"

    id = Column(Integer, primary_key=True)
    log_date = Column(Date, nullable=False, index=True)
    da_count = Column(Integer)              # "61 DAs"
    okami_count = Column(Integer)           # "Okami 44"
    capacity_base = Column(Integer)         # "Capacity at 50"
    capacity_4x4 = Column(Integer)          # "plus 4x4" — count of 4x4-eligible add-ons
    capacity_total = Column(Integer)        # "= 51" — capacity_base + capacity_4x4 if both given
    van_count = Column(Integer)             # "Vans - 55"
    frt = Column(Integer, nullable=True)    # Amazon's "Flex up target" row, when one exists this week
    submitted_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Set only by the Finalize action — snapshotted at that moment.
    finalized_at = Column(DateTime, nullable=True)
    finalized_by = Column(String(100), nullable=True)
    required_da_count = Column(Integer, nullable=True)
    da_status = Column(String(20), nullable=True)          # "ok" | "short"
    required_van_count = Column(Integer, nullable=True)
    effective_available_vans = Column(Integer, nullable=True)
    van_status = Column(String(20), nullable=True)          # "ok" | "short"
    van_deficit = Column(Integer, nullable=True)
    grounded_vans_snapshot = Column(JSON, nullable=True)     # [{vin, vehicle_name}, ...] at finalize time
    frt_breached = Column(Boolean, nullable=True)


class OkamiSettings(Base):
    """Singleton (id=1) tunable knobs for Okami finalization — the
    buffer percentages the user asked to tweak over time as they learn
    the right cost/risk tradeoff, plus the count of vehicles (e.g. the
    one 4x4) that don't flow through the normal Okami/fleet-ingest
    pipeline. Deliberately NOT per-day — these change rarely, unlike
    OkamiCapacityLog's per-day numbers.
    """
    __tablename__ = "okami_settings"

    id = Column(Integer, primary_key=True)
    driver_buffer_pct = Column(Integer, nullable=False, default=10)   # 10 -> want DAs >= 110% of capacity_total
    van_buffer_pct = Column(Integer, nullable=False, default=0)
    available_non_okami_vehicles = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(100))


def get_reminder_state(db, reminder_key: str) -> dict:
    row = db.query(ReminderThrottleState).filter(
        ReminderThrottleState.reminder_key == reminder_key
    ).first()
    return dict(row.state) if row and row.state else {}


def set_reminder_state(db, reminder_key: str, state: dict) -> None:
    row = db.query(ReminderThrottleState).filter(
        ReminderThrottleState.reminder_key == reminder_key
    ).first()
    if row:
        row.state = state
    else:
        db.add(ReminderThrottleState(reminder_key=reminder_key, state=state))
    db.commit()


class OpsIngestJob(Base):
    """Tracks every file dropped in #nday-operations-management.

    One row per Slack file share. Status lifecycle:
      pending → ingesting → complete | error | skipped
    """
    __tablename__ = "ops_ingest_jobs"

    id = Column(Integer, primary_key=True)
    slack_file_id = Column(String(50), unique=True, nullable=False, index=True)
    slack_message_ts = Column(String(50))
    slack_message_text = Column(Text)           # description the user typed with the file
    file_name = Column(String(255), nullable=False)
    file_url = Column(String(1000))             # Slack private download URL
    detected_type = Column(String(50), nullable=False, default="unknown")
    status = Column(String(20), nullable=False, default="pending", index=True)
    result_json = Column(JSON)
    error_message = Column(Text)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ingested_at = Column(DateTime)


class MisroutedFileAlert(Base):
    """Every file seen by the hourly misrouted-file watcher (ops_ingest.py's
    check_misrouted_files) in a channel OTHER than #nday-operations-management
    or #dlv3-nday-info — added 2026-07-15 because a real DSP Scorecard sat
    undetected for hours after landing in the wrong channel. One row per
    file evaluated (not just the ones that trigger an alert) so the watcher
    never re-classifies the same file twice; `alerted` distinguishes the
    two cases. All NDAY-sourced ingest is expected in C0BE4ALL1EX — this
    is the safety net for when it isn't.
    """
    __tablename__ = "misrouted_file_alerts"

    id = Column(Integer, primary_key=True)
    slack_file_id = Column(String(50), unique=True, nullable=False, index=True)
    channel_id = Column(String(50), nullable=False)
    file_name = Column(String(255), nullable=False)
    detected_type = Column(String(50), nullable=False)   # whatever _classify() returned
    alerted = Column(Boolean, nullable=False, default=False)  # False = looked "unknown", no alert sent
    seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DriverCallout(Base):
    """Tracks drivers who called out for a specific date.

    Callout rule: drivers on this list drop to the bottom of the assignment
    priority queue for their callout date. They are only assigned a route when
    no non-callout driver is available to cover it (is_callout_coverage flag).
    """
    __tablename__ = "driver_callouts"

    id = Column(Integer, primary_key=True)
    callout_date = Column(Date, nullable=False, index=True)
    transporter_id = Column(String(50), nullable=False, index=True)
    driver_name = Column(String(255), nullable=False)
    callout_type = Column(String(30), nullable=False, default="sick")
    # Values: 'sick' | 'no_show' | 'personal' | 'other'
    notes = Column(Text)
    recorded_by = Column(String(100))   # dispatcher username
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_callout_date_tid", "callout_date", "transporter_id"),
    )


def ensure_assignment_board_columns():
    """Add columns needed by the Route Assignment Board — added 2026-07-03.

    cortex_routes:           transporter_id VARCHAR(50)
    daily_route_assignments: transporter_id VARCHAR(50)
                             quality_rank INTEGER
                             quality_standing VARCHAR(30)
                             is_callout_coverage BOOLEAN
                             departure_time VARCHAR(20)
                             stops INTEGER
                             assignment_status VARCHAR(20)
    """
    migrations = [
        ("cortex_routes",            "transporter_id",      "VARCHAR(50)"),
        ("daily_route_assignments",  "transporter_id",      "VARCHAR(50)"),
        ("daily_route_assignments",  "vin",                 "VARCHAR(50)"),
        ("daily_route_assignments",  "quality_rank",        "INTEGER"),
        ("daily_route_assignments",  "quality_standing",    "VARCHAR(30)"),
        ("daily_route_assignments",  "is_callout_coverage", "BOOLEAN DEFAULT 0"),
        ("daily_route_assignments",  "departure_time",      "VARCHAR(20)"),
        ("daily_route_assignments",  "stops",               "INTEGER"),
        ("daily_route_assignments",  "assignment_status",   "VARCHAR(20) DEFAULT 'pending'"),
    ]
    for table, col, typedef in migrations:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists


def ensure_driver_shift_dm_checklist_columns():
    """Add daily-checklist columns to driver_shift_dms — added 2026-07-09.

    driver_shift_dms: schedule_acked_at  TIMESTAMP (when driver tapped 'Got My Schedule')
                      eod_checklist_at   TIMESTAMP (when driver tapped 'EOD Complete')
    """
    migrations = [
        ("driver_shift_dms", "schedule_acked_at", "TIMESTAMP"),
        ("driver_shift_dms", "eod_checklist_at",  "TIMESTAMP"),
    ]
    for table, col, typedef in migrations:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists


def ensure_crash_report_evidence_columns():
    """Expand crash reports with the full mandatory-evidence set, statement
    sanitization audit trail, and drug-screen tracking — added 2026-07-15.

    crash_reports: 5 new per-category photo columns (vehicle_damage/other_vehicle/
                   dl_driver/dl_other/insurance_other/license_plate_other — the
                   existing photo_urls column keeps covering the 'scene' category,
                   no change needed there), third_party_statement (+ declined
                   flag), *_raw columns holding the verbatim pre-sanitization
                   text, and drug_screen_status.
    drivers:       license_number/license_state, for future crash-report prefill.
    vehicles:      license_plate/license_plate_state/vehicle_year/vehicle_make_model,
                   for future crash-report prefill.
    """
    migrations = [
        ("crash_reports", "photo_vehicle_damage",          "JSON"),
        ("crash_reports", "photo_other_vehicle",           "JSON"),
        ("crash_reports", "photo_dl_driver",                "JSON"),
        ("crash_reports", "photo_dl_other",                 "JSON"),
        ("crash_reports", "photo_insurance_other",          "JSON"),
        ("crash_reports", "photo_license_plate_other",      "JSON"),
        ("crash_reports", "third_party_statement",          "TEXT"),
        ("crash_reports", "third_party_statement_declined", "BOOLEAN DEFAULT 0"),
        ("crash_reports", "accident_description_raw",       "TEXT"),
        ("crash_reports", "third_party_statement_raw",      "TEXT"),
        ("crash_reports", "drug_screen_status",              "VARCHAR(20)"),
        ("drivers",  "license_number", "VARCHAR(50)"),
        ("drivers",  "license_state",  "VARCHAR(10)"),
        ("vehicles", "license_plate",       "VARCHAR(20)"),
        ("vehicles", "license_plate_state", "VARCHAR(10)"),
        ("vehicles", "vehicle_year",        "VARCHAR(10)"),
        ("vehicles", "vehicle_make_model",  "VARCHAR(100)"),
    ]
    for table, col, typedef in migrations:
        try:
            with engine.begin() as conn:
                if DATABASE_URL.startswith("sqlite"):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typedef}"))
        except Exception:
            pass  # Column already exists


# ============================================================================
# ROSTERING & DRIVER SHIFT DMs
# ============================================================================

class WaveLeadNotification(Base):
    """Deduplication tracker for wave-lead pre-wave and missing-summary messages."""
    __tablename__ = "wave_lead_notifications"

    id = Column(Integer, primary_key=True)
    shift_date = Column(Date, nullable=False, index=True)
    wave_time = Column(String(20), nullable=False)
    notif_type = Column(String(20), nullable=False)  # "pre_wave" | "missing_summary"
    sent_at = Column(DateTime, default=datetime.utcnow)
    slack_ts = Column(String(50))
    wave_lead_slack_id = Column(String(50))

    __table_args__ = (
        Index("idx_wln_date_wave_type", "shift_date", "wave_time", "notif_type"),
    )


class NightlyRosterReminder(Base):
    """Deduplication tracker for the 1900-hrs nightly roster reminder DMs."""
    __tablename__ = "nightly_roster_reminders"

    id = Column(Integer, primary_key=True)
    shift_date = Column(Date, nullable=False, unique=True, index=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    driver_count = Column(Integer, default=0)
    reminder_ts_spencer = Column(String(50))
    reminder_ts_luis = Column(String(50))
    reminder_ts_fabian = Column(String(50))


class DriverShiftDM(Base):
    """Tracks the pre-shift DM sent to each driver and their arrival confirmation."""
    __tablename__ = "driver_shift_dms"

    id = Column(Integer, primary_key=True)
    shift_date = Column(Date, nullable=False, index=True)
    driver_name = Column(String(255), nullable=False, index=True)
    slack_user_id = Column(String(50))
    wave_time = Column(String(20))
    showtime = Column(String(20))         # wave_time - 25 min
    wave_lead = Column(String(150))
    dm_ts = Column(String(50))            # Slack message ts of the DM
    dm_sent_at = Column(DateTime)
    arrived_at = Column(DateTime)
    arrived_slack_user_id = Column(String(50))
    arrival_confirmed = Column(Boolean, default=False)
    schedule_acked_at = Column(DateTime)   # when driver tapped 'I've Got My Schedule'
    eod_checklist_at = Column(DateTime)    # when driver tapped 'EOD Complete'
    declined_at = Column(DateTime)         # when driver tapped 'Can't Make It' (Showtime DM)

    __table_args__ = (
        Index("idx_dsdm_date_driver", "shift_date", "driver_name"),
    )


class RtsDebrief(Base):
    """Return-to-Station debrief — driver-reported returns reviewed before heading back."""
    __tablename__ = "rts_debriefs"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)

    shift_date = Column(Date, nullable=False, index=True)
    driver_name = Column(String(150), nullable=False, index=True)
    slack_user_id = Column(String(50))
    route_id = Column(String(20))

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    # Driver-reported return counts
    damaged_count = Column(Integer, default=0)
    reverse_count = Column(Integer, default=0)          # customer return / SWA pickup
    excluded_count = Column(Integer, default=0)         # Business Closed / Refused / Rescheduled — not reattemptable
    reattempt_eligible_count = Column(Integer, default=0)   # candidates that could still be delivered
    reattempt_assigned_count = Column(Integer, default=0)   # driver self-reported as within 10-15 min drive
    reattempt_skipped_count = Column(Integer, default=0)    # too far — handed back to dispatch instead

    expected_return_time = Column(String(20))

    routed_to_rescue = Column(Boolean, default=False)
    rescue_event_id = Column(String(30))

    __table_args__ = (
        Index("idx_rts_date_driver", "shift_date", "driver_name"),
    )


class MgtSummaryPost(Base):
    """Tracks the #nday-mgt roster summary matrix message for each shift date."""
    __tablename__ = "mgt_summary_posts"

    id = Column(Integer, primary_key=True)
    shift_date = Column(Date, nullable=False, unique=True, index=True)
    posted_at = Column(DateTime, default=datetime.utcnow)
    slack_ts = Column(String(50))         # ts for future updates
    driver_count = Column(Integer, default=0)
    risk_flags = Column(Text)             # JSON list of risk strings


# ============================================================================
# CORTEX PACE TRACKING
# ============================================================================

class CortexSnapshot(Base):
    """Every 2-hour Cortex ingest during delivery — tracks route progress for pace prediction."""
    __tablename__ = "cortex_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_at = Column(DateTime, nullable=False, index=True, default=datetime.utcnow)
    route_date = Column(Date, nullable=False, index=True)
    route_code = Column(String(50), nullable=False, index=True)
    driver_name = Column(String(255), index=True)
    wave_time = Column(String(20))
    service_type = Column(String(100))
    packages_total = Column(Integer)
    packages_delivered = Column(Integer)
    packages_remaining = Column(Integer)
    pct_complete = Column(DECIMAL(5, 2))  # 0.00–100.00
    source_file = Column(String(255))

    __table_args__ = (
        Index("idx_cs_date_route", "route_date", "route_code"),
        Index("idx_cs_date_driver", "route_date", "driver_name"),
    )


class DriverRoutePerformance(Base):
    """Historical pace performance by driver — built from CortexSnapshot data.
    Used to predict whether a driver will finish on time based on 2-hr pace."""
    __tablename__ = "driver_route_performance"

    id = Column(Integer, primary_key=True)
    driver_name = Column(String(255), nullable=False, index=True)
    route_date = Column(Date, nullable=False, index=True)
    route_code = Column(String(50), index=True)
    service_type = Column(String(100))
    wave_time = Column(String(20))
    pct_at_2hr = Column(DECIMAL(5, 2))    # % complete at ~2hr mark
    pct_at_4hr = Column(DECIMAL(5, 2))
    final_pct = Column(DECIMAL(5, 2))
    finished_on_time = Column(Boolean)
    snapshot_count = Column(Integer, default=0)

    __table_args__ = (
        Index("idx_drp_driver_date", "driver_name", "route_date"),
    )


class RouteBandDefinition(Base):
    """Calibrated route-code-number bands — added 2026-07-19 as a proxy for
    geographic clustering, since nearby route numbers tend to be
    geographically close on this DSP and no real lat/long data exists
    anywhere in this system (Cortex.zone is always None — never populated
    by the ingest parser). Bands are inferred by finding unusually large
    gaps between consecutive distinct route numbers actually run
    (route_bands.py's calibrate_bands()) rather than a fixed width, per
    explicit 2026-07-19 decision to learn boundaries from real data instead
    of guessing a width. Re-calibrating replaces all existing rows wholesale
    (same pattern as OkamiSettings) — this is a periodically-refreshed
    config, not per-day data."""
    __tablename__ = "route_band_definitions"

    id = Column(Integer, primary_key=True)
    band_label = Column(String(20), nullable=False)   # e.g. "121-155"
    range_start = Column(Integer, nullable=False)
    range_end = Column(Integer, nullable=False)
    calibrated_at = Column(DateTime, default=datetime.utcnow)
    distinct_routes_used = Column(Integer)   # how many distinct route numbers informed this calibration


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
