"""API coverage for create, redirect, stats, expiry, delete, and validation."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shortener.app import create_app
from shortener.clock import FrozenClock
from shortener.config import Settings
from shortener.models import ShortLink
from shortener import service


def test_health_ready_and_openapi(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/api/v1/urls" in spec.text
    assert client.get("/").json()["service"] == "url-shortener"


def test_create_redirect_metadata_and_stats(client):
    created = client.post("/api/v1/urls", json={"url": "https://example.com/docs"})
    assert created.status_code == 201
    body = created.json()
    code = body["code"]
    assert body["replayed"] is False
    assert body["short_url"] == f"http://test/{code}"
    assert body["status"] == "active"
    assert body["custom_alias"] is False

    first = client.get(f"/{code}", headers={"Referer": "https://news.example/a"}, follow_redirects=False)
    second = client.get(f"/{code}", headers={"Referer": "https://news.example/a"}, follow_redirects=False)
    third = client.get(f"/{code}", follow_redirects=False)
    assert first.status_code == second.status_code == third.status_code == 302
    assert first.headers["location"] == "https://example.com/docs"
    assert first.headers["cache-control"] == "no-store"

    metadata = client.get(f"/api/v1/urls/{code}")
    assert metadata.status_code == 200
    assert metadata.json()["target_url"] == "https://example.com/docs"

    stats = client.get(f"/api/v1/urls/{code}/stats").json()
    assert stats["total_clicks"] == 3
    assert stats["last_access"] is not None
    assert stats["top_referrers"][0] == {"referrer": "https://news.example/a", "clicks": 2}
    assert {"referrer": "direct", "clicks": 1} in stats["top_referrers"]
    assert len(stats["clicks_per_day"]) == 1
    assert stats["clicks_per_day"][0]["clicks"] == 3

    missing = client.get("/missing-code", follow_redirects=False)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_custom_alias_reserved_and_collision(client):
    created = client.post("/api/v1/urls", json={"url": "https://example.com/launch", "alias": "Launch"})
    assert created.status_code == 201
    assert created.json()["code"] == "Launch"
    assert created.json()["custom_alias"] is True
    assert client.get("/Launch", follow_redirects=False).status_code == 302

    reserved = client.post("/api/v1/urls", json={"url": "https://example.com", "alias": "docs"})
    assert reserved.status_code == 400
    assert reserved.json()["error"]["code"] == "alias_reserved"

    taken = client.post("/api/v1/urls", json={"url": "https://example.com/other", "alias": "Launch"})
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "alias_taken"

    invalid = client.post("/api/v1/urls", json={"url": "https://example.com", "alias": "no"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_alias"


def test_unsafe_urls_and_validation_envelope(client):
    for url in (
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "ftp://example.com/a",
        "https://user:pass@example.com/a",
    ):
        response = client.post("/api/v1/urls", json={"url": url})
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "unsafe_url"
    extra = client.post("/api/v1/urls", json={"url": "https://example.com", "note": "nope"})
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_error"
    both = client.post(
        "/api/v1/urls",
        json={"url": "https://example.com", "ttl_seconds": 30, "expires_at": "2030-01-01T00:00:00Z"},
    )
    assert both.status_code == 422
    past = client.post("/api/v1/urls", json={"url": "https://example.com", "expires_at": "2000-01-01T00:00:00Z"})
    assert past.status_code == 422
    assert past.json()["error"]["code"] == "invalid_expiry"


def test_expiry_blocks_redirect_and_keeps_stats(app, client):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    app.state.clock = clock
    created = client.post("/api/v1/urls", json={"url": "https://example.com/soon", "ttl_seconds": 60})
    assert created.status_code == 201
    code = created.json()["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 302
    clock.advance(61)
    expired = client.get(f"/{code}", follow_redirects=False)
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "expired"
    metadata = client.get(f"/api/v1/urls/{code}").json()
    assert metadata["status"] == "expired"
    stats = client.get(f"/api/v1/urls/{code}/stats").json()
    assert stats["total_clicks"] == 1


def test_soft_delete_stops_redirect_and_is_idempotent(client):
    code = client.post("/api/v1/urls", json={"url": "https://example.com/off"}).json()["code"]
    assert client.delete(f"/api/v1/urls/{code}").status_code == 204
    gone = client.get(f"/{code}", follow_redirects=False)
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "disabled"
    metadata = client.get(f"/api/v1/urls/{code}").json()
    assert metadata["disabled"] is True
    assert metadata["status"] == "disabled"
    assert client.get(f"/api/v1/urls/{code}/stats").status_code == 200
    assert client.delete(f"/api/v1/urls/{code}").status_code == 204
    assert client.delete("/api/v1/urls/missing").status_code == 404


def test_idempotency_replay_and_conflict(app, client):
    headers = {"Idempotency-Key": "create-123"}
    first = client.post("/api/v1/urls", json={"url": "https://example.com/same"}, headers=headers)
    second = client.post("/api/v1/urls", json={"url": "https://example.com/same"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["code"] == first.json()["code"]
    conflict = client.post("/api/v1/urls", json={"url": "https://example.com/other"}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    with app.state.sessions() as session:
        count = session.scalar(select(func.count()).select_from(ShortLink))
    assert count == 1


def test_create_rate_limit(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/short.db",
        orchestrator_database_url=f"sqlite:///{tmp_path}/orch.db",
        rate_limit_creates=2,
        rate_limit_window_seconds=60,
        base_url="http://test",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/v1/urls", json={"url": "https://example.com/1"}).status_code == 201
        assert client.post("/api/v1/urls", json={"url": "https://example.com/2"}).status_code == 201
        blocked = client.post("/api/v1/urls", json={"url": "https://example.com/3"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers["retry-after"]


def test_code_collision_retries(app, client, monkeypatch):
    codes = iter(["TAKENCODE", "FRESHCODE"])
    monkeypatch.setattr(service, "generate_code", lambda length=7: next(codes))
    with app.state.sessions() as session:
        session.add(
            ShortLink(
                code="TAKENCODE",
                target_url="https://example.com/old",
                is_custom=False,
                expires_at=None,
                disabled_at=None,
                idempotency_key=None,
                request_hash="seed",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    created = client.post("/api/v1/urls", json={"url": "https://example.com/new"})
    assert created.status_code == 201
    assert created.json()["code"] == "FRESHCODE"
