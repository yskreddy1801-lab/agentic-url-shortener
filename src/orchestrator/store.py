"""Persistence for runs. All writes take one lock so parallel stages share SQLite safely."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update

from orchestrator.audit import GENESIS, hash_event
from orchestrator.db import Base, session_factory
from orchestrator.errors import OrchestratorError
from orchestrator.models import (
    ArtifactRow,
    AttemptRow,
    CheckpointRow,
    DecisionRow,
    EventRow,
    RunRow,
    StageRow,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _load(value: str | None):
    if value is None:
        return None
    return json.loads(value)


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    path = url.removeprefix("sqlite:///")
    if not path or path.startswith(":memory:"):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


class Store:
    def __init__(self, url: str):
        _ensure_sqlite_parent(url)
        self.url = url
        self.engine, self._sessions = session_factory(url)
        self._lock = threading.RLock()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def create_run(
        self,
        *,
        run_id: str,
        scenario: str,
        mode: str,
        brief: str,
        config: dict,
        status: str,
        created_at: str,
        started_at: str,
    ) -> None:
        with self._lock, self._sessions() as session:
            session.add(
                RunRow(
                    id=run_id,
                    scenario=scenario,
                    mode=mode,
                    brief=brief,
                    status=status,
                    config_json=_dump(config),
                    pause_stage=None,
                    pause_kind=None,
                    pause_details_json=None,
                    human_input_json=None,
                    stop_requested=False,
                    created_at=created_at,
                    started_at=started_at,
                    finished_at=None,
                    updated_at=created_at,
                )
            )
            session.commit()

    def get_run(self, run_id: str) -> dict:
        with self._lock, self._sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise OrchestratorError(f"Run '{run_id}' not found", status_code=404)
            return _run_dict(row)

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)).all()
            return [_run_dict(row) for row in rows]

    def update_run(self, run_id: str, **fields) -> None:
        json_fields = {"pause_details": "pause_details_json", "human_input": "human_input_json", "config": "config_json"}
        with self._lock, self._sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise OrchestratorError(f"Run '{run_id}' not found", status_code=404)
            for key, value in fields.items():
                column = json_fields.get(key, key)
                if key in json_fields:
                    value = None if value is None else _dump(value)
                setattr(row, column, value)
            row.updated_at = _now()
            session.commit()

    def init_stage(self, run_id: str, stage_id: str) -> None:
        with self._lock, self._sessions() as session:
            session.add(
                StageRow(
                    run_id=run_id,
                    stage_id=stage_id,
                    status="pending",
                    attempt=0,
                    generation=1,
                    fallback_used=False,
                    error=None,
                    started_at=None,
                    finished_at=None,
                )
            )
            session.commit()

    def set_stage(self, run_id: str, stage_id: str, **fields) -> None:
        with self._lock, self._sessions() as session:
            row = session.get(StageRow, (run_id, stage_id))
            if row is None:
                raise OrchestratorError(f"Stage '{stage_id}' not found on {run_id}", status_code=404)
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()

    def get_stages(self, run_id: str) -> dict[str, dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(StageRow).where(StageRow.run_id == run_id)).all()
            return {row.stage_id: _stage_dict(row) for row in rows}

    def invalidate_stage(self, run_id: str, stage_id: str) -> None:
        with self._lock, self._sessions() as session:
            row = session.get(StageRow, (run_id, stage_id))
            if row is None:
                raise OrchestratorError(f"Stage '{stage_id}' not found", status_code=404)
            row.generation += 1
            row.status = "pending"
            row.attempt = 0
            row.fallback_used = False
            row.error = None
            row.started_at = None
            row.finished_at = None
            session.commit()

    def open_attempt(self, run_id: str, stage_id: str, attempt: int) -> int:
        with self._lock, self._sessions() as session:
            stage = session.get(StageRow, (run_id, stage_id))
            if stage is None:
                raise OrchestratorError(f"Stage '{stage_id}' not found", status_code=404)
            row = AttemptRow(
                run_id=run_id,
                stage_id=stage_id,
                generation=stage.generation,
                attempt=attempt,
                status="running",
                started_at=_now(),
                finished_at=None,
                error=None,
            )
            session.add(row)
            session.commit()
            return row.id

    def close_attempt(self, attempt_id: int, status: str, error: str | None) -> None:
        with self._lock, self._sessions() as session:
            row = session.get(AttemptRow, attempt_id)
            if row is None:
                return
            row.status = status
            row.error = error
            row.finished_at = _now()
            session.commit()

    def list_attempts(self, run_id: str) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(AttemptRow).where(AttemptRow.run_id == run_id).order_by(AttemptRow.id)).all()
            return [_attempt_dict(row) for row in rows]

    def add_event(
        self,
        run_id: str,
        event_type: str,
        stage_id: str | None,
        details: dict | None = None,
    ) -> dict:
        safe_details = json.loads(_dump(details or {}))
        attempt = safe_details.get("attempt")
        if not isinstance(attempt, int):
            attempt = None
        with self._lock, self._sessions() as session:
            last = session.scalar(
                select(EventRow).where(EventRow.run_id == run_id).order_by(EventRow.seq.desc()).limit(1)
            )
            prev = last.event_hash if last is not None else GENESIS
            seq = 1 if last is None else last.seq + 1
            ts = _now()
            digest = hash_event(
                seq=seq,
                run_id=run_id,
                event_type=event_type,
                stage_id=stage_id,
                attempt=attempt,
                ts=ts,
                details=safe_details,
                prev_hash=prev,
            )
            row = EventRow(
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                stage_id=stage_id,
                attempt=attempt,
                ts=ts,
                details_json=_dump(safe_details),
                prev_hash=prev,
                event_hash=digest,
            )
            session.add(row)
            session.commit()
            return _event_dict(row)

    def list_events(self, run_id: str) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(EventRow).where(EventRow.run_id == run_id).order_by(EventRow.seq)).all()
            return [_event_dict(row) for row in rows]

    def write_artifact(self, run_id: str, name: str, content, content_type: str, stage_id: str) -> dict:
        text, digest = _encode_content(content, content_type)
        with self._lock, self._sessions() as session:
            current_max = session.scalar(
                select(func.max(ArtifactRow.version)).where(ArtifactRow.run_id == run_id, ArtifactRow.name == name)
            )
            version = int(current_max or 0) + 1
            session.execute(
                update(ArtifactRow)
                .where(ArtifactRow.run_id == run_id, ArtifactRow.name == name, ArtifactRow.is_current.is_(True))
                .values(is_current=False)
            )
            row = ArtifactRow(
                run_id=run_id,
                name=name,
                version=version,
                content_type=content_type,
                content_text=text,
                content_hash=digest,
                stage_id=stage_id,
                is_current=True,
                created_at=_now(),
            )
            session.add(row)
            session.commit()
            return _artifact_dict(row)

    def get_artifact(self, run_id: str, name: str, version: int | None = None) -> dict | None:
        with self._lock, self._sessions() as session:
            query = select(ArtifactRow).where(ArtifactRow.run_id == run_id, ArtifactRow.name == name)
            if version is None:
                query = query.where(ArtifactRow.is_current.is_(True))
            else:
                query = query.where(ArtifactRow.version == version)
            row = session.scalar(query)
            return None if row is None else _artifact_dict(row)

    def list_artifacts(self, run_id: str) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(
                select(ArtifactRow).where(ArtifactRow.run_id == run_id).order_by(ArtifactRow.name, ArtifactRow.version)
            ).all()
            return [_artifact_dict(row) for row in rows]

    def list_current_artifacts(self, run_id: str) -> list[dict]:
        return [row for row in self.list_artifacts(run_id) if row["is_current"]]

    def restore_artifacts(self, run_id: str, versions: dict[str, int]) -> None:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(ArtifactRow).where(ArtifactRow.run_id == run_id)).all()
            for row in rows:
                row.is_current = versions.get(row.name) == row.version
            session.commit()

    def add_decision(
        self,
        *,
        run_id: str,
        stage_id: str,
        kind: str,
        question: str,
        alternatives: list,
        selected: str,
        actor: str,
        rationale: str,
        rule: str,
        payload: dict | None = None,
    ) -> dict:
        decision_id = "dec_" + uuid.uuid4().hex[:10]
        with self._lock, self._sessions() as session:
            row = DecisionRow(
                id=decision_id,
                run_id=run_id,
                stage_id=stage_id,
                kind=kind,
                question=question,
                alternatives_json=_dump(alternatives),
                selected=selected,
                actor=actor,
                rationale=rationale,
                rule=rule,
                payload_json=_dump(payload or {}),
                invalidated=False,
                created_at=_now(),
            )
            session.add(row)
            session.commit()
            return _decision_dict(row)

    def list_decisions(self, run_id: str) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(select(DecisionRow).where(DecisionRow.run_id == run_id).order_by(DecisionRow.created_at)).all()
            return [_decision_dict(row) for row in rows]

    def find_decision(self, run_id: str, stage_id: str, kind: str) -> dict | None:
        with self._lock, self._sessions() as session:
            row = session.scalar(
                select(DecisionRow)
                .where(
                    DecisionRow.run_id == run_id,
                    DecisionRow.stage_id == stage_id,
                    DecisionRow.kind == kind,
                    DecisionRow.invalidated.is_(False),
                )
                .order_by(DecisionRow.created_at.desc())
            )
            return None if row is None else _decision_dict(row)

    def has_valid_approval(self, run_id: str, stage_id: str) -> bool:
        decision = self.find_decision(run_id, stage_id, "approval")
        return bool(decision and decision["selected"] == "approved")

    def invalidate_decisions(self, run_id: str, stage_id: str) -> None:
        with self._lock, self._sessions() as session:
            session.execute(
                update(DecisionRow)
                .where(DecisionRow.run_id == run_id, DecisionRow.stage_id == stage_id, DecisionRow.invalidated.is_(False))
                .values(invalidated=True)
            )
            session.commit()

    def add_checkpoint(self, run_id: str, label: str, snapshot: dict) -> dict:
        checkpoint_id = "ckpt_" + uuid.uuid4().hex[:10]
        with self._lock, self._sessions() as session:
            row = CheckpointRow(
                id=checkpoint_id,
                run_id=run_id,
                label=label,
                snapshot_json=_dump(snapshot),
                created_at=_now(),
            )
            session.add(row)
            session.commit()
            return _checkpoint_dict(row)

    def list_checkpoints(self, run_id: str) -> list[dict]:
        with self._lock, self._sessions() as session:
            rows = session.scalars(
                select(CheckpointRow).where(CheckpointRow.run_id == run_id).order_by(CheckpointRow.created_at, CheckpointRow.id)
            ).all()
            return [_checkpoint_dict(row) for row in rows]

    def get_checkpoint(self, run_id: str, checkpoint_id: str) -> dict | None:
        with self._lock, self._sessions() as session:
            row = session.get(CheckpointRow, checkpoint_id)
            if row is None or row.run_id != run_id:
                return None
            return _checkpoint_dict(row)

    def snapshot(self, run_id: str) -> dict:
        stages = self.get_stages(run_id)
        artifacts = self.list_current_artifacts(run_id)
        return {
            "stages": {
                stage_id: {
                    "status": stage["status"],
                    "attempt": stage["attempt"],
                    "generation": stage["generation"],
                }
                for stage_id, stage in stages.items()
            },
            "artifacts": {artifact["name"]: artifact["version"] for artifact in artifacts},
        }


def _encode_content(content, content_type: str) -> tuple[str, str]:
    if content_type == "json":
        text = json.dumps(content, sort_keys=True, indent=2, default=str)
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    else:
        text = str(content)
        canonical = text
    return text, hashlib.sha256(canonical.encode()).hexdigest()


def _run_dict(row: RunRow) -> dict:
    return {
        "id": row.id,
        "scenario": row.scenario,
        "mode": row.mode,
        "brief": row.brief,
        "status": row.status,
        "config": _load(row.config_json) or {},
        "pause_stage": row.pause_stage,
        "pause_kind": row.pause_kind,
        "pause_details": _load(row.pause_details_json),
        "human_input": _load(row.human_input_json),
        "stop_requested": bool(row.stop_requested),
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "updated_at": row.updated_at,
    }


def _stage_dict(row: StageRow) -> dict:
    return {
        "stage_id": row.stage_id,
        "status": row.status,
        "attempt": row.attempt,
        "generation": row.generation,
        "fallback_used": bool(row.fallback_used),
        "error": row.error,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


def _attempt_dict(row: AttemptRow) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "stage_id": row.stage_id,
        "generation": row.generation,
        "attempt": row.attempt,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": row.error,
    }


def _event_dict(row: EventRow) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "seq": row.seq,
        "type": row.event_type,
        "stage_id": row.stage_id,
        "attempt": row.attempt,
        "ts": row.ts,
        "details": _load(row.details_json) or {},
        "prev_hash": row.prev_hash,
        "event_hash": row.event_hash,
    }


def _artifact_dict(row: ArtifactRow) -> dict:
    if row.content_type == "json":
        content = json.loads(row.content_text)
    else:
        content = row.content_text
    return {
        "id": row.id,
        "run_id": row.run_id,
        "name": row.name,
        "version": row.version,
        "content_type": row.content_type,
        "content": content,
        "content_hash": row.content_hash,
        "stage_id": row.stage_id,
        "is_current": bool(row.is_current),
        "created_at": row.created_at,
    }


def _decision_dict(row: DecisionRow) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "stage_id": row.stage_id,
        "kind": row.kind,
        "question": row.question,
        "alternatives": _load(row.alternatives_json) or [],
        "selected": row.selected,
        "actor": row.actor,
        "rationale": row.rationale,
        "rule": row.rule,
        "payload": _load(row.payload_json) or {},
        "invalidated": bool(row.invalidated),
        "created_at": row.created_at,
    }


def _checkpoint_dict(row: CheckpointRow) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "label": row.label,
        "snapshot": _load(row.snapshot_json) or {},
        "created_at": row.created_at,
    }
