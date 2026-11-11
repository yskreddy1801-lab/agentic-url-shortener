"""Command line for the three scenarios and for run control.

Examples:

    python -m orchestrator run-all --mode demo
    python -m orchestrator run greenfield --mode demo
    python -m orchestrator status run_abc
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shortener.paths import repo_root
from orchestrator.engine import Engine
from orchestrator.errors import OrchestratorError
from orchestrator.export import export_run
from orchestrator.graph import default_sdlc_graph
from orchestrator.handlers import build_handlers
from orchestrator.metrics import compute_metrics
from orchestrator.scenarios import SCENARIOS
from orchestrator.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator", description="Run and inspect SDLC orchestration")
    parser.add_argument("--db", default="", help="SQLite URL. Defaults to data/orchestrator.db")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute one scenario")
    run.add_argument("scenario", choices=sorted(SCENARIOS))
    run.add_argument("--mode", choices=["demo", "supervised"], default="demo")

    run_all = sub.add_parser("run-all", help="Execute greenfield, brownfield, and ambiguous")
    run_all.add_argument("--mode", choices=["demo", "supervised"], default="demo")

    status = sub.add_parser("status", help="Show run and stage status")
    status.add_argument("run_id")

    events = sub.add_parser("events", help="Print the audit log")
    events.add_argument("run_id")

    lineage = sub.add_parser("lineage", help="Print decisions, checkpoints, and artifact versions")
    lineage.add_argument("run_id")

    metrics = sub.add_parser("metrics", help="Print reliability metrics")
    metrics.add_argument("run_id")

    approve = sub.add_parser("approve", help="Approve a paused checkpoint and resume")
    approve.add_argument("run_id")
    approve.add_argument("--stage", required=True)
    approve.add_argument("--note", default="")
    approve.add_argument("--alternative", default="")
    approve.add_argument("--accept-breaking", action="store_true")

    reject = sub.add_parser("reject", help="Reject a paused checkpoint")
    reject.add_argument("run_id")
    reject.add_argument("--stage", required=True)
    reject.add_argument("--note", default="")

    stop = sub.add_parser("stop", help="Safe-stop a run")
    stop.add_argument("run_id")

    rollback = sub.add_parser("rollback", help="Restore a checkpoint")
    rollback.add_argument("run_id")
    rollback.add_argument("--checkpoint", default="")

    replan = sub.add_parser("replan", help="Replace an upstream artifact and rerun downstream stages")
    replan.add_argument("run_id")
    replan.add_argument("--artifact", required=True)
    replan.add_argument("--content-file", required=True)
    replan.add_argument("--reason", default="upstream artifact replaced")

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except OrchestratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    root = repo_root()
    url = args.db or "sqlite:///" + str(root / "data" / "orchestrator.db")
    store = Store(url)
    store.create_all()
    engine = Engine(store, default_sdlc_graph(), build_handlers(), root)
    if args.command == "run":
        return _run_one(engine, store, args.scenario, args.mode, root)
    if args.command == "run-all":
        code = 0
        print(f"{'scenario':<12} {'run':<16} {'status':<12} {'ready':<6} {'retries':<8} {'e2e_ms':<8} interpretation")
        for name in ("greenfield", "brownfield", "ambiguous"):
            outcome = _execute(engine, store, name, args.mode, root)
            _print_summary_row(outcome)
            if outcome["exit_code"] != 0:
                code = outcome["exit_code"]
        return code
    if args.command == "status":
        _print_status(engine, args.run_id)
        return 0
    if args.command == "events":
        for event in store.list_events(args.run_id):
            print(json.dumps(event, sort_keys=True))
        return 0
    if args.command == "lineage":
        payload = {
            "decisions": store.list_decisions(args.run_id),
            "checkpoints": store.list_checkpoints(args.run_id),
            "artifacts": store.list_artifacts(args.run_id),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "metrics":
        print(json.dumps(compute_metrics(store, args.run_id), indent=2, sort_keys=True))
        return 0
    if args.command == "approve":
        engine.approve(
            args.run_id,
            args.stage,
            note=args.note,
            selected_alternative=args.alternative or None,
            accept_breaking=args.accept_breaking,
        )
        _print_status(engine, args.run_id)
        return 0
    if args.command == "reject":
        engine.reject(args.run_id, args.stage, note=args.note)
        _print_status(engine, args.run_id)
        return 0
    if args.command == "stop":
        engine.stop(args.run_id)
        _print_status(engine, args.run_id)
        return 0
    if args.command == "rollback":
        engine.rollback(args.run_id, args.checkpoint or None)
        _print_status(engine, args.run_id)
        return 0
    if args.command == "replan":
        content_path = Path(args.content_file)
        if not content_path.is_absolute():
            content_path = root / content_path
        content = json.loads(content_path.read_text(encoding="utf-8"))
        engine.replan(args.run_id, args.artifact, content, args.reason)
        _print_status(engine, args.run_id)
        return 0
    return 2


def _run_one(engine, store, scenario: str, mode: str, root) -> int:
    outcome = _execute(engine, store, scenario, mode, root)
    _print_summary_row(outcome)
    if outcome["run"]["status"] == "paused":
        print(json.dumps(outcome["run"]["pause_details"], indent=2))
    return outcome["exit_code"]


def _execute(engine, store, scenario_name: str, mode: str, root) -> dict:
    scenario = SCENARIOS[scenario_name]
    run_id = engine.start(
        scenario=scenario.name,
        mode=mode,
        brief=scenario.brief,
        config={"kind": scenario.kind, "title": scenario.title},
    )
    run = engine.execute(run_id)
    dest = root / "artifacts" / "scenarios" / scenario.name
    export_run(store, run_id, dest)
    report = store.get_artifact(run_id, "release_report")
    ready = bool(report and report["content"].get("ready"))
    metrics = compute_metrics(store, run_id)
    requirements = store.get_artifact(run_id, "normalized_requirements")
    selected = ""
    if requirements:
        selected = requirements["content"].get("selected_interpretation") or ""
    exit_code = 0 if run["status"] == "succeeded" and ready else 2 if run["status"] == "paused" else 1
    return {
        "run": run,
        "ready": ready,
        "metrics": metrics,
        "selected": selected,
        "artifacts": str(dest),
        "exit_code": exit_code,
    }


def _print_summary_row(outcome: dict) -> None:
    run = outcome["run"]
    metrics = outcome["metrics"]
    e2e = metrics["end_to_end_latency_ms"]
    e2e_text = "-" if e2e is None else str(e2e)
    print(
        f"{run['scenario']:<12} {run['id']:<16} {run['status']:<12} {str(outcome['ready']):<6} "
        f"{metrics['retry_count']:<8} {e2e_text:<8} {outcome['selected'] or '-'}"
    )
    print(f"  artifacts: {outcome['artifacts']}")


def _print_status(engine: Engine, run_id: str) -> None:
    run = engine.store.get_run(run_id)
    print(f"{run['id']}  {run['scenario']}  {run['mode']}  {run['status']}")
    if run["pause_stage"]:
        print(f"paused at {run['pause_stage']} ({run['pause_kind']})")
    for stage_id, stage in engine.store.get_stages(run_id).items():
        print(f"  {stage_id:<20} {stage['status']:<24} attempt={stage['attempt']} gen={stage['generation']}")


if __name__ == "__main__":
    raise SystemExit(main())
