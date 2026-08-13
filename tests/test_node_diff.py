"""Review what a STEP changed, not what the tree currently holds.

Consumer report 2026-08-13, item 1. A multi-phase flow shaped
`capture-diff → review → gate → remediate → capture-diff → review → gate`
re-reviewed its FIRST phase against a contaminated tree, twice, at a cost of
two full restarts. The reported cause (a whole-tree fingerprint invalidating
unrelated nodes) is not the mechanism: `capture-diff` is a shell node, shell
nodes always re-run on resume (SPEC §0.1.7), and `worktree_diff` captures the
tree AS IT IS NOW — which by phase 2 contains phase 2's work. The reviewer's
prompt embeds that text, so its input hash legitimately moves and it
legitimately re-runs, against evidence that is no longer about phase 1.

Nothing in that chain is a bug; the missing piece is a way to ask "what did
node X change" and get the same answer forever. The engine already computes
both git tree objects for any node that declares `spec.writes` and holds the
tree token — the baseline before it runs, and the tree it left. It just threw
them away. Persisting them costs nothing and makes `node_diff` deterministic.
"""

from __future__ import annotations

import subprocess
import sys

from lockstep.state import load_state

from conftest import build, git

PY = sys.executable


def flow(node_id="impl", writes=("src",), files=None):
    spec = {"outputs": ["ok"],
            "write_files": {"src/a.py": "phase one\n"} if files is None else files}
    if writes is not None:
        spec["writes"] = list(writes)
    return {"name": "nd", "nodes": [
        {"id": node_id, "kind": "fake", "final": True, "spec": spec}]}


def probe(repo, *args):
    p = subprocess.run(
        [PY, "-m", "lockstep.probes.node_diff", *args],
        cwd=str(repo), capture_output=True, encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout


# ------------------------------------------------------------------ recording


def test_a_scoped_node_records_both_trees(tmp_path, git_repo):
    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["impl"]
    assert rec.tree_before and rec.tree_after
    assert rec.tree_before != rec.tree_after, "the node wrote a file"


def test_an_unscoped_node_records_nothing(tmp_path, git_repo):
    """No declared scope means no baseline is taken (a snapshot is O(tree
    bytes)); promising a diff we never measured would be worse than none."""
    h = build(tmp_path, flow(writes=None), git_repo)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["impl"]
    assert rec.tree_before is None and rec.tree_after is None


def test_a_quarantined_node_records_no_after_tree(tmp_path, git_repo):
    """The violating attempt was rolled back — there is no tree it left. Its
    evidence is the preserved patch, which already exists."""
    h = build(tmp_path, flow(files={"docs/leak.md": "x"}), git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["impl"]
    assert rec.tree_before and rec.tree_after is None


# ------------------------------------------------------------------ the probe


def test_the_probe_shows_what_that_node_changed(tmp_path, git_repo):
    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    rc, out = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    assert rc == 0
    assert "src/a.py" in out
    assert "phase one" in out


def test_a_later_phase_cannot_contaminate_it(tmp_path, git_repo):
    """The whole point. After the run, more work lands in the tree — exactly
    what `conditional-remediation` did to the consumer's phase-1 evidence — and
    the recorded answer does not move."""
    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    rc, first = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    (git_repo / "src" / "later.py").write_text("a whole other phase\n", encoding="utf-8")
    (git_repo / "src" / "a.py").write_text("rewritten by a later phase\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    rc2, second = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    assert (rc, rc2) == (0, 0)
    assert first == second
    assert "later.py" not in second
    assert "a whole other phase" not in second


def test_the_probe_finds_its_run_dir_from_the_phase_dir_env(tmp_path, git_repo, monkeypatch):
    """A flow node does not know its own run dir — LOCKSTEP_PHASE_DIR is
    already exported to every spawn, and the run dir is its grandparent."""
    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    env_phase = h.run_dir / "phases" / "reviewer"
    p = subprocess.run(
        [PY, "-m", "lockstep.probes.node_diff", "--node", "impl"],
        cwd=str(git_repo), capture_output=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "LOCKSTEP_PHASE_DIR": str(env_phase)},
    )
    assert p.returncode == 0
    assert "src/a.py" in p.stdout


# -------------------------------------------------------- always exits 0 (probe)


def test_an_unrecorded_node_explains_itself_and_exits_zero(tmp_path, git_repo):
    h = build(tmp_path, flow(writes=None), git_repo)
    assert h.engine.run() == 0
    rc, out = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    assert rc == 0
    assert "spec.writes" in out, "say WHY there is nothing to show"


def test_an_unknown_node_exits_zero(tmp_path, git_repo):
    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    rc, out = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "nope")
    assert rc == 0
    assert "nope" in out


def test_a_missing_run_dir_exits_zero(tmp_path, git_repo):
    rc, out = probe(git_repo, "--run-dir", str(tmp_path / "gone"), "--node", "impl")
    assert rc == 0
    assert "gone" in out or "state.json" in out


def test_no_run_dir_and_no_env_exits_zero(tmp_path, git_repo, monkeypatch):
    monkeypatch.delenv("LOCKSTEP_PHASE_DIR", raising=False)
    rc, out = probe(git_repo, "--node", "impl")
    assert rc == 0
    assert "LOCKSTEP_PHASE_DIR" in out


def test_a_node_that_changed_nothing_says_so(tmp_path, git_repo):
    h = build(tmp_path, flow(files={}), git_repo)
    assert h.engine.run() == 0
    rc, out = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    assert rc == 0
    assert "nothing" in out.lower()


def test_a_re_run_never_leaves_a_mismatched_pair(tmp_path, git_repo):
    """The pair must bracket ONE attempt. A heal round or a resumed re-run
    takes a fresh baseline; keeping the previous attempt's `tree_after` would
    have `node_diff` diff two trees that never bracketed anything — and, if the
    re-run reverted work, diff them backwards."""
    from conftest import rebuild
    from lockstep.state import write_state

    h = build(tmp_path, flow(), git_repo)
    assert h.engine.run() == 0
    first = load_state(h.run_dir).nodes["impl"]
    assert first.tree_before and first.tree_after

    st = load_state(h.run_dir)
    st.nodes["impl"].status = "pending"
    write_state(h.run_dir, st)
    h2 = rebuild(tmp_path, flow(files={"docs/leak.md": "out of scope"}), git_repo, h.run_dir)
    assert h2.engine.run() == 3, "quarantined"
    rec = load_state(h.run_dir).nodes["impl"]
    assert rec.tree_after is None, "the rolled-back attempt left no tree"
    assert rec.tree_before != first.tree_before, "a fresh attempt takes a fresh baseline"


def test_the_run_dirs_own_bookkeeping_is_excluded(tmp_path, git_repo):
    """Where `runs/` is NOT gitignored, the recorded trees contain the driver's
    own prompts, logs and state.json. Handing those to a reviewer as "what this
    step changed" is the read-side twin of the bug `roles._outside_run_dir`
    fixed on the write side."""
    inside = git_repo / "runs" / "inside"
    inside.mkdir(parents=True)
    h = build(tmp_path, flow(), git_repo, run_dir=inside)
    assert h.engine.run() == 0
    rc, out = probe(git_repo, "--run-dir", str(h.run_dir), "--node", "impl")
    assert rc == 0
    assert "src/a.py" in out
    assert "state.json" not in out and "runs/inside" not in out


def test_a_resume_says_why_a_done_node_re_runs(tmp_path, git_repo):
    """Item 1(b): the reason was recorded and journalled from the start, but
    nothing printed it, so a re-billed node was indistinguishable from an
    ordinary cache miss while you watched it happen."""
    from conftest import rebuild

    f = {"name": "why", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"], "task": "one"}}]}
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    edited = {"name": "why", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"], "task": "two"}}]}
    h2 = rebuild(tmp_path, edited, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    said = [ln for ln in h2.logs if "re-running 'a'" in ln]
    assert said, h2.logs
    assert "prompt" in said[0], said[0]
