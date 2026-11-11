Title: Custom aliases and expiration

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
