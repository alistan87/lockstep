"""Test 4 (SPEC §13.1): gate adjudication."""

from __future__ import annotations

from lockstep.state import load_state

from conftest import PY, build

PASS = {"findings": [], "verdict": "pass", "reason": "clean"}
BLOCK = {
    "findings": [
        {"severity": "blocker", "category": "bug", "file": "x.py", "claim": "broken", "evidence": "line 3"}
    ],
    "verdict": "block",
    "reason": "it is broken",
}


def gate_flow(gate_outputs, gate_kind="fake", gate_spec=None):
    spec = gate_spec if gate_spec is not None else {"outputs": gate_outputs, "readonly": True}
    return {
        "name": "gated",
        "nodes": [
            {"id": "work", "kind": "fake", "spec": {"outputs": ["done work"]}},
            {
                "id": "gate", "role": "gate", "kind": gate_kind, "depends_on": ["work"],
                "spec": spec, "output": "json", "contract": "Verdict",
            },
            {"id": "after", "kind": "fake", "depends_on": ["gate"], "spec": {"outputs": ["A"]}, "final": True},
        ],
    }


def test_pass_proceeds(tmp_path, git_repo):
    h = build(tmp_path, gate_flow([PASS]), git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.verdicts["gate"] == "pass"
    assert st.nodes["after"].status == "done"


def test_block_terminates_branch_exit_2(tmp_path, git_repo):
    h = build(tmp_path, gate_flow([BLOCK]), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.verdicts["gate"].startswith("block: it is broken")
    assert st.nodes["gate"].status == "blocked"
    assert st.nodes["after"].status == "blocked"
    assert "it is broken" in st.nodes["after"].error


def test_invalid_verdict_is_block(tmp_path, git_repo):
    # Missing/invalid/non-conforming after the corrective re-spawn => terminal
    # BLOCK "no valid verdict emitted" (fail-closed), never healing.
    h = build(tmp_path, gate_flow(["not json at all", "still not json"]), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.verdicts["gate"] == "block: no valid verdict emitted"
    assert st.nodes["after"].status == "blocked"


def test_shell_gate_works_identically(tmp_path, git_repo):
    import json as _json

    script = f"import json; print(json.dumps({PASS!r}).replace(chr(39), chr(34)))"
    # Simpler: emit the verdict via json.dumps of a literal.
    script = "import json; print(json.dumps({'findings': [], 'verdict': 'pass', 'reason': 'clean'}))"
    f = gate_flow(None, gate_kind="shell", gate_spec={"cmd": [PY, "-c", script]})
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).verdicts["gate"] == "pass"


def test_shell_gate_invalid_verdict_zero_respawns(tmp_path, git_repo):
    # A4: shell is deterministic — no corrective re-spawn, immediately terminal.
    f = gate_flow(None, gate_kind="shell", gate_spec={"cmd": [PY, "-c", "print('garbage')"]})
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.verdicts["gate"] == "block: no valid verdict emitted"
    assert st.nodes["gate"].attempts == 1, "zero re-spawns for a shell gate"


def test_gate_timeout_is_named_not_no_valid_verdict(tmp_path, git_repo):
    # E6 (LESSONS-TO-MECHANISMS, lesson 20): "no valid verdict emitted" on a
    # timeout sent operators hunting a schema bug in a command that ran out of
    # window; heal correctly cannot fire (SPEC 9.4.3) but the reason must say
    # what actually happened and what to change.
    from conftest import PY

    f = {
        "name": "gt",
        "nodes": [
            {"id": "work", "kind": "fake", "spec": {"outputs": ["w"], "writes": []}},
            {"id": "gate", "role": "gate", "kind": "shell", "depends_on": ["work"],
             "timeout_s": 1, "retry": {"max": 0},
             "spec": {"cmd": [PY, "-c", "import time; time.sleep(15)"], "writes": []},
             "output": "json", "contract": "Verdict"},
            {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert "timed out after 1s" in st.verdicts["gate"]
    assert "raise timeout_s" in st.verdicts["gate"]
