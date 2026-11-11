"""Reliability metrics derived from the audit log and stage attempts."""

from __future__ import annotations

from datetime import datetime

SUCCESS = {"succeeded", "succeeded_with_fallback"}


def compute_metrics(store, run_id: str) -> dict:
    run = store.get_run(run_id)
    events = store.list_events(run_id)
    attempts = store.list_attempts(run_id)
    stages = store.get_stages(run_id)
    stage_count = len(stages) or 1
    succeeded = sum(1 for stage in stages.values() if stage["status"] in SUCCESS)
    timings = _stage_timings(attempts)
    return {
        "run_id": run_id,
        "scenario": run["scenario"],
        "status": run["status"],
        "mode": run["mode"],
        "stage_success_rate": round(succeeded / stage_count, 4),
        "stages_succeeded": succeeded,
        "stage_count": stage_count,
        "retry_count": _count(events, "stage.retry"),
        "rollback_count": _count(events, "run.rolled_back"),
        "fallback_count": _count(events, "stage.fallback"),
        "approval_count": _count(events, "approval.granted"),
        "decision_count": _count(events, "decision.recorded"),
        "policy_violation_count": _count(events, "policy.violation"),
        "stage_timings_ms": timings,
        "end_to_end_latency_ms": _millis(run["started_at"], run["finished_at"]),
        "mttr_ms": _mttr(attempts),
        "parallelism_max": _parallelism(attempts),
        "definitions": {
            "stage_success_rate": "Stages in a success status divided by stages in the graph. Pending and failed stages count against the rate.",
            "mttr_ms": "Mean time from the first failed attempt of a stage to a later successful attempt in the same run. Null when no stage recovered.",
            "end_to_end_latency_ms": "Run start to the timestamp recorded when the run left the executing state. Null while still running or paused.",
            "parallelism_max": "Peak number of stage attempts whose start/finish intervals overlap.",
        },
    }


def _count(events: list[dict], event_type: str) -> int:
    return sum(1 for event in events if event["type"] == event_type)


def _millis(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return int(delta.total_seconds() * 1000)


def _stage_timings(attempts: list[dict]) -> dict[str, int | None]:
    latest: dict[str, dict] = {}
    for attempt in attempts:
        if attempt["status"] in {"succeeded", "failed", "paused"}:
            latest[attempt["stage_id"]] = attempt
    timings = {}
    for stage_id, attempt in latest.items():
        timings[stage_id] = _millis(attempt["started_at"], attempt["finished_at"])
    return timings


def _mttr(attempts: list[dict]) -> int | None:
    by_stage: dict[str, list[dict]] = {}
    for attempt in attempts:
        by_stage.setdefault(attempt["stage_id"], []).append(attempt)
    samples = []
    for rows in by_stage.values():
        failure = next((row for row in rows if row["status"] == "failed" and row["finished_at"]), None)
        success = next(
            (
                row
                for row in rows
                if row["status"] == "succeeded" and failure and row["finished_at"] and row["id"] > failure["id"]
            ),
            None,
        )
        if failure and success:
            sample = _millis(failure["finished_at"], success["finished_at"])
            if sample is not None:
                samples.append(sample)
    if not samples:
        return None
    return int(sum(samples) / len(samples))


def _parallelism(attempts: list[dict]) -> int:
    points: list[tuple[str, int]] = []
    for attempt in attempts:
        if attempt["started_at"] and attempt["finished_at"]:
            points.append((attempt["started_at"], 1))
            points.append((attempt["finished_at"], -1))
    points.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for _ts, delta in points:
        current += delta
        peak = max(peak, current)
    return peak
