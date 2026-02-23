"""Van capacity limits by service type - max bags and cubic footage."""

# Van capacity data: max bags (off-peak) and BAC (Bag Aware Cubic Capacity)
# Also tracks: is_electric (if True, should only be used on electric routes)
VAN_CAPACITIES = {
    "Small Van": {
        "max_bags": 16,
        "cubic_capacity": 168.62,
        "is_electric": False,
        "aliases": ["small van"],
    },
    "Standard Parcel - Custom Delivery Van 14ft": {
        "max_bags": 43,
        "cubic_capacity": 501.21,
        "is_electric": False,
        "aliases": ["cdv14", "custom delivery van 14ft", "custom delivery van 14", "cdv 14"],
    },
    "Standard Parcel - Custom Delivery Van 16ft": {
        "max_bags": 48,
        "cubic_capacity": 579.89,
        "is_electric": False,
        "aliases": ["cdv16", "custom delivery van 16ft", "custom delivery van 16", "cdv 16"],
    },
    "Standard Parcel - Extra Large Van - US": {
        "max_bags": 22,
        "cubic_capacity": 286.96,
        "is_electric": False,
        "aliases": ["extra large van", "extra large van - us", "elv"],
    },
    "Large Van": {
        "max_bags": 20,
        "cubic_capacity": 257.54,
        "is_electric": False,
        "aliases": ["large van"],
    },
    "Standard Parcel": {  # Generic standard parcel
        "max_bags": 20,
        "cubic_capacity": 251.20,
        "is_electric": False,
        "aliases": ["standard parcel"],
    },
    "4WD P31 Delivery Truck": {
        "max_bags": 20,  # Estimate based on standard parcel
        "cubic_capacity": 251.20,
        "is_electric": False,
        "aliases": ["4wd p31", "p31", "4wd p31 delivery truck"],
    },
    "Electric Step Van - XL": {
        "max_bags": 56,
        "cubic_capacity": 625.51,
        "is_electric": True,
        "aliases": ["step van", "electric step van", "electric step van - xl", "step van xl"],
    },
    "Electric Cargo Van - M": {
        "max_bags": 20,  # Estimate, not in table
        "cubic_capacity": 251.20,
        "is_electric": True,
        "aliases": ["electric cargo van - m", "electric cargo van m", "cargo van m"],
    },
    "Electric Cargo Van - L": {
        "max_bags": 22,  # Estimate, not in table
        "cubic_capacity": 286.96,
        "is_electric": True,
        "aliases": ["electric cargo van - l", "electric cargo van l", "cargo van l"],
    },
    "Rivian SMALL": {
        "max_bags": 27,
        "cubic_capacity": 280.47,
        "is_electric": True,
        "aliases": ["rivian small", "rivian s", "rivian mini"],
    },
    "Rivian MEDIUM": {
        "max_bags": 36,
        "cubic_capacity": 370.07,
        "is_electric": True,
        "aliases": ["rivian medium", "rivian m", "rivian med"],
    },
    "Rivian LARGE": {
        "max_bags": 48,  # Estimate, not in table - assuming similar to CDV16
        "cubic_capacity": 579.89,
        "is_electric": True,
        "aliases": ["rivian large", "rivian l"],
    },
}


def get_van_capacity(service_type: str) -> dict:
    """
    Get capacity limits for a van service type.
    
    Args:
        service_type: The service type (e.g., "Standard Parcel - Custom Delivery Van 14ft")
    
    Returns:
        Dictionary with 'max_bags', 'cubic_capacity', and 'is_electric', or None if not found
    """
    if service_type in VAN_CAPACITIES:
        return VAN_CAPACITIES[service_type].copy()
    
    # Try to find by alias
    service_lower = service_type.lower().strip()
    for van_type, capacity_data in VAN_CAPACITIES.items():
        if service_lower in [a.lower() for a in capacity_data["aliases"]]:
            return capacity_data.copy()
    
    return None


def is_van_electric(service_type: str) -> bool:
    """Check if a van service type is electric."""
    capacity = get_van_capacity(service_type)
    return capacity.get("is_electric", False) if capacity else False


def is_route_electric(service_type: str) -> bool:
    """
    Check if a route service type is designated as electric.
    Electric route types contain 'electric' in the name or are electric vans.
    """
    service_lower = service_type.lower()
    return "electric" in service_lower or "rivian" in service_lower


def get_all_van_capacities() -> dict:
    """Get all van capacities, excluding aliases."""
    return {
        k: {"max_bags": v["max_bags"], "cubic_capacity": v["cubic_capacity"]}
        for k, v in VAN_CAPACITIES.items()
    }


def get_capacity_percentage(service_type: str, current_bags: int, current_cubic: float) -> dict:
    """
    Calculate how full a van is based on current load.
    
    Args:
        service_type: Service type of the van
        current_bags: Current number of bags in van
        current_cubic: Current cubic footage usage
    
    Returns:
        Dictionary with bag_percentage and cubic_percentage, or None if van type not found
    """
    capacity = get_van_capacity(service_type)
    if not capacity:
        return None
    
    return {
        "bag_percentage": (current_bags / capacity["max_bags"] * 100) if capacity["max_bags"] > 0 else 0,
        "cubic_percentage": (current_cubic / capacity["cubic_capacity"] * 100) if capacity["cubic_capacity"] > 0 else 0,
        "max_bags": capacity["max_bags"],
        "max_cubic": capacity["cubic_capacity"],
        "bags_remaining": max(0, capacity["max_bags"] - current_bags),
        "cubic_remaining": max(0, capacity["cubic_capacity"] - current_cubic),
    }


def is_van_at_capacity_threshold(service_type: str, current_bags: int, current_cubic: float, threshold_percent: float = 85.0) -> bool:
    """
    Check if a van is at or above the capacity threshold.
    
    Args:
        service_type: Service type of the van
        current_bags: Current number of bags in van
        current_cubic: Current cubic footage usage
        threshold_percent: Threshold percentage (default 85%)
    
    Returns:
        True if van is at or above threshold
    """
    capacity_info = get_capacity_percentage(service_type, current_bags, current_cubic)
    if not capacity_info:
        return False
    
    # Van is at threshold if EITHER bags or cubic space is at threshold
    return (capacity_info["bag_percentage"] >= threshold_percent or 
            capacity_info["cubic_percentage"] >= threshold_percent)


def is_van_over_capacity(service_type: str, current_bags: int, current_cubic: float) -> bool:
    """
    Check if a van exceeds capacity limits.
    
    Args:
        service_type: Service type of the van
        current_bags: Current number of bags in van
        current_cubic: Current cubic footage usage
    
    Returns:
        True if van exceeds limits
    """
    capacity = get_van_capacity(service_type)
    if not capacity:
        return False
    
    # Van is over capacity if EITHER bags or cubic space exceeds limits
    return (current_bags > capacity["max_bags"] or 
            current_cubic > capacity["cubic_capacity"])
