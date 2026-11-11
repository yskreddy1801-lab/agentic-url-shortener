"""Entry and exit gates. A gate failure is an explicit audit event, not a silent skip."""

from __future__ import annotations

from orchestrator.errors import GateFailure, PolicyViolation
from orchestrator.policies import scan_artifacts


def resolve_gate(name: str):
    if name.startswith("artifact:"):
        return _artifact_gate(name.split(":", 1)[1])
    try:
        return GATES[name]
    except KeyError as exc:
        raise GateFailure(name, f"Unknown gate '{name}'", retryable=False) from exc


def _artifact_gate(name: str):
    def gate(ctx) -> None:
        if ctx.artifact(name) is None:
            raise GateFailure(f"artifact:{name}", f"Required artifact '{name}' is missing", retryable=False)

    return gate


def gate_brief_present(ctx) -> None:
    if not (ctx.brief or "").strip():
        raise GateFailure("brief_present", "Brief is empty", retryable=False)


def gate_ambiguity_resolved(ctx) -> None:
    document = ctx.artifact("normalized_requirements")
    if not isinstance(document, dict):
        raise GateFailure("ambiguity_resolved", "Requirements artifact is missing", retryable=False)
    ambiguity = document.get("ambiguity") or {}
    if ambiguity.get("is_ambiguous") and not document.get("decision"):
        raise GateFailure(
            "ambiguity_resolved",
            "Ambiguous requirements have no recorded decision",
            retryable=False,
        )


def gate_tests_green(ctx) -> None:
    report = ctx.artifact("test_report") or {}
    if not report.get("passed"):
        raise GateFailure("tests_green", "Test report is missing or not passed", retryable=True)


def gate_policy_clear(ctx) -> None:
    findings = scan_artifacts(ctx.all_artifacts())
    blocking = [item for item in findings if item["severity"] == "block"]
    if blocking:
        raise PolicyViolation(blocking[0]["message"], findings=blocking)


GATES = {
    "brief_present": gate_brief_present,
    "ambiguity_resolved": gate_ambiguity_resolved,
    "tests_green": gate_tests_green,
    "policy_clear": gate_policy_clear,
}
