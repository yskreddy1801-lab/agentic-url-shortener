"""FastAPI application factory. Tests build an app with isolated databases."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shortener import __version__
from shortener.api import api_router, redirect_router, system_router
from shortener.clock import SystemClock
from shortener.config import Settings
from shortener.db import Base, build_session_factory
from shortener.errors import AppError
from shortener.logging_config import configure_logging
from shortener.models import Click, ShortLink  # noqa: F401  (register models)
from shortener.rate_limit import SlidingWindowLimiter

logger = logging.getLogger("shortener.access")


def _error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _ensure_parent(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    path = url.removeprefix("sqlite:///")
    if not path or path == ":memory:" or path.startswith(":memory:"):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    _ensure_parent(settings.database_url)
    _ensure_parent(settings.orchestrator_database_url)
    db_engine, sessions = build_session_factory(settings.database_url)

    from orchestrator.api import router as orchestrator_router
    from orchestrator.engine import Engine
    from orchestrator.graph import default_sdlc_graph
    from orchestrator.handlers import build_handlers
    from orchestrator.store import Store
    from shortener.paths import repo_root

    store = Store(settings.orchestrator_database_url)
    engine = Engine(store, default_sdlc_graph(), build_handlers(), repo_root())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(db_engine)
        store.create_all()
        yield

    app = FastAPI(
        title="Agentic URL Shortener",
        version=__version__,
        summary="Short links, click analytics, and an auditable SDLC orchestrator.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db_engine = db_engine
    app.state.sessions = sessions
    app.state.limiter = SlidingWindowLimiter(settings.rate_limit_creates, settings.rate_limit_window_seconds)
    app.state.clock = SystemClock()
    app.state.engine = engine
    app.state.logger = logging.getLogger("shortener")

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={"fields": {"request_id": request_id, "method": request.method, "path": request.url.path}},
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            },
        )
        return response

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "validation_error",
                "Request validation failed",
                {"errors": jsonable_encoder(exc.errors())},
            ),
        )

    from orchestrator.errors import OrchestratorError

    @app.exception_handler(OrchestratorError)
    async def orchestrator_error(_request: Request, exc: OrchestratorError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("orchestrator_error", str(exc)),
        )

    app.include_router(system_router)
    app.include_router(api_router)
    app.include_router(orchestrator_router)
    app.include_router(redirect_router)
    return app
