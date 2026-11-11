"""Uvicorn entrypoint.

Run from the repository root:

    uvicorn shortener.main:app --host 0.0.0.0 --port 8741
"""

from __future__ import annotations

from shortener.app import create_app
from shortener.config import Settings

app = create_app(Settings.from_env())
