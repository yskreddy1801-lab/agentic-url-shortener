"""HTTP routes for health, link management, and redirects."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from shortener import service
from shortener.compliance import DATA_CLASSIFICATION, purge_clicks
from shortener.errors import AppError
from shortener.schemas import CreateLinkIn, CreateLinkOut, LinkOut, StatsOut

system_router = APIRouter()
api_router = APIRouter()
redirect_router = APIRouter()


def _now(request: Request):
    return request.app.state.clock.now()


def _session(request: Request):
    return request.app.state.sessions()


def _present(link, request: Request, now, replayed: bool = False) -> dict:
    status = service.link_status(link, now)
    base = request.app.state.settings.base_url.rstrip("/")
    body = {
        "code": link.code,
        "target_url": link.target_url,
        "short_url": f"{base}/{link.code}",
        "created_at": service.as_utc(link.created_at),
        "expires_at": service.as_utc(link.expires_at) if link.expires_at else None,
        "disabled": link.disabled_at is not None,
        "status": status,
        "custom_alias": bool(link.is_custom),
    }
    if replayed:
        body["replayed"] = True
    return body


def _rate_headers(decision) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }


@system_router.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "url-shortener",
        "health": "/health",
        "ready": "/ready",
        "docs": "/docs",
        "create": "POST /api/v1/urls",
    }


@system_router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "url-shortener"}


@system_router.get("/ready")
def ready(request: Request):
    try:
        with _session(request) as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "not_ready", "message": "Database is unavailable", "details": {}}},
        )
    return {"status": "ready"}


@api_router.post("/api/v1/urls", response_model=CreateLinkOut)
def create_url(payload: CreateLinkIn, request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    decision = request.app.state.limiter.check(client)
    response.headers.update(_rate_headers(decision))
    if not decision.allowed:
        raise AppError(
            429,
            "rate_limited",
            "Too many create requests",
            details={"limit": decision.limit, "window_seconds": request.app.state.limiter.window_seconds},
            headers={
                **_rate_headers(decision),
                "Retry-After": str(decision.retry_after_seconds),
            },
        )
    idempotency_key = request.headers.get("Idempotency-Key")
    now = _now(request)
    with _session(request) as session:
        link, replayed = service.create_link(
            session,
            target_url=payload.url,
            alias=payload.alias,
            ttl_seconds=payload.ttl_seconds,
            expires_at=payload.expires_at,
            idempotency_key=idempotency_key,
            now=now,
            code_length=request.app.state.settings.code_length,
        )
        body = _present(link, request, now)
    body["replayed"] = replayed
    response.status_code = 200 if replayed else 201
    return body


@api_router.get("/api/v1/urls/{code}", response_model=LinkOut)
def get_metadata(code: str, request: Request):
    now = _now(request)
    with _session(request) as session:
        link = service.get_link(session, code)
        return _present(link, request, now)


@api_router.get("/api/v1/urls/{code}/stats", response_model=StatsOut)
def get_stats(code: str, request: Request):
    with _session(request) as session:
        stats = service.link_stats(session, code)
    return {
        "code": code,
        "total_clicks": stats["total_clicks"],
        "last_access": stats["last_access"],
        "top_referrers": stats["top_referrers"],
        "clicks_per_day": stats["clicks_per_day"],
    }


@api_router.post("/api/v1/compliance/purge")
def purge_stored_clicks(request: Request):
    retention_days = request.app.state.settings.click_retention_days
    with _session(request) as session:
        removed = purge_clicks(session, now=_now(request), retention_days=retention_days)
    return {
        "purged": removed,
        "retention_days": retention_days,
        "classification": DATA_CLASSIFICATION,
    }


@api_router.delete("/api/v1/urls/{code}", status_code=204)
def delete_url(code: str, request: Request):
    with _session(request) as session:
        service.deactivate_link(session, code, _now(request))
    return Response(status_code=204)


@redirect_router.get("/{code}")
def redirect_code(code: str, request: Request):
    now = _now(request)
    with _session(request) as session:
        link = service.resolve_redirect(session, code, now)
        try:
            service.record_click(session, link, request.headers.get("referer"), now)
        except Exception:
            session.rollback()
            request.app.state.logger.exception(
                "click_record_failed",
                extra={"fields": {"code": code}},
            )
        target = link.target_url
    response = RedirectResponse(url=target, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    return response
