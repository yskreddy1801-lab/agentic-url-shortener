"""The three executable SDLC scenarios."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    title: str
    brief: str


GREENFIELD_BRIEF = """Title: URL shortener service

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
"""

BROWNFIELD_BRIEF = """Title: Custom aliases and expiration

Problem:
The shortener already creates links, redirects them, and records clicks. Add optional custom aliases and optional expiration without changing links that use neither.

Requirements:
- FR: Accept an optional alias on POST /api/v1/urls.
  Acceptance: reserved aliases are rejected and a colliding alias returns 409.
- FR: Accept an optional expiration on POST /api/v1/urls.
  Acceptance: ttl_seconds or expires_at is stored, and GET /{code} returns 410 after the link is expired.
- FR: Keep analytics available with GET /api/v1/urls/{code}/stats after expiry.
  Acceptance: clicks recorded before expiry remain in the totals.

Constraints:
- Existing links with no alias and no expiry keep redirecting.
- Soft-delete with DELETE /api/v1/urls/{code} stays independent of expiry.
- Include an impact analysis of modules, APIs, and data flow.

Out of scope:
- Backfilling an expiration onto links that were created without one.
"""

AMBIGUOUS_BRIEF = """make links better
"""


SCENARIOS = {
    "greenfield": Scenario(
        name="greenfield",
        kind="greenfield",
        title="Build the URL shortener from a clear brief",
        brief=GREENFIELD_BRIEF,
    ),
    "brownfield": Scenario(
        name="brownfield",
        kind="brownfield",
        title="Add custom aliases and expiration to the existing service",
        brief=BROWNFIELD_BRIEF,
    ),
    "ambiguous": Scenario(
        name="ambiguous",
        kind="ambiguous",
        title="Resolve an underspecified request before designing it",
        brief=AMBIGUOUS_BRIEF,
    ),
}
