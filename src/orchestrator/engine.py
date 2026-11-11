"""DAG scheduler with gates, bounded retries, approval, rollback, and re-plan."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable

from orchestrator.ambiguity import choose_by_rubric
from orchestrator.context import RunContext
from orchestrator.planner import mark_tasks
from orchestrator.errors import GateFailure, OrchestratorError, PolicyViolation, StageFailed, WorkflowPaused
from orchestrator.gates import resolve_gate
from orchestrator.graph import Graph, StageSpec
from orchestrator.store import Store

Handler = Callable[[RunContext], None]
TERMINAL = {"succeeded", "failed", "stopped", "rejected", "rolled_back"}
SUCCESS = {"succeeded", "succeeded_with_fallback"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Engine:
    def __init__(self, store: Store, graph: Graph, handlers: dict[str, Handler], repo_root):
        self.store = store
        self.graph = graph
        self.handlers = handlers
        self.repo_root = repo_root
        self._guard = threading.Lock()
        self._exec_locks: dict[str, threading.Lock] = {}

    def _lock(self, run_id: str) -> threading.Lock:
        with self._guard:
            return self._exec_locks.setdefault(run_id, threading.Lock())

    def _executing(self, run_id: str) -> bool:
        lock = self._exec_locks.get(run_id)
        return lock is not None and lock.locked()

    def start(self, *, scenario: str, mode: str, brief: str, config: dict | None = None) -> str:
        if mode not in {"demo", "supervised"}:
            raise OrchestratorError(f"Unknown mode '{mode}'", status_code=400)
        run_id = "run_" + uuid.uuid4().hex[:12]
        now = _now()
        self.store.create_run(
            run_id=run_id,
            scenario=scenario,
            mode=mode,
            brief=brief,
            config=config or {},
            status="running",
            created_at=now,
            started_at=now,
        )
        for spec in self.graph.stages_in_order():
            self.store.init_stage(run_id, spec.id)
        self.store.add_event(run_id, "run.started", None, {"scenario": scenario, "mode": mode})
        self.checkpoint(run_id, "init")
        return run_id

    def execute(self, run_id: str) -> dict:
        lock = self._lock(run_id)
        if not lock.acquire(blocking=False):
            raise OrchestratorError("Run is already executing")
        try:
            self._loop(run_id)
        finally:
            lock.release()
        return self.store.get_run(run_id)

    def _loop(self, run_id: str) -> None:
        while True:
            run = self.store.get_run(run_id)
            if run["status"] in TERMINAL:
                return
            if run["stop_requested"]:
                self._safe_stop(run_id)
                return
            if run["status"] == "paused":
                return
            ready = self._ready(run_id)
            if not ready:
                self._finalize(run_id)
                return
            runnable: list[StageSpec] = []
            for spec in ready:
                if self.store.get_run(run_id)["stop_requested"]:
                    self._safe_stop(run_id)
                    return
                if not self._ensure_approval(run_id, spec):
                    return
                runnable.append(spec)
            self._dispatch(run_id, runnable)
            current = self.store.get_run(run_id)
            if current["status"] in TERMINAL or current["status"] == "paused":
                return
            if current["stop_requested"]:
                self._safe_stop(run_id)
                return

    def _dispatch(self, run_id: str, runnable: list[StageSpec]) -> None:
        if len(runnable) == 1:
            self._run_stage(run_id, runnable[0])
        else:
            with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
                futures = [pool.submit(self._run_stage, run_id, spec) for spec in runnable]
                wait(futures)
                for future in futures:
                    future.result()
        for spec in runnable:
            self._sync_tasks(run_id, spec.id)

    def _sync_tasks(self, run_id: str, stage_id: str) -> None:
        row = self.store.get_artifact(run_id, "task_plan")
        if row is None:
            return
        state = self.store.get_stages(run_id).get(stage_id) or {}
        if state.get("status") in SUCCESS:
            target = "succeeded"
        elif state.get("status") == "failed":
            target = "failed"
        else:
            return
        plan = row["content"]
        if mark_tasks(plan, stage_id, target):
            self.write_artifact(run_id, stage_id, "task_plan", plan)

    def _ready(self, run_id: str) -> list[StageSpec]:
        states = self.store.get_stages(run_id)
        ready = []
        for spec in self.graph.stages_in_order():
            state = states[spec.id]
            if state["status"] != "pending":
                continue
            if all(states[dep]["status"] in SUCCESS for dep in spec.depends_on):
                ready.append(spec)
        return ready

    def _finalize(self, run_id: str) -> None:
        states = self.store.get_stages(run_id)
        statuses = [stage["status"] for stage in states.values()]
        now = _now()
        if any(status == "failed" for status in statuses):
            failed = [stage_id for stage_id, stage in states.items() if stage["status"] == "failed"]
            self.store.update_run(run_id, status="failed", finished_at=now)
            self.store.add_event(run_id, "run.failed", None, {"failed_stages": failed})
            return
        if statuses and all(status in SUCCESS for status in statuses):
            self.store.update_run(run_id, status="succeeded", finished_at=now)
            self.store.add_event(run_id, "run.completed", None, {})
            return
        pending = [stage_id for stage_id, stage in states.items() if stage["status"] == "pending"]
        self.store.update_run(run_id, status="failed", finished_at=now)
        self.store.add_event(run_id, "run.failed", None, {"reason": "graph_blocked", "pending": pending})

    def _ensure_approval(self, run_id: str, spec: StageSpec) -> bool:
        if not spec.requires_approval:
            return True
        if self.store.has_valid_approval(run_id, spec.id):
            return True
        self.checkpoint(run_id, f"pre_approval:{spec.id}")
        prompt = spec.approval_prompt or f"Approve stage {spec.id} before it runs."
        self.store.add_event(run_id, "approval.requested", spec.id, {"kind": "approval", "prompt": prompt})
        run = self.store.get_run(run_id)
        if run["mode"] == "demo":
            decision = self.store.add_decision(
                run_id=run_id,
                stage_id=spec.id,
                kind="approval",
                question=prompt,
                alternatives=[{"id": "approved", "title": "Approve"}, {"id": "rejected", "title": "Reject"}],
                selected="approved",
                actor="demo_policy",
                rationale=(
                    "Demo mode records the pre-approval checkpoint and then auto-approves "
                    "so a CLI run can finish. This is not a human release sign-off."
                ),
                rule="demo_auto_approve",
                payload={"accept_breaking": False, "note": "demo auto-approve"},
            )
            self.store.add_event(
                run_id,
                "approval.granted",
                spec.id,
                {"actor": "demo_policy", "decision_id": decision["id"], "accept_breaking": False},
            )
            return True
        self.store.update_run(
            run_id,
            status="paused",
            pause_stage=spec.id,
            pause_kind="approval",
            pause_details={"question": prompt, "stage_id": spec.id},
        )
        self.store.add_event(run_id, "run.paused", spec.id, {"kind": "approval"})
        return False

    def request_approval(self, run_id: str, stage_id: str, prompt: str) -> dict:
        existing = self.store.find_decision(run_id, stage_id, "approval")
        if existing and existing["selected"] == "approved":
            return existing
        self.checkpoint(run_id, f"pre_approval:{stage_id}")
        self.store.add_event(run_id, "approval.requested", stage_id, {"kind": "approval", "prompt": prompt})
        run = self.store.get_run(run_id)
        if run["mode"] == "demo":
            decision = self.store.add_decision(
                run_id=run_id,
                stage_id=stage_id,
                kind="approval",
                question=prompt,
                alternatives=[{"id": "approved", "title": "Approve"}, {"id": "rejected", "title": "Reject"}],
                selected="approved",
                actor="demo_policy",
                rationale=(
                    "Demo mode records the pre-apply checkpoint and then auto-approves "
                    "so the generated patch can be written into the run workspace. "
                    "This is not a human sign-off."
                ),
                rule="demo_auto_approve",
                payload={"accept_breaking": False, "note": "demo auto-approve"},
            )
            self.store.add_event(
                run_id,
                "approval.granted",
                stage_id,
                {"actor": "demo_policy", "decision_id": decision["id"], "accept_breaking": False},
            )
            return decision
        self.store.update_run(
            run_id,
            status="paused",
            pause_stage=stage_id,
            pause_kind="approval",
            pause_details={"question": prompt, "stage_id": stage_id},
        )
        self.store.add_event(run_id, "run.paused", stage_id, {"kind": "approval"})
        raise WorkflowPaused("approval", prompt)

    def _run_stage(self, run_id: str, spec: StageSpec) -> None:
        try:
            self._run_stage_body(run_id, spec)
        except Exception as exc:
            self.store.set_stage(run_id, spec.id, status="failed", error=str(exc), finished_at=_now())
            self.store.add_event(run_id, "stage.failed", spec.id, {"error": str(exc), "unexpected": True})

    def _run_stage_body(self, run_id: str, spec: StageSpec) -> None:
        if self.store.get_run(run_id)["stop_requested"]:
            return
        self.store.set_stage(run_id, spec.id, status="running", error=None, started_at=_now())
        failures = 0
        last_error: Exception | None = None
        while failures < spec.max_attempts:
            attempt = failures + 1
            self.store.set_stage(run_id, spec.id, attempt=attempt)
            attempt_id = self.store.open_attempt(run_id, spec.id, attempt)
            event_type = "stage.started" if failures == 0 else "stage.retry"
            self.store.add_event(run_id, event_type, spec.id, {"attempt": attempt})
            ctx = RunContext(self, run_id, spec.id)
            try:
                self._run_gates(ctx, spec.entry_gates, "entry")
                self._call_handler(spec.handler, ctx)
                self._run_gates(ctx, spec.exit_gates, "exit")
                self.store.close_attempt(attempt_id, "succeeded", None)
                self.store.set_stage(
                    run_id,
                    spec.id,
                    status="succeeded",
                    finished_at=_now(),
                    error=None,
                    fallback_used=False,
                )
                self.store.add_event(run_id, "stage.succeeded", spec.id, {"attempt": attempt, "fallback": False})
                self.checkpoint(run_id, f"after:{spec.id}")
                return
            except WorkflowPaused:
                self.store.close_attempt(attempt_id, "paused", None)
                self.store.set_stage(run_id, spec.id, status="pending", finished_at=None)
                return
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", True))
                if isinstance(exc, PolicyViolation):
                    retryable = False
                    self.store.add_event(
                        run_id,
                        "policy.violation",
                        spec.id,
                        {"message": str(exc), "findings": getattr(exc, "findings", [])},
                    )
                last_error = exc
                self.store.close_attempt(attempt_id, "failed", str(exc))
                self.store.add_event(
                    run_id,
                    "stage.attempt_failed",
                    spec.id,
                    {
                        "attempt": attempt,
                        "error": str(exc),
                        "retryable": retryable,
                        "error_type": type(exc).__name__,
                    },
                )
                if not retryable:
                    break
                failures += 1
        if spec.fallback_handler and not isinstance(last_error, PolicyViolation):
            self.store.add_event(run_id, "stage.fallback", spec.id, {"handler": spec.fallback_handler})
            ctx = RunContext(self, run_id, spec.id)
            try:
                self._call_handler(spec.fallback_handler, ctx)
                self._run_gates(ctx, spec.fallback_exit_gates, "exit")
            except Exception as exc:
                last_error = exc
            else:
                self.store.set_stage(
                    run_id,
                    spec.id,
                    status="succeeded_with_fallback",
                    finished_at=_now(),
                    fallback_used=True,
                    error=None,
                )
                self.store.add_event(run_id, "stage.succeeded", spec.id, {"fallback": True})
                self.checkpoint(run_id, f"after:{spec.id}:fallback")
                return
        self.store.set_stage(
            run_id,
            spec.id,
            status="failed",
            finished_at=_now(),
            error=str(last_error) if last_error else "stage failed",
        )
        self.store.add_event(
            run_id,
            "stage.failed",
            spec.id,
            {"error": str(last_error) if last_error else "stage failed"},
        )

    def _call_handler(self, name: str, ctx: RunContext) -> None:
        try:
            handler = self.handlers[name]
        except KeyError as exc:
            raise StageFailed(f"No handler registered for '{name}'", retryable=False) from exc
        handler(ctx)

    def _run_gates(self, ctx: RunContext, names: tuple[str, ...], phase: str) -> None:
        for name in names:
            try:
                resolve_gate(name)(ctx)
            except GateFailure as exc:
                if phase == "entry":
                    exc.retryable = False
                self.store.add_event(
                    ctx.run_id,
                    "gate.failed",
                    ctx.stage_id,
                    {"gate": name, "phase": phase, "message": str(exc), "retryable": exc.retryable},
                )
                raise
            except PolicyViolation as exc:
                self.store.add_event(
                    ctx.run_id,
                    "gate.failed",
                    ctx.stage_id,
                    {"gate": name, "phase": phase, "message": str(exc), "retryable": False},
                )
                raise
            self.store.add_event(ctx.run_id, "gate.passed", ctx.stage_id, {"gate": name, "phase": phase})

    def checkpoint(self, run_id: str, label: str) -> dict:
        row = self.store.add_checkpoint(run_id, label, self.store.snapshot(run_id))
        self.store.add_event(run_id, "checkpoint.created", None, {"checkpoint_id": row["id"], "label": label})
        return row

    def write_artifact(self, run_id: str, stage_id: str, name: str, content, content_type: str = "json") -> dict:
        row = self.store.write_artifact(run_id, name, content, content_type, stage_id)
        self.store.add_event(
            run_id,
            "artifact.written",
            stage_id,
            {"name": name, "version": row["version"], "hash": row["content_hash"]},
        )
        return row

    def resolve_choice(self, run_id: str, stage_id: str, question: str, alternatives: list[dict], kind: str) -> dict:
        existing = self.store.find_decision(run_id, stage_id, kind)
        if existing:
            return existing
        run = self.store.get_run(run_id)
        human = run.get("human_input") or {}
        if human.get("selected_alternative"):
            selected = human["selected_alternative"]
            known = {item["id"] for item in alternatives}
            if selected not in known:
                raise StageFailed(
                    f"Selected alternative '{selected}' is not one of: {sorted(known)}",
                    retryable=False,
                )
            decision = self.store.add_decision(
                run_id=run_id,
                stage_id=stage_id,
                kind=kind,
                question=question,
                alternatives=alternatives,
                selected=selected,
                actor="human",
                rationale=human.get("note") or "Human selected this interpretation.",
                rule="human_selection",
                payload={"note": human.get("note") or ""},
            )
            self.store.update_run(run_id, human_input=None)
            self.store.add_event(
                run_id,
                "decision.recorded",
                stage_id,
                {"decision_id": decision["id"], "selected": selected, "actor": "human", "kind": kind},
            )
            return decision
        self.checkpoint(run_id, f"pre_decision:{stage_id}")
        summary = [
            {
                "id": item["id"],
                "title": item.get("title"),
                "summary": item.get("summary"),
                "score": item.get("score"),
                "breaking": item.get("breaking"),
                "risk": item.get("risk"),
            }
            for item in alternatives
        ]
        self.store.add_event(
            run_id,
            "decision.requested",
            stage_id,
            {"kind": kind, "question": question, "alternatives": summary},
        )
        if run["mode"] == "demo":
            choice = choose_by_rubric(alternatives)
            decision = self.store.add_decision(
                run_id=run_id,
                stage_id=stage_id,
                kind=kind,
                question=question,
                alternatives=alternatives,
                selected=choice["selected"],
                actor="demo_policy",
                rationale=choice["rationale"],
                rule=choice["rule"],
                payload={},
            )
            self.store.add_event(
                run_id,
                "decision.recorded",
                stage_id,
                {
                    "decision_id": decision["id"],
                    "selected": choice["selected"],
                    "actor": "demo_policy",
                    "kind": kind,
                    "rule": choice["rule"],
                },
            )
            return decision
        self.store.update_run(
            run_id,
            status="paused",
            pause_stage=stage_id,
            pause_kind="decision",
            pause_details={"question": question, "alternatives": summary, "stage_id": stage_id},
        )
        self.store.add_event(run_id, "run.paused", stage_id, {"kind": "decision"})
        raise WorkflowPaused("decision", question, {"alternatives": summary})

    def approve(
        self,
        run_id: str,
        stage_id: str,
        note: str = "",
        selected_alternative: str | None = None,
        accept_breaking: bool = False,
        actor: str = "human",
    ) -> dict:
        run = self.store.get_run(run_id)
        if run["status"] != "paused" or run["pause_stage"] != stage_id:
            raise OrchestratorError("Run is not paused on that stage")
        if run["pause_kind"] == "decision":
            if not selected_alternative:
                raise OrchestratorError(
                    "selected_alternative is required for a decision checkpoint",
                    status_code=400,
                )
            self.store.update_run(
                run_id,
                human_input={"selected_alternative": selected_alternative, "note": note},
                status="running",
                pause_stage=None,
                pause_kind=None,
                pause_details=None,
            )
            self.store.add_event(
                run_id,
                "run.resumed",
                stage_id,
                {"selected_alternative": selected_alternative, "actor": actor},
            )
            return self.execute(run_id)
        if run["pause_kind"] == "approval":
            decision = self.store.add_decision(
                run_id=run_id,
                stage_id=stage_id,
                kind="approval",
                question=f"Approve stage {stage_id}",
                alternatives=[{"id": "approved"}, {"id": "rejected"}],
                selected="approved",
                actor=actor,
                rationale=note or "Human approved the checkpoint.",
                rule="human_approval",
                payload={"note": note, "accept_breaking": accept_breaking},
            )
            self.store.add_event(
                run_id,
                "approval.granted",
                stage_id,
                {"actor": actor, "decision_id": decision["id"], "accept_breaking": accept_breaking},
            )
            self.store.update_run(run_id, status="running", pause_stage=None, pause_kind=None, pause_details=None)
            self.store.add_event(run_id, "run.resumed", stage_id, {"actor": actor})
            return self.execute(run_id)
        raise OrchestratorError("Unknown pause kind")

    def reject(self, run_id: str, stage_id: str, note: str = "", actor: str = "human") -> dict:
        run = self.store.get_run(run_id)
        if run["status"] != "paused" or run["pause_stage"] != stage_id:
            raise OrchestratorError("Run is not paused on that stage")
        kind = "approval" if run["pause_kind"] == "approval" else "ambiguity"
        decision = self.store.add_decision(
            run_id=run_id,
            stage_id=stage_id,
            kind=kind,
            question=f"Checkpoint for {stage_id}",
            alternatives=[{"id": "approved"}, {"id": "rejected"}],
            selected="rejected",
            actor=actor,
            rationale=note or "Human rejected the checkpoint.",
            rule="human_rejection",
            payload={"note": note},
        )
        event_type = "approval.rejected" if kind == "approval" else "decision.rejected"
        self.store.add_event(run_id, event_type, stage_id, {"actor": actor, "decision_id": decision["id"], "note": note})
        self.store.set_stage(run_id, stage_id, status="failed", error=note or "rejected", finished_at=_now())
        self.store.update_run(
            run_id,
            status="rejected",
            finished_at=_now(),
            pause_stage=None,
            pause_kind=None,
            pause_details=None,
        )
        self.store.add_event(run_id, "run.rejected", stage_id, {"note": note})
        return self.store.get_run(run_id)

    def stop(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run["status"] in TERMINAL:
            raise OrchestratorError("Run is already terminal")
        self.store.update_run(run_id, stop_requested=True)
        self.store.add_event(run_id, "run.stop_requested", None, {})
        if run["status"] == "paused" or not self._executing(run_id):
            self._safe_stop(run_id)
        return self.store.get_run(run_id)

    def _safe_stop(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run["status"] == "stopped":
            return
        self.store.update_run(run_id, status="stopped", finished_at=_now(), stop_requested=True)
        self.store.add_event(
            run_id,
            "run.stopped",
            None,
            {"reason": "safe-stop: an in-flight stage may finish; no further stage starts"},
        )

    def rollback(self, run_id: str, checkpoint_id: str | None = None) -> dict:
        run = self.store.get_run(run_id)
        if run["status"] == "running" or self._executing(run_id):
            raise OrchestratorError("Stop the run before rolling back")
        checkpoints = self.store.list_checkpoints(run_id)
        if not checkpoints:
            raise OrchestratorError("No checkpoint to roll back to")
        if checkpoint_id is None:
            if run["status"] == "failed" or len(checkpoints) == 1:
                target = checkpoints[-1]
            else:
                target = checkpoints[-2]
        else:
            target = self.store.get_checkpoint(run_id, checkpoint_id)
            if target is None:
                raise OrchestratorError("Checkpoint not found", status_code=404)
        snapshot = target["snapshot"]
        self.store.restore_artifacts(run_id, snapshot.get("artifacts", {}))
        states = self.store.get_stages(run_id)
        rolled = []
        for stage_id, state in states.items():
            snap_status = snapshot.get("stages", {}).get(stage_id, {}).get("status", "pending")
            if snap_status in SUCCESS:
                continue
            if state["status"] in SUCCESS or state["status"] in {"failed", "running"}:
                self.store.set_stage(run_id, stage_id, status="rolled_back")
                self.store.add_event(run_id, "stage.rolled_back", stage_id, {"checkpoint_id": target["id"]})
                rolled.append(stage_id)
        self.store.update_run(run_id, status="rolled_back", finished_at=_now(), pause_stage=None, pause_kind=None)
        self.store.add_event(
            run_id,
            "run.rolled_back",
            None,
            {"checkpoint_id": target["id"], "label": target["label"], "stages": rolled},
        )
        return self.store.get_run(run_id)

    def replan(self, run_id: str, artifact_name: str, new_content: Any = None, reason: str = "") -> dict:
        if self._executing(run_id) or self.store.get_run(run_id)["status"] == "running":
            raise OrchestratorError("Cannot re-plan a run that is executing")
        current = self.store.get_artifact(run_id, artifact_name)
        if current is None and new_content is None:
            raise OrchestratorError(f"Artifact '{artifact_name}' does not exist", status_code=404)
        owner = current["stage_id"] if current else "replan"
        if new_content is not None:
            written = self.write_artifact(run_id, owner, artifact_name, new_content, "json")
            new_version = written["version"]
        else:
            new_version = current["version"]
        downstream = self.graph.dependents_transitive(owner)
        for stage_id in downstream:
            self.store.invalidate_stage(run_id, stage_id)
            self.store.invalidate_decisions(run_id, stage_id)
            self.store.add_event(
                run_id,
                "stage.invalidated",
                stage_id,
                {"reason": reason or "upstream artifact changed", "artifact": artifact_name},
            )
        self.store.add_event(
            run_id,
            "replan.triggered",
            owner,
            {"artifact": artifact_name, "version": new_version, "downstream": downstream, "reason": reason},
        )
        self.store.update_run(
            run_id,
            status="running",
            finished_at=None,
            stop_requested=False,
            pause_stage=None,
            pause_kind=None,
            pause_details=None,
        )
        return self.execute(run_id)
