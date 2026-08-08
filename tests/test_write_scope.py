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
    return {
        "name": "scope",
        "nodes": [
            {
                "id": node_id,
                "kind": "fake",
                "final": True,
                "spec": {"outputs": ["ok"], "writes": writes, "write_files": write_files},
            }
        ],
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
    and supersedes the 2026-08-02 `spec.writes` entry in DEVIATIONS.md."""
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
    assert h.engine.run() == 3
    assert not (git_repo / "docs" / "leak.md").exists()
    moved = h.run_dir / "phases" / "w" / "out-of-scope-1" / "docs" / "leak.md"
    assert moved.read_text(encoding="utf-8") == "x"
    err = load_state(h.run_dir).nodes["w"].error or ""
    assert "docs/leak.md" in err
    assert "out-of-scope-1/" in err


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
    """Backward compatible: an undeclared node writes wherever it likes."""
    h = build(tmp_path, _flow([], write_files={"anywhere.txt": "x"}), git_repo)
    assert h.engine.run() == 0


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


def test_a_node_without_a_scope_records_no_touched_list(tmp_path, git_repo):
    h = build(tmp_path, _flow([], write_files={"anywhere.txt": "x"}), git_repo)
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


def test_verify_rejects_writes_on_a_map_node():
    flow = {
        "name": "scope-map",
        "nodes": [
            {"id": "s", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": [{"files": ["a"], "notes": ""}]}},
            {"id": "m", "role": "map", "kind": "fake", "final": True, "depends_on": ["s"],
             "over": "{steps.s.json.files}",
             "spec": {"task": "t {item}", "outputs": ["x"], "writes": ["src"]}},
        ],
    }
    assert "write-scope-on-map" in _codes(flow)
