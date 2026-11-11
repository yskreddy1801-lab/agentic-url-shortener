"""Write the latest run's artifacts and audit log to a folder a reviewer can open."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from orchestrator.metrics import compute_metrics


def export_run(store, run_id: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    versions = dest / "versions"
    versions.mkdir()
    run = store.get_run(run_id)
    for artifact in store.list_artifacts(run_id):
        _write_version(versions, artifact)
        if artifact["is_current"]:
            _write_current(dest, artifact)
    events = store.list_events(run_id)
    with (dest / "events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    lineage = {
        "run_id": run_id,
        "decisions": store.list_decisions(run_id),
        "checkpoints": [
            {"id": row["id"], "label": row["label"], "created_at": row["created_at"]}
            for row in store.list_checkpoints(run_id)
        ],
        "artifacts": [
            {
                "name": row["name"],
                "version": row["version"],
                "hash": row["content_hash"],
                "stage_id": row["stage_id"],
                "is_current": row["is_current"],
            }
            for row in store.list_artifacts(run_id)
        ],
    }
    (dest / "lineage.json").write_text(json.dumps(lineage, indent=2, sort_keys=True), encoding="utf-8")
    metrics = compute_metrics(store, run_id)
    (dest / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    summary = {key: value for key, value in run.items() if key != "brief"}
    (dest / "run.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (dest / "brief.md").write_text(run["brief"], encoding="utf-8")
    return dest


def _write_version(folder: Path, artifact: dict) -> None:
    suffix = "md" if artifact["content_type"] == "text" else "json"
    path = folder / f"{artifact['name']}.v{artifact['version']}.{suffix}"
    if artifact["content_type"] == "json":
        path.write_text(json.dumps(artifact["content"], indent=2, sort_keys=True), encoding="utf-8")
    else:
        path.write_text(str(artifact["content"]), encoding="utf-8")


def _write_current(folder: Path, artifact: dict) -> None:
    name = artifact["name"]
    content = artifact["content"]
    if name == "design" and isinstance(content, dict) and content.get("markdown"):
        (folder / "design.json").write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
        (folder / "design.md").write_text(content["markdown"], encoding="utf-8")
        return
    if name == "docs_snippet" and isinstance(content, dict) and content.get("markdown"):
        (folder / "docs_snippet.json").write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
        (folder / "docs_snippet.md").write_text(content["markdown"], encoding="utf-8")
        return
    if artifact["content_type"] == "text":
        extension = "md" if "checklist" in name or "doc" in name else "txt"
        (folder / f"{name}.{extension}").write_text(str(content), encoding="utf-8")
        return
    (folder / f"{name}.json").write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
