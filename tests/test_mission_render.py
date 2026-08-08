"""Cockpit UX proposal T3.1: the view layer, finally under test.

`cockpit.ps1` remains the shipped default and correctness still lives there —
it is the path that reaches machines whose terminal nobody here controls. But
the view layer has never had a test, which is how `Wait-PaneProgram` came to be
called at line 766 and defined nowhere (F1), and how ACTIVITY came to drop the
structured half of every progress record (F6).

The last test in this file is the one that matters most: it parses the glossary
out of `cockpit.ps1` and requires it to match `mission_view.GLOSSARY`. Two
implementations of the domain expert's trust anchor are only acceptable if they
cannot drift apart quietly.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
sys.path.insert(0, str(CONTRIB))

import mission_view as mv  # noqa: E402

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def make_run(tmp_path: Path, nodes: dict, *, flow: dict | None = None,
             started: datetime | None = None, name: str = "demo") -> Path:
    run = tmp_path / "run"
    (run / "phases").mkdir(parents=True, exist_ok=True)
    state = {
        "flow_name": name,
        "started_at": (started or NOW - timedelta(minutes=14)).isoformat().replace("+00:00", "Z"),
        "nodes": nodes,
        "verdicts": {},
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if flow is not None:
        (run / "flow.tg.json").write_text(json.dumps(flow), encoding="utf-8")
    return run


def rec(status="pending", role="work", **kw):
    base = {"node_id": "x", "role": role, "kind": "harness", "status": status,
            "attempts": 1, "heal_round": 0}
    base.update(kw)
    return base


# ------------------------------------------------------------------ headline

def test_headline_counts_settled_and_running(tmp_path):
    run = make_run(tmp_path, {
        "a": rec("done"), "b": rec("done"), "c": rec("running"), "d": rec("pending"),
    })
    line = mv.headline(mv.read_json(run / "state.json"), None, now=NOW)
    assert line.startswith("step 3 of 4  -  running")
    assert "14 m" in line


def test_headline_prefers_the_loudest_state(tmp_path):
    run = make_run(tmp_path, {"a": rec("running"), "b": rec("blocked"), "c": rec("failed")})
    line = mv.headline(mv.read_json(run / "state.json"), None, now=NOW)
    assert "stopped with a problem" in line
    assert "needs you" not in line


def test_headline_reports_rework_rounds(tmp_path):
    run = make_run(tmp_path, {"a": rec("done", heal_round=2), "b": rec("pending")})
    assert "2 rework rounds" in mv.headline(mv.read_json(run / "state.json"), None, now=NOW)


# -------------------------------------------------------- steps to decision

DECIDE_FLOW = {
    "nodes": [
        {"id": "a"},
        {"id": "b", "depends_on": ["a"]},
        {"id": "ask", "role": "approval", "depends_on": ["b"]},
        {"id": "after", "depends_on": ["ask"]},
    ]
}


def test_steps_to_decision_counts_unfinished_ancestors(tmp_path):
    run = make_run(tmp_path, {
        "a": rec("done"), "b": rec("pending"),
        "ask": rec("pending", role="approval"), "after": rec("pending"),
    }, flow=DECIDE_FLOW)
    state = mv.read_json(run / "state.json")
    flow = mv.read_json(run / "flow.tg.json")
    # b is unfinished, plus the approval itself. `after` is downstream and does
    # not count: it is not something standing between the human and their turn.
    assert mv.steps_to_decision(state, flow) == 2
    assert "a decision is 2 steps away" in mv.headline(state, flow, now=NOW)


def test_steps_to_decision_says_next_when_only_the_approval_remains(tmp_path):
    run = make_run(tmp_path, {
        "a": rec("done"), "b": rec("done"),
        "ask": rec("blocked", role="approval"), "after": rec("pending"),
    }, flow=DECIDE_FLOW)
    state, flow = mv.read_json(run / "state.json"), mv.read_json(run / "flow.tg.json")
    assert mv.steps_to_decision(state, flow) == 1
    assert "your decision is next" in mv.headline(state, flow, now=NOW)


def test_steps_to_decision_is_none_without_a_flow_copy(tmp_path):
    run = make_run(tmp_path, {"ask": rec("pending", role="approval")})
    assert mv.steps_to_decision(mv.read_json(run / "state.json"), None) is None


def test_steps_to_decision_is_none_when_ambiguous(tmp_path):
    # Two approvals in one graph: the number would be ambiguous, and a
    # confidently wrong "2 steps away" is worse than no clause at all.
    flow = {"nodes": [{"id": "p", "role": "approval"}, {"id": "q", "role": "approval"}]}
    run = make_run(tmp_path, {
        "p": rec("pending", role="approval"), "q": rec("pending", role="approval"),
    }, flow=flow)
    assert mv.steps_to_decision(mv.read_json(run / "state.json"),
                                mv.read_json(run / "flow.tg.json")) is None


# ------------------------------------------------------------------ collapse

def test_finished_work_collapses_but_the_loud_minority_never_does(tmp_path):
    run = make_run(tmp_path, {
        "q1": rec("done"), "q2": rec("done"), "q3": rec("done"),
        "loud": rec("running"),
        "hurt": rec("failed"),
        "redone": rec("done", heal_round=1),
    })
    rows = mv.mission_rows(run)
    shown = {nid for nid, _ in rows if nid}
    assert shown == {"loud", "hurt", "redone"}
    assert any("3 finished" in text for _, text in rows)


def test_only_three_waiting_nodes_are_listed(tmp_path):
    run = make_run(tmp_path, {f"n{i}": rec("pending") for i in range(9)})
    rows = mv.mission_rows(run)
    assert len([nid for nid, _ in rows if nid]) == 3
    assert any("+ 6 more waiting" in text for _, text in rows)


def test_a_node_with_a_mission_note_is_never_collapsed(tmp_path):
    run = make_run(tmp_path, {"a": rec("done"), "b": rec("done")})
    (run / "phases" / "a").mkdir(parents=True)
    (run / "phases" / "a" / "mission.txt").write_text("read 40 files\n", encoding="utf-8")
    rows = mv.mission_rows(run)
    assert "a" in {nid for nid, _ in rows if nid}
    assert any("read 40 files" in text for _, text in rows)


def test_map_nodes_collapse_to_a_counter(tmp_path):
    run = make_run(tmp_path, {"m": rec("running", role="map", items={
        "i1": {"status": "done", "attempts": 1},
        "i2": {"status": "done", "attempts": 3},
        "i3": {"status": "running", "attempts": 1},
    })})
    text = [t for nid, t in mv.mission_rows(run) if nid == "m"][0]
    assert "2 of 3 checked" in text
    assert "1 redone" in text


def test_visible_nodes_is_exactly_the_drilldown_index(tmp_path):
    """Pressing `3` must select the third thing ON SCREEN.

    Asserted against literal expected ids, not against `mission_rows` — since
    `visible_nodes` is implemented as a filter over `mission_rows`, comparing
    the two restates the implementation and cannot fail. The rows equality is
    still checked, but only after the ids are pinned independently.
    """
    run = make_run(tmp_path, {
        "done1": rec("done"), "run1": rec("running"),
        "pend1": rec("pending"), "skip1": rec("skipped"), "hurt1": rec("failed"),
    })
    # done1 collapses, skip1 collapses; the rest are shown in state.json order.
    assert mv.visible_nodes(run) == ["run1", "pend1", "hurt1"]
    assert mv.visible_nodes(run) == [nid for nid, _ in mv.mission_rows(run) if nid]


def test_heal_round_is_read_from_the_record_not_the_baselines(tmp_path):
    # state.heal_baselines maps a gate id to a git TREE SHA. Reading it as a
    # counter throws on the first healed run; the counter is PhaseRecord.heal_round.
    run = make_run(tmp_path, {"t": rec("running", heal_round=1)},
                   flow={"nodes": [{"id": "g", "heal": {"max_rounds": 2, "targets": ["t"]}},
                                   {"id": "t"}]})
    text = [t for nid, t in mv.mission_rows(run) if nid == "t"][0]
    assert "sent back for rework (1 of 2)" in text


# ------------------------------------------------------------------ progress

@pytest.mark.parametrize("record,expected", [
    ({"pct": 40, "step": "2 of 5", "note": "reading"}, "[####------]  40%  step 2 of 5  reading"),
    ({"note": "just a note"}, "just a note"),
    ({"pct": 0, "note": "starting"}, "[----------]   0%  starting"),
    ({"pct": 100}, "[##########] 100%"),
    ({"message": "legacy field"}, "legacy field"),
])
def test_progress_renders_every_field_it_is_given(record, expected):
    assert mv.format_progress(record) == expected


def test_progress_invents_no_denominator():
    # No pct means no bar. A made-up progress bar is the fastest way to teach a
    # human that this view makes things up.
    assert "[" not in mv.format_progress({"step": "3", "note": "x"})


def test_progress_survives_a_bad_line():
    assert mv.format_progress("not json at all") == "not json at all"


def test_progress_clamps_rather_than_crashing():
    assert "100%" in mv.format_progress({"pct": 900})
    assert "0%" in mv.format_progress({"pct": -5})


# ------------------------------------------------------------------ activity

def test_activity_idle_lines_are_mechanical(tmp_path):
    blocked = make_run(tmp_path / "a", {"x": rec("blocked")})
    assert mv.activity_lines(blocked) == ["needs you - nothing is spending"]
    waiting = make_run(tmp_path / "b", {"x": rec("pending")})
    assert mv.activity_lines(waiting) == ["waiting - nothing is spending"]
    finished = make_run(tmp_path / "c", {"x": rec("done")})
    assert mv.activity_lines(finished) == ["segment done - nothing is spending"]


def test_activity_falls_back_to_stdout_growth(tmp_path):
    # progress.jsonl is written by the AGENT on instruction. A harness that
    # ignores it must not leave the pane showing only a clock.
    run = make_run(tmp_path, {"work": rec("running")})
    phase = run / "phases" / "work"
    phase.mkdir(parents=True)
    (phase / "stdout.log").write_text("x" * 2048, encoding="utf-8")
    lines = mv.activity_lines(run)
    assert "still producing output" in lines[1]
    assert "2.0 KB" in lines[1]


def test_activity_says_so_when_there_is_nothing_at_all(tmp_path):
    run = make_run(tmp_path, {"work": rec("running")})
    (run / "phases" / "work").mkdir(parents=True)
    assert "no progress reported yet" in mv.activity_lines(run)[1]


# -------------------------------------------------------------------- labels

def test_labels_replace_node_ids(tmp_path):
    run = make_run(tmp_path, {"preflight": rec("running")})
    (run / "flow.labels.json").write_text(
        json.dumps({"nodes": {"preflight": "checking the plan is safe to apply"}}),
        encoding="utf-8")
    text = [t for nid, t in mv.mission_rows(run) if nid == "preflight"][0]
    assert text.startswith("checking the plan is safe to apply")
    assert "preflight" not in text


def test_missing_or_broken_labels_fall_back_to_ids(tmp_path):
    run = make_run(tmp_path, {"preflight": rec("running")})
    assert [t for nid, t in mv.mission_rows(run) if nid][0].startswith("preflight")
    (run / "flow.labels.json").write_text("{ not json", encoding="utf-8")
    assert [t for nid, t in mv.mission_rows(run) if nid][0].startswith("preflight")


# ------------------------------------------------------------------ drilldown

def test_node_detail_shows_the_error_and_where_it_lives(tmp_path):
    run = make_run(tmp_path, {"work": rec("failed", error="provider said 429")})
    phase = run / "phases" / "work"
    phase.mkdir(parents=True)
    (phase / "stderr.log").write_text("boom", encoding="utf-8")
    body = "\n".join(mv.node_detail(run, "work"))
    assert "stopped with a problem" in body
    assert "provider said 429" in body
    assert "stderr.log" in body


def test_node_detail_refuses_an_unknown_step(tmp_path):
    run = make_run(tmp_path, {"work": rec("done")})
    assert "no such step" in mv.node_detail(run, "nope")[0]


# ---------------------------------------------------------------- robustness

def test_an_unreadable_run_dir_never_raises(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert mv.mission_lines(empty) == ["(reading state...)"]
    assert mv.activity_lines(empty) == ["(reading state...)"]
    assert mv.newest_run(tmp_path / "absent") is None


def test_a_half_written_state_file_never_raises(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json").write_text('{"nodes": {"a": {"stat', encoding="utf-8")
    assert mv.mission_lines(run) == ["(reading state...)"]


# ------------------------------------------------------------ the cost panel

COST_FIELDS = """\
[claude]
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"
cost = "total_cost_usd"
"""


def envelope(cost: float, model: str = "claude-opus-5") -> str:
    return json.dumps({
        "type": "result", "usage": {"input_tokens": 10, "output_tokens": 5},
        "total_cost_usd": cost, "modelUsage": {model: {"costUSD": cost}},
        "result": "OK",
    })


def make_cost_run(tmp_path) -> Path:
    flow = {"nodes": [
        {"id": "plan", "role": "work", "kind": "harness"},
        {"id": "impl", "role": "work", "kind": "harness", "depends_on": ["plan"]},
        {"id": "audit", "role": "gate", "kind": "harness", "depends_on": ["plan"]},
        {"id": "ship", "role": "work", "kind": "shell", "depends_on": ["impl", "audit"]},
    ]}
    run = make_run(tmp_path, {
        "plan": rec("done"),
        "impl": rec("done", attempts=2),
        "audit": rec("done"),
        "ship": rec("done", kind="shell"),
    }, flow=flow)
    for node, cost in (("plan", 0.10), ("audit", 0.20)):
        d = run / "phases" / node
        d.mkdir(parents=True, exist_ok=True)
        (d / "argv.json").write_text(json.dumps(["claude", "-p"]), encoding="utf-8")
        (d / "stdout.log").write_text(envelope(cost), encoding="utf-8")
    impl = run / "phases" / "impl"
    impl.mkdir(parents=True, exist_ok=True)
    (impl / "argv.json").write_text(json.dumps(["claude", "-p"]), encoding="utf-8")
    (impl / "stdout-attempt1.log").write_text(envelope(0.30), encoding="utf-8")
    (impl / "stdout.log").write_text(envelope(0.40), encoding="utf-8")
    return run


@pytest.fixture()
def cost_cwd(tmp_path, monkeypatch):
    """Pin the field map: load_field_maps(None) prefers ./cost-fields.toml, and
    the developer machine may carry a local contrib/cost-fields.toml."""
    (tmp_path / "cost-fields.toml").write_text(COST_FIELDS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cost_lines_history_counts_every_attempt(tmp_path, cost_cwd):
    run = make_cost_run(tmp_path)
    text = "\n".join(mv.cost_lines(run, mode="history", now=NOW))
    assert "$1.00" in text                      # 0.10 + 0.30 + 0.40 + 0.20
    assert "history: every attempt is counted" in text
    assert "claude-opus-5" in text
    assert "↻1" in text                         # impl retried once
    # per-attempt history under the retried node, kept attempt last
    assert "attempt 1  claude-opus-5  $0.30  (superseded)" in text
    assert "kept  claude-opus-5  $0.40" in text


def test_cost_lines_head_counts_kept_attempts_only(tmp_path, cost_cwd):
    run = make_cost_run(tmp_path)
    text = "\n".join(mv.cost_lines(run, mode="head", now=NOW))
    assert "$0.70" in text                      # 0.10 + 0.40 + 0.20 — no attempt 1
    assert "head: kept attempts only" in text
    assert "(superseded)" not in text           # sub-lines are history-mode only


def test_cost_lines_layers_parallel_nodes_with_a_rail(tmp_path, cost_cwd):
    run = make_cost_run(tmp_path)
    lines = mv.cost_lines(run, mode="history", now=NOW)
    impl_line = next(ln for ln in lines if " impl " in ln)
    audit_line = next(ln for ln in lines if " audit " in ln)
    assert impl_line.lstrip().startswith("┌")   # impl ∥ audit: one layer, fanned
    assert audit_line.lstrip().startswith("└")
    ship_line = next(ln for ln in lines if " ship " in ln)
    assert "$" not in ship_line                 # shell node: never a fake dollar


def test_cost_lines_without_a_flow_copy_degrades_to_a_flat_list(tmp_path, cost_cwd):
    run = make_cost_run(tmp_path)
    (run / "flow.tg.json").unlink()
    lines = mv.cost_lines(run, mode="history", now=NOW)
    assert sum(1 for ln in lines if " impl " in ln) >= 1  # still rendered


def test_cost_lines_never_raise_on_an_empty_dir(tmp_path, cost_cwd):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert mv.cost_lines(empty) == ["(reading state...)"]


def test_topo_layers_orders_dependents_after_dependencies():
    flow = {"nodes": [
        {"id": "a"}, {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]}, {"id": "d", "depends_on": ["b", "c"]},
    ]}
    assert mv.topo_layers(flow, ["a", "b", "c", "d"]) == [["a"], ["b", "c"], ["d"]]
    # nodes the flow does not know come back as a final layer, state order
    assert mv.topo_layers(None, ["x", "y"]) == [["x", "y"]]


# ------------------------------------------------------ the anti-drift test

def test_the_glossary_matches_cockpit_ps1():
    """Two implementations of the trust anchor, pinned to each other.

    The glossary is the domain expert's contract: `blocked` means "needs you"
    and nothing else, in every surface. A PowerShell copy and a Python copy that
    disagree would show one word in the pane and another on the page, and the
    guide tells them that when two surfaces disagree, MISSION is right — which
    stops being a usable instruction the moment there are two MISSIONs.
    """
    text = (CONTRIB / "cockpit.ps1").read_text(encoding="utf-8")
    block = re.search(r"\$script:Glossary\s*=\s*@\{(.*?)\}", text, re.DOTALL)
    assert block, "cockpit.ps1 no longer declares $script:Glossary"
    pairs = dict(re.findall(r"'([a-z]+)'\s*=\s*'([^']*)'", block.group(1)))
    assert pairs == mv.GLOSSARY
