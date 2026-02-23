"""Vehicle assignment engine - matches routes to fleet vehicles by service type."""
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass
from api.src.models import RouteDOP, Vehicle, CortexRoute
from api.src.driver_van_affinity import affinity_tracker
from api.src.van_capacities import (
    get_van_capacity,
    get_capacity_percentage,
    is_van_at_capacity_threshold,
    is_van_over_capacity,
    is_van_electric,
    is_route_electric,
)


@dataclass
class RouteAssignment:
    """Assigned vehicle and driver for a route."""
    route_code: str
    vehicle_vin: str
    vehicle_name: str
    service_type: str
    driver_name: Optional[str] = None
    driver_id: Optional[str] = None
    dsp: Optional[str] = None
    wave_time: Optional[str] = None  # For sorting (e.g., "10:20 AM")
    route_duration: Optional[int] = None  # Route duration in minutes
    num_packages: Optional[int] = None  # Number of packages on this route
    estimated_cubic_feet: Optional[float] = None  # Estimated cubic footage of packages


@dataclass
class VanCapacityStatus:
    """Status of van capacity utilization."""
    vehicle_name: str
    service_type: str
    max_bags: int
    max_cubic_feet: float
    current_bags: int
    current_cubic_feet: float
    bag_percentage: float  # 0-100
    cubic_percentage: float  # 0-100
    bags_remaining: int
    cubic_remaining: float
    is_at_threshold: bool  # True if >85% full
    is_over_capacity: bool  # True if exceeds limits


class VehicleAssignmentEngine:
    """Matches routes to fleet vehicles by service type with fallback rules."""
    
    # Service type fallback chain (primary → fallback options)
    # Per governance: if primary unavailable, try fallbacks in order
    FALLBACK_CHAIN = {
        "Standard Parcel - Custom Delivery Van 14ft": [
            "Standard Parcel - Custom Delivery Van 14ft",
            "Standard Parcel - Custom Delivery Van 16ft",  # CDV14→CDV16 fallback
            "Standard Parcel - Extra Large Van - US",
        ],
        "Standard Parcel - Custom Delivery Van 16ft": [
            "Standard Parcel - Custom Delivery Van 16ft",
            "Standard Parcel - Extra Large Van - US",
        ],
        "Standard Parcel - Extra Large Van - US": [
            "Standard Parcel - Extra Large Van - US",
            "Standard Parcel - Custom Delivery Van 16ft",  # Allow smaller alt
        ],
        "4WD P31 Delivery Truck": [
            "4WD P31 Delivery Truck",
        ],
        "Rivian MEDIUM": [
            "Rivian MEDIUM",
            "Rivian LARGE",  # Upsize if needed
        ],
        "Rivian LARGE": [
            "Rivian LARGE",
            "Rivian MEDIUM",  # Note: downsize as fallback (risky but doable for light routes)
        ],
        "Electric Step Van - XL": [
            "Electric Step Van - XL",
            "Electric Cargo Van - L",
        ],
        "Electric Cargo Van - M": [
            "Electric Cargo Van - M",
            "Electric Cargo Van - L",
        ],
        "Electric Cargo Van - L": [
            "Electric Cargo Van - L",
            "Electric Step Van - XL",
        ],
    }
    
    # Default fallback for unrecognized types (permissive)
    DEFAULT_FALLBACK = [
        "Standard Parcel - Extra Large Van - US",
        "Standard Parcel - Custom Delivery Van 16ft",
    ]
    
    def __init__(self, fleet: List[Vehicle]):
        """Initialize with available fleet vehicles."""
        self.fleet = fleet
        self.vehicle_pool = self._build_vehicle_pool()
        self.assignments: Dict[str, RouteAssignment] = {}
        self.failed_assignments: List[Tuple[str, str]] = []  # (route_code, reason)
        self.fallback_assignments: List[Tuple[str, str, str]] = []  # (route_code, requested_type, assigned_type)
        
        # Capacity tracking: vin -> current bag count
        self.van_loads: Dict[str, int] = {vehicle.vin: 0 for vehicle in fleet}
        # Vehicle info lookup: vin -> vehicle
        self.vehicle_info: Dict[str, Vehicle] = {vehicle.vin: vehicle for vehicle in fleet}
        # Capacity warnings: list of (route_code, van_name, bags, max_bags, percentage)
        self.capacity_warnings: List[Tuple[str, str, int, int, float]] = []
        
        # Electric van constraint violations (electric van on non-electric route without authorization)
        # (route_code, van_name, service_type, route_service_type)
        self.electric_van_violations: List[Tuple[str, str, str, str]] = []
        # User-authorized electric van assignments: set of route_codes approved by user
        self.authorized_electric_assignments: Set[str] = set()
    
    def _build_vehicle_pool(self) -> Dict[str, List[Vehicle]]:
        """Build pool of vehicles organized by service type."""
        pool = {}
        for vehicle in self.fleet:
            service_type = vehicle.service_type
            if service_type not in pool:
                pool[service_type] = []
            pool[service_type].append(vehicle)
        return pool
    
    def _can_fit_in_van(self, vehicle_vin: str, route_packages: int) -> bool:
        """
        Check if a route's packages will fit in a van within capacity limits.
        
        Args:
            vehicle_vin: VIN of the vehicle
            route_packages: Number of packages on the route
        
        Returns:
            True if the route fits within capacity
        """
        if vehicle_vin not in self.vehicle_info:
            return False
        
        vehicle = self.vehicle_info[vehicle_vin]
        capacity_data = get_van_capacity(vehicle.service_type)
        
        if not capacity_data:
            # No capacity data, assume it fits
            return True
        
        current_load = self.van_loads.get(vehicle_vin, 0)
        total_after = current_load + route_packages
        max_bags = capacity_data["max_bags"]
        
        return total_after <= max_bags
    
    def _get_current_van_capacity_percent(self, vehicle_vin: str) -> float:
        """Get current capacity utilization percentage for a van."""
        if vehicle_vin not in self.vehicle_info:
            return 0.0
        
        vehicle = self.vehicle_info[vehicle_vin]
        capacity_data = get_van_capacity(vehicle.service_type)
        
        if not capacity_data:
            return 0.0
        
        current_load = self.van_loads.get(vehicle_vin, 0)
        return (current_load / capacity_data["max_bags"] * 100) if capacity_data["max_bags"] > 0 else 0.0
    
    def _find_best_available_van(self, service_type: str, route_packages: int, fallback_chain: List[str]) -> Optional[Vehicle]:
        """
        Find the best available van that can fit the route's packages.
        Prefers vans with more remaining capacity to avoid overloading.
        
        Args:
            service_type: Primary service type
            route_packages: Number of packages on the route
            fallback_chain: Service types to try in order
        
        Returns:
            Best available Vehicle or None
        """
        for try_service_type in fallback_chain:
            available_vans = self.vehicle_pool.get(try_service_type, [])
            
            # Filter vans that have capacity
            vans_with_capacity = [
                van for van in available_vans
                if self._can_fit_in_van(van.vin, route_packages)
            ]
            
            if vans_with_capacity:
                # Sort by remaining capacity (prefer less full vans for balanced loading)
                vans_with_capacity.sort(
                    key=lambda v: self.van_loads.get(v.vin, 0),
                    reverse=False  # Least full first
                )
                return vans_with_capacity[0]
        
        return None
    
    def _is_electric_constraint_violation(self, van_service_type: str, route_service_type: str) -> bool:
        """
        Check if assigning this van to this route violates the electric van constraint.
        Electric vans can ONLY be used on electric routes unless user-authorized.
        
        Args:
            van_service_type: Service type of the van
            route_service_type: Service type of the route
        
        Returns:
            True if this is a violation (electric van on non-electric route)
        """
        van_is_electric = is_van_electric(van_service_type)
        route_is_electric = is_route_electric(route_service_type)
        
        # Violation: electric van on non-electric route
        return van_is_electric and not route_is_electric
    
    def assign_routes(
        self,
        routes: List[RouteDOP],
        cortex_records: Optional[List[CortexRoute]] = None,
    ) -> Dict[str, RouteAssignment]:
        """
        Assign fleet vehicles to routes based on service type.
        
        Args:
            routes: List of DOP routes to assign
            cortex_records: Optional Cortex driver assignments (enriches assignment with driver name)
        
        Returns:
            Dictionary of route_code → RouteAssignment
        """
        self.assignments = {}
        self.failed_assignments = []
        self.fallback_assignments = []
        
        # Build driver lookup if Cortex records provided
        driver_lookup = {}
        if cortex_records:
            driver_lookup = {
                record.route_code: record for record in cortex_records
            }
        
        for route in routes:
            assignment = self._assign_route(route, driver_lookup)
            if assignment:
                self.assignments[route.route_code] = assignment
            else:
                self.failed_assignments.append((route.route_code, "No available vehicle"))
        
        return self.assignments
    
    def _assign_route(
        self,
        route: RouteDOP,
        driver_lookup: Dict[str, CortexRoute],
    ) -> Optional[RouteAssignment]:
        """Assign a single route to a vehicle with affinity and fallback logic."""
        
        # Get driver info if available from Cortex
        driver_record = driver_lookup.get(route.route_code)
        driver_name = driver_record.driver_name if driver_record else None
        driver_id = driver_record.transporter_id if driver_record else None
        dsp = driver_record.dsp if driver_record else None
        
        # FIRST: Try driver-van affinity (if driver is available)
        if driver_name:
            preferred_vehicle_name = affinity_tracker.get_preferred_vehicle(
                driver_name, route.service_type, days_back=7
            )
            
            if preferred_vehicle_name:
                # Check if this vehicle is still available
                available_vehicles = self.vehicle_pool.get(route.service_type, [])
                for idx, vehicle in enumerate(available_vehicles):
                    if vehicle.vehicle_name == preferred_vehicle_name:
                        # Check electric van constraint
                        if self._is_electric_constraint_violation(vehicle.service_type, route.service_type):
                            if route.route_code not in self.authorized_electric_assignments:
                                # Skip this vehicle, it violates electric constraint
                                continue
                            # Otherwise, it's user-authorized, proceed
                        
                        # Found the preferred vehicle! Use it with affinity priority
                        assigned_vehicle = available_vehicles.pop(idx)
                        
                        assignment = RouteAssignment(
                            route_code=route.route_code,
                            vehicle_vin=assigned_vehicle.vin,
                            vehicle_name=assigned_vehicle.vehicle_name,
                            service_type=route.service_type,
                            driver_name=driver_name,
                            driver_id=driver_id,
                            dsp=dsp,
                            wave_time=route.wave,
                            route_duration=route.route_duration,
                        )
                        
                        # Record this assignment for future affinity tracking
                        affinity_tracker.record_assignment(
                            driver_name, assigned_vehicle.vehicle_name, route.service_type, route.route_code
                        )
                        
                        return assignment
        
        # SECOND: Try normal fallback chain
        fallback_chain = self.FALLBACK_CHAIN.get(route.service_type, self.DEFAULT_FALLBACK)
        
        # Try each fallback option in order
        for fallback_service_type in fallback_chain:
            available_vehicles = self.vehicle_pool.get(fallback_service_type, [])
            
            if available_vehicles:
                # Pick first available vehicle (FIFO)
                vehicle = available_vehicles[0]
                
                # Check electric van constraint BEFORE assigning
                if self._is_electric_constraint_violation(vehicle.service_type, route.service_type):
                    if route.route_code not in self.authorized_electric_assignments:
                        # Record violation and skip this vehicle
                        self.electric_van_violations.append(
                            (route.route_code, vehicle.vehicle_name, vehicle.service_type, route.service_type)
                        )
                        # Skip to next vehicle type
                        continue
                    # Otherwise, it's user-authorized, proceed
                
                # REMOVE vehicle from pool so it won't be assigned again
                available_vehicles.pop(0)
                
                # Track if this was a fallback assignment
                if fallback_service_type != route.service_type:
                    self.fallback_assignments.append(
                        (route.route_code, route.service_type, fallback_service_type)
                    )
                
                assignment = RouteAssignment(
                    route_code=route.route_code,
                    vehicle_vin=vehicle.vin,
                    vehicle_name=vehicle.vehicle_name,
                    service_type=fallback_service_type,
                    driver_name=driver_name,
                    driver_id=driver_id,
                    dsp=dsp,
                    wave_time=route.wave,
                    route_duration=route.route_duration,
                )
                
                # Record this assignment for future affinity tracking (if driver available)
                if driver_name:
                    affinity_tracker.record_assignment(
                        driver_name, vehicle.vehicle_name, fallback_service_type, route.route_code
                    )
                
                return assignment
        
        # No vehicle found even with fallbacks
        return None
    
    def get_assignment_status(self) -> Dict:
        """Return detailed assignment status."""
        total_routes = len(self.assignments) + len(self.failed_assignments)
        
        return {
            "total_routes": total_routes,
            "assigned": len(self.assignments),
            "failed": len(self.failed_assignments),
            "fallback_used": len(self.fallback_assignments),
            "success_rate": round(100 * len(self.assignments) / total_routes, 1) if total_routes > 0 else 0,
            "failed_routes": [route_code for route_code, _ in self.failed_assignments],
            "fallback_routes": [
                {
                    "route_code": route_code,
                    "requested_type": requested,
                    "assigned_type": assigned,
                }
                for route_code, requested, assigned in self.fallback_assignments
            ],
            "electric_van_violations": [
                {
                    "route_code": route_code,
                    "van_name": van_name,
                    "van_type": van_type,
                    "route_type": route_type,
                    "message": f"Electric van '{van_name}' cannot be used on non-electric route '{route_code}' ({route_type})",
                }
                for route_code, van_name, van_type, route_type in self.electric_van_violations
            ],
            "has_electric_violations": len(self.electric_van_violations) > 0,
            "electric_violation_count": len(self.electric_van_violations),
        }
    
    def get_capacity_status(self) -> Dict:
        """
        Get capacity utilization by service type.
        Aggregates all assigned bags per service type and compares to limits.
        
        Returns:
            Dictionary with service type → capacity status
            Includes alerts for types at 80%+ capacity
        """
        from collections import defaultdict
        
        # Count total bags per service type
        bags_by_service = defaultdict(int)
        assignments_by_service = defaultdict(list)
        
        for assignment in self.assignments.values():
            service_type = assignment.service_type
            # Count packages if available, else estimate 1 package per route
            num_packages = assignment.num_packages or 1
            bags_by_service[service_type] += num_packages
            assignments_by_service[service_type].append(assignment)
        
        # Build capacity status for each service type used
        capacity_status = {}
        alerts = []
        
        for service_type, total_bags in bags_by_service.items():
            capacity_data = get_van_capacity(service_type)
            
            if capacity_data:
                max_bags = capacity_data["max_bags"]
                percentage = (total_bags / max_bags * 100) if max_bags > 0 else 0
                is_alert = percentage >= 80.0
                
                capacity_status[service_type] = {
                    "total_bags": total_bags,
                    "max_bags": max_bags,
                    "percentage": round(percentage, 1),
                    "routes_assigned": len(assignments_by_service[service_type]),
                    "bags_remaining": max(0, max_bags - total_bags),
                    "is_at_threshold": is_alert,
                }
                
                if is_alert:
                    alerts.append({
                        "service_type": service_type,
                        "total_bags": total_bags,
                        "max_bags": max_bags,
                        "percentage": round(percentage, 1),
                        "message": f"{service_type}: {round(percentage, 1)}% capacity ({total_bags}/{max_bags} bags)",
                    })
        
        return {
            "by_service_type": capacity_status,
            "alerts": alerts,
            "has_alerts": len(alerts) > 0,
            "alert_count": len(alerts),
        }
