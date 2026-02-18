"""Service type normalization and mapping per governance rules."""

# Service type alias mapping from Ingest Matrix
SERVICE_TYPE_MAP = {
    # Standard Parcel Electric - Rivian MEDIUM
    "Standard Parcel Electric - Rivian MEDIUM": "Standard Parcel Electric - Rivian MEDIUM",
    "Rivian MEDIUM": "Standard Parcel Electric - Rivian MEDIUM",
    
    # Standard Parcel - Extra Large Van - US
    "Standard Parcel - Extra Large Van - US": "Standard Parcel - Extra Large Van - US",
    "Extra Large Van": "Standard Parcel - Extra Large Van - US",
    "XL": "Standard Parcel - Extra Large Van - US",
    "Nursery Route Level 1": "Standard Parcel - Extra Large Van - US",
    "Nursery Route Level 2": "Standard Parcel - Extra Large Van - US",
    "Nursery Route Level 3": "Standard Parcel - Extra Large Van - US",
    "Standard Parcel - On-Road Experience: Driver": "Standard Parcel - Extra Large Van - US",
    
    # Standard Parcel - Custom Delivery Van 14ft
    "Standard Parcel - Custom Delivery Van 14ft": "Standard Parcel - Custom Delivery Van 14ft",
    "CDV 14ft": "Standard Parcel - Custom Delivery Van 14ft",
    "CDV14": "Standard Parcel - Custom Delivery Van 14ft",
    
    # 4WD P31 Delivery Truck
    "4WD P31 Delivery Truck": "4WD P31 Delivery Truck",
    "AmFlex Large Vehicle": "4WD P31 Delivery Truck",
    "P31": "4WD P31 Delivery Truck",
    
    # Standard Parcel - Custom Delivery Van 16ft
    "Standard Parcel - Custom Delivery Van 16ft": "Standard Parcel - Custom Delivery Van 16ft",
    "CDV 16ft": "Standard Parcel - Custom Delivery Van 16ft",
    "CDV16": "Standard Parcel - Custom Delivery Van 16ft",
    
    # Nursery Electric routes
    "Nursery Route Level 1 - Electric Vehicle": "Standard Parcel Electric - Rivian MEDIUM",
    "Nursery Route Level 2 - Electric Vehicle": "Standard Parcel Electric - Rivian MEDIUM",
    "Nursery Route Level 3 - Electric Vehicle": "Standard Parcel Electric - Rivian MEDIUM",
}


def normalize_service_type(service_type: str) -> str:
    """Normalize service type to canonical form."""
    if not service_type or not isinstance(service_type, str):
        return None
    
    service_type_clean = service_type.strip()
    return SERVICE_TYPE_MAP.get(service_type_clean, service_type_clean)


def is_electric_service_type(service_type: str) -> bool:
    """Check if service type is electric."""
    normalized = normalize_service_type(service_type)
    return normalized and "Electric" in normalized


def normalize_route_code(route_code: str) -> str:
    """Normalize route code: uppercase, strip whitespace, remove leading apostrophes."""
    if not route_code or not isinstance(route_code, str):
        return None
    
    code = route_code.strip().upper()
    code = code.lstrip("'")
    code = code.replace(" ", "")  # Remove internal spaces
    return code


def validate_route_code(route_code: str) -> bool:
    """Validate route code: must be 4-5 characters after normalization."""
    normalized = normalize_route_code(route_code)
    if not normalized:
        return False
    return 4 <= len(normalized) <= 5
