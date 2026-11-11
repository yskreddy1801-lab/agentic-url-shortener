Title: URL shortener service

Problem:
Clients need a durable short link for an http or https URL. A recipient follows the short link. An operator needs click analytics and a way to turn a link off.

Requirements:
- FR: Create a short link with POST /api/v1/urls.
  Acceptance: the target must be http or https; the javascript scheme and other schemes are rejected; optional alias and optional expiration are accepted.
- FR: Redirect with GET /{code} and record the click.
  Acceptance: a missing code returns 404; an expired or disabled code returns 410; an active code returns 302.
- FR: Read metadata with GET /api/v1/urls/{code}.
  Acceptance: the payload includes the target, expiry, and status.
- FR: Read analytics with GET /api/v1/urls/{code}/stats.
  Acceptance: the payload includes total clicks, last access, top referrers, and clicks per day.
- FR: Disable a link with DELETE /api/v1/urls/{code}.
  Acceptance: a later redirect stops and metadata stays available.
- FR: Report process health with GET /health.
  Acceptance: the body reports status ok.
- FR: Report dependency readiness with GET /ready.
  Acceptance: the database is checked before the service reports ready.

Constraints:
- Persist links and clicks in SQLite.
- Generated codes are non-sequential.
- Reserved aliases such as api, health, and docs are rejected.
- Idempotency-Key replays the original create when the payload matches.
- Errors use one JSON envelope.

Out of scope:
- Accounts, API keys, and custom domains.
- A public marketing site.
