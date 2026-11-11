"""End-to-end demo runs of the three SDLC scenarios."""

from __future__ import annotations

from orchestrator.audit import verify_event_chain
from orchestrator.engine import Engine
from orchestrator.export import export_run
from orchestrator.graph import default_sdlc_graph
from orchestrator.handlers import build_handlers
from orchestrator.metrics import compute_metrics
from orchestrator.scenarios import SCENARIOS
from orchestrator.store import Store
from shortener.paths import repo_root


def _run(tmp_path, name: str):
    scenario = SCENARIOS[name]
    store = Store(f"sqlite:///{tmp_path}/{name}.db")
    store.create_all()
    engine = Engine(store, default_sdlc_graph(), build_handlers(), repo_root())
    run_id = engine.start(
        scenario=scenario.name,
        mode="demo",
        brief=scenario.brief,
        config={"kind": scenario.kind, "title": scenario.title},
    )
    engine.execute(run_id)
    export_run(store, run_id, tmp_path / name)
    return store, run_id


def test_three_scenarios_reach_release_readiness(tmp_path):
    summaries = {}
    for name in ("greenfield", "brownfield", "ambiguous"):
        store, run_id = _run(tmp_path, name)
        run = store.get_run(run_id)
        assert run["status"] == "succeeded", store.get_stages(run_id)
        report = store.get_artifact(run_id, "release_report")["content"]
        assert report["ready"] is True
        assert report["recommendation"] == "accept_for_prototype_with_known_limits"
        assert store.get_artifact(run_id, "test_report")["content"]["passed"] is True
        verify_event_chain(store.list_events(run_id))
        metrics = compute_metrics(store, run_id)
        assert metrics["stage_success_rate"] == 1.0
        assert metrics["parallelism_max"] >= 2
        assert (tmp_path / name / "events.jsonl").is_file()
        assert (tmp_path / name / "release_checklist.md").is_file()
        summaries[name] = (store, run_id, report)

    _store, run_id, _report = summaries["ambiguous"]
    store = _store
    decisions = [item for item in store.list_decisions(run_id) if item["kind"] == "ambiguity"]
    assert len(decisions) == 1
    assert decisions[0]["actor"] == "demo_policy"
    assert decisions[0]["selected"] == "analytics"
    assert {item["id"] for item in decisions[0]["alternatives"]} == {"analytics", "aliases", "auto_expire_all"}
    requirements = store.get_artifact(run_id, "normalized_requirements")["content"]
    assert requirements["selected_interpretation"] == "analytics"
    assert requirements["decision"]["rule"] == "max_rubric_score_then_lexical_id"

    store, run_id, _report = summaries["brownfield"]
    impact = store.get_artifact(run_id, "design")["content"]["impact_analysis"]
    assert impact["verified_symbols"] is True
    assert impact["data_flow"]
    assert impact["impacted_modules"]
    assert any(api["path"] == "/{code}" for api in impact["impacted_apis"])

    for name in ("greenfield", "brownfield", "ambiguous"):
        store, run_id, _report = summaries[name]
        types = [event["type"] for event in store.list_events(run_id)]
        requested = types.index("approval.requested")
        assert types[requested - 1] == "checkpoint.created"
        assert types.index("approval.granted") > requested
