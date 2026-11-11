# Bulk link creation

The shortener already creates one link per request. Callers need to create a batch without changing the single-create API.

## Requirements in scope

- **FR-1**: Create many links in one call with POST /api/v1/urls/bulk.
  - Acceptance: the body accepts 1 to 20 http or https urls and the response lists a code for each item. A javascript scheme inside the batch is rejected.

## API surface grounded in the source tree

- No requirement path matched a scanned route.

## Data model

- `ShortLink` in `src/shortener/models.py`: id, code, target_url, is_custom, expires_at, disabled_at, idempotency_key, request_hash, created_at, clicks
- `Click` in `src/shortener/models.py`: id, link_id, clicked_at, clicked_day, referrer, link

## Decisions

- **Redirect with 302 and Cache-Control: no-store.** A cached 301 would keep sending people to a target after expiry or disable.
- **Non-sequential codes from a CSPRNG alphabet.** A counter leaks volume and lets neighbors guess nearby links.
- **Soft delete.** Metadata and stats stay explainable after a link is turned off.
- **Idempotency-Key compares a hash of the request, not of the derived expiry instant.** A replay must return the original row. Recomputing ttl from 'now' would mint a second link.
- **Generated patches land in the run workspace, not in the main checkout.** A missing route becomes a unified diff and new files. Applying them requires approval. The main tree stays clean.

## Failure modes

- Unknown code: 404 not_found
- Expired or disabled link: 410 expired or disabled
- Alias already used: 409 alias_taken
- Same Idempotency-Key, different body: 409 idempotency_conflict
- Non-http(s) scheme or embedded credentials: 422 unsafe_url
- Create rate exceeded: 429 rate_limited
- Generated code collides: Retry with a new code, then 503
- Click insert fails: Log and still redirect

## Impact analysis

optional custom aliases and optional link expiration

Modules:
- `src/shortener/models.py` (ShortLink, Click): ShortLink stores the public code, target, expiry, and soft-delete timestamp.
- `src/shortener/service.py` (create_link, resolve_redirect, link_status, record_click, deactivate_link, link_stats): Create, redirect policy, click recording, and stats live in the service.
- `src/shortener/api.py` (create_url, redirect_code, get_metadata, get_stats, delete_url): HTTP contract for create, redirect, metadata, stats, and delete.
- `src/shortener/validation.py` (validate_target_url, validate_alias): Scheme checks and alias rules run before a row is written.
- `src/shortener/codes.py` (generate_code, RESERVED_ALIASES): Reserved aliases and the non-sequential code generator.

APIs:
- `POST /api/v1/urls`: Accepts optional alias, ttl_seconds, and expires_at.
- `GET /{code}`: Returns 410 when the link is expired or disabled, otherwise 302 and a click.
- `GET /api/v1/urls/{code}`: Metadata includes expiry, disabled flag, and status.
- `GET /api/v1/urls/{code}/stats`: Stats remain available after expiry or soft-delete.
- `DELETE /api/v1/urls/{code}`: Soft-disables the link. A second delete is idempotent.

Data flow:
- Client POST body enters validate_target_url and validate_alias, then create_link persists a ShortLink with code, target_url, expires_at, and optional idempotency_key.
- GET /{code} calls resolve_redirect. link_status returns disabled or expired as 410, and an active link continues to record_click before a 302.
- GET /api/v1/urls/{code}/stats calls link_stats, which aggregates clicks for that link even when the link is later expired or disabled.
- DELETE /api/v1/urls/{code} calls deactivate_link, which sets disabled_at and leaves the row in place.

Risks:
- Custom aliases are low entropy. Stats and delete are unauthenticated, so a guessable alias is a capability.
- Expiry uses the application clock. A skewed clock expires links early or late.
- Redirect responses send Cache-Control: no-store so a cached 302 does not outlive expiry.
- Links created without an alias or expiry are unchanged. This change does not backfill a TTL.

## Planned change

- `POST /api/v1/urls/bulk` (not in the tree yet) for FR-1

Insertion points: `src/shortener/api.py`, `src/shortener/app.py`, `src/shortener/service.py`, `tests/test_shortener_api.py`
