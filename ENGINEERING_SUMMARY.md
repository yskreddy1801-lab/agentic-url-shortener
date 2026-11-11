# Engineering summary

## Plan and rationale

The assignment asks for two things that are easy to fake separately and hard to fake together: a URL shortener that actually redirects, and an SDLC workflow that is a governed graph rather than a script.

The shortener is a small FastAPI service with one SQLite database. The interesting constraints are the ones that show up in review: scheme validation, reserved aliases, non-sequential codes, idempotent create, soft delete, expiry that a cached redirect cannot ignore, and stats that still exist after a link is turned off.

The orchestrator is a second component with its own database. Stages are data (`StageSpec`: dependencies, gates, retry budget, fallback, approval). The scheduler computes the ready set and runs that set together. Release readiness depends on testing, documentation, and security review, so it cannot start until that wave finishes. That is the parallel path and the barrier.

Handlers are ordinary Python. They normalize text, parse the shortener with `ast`, run pytest, scan artifacts, and write JSON. There is no model client. An optional model would be an extra reader of the same artifacts; the run does not need one to finish.

Human control sits on two checkpoints. An ambiguous brief pauses before design, with the alternatives stored on the run. Release pauses before the checklist handler. Demo mode performs those pauses in the log and then applies a named rule (`max_rubric_score_then_lexical_id`, `demo_auto_approve`) so a CLI demo can finish. The actor on those rows is `demo_policy`, and the release checklist repeats that this is not a person's sign-off.

## What was built

- Create, redirect, metadata, stats, soft delete, health, and readiness.
- Validation for schemes and aliases, idempotency, collision retry, create rate limit, JSON errors, JSON access logs.
- A seven-stage graph with entry and exit gates, retries, a testing fallback, safe-stop, rollback to a checkpoint, policy blocks, and re-plan that bumps a generation and keeps prior attempts.
- Hash-chained audit events and metrics derived from those events.
- CLI and HTTP control plane.
- Three scenarios, with their latest artifacts checked in under `artifacts/scenarios/`.

The current demo run of the full graph reports stage success rate 1.0, zero retries, zero rollbacks, and peak parallelism 3. End-to-end time on this machine is about 1.3 seconds per scenario, most of it in the nested pytest process.

## Artifacts

| Artifact | Produced by | What a reviewer should see |
| --- | --- | --- |
| `normalized_requirements.json` | requirements | FRs with acceptance, constraints, ambiguity signals |
| `design.md` / `design.json` | architecture | Routes tied to source functions, decisions, and for brownfield the impact section |
| `implementation_report.json` | implementation | Each FR satisfied or a named gap |
| `test_report.json` | testing | Pytest command, exit code, tail of output |
| `docs_snippet.md` | documentation | Caller-facing summary of the requirements in scope |
| `security_review.json` | security_review | Blocking findings, plus the standing product risks |
| `release_checklist.md` | release_readiness | Scope checks, approval evidence, production gaps |
| `events.jsonl` | engine | Ordered, hash-chained transitions |
| `lineage.json` | export | Decisions, checkpoints, artifact versions |
| `metrics.json` | export | Success rate, timings, retries, parallelism, MTTR |

`versions/` keeps every version written during the run. Re-plan adds another version and leaves the previous one with `is_current` false.

## Validation

`pytest` is the check. At the time of writing it reports **43 passed**.

Shortener tests exercise create, redirect and `Cache-Control`, stats (referrers, direct, clicks per day, last access), unsafe schemes, credentials, reserved and colliding aliases, TTL expiry under a frozen clock, soft delete and idempotent delete, idempotency replay versus conflict, the create rate limit, generated-code collision, health, readiness, and OpenAPI.

Orchestrator tests exercise the default edges and the approval flag, overlapping execution of the three parallel stages with release starting after the barrier, retry counts, fallback, rollback of artifact currency, safe-stop, demo approval order (checkpoint, request, grant), supervised pause then human approve, reject, a secret-pattern policy failure that does not retry, re-plan history, the ambiguity rubric, exact route citation in the design note, and the auto-expire interpretation failing closed. One HTTP test starts the ambiguous scenario in supervised mode and asserts the pause payload.

`tests/test_scenarios.py` runs all three demo scenarios through the real handlers, including the real shortener suite inside the testing stage, and checks release readiness, the audit chain, brownfield symbol verification, and the analytics decision lineage.

## Risks, trade-offs, and guardrails

| Risk | What the prototype does about it |
| --- | --- |
| Open redirect | Inherent. Only http and https, no userinfo. Documented as a product property, not a bug that was "fixed" by blocking all off-site targets. |
| Guessable alias | Reserved words and collision checks. No owner auth. Security review and the release checklist say so. Stats and delete are available to anyone who knows the code. |
| Cached redirect ignores expiry | 302 plus `Cache-Control: no-store`. |
| Idempotent create mints a second expiry | Fingerprint is the request body, not `now + ttl`. |
| Click insert failure breaks the redirect | Insert failure is logged and the 302 still returns. |
| Ambiguous brief gets a silent guess | Detector refuses to proceed without a decision record. Demo selection is a published rubric, stored with the rejected alternatives. |
| Workflow edits code on its own | Implementation stage only verifies. A requirement it cannot point at a route or symbol fails the stage. |
| Secret lands in an artifact | `policy_clear` fails the stage on the first attempt. |
| Breaking change slips through | Release checklist requires `accept_breaking` on the approval when the design lists one. Demo does not set that flag, so a breaking interpretation cannot be marked ready by the demo rule. |
| Red tests reported as green | Testing fallback writes `passed: false`. The checklist item fails. |
| Stale approval after an upstream edit | Re-plan invalidates downstream decisions, so release asks again. |
| Audit log edited in place | Each event hashes the previous hash. Tests recompute the chain. |

Trade-off I would defend: the testing stage runs a fixed suite instead of generating tests from acceptance text. Generating tests inside the same process that then grades them is easy to game, and it is a poor use of a prototype without a model. The suite is written against the contract, and the test report says that limitation in its `note` field. The cost is that a brand-new acceptance sentence is not automatically covered by a new test. The implementation stage still fails if the requirement does not name a real route.

Trade-off I would also defend: demo auto-approval. A take-home has to finish in one command. Hiding the checkpoint would be worse. The log shows the pause that a supervised run would honor.

## Assumptions

- One process and one machine. SQLite is the system of record for links and for runs.
- Callers are trusted enough to create links. There is no tenancy.
- "Release" means a readiness report a person can read. This prototype does not deploy, tag, or migrate a second environment.
- The shortener code in this repository is the implementation under review. The greenfield scenario audits that code against a clear brief. It does not regenerate the service from the brief.
- The ambiguity catalog is specific to a URL shortener. A different product would need its own interpretations and rubric. The pause, the lineage, and the refusal to continue without a decision are the reusable parts.
- Clocks are UTC. Expiry uses the application clock injected on the app, which tests freeze.

## Limitations

- The rate limiter is memory inside one process. A second worker has its own window. Restart clears it.
- SQLite and a single writer lock will not carry a large click volume. Stats queries are indexed by link and day; they are not a warehouse.
- Referrers are stored raw, with no retention job and no stripping.
- Create, metadata, stats, and delete are unauthenticated.
- The orchestrator executes inside the API process (the CLI runs it in-process too). `POST /api/v1/runs` waits about a second and then expects the client to poll. A production runner would be a queue and a worker with a lease.
- Rollback restores artifact pointers and stage status. It does not revert git commits, because the workflow does not make commits.
- Safe-stop is cooperative between stages. It does not kill a pytest subprocess that has already started; that stage finishes, and the next one does not start.
- MTTR in the metrics is recovery inside a run (failed attempt to later success). It is not an operations MTTR.
- Hash chaining detects a rewritten log for a single run. It is not a signed external ledger.
- `pytest` prints no third-party deprecation from Starlette's TestClient; that warning is filtered in `pyproject.toml` because current Starlette marks `httpx` TestClient as deprecated in favor of a not-yet-required `httpx2`. The tests still run through FastAPI's TestClient, which is built on httpx.

## How to read a run quickly

```bash
python -m orchestrator run-all --mode demo
python -m orchestrator metrics RUN_ID
python -m orchestrator events RUN_ID
```

Then open `artifacts/scenarios/ambiguous/lineage.json` for the decision, `artifacts/scenarios/brownfield/design.md` for the impact analysis, and any scenario's `release_checklist.md` for what was actually claimed ready.
