"""Click-log compliance: strip secrets, classify the data, and enforce retention."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from shortener.errors import AppError
from shortener.models import Click
from shortener.service import as_utc

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_UNSAFE_SCHEMES = {"javascript", "data", "file", "vbscript"}

DATA_CLASSIFICATION = {
    "short_links.target_url": "customer_content",
    "clicks.clicked_at": "operational",
    "clicks.referrer": "confidential_telemetry",
    "note": (
        "Referrers are telemetry that may carry query-string tokens or an email address. "
        "The service stores a scheme/host/path only, and replaces an email-bearing value with a hash. "
        "Clicks older than the configured retention window are eligible for purge."
    ),
}


def sanitize_referrer(value: str | None) -> str | None:
    """Drop query strings, fragments, unsafe schemes, and raw email addresses."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > 1024:
        text = text[:1024]
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme in _UNSAFE_SCHEMES:
        return None
    if scheme in {"http", "https"} and parts.netloc:
        cleaned = urlunsplit((scheme, parts.netloc, parts.path or "", "", ""))
    else:
        cleaned = text.split("?", 1)[0].split("#", 1)[0]
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if _EMAIL.search(cleaned):
        digest = hashlib.sha256(cleaned.encode()).hexdigest()[:16]
        cleaned = f"hashed:{digest}"
    return cleaned[:512]


def purge_clicks(session: Session, *, now: datetime, retention_days: int) -> int:
    """Delete click rows older than the retention window. Returns the number removed."""
    if retention_days < 1:
        raise AppError(422, "invalid_retention", "Retention window must be at least 1 day")
    cutoff = as_utc(now) - timedelta(days=retention_days)
    removed = 0
    for row in session.scalars(select(Click)).all():
        clicked = row.clicked_at
        if clicked is None:
            continue
        if as_utc(clicked) < cutoff:
            session.delete(row)
            removed += 1
    session.commit()
    return removed
