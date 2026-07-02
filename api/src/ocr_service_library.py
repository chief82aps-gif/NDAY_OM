"""
Service Type Library and OCR Image Processing.

This module provides:
1. Service type classification and matching
2. Image-to-text OCR conversion
3. Training/Excluded service detection
4. Cortex package calculation with deductions
"""

import re
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass


# ============================================================================
# SERVICE TYPE LIBRARY
# ============================================================================

SERVICE_TYPE_LIBRARY = {
    "nursery_route_level_1": {
        "display": "Nursery Route Level 1",
        "variations": [
            r"nursery\s+route\s+level\s+1(?:\s*-)?",
            r"nursery\s+1\s*(?:-|–)",
        ],
        "category": "nursery",
        "code_pattern": r"NUR[0-9]+"
    },
    "nursery_route_level_2": {
        "display": "Nursery Route Level 2",
        "variations": [
            r"nursery\s+route\s+level\s+2(?:\s*-)?",
            r"nursery\s+2\s*(?:-|–)",
        ],
        "category": "nursery",
        "code_pattern": r"NUR[0-9]+"
    },
    "nursery_route_level_3": {
        "display": "Nursery Route Level 3",
        "variations": [
            r"nursery\s+route\s+level\s+3(?:\s*-)?",
            r"nursery\s+3\s*(?:-|–)",
        ],
        "category": "nursery",
        "code_pattern": r"NUR[0-9]+"
    },
    "standard_parcel_custom_14": {
        "display": "Standard Parcel - Custom Delivery Van 14ft",
        "variations": [
            r"standard\s+parcel\s*-\s*custom\s+delivery\s+van\s+14ft",
            r"parcel\s+custom\s+14ft",
        ],
        "category": "standard_parcel",
        "code_pattern": r"SP[0-9]+"
    },
    "standard_parcel_custom_16": {
        "display": "Standard Parcel - Custom Delivery Van 16ft",
        "variations": [
            r"standard\s+parcel\s*-\s*custom\s+delivery\s+van\s+16ft",
            r"parcel\s+custom\s+16ft",
        ],
        "category": "standard_parcel",
        "code_pattern": r"SP[0-9]+"
    },
    "standard_parcel_electric_rivian": {
        "display": "Standard Parcel Electric - Rivian MEDIUM",
        "variations": [
            r"standard\s+parcel\s+electric\s*-\s*rivian",
            r"parcel\s+electric\s+rivian",
        ],
        "category": "electric",
        "code_pattern": r"EV[0-9]+"
    },
    "standard_parcel_on_road": {
        "display": "Standard Parcel - On-Road Experience Driver",
        "variations": [
            r"standard\s+parcel\s*-\s*on[\s-]*road\s+experience",
            r"on[\s-]*road\s+experience",
        ],
        "category": "standard_parcel",
        "code_pattern": r"SP[0-9]+"
    },
    "standard_parcel_extra_large": {
        "display": "Standard Parcel - Extra Large Van - US",
        "variations": [
            r"standard\s+parcel\s*-\s*extra\s+large\s+van",
            r"parcel\s+extra\s+large",
        ],
        "category": "standard_parcel",
        "code_pattern": r"SP[0-9]+"
    },
    "nursery_electric_vehicle": {
        "display": "Nursery Route - Electric Vehicle",
        "variations": [
            r"nursery\s+route\s*-\s*electric\s+vehicle",
            r"nursery\s+electric",
        ],
        "category": "electric",
        "code_pattern": r"EV[0-9]+"
    },
    "4wd_p51_delivery": {
        "display": "4WD P51 Delivery Truck",
        "variations": [
            r"4wd\s+p51",
            r"p51\s+delivery",
        ],
        "category": "truck",
        "code_pattern": r"4WD[0-9]+"
    }
}


def match_service_type(service_name: str) -> Optional[str]:
    """
    Match a service name against the service type library.
    
    Args:
        service_name: Service name from OCR
    
    Returns:
        Service type key if matched, None otherwise
    """
    service_lower = service_name.lower()
    
    for service_key, service_config in SERVICE_TYPE_LIBRARY.items():
        for variation_pattern in service_config['variations']:
            if re.search(variation_pattern, service_lower, re.IGNORECASE):
                return service_key
    
    return None


def get_service_display_name(service_key: str) -> str:
    """Get the canonical display name for a service type."""
    if service_key in SERVICE_TYPE_LIBRARY:
        return SERVICE_TYPE_LIBRARY[service_key]['display']
    return service_key


# ============================================================================
# TRAINING & EXCLUDED SERVICE DETECTION
# ============================================================================

TRAINING_KEYWORDS = [
    r'\btraining\b',
    r'\btrainee\b',
    r'\btraining\s+route\b',
    r'\bon[\s-]*the[\s-]*job\b',
]

EXCLUDED_SERVICE_KEYWORDS = [
    r'\bexcluded\b',
    r'\bnot\s+delivered\b',
    r'\bexclude\b',
    r'\bskipped\b',
]


def is_training_service(service_name: str) -> bool:
    """Check if a service name indicates training data."""
    service_lower = service_name.lower()
    return any(re.search(pattern, service_lower) for pattern in TRAINING_KEYWORDS)


def is_excluded_service(service_name: str) -> bool:
    """Check if a service name is marked as excluded."""
    service_lower = service_name.lower()
    return any(re.search(pattern, service_lower) for pattern in EXCLUDED_SERVICE_KEYWORDS)


# ============================================================================
# CORTEX PACKAGE CALCULATION
# ============================================================================

@dataclass
class CortexPackageBreakdown:
    """Represents completed vs attempted deliveries and deductions."""
    total_fraction_text: str
    completed_deliveries: int
    attempted_deliveries: int
    deductions: Dict[str, int]
    final_delivered_packages: int


def extract_cortex_packages(raw_text: str) -> CortexPackageBreakdown:
    """
    Calculate Cortex delivered packages using the formula:
    (sum of numerators in X/Y deliveries fractions) - (deductions)
    
    Deductions:
    - Remaining
    - Reattemptable
    - Undeliverable
    - Missing
    - Returned to Station
    - Pick up failed
    
    Args:
        raw_text: Raw Cortex OCR text
    
    Returns:
        CortexPackageBreakdown with calculation details
    """
    # Extract all "X/Y deliveries" fractions
    delivery_pattern = r'(\d+)\s*\/\s*(\d+)\s*deliveries?'
    matches = list(re.finditer(delivery_pattern, raw_text, re.IGNORECASE))
    
    completed_sum = sum(int(m.group(1)) for m in matches)
    attempted_sum = sum(int(m.group(2)) for m in matches)
    
    # Extract deductions
    deduction_patterns = {
        'remaining': r'(\d+)\s*remaining\b',
        'reattemptable': r'(\d+)\s*reattemptable\b',
        'undeliverable': r'(\d+)\s*undeliverable\b',
        'missing': r'(\d+)\s*missing\b',
        'returned_to_station': r'(\d+)\s*returned\s+to\s+station\b',
        'pickup_failed': r'(\d+)\s*pickup\s+failed\b',
    }
    
    deductions = {}
    total_deductions = 0
    
    for deduction_type, pattern in deduction_patterns.items():
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            deductions[deduction_type] = value
            total_deductions += value
    
    final_delivered = max(0, completed_sum - total_deductions)
    
    return CortexPackageBreakdown(
        total_fraction_text=f"{completed_sum}/{attempted_sum}",
        completed_deliveries=completed_sum,
        attempted_deliveries=attempted_sum,
        deductions=deductions,
        final_delivered_packages=final_delivered
    )


# ============================================================================
# OCR IMAGE TO TEXT (Placeholder for Tesseract/CloudVision)
# ============================================================================

# ============================================================================
# OCR IMAGE TO TEXT (Google Cloud Vision API)
# ============================================================================

import os
from typing import Union
from io import BytesIO

def ocr_image_to_text(image_source: Union[str, bytes]) -> str:
    """
    Convert an image to text using Google Cloud Vision API.
    
    Args:
        image_source: Either a file path (str) or image bytes
    
    Returns:
        Extracted text from image
    
    Raises:
        ImportError: If google-cloud-vision is not installed
        ValueError: If image cannot be processed
    """
    try:
        from google.cloud import vision
        from google.cloud.vision_v1 import types
        from google.oauth2 import service_account
    except ImportError:
        raise ImportError(
            "google-cloud-vision is required. Install with: "
            "pip install google-cloud-vision"
        )
    
    try:
        # Try to load credentials from config file
        import os
        creds_path = os.path.join(os.path.dirname(__file__), '../../config/nday-service-account-key.json')
        
        client = None
        if os.path.exists(creds_path):
            # Load credentials explicitly from file
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            client = vision.ImageAnnotatorClient(credentials=credentials)
        else:
            # Fall back to environment variable or default credentials
            client = vision.ImageAnnotatorClient()
        
        # Load image
        if isinstance(image_source, str):
            # File path
            with open(image_source, 'rb') as f:
                image_content = f.read()
        elif isinstance(image_source, bytes):
            # Bytes already
            image_content = image_source
        else:
            raise ValueError("image_source must be str (file path) or bytes")
        
        # Create image object
        image = types.Image(content=image_content)
        
        # Perform OCR
        response = client.document_text_detection(image=image)
        
        # Extract full text
        if response.full_text_annotation:
            return response.full_text_annotation.text
        else:
            raise ValueError("No text detected in image")
    
    except Exception as e:
        raise ValueError(f"OCR processing failed: {str(e)}")


def ocr_image_to_text_with_confidence(image_source: Union[str, bytes]) -> Dict[str, any]:
    """
    Convert image to text with confidence scores and detailed blocks.
    
    Returns:
        {
            'full_text': str,
            'blocks': List of text blocks with confidence,
            'confidence_average': float (0-1),
            'detected_language': str
        }
    """
    try:
        from google.cloud import vision
        from google.cloud.vision_v1 import types
        from google.oauth2 import service_account
    except ImportError:
        raise ImportError(
            "google-cloud-vision is required. Install with: "
            "pip install google-cloud-vision"
        )
    
    try:
        # Try to load credentials from config file
        import os
        creds_path = os.path.join(os.path.dirname(__file__), '../../config/nday-service-account-key.json')
        
        client = None
        if os.path.exists(creds_path):
            # Load credentials explicitly from file
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            client = vision.ImageAnnotatorClient(credentials=credentials)
        else:
            # Fall back to environment variable or default credentials
            client = vision.ImageAnnotatorClient()
        
        # Load image
        if isinstance(image_source, str):
            with open(image_source, 'rb') as f:
                image_content = f.read()
        else:
            image_content = image_source
        
        image = types.Image(content=image_content)
        response = client.document_text_detection(image=image)
        
        # Extract blocks with confidence
        blocks = []
        total_confidence = 0
        block_count = 0
        
        if response.full_text_annotation:
            full_text = response.full_text_annotation.text
            
            # Extract paragraphs with confidence
            for page in response.full_text_annotation.pages:
                for block in page.blocks:
                    block_text = ''.join(
                        ''.join(
                            ''.join(
                                symbol.text
                                for symbol in word.symbols
                            )
                            for word in paragraph.words
                        )
                        for paragraph in block.paragraphs
                    )
                    
                    # Get average confidence for block
                    confidences = []
                    for paragraph in block.paragraphs:
                        for word in paragraph.words:
                            for symbol in word.symbols:
                                if symbol.confidence > 0:
                                    confidences.append(symbol.confidence)
                    
                    block_confidence = sum(confidences) / len(confidences) if confidences else 0
                    
                    blocks.append({
                        'text': block_text.strip(),
                        'confidence': block_confidence
                    })
                    
                    total_confidence += block_confidence
                    block_count += 1
            
            avg_confidence = total_confidence / block_count if block_count > 0 else 0
            
            return {
                'full_text': full_text,
                'blocks': blocks,
                'confidence_average': avg_confidence,
                'block_count': block_count,
                'detected_language': 'en'  # Vision API doesn't provide language detection in document_text_detection
            }
        else:
            raise ValueError("No text detected in image")
    
    except Exception as e:
        raise ValueError(f"OCR processing with confidence failed: {str(e)}")


if __name__ == '__main__':
    # Test service type matching
    print("=" * 70)
    print("SERVICE TYPE LIBRARY TESTS")
    print("=" * 70)
    
    test_services = [
        "Nursery Route Level 2 - 10 hr",
        "Standard Parcel Electric - Rivian MEDIUM - 10 hr",
        "Nursery Electric Vehicle - 10 hr",
        "Training Route Level 1",
        "Excluded Service - Do Not Count",
    ]
    
    for service in test_services:
        matched = match_service_type(service)
        is_training = is_training_service(service)
        is_excluded = is_excluded_service(service)
        
        print(f"\nService: {service}")
        print(f"  Matched Type: {get_service_display_name(matched) if matched else 'NO MATCH'}")
        print(f"  Is Training: {is_training}")
        print(f"  Is Excluded: {is_excluded}")
    
    # Test Cortex package calculation
    print("\n" + "=" * 70)
    print("CORTEX PACKAGE CALCULATION TEST")
    print("=" * 70)
    
    cortex_sample = """
    Route CX001 - 15 deliveries completed / 25 deliveries total, 10 stops
    Route CX002 - 22 deliveries completed / 30 deliveries total, 8 stops
    
    Summary:
    32 Remaining
    5 Reattemptable
    2 Undeliverable
    1 Missing
    3 Returned to Station
    1 Pickup Failed
    """
    
    breakdown = extract_cortex_packages(cortex_sample)
    print(f"\nCompleted Deliveries: {breakdown.completed_deliveries}")
    print(f"Attempted Deliveries: {breakdown.attempted_deliveries}")
    print(f"Deductions:")
    for deduction_type, value in breakdown.deductions.items():
        print(f"  - {deduction_type}: {value}")
    print(f"Final Delivered Packages: {breakdown.final_delivered_packages}")
