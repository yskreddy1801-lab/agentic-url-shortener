"""App fixtures with isolated SQLite files."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/short.db",
        orchestrator_database_url=f"sqlite:///{tmp_path}/orch.db",
        rate_limit_creates=1000,
        rate_limit_window_seconds=60,
        base_url="http://test",
        code_length=7,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
