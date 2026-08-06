"""A6 — one cost band, one implementation: per-run totals in the estimator,
rendered as a range by both `--estimate` and the plan card."""

from __future__ import annotations

from lockstep.estimate import estimate_flow, render_estimate
from lockstep.state import PhaseRecord, RunState, new_run_dir, write_state
from lockstep.taskgraph import TaskGraph

FLOW = {
    "name": "band",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {}},
        {"id": "b", "kind": "shell", "final": True, "depends_on": ["a"],
         "spec": {"cmd": ["x"]}},
    ],
}


def _run_state(attempts: int) -> RunState:
    return RunState(
        flow_name="band",
        flow_hash="fh",
        format_version="1.0",
        args={},
        nodes={
            "a": PhaseRecord(node_id="a", role="work", kind="fake", status="done",
                             attempts=attempts,
                             started_at="2026-08-01T00:00:00+00:00",
                             ended_at="2026-08-01T00:01:00+00:00"),
            "b": PhaseRecord(node_id="b", role="work", kind="shell", status="done",
                             attempts=1,
                             started_at="2026-08-01T00:01:00+00:00",
                             ended_at="2026-08-01T00:01:30+00:00"),
        },
        started_at="2026-08-01T00:00:00.000000Z",
    )


def test_band_reflects_per_run_totals(tmp_path):
    runs = tmp_path / "runs"
    for attempts in (1, 3):
        write_state(new_run_dir(runs, "band"), _run_state(attempts))
    tg = TaskGraph.model_validate(FLOW)
    est = estimate_flow(tg, runs, "fh")
    assert est.matched_by == "flow_hash" and est.matched_runs == 2
    assert sorted(est.run_agent_tasks) == [1.0, 3.0]  # shell node spends no tokens
    assert est.tasks_band() == (1.0, 3.0)
    rendered = render_estimate(est)
    assert "1-3 agent tasks" in rendered
    assert "a range, never a forecast" in rendered


def test_band_counts_renamed_nodes_for_edited_definitions(tmp_path):
    """For a name-matched (edited) flow, "what those runs used" must include
    spend under node ids the current definition no longer has."""
    runs = tmp_path / "runs"
    state = _run_state(2)
    state.flow_hash = "old-definition"
    state.nodes["renamed-away"] = PhaseRecord(
        node_id="renamed-away", role="work", kind="fake", status="done", attempts=5,
        started_at="2026-08-01T00:02:00+00:00", ended_at="2026-08-01T00:03:00+00:00",
    )
    write_state(new_run_dir(runs, "band"), state)
    tg = TaskGraph.model_validate(FLOW)
    est = estimate_flow(tg, runs, "current-definition-hash")
    assert est.matched_by == "flow_name"
    assert est.run_agent_tasks == [7.0], "2 from 'a' + 5 from the renamed node"


def test_band_absent_without_history(tmp_path):
    tg = TaskGraph.model_validate(FLOW)
    est = estimate_flow(tg, tmp_path / "none", "fh")
    assert est.tasks_band() is None
    assert "range" not in render_estimate(est)
