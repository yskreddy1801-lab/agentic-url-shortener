# URL shortener service

Clients need a durable short link for an http or https URL. A recipient follows the short link. An operator needs click analytics and a way to turn a link off.

## What shipped in this scope

- FR-1: Create a short link with POST /api/v1/urls.
- FR-2: Redirect with GET /{code} and record the click.
- FR-3: Read metadata with GET /api/v1/urls/{code}.
- FR-4: Read analytics with GET /api/v1/urls/{code}/stats.
- FR-5: Disable a link with DELETE /api/v1/urls/{code}.
- FR-6: Report process health with GET /health.
- FR-7: Report dependency readiness with GET /ready.

## How to call it

Create a link:

```bash
curl -s -X POST http://127.0.0.1:8741/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/docs"}'
```

Routes traced to the repository:

- `POST /api/v1/urls`
- `GET /{code}`
- `GET /api/v1/urls/{code}`
- `GET /api/v1/urls/{code}/stats`
- `DELETE /api/v1/urls/{code}`
- `GET /health`
- `GET /ready`

Redirects use HTTP 302 and `Cache-Control: no-store`. Expired and disabled links return 410.
