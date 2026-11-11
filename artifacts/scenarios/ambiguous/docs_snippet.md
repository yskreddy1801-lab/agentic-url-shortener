# make links better

Make links better by exposing click analytics: total clicks, last access, top referrers, and clicks per day.

## What shipped in this scope

- FR-1: Expose click analytics for a short link.

## How to call it

Create a link:

```bash
curl -s -X POST http://127.0.0.1:8741/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/docs"}'
```

Routes traced to the repository:

- `GET /api/v1/urls/{code}/stats`

## Requirement decision

Selected `analytics` by `demo_policy` using rule `max_rubric_score_then_lexical_id`.

Selected 'analytics' with score 1.0. Rule: highest rubric score, then lexical id. Ranking: analytics=1.0, aliases=0.9, auto_expire_all=0.25. Non-breaking, testable, low-risk, in-codebase, and narrow scope score higher. Demo mode records this choice so the run can continue. A supervised run pauses until a person picks an interpretation.

Redirects use HTTP 302 and `Cache-Control: no-store`. Expired and disabled links return 410.
