"""Vehicle assignment engine - matches routes to fleet vehicles by service type."""
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from api.src.models import RouteDOP, Vehicle, CortexRoute
from api.src.driver_van_affinity import affinity_tracker


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
    
    def _build_vehicle_pool(self) -> Dict[str, List[Vehicle]]:
        """Build pool of vehicles organized by service type."""
        pool = {}
        for vehicle in self.fleet:
            service_type = vehicle.service_type
            if service_type not in pool:
                pool[service_type] = []
            pool[service_type].append(vehicle)
        return pool
    
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
        }
