"""Data models for ingest and validation."""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RouteDOP:
    """DOP (Day of Plan) route record."""
    dsp: str
    route_code: str
    service_type: str
    wave: str
    staging_location: str
    route_duration: int
    num_zones: Optional[int] = None
    num_packages: Optional[int] = None
    num_commercial_pkgs: Optional[int] = None


@dataclass
class Vehicle:
    """Fleet vehicle record."""
    vin: str
    service_type: str
    vehicle_name: str
    operational_status: str


@dataclass
class RouteSheetBag:
    """Bag entry from route sheet PDF."""
    bag_id: str
    sort_zone: str
    color_code: str
    package_count: int


@dataclass
class RouteSheetOverflow:
    """Overflow entry from route sheet PDF."""
    sort_zone: str
    bag_code: str
    package_count: int


@dataclass
class RouteSheet:
    """Route sheet PDF record."""
    route_code: str
    staging_location: str
    service_type: str
    wave_time: str
    dsp: str
    bags: List[RouteSheetBag] = field(default_factory=list)
    overflow: List[RouteSheetOverflow] = field(default_factory=list)
    total_packages: int = 0
    total_bags: int = 0
    expected_return: Optional[str] = None  # Calculated as wave_time + route_duration - 30 min


@dataclass
class CortexRoute:
    """Cortex route assignment record."""
    transporter_id: str
    driver_name: str
    dsp: str
    route_code: str
    delivery_service_type: str
    cortex_vin_number: Optional[str] = None
    progress_status: Optional[str] = None
    projected_return: Optional[str] = None


@dataclass
class IngestStatus:
    """Overall ingest status and validation results."""
    dop_uploaded: bool = False
    fleet_uploaded: bool = False
    cortex_uploaded: bool = False
    route_sheets_uploaded: bool = False
    dop_records: List[RouteDOP] = field(default_factory=list)
    fleet_records: List[Vehicle] = field(default_factory=list)
    cortex_records: List[CortexRoute] = field(default_factory=list)
    route_sheets: List[RouteSheet] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)
