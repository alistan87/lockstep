"""Composition (`kind: "flow"`): PROPOSAL-flow-composition, adopted 2026-08-14.

The torture cases the proposal froze, as pytest: child budget trip is a
run-level stop; a child gate block maps to a parent failure naming the gate;
crash/block-resume re-enters the child WITHOUT re-billing completed child
nodes; a child writer and a parent writer serialize on the ONE shared tree;
cancel stops the child cooperatively; and the child dir is invisible to the
single-level readers by construction.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from lockstep import EXIT_BUDGET
from lockstep.executors.fake import FakeExecutor
from lockstep.executors.flow import FlowExecutor
from lockstep.executors.shell import ShellExecutor
from lockstep.policy import AllowAllPolicy
from lockstep.registry import Registry
from lockstep.roles import Engine
from lockstep.state import load_state

from conftest import build, calls_of, make_config

BLOCK = {"findings": [], "verdict": "block", "reason": "not yet"}
PASS = {"findings": [], "verdict": "pass", "reason": "fine"}


def compose_build(tmp_path, parent_flow, git_repo, *, child_files,
                  child_fake=None, run_dir=None, state=None, max_workers=2):
    """conftest.build plus a FlowExecutor whose child registries share ONE
    observable FakeExecutor (`h.child_fake.calls`) — and a rebuilt engine, so
    bind_run sees the flow kind."""
    for name, flow in child_files.items():
        (git_repo / name).write_text(json.dumps(flow), encoding="utf-8")
    config = make_config()
    child_fake = child_fake or FakeExecutor(repo_root=git_repo)

    def child_registry() -> Registry:
        reg = Registry()
        reg.register(child_fake)
        reg.register(ShellExecutor(repo_root=git_repo))
        reg.register(FlowExecutor(config=config, repo_root=git_repo,
                                  make_registry=child_registry))
        return reg

    h = build(tmp_path, parent_flow, git_repo, config=config,
              run_dir=run_dir, state=state, max_workers=max_workers)
    h.engine.registry.register(FlowExecutor(
        config=config, repo_root=git_repo, make_registry=child_registry))
    h.engine = Engine(
        tg=h.tg, registry=h.engine.registry, config=config,
        workspace=h.engine.workspace, store=h.store, policy=AllowAllPolicy(),
        repo_root=git_repo, max_workers=max_workers, log=h.logs.append,
    )
    h.child_fake = child_fake
    return h


def child_two_step(final_output="child-answer"):
    return {
        "name": "child",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["step one"], "readonly": True}},
            {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
             "spec": {"outputs": [final_output], "task": "use {steps.a.output}",
                      "readonly": True}},
        ],
    }


def parent_with_flow(spec_args=None, flow_file="child.tg.json", args=None):
    node = {"id": "sub", "role": "work", "kind": "flow", "final": True,
            "spec": {"flow": flow_file, **({"args": spec_args} if spec_args else {})}}
    out = {"name": "parent", "nodes": [node]}
    if args:
        out["args"] = args
    return out


def _children_of(run_dir):
    root = run_dir / "children"
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


# --------------------------------------------------------------- the happy path


def test_a_child_flow_runs_as_one_node_and_its_final_result_is_the_nodes(tmp_path, git_repo):
    h = compose_build(tmp_path, parent_with_flow(), git_repo,
                      child_files={"child.tg.json": child_two_step()})
    assert h.engine.run() == 0
    assert (h.run_dir / "phases" / "sub" / "result.txt").read_text(
        encoding="utf-8") == "child-answer"
    assert load_state(h.run_dir).nodes["sub"].status == "done"
    kids = _children_of(h.run_dir)
    assert len(kids) == 1 and kids[0].startswith("sub-")
    child_state = load_state(h.run_dir / "children" / kids[0])
    assert all(r.status == "done" for r in child_state.nodes.values())
    assert len(calls_of(SimpleNamespace(fake=h.child_fake), "a")) == 1


def test_parent_args_thread_through_to_the_childs_prompts(tmp_path, git_repo):
    child = {
        "name": "child",
        "args": {"topic": None},
        "nodes": [{"id": "w", "kind": "fake", "final": True,
                   "spec": {"outputs": ["ok"], "task": "write about {args.topic}",
                            "readonly": True}}],
    }
    parent = parent_with_flow(spec_args={"topic": "{args.topic}"},
                              args={"topic": None})
    h = compose_build(tmp_path, parent, git_repo, child_files={"child.tg.json": child})
    h.state.args["topic"] = "volcanoes"
    assert h.engine.run() == 0
    prompts = [c.prompt for c in h.child_fake.calls if c.node_id == "w"]
    assert prompts and "volcanoes" in prompts[0]


def test_a_json_flow_node_validates_the_childs_result_against_its_contract(tmp_path, git_repo):
    child = {
        "name": "child",
        "nodes": [{"id": "v", "kind": "fake", "final": True,
                   "spec": {"outputs": [PASS], "readonly": True}}],
    }
    parent = parent_with_flow()
    parent["nodes"][0]["output"] = "json"
    parent["nodes"][0]["contract"] = "Verdict"
    h = compose_build(tmp_path, parent, git_repo, child_files={"child.tg.json": child})
    assert h.engine.run() == 0
    stored = json.loads((h.run_dir / "phases" / "sub" / "result.json").read_text(
        encoding="utf-8"))
    assert stored["verdict"] == "pass"


# ------------------------------------------------------------ caching & lineage


def test_an_unchanged_resume_serves_the_flow_node_from_cache(tmp_path, git_repo):
    h1 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": child_two_step()})
    assert h1.engine.run() == 0
    h2 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": child_two_step()},
                       run_dir=h1.run_dir, state=load_state(h1.run_dir))
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert h2.child_fake.calls == [], "cached parent node: the child never re-ran"
    assert len(_children_of(h1.run_dir)) == 1


def test_editing_the_child_flow_rebills_the_parent_and_starts_a_new_child_lineage(tmp_path, git_repo):
    h1 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": child_two_step()})
    assert h1.engine.run() == 0
    edited = child_two_step(final_output="a different answer")
    h2 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": edited},
                       run_dir=h1.run_dir, state=load_state(h1.run_dir))
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert (h1.run_dir / "phases" / "sub" / "result.txt").read_text(
        encoding="utf-8") == "a different answer"
    assert len(_children_of(h1.run_dir)) == 2, "old child lineage stays as evidence"


# ----------------------------------------------- torture: block, resume, budget


def test_a_child_gate_block_fails_the_parent_and_resume_reenters_without_rebilling(tmp_path, git_repo):
    child = {
        "name": "child",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["work"], "readonly": True}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["a"],
             "spec": {"outputs": [BLOCK, PASS], "readonly": True},
             "output": "json", "contract": "Verdict"},
            {"id": "b", "kind": "fake", "final": True, "depends_on": ["gate"],
             "spec": {"outputs": ["done"], "readonly": True}},
        ],
    }
    h1 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": child})
    assert h1.engine.run() == 3, "child exit 2 maps to a parent node FAILURE"
    rec = load_state(h1.run_dir).nodes["sub"]
    assert rec.status == "failed"
    assert "child gate blocked" in (rec.error or "") and "gate" in rec.error
    assert "children" in rec.error, "the error points at the child run dir"

    h2 = compose_build(tmp_path, parent_with_flow(), git_repo,
                       child_files={"child.tg.json": child},
                       child_fake=h1.child_fake,
                       run_dir=h1.run_dir, state=load_state(h1.run_dir))
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    calls = [c.node_id for c in h1.child_fake.calls]
    assert calls.count("a") == 1, "completed child work is NOT re-billed on re-entry"
    assert calls.count("gate") == 2, "the blocked gate re-ran (r5 blocked-on-resume)"
    assert load_state(h1.run_dir).nodes["sub"].status == "done"


def test_a_child_budget_trip_is_a_run_level_stop_not_a_node_failure(tmp_path, git_repo):
    child = {
        "name": "child",
        "nodes": [
            {"id": "s1", "kind": "fake", "spec": {"outputs": ["one"], "readonly": True}},
            {"id": "s2", "kind": "fake", "final": True, "depends_on": ["s1"],
             "spec": {"outputs": ["two"], "readonly": True}},
        ],
    }
    parent = parent_with_flow()
    parent["budget"] = {"max_agent_spawns": 1, "max_run_minutes": 10}
    h = compose_build(tmp_path, parent, git_repo, child_files={"child.tg.json": child})
    assert h.engine.run() == EXIT_BUDGET, "one wallet: the CHILD spent it, the RUN stopped"
    st = load_state(h.run_dir)
    assert st.token_spawns == 1, "the root wallet counted the child's spawn"
    assert st.nodes["sub"].status == "pending", "re-pended, never failed — resume continues"


# ------------------------------------------------- torture: one tree, one token


def test_a_child_writer_and_a_parent_writer_never_overlap_on_the_tree(tmp_path, git_repo):
    child = {
        "name": "child",
        "nodes": [{"id": "cw", "kind": "fake", "final": True,
                   "spec": {"outputs": ["c"], "sleep_s": 0.3,
                            "write_files": {"from-child.txt": "c\n"}}}],
    }
    parent = {
        "name": "parent",
        "nodes": [
            {"id": "pw", "kind": "fake",
             "spec": {"outputs": ["p"], "sleep_s": 0.3,
                      "write_files": {"from-parent.txt": "p\n"}}},
            {"id": "sub", "role": "work", "kind": "flow", "final": True,
             "spec": {"flow": "child.tg.json"}},
        ],
    }
    h = compose_build(tmp_path, parent, git_repo,
                      child_files={"child.tg.json": child}, max_workers=4)
    assert h.engine.run() == 0
    pw = next(c for c in h.fake.calls if c.node_id == "pw")
    cw = next(c for c in h.child_fake.calls if c.node_id == "cw")
    overlap = pw.started < cw.ended and cw.started < pw.ended
    assert not overlap, (
        "shared `tree` token must serialize a parent writer against a child "
        f"writer: parent [{pw.started:.3f},{pw.ended:.3f}] "
        f"child [{cw.started:.3f},{cw.ended:.3f}]")


# ------------------------------------------------------------- torture: cancel


def test_a_mid_run_cancel_stops_the_child_between_waves(tmp_path, git_repo):
    """cancel targets a RUNNING node (the engine unlinks stale markers at
    spawn start, r6 C3). The marker lands while the child's first wave is
    executing; the watcher translates it to the child's abort; the second
    wave never dispatches; the engine's existing marker handling records the
    parent node as cancelled, no retries."""
    import threading
    import time as _time

    child = {
        "name": "child",
        "nodes": [
            {"id": "slow", "kind": "fake",
             "spec": {"outputs": ["one"], "sleep_s": 1.2, "readonly": True}},
            {"id": "never", "kind": "fake", "final": True, "depends_on": ["slow"],
             "spec": {"outputs": ["two"], "readonly": True}},
        ],
    }
    h = compose_build(tmp_path, parent_with_flow(), git_repo,
                      child_files={"child.tg.json": child})
    phase = h.run_dir / "phases" / "sub"

    def drop_marker():
        _time.sleep(0.4)  # after the spawn started (and its stale-marker unlink)
        phase.mkdir(parents=True, exist_ok=True)
        (phase / "CANCELLED").write_text("now", encoding="utf-8")

    t = threading.Thread(target=drop_marker)
    t.start()
    code = h.engine.run()
    t.join()
    assert code == 3
    rec = load_state(h.run_dir).nodes["sub"]
    assert rec.status == "failed" and "cancelled" in (rec.error or "")
    called = [c.node_id for c in h.child_fake.calls]
    assert called == ["slow"], f"the second wave must never dispatch: {called}"


# ------------------------------------- finding 21: invisible to the flat readers


def test_child_dirs_are_invisible_to_gc_estimate_and_active(tmp_path, git_repo):
    from lockstep.estimate import estimate_flow
    from lockstep.gc import plan_gc
    from lockstep.taskgraph import TaskGraph

    h = compose_build(tmp_path, parent_with_flow(), git_repo,
                      child_files={"child.tg.json": child_two_step()})
    assert h.engine.run() == 0
    runs_root = h.run_dir.parent
    plan = plan_gc(runs_root)
    assert not any("children" in str(p) for p, _why in plan.candidates), (
        "gc must never plan a child apart from its parent")
    assert plan.kept >= 1, "the parent run itself is a normal, visible run"
    est = estimate_flow(TaskGraph.model_validate(parent_with_flow()),
                        runs_root, "test-flow-hash")
    assert est is not None, "estimate scans the flat level without tripping on children"
