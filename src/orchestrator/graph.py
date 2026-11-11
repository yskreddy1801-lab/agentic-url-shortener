"""Explicit stage graph. Dependencies are data, not an implied script order."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageSpec:
    id: str
    handler: str
    depends_on: tuple[str, ...] = ()
    entry_gates: tuple[str, ...] = ()
    exit_gates: tuple[str, ...] = ()
    fallback_exit_gates: tuple[str, ...] = ()
    max_attempts: int = 2
    fallback_handler: str | None = None
    requires_approval: bool = False
    approval_prompt: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


class Graph:
    def __init__(self, stages: list[StageSpec]):
        self.order: tuple[str, ...] = tuple(stage.id for stage in stages)
        self.stages: dict[str, StageSpec] = {stage.id: stage for stage in stages}
        if len(self.stages) != len(self.order):
            raise ValueError("stage ids must be unique")
        self._validate()

    def stage(self, stage_id: str) -> StageSpec:
        return self.stages[stage_id]

    def stages_in_order(self) -> list[StageSpec]:
        return [self.stages[stage_id] for stage_id in self.order]

    def dependents_transitive(self, stage_id: str) -> list[str]:
        children: dict[str, list[str]] = {sid: [] for sid in self.order}
        for spec in self.stages.values():
            for dep in spec.depends_on:
                children[dep].append(spec.id)
        seen: set[str] = set()
        stack = list(children[stage_id])
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(children[current])
        return [sid for sid in self.order if sid in seen]

    def _validate(self) -> None:
        for spec in self.stages.values():
            for dep in spec.depends_on:
                if dep not in self.stages:
                    raise ValueError(f"{spec.id} depends on unknown stage {dep}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(stage_id: str) -> None:
            if stage_id in visiting:
                raise ValueError(f"cycle detected at {stage_id}")
            if stage_id in visited:
                return
            visiting.add(stage_id)
            for dep in self.stages[stage_id].depends_on:
                walk(dep)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in self.order:
            walk(stage_id)


def default_sdlc_graph() -> Graph:
    """Requirements, then design, then implementation.

    Testing, documentation, and security review fan out together.
    Release readiness waits for all three and asks for approval first.
    """
    return Graph(
        [
            StageSpec(
                id="requirements",
                handler="requirements",
                entry_gates=("brief_present",),
                exit_gates=("artifact:normalized_requirements", "ambiguity_resolved"),
                max_attempts=2,
            ),
            StageSpec(
                id="planning",
                handler="planning",
                depends_on=("requirements",),
                entry_gates=("artifact:normalized_requirements",),
                exit_gates=("artifact:task_plan",),
                max_attempts=2,
            ),
            StageSpec(
                id="architecture",
                handler="architecture",
                depends_on=("planning",),
                entry_gates=("artifact:normalized_requirements", "artifact:task_plan"),
                exit_gates=("artifact:design",),
                max_attempts=2,
            ),
            StageSpec(
                id="implementation",
                handler="implementation",
                depends_on=("architecture",),
                entry_gates=("artifact:design",),
                exit_gates=("artifact:implementation_report",),
                max_attempts=2,
            ),
            StageSpec(
                id="apply_patch",
                handler="apply_patch",
                depends_on=("implementation",),
                entry_gates=("artifact:implementation_report",),
                exit_gates=("artifact:apply_report",),
                max_attempts=1,
            ),
            StageSpec(
                id="testing",
                handler="testing",
                depends_on=("apply_patch",),
                entry_gates=("artifact:apply_report",),
                exit_gates=("artifact:test_report", "tests_green"),
                fallback_handler="testing_fallback",
                fallback_exit_gates=("artifact:test_report",),
                max_attempts=2,
            ),
            StageSpec(
                id="documentation",
                handler="documentation",
                depends_on=("apply_patch",),
                entry_gates=("artifact:apply_report",),
                exit_gates=("artifact:docs_snippet",),
                max_attempts=2,
            ),
            StageSpec(
                id="security_review",
                handler="security_review",
                depends_on=("apply_patch",),
                entry_gates=("artifact:apply_report",),
                exit_gates=("artifact:security_review", "policy_clear", "compliance_controls"),
                max_attempts=2,
            ),
            StageSpec(
                id="release_readiness",
                handler="release_readiness",
                depends_on=("testing", "documentation", "security_review"),
                entry_gates=(
                    "artifact:test_report",
                    "artifact:docs_snippet",
                    "artifact:security_review",
                ),
                exit_gates=("artifact:release_report", "policy_clear", "compliance_controls"),
                requires_approval=True,
                approval_prompt=(
                    "Release readiness is a high-impact checkpoint. "
                    "Approve it to publish the release report."
                ),
                max_attempts=2,
            ),
        ]
    )
