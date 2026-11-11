"""Stage handlers. Each one does deterministic work and writes an artifact."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from pathlib import Path

from orchestrator.codegen import generate_patch
from orchestrator.context import RunContext
from orchestrator.design_doc import build_design
from orchestrator.errors import PolicyViolation, StageFailed
from orchestrator.impact import analyze_planned_change, scan_repository
from orchestrator.normalize import normalize_brief
from orchestrator.planner import build_task_plan
from orchestrator.policies import evaluate_compliance, scan_artifacts
from orchestrator.verify_impl import verify_implementation
from orchestrator.workspace import apply_files, create_workspace, rollback_files

TEST_FILES = [
    "tests/test_validation.py",
    "tests/test_shortener_api.py",
    "tests/test_compliance.py",
]
CONTRACT_TOKENS = (
    "javascript",
    "alias_reserved",
    "Idempotency-Key",
    "rate_limited",
    "expired",
    "top_referrers",
    "clicks_per_day",
)


def build_handlers() -> dict:
    return {
        "requirements": handle_requirements,
        "planning": handle_planning,
        "architecture": handle_architecture,
        "implementation": handle_implementation,
        "apply_patch": handle_apply_patch,
        "testing": handle_testing,
        "testing_fallback": handle_testing_fallback,
        "documentation": handle_documentation,
        "security_review": handle_security_review,
        "release_readiness": handle_release_readiness,
    }


def handle_requirements(ctx: RunContext) -> None:
    document = normalize_brief(ctx.brief, kind=ctx.config.get("kind", "unspecified"))
    if document["ambiguity"]["is_ambiguous"]:
        decision = ctx.resolve_choice(
            question="The brief does not pin down a single engineering outcome. Choose an interpretation.",
            alternatives=document["ambiguity"]["interpretations"],
            kind="ambiguity",
        )
        selected = next(
            item for item in document["ambiguity"]["interpretations"] if item["id"] == decision["selected"]
        )
        document["decision"] = {
            "id": decision["id"],
            "selected": decision["selected"],
            "actor": decision["actor"],
            "rationale": decision["rationale"],
            "rule": decision["rule"],
        }
        document["selected_interpretation"] = selected["id"]
        document["breaking"] = bool(selected["breaking"])
        document["functional_requirements"] = [
            {
                "id": "FR-1",
                "statement": selected["statement"],
                "acceptance": list(selected["acceptance"]),
            }
        ]
        document["problem_statement"] = selected["summary"]
    ctx.write_artifact("normalized_requirements", document)


def handle_planning(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements")
    if not isinstance(requirements, dict):
        raise StageFailed("Normalized requirements are missing", retryable=False)
    ctx.write_artifact("task_plan", build_task_plan(requirements))


def handle_architecture(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements")
    if not isinstance(requirements, dict):
        raise StageFailed("Normalized requirements are missing", retryable=False)
    code_map = scan_repository(ctx.repo_root)
    design = build_design(requirements, code_map)
    if requirements.get("kind") == "brownfield":
        impact = analyze_planned_change(code_map, requirements)
        design["impact_analysis"] = impact
        design["markdown"] = design["markdown"] + _impact_markdown(impact)
        if not impact.get("verified_symbols"):
            missing = ", ".join(impact.get("missing_symbols") or [])
            raise StageFailed(f"Impact analysis could not find the base service: {missing}", retryable=False)
    ctx.write_artifact("design", design)


def handle_implementation(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements") or {}
    code_map = scan_repository(ctx.repo_root)
    report = verify_implementation(requirements, code_map)
    patch = generate_patch(requirements, report, ctx.repo_root)
    if patch is None and report.get("missing_routes"):
        raise StageFailed(
            "No code template for: " + "; ".join(report["gaps"]),
            retryable=False,
        )
    report["generated"] = bool(patch and patch.get("files"))
    report["patch_id"] = patch["id"] if patch else None
    ctx.write_artifact("implementation_report", report)
    if patch and patch.get("files"):
        ctx.write_artifact("code_patch", patch)
    if not report["verified"] and not report["generated"]:
        raise StageFailed("Implementation gaps: " + "; ".join(report["gaps"]), retryable=False)


def handle_apply_patch(ctx: RunContext) -> None:
    patch = ctx.artifact("code_patch") or {}
    files = patch.get("files") or []
    if files:
        ctx.request_approval(
            "Applying a generated patch is a high-impact action. "
            "Approve it to write the patch into this run's workspace copy. "
            "The main checkout is not modified."
        )
    workspace = create_workspace(ctx.repo_root, ctx.run_id)
    snapshot: list[dict] = []
    if files:
        snapshot = apply_files(workspace, files)
        (workspace / ".orch_snapshot.json").write_text(_snapshot_text(snapshot), encoding="utf-8")
    ctx.write_artifact(
        "apply_report",
        {
            "applied": bool(files),
            "workspace": str(workspace),
            "patch_id": patch.get("id"),
            "files": [item["path"] for item in files],
            "summary": patch.get("summary") or "No product patch was required.",
            "main_tree_modified": False,
        },
    )


def handle_testing(ctx: RunContext) -> None:
    apply_report = ctx.artifact("apply_report") or {}
    root = Path(apply_report["workspace"]) if apply_report.get("workspace") else ctx.repo_root
    files = list(TEST_FILES)
    generated = root / "tests" / "test_bulk_create.py"
    if generated.is_file():
        files.append("tests/test_bulk_create.py")
    command = [sys.executable, "-m", "pytest", *files, "-q", "--tb=line", "-p", "no:cacheprovider"]
    if any(token in " ".join(command) for token in ("test_scenarios", "test_orchestrator")):
        raise StageFailed("Refusing to run orchestrator tests from the testing stage", retryable=False)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PYTEST_"):
            env.pop(key)
    src = str(root / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    blob = ""
    for relative in TEST_FILES:
        blob += (root / relative).read_text(encoding="utf-8")
    missing = [token for token in CONTRACT_TOKENS if token not in blob]
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    report = {
        "command": command,
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "duration_ms": duration_ms,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "contract_tokens_missing": missing,
        "fallback": False,
        "note": "Executes the shortener suite in the run workspace, including a generated test when the patch added one.",
        "workspace": str(root),
        "patch_rolled_back": False,
    }
    if completed.returncode != 0 and apply_report.get("applied"):
        snapshot_path = root / ".orch_snapshot.json"
        if snapshot_path.is_file():
            rollback_files(root, json.loads(snapshot_path.read_text(encoding="utf-8")))
            report["patch_rolled_back"] = True
            report["note"] = "The suite failed. The applied patch was rolled back inside the run workspace."
    ctx.write_artifact("test_report", report)
    if completed.returncode != 0:
        raise StageFailed(f"pytest exited {completed.returncode}", retryable=not report["patch_rolled_back"])


def handle_testing_fallback(ctx: RunContext) -> None:
    previous = ctx.artifact("test_report") or {}
    ctx.write_artifact(
        "test_report",
        {
            "passed": False,
            "fallback": True,
            "exit_code": previous.get("exit_code"),
            "stdout_tail": previous.get("stdout_tail", ""),
            "stderr_tail": previous.get("stderr_tail", ""),
            "contract_tokens_missing": previous.get("contract_tokens_missing", []),
            "summary": (
                "Primary test execution failed after retries. "
                "Release readiness must not treat the suite as green."
            ),
        },
    )


def handle_documentation(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements") or {}
    design = ctx.artifact("design") or {}
    lines = [
        f"# {requirements.get('title', 'URL shortener')}",
        "",
        requirements.get("problem_statement", ""),
        "",
        "## What shipped in this scope",
        "",
    ]
    for item in requirements.get("functional_requirements") or []:
        lines.append(f"- {item['id']}: {item['statement']}")
    lines.extend(["", "## How to call it", "", "Create a link:", "", "```bash",
                  "curl -s -X POST http://127.0.0.1:8741/api/v1/urls \\",
                  "  -H 'Content-Type: application/json' \\",
                  "  -d '{\"url\":\"https://example.com/docs\"}'",
                  "```", ""])
    if design.get("apis"):
        lines.append("Routes traced to the repository:")
        lines.append("")
        for api in design["apis"]:
            lines.append(f"- `{api['method']} {api['path']}`")
        lines.append("")
    if requirements.get("decision"):
        decision = requirements["decision"]
        lines.extend(
            [
                "## Requirement decision",
                "",
                f"Selected `{decision['selected']}` by `{decision['actor']}` using rule `{decision['rule']}`.",
                "",
                decision["rationale"],
                "",
            ]
        )
    lines.append("Redirects use HTTP 302 and `Cache-Control: no-store`. Expired and disabled links return 410.")
    lines.append("")
    ctx.write_artifact("docs_snippet", {"title": requirements.get("title"), "markdown": "\n".join(lines)})


def handle_security_review(ctx: RunContext) -> None:
    artifacts = ctx.all_artifacts()
    findings = scan_artifacts(artifacts)
    design = ctx.artifact("design") or {}
    for change in design.get("breaking_changes") or []:
        findings.append(
            {
                "severity": "warn",
                "policy": "breaking_change_control",
                "artifact": "design",
                "message": change["description"],
            }
        )
    compliance_root = Path((ctx.artifact("apply_report") or {}).get("workspace") or ctx.repo_root)
    compliance = evaluate_compliance(compliance_root)
    findings.extend(compliance["findings"])
    findings.append(
        {
            "severity": "info",
            "policy": "data_classification",
            "message": compliance["classification"]["note"]
            if "note" in compliance["classification"]
            else compliance["classification"]["referrer_rule"],
            "classification": compliance["classification"],
        }
    )
    findings.extend(
        [
            {
                "severity": "info",
                "policy": "open_redirect_is_the_product",
                "message": (
                    "Redirecting a browser to a caller-supplied http(s) URL is the product. "
                    "The javascript scheme, data, file, and embedded credentials are rejected at create time."
                ),
            },
            {
                "severity": "info",
                "policy": "unauthenticated_capabilities",
                "message": (
                    "Anyone who knows a code can read stats and disable the link. "
                    "Generated codes are high entropy. Custom aliases are not a security boundary."
                ),
            },
        ]
    )
    blocking = any(item["severity"] == "block" for item in findings)
    ctx.write_artifact(
        "security_review",
        {
            "findings": findings,
            "blocking": blocking,
            "breaking_changes": design.get("breaking_changes") or [],
        },
    )
    if blocking:
        first = next(item for item in findings if item["severity"] == "block")
        raise PolicyViolation(first["message"], findings=[item for item in findings if item["severity"] == "block"])


def handle_release_readiness(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements") or {}
    design = ctx.artifact("design") or {}
    implementation = ctx.artifact("implementation_report") or {}
    tests = ctx.artifact("test_report") or {}
    docs = ctx.artifact("docs_snippet") or {}
    security = ctx.artifact("security_review") or {}
    decisions = ctx.store.list_decisions(ctx.run_id)
    approval = next(
        (
            item
            for item in reversed(decisions)
            if item["kind"] == "approval"
            and item["stage_id"] == "release_readiness"
            and not item["invalidated"]
            and item["selected"] == "approved"
        ),
        None,
    )
    checklist: list[dict] = []

    def add(item: str, status: str, evidence: str = "", note: str = "") -> None:
        checklist.append({"item": item, "status": status, "evidence": evidence, "note": note})

    add("Requirements normalized", "pass" if requirements else "fail", "normalized_requirements")
    if (requirements.get("ambiguity") or {}).get("is_ambiguous"):
        decision = requirements.get("decision") or {}
        add(
            "Ambiguity resolved with a recorded decision",
            "pass" if decision else "fail",
            decision.get("id", ""),
            decision.get("rationale", ""),
        )
    plan = ctx.artifact("task_plan") or {}
    earlier = [
        task
        for task in plan.get("tasks") or []
        if task.get("owning_stage") != "release_readiness" and task.get("status") != "succeeded"
    ]
    add(
        "Task plan executed for this brief",
        "pass" if plan.get("tasks") and not earlier else "fail",
        "task_plan",
        f"{len(plan.get('tasks') or [])} tasks",
    )
    add("Architecture and design written", "pass" if design else "fail", "design")
    if requirements.get("kind") == "brownfield":
        impact = design.get("impact_analysis") or {}
        add(
            "Brownfield impact analysis verified against the source tree",
            "pass" if impact.get("verified_symbols") else "fail",
            "design.impact_analysis",
        )
    apply_report = ctx.artifact("apply_report") or {}
    if implementation.get("generated"):
        add(
            "Generated patch applied in the run workspace",
            "pass" if apply_report.get("applied") else "fail",
            "code_patch",
            apply_report.get("summary", ""),
        )
    else:
        add(
            "Implementation mapped onto the repository",
            "pass" if implementation.get("verified") else "fail",
            "implementation_report",
        )
    if tests.get("patch_rolled_back"):
        add("Behavioral test suite passed", "fail", "test_report", "The generated patch was rolled back after the suite failed.")
    elif tests.get("fallback"):
        add("Behavioral test suite passed", "fail", "test_report", "Fallback report only; the suite did not pass.")
    else:
        add("Behavioral test suite passed", "pass" if tests.get("passed") else "fail", "test_report")
    missing = tests.get("contract_tokens_missing") or []
    add(
        "Shortener contract tokens present in the test suite",
        "pass" if not missing else "warn",
        ",".join(missing),
    )
    add("Documentation snippet produced", "pass" if docs else "fail", "docs_snippet")
    add(
        "Security policy clear of blocking findings",
        "pass" if security and not security.get("blocking") else "fail",
        "security_review",
    )
    compliance_ok = not any(
        item.get("severity") == "block" and item.get("policy") in {
            "referrer_sanitized",
            "referrer_used_on_write",
            "retention_purge",
            "purge_endpoint",
            "retention_window",
        }
        for item in security.get("findings") or []
    )
    add(
        "Compliance controls: retention, purge, and referrer sanitizing",
        "pass" if security and compliance_ok else "fail",
        "security_review",
    )
    breaking = design.get("breaking_changes") or []
    if breaking:
        accepted = bool(approval and (approval.get("payload") or {}).get("accept_breaking"))
        add(
            "Breaking change explicitly accepted",
            "pass" if accepted else "fail",
            approval["id"] if approval else "",
            "Breaking changes require accept_breaking on the approval.",
        )
    else:
        add("Breaking-change control", "pass", "", "No breaking changes were declared.")
    if approval is None:
        add("Release checkpoint approved", "fail", "", "No approval decision is recorded.")
    else:
        note = approval.get("rationale") or ""
        if approval["actor"] == "demo_policy":
            note = (
                "Approved by demo_policy after the pre-approval checkpoint. "
                "Supervised mode would wait for a person. "
                + note
            )
        add("Release checkpoint approved", "pass", approval["id"], note)

    ready = not any(item["status"] == "fail" for item in checklist)
    production_gaps = [
        "SQLite and the in-memory rate limiter are process-local.",
        "Create, stats, and delete are unauthenticated. A custom alias is a guessable capability.",
        "The orchestrator runs in-process. It is not an isolated worker fleet.",
        "Generated patches are applied only inside a run workspace. They are not merged to the main checkout.",
        "Demo approval is recorded in the audit log. It is not a production change-management control.",
        "Referrer retention is a fixed day window in one SQLite file, not a per-tenant policy.",
    ]
    report = {
        "ready": ready,
        "recommendation": "accept_for_prototype_with_known_limits" if ready else "hold",
        "approval_mode": ctx.mode,
        "checklist": checklist,
        "production_gaps": production_gaps,
        "scenario": ctx.run["scenario"],
        "kind": requirements.get("kind"),
        "selected_interpretation": requirements.get("selected_interpretation"),
    }
    ctx.write_artifact("release_report", report)
    ctx.write_artifact("release_checklist", _checklist_markdown(report), content_type="text")


def _checklist_markdown(report: dict) -> str:
    lines = [
        "# Release checklist",
        "",
        f"Recommendation: `{report['recommendation']}`",
        f"Ready for this prototype scope: `{str(report['ready']).lower()}`",
        f"Approval mode: `{report['approval_mode']}`",
        "",
        "| Item | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for item in report["checklist"]:
        evidence = item["evidence"] or ""
        lines.append(f"| {item['item']} | {item['status']} | {evidence} |")
    lines.extend(["", "## Notes", ""])
    for item in report["checklist"]:
        if item["note"]:
            lines.append(f"- **{item['item']}.** {item['note']}")
    lines.extend(["", "## Production gaps kept visible", ""])
    for gap in report["production_gaps"]:
        lines.append(f"- {gap}")
    lines.append("")
    return "\n".join(lines)


def _snapshot_text(snapshot: list[dict]) -> str:
    return json.dumps(snapshot)


def _impact_markdown(impact: dict) -> str:
    lines = ["", "## Planned change", ""]
    for item in impact.get("planned_additions") or []:
        state = "present" if item.get("present") else "not in the tree yet"
        lines.append(f"- `{item['method']} {item['path']}` ({state}) for {item.get('requirement_id')}")
    if impact.get("insertion_points"):
        lines.append("")
        lines.append("Insertion points: " + ", ".join(f"`{path}`" for path in impact["insertion_points"]))
    lines.append("")
    return "\n".join(lines)
