Title: Bulk link creation

Problem:
The shortener already creates one link per request. Callers need to create a batch without changing the single-create API.

Requirements:
- FR: Create many links in one call with POST /api/v1/urls/bulk.
  Acceptance: the body accepts 1 to 20 http or https urls and the response lists a code for each item. A javascript scheme inside the batch is rejected.

Constraints:
- Do not change the behavior of POST /api/v1/urls.
- Keep GET /{code} redirect behavior.
- Include an impact analysis of modules, APIs, and data flow.

Out of scope:
- A background import from a CSV file.
