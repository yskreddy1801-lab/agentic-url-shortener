# URL shortener service

Clients need a durable short link for an http or https URL. A recipient follows the short link. An operator needs click analytics and a way to turn a link off.

## Requirements in scope

- **FR-1**: Create a short link with POST /api/v1/urls.
  - Acceptance: the target must be http or https; the javascript scheme and other schemes are rejected; optional alias and optional expiration are accepted.
- **FR-2**: Redirect with GET /{code} and record the click.
  - Acceptance: a missing code returns 404; an expired or disabled code returns 410; an active code returns 302.
- **FR-3**: Read metadata with GET /api/v1/urls/{code}.
  - Acceptance: the payload includes the target, expiry, and status.
- **FR-4**: Read analytics with GET /api/v1/urls/{code}/stats.
  - Acceptance: the payload includes total clicks, last access, top referrers, and clicks per day.
- **FR-5**: Disable a link with DELETE /api/v1/urls/{code}.
  - Acceptance: a later redirect stops and metadata stays available.
- **FR-6**: Report process health with GET /health.
  - Acceptance: the body reports status ok.
- **FR-7**: Report dependency readiness with GET /ready.
  - Acceptance: the database is checked before the service reports ready.

## API surface grounded in the source tree

- `POST /api/v1/urls` in `src/shortener/api.py` (`create_url`) for FR-1
- `GET /{code}` in `src/shortener/api.py` (`redirect_code`) for FR-2
- `GET /api/v1/urls/{code}` in `src/shortener/api.py` (`get_metadata`) for FR-3
- `GET /api/v1/urls/{code}/stats` in `src/shortener/api.py` (`get_stats`) for FR-4
- `DELETE /api/v1/urls/{code}` in `src/shortener/api.py` (`delete_url`) for FR-5
- `GET /health` in `src/shortener/api.py` (`health`) for FR-6
- `GET /ready` in `src/shortener/api.py` (`ready`) for FR-7

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
