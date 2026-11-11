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
