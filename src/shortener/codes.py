"""Non-sequential public codes and reserved aliases.

Codes are drawn from a cryptographic generator. They are not a counter, so
neighbors cannot be guessed by adding one.
"""

from __future__ import annotations

import secrets

# Unambiguous alphabet: no 0/O, 1/I/l.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

RESERVED_ALIASES = frozenset(
    {
        "api",
        "admin",
        "docs",
        "favicon.ico",
        "health",
        "metrics",
        "openapi",
        "ready",
        "redoc",
        "static",
        "v1",
    }
)


def generate_code(length: int = 7, rng=secrets.choice) -> str:
    if length < 4:
        raise ValueError("code length must be at least 4")
    return "".join(rng(ALPHABET) for _ in range(length))
