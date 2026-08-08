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

from lockstep.state import load_state
from lockstep.taskgraph import TaskGraph, verify_flow

from conftest import PY, build, make_config


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


def test_the_offending_file_is_not_deleted(tmp_path, git_repo):
    """Rollback never deletes (SPEC §0.1 item 2); a scope violation reports,
    it does not clean up behind the node."""
    h = build(tmp_path, _flow(["src"], write_files={"docs/leak.md": "x"}), git_repo)
    h.engine.run()
    assert (git_repo / "docs" / "leak.md").exists()


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
