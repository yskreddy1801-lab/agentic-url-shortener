"""Code generation, task plans, workspace rollback, and supervised apply."""

from __future__ import annotations

from orchestrator.codegen import generate_patch
from orchestrator.engine import Engine
from orchestrator.graph import default_sdlc_graph
from orchestrator.handlers import build_handlers
from orchestrator.impact import scan_repository
from orchestrator.normalize import normalize_brief
from orchestrator.planner import build_task_plan
from orchestrator.scenarios import BROWNFIELD_BRIEF, GREENFIELD_BRIEF
from orchestrator.store import Store
from orchestrator.verify_impl import verify_implementation
from orchestrator.workspace import apply_files, rollback_files
from shortener.paths import repo_root


def test_brownfield_gap_generates_a_bulk_patch():
    requirements = normalize_brief(BROWNFIELD_BRIEF, kind="brownfield")
    report = verify_implementation(requirements, scan_repository(repo_root()))
    assert report["verified"] is False
    assert ("POST", "/api/v1/urls/bulk") in {(item["method"], item["path"]) for item in report["missing_routes"]}
    patch = generate_patch(requirements, report, repo_root())
    assert patch is not None
    assert patch["id"] == "patch-bulk-create"
    assert "def create_bulk" in patch["diff"]
    assert "POST /api/v1/urls/bulk" in patch["diff"] or "/api/v1/urls/bulk" in patch["diff"]
    names = {item["path"] for item in patch["files"]}
    assert "src/shortener/bulk.py" in names
    assert "tests/test_bulk_create.py" in names


def test_greenfield_scaffold_does_not_rewrite_existing_routes():
    requirements = normalize_brief(GREENFIELD_BRIEF, kind="greenfield")
    report = verify_implementation(requirements, scan_repository(repo_root()))
    assert report["verified"] is True
    patch = generate_patch(requirements, report, repo_root())
    assert patch is not None
    assert patch["id"] == "patch-greenfield-scaffold"
    assert all(item["path"].startswith("sandbox/") for item in patch["files"])


def test_task_plan_is_a_subgraph_of_the_brief():
    plan = build_task_plan(normalize_brief(BROWNFIELD_BRIEF, kind="brownfield"))
    by_id = {task["id"]: task for task in plan["tasks"]}
    assert "FR-1-design" in by_id["FR-1-implement"]["depends_on"]
    assert "FR-1-implement" in by_id["FR-1-test"]["depends_on"]
    assert "APPLY-1" in by_id
    assert by_id["APPLY-1"]["owning_stage"] == "apply_patch"
    assert plan["edges"]


def test_apply_rollback_removes_created_files(tmp_path):
    snapshot = apply_files(tmp_path, [{"path": "src/generated.py", "content": "VALUE = 1\n"}])
    assert (tmp_path / "src/generated.py").read_text() == "VALUE = 1\n"
    rollback_files(tmp_path, snapshot)
    assert not (tmp_path / "src/generated.py").exists()


def test_supervised_brownfield_pauses_before_applying(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/orch.db")
    store.create_all()
    engine = Engine(store, default_sdlc_graph(), build_handlers(), repo_root())
    run_id = engine.start(
        scenario="brownfield",
        mode="supervised",
        brief=BROWNFIELD_BRIEF,
        config={"kind": "brownfield", "title": "Bulk link creation"},
    )
    engine.execute(run_id)
    run = store.get_run(run_id)
    assert run["status"] == "paused"
    assert run["pause_stage"] == "apply_patch"
    assert run["pause_kind"] == "approval"
    assert not (repo_root() / "src/shortener/bulk.py").exists()
    patch = store.get_artifact(run_id, "code_patch")["content"]
    assert patch["id"] == "patch-bulk-create"
