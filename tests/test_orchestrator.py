"""Orchestrator controls: ordering, parallelism, retry, rollback, approval, policy, replan."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from orchestrator.ambiguity import analyze_ambiguity, choose_by_rubric
from orchestrator.design_doc import build_design
from orchestrator.audit import verify_event_chain
from orchestrator.engine import Engine
from orchestrator.graph import Graph, StageSpec, default_sdlc_graph
from orchestrator.impact import scan_repository
from orchestrator.normalize import normalize_brief
from orchestrator.policies import scan_artifacts
from orchestrator.scenarios import AMBIGUOUS_BRIEF, BROWNFIELD_BRIEF, GREENFIELD_BRIEF
from orchestrator.store import Store
from orchestrator.verify_impl import verify_implementation
from shortener.paths import repo_root


def _engine(tmp_path, stages, handlers):
    store = Store(f"sqlite:///{tmp_path}/orch.db")
    store.create_all()
    return Engine(store, Graph(stages), handlers, tmp_path), store


def test_default_graph_shape():
    graph = default_sdlc_graph()
    assert graph.stage("requirements").depends_on == ()
    assert graph.stage("architecture").depends_on == ("requirements",)
    assert graph.stage("implementation").depends_on == ("architecture",)
    assert graph.stage("testing").depends_on == ("implementation",)
    assert graph.stage("documentation").depends_on == ("implementation",)
    assert graph.stage("security_review").depends_on == ("implementation",)
    assert set(graph.stage("release_readiness").depends_on) == {"testing", "documentation", "security_review"}
    assert graph.stage("release_readiness").requires_approval is True
    assert graph.stage("testing").fallback_handler == "testing_fallback"


def test_parallel_stages_wait_at_the_sync_barrier(tmp_path):
    original = default_sdlc_graph()
    stages = [
        replace(
            spec,
            handler=spec.id,
            entry_gates=(),
            exit_gates=(),
            fallback_exit_gates=(),
            fallback_handler=None,
            requires_approval=False,
            max_attempts=1,
        )
        for spec in original.stages_in_order()
    ]
    order = []
    lock = threading.Lock()

    def make(stage_id: str):
        def handler(_ctx):
            started = time.monotonic()
            with lock:
                order.append((stage_id, "start", started))
            time.sleep(0.25 if stage_id in {"testing", "documentation", "security_review"} else 0.01)
            with lock:
                order.append((stage_id, "end", time.monotonic()))

        return handler

    engine, store = _engine(tmp_path, stages, {spec.id: make(spec.id) for spec in stages})
    run_id = engine.start(scenario="shape", mode="demo", brief="clear brief", config={})
    engine.execute(run_id)
    assert store.get_run(run_id)["status"] == "succeeded"

    def stamp(stage_id, phase):
        return next(item[2] for item in order if item[0] == stage_id and item[1] == phase)

    assert stamp("requirements", "end") <= stamp("architecture", "start")
    assert stamp("architecture", "end") <= stamp("implementation", "start")
    assert stamp("implementation", "end") <= stamp("testing", "start")
    assert stamp("testing", "start") < stamp("security_review", "end")
    assert stamp("security_review", "start") < stamp("testing", "end")
    assert stamp("documentation", "start") < stamp("testing", "end")
    barrier = max(stamp("testing", "end"), stamp("documentation", "end"), stamp("security_review", "end"))
    assert stamp("release_readiness", "start") >= barrier


def test_retry_then_success_records_attempts(tmp_path):
    state = {"n": 0}

    def flaky(ctx):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        ctx.write_artifact("out", {"ok": True})

    engine, store = _engine(
        tmp_path,
        [StageSpec("work", "flaky", exit_gates=("artifact:out",), max_attempts=3)],
        {"flaky": flaky},
    )
    run_id = engine.start(scenario="retry", mode="demo", brief="retry me", config={})
    engine.execute(run_id)
    assert store.get_run(run_id)["status"] == "succeeded"
    events = store.list_events(run_id)
    assert sum(1 for event in events if event["type"] == "stage.retry") == 2
    assert state["n"] == 3
    verify_event_chain(events)


def test_fallback_after_retries(tmp_path):
    def always(_ctx):
        raise RuntimeError("nope")

    def fallback(ctx):
        ctx.write_artifact("out", {"fallback": True})

    engine, store = _engine(
        tmp_path,
        [
            StageSpec(
                "work",
                "always",
                max_attempts=2,
                fallback_handler="fallback",
                exit_gates=("tests_green",),
                fallback_exit_gates=("artifact:out",),
            )
        ],
        {"always": always, "fallback": fallback},
    )
    run_id = engine.start(scenario="fallback", mode="demo", brief="fallback", config={})
    engine.execute(run_id)
    assert store.get_stages(run_id)["work"]["status"] == "succeeded_with_fallback"
    assert store.get_run(run_id)["status"] == "succeeded"
    assert any(event["type"] == "stage.fallback" for event in store.list_events(run_id))


def test_rollback_restores_last_good_checkpoint(tmp_path):
    def make(name):
        def handler(ctx):
            ctx.write_artifact(name, {"name": name})

        return handler

    stages = [
        StageSpec("a", "a", exit_gates=("artifact:a",)),
        StageSpec("b", "b", depends_on=("a",), exit_gates=("artifact:b",)),
        StageSpec("c", "c", depends_on=("b",), exit_gates=("artifact:c",)),
    ]
    engine, store = _engine(tmp_path, stages, {"a": make("a"), "b": make("b"), "c": make("c")})
    run_id = engine.start(scenario="rollback", mode="demo", brief="linear", config={})
    engine.execute(run_id)
    assert store.get_artifact(run_id, "c")["content"]["name"] == "c"
    engine.rollback(run_id)
    assert store.get_run(run_id)["status"] == "rolled_back"
    assert store.get_stages(run_id)["c"]["status"] == "rolled_back"
    assert store.get_stages(run_id)["b"]["status"] == "succeeded"
    assert store.get_artifact(run_id, "c") is None
    assert store.get_artifact(run_id, "b")["content"]["name"] == "b"
    versions = [row for row in store.list_artifacts(run_id) if row["name"] == "c"]
    assert versions and versions[0]["is_current"] is False
    assert any(event["type"] == "run.rolled_back" for event in store.list_events(run_id))
    verify_event_chain(store.list_events(run_id))


def test_safe_stop_finishes_inflight_stage_and_skips_the_next(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def stage_a(ctx):
        ctx.write_artifact("a", {"ok": True})

    def stage_b(_ctx):
        started.set()
        assert release.wait(5)

    def stage_c(ctx):
        ctx.write_artifact("c", {"should": "not run"})

    engine, store = _engine(
        tmp_path,
        [
            StageSpec("a", "a", exit_gates=("artifact:a",)),
            StageSpec("b", "b", depends_on=("a",)),
            StageSpec("c", "c", depends_on=("b",)),
        ],
        {"a": stage_a, "b": stage_b, "c": stage_c},
    )
    run_id = engine.start(scenario="stop", mode="demo", brief="stop", config={})
    worker = threading.Thread(target=engine.execute, args=(run_id,))
    worker.start()
    assert started.wait(5)
    engine.stop(run_id)
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert store.get_run(run_id)["status"] == "stopped"
    assert store.get_stages(run_id)["b"]["status"] == "succeeded"
    assert store.get_stages(run_id)["c"]["status"] == "pending"
    types = [event["type"] for event in store.list_events(run_id)]
    assert "run.stop_requested" in types
    assert "run.stopped" in types


def test_demo_auto_approves_after_checkpoint_and_supervised_pauses(tmp_path):
    def work(ctx):
        ctx.write_artifact("out", {"ok": True})

    spec = StageSpec("release_readiness", "work", requires_approval=True, exit_gates=("artifact:out",), approval_prompt="ship")

    demo, demo_store = _engine(tmp_path / "demo", [spec], {"work": work})
    demo_id = demo.start(scenario="approval", mode="demo", brief="ship", config={})
    demo.execute(demo_id)
    demo_events = demo_store.list_events(demo_id)
    types = [event["type"] for event in demo_events]
    requested = types.index("approval.requested")
    granted = types.index("approval.granted")
    assert types[requested - 1] == "checkpoint.created"
    assert requested < granted
    assert demo_store.list_decisions(demo_id)[0]["actor"] == "demo_policy"
    assert demo_store.get_run(demo_id)["status"] == "succeeded"
    verify_event_chain(demo_events)

    supervised, supervised_store = _engine(tmp_path / "sup", [spec], {"work": work})
    supervised_id = supervised.start(scenario="approval", mode="supervised", brief="ship", config={})
    supervised.execute(supervised_id)
    paused = supervised_store.get_run(supervised_id)
    assert paused["status"] == "paused"
    assert paused["pause_kind"] == "approval"
    assert supervised_store.get_artifact(supervised_id, "out") is None
    supervised.approve(supervised_id, "release_readiness", note="ship it")
    assert supervised_store.get_run(supervised_id)["status"] == "succeeded"
    assert any(item["actor"] == "human" for item in supervised_store.list_decisions(supervised_id))


def test_reject_blocks_the_stage(tmp_path):
    def work(ctx):
        ctx.write_artifact("out", {"ok": True})

    engine, store = _engine(
        tmp_path,
        [StageSpec("release_readiness", "work", requires_approval=True)],
        {"work": work},
    )
    run_id = engine.start(scenario="reject", mode="supervised", brief="no", config={})
    engine.execute(run_id)
    engine.reject(run_id, "release_readiness", note="not yet")
    assert store.get_run(run_id)["status"] == "rejected"
    assert store.get_artifact(run_id, "out") is None
    assert any(event["type"] == "approval.rejected" for event in store.list_events(run_id))


def test_policy_blocks_secret_material(tmp_path):
    def leak(ctx):
        ctx.write_artifact("notes", {"token": "AKIAIOSFODNN7EXAMPLE"})

    engine, store = _engine(
        tmp_path,
        [StageSpec("scan", "leak", exit_gates=("policy_clear",), max_attempts=3)],
        {"leak": leak},
    )
    run_id = engine.start(scenario="policy", mode="demo", brief="scan", config={})
    engine.execute(run_id)
    assert store.get_run(run_id)["status"] == "failed"
    events = store.list_events(run_id)
    assert any(event["type"] == "policy.violation" for event in events)
    assert sum(1 for event in events if event["type"] == "stage.started") == 1
    prose = scan_artifacts({"design": {"markdown": "Reject the javascript scheme at create time."}})
    assert prose == []


def test_replan_reruns_downstream_and_keeps_history(tmp_path):
    def write_upstream(ctx):
        ctx.write_artifact("doc", {"marker": "v1"})

    def write_downstream(ctx):
        ctx.write_artifact("downstream", {"saw": ctx.artifact("doc")["marker"]})

    engine, store = _engine(
        tmp_path,
        [
            StageSpec("a", "a", exit_gates=("artifact:doc",)),
            StageSpec("b", "b", depends_on=("a",), entry_gates=("artifact:doc",), exit_gates=("artifact:downstream",)),
        ],
        {"a": write_upstream, "b": write_downstream},
    )
    run_id = engine.start(scenario="replan", mode="demo", brief="replan", config={})
    engine.execute(run_id)
    engine.replan(run_id, "doc", {"marker": "v2"}, reason="upstream edited")
    assert store.get_artifact(run_id, "downstream")["content"]["saw"] == "v2"
    assert store.get_artifact(run_id, "doc")["version"] == 2
    attempts = store.list_attempts(run_id)
    downstream = [row for row in attempts if row["stage_id"] == "b" and row["status"] == "succeeded"]
    upstream = [row for row in attempts if row["stage_id"] == "a" and row["status"] == "succeeded"]
    assert len(downstream) == 2
    assert {row["generation"] for row in downstream} == {1, 2}
    assert len(upstream) == 1
    versions = [row["version"] for row in store.list_artifacts(run_id) if row["name"] == "doc"]
    assert versions == [1, 2]
    types = [event["type"] for event in store.list_events(run_id)]
    assert "replan.triggered" in types
    assert "stage.invalidated" in types
    verify_event_chain(store.list_events(run_id))


def test_ambiguity_detector_and_rubric():
    vague = analyze_ambiguity(AMBIGUOUS_BRIEF)
    assert vague["is_ambiguous"] is True
    assert len(vague["interpretations"]) == 3
    assert choose_by_rubric(vague["interpretations"])["selected"] == "analytics"
    assert analyze_ambiguity(GREENFIELD_BRIEF)["is_ambiguous"] is False
    assert analyze_ambiguity(BROWNFIELD_BRIEF)["is_ambiguous"] is False
    green = normalize_brief(GREENFIELD_BRIEF, kind="greenfield")
    assert len(green["functional_requirements"]) == 7
    brown = normalize_brief(BROWNFIELD_BRIEF, kind="brownfield")
    assert len(brown["functional_requirements"]) == 3
    assert brown["constraints"]


def test_design_cites_exact_routes_not_path_prefixes():
    requirements = normalize_brief(BROWNFIELD_BRIEF, kind="brownfield")
    design = build_design(requirements, scan_repository(repo_root()))
    cited = {(api["method"], api["path"]) for api in design["apis"]}
    assert ("GET", "/") not in cited
    assert ("POST", "/api/v1/urls") in cited
    assert ("GET", "/{code}") in cited
    assert ("GET", "/api/v1/urls/{code}/stats") in cited
    assert [model["class"] for model in design["data_model"]] == ["ShortLink", "Click"]


def test_auto_expire_interpretation_is_not_treated_as_implemented():
    requirements = {
        "functional_requirements": [
            {
                "id": "FR-1",
                "statement": "Automatically expire every existing link after 7 days.",
                "acceptance": ["A retention policy updates links that were created without an expiration."],
            }
        ]
    }
    report = verify_implementation(requirements, scan_repository(repo_root()))
    assert report["verified"] is False


def test_supervised_ambiguous_run_pauses_over_http(client: TestClient):
    response = client.post("/api/v1/runs", json={"scenario": "ambiguous", "mode": "supervised"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "paused"
    assert body["pause_kind"] == "decision"
    assert {item["id"] for item in body["pause_details"]["alternatives"]} == {
        "analytics",
        "aliases",
        "auto_expire_all",
    }
    events = client.get(f"/api/v1/runs/{body['id']}/events")
    assert events.status_code == 200
    assert any(event["type"] == "decision.requested" for event in events.json())
    metrics = client.get(f"/api/v1/runs/{body['id']}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["decision_count"] == 0
