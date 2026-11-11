"""Retention, purge, and referrer sanitizing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from shortener.compliance import purge_clicks, sanitize_referrer
from shortener.models import Click, ShortLink
from shortener.paths import repo_root
from orchestrator.policies import evaluate_compliance


def test_query_tokens_and_emails_are_not_stored_raw():
    assert sanitize_referrer("https://news.example/a?token=sekret&x=1") == "https://news.example/a"
    assert sanitize_referrer("https://news.example/a#fragment") == "https://news.example/a"
    assert sanitize_referrer("javascript:alert(1)") is None
    hashed = sanitize_referrer("alice@example.com")
    assert hashed is not None
    assert hashed.startswith("hashed:")
    assert "alice@" not in hashed


def test_redirect_stores_the_sanitized_referrer(client):
    code = client.post("/api/v1/urls", json={"url": "https://example.com/clean"}).json()["code"]
    response = client.get(
        f"/{code}",
        headers={"Referer": "https://news.example/a?token=sekret"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    stats = client.get(f"/api/v1/urls/{code}/stats").json()
    assert stats["top_referrers"] == [{"referrer": "https://news.example/a", "clicks": 1}]


def test_purge_removes_clicks_outside_the_retention_window(app, client):
    code = client.post("/api/v1/urls", json={"url": "https://example.com/keep"}).json()["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 302
    with app.state.sessions() as session:
        link = session.scalar(select(ShortLink).where(ShortLink.code == code))
        session.add(
            Click(
                link_id=link.id,
                clicked_at=datetime.now(timezone.utc) - timedelta(days=90),
                clicked_day="2020-01-01",
                referrer="https://old.example/path",
            )
        )
        session.commit()
    purged = client.post("/api/v1/compliance/purge")
    assert purged.status_code == 200
    body = purged.json()
    assert body["purged"] == 1
    assert body["retention_days"] == app.state.settings.click_retention_days
    assert body["classification"]["clicks.referrer"] == "confidential_telemetry"
    stats = client.get(f"/api/v1/urls/{code}/stats").json()
    assert stats["total_clicks"] == 1


def test_purge_function_respects_a_short_window(app, client):
    client.get("/health")
    with app.state.sessions() as session:
        session.add(
            ShortLink(
                code="OLD1",
                target_url="https://example.com/old",
                is_custom=False,
                expires_at=None,
                disabled_at=None,
                idempotency_key=None,
                request_hash=None,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        link = session.scalar(select(ShortLink).where(ShortLink.code == "OLD1"))
        session.add(
            Click(
                link_id=link.id,
                clicked_at=datetime.now(timezone.utc) - timedelta(days=2),
                clicked_day="2020-01-01",
                referrer="https://old.example",
            )
        )
        session.commit()
        removed = purge_clicks(session, now=datetime.now(timezone.utc), retention_days=1)
    assert removed == 1


def test_compliance_policy_passes_on_this_tree_and_blocks_an_empty_one(tmp_path):
    assert evaluate_compliance(repo_root())["ok"] is True
    empty = evaluate_compliance(tmp_path)
    assert empty["ok"] is False
    assert any(item["policy"] == "purge_endpoint" for item in empty["findings"])
