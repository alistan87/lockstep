"""The layer-boundary instrument (OPEN-WORK item 14's prerequisite).

The engine dispatches in waves and `futures_wait` is a full barrier: a node
whose dependencies settled early waits behind the slowest node of its wave.
Event-driven dispatch (throughput proposal section 6) is deferred on a trigger
that names `kind:"timing"` lines as its evidence, but those lines recorded
tree ops only, so the gap the trigger asks about could never be seen. This is
the line that shows it: `op: "dispatch-wait"`, per dispatched node, the
milliseconds between its LAST dependency settling and its dispatch, with the
dependency named. Advisory like every timing line: no reader branches on it.
"""

from __future__ import annotations

import json

from lockstep.cli import main as lockstep_main

from conftest import build, calls_of, rebuild


def _events(run_dir):
    return [json.loads(ln) for ln in
            (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]


def _waits(run_dir):
    return {e["node"]: e for e in _events(run_dir)
            if e.get("kind") == "timing" and e.get("op") == "dispatch-wait"}


def _diamond(slow_s=0.6):
    # a and b start together; c is ready the moment a settles but is dispatched
    # only after b (the slow one) finishes the wave; d genuinely needs b. d lists
    # b FIRST so that "the last-listed dependency" would be the wrong answer:
    # `after` must come from settle time, not from declaration order.
    return {
        "name": "diamond",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["A"], "readonly": True}},
            {"id": "b", "kind": "fake", "spec": {"outputs": ["B"], "readonly": True, "sleep_s": slow_s}},
            {"id": "c", "kind": "fake", "depends_on": ["a"], "spec": {"outputs": ["C"], "readonly": True}},
            {"id": "d", "kind": "fake", "depends_on": ["b", "a"], "final": True,
             "spec": {"outputs": ["D"], "readonly": True}},
        ],
    }


def test_a_node_ready_early_records_the_wait_and_names_what_made_it_ready(tmp_path, git_repo):
    h = build(tmp_path, _diamond(), git_repo, max_workers=2)
    assert h.engine.run() == 0
    waits = _waits(h.run_dir)
    # Root nodes have no dependency to be ready after: no line, not a zero.
    assert "a" not in waits and "b" not in waits
    assert waits["c"]["after"] == "a"
    assert waits["c"]["ms"] >= 300, waits  # it sat behind b's 600 ms
    assert waits["d"]["after"] == "b"
    assert waits["d"]["ms"] < 300, waits    # b settling is what made d ready


def test_a_dependency_revalidated_in_place_is_measured_from_the_revalidation(tmp_path, git_repo):
    """On resume a done dependency is revalidated in place by `_settle`, in
    this process, moments before dispatch. That is when its dependents became
    dispatchable HERE, so the wait is measured from it — never from the
    previous process's `ended_at` string, and never folded into the
    dependency's own earlier attempt."""
    def flow(b_exit: int):
        return {
            "name": "resume",
            "nodes": [
                {"id": "a", "kind": "fake", "spec": {"outputs": ["A"], "readonly": True}},
                {"id": "b", "kind": "fake", "depends_on": ["a"], "final": True,
                 "spec": {"outputs": ["B"], "readonly": True, "exit_code": b_exit}},
            ],
        }
    h = build(tmp_path, flow(1), git_repo)
    assert h.engine.run() != 0
    assert h.state.nodes["b"].status == "failed"
    assert "b" in _waits(h.run_dir)  # measured in the first drive
    # The fixed flow: b re-runs (it failed), a is revalidated in place — it
    # settled in the FIRST drive, so this drive has no time to measure from.
    h2 = rebuild(tmp_path, flow(0), git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "a") == []  # the premise: a was revalidated in place, not re-run
    lines_b = [e for e in _events(h.run_dir) if e.get("kind") == "timing"
               and e.get("op") == "dispatch-wait" and e.get("node") == "b"]
    assert len(lines_b) == 2, lines_b  # one per drive
    second = lines_b[1]
    assert second["after"] == "a"
    assert second["ms"] < 300, second  # from this drive's revalidation, not drive 1's clock


def test_a_heal_round_measures_from_the_re_pend_not_from_the_first_attempt(tmp_path, git_repo):
    """root -> impl (heal target, slow) -> gate (BLOCK then PASS). After the
    round, impl's dependency `root` settled long ago; measuring impl's second
    dispatch from it would report impl's whole first attempt plus the gate as
    a "barrier wait" — a material gap manufactured by the cascade, which is
    the one thing the instrument must not fabricate (review 2026-09-20)."""
    from test_heal import BLOCK, PASS
    flow = {
        "name": "heal-wait",
        "nodes": [
            {"id": "root", "kind": "fake", "spec": {"outputs": ["R"], "readonly": True}},
            {"id": "impl", "kind": "fake", "depends_on": ["root"],
             "spec": {"outputs": ["did work"], "write_files": {"gen.txt": "generated\n"},
                      "sleep_s": 0.5}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "spec": {"outputs": [BLOCK, PASS], "readonly": True},
             "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["impl"], "rollback": True}},
            {"id": "after", "kind": "fake", "depends_on": ["gate"], "final": True,
             "spec": {"outputs": ["A"], "readonly": True}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    impl_lines = [e for e in _events(h.run_dir) if e.get("kind") == "timing"
                  and e.get("op") == "dispatch-wait" and e.get("node") == "impl"]
    assert len(impl_lines) == 2, impl_lines
    first, second = impl_lines
    assert first["after"] == "root" and first["ms"] < 300, first
    assert second["after"] == "heal round 1 of gate gate", second
    assert second["ms"] < 300, second  # NOT >= 500: the first attempt is not a wait


def test_status_sums_the_waits_and_names_the_worst(tmp_path, git_repo, capsys):
    h = build(tmp_path, _diamond(), git_repo, max_workers=2)
    assert h.engine.run() == 0
    assert lockstep_main(["status", str(h.run_dir)]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("dispatch wait:"))
    waits = _waits(h.run_dir)
    total = waits["c"]["ms"] + waits["d"]["ms"]
    assert line.startswith(f"dispatch wait: {total} ms over 2 node(s) (2 dispatches)")
    assert f"worst c {waits['c']['ms']} ms after a" in line


def test_status_says_nothing_when_no_wait_was_measured(tmp_path, git_repo, capsys):
    flow = {"name": "one", "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}, "final": True}]}
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    assert lockstep_main(["status", str(h.run_dir)]) == 0
    assert "dispatch wait" not in capsys.readouterr().out
