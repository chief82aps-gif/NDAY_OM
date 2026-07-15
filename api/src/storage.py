"""
S3-backed object storage for evidence photos (crash reports, and later
whatever else in uploads.py wants durable storage instead of local disk).

Local disk doesn't survive a Render redeploy — crash_report.py's old
UPLOAD_DIR carried an explicit warning about this. This module is the
durable replacement: a thin key/value wrapper around S3 so callers never
touch boto3 directly.

Photos here are potentially PII (driver's license, insurance card) — URLs
handed back to the frontend are always short-lived presigned GETs, never
permanent public links, and the bucket itself should be private.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

DEFAULT_PRESIGN_EXPIRES = 7 * 24 * 3600  # 7 days


@lru_cache(maxsize=1)
def _client():
    if not AWS_S3_BUCKET:
        return None
    import boto3
    return boto3.client("s3", region_name=AWS_REGION)


def is_configured() -> bool:
    """False until AWS_S3_BUCKET (and credentials) are set on Render —
    callers should surface a clear error rather than silently no-op."""
    return _client() is not None


def build_key(*parts: str) -> str:
    """Join path-like parts into an S3 key, e.g.
    build_key('crash_reports', 'CR-20260715-0001', 'vehicle_damage_1.jpg')."""
    return "/".join(p.strip("/") for p in parts if p)


def upload_bytes(data: bytes, key: str, content_type: Optional[str] = None) -> str:
    """Puts an object and returns its key — callers store the key (not a
    URL) and mint presigned URLs on demand via presigned_url()."""
    client = _client()
    if not client:
        raise RuntimeError("AWS_S3_BUCKET is not configured — cannot upload evidence photos.")
    extra = {"ContentType": content_type} if content_type else {}
    client.put_object(Bucket=AWS_S3_BUCKET, Key=key, Body=data, **extra)
    return key


def presigned_url(key: str, expires_in: int = DEFAULT_PRESIGN_EXPIRES) -> str:
    client = _client()
    if not client:
        raise RuntimeError("AWS_S3_BUCKET is not configured.")
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": AWS_S3_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def download_bytes(key: str) -> bytes:
    """Pulls an object's bytes back down — used by crash_report_pdf.py to
    embed evidence photos into the generated PDF."""
    client = _client()
    if not client:
        raise RuntimeError("AWS_S3_BUCKET is not configured.")
    obj = client.get_object(Bucket=AWS_S3_BUCKET, Key=key)
    return obj["Body"].read()
