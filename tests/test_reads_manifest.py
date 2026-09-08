"""S1 (upstream-response-ow07-feedback, 0.11.0): `spec.reads_manifest`.

`spec.reads` is an input-hash declaration only — it does not tell the harness
which files exist. The OW-07 reviewer that blocked for `evidence-access` had
declared exact paths and a prompt saying "read the files named in spec.reads";
the model, correctly, saw no such list. `reads_manifest: "paths"` appends the
resolved list to the prompt — in the prompt, therefore in the input hash, so a
changed match set re-bills and names itself in `explain` with no extra
machinery. Absent/"none" stays byte-identical (M3 additivity, the same pin
`reads` itself carries in test_reads.py).
"""

from __future__ import annotations

from lockstep import reads as reads_mod
from lockstep.taskgraph import TaskGraph, verify_flow

from conftest import build, make_config, rebuild


def _flow(reader_spec):
    return {
        "name": "manifest",
        "nodes": [
            {"id": "reader", "kind": "fake", "spec": reader_spec},
            {"id": "sink", "kind": "fake", "depends_on": ["reader"],
             "final": True, "spec": {"outputs": ["ok"], "readonly": True}},
        ],
    }


def setup_function(_fn):
    reads_mod.clear_memo()


def test_paths_manifest_lands_in_the_prompt_sorted(tmp_path, git_repo):
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "b.py").write_text("b\n", encoding="utf-8")
    (git_repo / "src" / "a.py").write_text("a\n", encoding="utf-8")
    h = build(tmp_path, _flow({
        "outputs": ["done"], "task": "review the declared files",
        "reads": ["src/*.py"], "reads_manifest": "paths",
    }), git_repo)
    assert h.engine.run() == 0
    prompt = next(c.prompt for c in h.fake.calls if c.node_id == "reader")
    assert "spec.reads" in prompt
    assert prompt.index("src/a.py") < prompt.index("src/b.py"), "sorted, stable"


def test_a_zero_match_glob_names_the_emptiness(tmp_path, git_repo):
    # Silence is how the OW-07 reviewer came to block on evidence-access: an
    # empty manifest must SAY it is empty, not vanish.
    h = build(tmp_path, _flow({
        "outputs": ["done"], "task": "t",
        "reads": ["nothing/matches/*.xyz"], "reads_manifest": "paths",
    }), git_repo)
    assert h.engine.run() == 0
    prompt = next(c.prompt for c in h.fake.calls if c.node_id == "reader")
    assert "matched NO files" in prompt


def test_a_changed_match_set_changes_the_hash_and_explains_itself(tmp_path, git_repo):
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "a.py").write_text("a\n", encoding="utf-8")
    spec = {"outputs": ["done"], "task": "t",
            "reads": ["src/*.py"], "reads_manifest": "paths"}
    flow = _flow(spec)
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0

    # A NEW matching file: content hashing (plain `reads`) would catch an
    # edit; the manifest is what catches the LIST moving.
    (git_repo / "src" / "new.py").write_text("new\n", encoding="utf-8")
    reads_mod.clear_memo()
    h2 = rebuild(tmp_path, flow, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    reran = [c.node_id for c in h2.fake.calls]
    assert "reader" in reran, "a changed match set must re-bill the declarer"
    rec = h2.store.state.nodes["reader"]
    assert any("reads_manifest" in part or "reads" in part
               for part in (rec.invalidated_by or [])), rec.invalidated_by


def test_no_manifest_is_byte_identical(tmp_path, git_repo):
    # M3: the field's absence (and its default) contributes NOTHING.
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "a.py").write_text("a\n", encoding="utf-8")
    base = {"outputs": ["done"], "task": "t", "reads": ["src/*.py"]}
    h = build(tmp_path, _flow(base), git_repo)
    assert h.engine.run() == 0
    node = h.tg.node("reader")
    ctx = h.engine._render_ctx(node, h.store.phase_dir("reader"))
    parts_absent = h.fake.plan(node, ctx).fingerprint_parts

    h2 = build(tmp_path / "two", _flow({**base, "reads_manifest": "none"}), git_repo)
    assert h2.engine.run() == 0
    node2 = h2.tg.node("reader")
    ctx2 = h2.engine._render_ctx(node2, h2.store.phase_dir("reader"))
    parts_none = h2.fake.plan(node2, ctx2).fingerprint_parts
    assert parts_absent == parts_none


def test_manifest_without_reads_is_a_verify_error():
    # Dead config must not hide a wrong belief about what it does (the
    # on_exhausted posture): a manifest with nothing to list is refused.
    from lockstep.executors.fake import FakeExecutor
    from lockstep.registry import Registry

    reg = Registry()
    reg.register(FakeExecutor(repo_root="."))
    flow = _flow({"outputs": ["done"], "task": "t", "reads_manifest": "paths"})
    issues = verify_flow(TaskGraph.model_validate(flow), registry=reg,
                         config=make_config())
    assert any(i.code == "spec-invalid" for i in issues), [i.code for i in issues]
