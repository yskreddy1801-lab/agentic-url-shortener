"""HTTP control plane for orchestration runs."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from orchestrator.errors import OrchestratorError
from orchestrator.metrics import compute_metrics
from orchestrator.scenarios import SCENARIOS

router = APIRouter(prefix="/api/v1")


class StartRunIn(BaseModel):
    scenario: str
    mode: str = "demo"
    brief: str | None = None


class ApproveIn(BaseModel):
    stage_id: str
    note: str = ""
    selected_alternative: str | None = None
    accept_breaking: bool = False


class RejectIn(BaseModel):
    stage_id: str
    note: str = ""


class RollbackIn(BaseModel):
    checkpoint_id: str | None = None


class ReplanIn(BaseModel):
    artifact: str
    content: dict | None = None
    reason: str = ""


class RunView(BaseModel):
    id: str
    scenario: str
    mode: str
    status: str
    pause_stage: str | None = None
    pause_kind: str | None = None
    pause_details: dict | None = None
    stages: list[dict] = Field(default_factory=list)
    artifacts: list[dict] = Field(default_factory=list)


def _engine(request: Request):
    return request.app.state.engine


def _view(engine, run_id: str) -> dict:
    run = engine.store.get_run(run_id)
    stages = [
        {"stage_id": stage_id, **stage}
        for stage_id, stage in engine.store.get_stages(run_id).items()
    ]
    artifacts = [
        {
            "name": row["name"],
            "version": row["version"],
            "hash": row["content_hash"],
            "stage_id": row["stage_id"],
        }
        for row in engine.store.list_current_artifacts(run_id)
    ]
    return {
        "id": run["id"],
        "scenario": run["scenario"],
        "mode": run["mode"],
        "status": run["status"],
        "pause_stage": run["pause_stage"],
        "pause_kind": run["pause_kind"],
        "pause_details": run["pause_details"],
        "stop_requested": run["stop_requested"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "stages": stages,
        "artifacts": artifacts,
    }


def _kick(engine, run_id: str, timeout: float = 1.0) -> None:
    thread = threading.Thread(target=engine.execute, args=(run_id,), name=f"orch-{run_id}", daemon=True)
    thread.start()
    thread.join(timeout=timeout)


@router.get("/runs")
def list_runs(request: Request, limit: int = 20):
    engine = _engine(request)
    runs = engine.store.list_runs(limit=max(1, min(limit, 100)))
    return [
        {
            "id": run["id"],
            "scenario": run["scenario"],
            "mode": run["mode"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        }
        for run in runs
    ]


@router.post("/runs", status_code=201)
def start_run(payload: StartRunIn, request: Request):
    scenario = SCENARIOS.get(payload.scenario)
    if scenario is None:
        raise OrchestratorError(f"Unknown scenario '{payload.scenario}'", status_code=400)
    if payload.mode not in {"demo", "supervised"}:
        raise OrchestratorError("mode must be demo or supervised", status_code=400)
    engine = _engine(request)
    run_id = engine.start(
        scenario=scenario.name,
        mode=payload.mode,
        brief=payload.brief if payload.brief is not None else scenario.brief,
        config={"kind": scenario.kind, "title": scenario.title},
    )
    _kick(engine, run_id)
    return _view(engine, run_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return _view(_engine(request), run_id)


@router.get("/runs/{run_id}/events")
def get_events(run_id: str, request: Request):
    engine = _engine(request)
    engine.store.get_run(run_id)
    return engine.store.list_events(run_id)


@router.get("/runs/{run_id}/lineage")
def get_lineage(run_id: str, request: Request):
    engine = _engine(request)
    engine.store.get_run(run_id)
    return {
        "run_id": run_id,
        "decisions": engine.store.list_decisions(run_id),
        "checkpoints": engine.store.list_checkpoints(run_id),
        "artifacts": engine.store.list_artifacts(run_id),
    }


@router.get("/runs/{run_id}/metrics")
def get_metrics(run_id: str, request: Request):
    engine = _engine(request)
    return compute_metrics(engine.store, run_id)


@router.get("/runs/{run_id}/artifacts/{name}")
def get_artifact(run_id: str, name: str, request: Request):
    row = _engine(request).store.get_artifact(run_id, name)
    if row is None:
        raise OrchestratorError(f"Artifact '{name}' not found", status_code=404)
    return row


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, payload: ApproveIn, request: Request):
    engine = _engine(request)
    engine.approve(
        run_id,
        payload.stage_id,
        note=payload.note,
        selected_alternative=payload.selected_alternative,
        accept_breaking=payload.accept_breaking,
    )
    return _view(engine, run_id)


@router.post("/runs/{run_id}/reject")
def reject_run(run_id: str, payload: RejectIn, request: Request):
    engine = _engine(request)
    engine.reject(run_id, payload.stage_id, note=payload.note)
    return _view(engine, run_id)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, request: Request):
    engine = _engine(request)
    engine.stop(run_id)
    return _view(engine, run_id)


@router.post("/runs/{run_id}/rollback")
def rollback_run(run_id: str, payload: RollbackIn, request: Request):
    engine = _engine(request)
    engine.rollback(run_id, payload.checkpoint_id)
    return _view(engine, run_id)


@router.post("/runs/{run_id}/replan")
def replan_run(run_id: str, payload: ReplanIn, request: Request):
    engine = _engine(request)
    engine.replan(run_id, payload.artifact, payload.content, payload.reason)
    return _view(engine, run_id)
