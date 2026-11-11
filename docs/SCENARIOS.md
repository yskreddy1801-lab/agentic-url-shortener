# Scenarios

All three scenarios use the same graph:

```
requirements -> planning -> architecture -> implementation -> apply_patch
apply_patch -> testing, documentation, security_review   (parallel)
those three -> release_readiness                         (barrier, approval first)
```

`planning` builds a task subgraph from that brief's functional requirements. Each FR gets design, implement, test, and document tasks with ids, inputs, outputs, an owning stage, and dependencies. Apply, security, and release are run-level tasks. The engine updates task status when the owning stage finishes. `task_plan.md`, `lineage.json`, and `metrics.json` all show the plan.

Demo mode is what `python -m orchestrator run-all --mode demo` executes. It records approval checkpoints and continues as `demo_policy`. Supervised mode pauses. A brownfield supervised run pauses at `apply_patch` before any generated file is written. An ambiguous supervised run pauses at `requirements` until a person picks an interpretation.

Patches are applied under `artifacts/workspaces/<run_id>/`. That directory is gitignored. Scenario runs do not modify `src/`.

Validation for every scenario:

- The implementation stage maps each FR to a route in the tree, or generates a patch when a template exists.
- The testing stage runs `tests/test_validation.py`, `tests/test_shortener_api.py`, and `tests/test_compliance.py` inside the workspace, plus `tests/test_bulk_create.py` when the patch created it.
- Security review blocks on secret-like artifacts, unsafe URL tokens, and missing compliance controls.
- Release readiness sets `ready` only when those checks pass and the approval row exists.

The latest demo runs each finish with stage success rate 1.0, zero retries, and `parallelism_max` 3.

## 1. Greenfield

**Input.** A structured brief for create, redirect, metadata, stats, delete, health, and readiness. The text is `orchestrator.scenarios.GREENFIELD_BRIEF`.

**Decomposition.** The normalizer emits FR-1 through FR-7. The task plan has 32 tasks (four per FR, plus plan, apply, security, and release). The ambiguity detector stays quiet.

**What the implementation stage generates.** Every product route already exists, so verification passes. The stage still writes `patch-greenfield-scaffold`: `sandbox/shortener_scaffold/contract.py` and a README. That scaffold lists the contract. It is not imported by the service. This is intentional: greenfield generation produces a reviewable diff without forking a second shortener over the one that already runs.

**Orchestration.** `apply_patch` auto-approves in demo mode and writes the scaffold into the run workspace. Testing runs the real suite there. Documentation and security review run in parallel with testing. Security checks compliance controls that live in the main service (retention, purge, referrer sanitizing). Release waits for all three and for its own approval. Demo runs record two approvals: apply, then release.

**Where to look.** `artifacts/scenarios/greenfield/code_patch.diff`, `task_plan.md`, `metrics.json`.

## 2. Brownfield

**Input.** The service already creates one link per call. The brief asks for `POST /api/v1/urls/bulk`, accepting 1 to 20 http(s) URLs and rejecting a javascript-scheme target. That route is not in the tree. Aliases and expiry are already shipped, so they are not this scenario.

**Decomposition.** One FR. The task plan has 8 tasks. `FR-1-implement` depends on `FR-1-design`. `FR-1-test` and `FR-1-document` depend on implement. `APPLY-1` depends on implement. `SEC-1` depends on the test task. `REL-1` depends on security and the document task.

**Impact analysis.** Architecture scans the real modules and confirms the base symbols (`create_link`, `resolve_redirect`, models, existing routes). `planned_additions` records `POST /api/v1/urls/bulk` as not present yet, with insertion points in the API, app wiring, service layer, and tests. The stage does not fail just because the new route is absent.

**What gets generated.** A deterministic template writes:

- `src/shortener/bulk.py` (`create_bulk`)
- `src/shortener/bulk_api.py` (`POST /api/v1/urls/bulk`)
- a two-line include in `app.py`
- `tests/test_bulk_create.py`

The unified diff is `code_patch.diff`. Applying it requires approval. Demo mode logs `demo_policy` and applies it in the workspace. The testing stage then runs the shortener suite plus the generated bulk test. A failing suite rolls the patch back inside the workspace and the release checklist does not go green. The main checkout never receives `bulk.py`.

**Where to look.** `artifacts/scenarios/brownfield/design.md` (planned change), `code_patch.diff`, `apply_report.json`, `task_plan.md`, `release_checklist.md`.

A supervised run of this scenario pauses at `apply_patch` with the patch already in the artifact and nothing written yet:

```bash
python -m orchestrator run brownfield --mode supervised
python -m orchestrator approve RUN_ID --stage apply_patch --note "Apply bulk create in the workspace"
```

## 3. Ambiguous

**Input.** The brief is `make links better`.

**Decomposition.** The detector flags vague language, no contract, and a very short brief. It attaches three scored interpretations (`analytics` 1.00, `aliases` 0.90, `auto_expire_all` 0.25). Demo mode records the checkpoint and selects `analytics` with rule `max_rubric_score_then_lexical_id`. The task plan then has a single FR for `GET /api/v1/urls/{code}/stats`. That route already exists, so no product patch is generated and apply is a no-op (`approval_count` is 1, the release checkpoint only).

Supervised mode pauses at requirements. Choosing `auto_expire_all` is allowed and then fails implementation, because that reading names no route the generator or the tree can satisfy.

**Where to look.** `artifacts/scenarios/ambiguous/lineage.json` and `task_plan.md`.

## Compliance on every run

Click referrers are classified as confidential telemetry. Query strings and fragments are stripped before insert. An email-bearing referrer is stored as `hashed:<sha256 prefix>`. `POST /api/v1/compliance/purge` deletes clicks older than `click_retention_days` (default 30, override with `CLICK_RETENTION_DAYS`). The security review fails the run if those controls are missing from the tree under test. The release checklist has an explicit compliance row.
