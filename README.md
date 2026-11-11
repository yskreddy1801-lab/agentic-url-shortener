# Agentic URL shortener

A working URL shortener and a workflow engine that takes a brief through requirements, design, implementation checks, testing, documentation, security review, and release readiness.

The shortener is a normal HTTP service. The orchestrator is a dependency graph with gates, parallel stages, approval checkpoints, retries, rollback, policy checks, and an append-only hash-chained audit log. It runs with no LLM key. Handlers do deterministic work: normalize a brief, read the source tree, run pytest, write artifacts, and record why a decision was made.

## Setup

From the repository root, with Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e . --no-deps
```

## Run the server

```bash
uvicorn shortener.main:app --host 0.0.0.0 --port 8741
```

- Health: `GET http://127.0.0.1:8741/health`
- Readiness: `GET http://127.0.0.1:8741/ready`
- OpenAPI: `http://127.0.0.1:8741/docs`

Create a link, follow it, and read stats:

```bash
curl -s -X POST http://127.0.0.1:8741/api/v1/urls \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"url":"https://example.com/docs","alias":"launch","ttl_seconds":86400}'

curl -s -D - -o /dev/null http://127.0.0.1:8741/launch

curl -s http://127.0.0.1:8741/api/v1/urls/launch/stats
```

`SHORTENER_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` override the default SQLite files under `data/`. `BASE_URL` is the prefix used in `short_url` (default `http://127.0.0.1:8741`). `RATE_LIMIT_CREATES` defaults to 60 creates per 60 seconds per client address.

## Run the tests

```bash
pytest
```

The suite covers create, redirect, stats, expiry, delete, validation, and the orchestrator (graph order, parallel barrier, retry, fallback, rollback, safe-stop, demo auto-approve versus a real pause, policy block, re-plan, and ambiguous-requirement lineage).

## Run the three scenarios

```bash
python -m orchestrator run-all --mode demo
```

One scenario:

```bash
python -m orchestrator run greenfield --mode demo
python -m orchestrator run brownfield --mode demo
python -m orchestrator run ambiguous --mode demo
```

`demo` records each human checkpoint and then applies a documented rule so the command finishes. `supervised` pauses instead:

```bash
python -m orchestrator run ambiguous --mode supervised
python -m orchestrator approve RUN_ID --stage requirements --alternative analytics --note "Ship analytics"
python -m orchestrator approve RUN_ID --stage release_readiness --note "Prototype scope accepted"
```

Other controls:

```bash
python -m orchestrator status RUN_ID
python -m orchestrator events RUN_ID
python -m orchestrator lineage RUN_ID
python -m orchestrator metrics RUN_ID
python -m orchestrator stop RUN_ID
python -m orchestrator rollback RUN_ID
python -m orchestrator reject RUN_ID --stage release_readiness --note "Hold"
python -m orchestrator replan RUN_ID --artifact normalized_requirements --content-file path/to/requirements.json --reason "Brief edited"
```

Latest artifacts for each scenario are written to:

- `artifacts/scenarios/greenfield/`
- `artifacts/scenarios/brownfield/`
- `artifacts/scenarios/ambiguous/`

Each folder contains the normalized requirements, design note, implementation report, test report, docs snippet, security review, release checklist, hash-chained `events.jsonl`, lineage, and metrics. Older versions of an artifact are under `versions/`. The full run history stays in `data/orchestrator.db`.

The same controls are on the HTTP API: `POST /api/v1/runs`, then `GET /api/v1/runs/{id}`, `/events`, `/lineage`, `/metrics`, and `POST` `approve`, `reject`, `stop`, `rollback`, and `replan`. `POST /api/v1/runs` waits up to one second so a fast pause is visible; poll `GET /api/v1/runs/{id}` while status is `running`.

## Layout

```
src/shortener/        link API, validation, persistence, rate limit
src/orchestrator/     graph, engine, gates, policies, handlers, CLI
tests/                shortener API tests and orchestrator tests
artifacts/scenarios/  output of the three demo runs
docs/SCENARIOS.md     how each scenario is decomposed and checked
ARCHITECTURE.md       components, control flow, decisions
ENGINEERING_SUMMARY.md
```

Read `ARCHITECTURE.md` for the execution model and `ENGINEERING_SUMMARY.md` for scope, risks, and what this prototype does not pretend to be.
