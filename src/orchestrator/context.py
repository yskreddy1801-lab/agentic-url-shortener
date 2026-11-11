"""Handle passed to a stage. Handlers read and write through this object."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.engine import Engine


class RunContext:
    def __init__(self, engine: "Engine", run_id: str, stage_id: str):
        self.engine = engine
        self.store = engine.store
        self.run_id = run_id
        self.stage_id = stage_id

    @property
    def run(self) -> dict:
        return self.store.get_run(self.run_id)

    @property
    def brief(self) -> str:
        return self.run["brief"]

    @property
    def mode(self) -> str:
        return self.run["mode"]

    @property
    def config(self) -> dict:
        return self.run["config"]

    @property
    def repo_root(self):
        return self.engine.repo_root

    def artifact(self, name: str) -> Any:
        row = self.store.get_artifact(self.run_id, name)
        if row is None:
            return None
        return row["content"]

    def write_artifact(self, name: str, content, content_type: str = "json") -> dict:
        return self.engine.write_artifact(self.run_id, self.stage_id, name, content, content_type)

    def all_artifacts(self) -> dict[str, Any]:
        return {row["name"]: row["content"] for row in self.store.list_current_artifacts(self.run_id)}

    def resolve_choice(self, *, question: str, alternatives: list[dict], kind: str = "ambiguity") -> dict:
        return self.engine.resolve_choice(self.run_id, self.stage_id, question, alternatives, kind)
