"""Parity 3.2: `explain --graph` — the whole-graph staleness dry run.

Plans every node against the current tree into a THROWAWAY directory and
compares to the record. The invariants under test: zero mutation of the run
dir it reads, fail-toward-re-running for anything unprovable, and the moved
part named for anything directly stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep import EXIT_CONFIG, EXIT_OK
from lockstep import reads as reads_mod
from lockstep.explain import explain_graph

from conftest import build, make_config


def _capture():
    lines: list[str] = []
    return lines, lines.append


def _flow():
    return {
        "name": "graph",
        "nodes": [
            {"id": "probe", "kind": "shell",
             "spec": {"cmd": ["python", "-c", "print('probe')"], "writes": []}},
            {"id": "impl", "kind": "fake", "depends_on": ["probe"],
             "spec": {"outputs": ["impl done"], "task": "work on {steps.probe.output}",
                      "reads": ["in.txt"]}},
            {"id": "review", "kind": "fake", "depends_on": ["impl"], "final": True,
             "spec": {"outputs": ["ok"], "task": "review {steps.impl.output}",
                      "readonly": True}},
        ],
    }


def _ran(tmp_path, git_repo, flow=None):
    (git_repo / "in.txt").write_text("v1\n", encoding="utf-8")
    f = flow or _flow()
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    (h.run_dir / "flow.tg.json").write_text(json.dumps(f), encoding="utf-8")
    reads_mod.clear_memo()
    return h


def setup_function(_fn):
    reads_mod.clear_memo()


def test_an_unchanged_graph_reports_fresh(tmp_path, git_repo):
    h = _ran(tmp_path, git_repo)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "fresh: 2" in text and "stale: 0 (0 directly, 0 transitively)" in text
    assert "re-runs probe (shell — always re-runs)" in text
    assert "fresh impl" in text and "fresh review" in text
    assert "nothing was executed" in lines[0]


def test_an_edited_read_file_is_directly_stale_and_named_downstream_transitive(tmp_path, git_repo):
    h = _ran(tmp_path, git_repo)
    (git_repo / "in.txt").write_text("v2 — moved underneath the record\n", encoding="utf-8")
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "stale impl" in text
    assert "reads.in.txt" in text, "the moved FILE is named, not an opaque part"
    assert "transitively stale review — upstream 'impl' is stale" in text
    assert "stale: 2 (1 directly, 1 transitively)" in text


def test_a_gcd_result_reports_stale_never_unchanged(tmp_path, git_repo):
    """Proposal finding 20: `gc` may have deleted an upstream's result. A
    missing result is not an error and never a false 'unchanged' — the reader
    cannot be proven fresh, so it reports stale."""
    h = _ran(tmp_path, git_repo)
    result = h.run_dir / "phases" / "impl" / "result.txt"
    assert result.exists()
    result.unlink()
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "stale review" in text
    assert "fresh review" not in text


def test_the_dry_run_never_writes_into_the_run_dir_it_reads(tmp_path, git_repo):
    """A read-only command that mutates the artifact it inspects is worse
    than no command: planning spills values and journals reads-hash timing
    lines, and ALL of it must land in the throwaway directory."""
    h = _ran(tmp_path, git_repo)
    before = {p: p.stat().st_mtime_ns for p in h.run_dir.rglob("*") if p.is_file()}
    journal = (h.run_dir / "events.jsonl").read_bytes()
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    after = {p: p.stat().st_mtime_ns for p in h.run_dir.rglob("*") if p.is_file()}
    assert after == before, "no file in the run dir was created, deleted, or touched"
    assert (h.run_dir / "events.jsonl").read_bytes() == journal


def test_an_unfinished_node_reports_stale_and_propagates(tmp_path, git_repo):
    f = _flow()
    f["nodes"][1]["spec"]["exit_code"] = 1  # impl fails
    (git_repo / "in.txt").write_text("v1\n", encoding="utf-8")
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    (h.run_dir / "flow.tg.json").write_text(json.dumps(f), encoding="utf-8")
    reads_mod.clear_memo()
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "stale impl" in text and "'failed'" in text
    assert "transitively stale review" in text


def test_graph_mode_needs_a_flow_copy_and_readable_state(tmp_path, git_repo):
    lines, out = _capture()
    assert explain_graph(tmp_path / "nope", repo_root=git_repo,
                         config=make_config(), out=out) == EXIT_CONFIG
    h = _ran(tmp_path, git_repo)
    (h.run_dir / "flow.tg.json").unlink()
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo,
                         config=make_config(), out=out) == EXIT_CONFIG
    assert "no flow.tg.json copy" in "\n".join(lines)


def test_downstream_of_an_always_rerun_shell_is_conditionally_fresh(tmp_path, git_repo):
    """S4 (upstream-response-ow07-feedback): the OW-07 pre-run explain said
    three fresh; at run time the shell printed a different duration string and
    two of them re-billed. "Fresh" was the dry run's honest assumption wearing
    a certain word — the assumption gets named per-node now."""
    h = _ran(tmp_path, git_repo)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    # impl interpolates {steps.probe.output}; probe always re-runs.
    assert "conditionally fresh impl — depends on always-rerun shell 'probe'" in text
    # review interpolates {steps.impl.output}: the taint carries, and the ROOT
    # shell is named, not the intermediate.
    assert "conditionally fresh review — depends on always-rerun shell 'probe'" in text
    assert "fresh: 2 (2 conditionally)" in text


def test_ordering_only_dependency_on_a_shell_stays_plain_fresh(tmp_path, git_repo):
    # A node that depends_on a shell purely for sequencing interpolates
    # nothing from it — its hash cannot move when the shell prints
    # differently, and calling it conditional would cry wolf.
    flow = {
        "name": "ordering",
        "nodes": [
            {"id": "probe", "kind": "shell",
             "spec": {"cmd": ["python", "-c", "print('probe')"], "writes": []}},
            {"id": "after", "kind": "fake", "depends_on": ["probe"], "final": True,
             "spec": {"outputs": ["done"], "task": "independent work"}},
        ],
    }
    h = _ran(tmp_path, git_repo, flow=flow)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "fresh after" in text
    assert "conditionally fresh" not in text


# ------------------------------------------------- OPEN-WORK item 8: root gone

def _with_root(h, root: Path):
    from lockstep.state import load_state, write_state
    st = load_state(h.run_dir)
    st.repo_root = str(root)
    write_state(h.run_dir, st)


def test_a_vanished_recorded_root_is_said_before_any_node_is_judged(tmp_path, git_repo):
    """A harvested lane's run records a `repo_root` that no longer exists. The
    dry run still plans against the CURRENT tree (that is what `--graph` is
    for), but says up front that the record came from a tree that is gone, so
    "every node moved" reads as the tree difference it is, not as edits."""
    h = _ran(tmp_path, git_repo)
    gone = tmp_path / "worktrees" / "lane-7"
    _with_root(h, gone)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    note = next(ln for ln in lines if "root gone" in ln)
    assert str(gone) in note and str(git_repo) in note
    # Said BEFORE the first per-node verdict, where a reader decides how to read them.
    first_verdict = next(i for i, ln in enumerate(lines)
                         if ln.startswith(("fresh ", "stale ", "re-runs ", "conditionally ")))
    assert lines.index(note) < first_verdict


def test_a_different_but_present_root_is_named_as_another_tree(tmp_path, git_repo):
    h = _ran(tmp_path, git_repo)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _with_root(h, other)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    note = next(ln for ln in lines if "another tree" in ln)
    assert str(other) in note and str(git_repo) in note
    assert not any("root gone" in ln for ln in lines)


def test_the_same_root_says_nothing_about_roots(tmp_path, git_repo):
    h = _ran(tmp_path, git_repo)
    _with_root(h, git_repo)
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    assert not any("root gone" in ln or "another tree" in ln for ln in lines)


def test_a_legacy_run_with_no_recorded_root_says_nothing_about_roots(tmp_path, git_repo):
    """Empty is unknown, never gone — the same reading `_same_root` gives a
    run recorded before the field existed."""
    h = _ran(tmp_path, git_repo)  # conftest records no repo_root
    from lockstep.state import load_state
    assert load_state(h.run_dir).repo_root == ""
    lines, out = _capture()
    assert explain_graph(h.run_dir, repo_root=git_repo, config=make_config(), out=out) == EXIT_OK
    assert not any("root gone" in ln or "another tree" in ln for ln in lines)
