# Architecture

Two processes of thought share one FastAPI app. The shortener stores links and answers HTTP. The orchestrator stores runs and walks an explicit stage graph. They use separate SQLite files so a workflow experiment cannot corrupt link data.

## Components

| Piece | Responsibility |
| --- | --- |
| `shortener.api` | Routes. Health and readiness are registered before `GET /{code}` so the redirect cannot swallow them. |
| `shortener.validation` | http/https only. Rejects other schemes, embedded credentials, and control characters. Alias shape and reserved words. |
| `shortener.codes` | 7-character codes from `secrets`, using an alphabet without 0/O and 1/I/l. Not a counter. |
| `shortener.service` | Create, idempotent replay, redirect policy, best-effort click insert, soft delete, stats. |
| `shortener.rate_limit` | In-process sliding window on create. |
| `orchestrator.graph` | Stage specs: dependencies, entry gates, exit gates, retry budget, fallback, approval flag. |
| `orchestrator.engine` | Scheduler. Runs every ready stage, including a parallel wave, then waits before the next wave. |
| `orchestrator.gates` | Artifact presence, resolved ambiguity, green tests, clean policy scan. |
| `orchestrator.policies` | Secret patterns and unsafe URL tokens in artifacts. Blocking findings fail the stage. |
| `orchestrator.handlers` | Deterministic SDLC work. No model call. |
| `orchestrator.store` | Runs, stage attempts, artifact versions, decisions, checkpoints, hash-chained events. |
| `orchestrator.cli` / `orchestrator.api` | Start, inspect, approve, reject, stop, roll back, re-plan. |

## Shortener

`POST /api/v1/urls` accepts `url`, optional `alias`, and one of `ttl_seconds` or `expires_at`. The idempotency key is a header. The fingerprint is the request fields, not the computed expiry instant, so a replay returns the original row instead of minting a second expiry.

Public codes are either the caller's alias or a generated code. Alias collisions return 409. Generated collisions retry. Reserved words (`api`, `health`, `docs`, and the rest of `RESERVED_ALIASES`) are rejected case-insensitively.

`GET /{code}` returns 302 with `Cache-Control: no-store`. Missing codes are 404. Expired and disabled links are 410, with `expired` and `disabled` distinguished in the error envelope. A click is written before the redirect. If that insert fails, the redirect still proceeds and the failure is logged. Analytics must not take the redirect down with them.

`DELETE /api/v1/urls/{code}` sets `disabled_at`. The row, metadata, and stats remain. A second delete is 204.

Stats aggregate in SQL: total clicks, last access, top referrers (empty referrer reported as `direct`), and clicks per UTC day. The day is stored on the click row so the group-by does not depend on SQLite datetime formatting.

Errors share one envelope:

```json
{"error": {"code": "unsafe_url", "message": "...", "details": {}}}
```

Access logs are JSON lines with method, path, status, duration, and request id.

## Orchestration model

The default graph is:

```
requirements
    -> planning
        -> architecture
            -> implementation
                -> apply_patch
                    -> testing
                    -> documentation
                    -> security_review
                        -> release_readiness   (approval required before the handler runs)
```

`planning` writes a task subgraph from the normalized requirements: one design, implement, test, and document task per functional requirement, plus apply, security, and release tasks. Dependencies are recorded on the tasks. The engine marks each task succeeded when its owning stage succeeds. The current plan is in `task_plan` and in `metrics.json` (`tasks_total`, `tasks_succeeded`).

`testing`, `documentation`, and `security_review` depend only on `apply_patch`. The scheduler submits that wave to a thread pool. `release_readiness` stays pending until all three are in a success state. That is the synchronization barrier. The latest demo runs record `parallelism_max` of 3.

A stage is ready when it is `pending` and every dependency is `succeeded` or `succeeded_with_fallback`.

### Gates

Entry gates run before the handler. Exit gates run after it. A failed entry gate is not retried: repeating the handler will not create an upstream artifact. A failed exit gate such as `tests_green` is retried inside the stage budget. `policy_clear` is not retried. A secret in an artifact does not disappear on the second attempt.

### Context and lineage

Handlers see a `RunContext`: the brief, the mode, the current artifacts, and `resolve_choice`. Every artifact write, gate result, retry, fallback, approval, decision, checkpoint, invalidation, and terminal transition is an event. Each event stores `prev_hash` and `event_hash` over a canonical JSON payload, so the log for a run can be checked for gaps or edits. `verify_event_chain` does that check in tests.

Decisions are first-class rows: question, alternatives, selected id, actor (`human` or `demo_policy`), rule, and rationale. Re-plan marks downstream decisions invalid so a release approval does not survive a changed upstream artifact.

### Approval

`release_readiness` requires approval before it runs. The engine writes a pre-approval checkpoint, then `approval.requested`.

- `supervised` sets the run to `paused` and returns. `approve` records a human decision and resumes. `reject` fails the stage and ends the run as `rejected` without running the handler.
- `demo` records the same checkpoint and request, then writes an approval whose actor is `demo_policy` and whose rationale says this is an automatic continuation, not a human sign-off.

Ambiguous requirements use the same pause machinery with `pause_kind=decision`. The requirements handler calls `resolve_choice` with three scored interpretations. Supervised mode pauses before any design work. Demo mode selects the highest rubric score, breaking ties by id, and stores the ranking in the decision.

### Retry, fallback, stop, rollback

Each stage has `max_attempts`. A retryable exception consumes an attempt and emits `stage.retry`. After the budget, an optional fallback handler runs once. The testing stage's fallback writes a report with `passed: false`. Release readiness then refuses to mark the scope ready. Fallback cannot paint a red suite green.

`stop` sets a flag and emits `run.stop_requested`. The in-flight stage finishes. The scheduler does not start another stage. The run becomes `stopped`.

Checkpoints are snapshots of stage status and current artifact versions, taken at run start, before an approval or decision, and after each successful stage. `rollback` restores that snapshot. Stages that succeeded after the chosen checkpoint become `rolled_back`. Their artifact versions stay in the table with `is_current` false. Default target: the latest checkpoint when the run failed (the failed stage did not checkpoint), otherwise the checkpoint before the newest one, which undoes the last successful stage.

### Re-plan

`replan` writes a new version of an upstream artifact, invalidates every transitive dependent (status back to `pending`, generation incremented, decisions invalidated), and executes again. Attempt rows from the previous generation stay. History is the attempt table plus artifact versions, not a rewritten log.

### Policy

`scan_artifacts` blocks on AWS-key-shaped tokens, private-key blocks, quoted secret assignments, Slack-style tokens, and URL tokens whose scheme is javascript, data, vbscript, file, or ftp. Prose that says "javascript scheme" does not match, because the pattern requires a payload after the colon. `evaluate_compliance` is a separate gate (`compliance_controls`) on the workspace tree: `sanitize_referrer` must exist and be used when a click is stored, `purge_clicks` must exist, `POST /api/v1/compliance/purge` must exist, and `click_retention_days` must be configurable. The security report includes the data-classification note. Warnings do not fail the stage. The release checklist fails closed if the design declares a breaking change and the approval payload does not set `accept_breaking`, and it fails closed if a compliance control is missing.

### Metrics

Computed from the log and attempt rows, not from a second write path:

- stage success rate (success states / stages in the graph)
- task counts from the current task plan (`tasks_total`, `tasks_succeeded`)
- retry, rollback, fallback, approval, and decision counts
- per-stage timings
- end-to-end latency from run start to the terminal or paused-finished timestamp
- MTTR as the mean time from a failed attempt to a later success of that same stage, null when nothing recovered
- peak overlapping attempt intervals

On the default demo runs, peak parallelism is 3: testing, documentation, and security review.

## Control flow for a demo run

1. `run.started`, initial checkpoint.
2. Requirements normalizes the brief. A vague brief emits `decision.requested`, a checkpoint, and `decision.recorded` before the artifact exists.
3. Planning writes the task subgraph for those FRs.
4. Architecture scans `src/shortener` with the AST and writes a design. Brownfield runs attach an impact report. Missing base symbols fail the stage. A new route that is not in the tree yet is listed as a planned addition.
5. Implementation maps each FR to an exact route. A known missing route becomes a unified diff and new files. Greenfield, already implemented, writes a sandbox scaffold. A gap with no template fails the stage.
6. `apply_patch` requests approval, then writes the patch into `artifacts/workspaces/<run_id>/`. Demo mode auto-approves as `demo_policy`.
7. Testing, docs, and security review run together against that workspace. Testing runs the shortener suite plus `tests/test_compliance.py` and any generated test. A failed suite rolls the patch back. Security enforces secret scanning and the compliance gate.
8. After the barrier, the release checkpoint and the demo approval are recorded, then release readiness builds the checklist.
9. `run.completed`. The business outcome is `release_report.ready`. A fallback test report or a rolled-back patch keeps it false.

## Key decisions

**302 and `Cache-Control: no-store`.** A cached permanent redirect would ignore expiry and disable.

**Non-sequential codes.** A counter discloses volume and makes neighbors guessable. Aliases are a separate, explicit, low-entropy choice.

**Soft delete.** Operators can still explain a link after it stops redirecting.

**Idempotency hashes the request, not the derived timestamp.** Otherwise every replay of `ttl_seconds` would look like a new expiry.

**The implementation stage writes a patch when the tree does not already satisfy the brief.** Verification still maps each FR to a real route. A missing route with a template (`POST /api/v1/urls/bulk`) becomes a unified diff plus new files: service function, route module, app wiring, and a test. Greenfield, whose routes already exist, writes a sandbox contract scaffold under `sandbox/shortener_scaffold/` so generation still produces a reviewable diff. The scaffold is not a second service.

**Applying that patch is high impact and stays off the main checkout.** `apply_patch` asks for approval. Demo mode records `demo_policy` and continues. Supervised mode pauses. The files are written only under `artifacts/workspaces/<run_id>/`, which is gitignored. If the workspace suite fails, the testing stage rolls the patch back inside that copy. The main `src/` tree is unchanged.

**Demo approval is a recorded override.** The event order is checkpoint, `approval.requested`, `approval.granted` with actor `demo_policy`. The release checklist repeats that this is not a human sign-off.

**Separate databases and a store-wide lock.** Parallel handlers share one SQLite connection factory. Writes take an `RLock`. WAL and `busy_timeout` cover the rest. Link data is not in that file.

## Trust boundary

The create endpoint will redirect browsers to any http(s) URL the caller supplies. That is the product. Schemes other than http and https, and URLs with userinfo, are rejected. There is no authentication. Knowing a code is enough to read stats or disable the link. Generated codes are the mitigation for that; custom aliases are not.

The orchestrator API does not accept a shell command. The test stage's argv is fixed in code.
