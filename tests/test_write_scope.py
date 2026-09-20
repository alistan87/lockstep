"""Per-node write-scope permits: `spec.writes`.

A node may declare the paths it is allowed to write. The driver DETECTS
violations after the fact (it never sees tool calls); an in-harness extension
can PREVENT them, using the exported LOCKSTEP_WRITE_SCOPE. That split is
ADDENDUM-A's rule — extensions enforce, they never enable — so deleting the
extension changes only how fast a violation surfaces, never whether it does.

Detection needs the node to be serialized on the tree, or a concurrent node's
writes would be attributed to it — which is why the check runs INSIDE the
token, and why every write-capable executor takes it (shell included).
`verify` warns on the classes that still cannot be checked (readonly nodes).
"""

from __future__ import annotations

import json
import subprocess

from lockstep.state import load_state
from lockstep.taskgraph import TaskGraph, verify_flow
from lockstep.workspace import WorkspaceError

from conftest import PY, build, git, make_config, rebuild


def _codes(flow: dict) -> list[str]:
    from lockstep.executors.fake import FakeExecutor
    from lockstep.executors.shell import ShellExecutor
    from lockstep.registry import Registry

    reg = Registry()
    reg.register(FakeExecutor(repo_root="."))
    reg.register(ShellExecutor(repo_root="."))
    issues = verify_flow(TaskGraph.model_validate(flow), registry=reg, config=make_config())
    return [i.code for i in issues]


def _flow(writes, *, write_files, node_id="w"):
    """`writes=None` omits the key entirely (the v1 unconstrained behavior);
    a list — INCLUDING [] — declares an enforced scope (V1 presence-keying,
    DEVIATIONS 2026-08-11)."""
    spec = {"outputs": ["ok"], "write_files": write_files}
    if writes is not None:
        spec["writes"] = writes
    return {
        "name": "scope",
        "nodes": [{"id": node_id, "kind": "fake", "final": True, "spec": spec}],
    }


# ------------------------------------------------------------- enforcement


def test_a_write_inside_scope_passes(tmp_path, git_repo):
    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).nodes["w"].status == "done"


def test_a_write_outside_scope_fails_the_node(tmp_path, git_repo):
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["w"]
    assert rec.status == "failed"
    assert "write scope" in (rec.error or "")
    assert "docs/leak.md" in (rec.error or "")


def test_the_offending_file_is_moved_aside_not_deleted(tmp_path, git_repo):
    """Rollback never deletes (SPEC §0.1 item 2) — and leaving the file exactly
    where the agent put it was never the only way to honour that. A creation is
    MOVED into the attempt's discard dir and the failure message says where.

    This reverses a pinned test (was `test_the_offending_file_is_not_deleted`)
    and supersedes the 2026-08-02 `spec.writes` entry in DEVIATIONS.md.
    Since G1b the fake's fixed write map re-offends on the corrective re-spawn,
    so BOTH attempts leave evidence and the recorded error is round 2's —
    attempt-scoped names are exactly what keeps attempt 2 from destroying
    attempt 1's evidence (the `_quarantine` docstring's promise, now load-
    bearing on every violation)."""
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
    assert h.engine.run() == 3
    assert not (git_repo / "docs" / "leak.md").exists()
    for attempt in (1, 2):
        moved = h.run_dir / "phases" / "w" / f"out-of-scope-{attempt}" / "docs" / "leak.md"
        assert moved.read_text(encoding="utf-8") == "x"
    err = load_state(h.run_dir).nodes["w"].error or ""
    assert "docs/leak.md" in err
    assert "out-of-scope-2/" in err


def test_a_node_is_not_accused_of_a_concurrent_peers_write(tmp_path, git_repo):
    """The check compares against a whole-tree baseline, so it is only sound
    while this node is serialized on the tree. It used to run in the window
    AFTER `finally: _release(locks)`, where the next node has already taken the
    token and written — a false accusation of a node that stayed in scope."""
    flow = {
        "name": "concurrent-scope",
        "nodes": [
            # Holds `tree` long enough that the peer is queued behind it, so the
            # peer's write lands in the window the check used to run in.
            {"id": "scoped", "kind": "fake",
             "spec": {"outputs": ["ok"], "writes": ["src"],
                      "write_files": {"src/a.py": "x"}, "sleep_s": 0.3}},
            {"id": "peer", "kind": "fake", "final": True,
             "spec": {"outputs": ["ok"], "write_files": {"docs/x.md": "y"}}},
        ],
    }
    h = build(tmp_path, flow, git_repo, max_workers=2)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["scoped"]
    assert rec.status == "done", rec.error


def test_a_node_is_not_accused_of_a_concurrent_shell_nodes_write(tmp_path, git_repo):
    """A shell node used to hold no token at all, so it wrote freely while a
    scoped node was being measured against a whole-tree baseline. Shell nodes
    now take `tree` (§9 decision, measured at +0.15s on the one shipped flow
    with a parallel shell wave)."""
    flow = {
        "name": "shell-scope",
        "nodes": [
            {"id": "scoped", "kind": "fake",
             "spec": {"outputs": ["ok"], "writes": ["src"],
                      "write_files": {"src/a.py": "x"}, "sleep_s": 0.3}},
            {"id": "sh", "kind": "shell", "final": True,
             "spec": {"cmd": [PY, "-c",
                              "import pathlib;"
                              "p=pathlib.Path('docs');p.mkdir(exist_ok=True);"
                              "(p/'from-shell.md').write_text('y');print('ok')"]}},
        ],
    }
    h = build(tmp_path, flow, git_repo, max_workers=2)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["scoped"]
    assert rec.status == "done", rec.error
    assert (git_repo / "docs" / "from-shell.md").exists()


def test_scope_matches_a_directory_prefix(tmp_path, git_repo):
    h = build(tmp_path, _flow(["src"], write_files={"src/deep/nested/a.py": "x"}), git_repo)
    assert h.engine.run() == 0


def test_scope_matches_a_glob(tmp_path, git_repo):
    h = build(tmp_path, _flow(["*.md"], write_files={"README.md": "x"}), git_repo)
    assert h.engine.run() == 0


def test_an_exact_file_scope_matches_only_that_file(tmp_path, git_repo):
    ok = build(tmp_path, _flow(["src/a.py"], write_files={"src/a.py": "x"}), git_repo)
    assert ok.engine.run() == 0
    bad = build(tmp_path / "2", _flow(["src/a.py"], write_files={"src/b.py": "x"}), git_repo)
    assert bad.engine.run() == 3


def test_no_declaration_means_no_check(tmp_path, git_repo):
    """Backward compatible: an ABSENT key writes wherever it likes."""
    h = build(tmp_path, _flow(None, write_files={"anywhere.txt": "x"}), git_repo)
    assert h.engine.run() == 0


def test_declared_empty_scope_blocks_every_write(tmp_path, git_repo):
    """Presence-keyed (V1): `writes: []` declares "this node writes nothing"
    and is enforced — the old truthiness reading silently disabled the check
    for exactly the node that declared the tightest possible scope."""
    h = build(tmp_path, _flow([], write_files={"anywhere.txt": "x"}), git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["w"]
    assert rec.status == "failed"
    assert "nothing (declared writes: [])" in (rec.error or "")
    # The violating write was quarantined out of the tree.
    assert not (h.engine.repo_root / "anywhere.txt").exists()


def test_multiple_scopes_are_a_union(tmp_path, git_repo):
    h = build(
        tmp_path,
        _flow(["src", "docs"], write_files={"src/a.py": "x", "docs/b.md": "y"}),
        git_repo,
    )
    assert h.engine.run() == 0


def test_null_workspace_cannot_detect_and_says_so(tmp_path, plain_repo):
    """No git tree, no diff — the check is skipped, like M6's external-edit
    detection, and the run is not failed on a check that never ran."""
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), plain_repo)
    assert h.engine.run() == 0
    assert any("write scope" in line for line in h.logs), h.logs


# -------------------------------------------------------------- quarantine


def _err(h, node_id="w") -> str:
    return load_state(h.run_dir).nodes[node_id].error or ""


def _cached(repo, rel: str) -> str:
    return subprocess.run(
        ["git", "ls-files", "--cached", "--", rel],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_an_out_of_scope_modify_is_restored_to_the_baseline(tmp_path, git_repo):
    h = build(tmp_path, _flow(["src"], write_files={"a.txt": "clobbered\n"}), git_repo)
    assert h.engine.run() == 3
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "original\n"
    assert "restored to its state before this step" in _err(h)


def test_an_out_of_scope_revert_of_uncommitted_work_is_restored(tmp_path, git_repo):
    """The case SSSF's changeset comparison cannot do (`permissions.py:150`):
    the agent reverts work the operator had not committed. Our baseline is a
    real tree built from the WORKTREE, so the pre-node content is there to
    restore."""
    (git_repo / "a.txt").write_text("operator work in progress\n", encoding="utf-8")
    h = build(tmp_path, _flow(["src"], write_files={"a.txt": "original\n"}), git_repo)
    assert h.engine.run() == 3
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "operator work in progress\n"


def test_in_scope_writes_are_left_exactly_as_they_are(tmp_path, git_repo):
    h = build(
        tmp_path,
        _flow(["src"], write_files={"src/keep.py": "kept\n", "docs/leak.md": "x"}),
        git_repo,
    )
    assert h.engine.run() == 3
    assert (git_repo / "src" / "keep.py").read_text(encoding="utf-8") == "kept\n"
    assert not (git_repo / "docs" / "leak.md").exists()


def test_the_patch_is_written_before_the_restore(tmp_path, git_repo):
    """A blocked attempt must leave evidence — which means capturing the diff
    while the agent's work is still on disk."""
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "secret sauce\n"}), git_repo)
    assert h.engine.run() == 3
    patch = (h.run_dir / "phases" / "w" / "out-of-scope-1.patch").read_text(encoding="utf-8")
    assert "docs/leak.md" in patch
    assert "+secret sauce" in patch


def test_a_staged_out_of_scope_write_does_not_survive_in_the_index(tmp_path, git_repo):
    """An index-safe restore fixes the worktree and leaves the index alone —
    which would strand the violating blob where the next commit picks it up and
    `snapshot()` (worktree-reading) can never see it."""
    code = (
        "import pathlib,subprocess;"
        "d=pathlib.Path('docs');d.mkdir(exist_ok=True);"
        "(d/'leak.md').write_text('x');"
        "subprocess.run(['git','add','docs/leak.md'],check=True);"
        "print('ok')"
    )
    flow = {
        "name": "staged-leak",
        "nodes": [{"id": "w", "kind": "shell", "final": True,
                   "spec": {"cmd": [PY, "-c", code], "writes": ["src"]}}],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 3
    assert _cached(git_repo, "docs/leak.md") == "", "violating blob left in the index"
    assert not (git_repo / "docs" / "leak.md").exists()


def test_an_operators_pre_existing_staged_path_is_named_and_left_alone(tmp_path, git_repo):
    """Same rule SSSF uses at `permissions.py:138-160`, and the same reasoning
    as `_roll_back`'s "was already modified" branch: an index entry that
    predates the node is not the agent's to reset."""
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "pre.md").write_text("operator draft\n", encoding="utf-8")
    git(git_repo, "add", "docs/pre.md")

    h = build(tmp_path, _flow(["src"], write_files={"docs/pre.md": "agent rewrote it\n"}), git_repo)
    assert h.engine.run() == 3
    assert (git_repo / "docs" / "pre.md").read_text(encoding="utf-8") == "operator draft\n"
    assert _cached(git_repo, "docs/pre.md") != "", "the operator's staged entry was reset"
    err = _err(h)
    assert "left staged as you had it" in err
    assert "docs/pre.md" in err


def test_a_partial_restore_names_both_errors(tmp_path, git_repo):
    """A half rollback that reads as a clean one is the failure mode this
    feature exists to prevent."""
    h = build(
        tmp_path,
        _flow(["src"], write_files={"docs/a.md": "1", "docs/b.md": "2"}),
        git_repo,
    )
    real = h.engine.workspace.restore

    def flaky(ref, scope, discard):
        if "docs/b.md" in scope:
            raise WorkspaceError("disk on fire")
        return real(ref, scope, discard)

    h.engine.workspace.restore = flaky
    assert h.engine.run() == 3
    err = _err(h)
    assert "docs/a.md" in err                    # the violation, and its outcome
    assert "THE ROLLBACK DID NOT COMPLETE" in err
    assert "disk on fire" in err                 # the restore error
    assert "not handled: docs/b.md" in err


def test_attempt_two_does_not_overwrite_attempt_ones_evidence(tmp_path, git_repo):
    """`phase_dir` survives resume and heal rounds, and `shutil.move`
    overwrites silently — so every artifact is attempt-scoped, as heal's are."""
    flow = _flow(["src"], write_files={"docs/leak.md": "first\n"})
    h1 = build(tmp_path, flow, git_repo)
    assert h1.engine.run() == 3

    h2 = rebuild(tmp_path, flow, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 3

    phase = h1.run_dir / "phases" / "w"
    assert (phase / "out-of-scope-1.patch").exists()
    assert (phase / "out-of-scope-2.patch").exists()
    assert (phase / "out-of-scope-1" / "docs" / "leak.md").read_text(encoding="utf-8") == "first\n"
    assert (phase / "out-of-scope-2" / "docs" / "leak.md").exists()


def test_the_lineage_head_is_refreshed_so_resume_sees_no_external_edits(tmp_path, git_repo):
    """The quarantine mutates the tree, and the in-scope writes it deliberately
    keeps are not in the last completed node's fingerprint. Without a refresh a
    crash-then-resume reads all of it as somebody editing behind lockstep's
    back (heal does the same at `_heal`)."""
    flow = {
        "name": "lineage",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["ok"]}},
            {"id": "w", "kind": "fake", "final": True, "depends_on": ["a"],
             "spec": {"outputs": ["ok"], "writes": ["src"],
                      "write_files": {"src/kept.py": "in scope\n", "docs/leak.md": "out\n"}}},
        ],
    }
    h1 = build(tmp_path, flow, git_repo)
    assert h1.engine.run() == 3
    h2 = rebuild(tmp_path, flow, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert not any("OUTSIDE lockstep" in line for line in h2.logs), h2.logs


def test_a_rename_out_of_scope_is_reported(tmp_path, git_repo):
    """`--no-renames` splits a rename into delete-old + create-new. The delete
    is in scope and permitted; only the creation is quarantined — so the file
    ends up in neither place, and saying so is the whole job."""
    (git_repo / "src").mkdir()
    (git_repo / "src" / "a.py").write_text("payload\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "src")

    code = (
        "import pathlib,shutil;"
        "d=pathlib.Path('docs');d.mkdir(exist_ok=True);"
        "shutil.move('src/a.py','docs/a.py');print('ok')"
    )
    flow = {
        "name": "rename-out",
        "nodes": [{"id": "w", "kind": "shell", "final": True,
                   "spec": {"cmd": [PY, "-c", code], "writes": ["src"]}}],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 3
    err = _err(h)
    assert "rename out of scope" in err
    assert "src/a.py" in err and "docs/a.py" in err
    assert (h.run_dir / "phases" / "w" / "out-of-scope-1" / "docs" / "a.py").exists()
    assert not (git_repo / "src" / "a.py").exists(), "the in-scope delete stands"


def test_the_run_dir_is_never_quarantined(tmp_path, git_repo):
    """`runs/` is gitignored in THIS repo, so `git add -A` never sees it — a
    convention of one repository, not a property of the design. With the run dir
    inside a tree that does not ignore it, every prompt, log and state.json
    write is a change the node appears to have made: the engine would move its
    own stdout.log aside and roll state.json back mid-run."""
    run_dir = git_repo / "runs" / "inside"
    run_dir.mkdir(parents=True)
    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo,
              run_dir=run_dir)
    assert h.engine.run() == 0
    rec = load_state(run_dir).nodes["w"]
    assert rec.status == "done", rec.error
    assert (run_dir / "state.json").exists()
    assert rec.touched_count == 1, "the run dir is not touched-path evidence either"


def test_a_heal_rollback_never_reverts_the_run_dir(tmp_path, git_repo):
    """The same exclusion, with a sharper edge: a rollback that reverted the run
    dir would restore state.json from under the engine mid-heal."""
    run_dir = git_repo / "runs" / "healing"
    run_dir.mkdir(parents=True)
    flow = {
        "name": "heal-inside",
        "nodes": [
            {"id": "t", "kind": "fake",
             "spec": {"outputs": ["one", "two"], "write_files": {"src/t.py": "x"}}},
            {"id": "g", "role": "gate", "kind": "fake", "final": True, "depends_on": ["t"],
             "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["t"], "rollback": True},
             "spec": {"outputs": [{"verdict": "block", "reason": "again", "findings": []},
                                  {"verdict": "pass", "reason": "ok", "findings": []}]}},
        ],
    }
    h = build(tmp_path, flow, git_repo, run_dir=run_dir)
    assert h.engine.run() == 0
    assert (run_dir / "state.json").exists()
    discarded = list((run_dir / "phases" / "g").glob("discarded-*/**/*"))
    assert not any("state.json" in str(p) for p in discarded), discarded


# -------------------------------------------------------- touched evidence


def test_touched_paths_are_recorded_as_a_count_and_a_file(tmp_path, git_repo):
    h = build(
        tmp_path,
        _flow(["src"], write_files={"src/a.py": "x", "src/b.py": "y"}),
        git_repo,
    )
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["w"]
    assert rec.touched_count == 2
    assert rec.touched_path == "phases/w/touched-1.txt"
    listed = (h.run_dir / "phases" / "w" / "touched-1.txt").read_text(encoding="utf-8").split()
    assert sorted(listed) == ["src/a.py", "src/b.py"]


def test_the_path_list_is_not_put_on_the_record(tmp_path, git_repo):
    """`FileStore.record` rewrites the whole of state.json on every call, and
    this machine's AV makes file replaces the flaky operation."""
    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    assert h.engine.run() == 0
    node_json = json.dumps(
        json.loads((h.run_dir / "state.json").read_text(encoding="utf-8"))["nodes"]["w"]
    )
    assert "src/a.py" not in node_json


def test_a_failed_node_leaves_no_touched_list(tmp_path, git_repo):
    """A failed spawn's changed paths are the wreckage, not the record."""
    flow = _flow(["src"], write_files={"src/a.py": "x"})
    flow["nodes"][0]["spec"]["exit_code"] = 1
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["w"]
    assert rec.status == "failed"
    assert rec.touched_count is None
    assert not list((h.run_dir / "phases" / "w").glob("touched-*.txt"))


def test_a_node_without_a_scope_records_no_touched_list(tmp_path, git_repo):
    h = build(tmp_path, _flow(None, write_files={"anywhere.txt": "x"}), git_repo)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["w"]
    assert rec.touched_count is None and rec.touched_path is None


def test_the_touched_list_is_attempt_scoped(tmp_path, git_repo):
    h1 = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    assert h1.engine.run() == 0

    # A different task text is a different input hash, so the node re-runs.
    flow2 = _flow(["src"], write_files={"src/a.py": "y"})
    flow2["nodes"][0]["spec"]["task"] = "do it again"
    h2 = rebuild(tmp_path, flow2, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0

    phase = h1.run_dir / "phases" / "w"
    assert (phase / "touched-1.txt").exists()
    assert (phase / "touched-2.txt").exists()
    assert load_state(h1.run_dir).nodes["w"].touched_path == "phases/w/touched-2.txt"


# ------------------------------------------------------------ the env var


def test_write_scope_is_exported_to_the_spawn(tmp_path, git_repo):
    dump = (
        "import os, json; print(json.dumps({'s': os.environ.get('LOCKSTEP_WRITE_SCOPE', '')}))"
    )
    flow = {
        "name": "scope-env",
        "nodes": [
            {"id": "p", "kind": "shell", "final": True,
             "spec": {"cmd": [PY, "-c", dump], "writes": ["src", "docs"]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    result = json.loads((h.run_dir / "phases" / "p" / "result.txt").read_text(encoding="utf-8"))
    assert json.loads(result["s"]) == ["src", "docs"]


def test_undeclared_scope_exports_empty(tmp_path, git_repo):
    dump = (
        "import os, json; print(json.dumps({'s': os.environ.get('LOCKSTEP_WRITE_SCOPE', 'MISSING')}))"
    )
    flow = {
        "name": "scope-env-none",
        "nodes": [
            {"id": "p", "kind": "shell", "final": True, "spec": {"cmd": [PY, "-c", dump]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    result = json.loads((h.run_dir / "phases" / "p" / "result.txt").read_text(encoding="utf-8"))
    assert result["s"] == ""


# ---------------------------------------------------------- verification


def test_verify_rejects_an_absolute_or_escaping_scope():
    assert "bad-write-scope" in _codes(_flow(["/etc"], write_files={}))
    assert "bad-write-scope" in _codes(_flow(["../outside"], write_files={}))
    assert "bad-write-scope" in _codes(_flow([""], write_files={}))


def test_verify_accepts_an_ordinary_scope():
    assert "bad-write-scope" not in _codes(_flow(["src", "docs/*.md"], write_files={}))


def test_verify_warns_when_the_scope_cannot_be_enforced():
    """A readonly node holds no tree token, so a concurrent write would be
    misattributed; the declaration stands but detection is off."""
    flow = {
        "name": "scope-unenforced",
        "nodes": [
            {"id": "r", "kind": "fake", "final": True,
             "spec": {"outputs": ["ok"], "readonly": True, "writes": ["src"]}},
        ],
    }
    assert "write-scope-unenforced" in _codes(flow)


def test_verify_no_longer_warns_for_a_shell_node():
    """Shell nodes take the `tree` token, so their declared scope IS enforced
    (see `_serialized_on_tree`)."""
    flow = {
        "name": "scope-shell",
        "nodes": [
            {"id": "s", "kind": "shell", "final": True,
             "spec": {"cmd": ["git", "--version"], "writes": ["src"]}},
        ],
    }
    assert "write-scope-unenforced" not in _codes(flow)


def _map_flow(writes, *, write_files=None, write_files_by_attempt=None, items=("a", "b"),
              readonly=False):
    """A map whose items all run the same fake write map. `writes=None` omits
    the key (unconstrained); a list — including [] — declares the map's scope,
    checked per ITEM against a baseline taken for that item."""
    spec: dict = {"task": "t {item}", "outputs": ["x"]}
    if write_files is not None:
        spec["write_files"] = write_files
    if write_files_by_attempt is not None:
        spec["write_files_by_attempt"] = write_files_by_attempt
    if writes is not None:
        spec["writes"] = writes
    if readonly:
        spec["readonly"] = True
    return {
        "name": "scope-map",
        "nodes": [
            {"id": "s", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": [{"files": list(items), "notes": ""}], "writes": []}},
            {"id": "m", "role": "map", "kind": "fake", "final": True, "depends_on": ["s"],
             "over": "{steps.s.json.files}", "concurrency": 1, "spec": spec},
        ],
    }


def test_verify_accepts_writes_on_a_map_node():
    """Reversed 2026-09-19: `write-scope-on-map` was the error while the items
    shared one diff. Each item now gets its own baseline inside the tree
    token, so a map declares a scope like any other mutator."""
    assert "write-scope-on-map" not in _codes(_map_flow(["src"]))


def test_verify_warns_when_a_map_scope_cannot_be_enforced():
    """A readonly map's items hold no `tree` token, so the per-item diff would
    be unsound — the same advisory a readonly work node gets."""
    assert "write-scope-unenforced" in _codes(_map_flow(["src"], readonly=True))


class TestMapScope:
    """Per-item write scopes (ROADMAP 2026-08-12, built 2026-09-19): the map's
    one declared scope, enforced per item. Every item takes its own baseline
    and after-snapshot inside the token, so item 1's writes are never
    attributed to item 2 and a violation quarantines exactly the item that
    made it."""

    def test_in_scope_items_pass_and_record_touched_paths(self, tmp_path, git_repo):
        h = build(tmp_path, _map_flow(["src"], write_files={"src/a.py": "x"}), git_repo)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["m"]
        assert rec.status == "done", rec.error
        for i in ("0", "1"):
            irec = rec.items[i]
            assert irec.status == "done"
            assert irec.tree_before and irec.tree_after
            assert irec.touched_path == f"phases/m/items/{i}/touched-1.txt"
        # Item 0 created the file; item 1 rewrote it with identical bytes, so
        # against ITS baseline nothing changed — the diff is per item.
        assert rec.items["0"].touched_count == 1
        assert rec.items["1"].touched_count == 0
        assert (h.run_dir / "phases" / "m" / "items" / "0" / "touched-1.txt").read_text() == "src/a.py\n"

    def test_an_out_of_scope_item_is_quarantined_and_fails_the_map(self, tmp_path, git_repo):
        from lockstep.state import read_events
        h = build(tmp_path, _map_flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["m"]
        assert rec.status == "failed"
        assert "item 0 failed" in (rec.error or "")
        assert "write scope violated: item m[0]" in (rec.error or "")
        irec = rec.items["0"]
        assert irec.status == "failed"
        assert "docs/leak.md" in (irec.error or "")
        # The evidence is the ITEM's, attempt-scoped, under items/<i>/ —
        # and the fake re-offends on its corrective, so both rounds leave one.
        item_dir = h.run_dir / "phases" / "m" / "items" / "0"
        assert (item_dir / "out-of-scope-1.patch").exists()
        assert (item_dir / "out-of-scope-1" / "docs" / "leak.md").exists()
        assert "items/0/out-of-scope-2.patch" in (irec.error or "")
        assert not (git_repo / "docs" / "leak.md").exists()
        assert irec.tree_after is None, "a quarantined attempt left no tree"
        # Item 1 runs after item 0 fails (errors are collected, §9.3) and
        # re-offends against ITS OWN baseline, so both items quarantine, and
        # every journal line says which item.
        evs = [e for e in read_events(h.run_dir) if e.get("status") == "quarantined"]
        assert evs and all(e.get("node") == "m" for e in evs)
        assert {e.get("item") for e in evs} == {0, 1}
        assert rec.items["1"].status == "failed"

    def test_the_corrective_is_per_item_and_recovers(self, tmp_path, git_repo):
        from conftest import calls_of
        flow = _map_flow(["src"], write_files_by_attempt=[
            {"docs/leak.md": "x", "src/a.py": "good"},
            {"src/a.py": "good2"},
        ], items=("only",))
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["m"]
        assert rec.status == "done", rec.error
        irec = rec.items["0"]
        assert irec.status == "done" and irec.attempts == 2
        assert irec.tree_before and irec.tree_after
        assert irec.touched_path == "phases/m/items/0/touched-2.txt"
        assert not (git_repo / "docs" / "leak.md").exists()
        assert (git_repo / "src" / "a.py").read_text(encoding="utf-8") == "good2"
        calls = calls_of(h, "m")
        assert len(calls) == 2 and calls[1].corrective
        assert "ONLY these paths (spec.writes): src" in calls[1].prompt
        # The map's own attempt counter is untouched: the item carried it.
        assert rec.attempts == 0

    def test_declared_empty_scope_blocks_every_item_write(self, tmp_path, git_repo):
        h = build(tmp_path, _map_flow([], write_files={"src/a.py": "x"}, items=("one",)), git_repo)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["m"]
        assert "declared writes: []" in (rec.items["0"].error or "")

    def test_no_declaration_means_no_per_item_check(self, tmp_path, git_repo):
        h = build(tmp_path, _map_flow(None, write_files={"docs/free.md": "x"}), git_repo)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["m"]
        assert rec.items["0"].tree_before is None and rec.items["0"].touched_path is None

    def test_each_item_is_measured_against_its_own_baseline(self, tmp_path, git_repo):
        """The fake's attempt counter is per NODE, so `write_files_by_attempt`
        indexes across the map's spawns: item 0 writes src/a.py, item 1 writes
        src/b.py, item 2 writes nothing. With ONE baseline for the whole map,
        item 1's list would carry src/a.py and item 2's both — per-item
        baselines make each list exactly that item's own writes."""
        h = build(tmp_path, _map_flow(["src"], write_files_by_attempt=[
            {"src/a.py": "x"}, {"src/b.py": "y"}, {},
        ], items=("a", "b", "c")), git_repo)
        assert h.engine.run() == 0
        items = h.run_dir / "phases" / "m" / "items"
        assert (items / "0" / "touched-1.txt").read_text() == "src/a.py\n"
        assert (items / "1" / "touched-1.txt").read_text() == "src/b.py\n"
        assert (items / "2" / "touched-1.txt").read_text() == ""
        rec = load_state(h.run_dir).nodes["m"]
        assert [rec.items[str(i)].touched_count for i in range(3)] == [1, 1, 0]

    def test_a_parallel_map_still_attributes_per_item(self, tmp_path, git_repo):
        """concurrency > 1: the items hold `tree` and serialize on it, and the
        whole check sits inside the token — so three items on a pool are
        still measured one at a time against their own baselines."""
        flow = _map_flow(["src"], write_files_by_attempt=[
            {"src/a.py": "x"}, {"src/b.py": "y"}, {"src/c.py": "z"},
        ], items=("a", "b", "c"))
        flow["nodes"][1]["concurrency"] = 3
        h = build(tmp_path, flow, git_repo, max_workers=3)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["m"]
        assert all(rec.items[str(i)].touched_count == 1 for i in range(3))
        seen = set()
        for i in range(3):
            seen |= set((h.run_dir / "phases" / "m" / "items" / str(i) / "touched-1.txt")
                        .read_text().split())
        assert seen == {"src/a.py", "src/b.py", "src/c.py"}

    def test_heal_rounds_keep_each_items_evidence(self, tmp_path, git_repo):
        """A heal round clears the map's item records (A3.4) but must KEEP
        each item's attempt counter: the counter names `out-of-scope-<n>`
        and `touched-<n>`, and a reset let round 1's quarantine overwrite
        round 0's preserved attempt (adversarial review 2026-09-19)."""
        from test_heal import BLOCK, PASS
        flow = _map_flow(["src"], write_files_by_attempt=[
            {"docs/leakA.md": "a", "src/a.py": "1"},   # round 0, attempt 1: leaks A
            {"src/a.py": "1"},                          # its corrective: clean
            {"docs/leakB.md": "b", "src/a.py": "2"},   # heal round, attempt 3: leaks B
            {"src/a.py": "2"},                          # its corrective: clean
        ], items=("only",))
        flow["nodes"][1]["final"] = False
        flow["nodes"] += [
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["m"],
             "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["m"], "rollback": True},
             "spec": {"outputs": [BLOCK, PASS], "readonly": True}},
            {"id": "after", "kind": "fake", "depends_on": ["gate"], "final": True,
             "spec": {"outputs": ["ok"], "readonly": True}},
        ]
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 0
        st = load_state(h.run_dir)
        assert st.verdicts["gate"] == "pass"
        irec = st.nodes["m"].items["0"]
        assert irec.attempts == 4, "the counter survived the heal round"
        item_dir = h.run_dir / "phases" / "m" / "items" / "0"
        assert "leakA" in (item_dir / "out-of-scope-1.patch").read_text(encoding="utf-8")
        assert "leakB" in (item_dir / "out-of-scope-3.patch").read_text(encoding="utf-8")
        assert (item_dir / "out-of-scope-1" / "docs" / "leakA.md").exists()
        assert (item_dir / "out-of-scope-3" / "docs" / "leakB.md").exists()
        assert irec.touched_path == "phases/m/items/0/touched-4.txt"

    def test_a_cancelled_item_is_quarantined_but_gets_no_corrective(self, tmp_path, git_repo):
        """r6 C3: a cancelled attempt consumes no corrective re-spawn. The
        quarantine still happens (it spawns nothing) and the record says both."""
        from conftest import calls_of
        flow = _map_flow(["src"], write_files={"docs/leak.md": "x"}, items=("only",))
        flow["nodes"][1]["spec"]["write_phase_files"] = {"CANCELLED": ""}
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 3
        irec = load_state(h.run_dir).nodes["m"].items["0"]
        assert (irec.error or "").startswith("cancelled\nwrite scope violated")
        assert not (git_repo / "docs" / "leak.md").exists()
        assert len(calls_of(h, "m")) == 1, "no corrective after a cancel"


def test_dirty_scope_preflight_refuses_overlap(tmp_path, git_repo):
    # E9 (LESSONS-TO-MECHANISMS): an in-scope write legally OVERWRITES a file
    # the operator edited before the run; the preflight makes the overlap a
    # refusal instead of a checklist item.
    import pytest as _pytest

    from lockstep.roles import RunRefusal

    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "a.py").write_text("operator edit, uncommitted\n", encoding="utf-8")
    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    h.engine.check_dirty_scope = True
    with _pytest.raises(RunRefusal) as ei:
        h.engine.run()
    assert "src/a.py" in str(ei.value)
    assert "--allow-dirty-scope" in str(ei.value)


def test_dirty_scope_preflight_ignores_out_of_scope_dirt(tmp_path, git_repo):
    (git_repo / "notes.md").write_text("unrelated dirt\n", encoding="utf-8")
    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    h.engine.check_dirty_scope = True
    assert h.engine.run() == 0


def test_declared_empty_scope_reaches_the_spawn_env(tmp_path):
    # Adversarial-review finding 2: `writes: []` must reach the in-harness
    # guard as "[]" (block everything), not "" (no scope) — truthiness at the
    # env boundary disarmed the preventive layer for exactly the tightest
    # declaration. Absent key stays "".
    from lockstep.executors.shell import ShellExecutor, node_env
    from lockstep.protocols import RenderCtx
    from lockstep.taskgraph import Node

    ex = ShellExecutor(repo_root=tmp_path)
    ctx = RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "p", phase_dir=tmp_path / "ph",
        max_interp_chars=20000, config_digest="d",
    )
    declared_empty = Node(id="a", kind="shell", spec={"cmd": ["git", "status"], "writes": []})
    env = node_env(ex.plan(declared_empty, ctx), tmp_path / "ph")
    assert env["LOCKSTEP_WRITE_SCOPE"] == "[]"
    absent = Node(id="b", kind="shell", spec={"cmd": ["git", "status"]})
    env = node_env(ex.plan(absent, ctx), tmp_path / "ph")
    assert env["LOCKSTEP_WRITE_SCOPE"] == ""
    scoped = Node(id="c", kind="shell", spec={"cmd": ["git", "status"], "writes": ["docs"]})
    env = node_env(ex.plan(scoped, ctx), tmp_path / "ph")
    assert env["LOCKSTEP_WRITE_SCOPE"] == '["docs"]'


def test_dirty_paths_survive_non_ascii_names(git_repo):
    # Adversarial-review finding 6: porcelain line output C-quotes non-ASCII
    # paths and naive unquoting mangled them; -z hands them over verbatim.
    from lockstep.workspace import GitWorkspace

    (git_repo / "café.md").write_text("x\n", encoding="utf-8")
    dirty = GitWorkspace(git_repo).dirty_paths()
    assert "café.md" in dirty, dirty


def test_dirty_scope_preflight_ignores_the_runs_own_dir(tmp_path, git_repo):
    # Adversarial-review finding 1 (repro'd live): with the run dir inside an
    # un-ignored work tree, the driver's own just-written state.json is
    # "dirty" and a ["**"] scope refused every fresh run on its own
    # bookkeeping. The preflight applies the same exclusion quarantine does.
    import shutil

    from lockstep.state import write_state

    h = build(tmp_path, _flow(["**"], write_files={"src/a.py": "x"}), git_repo)
    # Relocate the run dir INSIDE the repo (un-ignored) and point the store at it.
    inner = git_repo / "runs" / h.run_dir.name
    shutil.copytree(h.run_dir, inner)
    h2 = build(tmp_path, _flow(["**"], write_files={"src/a.py": "x"}), git_repo,
               run_dir=inner)
    h2.engine.check_dirty_scope = True
    assert h2.engine.run() == 0, "must not refuse on its own state.json"


def test_workspace_timings_are_journalled_and_stay_advisory(tmp_path, git_repo):
    """P1-perf: every git tree operation the engine performs is journalled with
    its duration, because a run that slows down over its life had no way to
    show where the time went (lesson 20: a gate creeping 13 -> 32 minutes
    across resumes).

    Advisory means three things at once, and each is asserted: the lines carry
    no `status`, so nothing that branches on transitions can see them; they
    still chain, so the audit trail stays verifiable; and the run's outcome is
    unchanged by their presence.
    """
    from lockstep.state import trace_status

    h = build(tmp_path, _flow(["src"], write_files={"src/a.py": "x"}), git_repo)
    assert h.engine.run() == 0

    events = [json.loads(ln) for ln in
              (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    timings = [e for e in events if e.get("kind") == "timing"]
    # A scoped node costs TWO whole-tree walks: the baseline before it runs and
    # the tree it left after. That is the number the measurement is about, and
    # it did not change when `node_diff` started recording both — the engine
    # already computed them. What changed is that `scope-diff` now names a
    # `diff-tree` of those two snapshots rather than a third walk of its own.
    ops = sorted(e["op"] for e in timings)
    assert ops == ["scope-after", "scope-baseline", "scope-diff"], ops
    assert len([e for e in timings if e["op"].startswith("scope-") and e["op"] != "scope-diff"]) == 2
    assert all(e["node"] == "w" for e in timings)
    assert all(isinstance(e["ms"], int) and e["ms"] >= 0 for e in timings)
    assert all("status" not in e for e in timings), "a timing is not a transition"
    assert trace_status(h.run_dir)["ok"], "advisory lines must still chain"


# ------------------------------------------------ interpolated scopes (r7)


def _arg_flow(writes, *, write_files, args=None):
    spec = {"outputs": ["ok"], "write_files": write_files, "writes": writes,
            "task": "produce {args.name}"}
    return {
        "name": "argscope",
        "args": args if args is not None else {"name": "plan.md"},
        "nodes": [{"id": "w", "kind": "fake", "final": True, "spec": spec}],
    }


def test_a_scope_may_name_an_arg(tmp_path, git_repo):
    """The gap this closes: a parameterized flow could not scope to the file it
    was told to write, so it declared ["**"] and the permit meant nothing."""
    flow = _arg_flow(["docs/{args.name}"], write_files={"docs/plan.md": "x"})
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).nodes["w"].status == "done"


def test_an_arg_scope_still_quarantines_what_it_does_not_cover(tmp_path, git_repo):
    flow = _arg_flow(["docs/{args.name}"], write_files={"docs/other.md": "x"})
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 3
    assert "docs/other.md" in (load_state(h.run_dir).nodes["w"].error or "")


def test_a_scope_may_not_reference_a_step(tmp_path, git_repo):
    """A scope resolved from a step's OUTPUT would let the graph widen its own
    permissions — the model writes the answer that decides what it may write."""
    flow = {
        "name": "dynscope",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["docs"]}},
            {"id": "w", "kind": "fake", "final": True, "depends_on": ["a"],
             "spec": {"outputs": ["ok"], "writes": ["{steps.a.output}/x.md"]}},
        ],
    }
    assert "dynamic-write-scope" in _codes(flow)


def test_an_arg_cannot_render_a_scope_out_of_the_repo(tmp_path, git_repo):
    """`verify` checks the WRITTEN entry for `..` and absolute paths; after
    substitution it is a different string, so the same two rules are applied
    to what will actually be matched."""
    from lockstep.interpolate import InterpolationError, render_scope

    try:
        render_scope(["out/{args.dir}"], {"dir": "../../etc"})
    except InterpolationError as e:
        assert "escapes the repo root" in str(e)
    else:
        raise AssertionError("an escaping arg must not render into a scope")


def test_an_arg_used_only_in_a_scope_counts_as_referenced(tmp_path, git_repo):
    """Otherwise declaring the arg a scope needs trips `unused-arg`, and the
    author's only way out is to stop scoping."""
    flow = {
        "name": "onlyscope",
        "args": {"name": "plan.md"},
        "nodes": [{"id": "w", "kind": "fake", "final": True,
                   "spec": {"outputs": ["ok"], "writes": ["docs/{args.name}"]}}],
    }
    codes = _codes(flow)
    assert "unused-arg" not in codes
    assert "undeclared-arg" not in codes


def test_a_scope_naming_an_undeclared_arg_is_an_error(tmp_path, git_repo):
    flow = {
        "name": "badarg",
        "nodes": [{"id": "w", "kind": "fake", "final": True,
                   "spec": {"outputs": ["ok"], "writes": ["docs/{args.nope}"]}}],
    }
    assert "undeclared-arg" in _codes(flow)


def test_the_spawn_sees_the_RENDERED_scope(tmp_path, git_repo):
    """The in-harness guard enforces LOCKSTEP_WRITE_SCOPE; if it got the raw
    template it would block the very path the driver was about to allow."""
    import sys

    from lockstep.executors.shell import node_env, ShellExecutor
    from lockstep.protocols import RenderCtx

    node = TaskGraph.model_validate({
        "name": "envscope",
        "args": {"name": "plan.md"},
        "nodes": [{"id": "s", "kind": "shell", "final": True,
                   "spec": {"cmd": [sys.executable, "-c", "pass"],
                            "writes": ["docs/{args.name}"]}}],
    }).nodes[0]
    ctx = RenderCtx(
        args={"name": "plan.md"}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "p", phase_dir=tmp_path / "ph",
        max_interp_chars=20000, config_digest="d",
    )
    work = ShellExecutor(repo_root=git_repo).plan(node, ctx)
    env = node_env(work, tmp_path)
    assert env["LOCKSTEP_WRITE_SCOPE"] == '["docs/plan.md"]'


# ------------------------------------------------- G1b: scope-corrective re-spawn


class TestScopeCorrective:
    """G1b (upstream-response-ow07-feedback, accepted 0.12.0): one corrective
    re-spawn after a write-scope quarantine, symmetric with the
    contract-violation shape. The boundary is not weakened: the quarantine
    still happened, the retry starts from the restored tree, and a second
    violation is terminal."""

    def test_corrective_recovers_when_it_stays_in_scope(self, tmp_path, git_repo):
        from lockstep.state import read_events
        from conftest import calls_of
        flow = {
            "name": "scope-corrective",
            "nodes": [{"id": "w", "kind": "fake", "final": True,
                       "spec": {"outputs": ["ok"], "writes": ["src"],
                                "write_files_by_attempt": [
                                    {"docs/leak.md": "x", "src/a.py": "good"},
                                    {"src/a.py": "good2"},
                                ]}}],
        }
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.status == "done", rec.error
        assert rec.attempts == 2
        # The quarantine happened for real: the leak is gone, evidence kept.
        assert not (git_repo / "docs" / "leak.md").exists()
        assert (h.run_dir / "phases" / "w" / "out-of-scope-1" / "docs" / "leak.md").exists()
        # In-scope work survives both rounds; the corrective's write wins.
        assert (git_repo / "src" / "a.py").read_text(encoding="utf-8") == "good2"
        # Journaled, and the spawn was spent.
        evs = [e for e in read_events(h.run_dir) if e.get("status") == "scope-corrective-respawn"]
        assert len(evs) == 1
        assert load_state(h.run_dir).token_spawns == 2
        # The corrective prompt carries its own context: original task, the
        # reverted patch as fenced evidence, the scope restated.
        calls = calls_of(h, "w")
        assert len(calls) == 2 and calls[1].corrective
        assert "scope.violation.patch" in calls[1].prompt
        assert "ONLY these paths (spec.writes): src" in calls[1].prompt
        # node_diff's pair brackets the node's total surviving change.
        assert rec.tree_before and rec.tree_after

    def test_a_cancelled_node_is_quarantined_but_gets_no_corrective(self, tmp_path, git_repo):
        """r6 C3: "a cancelled node consumes no retries and no corrective
        re-spawn". The quarantine still runs — it spawns nothing and an
        out-of-scope write left by a cancelled attempt is still a violation —
        and the error names the cancel first (adversarial review 2026-09-19)."""
        from conftest import calls_of
        flow = _flow(["src"], write_files={"docs/leak.md": "x"})
        flow["nodes"][0]["spec"]["write_phase_files"] = {"CANCELLED": ""}
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["w"]
        assert (rec.error or "").startswith("cancelled\nwrite scope violated")
        assert not (git_repo / "docs" / "leak.md").exists()
        assert len(calls_of(h, "w")) == 1, "no corrective after a cancel"
        assert load_state(h.run_dir).token_spawns == 1

    def test_a_second_violation_is_terminal(self, tmp_path, git_repo):
        from conftest import calls_of
        h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.status == "failed"
        assert "ALSO violated" in (rec.error or "")
        assert "one round" in (rec.error or "")
        assert len(calls_of(h, "w")) == 2, "no third chance"
        assert rec.tree_after is None, "a quarantined attempt left no tree"

    def test_shell_nodes_get_no_corrective(self, tmp_path, git_repo):
        """Shell is deterministic — re-running the same argv writes the same
        paths (AMENDMENTS A4's reasoning, applied to scope)."""
        from lockstep.state import read_events
        flow = {
            "name": "shell-no-corrective",
            "nodes": [{"id": "s", "kind": "shell", "final": True,
                       "spec": {"cmd": [PY, "-c",
                                        "open('docs_leak.md','w').write('x')"],
                                "writes": ["src"]}}],
        }
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["s"]
        assert rec.status == "failed" and "write scope" in (rec.error or "")
        assert not any(e.get("status") == "scope-corrective-respawn"
                       for e in read_events(h.run_dir))

    def test_budget_trip_before_corrective_is_a_clean_stop(self, tmp_path, git_repo):
        """The corrective spends a spawn like any other; a cap of 1 means the
        quarantine stands and the run stops at the budget, never a traceback."""
        flow = {
            "name": "scope-budget",
            "budget": {"max_agent_spawns": 1},
            "nodes": [{"id": "w", "kind": "fake", "final": True,
                       "spec": {"outputs": ["ok"], "writes": ["src"],
                                "write_files": {"docs/leak.md": "x"}}}],
        }
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 4
        assert load_state(h.run_dir).token_spawns == 1
        # The quarantine itself completed before the trip: the leak is gone.
        assert not (git_repo / "docs" / "leak.md").exists()
