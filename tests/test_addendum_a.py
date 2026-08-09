"""ADDENDUM-A (pi hooks) lockstep-side items: node-identity env vars (A.7.1),
{phase_dir} argv substitution (A.7.4), and the verdict-file gate path proven
offline without a model (A.7.3 + A.7.5)."""

from __future__ import annotations

import json
from pathlib import Path

from lockstep.registry import ExecutorStanza
from lockstep.state import load_state

from conftest import PY, build, make_config

PRINT_ENV = (
    "import os, json; print(json.dumps({k: os.environ.get(k, '') for k in ("
    "'LOCKSTEP_NODE_ID', 'LOCKSTEP_ROLE', 'LOCKSTEP_WORKSPACE_SCOPE', "
    "'LOCKSTEP_VERDICT_FILE', 'LOCKSTEP_PHASE_DIR', 'LOCKSTEP_CONTRACT', "
    "'LOCKSTEP_WRITE_SCOPE', 'LOCKSTEP_REPO_ROOT')}))"
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
    assert env["LOCKSTEP_CONTRACT"] == ""  # no contract on this node
    # The two the scope guard cannot work without. WRITE_SCOPE is the real
    # write boundary (spec.writes), empty here because this node declares none;
    # REPO_ROOT is what its relative globs resolve against. A guard that
    # resolves them against cwd instead silently blocks nothing the moment a
    # node sets spec.cwd — which is how it shipped the first time.
    assert env["LOCKSTEP_WRITE_SCOPE"] == ""
    assert Path(env["LOCKSTEP_REPO_ROOT"]).resolve() == Path(git_repo).resolve()


def test_contract_env_var_names_the_node_contract(tmp_path, git_repo):
    # A.3.2: LOCKSTEP_CONTRACT lets an extension pick the submit_result schema
    # matching the node's envelope (built-in names resolve; unknown degrades).
    emit = ("import os, json; print(json.dumps({'findings': [], 'verdict': 'pass',"
            " 'reason': os.environ['LOCKSTEP_CONTRACT']}))")
    f = {
        "name": "contract-env",
        "nodes": [
            {"id": "n", "kind": "shell", "spec": {"cmd": [PY, "-c", emit]},
             "output": "json", "contract": "Verdict", "final": True}
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    out = json.loads(open(load_state(h.run_dir).nodes["n"].result_path, encoding="utf-8").read())
    assert out["reason"] == "Verdict"


FLAKY_WITH_VERDICT = (
    "import sys, pathlib\n"
    "pd = pathlib.Path(sys.argv[1])\n"
    "marker = pd / 'marker'\n"
    "if not marker.exists():\n"
    "    marker.write_text('1')\n"
    "    # simulate an in-session enforcement block on the failing attempt\n"
    "    (pd / 'verdicts.jsonl').write_text('{\"stale\": true}\\n')\n"
    "    sys.exit(1)\n"
    "(pd / 'result.txt').write_text('ok-after-retry')\n"
)


def test_verdict_file_rotates_per_attempt(tmp_path, git_repo):
    # A.3.3 lifecycle: the gate must read only the FINAL attempt's in-session
    # blocks. Attempt 1 records a block and fails (no result + exit 1, so the
    # M4 auto-retry re-spawns — rotation runs at the top of execute() for EVERY
    # re-invocation); the retry succeeds cleanly. The stale record must rotate
    # away with the other per-attempt artifacts, or a downstream verdict-file
    # gate would block a node that self-corrected.
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", FLAKY_WITH_VERDICT, "{phase_dir}"]))
    f = {
        "name": "rot",
        "nodes": [{"id": "n", "kind": "harness", "spec": {"task": "t"},
                   "output": "text", "final": True}],
    }
    h = build(tmp_path, f, git_repo, config=config)
    assert h.engine.run() == 0
    pd = h.store.phase_dir("n")
    assert not (pd / "verdicts.jsonl").exists()          # final attempt was clean
    assert (pd / "verdicts-attempt1.jsonl").exists()      # forensics preserved


SHELL_FLAKY_WITH_VERDICT = (
    "import os, sys, pathlib\n"
    "pd = pathlib.Path(os.environ['LOCKSTEP_PHASE_DIR'])\n"
    "marker = pd / 'marker'\n"
    "if not marker.exists():\n"
    "    marker.write_text('1')\n"
    "    (pd / 'verdicts.jsonl').write_text('{\"stale\": true}\\n')\n"
    "    sys.exit(1)\n"
    "print('ok-after-retry')\n"
)


def test_shell_verdict_file_rotates_per_attempt(tmp_path, git_repo):
    # Same lifecycle guarantee for the shell executor's rotation loop.
    f = {
        "name": "rot-shell",
        "nodes": [{"id": "n", "kind": "shell",
                   "spec": {"cmd": [PY, "-c", SHELL_FLAKY_WITH_VERDICT]},
                   "retry": {"max": 1}, "output": "text", "final": True}],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    pd = h.store.phase_dir("n")
    assert not (pd / "verdicts.jsonl").exists()
    assert (pd / "verdicts-attempt1.jsonl").exists()


def test_doctor_expands_phase_dir_placeholder(tmp_path):
    # A stanza like `pi --session-dir {phase_dir}` must probe with a real dir:
    # doctor exists to catch flag drift, not to inject a literal placeholder.
    from lockstep.doctor import _probe_once

    seen = tmp_path / "seen.txt"
    script = ("import sys, pathlib; pathlib.Path(sys.argv[1], 'result.txt').write_text('OK'); "
              f"pathlib.Path(r'{seen}').write_text(sys.argv[1])")
    stanza = ExecutorStanza(argv=[PY, "-c", script, "{phase_dir}", "{prompt}"])
    ok, _msg = _probe_once("x", stanza, [], tmp_path, print)
    assert ok
    assert seen.read_text() == str(tmp_path)  # expanded, not the literal "{phase_dir}"


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


# ---------------------------------------- note 3: readonly on pi, resolved

PI_READONLY_ARGV = ["--tools", "read,grep,find,ls,submit_result"]


def _readonly_flow() -> dict:
    return {
        "name": "pi-readonly",
        "nodes": [{"id": "review", "kind": "harness", "final": True,
                   "spec": {"task": "review it", "readonly": True}}],
    }


def _codes_for(stanza) -> list[str]:
    from lockstep.executors.harness import HarnessExecutor
    from lockstep.registry import Registry
    from lockstep.taskgraph import TaskGraph, verify_flow

    cfg = make_config(pi=stanza)
    reg = Registry()
    reg.register(HarnessExecutor(config=cfg, repo_root="."))
    return [i.code for i in verify_flow(TaskGraph.model_validate(_readonly_flow()),
                                        registry=reg, config=cfg)]


def test_a_readonly_pi_node_used_to_be_a_verification_error():
    """ADDENDUM-A note 3 parked this: an extension `tool_call` gate could be
    pi's readonly enforcement, but §6.11 requires ARGV-VISIBLE enforcement, and
    pi had none — so readonly nodes on pi were a verification error and
    reviewers could not fan out in parallel there."""
    assert "readonly-unenforced" in _codes_for(ExecutorStanza(argv=[PY, "-c", "pass"]))


def test_pi_tools_allowlist_is_argv_visible_readonly_enforcement():
    """pi 0.83.0 takes `--tools`, an allowlist applied to built-in, extension
    and custom tools. That IS argv-visible enforcement, so §6.11 is satisfied
    and `spec.readonly: true` is legal on pi. Verified against live pi with a
    control: unrestricted the model created the file, with the allowlist it did
    not — while still replying "DONE", which is why the driver validates
    independently of what the model claims."""
    codes = _codes_for(ExecutorStanza(argv=[PY, "-c", "pass"],
                                      readonly_argv=PI_READONLY_ARGV))
    assert "readonly-unenforced" not in codes


def test_the_allowlist_keeps_the_extensions_tool():
    """`--tools` is an allowlist across EXTENSION tools too, so a bare readonly
    list would silently disable the guard extension's `submit_result` — the one
    thing A.3.2 exists to provide. Naming a tool that is not installed is
    harmless (checked against live pi), so it belongs in the list either way."""
    assert "submit_result" in PI_READONLY_ARGV[1]
    example = (Path(__file__).resolve().parents[1] / "lockstep.toml.example").read_text(
        encoding="utf-8")
    assert 'readonly_argv = ["--tools", "read,grep,find,ls,submit_result"]' in example
