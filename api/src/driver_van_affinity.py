"""Driver-Van Affinity Management - Tracks and applies driver-van affinity for consistent assignments."""
import json
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

# Store affinity data in uploads directory
AFFINITY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'uploads',
    'driver_van_affinity.json'
)


class DriverVanAffinity:
    """Manages driver-van affinity relationships for day-over-day consistency."""

    def __init__(self):
        """Initialize affinity tracker."""
        self.affinities: Dict[str, List[Dict]] = {}
        self._load_affinities()

    def _load_affinities(self) -> None:
        """Load affinity data from file if it exists."""
        try:
            if os.path.exists(AFFINITY_FILE):
                with open(AFFINITY_FILE, 'r') as f:
                    self.affinities = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load affinity data: {e}")
            self.affinities = {}

    def _save_affinities(self) -> None:
        """Save affinity data to file."""
        try:
            os.makedirs(os.path.dirname(AFFINITY_FILE), exist_ok=True)
            with open(AFFINITY_FILE, 'w') as f:
                json.dump(self.affinities, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save affinity data: {e}")

    def record_assignment(
        self,
        driver_name: str,
        vehicle_name: str,
        service_type: str,
        route_code: str,
    ) -> None:
        """Record a successful assignment for affinity tracking.
        
        Args:
            driver_name: Name of assigned driver
            vehicle_name: Name of assigned vehicle
            service_type: Service type of the route
            route_code: Route code for reference
        """
        if not driver_name or not vehicle_name or not service_type:
            return

        # Create affinity key: driver + service type
        affinity_key = f"{driver_name}|{service_type}"

        if affinity_key not in self.affinities:
            self.affinities[affinity_key] = []

        # Check if this exact vehicle association already exists
        existing = next(
            (a for a in self.affinities[affinity_key] if a['vehicle_name'] == vehicle_name),
            None
        )

        if existing:
            # Update last used date and frequency
            existing['last_used'] = datetime.now().isoformat()
            existing['frequency'] += 1
            existing['routes'].append(route_code)
        else:
            # Add new affinity
            self.affinities[affinity_key].append({
                'vehicle_name': vehicle_name,
                'service_type': service_type,
                'first_used': datetime.now().isoformat(),
                'last_used': datetime.now().isoformat(),
                'frequency': 1,
                'routes': [route_code],
            })

        self._save_affinities()

    def get_preferred_vehicle(
        self,
        driver_name: str,
        service_type: str,
        days_back: int = 7,
    ) -> Optional[str]:
        """Get the preferred vehicle for a driver-service type combination.
        
        Args:
            driver_name: Driver name to look up
            service_type: Service type to match
            days_back: Only consider affinities from the last N days
        
        Returns:
            Preferred vehicle name if found and recent, None otherwise
        """
        affinity_key = f"{driver_name}|{service_type}"

        if affinity_key not in self.affinities:
            return None

        affinities_list = self.affinities[affinity_key]
        if not affinities_list:
            return None

        # Find the most recently used vehicle (highest frequency and recent)
        cutoff_date = datetime.now() - timedelta(days=days_back)

        for affinity in sorted(
            affinities_list, key=lambda x: (-x['frequency'], x['last_used']), reverse=True
        ):
            last_used = datetime.fromisoformat(affinity['last_used'])
            if last_used > cutoff_date:
                return affinity['vehicle_name']

        return None

    def get_affinity_strength(
        self,
        driver_name: str,
        vehicle_name: str,
        service_type: str,
    ) -> int:
        """Get the strength of an affinity (frequency of past assignments).
        
        Args:
            driver_name: Driver name
            vehicle_name: Vehicle name
            service_type: Service type
        
        Returns:
            Frequency count (0 if no affinity)
        """
        affinity_key = f"{driver_name}|{service_type}"

        if affinity_key not in self.affinities:
            return 0

        for affinity in self.affinities[affinity_key]:
            if affinity['vehicle_name'] == vehicle_name:
                return affinity['frequency']

        return 0

    def get_driver_summary(self, driver_name: str) -> Dict:
        """Get summary of all affinities for a driver.
        
        Args:
            driver_name: Driver name
        
        Returns:
            Summary of vehicle-service type combinations
        """
        summary = {}

        for key, affinities_list in self.affinities.items():
            if key.startswith(driver_name + '|'):
                service_type = key.split('|')[1]
                for affinity in affinities_list:
                    summary[f"{affinity['vehicle_name']} ({service_type})"] = {
                        'frequency': affinity['frequency'],
                        'last_used': affinity['last_used'],
                        'routes': len(affinity['routes']),
                    }

        return summary

    def clear_old_affinities(self, days_old: int = 30) -> int:
        """Clean up very old affinity records.
        
        Args:
            days_old: Remove affinities older than this many days
        
        Returns:
            Number of affinities removed
        """
        cutoff_date = datetime.now() - timedelta(days=days_old)
        removed_count = 0

        for key in list(self.affinities.keys()):
            affinities_list = self.affinities[key]
            original_count = len(affinities_list)

            # Keep only recent affinities
            self.affinities[key] = [
                a for a in affinities_list
                if datetime.fromisoformat(a['last_used']) > cutoff_date
            ]

            removed_count += original_count - len(self.affinities[key])

            # Remove key if no affinities left
            if not self.affinities[key]:
                del self.affinities[key]

        if removed_count > 0:
            self._save_affinities()

        return removed_count


# Global instance
affinity_tracker = DriverVanAffinity()
