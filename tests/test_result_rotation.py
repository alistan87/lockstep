"""Result files rotate per attempt like the other artifacts (r5 A4 pattern).

The driver persists each completed node's validated result as
phases/<node>/result.json|txt (store.write_result) — the SAME path the
executors read as the child's file-first result channel (SPEC §8.3). Without
rotation, any re-execution in the same phase dir (retry, heal round, resume of
a blocked gate, shell always-re-run) returned the STALE previous result and
ignored the fresh run's output: a shell heal gate could never pass after its
first block, and fix-then-resume re-blocked forever. Found by the starter-flow
adversarial review (2026-08-01), proven here offline."""

from __future__ import annotations

import json

from lockstep.registry import ExecutorStanza
from lockstep.state import load_state

from conftest import PY, build, make_config

# Shell gate: blocks until a marker file exists at the repo root, then passes.
MARKER_GATE = (
    "import json, pathlib\n"
    "ok = pathlib.Path('fixed.marker').exists()\n"
    "v = {'findings': [], 'verdict': 'pass' if ok else 'block',\n"
    "     'reason': 'marker present' if ok else 'marker missing'}\n"
    "print(json.dumps(v))\n"
)


def gate_flow() -> dict:
    return {
        "name": "resume-gate",
        "nodes": [
            {"id": "work", "kind": "fake", "spec": {"outputs": ["did work"]}},
            {"id": "gate", "role": "gate", "kind": "shell", "depends_on": ["work"],
             "spec": {"cmd": [PY, "-c", MARKER_GATE]},
             "output": "json", "contract": "Verdict"},
            {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }


def test_blocked_shell_gate_passes_on_resume_after_fix(tmp_path, git_repo):
    from conftest import rebuild

    h = build(tmp_path, gate_flow(), git_repo)
    assert h.engine.run() == 2  # gate blocks: marker missing
    assert load_state(h.run_dir).verdicts["gate"].startswith("block")
    # The operator fixes the condition and resumes — the re-executed gate's
    # FRESH stdout must win over the previously persisted result.json.
    (git_repo / "fixed.marker").write_text("fixed", encoding="utf-8")
    h2 = rebuild(tmp_path, gate_flow(), git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    st = load_state(h2.run_dir)
    assert st.verdicts["gate"] == "pass"
    assert st.nodes["after"].status == "done"
    # Forensics: the blocked attempt's result was rotated, not lost.
    pd = h2.store.phase_dir("gate")
    assert (pd / "result-attempt1.json").exists()
    assert json.loads((pd / "result-attempt1.json").read_text(encoding="utf-8"))["verdict"] == "block"


# Harness: attempt 1 writes result.txt AND fails; attempt 2 writes nothing and
# answers on stdout. The final result must be attempt 2's stdout, not the
# stale file-channel leftover from attempt 1.
FLAKY_STALE_RESULT = (
    "import sys, pathlib\n"
    "pd = pathlib.Path(sys.argv[1])\n"
    "marker = pd / 'marker'\n"
    "if not marker.exists():\n"
    "    marker.write_text('1')\n"
    "    (pd / 'result.txt').write_text('stale-from-attempt-1')\n"
    "    sys.exit(1)\n"
    "print('fresh-from-attempt-2')\n"
)


def test_harness_retry_does_not_resurrect_prior_attempt_result(tmp_path, git_repo):
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", FLAKY_STALE_RESULT, "{phase_dir}"]))
    f = {
        "name": "retry-result",
        # Explicit retry: attempt 1 HAS a result (the stale file), so the M4
        # auto-retry branch (empty result) doesn't fire — without this the
        # node would take default_retry's 60s backoff and stall the suite.
        "nodes": [{"id": "n", "kind": "harness", "spec": {"task": "t"},
                   "retry": {"max": 1, "backoff_ms": 0},
                   "output": "text", "final": True}],
    }
    h = build(tmp_path, f, git_repo, config=config)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["n"]
    assert open(rec.result_path, encoding="utf-8").read().strip() == "fresh-from-attempt-2"
    pd = h.store.phase_dir("n")
    assert (pd / "result-attempt1.txt").exists()  # attempt 1's channel preserved
