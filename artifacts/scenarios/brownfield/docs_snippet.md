# Custom aliases and expiration

The shortener already creates links, redirects them, and records clicks. Add optional custom aliases and optional expiration without changing links that use neither.

## What shipped in this scope

- FR-1: Accept an optional alias on POST /api/v1/urls.
- FR-2: Accept an optional expiration on POST /api/v1/urls.
- FR-3: Keep analytics available with GET /api/v1/urls/{code}/stats after expiry.

## How to call it

Create a link:

```bash
curl -s -X POST http://127.0.0.1:8741/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/docs"}'
```

Routes traced to the repository:

- `POST /api/v1/urls`
- `POST /api/v1/urls`
- `GET /{code}`
- `GET /api/v1/urls/{code}/stats`

Redirects use HTTP 302 and `Cache-Control: no-store`. Expired and disabled links return 410.
