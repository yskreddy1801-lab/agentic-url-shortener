# Scenarios

All three scenarios use the same graph:

```
requirements -> architecture -> implementation
implementation -> testing, documentation, security_review   (parallel)
those three -> release_readiness                            (barrier, approval first)
```

Demo mode is the path `python -m orchestrator run-all --mode demo` executes. Supervised mode pauses at the decision checkpoint and again at release. Artifacts from the latest demo run of each scenario are in `artifacts/scenarios/<name>/`.

Validation for every scenario is the same pair of checks:

- The implementation stage maps each normalized requirement onto a route that the AST scan actually found.
- The testing stage runs `tests/test_validation.py` and `tests/test_shortener_api.py` and records the exit code. Release readiness sets `ready` only when that suite passed, the security scan has no blocking finding, and the approval row exists.

## 1. Greenfield

**Input.** A structured brief: create, redirect, metadata, stats, delete, health, and readiness, with acceptance lines for schemes, 404/410/302, and the JSON error envelope. The text is in `orchestrator.scenarios.GREENFIELD_BRIEF`.

**Decomposition.** The normalizer reads `Title`, `Problem`, `Requirements` (`- FR:` plus `Acceptance:`), `Constraints`, and `Out of scope`. It produces seven functional requirements, FR-1 through FR-7. The ambiguity detector stays quiet because the brief names methods, paths, and status codes.

**Orchestration.**

| Stage | What it writes |
| --- | --- |
| requirements | `normalized_requirements` with `is_ambiguous: false` |
| architecture | Design note whose API list is the exact routes named by those FRs, plus failure modes and the redirect/code/idempotency decisions |
| implementation | One evidence row per FR. A missing route is a gap and the stage fails |
| testing | Pytest report for the shortener suite |
| documentation | A short caller-facing snippet, drafted from the requirements, in parallel with testing |
| security_review | Policy scan of the artifacts so far, plus the standing notes on open redirect and unauthenticated codes |
| release_readiness | Checklist and recommendation, after the demo approval checkpoint |

**Validation.** `release_report.ready` is true only when the suite passed and every scope checklist item passed. The report's recommendation is `accept_for_prototype_with_known_limits`. Production gaps (auth, process-local rate limit, in-process orchestrator) are listed on the checklist and do not get marked as solved.

**Where to look.** `artifacts/scenarios/greenfield/`. `events.jsonl` is the audit log. `metrics.json` shows stage timings and `parallelism_max` of 3.

## 2. Brownfield

**Input.** The service already shortens links. The brief asks for optional custom aliases and optional expiration, and it asks for an impact analysis of modules, APIs, and data flow. Existing links with neither feature must keep working. Backfilling a TTL onto old links is out of scope.

**Decomposition.** Three requirements: alias on create, expiry on create and 410 on `GET /{code}`, stats still available after expiry. The scenario kind is `brownfield`, recorded on the run and copied into the requirements document. The detector does not treat this brief as ambiguous: it names aliases, expiry, and concrete routes.

**Orchestration.** The graph is the same one used for greenfield. The architecture handler, because `kind` is `brownfield`, runs `analyze_alias_and_expiry` on the AST scan:

- Required symbols such as `create_link`, `resolve_redirect`, `validate_alias`, `generate_code`, and `RESERVED_ALIASES` must be present in the files the scanner opened.
- `ShortLink` must expose `expires_at`, `disabled_at`, `code`, `target_url`, and `idempotency_key`.
- The five public routes must be present.
- Data-flow sentences are emitted only when the symbols they name were actually found.

If any of that is missing, the architecture stage fails and release does not run. The design note includes the impact section: modules, API diffs, data flow, and risks (guessable aliases, clock skew, cache, no backfill).

**Validation.** The implementation stage checks the three requirement paths against the tree. The testing stage runs the suite that covers alias reservation, alias collision, TTL expiry with a frozen clock, and stats after expiry. The release checklist has an extra item: "Brownfield impact analysis verified against the source tree".

**Where to look.** `artifacts/scenarios/brownfield/design.md` (impact section) and `implementation_report.json`.

## 3. Ambiguous

**Input.** The entire brief is `make links better`.

**Decomposition.** The detector flags `vague_language:better`, `no_concrete_acceptance_or_contract`, `very_short`, and `no_structured_requirements`. It does not pick a behavior by itself. It attaches three interpretations of a URL shortener:

| Id | Reading | Score | Why that score |
| --- | --- | --- | --- |
| `analytics` | Expose click stats | 1.00 | Non-breaking, testable, low risk, already in the tree, narrow |
| `aliases` | Custom alias on create | 0.90 | Same, with a wider write-path scope |
| `auto_expire_all` | Force-expire every existing link after 7 days | 0.25 | Breaking, high risk, not what the code does |

The rubric is fixed in `orchestrator.ambiguity`: +0.35 non-breaking, +0.25 testable, +0.20 low risk, +0.10 fits the current code, +0.10 narrow scope. Ties break by id.

**Orchestration.** Before any design artifact is written, the requirements stage creates a checkpoint and emits `decision.requested` with all three alternatives.

- **Demo.** The next event is `decision.recorded` with actor `demo_policy`, rule `max_rubric_score_then_lexical_id`, and the ranking in the rationale. The normalized requirements then contain a single FR for `GET /api/v1/urls/{code}/stats`. Downstream stages design, verify, test, and document that interpretation only.
- **Supervised.** The run stays `paused` with `pause_kind=decision`. Nothing downstream starts. Approving with `--alternative aliases` (or `analytics`) resumes; the handler checks the id is one of the three and records actor `human`. Approving `auto_expire_all` is legal as a decision and then fails the implementation stage, because that requirement names no route or symbol the tree contains. The workflow does not invent a retention job to make the choice look done.

Release still waits for the approval checkpoint. The checklist item for ambiguity quotes the decision id and the rationale.

**Validation.** The demo run's lineage has one ambiguity decision, three alternatives, and `selected=analytics`. `tests/test_scenarios.py` asserts that. `tests/test_orchestrator.py` asserts the detector, the rubric winner, and that the auto-expire reading is not reported as implemented. A supervised HTTP check (`POST /api/v1/runs` with `mode=supervised`) asserts the pause payload lists all three ids and that no decision has been recorded yet.

**Where to look.** `artifacts/scenarios/ambiguous/lineage.json`, `normalized_requirements.json`, and the `decision.requested` / `decision.recorded` lines in `events.jsonl`.

## What a failed control looks like

These are covered by unit tests rather than by the happy-path scenario folders:

- A handler that raises twice then succeeds emits two `stage.retry` events.
- After the testing budget, the fallback handler runs once and release will not claim the suite passed.
- `rollback` on a finished linear graph restores the previous checkpoint and marks the undone stage `rolled_back` while keeping the old artifact version.
- `stop` during a running stage lets that stage finish and leaves the next stage `pending`.
- An artifact containing an `AKIA…` token fails `policy_clear` on the first attempt and fails the run.
- Replacing an upstream artifact and calling `replan` reruns dependents at generation 2 and leaves generation 1 attempts in the table.
