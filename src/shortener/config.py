"""Runtime settings. Environment variables override the local defaults."""

from __future__ import annotations

import os

from pydantic import BaseModel

from shortener.paths import repo_root


def sqlite_url(path) -> str:
    return "sqlite:///" + str(path)


class Settings(BaseModel):
    database_url: str
    orchestrator_database_url: str
    rate_limit_creates: int = 60
    rate_limit_window_seconds: int = 60
    base_url: str = "http://127.0.0.1:8741"
    code_length: int = 7
    log_level: str = "INFO"
    click_retention_days: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        data = repo_root() / "data"
        return cls(
            database_url=os.environ.get("SHORTENER_DATABASE_URL", sqlite_url(data / "shortener.db")),
            orchestrator_database_url=os.environ.get(
                "ORCHESTRATOR_DATABASE_URL", sqlite_url(data / "orchestrator.db")
            ),
            rate_limit_creates=int(os.environ.get("RATE_LIMIT_CREATES", "60")),
            rate_limit_window_seconds=int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")),
            base_url=os.environ.get("BASE_URL", "http://127.0.0.1:8741"),
            code_length=int(os.environ.get("CODE_LENGTH", "7")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            click_retention_days=int(os.environ.get("CLICK_RETENTION_DAYS", "30")),
        )
