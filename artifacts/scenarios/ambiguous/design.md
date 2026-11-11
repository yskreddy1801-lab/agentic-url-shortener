# make links better

Chosen interpretation 'analytics'. Make links better by exposing click analytics: total clicks, last access, top referrers, and clicks per day.

## Requirements in scope

- **FR-1**: Expose click analytics for a short link.
  - Acceptance: GET /api/v1/urls/{code}/stats returns total clicks, last access, top referrers, and clicks per day.

## API surface grounded in the source tree

- `GET /api/v1/urls/{code}/stats` in `src/shortener/api.py` (`get_stats`) for FR-1

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
