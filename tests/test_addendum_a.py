"""ADDENDUM-A (pi hooks) lockstep-side items: node-identity env vars (A.7.1),
{phase_dir} argv substitution (A.7.4), and the verdict-file gate path proven
offline without a model (A.7.3 + A.7.5)."""

from __future__ import annotations

import json

from lockstep.registry import ExecutorStanza
from lockstep.state import load_state

from conftest import PY, build, make_config

PRINT_ENV = (
    "import os, json; print(json.dumps({k: os.environ.get(k, '') for k in ("
    "'LOCKSTEP_NODE_ID', 'LOCKSTEP_ROLE', 'LOCKSTEP_WORKSPACE_SCOPE', "
    "'LOCKSTEP_VERDICT_FILE', 'LOCKSTEP_PHASE_DIR')}))"
)


def test_node_identity_env_vars(tmp_path, git_repo):
    f = {
        "name": "env",
        "nodes": [
            {"id": "probe", "kind": "shell", "spec": {"cmd": [PY, "-c", PRINT_ENV]},
             "output": "text", "final": True}
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    env = json.loads(open(load_state(h.run_dir).nodes["probe"].result_path, encoding="utf-8").read())
    assert env["LOCKSTEP_NODE_ID"] == "probe"
    assert env["LOCKSTEP_ROLE"] == "work"
    assert env["LOCKSTEP_WORKSPACE_SCOPE"].endswith("repo")  # resolved spec.cwd
    assert env["LOCKSTEP_VERDICT_FILE"].endswith("verdicts.jsonl")
    assert env["LOCKSTEP_PHASE_DIR"]


def test_phase_dir_argv_substitution(tmp_path, git_repo):
    # A.3.4 mechanism: e.g. pi --session-dir {phase_dir}. The placeholder
    # expands at spawn but stays intact in the fingerprint (run-specific paths
    # are excluded from input_hash).
    script = "import sys, pathlib; pathlib.Path(sys.argv[1], 'result.txt').write_text('via-argv-phase-dir')"
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", script, "{phase_dir}"]))
    f = {
        "name": "pd",
        "nodes": [{"id": "n", "kind": "harness", "spec": {"task": "t"}, "output": "text", "final": True}],
    }
    h = build(tmp_path, f, git_repo, config=config)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["n"]
    assert open(rec.result_path, encoding="utf-8").read() == "via-argv-phase-dir"
    # Fingerprint keeps the placeholder, not the expanded path.
    ex = h.engine.registry.get("harness")
    ctx = h.engine._render_ctx(h.tg.node("n"), h.store.phase_dir("n"))
    parts = ex.plan(h.tg.node("n"), ctx).fingerprint_parts
    argv_part = next(p for p in parts if p.startswith("argv:"))
    assert "{phase_dir}" in argv_part and str(h.run_dir) not in argv_part


VERDICT_GATE_SCRIPT = (
    "import os, json, pathlib\n"
    "vf = pathlib.Path(os.environ['LOCKSTEP_PHASE_DIR']).parent / 'impl' / 'verdicts.jsonl'\n"
    "records = []\n"
    "if vf.exists():\n"
    "    records = [json.loads(l) for l in vf.read_text().splitlines() if l.strip()]\n"
    "if records:\n"
    "    findings = [{'severity': 'blocker', 'category': 'in-session-block', 'file': r['node_id'],\n"
    "                 'claim': r['reason'], 'evidence': 'tool ' + r['tool'] + ' digest ' + r['input_digest']}\n"
    "                for r in records]\n"
    "    print(json.dumps({'findings': findings, 'verdict': 'block',\n"
    "                      'reason': str(len(records)) + ' in-session enforcement block(s)'}))\n"
    "else:\n"
    "    print(json.dumps({'findings': [], 'verdict': 'pass', 'reason': 'no enforcement blocks'}))\n"
)


def verdict_flow(phase_files: dict) -> dict:
    # A.3.3 as pure convention: the enforcement layer wrote verdicts.jsonl into
    # the work node's phase dir; a DETERMINISTIC shell gate reads it and emits
    # Verdict — machine checks before model judgment, zero driver changes.
    return {
        "name": "verdict-gated",
        "nodes": [
            {"id": "impl", "kind": "fake",
             "spec": {"outputs": ["did work"], "write_phase_files": phase_files}},
            {"id": "gate", "role": "gate", "kind": "shell", "depends_on": ["impl"],
             "spec": {"cmd": [PY, "-c", VERDICT_GATE_SCRIPT]},
             "output": "json", "contract": "Verdict"},
            {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }


def test_verdict_file_gate_blocks(tmp_path, git_repo):
    record = json.dumps({"ts": "2026-07-26T00:00:00Z", "node_id": "impl", "tool": "write",
                         "reason": "path escapes workspace scope", "input_digest": "abc123"})
    h = build(tmp_path, verdict_flow({"verdicts.jsonl": record + "\n"}), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.verdicts["gate"].startswith("block: 1 in-session enforcement block(s)")
    assert st.nodes["after"].status == "blocked"


def test_verdict_file_gate_passes_when_clean(tmp_path, git_repo):
    h = build(tmp_path, verdict_flow({}), git_repo)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).verdicts["gate"] == "pass"
