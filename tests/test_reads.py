"""Parity 3.1: `spec.reads` — declared file inputs as hash parts.

The one non-negotiable is M3 additivity: a node WITHOUT reads produces
byte-identical `fingerprint_parts` to every release before this existed. The
replay fixture passing without re-recording is the second, independent pin of
the same claim (`contrib/replay_suite.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep import reads as reads_mod
from lockstep.state import load_state

from conftest import build, calls_of, rebuild


def _flow(reader_spec):
    # reader and other are INTERIOR nodes (sink consumes both): on resume
    # after an external edit, M7's lineage-head rule re-runs leaves and
    # not-yet-consumed nodes regardless of reads — interior nodes are where
    # reads adds per-node precision, so that is where these tests look.
    return {
        "name": "reads",
        "nodes": [
            {"id": "reader", "kind": "fake", "spec": reader_spec},
            {"id": "other", "kind": "fake",
             "spec": {"outputs": ["other done"], "readonly": True}},
            {"id": "sink", "kind": "fake", "depends_on": ["reader", "other"],
             "final": True, "spec": {"outputs": ["ok"], "readonly": True}},
        ],
    }


def setup_function(_fn):
    reads_mod.clear_memo()


# ------------------------------------------------------------- additivity (M3)


def test_a_node_without_reads_contributes_byte_identical_parts(tmp_path, git_repo):
    """The frozen-surface pin, in its strongest form: the EXACT parts list a
    pre-reads release produced, not merely 'no reads part'."""
    h = build(tmp_path, _flow({"outputs": ["done"], "task": "do the thing"}), git_repo)
    assert h.engine.run() == 0
    node = h.tg.node("reader")
    ctx = h.engine._render_ctx(node, h.store.phase_dir("reader"))
    work = h.fake.plan(node, ctx)
    assert work.fingerprint_parts == [
        "prompt:do the thing",
        f"config:{ctx.config_digest}",
    ], "a no-reads node's parts must be byte-identical to the pre-reads era"
    assert not any(k.startswith("reads.") for k in work.meta["hash_detail"])


def test_an_empty_reads_list_is_the_same_no_op(tmp_path, git_repo):
    h = build(tmp_path, _flow({"outputs": ["done"], "task": "t", "reads": []}), git_repo)
    assert h.engine.run() == 0
    node = h.tg.node("reader")
    work = h.fake.plan(node, h.engine._render_ctx(node, h.store.phase_dir("reader")))
    assert not any(p.startswith("reads:") for p in work.fingerprint_parts)


# ------------------------------------------------------- the part and its detail


def test_reads_part_names_every_matched_file(tmp_path, git_repo):
    (git_repo / "data").mkdir()
    (git_repo / "data" / "a.txt").write_text("alpha\n", encoding="utf-8")
    (git_repo / "data" / "b.txt").write_text("beta\n", encoding="utf-8")
    (git_repo / "data" / "skip.md").write_text("no\n", encoding="utf-8")
    h = build(tmp_path, _flow(
        {"outputs": ["done"], "task": "t", "reads": ["data/*.txt"]}), git_repo)
    assert h.engine.run() == 0
    node = h.tg.node("reader")
    work = h.fake.plan(node, h.engine._render_ctx(node, h.store.phase_dir("reader")))
    part = next(p for p in work.fingerprint_parts if p.startswith("reads:"))
    entries = json.loads(part[len("reads:"):])
    assert [e.split("|")[0] for e in entries] == ["data/a.txt", "data/b.txt"]
    assert all(len(e.split("|")[1]) == 64 for e in entries), "content sha256 per file"
    detail = work.meta["hash_detail"]
    assert "reads.data/a.txt" in detail and "reads.data/b.txt" in detail
    assert "reads.data/skip.md" not in detail


def test_editing_a_declared_file_rebills_exactly_the_declarers(tmp_path, git_repo):
    """The feature's whole point, end to end: an unchanged resume caches both
    nodes; editing the declared file re-runs the declarer, names the file in
    invalidated_by, and leaves the non-declarer cached."""
    (git_repo / "in.txt").write_text("v1\n", encoding="utf-8")
    f = _flow({"outputs": ["done"], "task": "t", "reads": ["in.txt"]})
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0

    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "reader") == [] and calls_of(h2, "other") == []

    reads_mod.clear_memo()  # a new process would not have the memo
    (git_repo / "in.txt").write_text("v2 — edited between resumes\n", encoding="utf-8")
    h3 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h3.engine.prepare_resume()
    assert h3.engine.run() == 0
    assert len(calls_of(h3, "reader")) == 1, "the declarer re-ran"
    assert calls_of(h3, "other") == [], "the non-declaring interior node stayed cached"
    # sink re-runs too, but for M7's reason (a LEAF re-runs on any external
    # edit) — reads is the interior-node precision M7 does not have.
    assert len(calls_of(h3, "sink")) == 1
    rec = load_state(h1.run_dir).nodes["reader"]
    assert any("reads.in.txt" in r for r in (rec.invalidated_by or [])), (
        "explain must name the FILE that moved, not an opaque part: "
        f"{rec.invalidated_by}"
    )


# ------------------------------------------------------------------ the memo


def test_memo_serves_by_stat_key_and_invalidates_when_the_file_moves(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("one\n", encoding="utf-8")
    part1, detail1, _ = reads_mod.hash_reads(tmp_path, ["x.txt"])
    # Same stat key -> the memo is consulted (prove it by poisoning it).
    st = f.stat()
    with reads_mod._memo_lock:
        reads_mod._memo[str(f)] = (st.st_mtime_ns, st.st_size, "poisoned")
    part2, _, _ = reads_mod.hash_reads(tmp_path, ["x.txt"])
    assert "poisoned" in part2, "unchanged stat must hit the memo (no re-read)"
    # A content change moves the stat key -> the poison is ignored, re-hashed.
    f.write_text("two, and longer\n", encoding="utf-8")
    part3, detail3, _ = reads_mod.hash_reads(tmp_path, ["x.txt"])
    assert "poisoned" not in part3
    assert part3 != part1
    assert detail3["reads.x.txt"] != detail1["reads.x.txt"]


def test_git_and_the_runs_root_are_never_hashed(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("code", encoding="utf-8")
    files = reads_mod.matched_files(
        tmp_path, ["**/*"], exclude_roots=(tmp_path / "runs",))
    assert [p.relative_to(tmp_path).as_posix() for p in files] == ["src/a.py"]


# --------------------------------------------------------------- the timing line


def test_reads_hashing_leaves_a_timing_line_in_the_journal(tmp_path, git_repo):
    (git_repo / "in.txt").write_text("v1\n", encoding="utf-8")
    h = build(tmp_path, _flow(
        {"outputs": ["done"], "task": "t", "reads": ["in.txt"]}), git_repo)
    assert h.engine.run() == 0
    events = [json.loads(ln) for ln in
              (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    timing = [e for e in events
              if e.get("kind") == "timing" and e.get("op") == "reads-hash"]
    assert timing and timing[0]["node"] == "reader" and timing[0]["files"] == 1


# ------------------------------------------------------------------ args in reads


def test_reads_interpolates_args_like_a_write_scope(tmp_path, git_repo):
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "spec.md").write_text("text", encoding="utf-8")
    f = _flow({"outputs": ["done"], "task": "t", "reads": ["docs/{args.name}"]})
    f["args"] = {"name": None}
    f["nodes"][1]["spec"]["task"] = "consume {args.name}"  # every arg referenced
    h = build(tmp_path, f, git_repo, args={"name": "spec.md"})
    assert h.engine.run() == 0
    node = h.tg.node("reader")
    work = h.fake.plan(node, h.engine._render_ctx(node, h.store.phase_dir("reader")))
    part = next(p for p in work.fingerprint_parts if p.startswith("reads:"))
    assert "docs/spec.md|" in part
