"""Unit checks for URL rules and the code generator."""

from __future__ import annotations

import pytest

from shortener.codes import ALPHABET, RESERVED_ALIASES, generate_code
from shortener.errors import AppError
from shortener.validation import validate_alias, validate_target_url


def test_generated_codes_are_unique_and_not_a_counter():
    codes = [generate_code(7) for _ in range(40)]
    assert len(set(codes)) == 40
    assert all(len(code) == 7 for code in codes)
    assert all(char in ALPHABET for code in codes for char in code)
    assert all(code.lower() not in RESERVED_ALIASES for code in codes)
    assert codes != [str(index) for index in range(40)]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "ftp://example.com/file",
        "http://user:pass@example.com/secret",
        "https://user@example.com",
        "not a url",
        "//example.com",
    ],
)
def test_rejects_unsafe_or_malformed_urls(url):
    with pytest.raises(AppError) as caught:
        validate_target_url(url)
    assert caught.value.code in {"unsafe_url", "invalid_url"}


def test_accepts_http_and_https():
    assert validate_target_url("  https://example.com/a  ") == "https://example.com/a"
    assert validate_target_url("http://localhost:8741/health") == "http://localhost:8741/health"


@pytest.mark.parametrize("alias", ["api", "Health", "docs", "a", "has space", "bad.alias"])
def test_rejects_bad_aliases(alias):
    with pytest.raises(AppError):
        validate_alias(alias)


def test_accepts_alias():
    assert validate_alias("Launch_2026") == "Launch_2026"
