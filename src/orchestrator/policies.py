"""Guardrails applied to workflow artifacts.

Blocking findings stop a stage. Informational notes stay in the security report
so a reviewer can see accepted risks.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    (
        "assigned_secret",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
    ),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
)

UNSAFE_URL = re.compile(r"(?i)\b(?:javascript|data|vbscript|file):[^\s'\"<>]+")
_BLOCKED_SCHEMES = {"javascript", "data", "vbscript", "file", "ftp"}


def scan_artifacts(artifacts: dict[str, Any]) -> list[dict]:
    findings: list[dict] = []
    for name, content in artifacts.items():
        blob = content if isinstance(content, str) else _stringify(content)
        for pattern_id, pattern in SECRET_PATTERNS:
            if pattern.search(blob):
                findings.append(
                    {
                        "severity": "block",
                        "policy": "secrets_in_artifacts",
                        "artifact": name,
                        "pattern": pattern_id,
                        "message": f"Secret-like pattern '{pattern_id}' found in artifact '{name}'",
                    }
                )
        for match in UNSAFE_URL.finditer(blob):
            token = match.group(0)
            if _unsafe_token(token):
                findings.append(
                    {
                        "severity": "block",
                        "policy": "unsafe_urls",
                        "artifact": name,
                        "pattern": token[:80],
                        "message": f"Unsafe URL '{token[:80]}' found in artifact '{name}'",
                    }
                )
    return findings


def _stringify(content: Any) -> str:
    import json

    return json.dumps(content, sort_keys=True, default=str)


def _unsafe_token(token: str) -> bool:
    parts = urlsplit(token)
    scheme = parts.scheme.lower()
    return scheme in _BLOCKED_SCHEMES


def evaluate_compliance(root) -> dict:
    """Read the service tree and require retention, purge, and referrer sanitizing.

    `root` is a pathlib.Path of a checkout or a run workspace.
    """
    from pathlib import Path

    root = Path(root)
    texts = {
        "compliance": _read(root / "src/shortener/compliance.py"),
        "service": _read(root / "src/shortener/service.py"),
        "api": _read(root / "src/shortener/api.py"),
        "config": _read(root / "src/shortener/config.py"),
    }
    checks = [
        ("referrer_sanitized", "def sanitize_referrer" in texts["compliance"], "sanitize_referrer is missing"),
        ("referrer_used_on_write", "sanitize_referrer" in texts["service"], "record_click does not sanitize the referrer"),
        ("retention_purge", "def purge_clicks" in texts["compliance"], "purge_clicks is missing"),
        ("purge_endpoint", "/api/v1/compliance/purge" in texts["api"], "POST /api/v1/compliance/purge is missing"),
        ("retention_window", "click_retention_days" in texts["config"], "click_retention_days is not configurable"),
    ]
    findings = []
    for policy, ok, message in checks:
        if not ok:
            findings.append(
                {
                    "severity": "block",
                    "policy": policy,
                    "artifact": "source",
                    "message": message,
                }
            )
    classification = {
        "short_links.target_url": "customer_content",
        "clicks.clicked_at": "operational",
        "clicks.referrer": "confidential_telemetry",
        "retention": "Clicks are purged through POST /api/v1/compliance/purge using click_retention_days.",
        "referrer_rule": "Query strings and fragments are stripped. Email-bearing referrers are stored as a hash.",
    }
    return {
        "ok": not findings,
        "findings": findings,
        "classification": classification,
    }


def _read(path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
