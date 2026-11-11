"""Input rules for targets, aliases, and idempotency keys."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from shortener.codes import RESERVED_ALIASES
from shortener.errors import AppError

_ALIAS = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ALLOWED_SCHEMES = {"http", "https"}


def validate_target_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise AppError(422, "invalid_url", "URL is required")
    if len(url) > 2048:
        raise AppError(422, "url_too_long", "URL must be 2048 characters or fewer")
    if any(char in url for char in ("\n", "\r", "\t", "\\")):
        raise AppError(422, "invalid_url", "URL contains a forbidden character")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise AppError(422, "unsafe_url", "Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise AppError(422, "unsafe_url", "URLs with embedded credentials are not allowed")
    if not parts.hostname:
        raise AppError(422, "invalid_url", "URL must include a host")
    return url


def validate_alias(alias: str) -> str:
    value = alias.strip()
    if not _ALIAS.fullmatch(value):
        raise AppError(
            422,
            "invalid_alias",
            "Alias must be 3-32 characters and use only letters, digits, '_' or '-'",
        )
    if value.lower() in RESERVED_ALIASES:
        raise AppError(400, "alias_reserved", f"Alias '{value}' is reserved")
    return value


def validate_idempotency_key(key: str) -> str:
    value = key.strip()
    if not _IDEMPOTENCY.fullmatch(value):
        raise AppError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-200 characters of letters, digits, '.', '_', ':', or '-'",
        )
    return value
