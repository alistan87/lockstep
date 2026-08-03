"""Cost preflight: what a run will cost, from what prior runs of the same flow
actually cost, printed BEFORE anything is spent.

The engine estimates in its own honest units — agent tasks (token-costing
spawns) and wall time — which the cockpit proposal designates as the primary
columns because they are always present. Harness-reported tokens and dollars
stay with contrib/cost_report.py, where the envelope field maps live.
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep import EXIT_OK
from lockstep.cli import main
from lockstep.estimate import estimate_flow, render_estimate
from lockstep.state import load_state, write_state

from conftest import build

FLOW = {
    "name": "est",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"outputs": ["two"]}},
    ],
}


def _history(tmp_path, git_repo, flow=FLOW, times=1):
    for _ in range(times):
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 0
    return tmp_path / "runs", h.tg


# ------------------------------------------------------------ the estimate


def test_no_history_is_reported_not_guessed(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    est = estimate_flow(h.tg, tmp_path / "no-such-runs", "test-flow-hash")
    assert est.matched_runs == 0
    assert est.matched_by == "none"
    out = render_estimate(est)
    assert "no history" in out.lower()


def test_prior_spawns_are_reported_per_node(tmp_path, git_repo):
    runs_dir, tg = _history(tmp_path, git_repo)
    est = estimate_flow(tg, runs_dir, "test-flow-hash")
    assert est.matched_runs == 1
    assert est.matched_by == "flow_hash"
    by_id = {n.node_id: n for n in est.nodes}
    assert by_id["a"].spawns == 1
    assert by_id["b"].spawns == 1
    assert est.agent_tasks == 2


def test_repeated_history_is_aggregated(tmp_path, git_repo):
    runs_dir, tg = _history(tmp_path, git_repo, times=3)
    est = estimate_flow(tg, runs_dir, "test-flow-hash")
    assert est.matched_runs == 3
    assert est.agent_tasks == 2  # median per run, not the sum across runs


def test_a_node_without_history_is_marked_and_the_total_is_a_floor(tmp_path, git_repo):
    runs_dir, _tg = _history(tmp_path, git_repo)
    extended = {
        "name": "est",
        "nodes": [
            *FLOW["nodes"][:1],
            {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {"outputs": ["two"]}},
            {"id": "c", "kind": "fake", "final": True, "depends_on": ["b"],
             "spec": {"outputs": ["three"]}},
        ],
    }
    h = build(tmp_path, extended, git_repo)
    est = estimate_flow(h.tg, runs_dir, "test-flow-hash")
    by_id = {n.node_id: n for n in est.nodes}
    assert by_id["c"].runs == 0
    assert est.without_history == ["c"]
    out = render_estimate(est)
    assert "floor" in out.lower()
    assert "c" in out


def test_a_different_flow_definition_is_flagged_not_silently_used(tmp_path, git_repo):
    """Matching by name after the definition changed is still useful, but the
    reader must be told the numbers came from a different version."""
    runs_dir, tg = _history(tmp_path, git_repo)
    est = estimate_flow(tg, runs_dir, "some-other-flow-hash")
    assert est.matched_by == "flow_name"
    assert est.matched_runs == 1
    assert "different" in render_estimate(est).lower()


def test_only_token_costing_nodes_count_as_agent_tasks(tmp_path, git_repo):
    flow = {
        "name": "mixed",
        "nodes": [
            {"id": "s", "kind": "shell", "spec": {"cmd": ["git", "--version"]}},
            {"id": "a", "kind": "fake", "final": True, "depends_on": ["s"],
             "spec": {"outputs": ["x"]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    est = estimate_flow(h.tg, tmp_path / "runs", "test-flow-hash")
    by_id = {n.node_id: n for n in est.nodes}
    assert by_id["s"].token_costing is False
    assert by_id["a"].token_costing is True
    assert est.agent_tasks == 1
    assert est.spawns == 2


def test_failed_prior_attempts_are_counted(tmp_path, git_repo):
    """A node that needed a corrective re-spawn cost two tasks, and the next
    run should be told so."""
    flow = {
        "name": "retry",
        "nodes": [
            {"id": "n", "kind": "fake", "final": True, "output": "json",
             "contract": "StepResult",
             "spec": {"outputs": ["garbage", json.dumps({"status": "ok", "summary": "s"})]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    h.engine.run()
    est = estimate_flow(h.tg, tmp_path / "runs", "test-flow-hash")
    assert {n.node_id: n for n in est.nodes}["n"].spawns == 2


# ----------------------------------------------------------------- the CLI


def test_estimate_spends_nothing_and_creates_no_run(tmp_path, git_repo, capsys):
    flow_file = git_repo / "f.tg.json"
    flow_file.write_text(json.dumps(FLOW), encoding="utf-8")
    runs_dir = tmp_path / "cli-runs"
    code = main([
        "run", str(flow_file), "--estimate",
        "--runs-dir", str(runs_dir), "--repo-root", str(git_repo),
    ])
    assert code == EXIT_OK
    assert not runs_dir.exists(), "--estimate must not create a run dir"
    assert "estimate" in capsys.readouterr().out.lower()


def test_estimate_reads_the_lineage_it_is_given(tmp_path, git_repo, capsys):
    runs_dir, _tg = _history(tmp_path, git_repo)
    flow_file = git_repo / "f.tg.json"
    flow_file.write_text(json.dumps(FLOW), encoding="utf-8")
    code = main([
        "run", str(flow_file), "--estimate",
        "--runs-dir", str(runs_dir), "--repo-root", str(git_repo),
    ])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "1 prior run" in out
