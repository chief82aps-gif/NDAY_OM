"""Driver performance metrics tracking by route code."""
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import json
import os


@dataclass
class PerformanceRecord:
    """Record of a single driver performance event."""
    driver_name: str
    route_code: str
    assignment_date: str
    wave_time: str
    show_time: str
    scheduled_start: str
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    packages_delivered: int = 0
    stops_completed: int = 0
    on_time: bool = False  # True if arrived by show_time
    completion_rate: float = 0.0  # 0-1
    customer_rating: Optional[float] = None  # 1-5
    notes: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DriverRouteStats:
    """Aggregated statistics for a driver on a specific route."""
    driver_name: str
    route_code: str
    total_assignments: int = 0
    total_completions: int = 0
    on_time_count: int = 0
    avg_packages: float = 0.0
    avg_stops: float = 0.0
    avg_rating: float = 0.0
    completion_rate: float = 0.0
    on_time_percentage: float = 0.0
    last_assignment: Optional[str] = None
    
    def get_on_time_percentage(self) -> float:
        """Calculate on-time percentage."""
        if self.total_assignments == 0:
            return 0.0
        return (self.on_time_count / self.total_assignments) * 100


class PerformanceMetricsTracker:
    """Track and analyze driver performance metrics."""
    
    def __init__(self, storage_file: str = None):
        """Initialize performance tracker with optional file storage."""
        self.storage_file = storage_file or os.path.join(
            os.path.dirname(__file__),
            "../../uploads/performance_metrics.json"
        )
        self.performance_records: List[PerformanceRecord] = []
        self.stats_cache: Dict[Tuple[str, str], DriverRouteStats] = {}
        self._load_from_file()
    
    def record_performance(self, record: PerformanceRecord) -> bool:
        """Record a performance event."""
        try:
            self.performance_records.append(record)
            self._invalidate_cache(record.driver_name, record.route_code)
            self._save_to_file()
            return True
        except Exception as e:
            print(f"Failed to record performance: {str(e)}")
            return False
    
    def record_delivery(
        self,
        driver_name: str,
        route_code: str,
        assignment_date: str,
        wave_time: str,
        show_time: str,
        packages_delivered: int,
        stops_completed: int,
        on_time: bool,
        customer_rating: Optional[float] = None,
    ) -> bool:
        """Record a delivery completion."""
        record = PerformanceRecord(
            driver_name=driver_name,
            route_code=route_code,
            assignment_date=assignment_date,
            wave_time=wave_time,
            show_time=show_time,
            scheduled_start=show_time,
            packages_delivered=packages_delivered,
            stops_completed=stops_completed,
            on_time=on_time,
            completion_rate=1.0,
            customer_rating=customer_rating,
        )
        return self.record_performance(record)
    
    def get_driver_route_stats(self, driver_name: str, route_code: str) -> DriverRouteStats:
        """Get aggregated stats for a driver on a specific route."""
        cache_key = (driver_name, route_code)
        
        # Return cached if available
        if cache_key in self.stats_cache:
            return self.stats_cache[cache_key]
        
        # Calculate from records
        relevant_records = [
            r for r in self.performance_records
            if r.driver_name == driver_name and r.route_code == route_code
        ]
        
        if not relevant_records:
            stats = DriverRouteStats(driver_name=driver_name, route_code=route_code)
        else:
            stats = DriverRouteStats(
                driver_name=driver_name,
                route_code=route_code,
                total_assignments=len(relevant_records),
                total_completions=len([r for r in relevant_records if r.completion_rate > 0]),
                on_time_count=len([r for r in relevant_records if r.on_time]),
                avg_packages=sum(r.packages_delivered for r in relevant_records) / len(relevant_records),
                avg_stops=sum(r.stops_completed for r in relevant_records) / len(relevant_records),
                avg_rating=sum(r.customer_rating for r in relevant_records if r.customer_rating) / max(1, len([r for r in relevant_records if r.customer_rating])),
                completion_rate=sum(r.completion_rate for r in relevant_records) / len(relevant_records),
                last_assignment=relevant_records[-1].assignment_date if relevant_records else None,
            )
            stats.on_time_percentage = stats.get_on_time_percentage()
        
        self.stats_cache[cache_key] = stats
        return stats
    
    def get_driver_performance_summary(self, driver_name: str) -> Dict:
        """Get performance summary across all routes for a driver."""
        driver_routes = {}
        
        for record in self.performance_records:
            if record.driver_name == driver_name:
                if record.route_code not in driver_routes:
                    driver_routes[record.route_code] = []
                driver_routes[record.route_code].append(record)
        
        summary = {
            "driver_name": driver_name,
            "total_assignments": len(self.performance_records),
            "routes": {}
        }
        
        for route_code, records in driver_routes.items():
            stats = self.get_driver_route_stats(driver_name, route_code)
            summary["routes"][route_code] = {
                "assignments": stats.total_assignments,
                "on_time_percentage": stats.on_time_percentage,
                "avg_packages": round(stats.avg_packages, 1),
                "avg_stops": round(stats.avg_stops, 1),
                "avg_rating": round(stats.avg_rating, 2) if stats.avg_rating > 0 else None,
                "completion_rate": round(stats.completion_rate * 100, 1),
                "last_assignment": stats.last_assignment,
            }
        
        return summary
    
    def get_route_performance_stats(self, route_code: str) -> Dict:
        """Get performance stats for all drivers on a specific route."""
        route_records = [r for r in self.performance_records if r.route_code == route_code]
        
        if not route_records:
            return {"route_code": route_code, "drivers": {}}
        
        # Group by driver
        drivers_on_route = {}
        for record in route_records:
            if record.driver_name not in drivers_on_route:
                drivers_on_route[record.driver_name] = []
            drivers_on_route[record.driver_name].append(record)
        
        drivers_stats = {}
        for driver_name, records in drivers_on_route.items():
            stats = self.get_driver_route_stats(driver_name, route_code)
            drivers_stats[driver_name] = {
                "assignments": stats.total_assignments,
                "on_time_percentage": stats.on_time_percentage,
                "avg_packages": round(stats.avg_packages, 1),
                "avg_stops": round(stats.avg_stops, 1),
                "avg_rating": round(stats.avg_rating, 2) if stats.avg_rating > 0 else None,
            }
        
        return {
            "route_code": route_code,
            "drivers": drivers_stats,
            "total_assignments": len(route_records),
        }
    
    def get_top_performers(self, route_code: str = None, metric: str = "on_time_percentage", limit: int = 10) -> List[Dict]:
        """Get top performing drivers, optionally filtered by route."""
        stats_list = []
        
        if route_code:
            records = [r for r in self.performance_records if r.route_code == route_code]
            drivers = set(r.driver_name for r in records)
            for driver in drivers:
                stats = self.get_driver_route_stats(driver, route_code)
                stats_list.append(stats)
        else:
            # Across all routes
            all_drivers = set(r.driver_name for r in self.performance_records)
            for driver in all_drivers:
                # Calculate overall stats
                driver_records = [r for r in self.performance_records if r.driver_name == driver]
                if driver_records:
                    avg_rating = sum(r.customer_rating for r in driver_records if r.customer_rating) / max(1, len([r for r in driver_records if r.customer_rating]))
                    on_time_pct = len([r for r in driver_records if r.on_time]) / len(driver_records) * 100
                    
                    # Create pseudo-stats for sorting
                    class OverallStats:
                        def __init__(self, driver, records):
                            self.driver_name = driver
                            self.avg_rating = avg_rating
                            self.on_time_percentage = on_time_pct
                            self.completion_rate = sum(r.completion_rate for r in records) / len(records)
                            self.avg_packages = sum(r.packages_delivered for r in records) / len(records)
                    
                    stats_list.append(OverallStats(driver, driver_records))
        
        # Sort by metric
        if metric == "on_time_percentage":
            stats_list.sort(key=lambda x: x.on_time_percentage, reverse=True)
        elif metric == "avg_rating":
            stats_list.sort(key=lambda x: getattr(x, 'avg_rating', 0), reverse=True)
        elif metric == "avg_packages":
            stats_list.sort(key=lambda x: x.avg_packages, reverse=True)
        
        return [
            {
                "driver_name": s.driver_name,
                "on_time_percentage": round(s.on_time_percentage, 1),
                "avg_rating": round(s.avg_rating, 2) if hasattr(s, 'avg_rating') else None,
                "avg_packages": round(s.avg_packages, 1) if hasattr(s, 'avg_packages') else None,
            }
            for s in stats_list[:limit]
        ]
    
    def _invalidate_cache(self, driver_name: str, route_code: str):
        """Invalidate cache for a driver-route combination."""
        cache_key = (driver_name, route_code)
        if cache_key in self.stats_cache:
            del self.stats_cache[cache_key]
    
    def _save_to_file(self):
        """Save performance records to file."""
        try:
            os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
            records_dict = [
                {
                    "driver_name": r.driver_name,
                    "route_code": r.route_code,
                    "assignment_date": r.assignment_date,
                    "wave_time": r.wave_time,
                    "show_time": r.show_time,
                    "packages_delivered": r.packages_delivered,
                    "stops_completed": r.stops_completed,
                    "on_time": r.on_time,
                    "customer_rating": r.customer_rating,
                    "recorded_at": r.recorded_at,
                }
                for r in self.performance_records
            ]
            with open(self.storage_file, "w") as f:
                json.dump(records_dict, f, indent=2)
        except Exception as e:
            print(f"Failed to save performance metrics: {str(e)}")
    
    def _load_from_file(self):
        """Load performance records from file."""
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, "r") as f:
                    records_dict = json.load(f)
                    for r in records_dict:
                        record = PerformanceRecord(
                            driver_name=r["driver_name"],
                            route_code=r["route_code"],
                            assignment_date=r["assignment_date"],
                            wave_time=r["wave_time"],
                            show_time=r["show_time"],
                            packages_delivered=r.get("packages_delivered", 0),
                            stops_completed=r.get("stops_completed", 0),
                            on_time=r.get("on_time", False),
                            customer_rating=r.get("customer_rating"),
                            recorded_at=r.get("recorded_at", datetime.now().isoformat()),
                        )
                        self.performance_records.append(record)
        except Exception as e:
            print(f"Failed to load performance metrics: {str(e)}")


# Global instance
performance_tracker = PerformanceMetricsTracker()
