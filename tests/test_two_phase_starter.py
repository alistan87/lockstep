"""The `two-phase-remediation` starter, executed end to end.

The template exists to demonstrate one claim, so the claim is what is tested:
each phase's reviewer sees ITS OWN phase's change and nothing else, and that
stays true across a resume. With `worktree_diff` neither half holds — the
capture re-runs on every resume (shell nodes always do, SPEC §0.1.7) and by
then the tree contains the next phase's work, which is the failure that cost
the reporting consumer two full restarts.

The harness nodes are swapped for the fake executor and the pytest gate for a
canned Verdict, so this spends nothing. Everything that carries the claim — the
graph, the `node_diff` wiring, the declared scopes, the real probe, the real
`block_on_severity` gate — is the shipped file's own.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lockstep.state import load_state

from conftest import build

PY = sys.executable
FLOW = Path(__file__).resolve().parents[1] / "flows" / "starter" / "two-phase-remediation.tg.json"

PASS_VERDICT = json.dumps({"schema_version": "1.0", "verdict": "pass",
                           "reason": "canned: the real gate runs pytest", "findings": []})

TEST_FILE = "def test_bug():\n    assert reported_behaviour() == expected\n"
FIX_FILE = "def reported_behaviour():\n    return expected\n"


def offline_flow() -> dict:
    """The shipped graph, with the token-spending nodes doubled."""
    d = json.loads(FLOW.read_text(encoding="utf-8"))
    doubles = {
        "reproduce": {"outputs": ["wrote tests/test_bug.py; it fails"],
                      "write_files": {"tests/test_bug.py": TEST_FILE}},
        "review-repro": {"outputs": [[]], "readonly": True},
        "remediate": {"outputs": ["fixed the root cause in src/fix.py"],
                      "write_files": {"src/fix.py": FIX_FILE}},
        "review-fix": {"outputs": [[]], "readonly": True},
    }
    for n in d["nodes"]:
        if n["id"] in doubles:
            n["kind"] = "fake"
            keep = {k: v for k, v in n["spec"].items() if k == "writes"}
            n["spec"] = {**keep, **doubles[n["id"]]}
        elif n["id"] == "checks":
            n["spec"]["cmd"] = [PY, "-c", f"print({PASS_VERDICT!r})"]
    return d


def result_text(run_dir: Path, node: str) -> str:
    for name in ("result.txt", "result.json"):
        p = run_dir / "phases" / node / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise AssertionError(f"{node} left no result file")


def test_each_phase_is_reviewed_on_its_own_change(tmp_path, git_repo):
    h = build(tmp_path, offline_flow(), git_repo, args={"bug": "the export drops the last row"})
    assert h.engine.run() == 0, load_state(h.run_dir).nodes

    phase1 = result_text(h.run_dir, "repro-diff")
    phase2 = result_text(h.run_dir, "fix-diff")

    assert "tests/test_bug.py" in phase1, phase1
    assert "src/fix.py" not in phase1, "phase 1's reviewer must not see the fix"

    assert "src/fix.py" in phase2, phase2
    assert "tests/test_bug.py" not in phase2, (
        "phase 2's reviewer must not be shown the evidence test again — it existed "
        "before this step and after it, so it is not part of this step's change"
    )


def test_a_resume_cannot_contaminate_phase_one(tmp_path, git_repo):
    """The reported failure, reproduced against the template. `repro-diff` DOES
    re-run on the resume — every shell node does — but it re-reads the same two
    recorded trees, so its output is byte-identical and the reviewer above it
    keeps its cached result instead of re-billing against phase 2's tree."""
    from conftest import rebuild

    h = build(tmp_path, offline_flow(), git_repo, args={"bug": "the export drops the last row"})
    assert h.engine.run() == 0
    before = result_text(h.run_dir, "repro-diff")
    reviewed_at = load_state(h.run_dir).nodes["review-repro"].input_hash

    h2 = rebuild(tmp_path, offline_flow(), git_repo, h.run_dir,
                 args={"bug": "the export drops the last row"})
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0

    after = result_text(h.run_dir, "repro-diff")
    assert after == before, "the phase-1 capture moved under the reviewer"
    assert "src/fix.py" not in after
    st = load_state(h.run_dir)
    assert st.nodes["review-repro"].input_hash == reviewed_at, "the reviewer's input moved"
    assert [c.node_id for c in h2.fake.calls] == [], "no harness node re-billed on the resume"
