"""Link creation, redirect policy, soft delete, and click analytics."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shortener.codes import RESERVED_ALIASES, generate_code
from shortener.errors import AppError
from shortener.models import Click, ShortLink
from shortener.validation import validate_alias, validate_idempotency_key, validate_target_url

_CODE_SHAPE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def _as_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def request_fingerprint(url: str, alias: str | None, ttl_seconds: int | None, expires_at: datetime | None) -> str:
    payload = {
        "url": url,
        "alias": alias,
        "ttl_seconds": ttl_seconds,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def link_status(link: ShortLink, now: datetime) -> str:
    if link.disabled_at is not None:
        return "disabled"
    if link.expires_at is not None and as_utc(link.expires_at) <= as_utc(now):
        return "expired"
    return "active"


def get_link(session: Session, code: str) -> ShortLink:
    link = session.scalar(select(ShortLink).where(ShortLink.code == code))
    if link is None:
        raise AppError(404, "not_found", f"No short link exists for code '{code}'")
    return link


def resolve_redirect(session: Session, code: str, now: datetime) -> ShortLink:
    link = get_link(session, code)
    state = link_status(link, now)
    if state == "disabled":
        raise AppError(410, "disabled", f"Link '{code}' is disabled")
    if state == "expired":
        raise AppError(410, "expired", f"Link '{code}' has expired")
    return link


def record_click(session: Session, link: ShortLink, referrer: str | None, now: datetime) -> None:
    cleaned = (referrer or "").strip()
    if len(cleaned) > 512:
        cleaned = cleaned[:512]
    moment = as_utc(now)
    session.add(
        Click(
            link_id=link.id,
            clicked_at=moment,
            clicked_day=moment.strftime("%Y-%m-%d"),
            referrer=cleaned or None,
        )
    )
    session.commit()


def deactivate_link(session: Session, code: str, now: datetime) -> ShortLink:
    link = get_link(session, code)
    if link.disabled_at is None:
        link.disabled_at = as_utc(now)
        session.commit()
    return link


def link_stats(session: Session, code: str) -> dict:
    link = get_link(session, code)
    total = session.scalar(select(func.count()).select_from(Click).where(Click.link_id == link.id)) or 0
    last_access = _as_datetime(session.scalar(select(func.max(Click.clicked_at)).where(Click.link_id == link.id)))
    referrer_rows = session.execute(
        select(func.coalesce(Click.referrer, "direct"), func.count())
        .where(Click.link_id == link.id)
        .group_by(func.coalesce(Click.referrer, "direct"))
        .order_by(func.count().desc(), func.coalesce(Click.referrer, "direct"))
        .limit(10)
    ).all()
    day_rows = session.execute(
        select(Click.clicked_day, func.count())
        .where(Click.link_id == link.id)
        .group_by(Click.clicked_day)
        .order_by(Click.clicked_day)
    ).all()
    return {
        "link": link,
        "total_clicks": int(total),
        "last_access": as_utc(last_access) if last_access is not None else None,
        "top_referrers": [{"referrer": row[0], "clicks": int(row[1])} for row in referrer_rows],
        "clicks_per_day": [{"date": row[0], "clicks": int(row[1])} for row in day_rows],
    }


def create_link(
    session: Session,
    *,
    target_url: str,
    alias: str | None,
    ttl_seconds: int | None,
    expires_at: datetime | None,
    idempotency_key: str | None,
    now: datetime,
    code_length: int,
) -> tuple[ShortLink, bool]:
    """Create a link. The bool is True when an idempotency key replayed an existing row."""
    if ttl_seconds is not None and expires_at is not None:
        raise AppError(422, "invalid_expiry", "Provide only one of ttl_seconds or expires_at")
    target = validate_target_url(target_url)
    custom = validate_alias(alias) if alias else None
    moment = as_utc(now)
    resolved_expiry: datetime | None
    if ttl_seconds is not None:
        resolved_expiry = moment + timedelta(seconds=ttl_seconds)
    elif expires_at is not None:
        resolved_expiry = as_utc(expires_at)
        if resolved_expiry <= moment:
            raise AppError(422, "invalid_expiry", "Expiration must be in the future")
    else:
        resolved_expiry = None
    fingerprint = request_fingerprint(target, custom, ttl_seconds, expires_at)
    key = validate_idempotency_key(idempotency_key) if idempotency_key else None
    if key:
        existing = session.scalar(select(ShortLink).where(ShortLink.idempotency_key == key))
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Idempotency-Key was already used with a different request",
                )
            return existing, True

    attempts = 1 if custom else 8
    for _ in range(attempts):
        code = custom or _fresh_code(code_length)
        link = ShortLink(
            code=code,
            target_url=target,
            is_custom=custom is not None,
            expires_at=resolved_expiry,
            disabled_at=None,
            idempotency_key=key,
            request_hash=fingerprint,
            created_at=moment,
        )
        session.add(link)
        try:
            session.commit()
            return link, False
        except IntegrityError as exc:
            session.rollback()
            message = str(getattr(exc, "orig", exc)).lower()
            if key and "idempotency" in message:
                existing = session.scalar(select(ShortLink).where(ShortLink.idempotency_key == key))
                if existing is not None and existing.request_hash == fingerprint:
                    return existing, True
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Idempotency-Key was already used with a different request",
                ) from exc
            if custom:
                raise AppError(409, "alias_taken", f"Alias '{custom}' is already in use") from exc
            continue
    raise AppError(503, "code_allocation_failed", "Could not allocate a unique code")


def _fresh_code(length: int) -> str:
    for _ in range(5):
        candidate = generate_code(length)
        if candidate.lower() not in RESERVED_ALIASES and _CODE_SHAPE.fullmatch(candidate):
            return candidate
    return generate_code(length)
