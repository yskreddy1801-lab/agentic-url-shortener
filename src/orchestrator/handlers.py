"""Stage handlers. Each one does deterministic work and writes an artifact."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from orchestrator.context import RunContext
from orchestrator.design_doc import build_design
from orchestrator.errors import PolicyViolation, StageFailed
from orchestrator.impact import scan_repository
from orchestrator.normalize import normalize_brief
from orchestrator.policies import scan_artifacts
from orchestrator.verify_impl import verify_implementation

TEST_FILES = ["tests/test_validation.py", "tests/test_shortener_api.py"]
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
        "architecture": handle_architecture,
        "implementation": handle_implementation,
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


def handle_architecture(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements")
    if not isinstance(requirements, dict):
        raise StageFailed("Normalized requirements are missing", retryable=False)
    code_map = scan_repository(ctx.repo_root)
    design = build_design(requirements, code_map)
    ctx.write_artifact("design", design)
    impact = design.get("impact_analysis")
    if requirements.get("kind") == "brownfield" and impact and not impact.get("verified_symbols"):
        missing = ", ".join(impact.get("missing_symbols") or [])
        raise StageFailed(f"Impact analysis could not find the implementation: {missing}", retryable=False)


def handle_implementation(ctx: RunContext) -> None:
    requirements = ctx.artifact("normalized_requirements") or {}
    code_map = scan_repository(ctx.repo_root)
    report = verify_implementation(requirements, code_map)
    ctx.write_artifact("implementation_report", report)
    if not report["verified"]:
        raise StageFailed("Implementation gaps: " + "; ".join(report["gaps"]), retryable=False)


def handle_testing(ctx: RunContext) -> None:
    command = [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "--tb=line", "-p", "no:cacheprovider"]
    if any(token in " ".join(command) for token in ("test_scenarios", "test_orchestrator")):
        raise StageFailed("Refusing to run orchestrator tests from the testing stage", retryable=False)
    root = ctx.repo_root
    env = os.environ.copy()
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
        "note": "Executes the repository shortener suite. Does not synthesize new tests from acceptance text.",
    }
    ctx.write_artifact("test_report", report)
    if completed.returncode != 0:
        raise StageFailed(f"pytest exited {completed.returncode}", retryable=True)


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
    add("Architecture and design written", "pass" if design else "fail", "design")
    if requirements.get("kind") == "brownfield":
        impact = design.get("impact_analysis") or {}
        add(
            "Brownfield impact analysis verified against the source tree",
            "pass" if impact.get("verified_symbols") else "fail",
            "design.impact_analysis",
        )
    add(
        "Implementation mapped onto the repository",
        "pass" if implementation.get("verified") else "fail",
        "implementation_report",
    )
    if tests.get("fallback"):
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
        "The implementation stage verifies the repository. It does not rewrite or merge code.",
        "The testing stage runs a fixed suite. It does not generate new tests from acceptance text.",
        "Demo approval is recorded in the audit log. It is not a production change-management control.",
        "Click analytics stores referrers with no retention window.",
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
