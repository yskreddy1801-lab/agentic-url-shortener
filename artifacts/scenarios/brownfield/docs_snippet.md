# Bulk link creation

The shortener already creates one link per request. Callers need to create a batch without changing the single-create API.

## What shipped in this scope

- FR-1: Create many links in one call with POST /api/v1/urls/bulk.

## How to call it

Create a link:

```bash
curl -s -X POST http://127.0.0.1:8741/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/docs"}'
```

Redirects use HTTP 302 and `Cache-Control: no-store`. Expired and disabled links return 410.
