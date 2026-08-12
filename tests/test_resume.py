"""Test 2 (SPEC §13.1): resume with lineage-head semantics."""

from __future__ import annotations

from lockstep.state import load_state

from conftest import build, calls_of, git, rebuild


def chain_flow(task_b="consume {previous.output}"):
    return {
        "name": "chain",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"task": "produce", "outputs": ["OUT-A"]}},
            {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {"task": task_b, "outputs": ["OUT-B"]}, "final": True},
        ],
    }


def test_hash_matched_nodes_skipped_on_resume(tmp_path, git_repo):
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert h2.fake.calls == [], "everything hash-matched: nothing re-runs"


def test_changed_upstream_invalidates_dependents(tmp_path, git_repo):
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    # Same graph, but a's task changed => a's hash differs => a re-runs; a's new
    # output flows into b's prompt => b's hash differs => b re-runs.
    f2 = chain_flow()
    f2["nodes"][0]["spec"]["task"] = "produce v2"
    f2["nodes"][0]["spec"]["outputs"] = ["OUT-A2"]
    h2 = rebuild(tmp_path, f2, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert [c.node_id for c in h2.fake.calls] == ["a", "b"]
    assert "OUT-A2" in h2.fake.calls[1].prompt


def test_shell_nodes_always_rerun(tmp_path, git_repo):
    from conftest import PY

    f = {
        "name": "sh",
        "nodes": [
            {"id": "s", "kind": "shell", "spec": {"cmd": [PY, "-c", "print('hi')"]}},
            {"id": "b", "kind": "fake", "depends_on": ["s"], "spec": {"outputs": ["B"]}, "final": True},
        ],
    }
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    attempts_1 = load_state(h1.run_dir).nodes["s"].attempts
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert load_state(h2.run_dir).nodes["s"].attempts > attempts_1, "shell always re-runs"


def test_partial_run_resume_reruns_only_from_crash_point(tmp_path, git_repo):
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    # Simulate a crash while b was running: stale-`running`, result gone.
    st = load_state(h1.run_dir)
    st.nodes["b"].status = "running"
    from lockstep.state import write_state

    write_state(h1.run_dir, st)
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    # a completed before the crash: NOT re-run despite its recorded fingerprint
    # differing from later tree states (lineage-head comparison, not per-node).
    assert calls_of(h2, "a") == []
    assert [c.node_id for c in h2.fake.calls] == ["b"]


def test_external_edit_warns_and_reruns_unconsumed(tmp_path, git_repo):
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    # b failed later (simulated), then someone edited the tree OUTSIDE lockstep.
    st = load_state(h1.run_dir)
    st.nodes["b"].status = "failed"
    from lockstep.state import write_state

    write_state(h1.run_dir, st)
    (git_repo / "a.txt").write_text("edited externally\n", encoding="utf-8")
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert any("OUTSIDE lockstep" in line and "a.txt" in line for line in h2.logs), h2.logs
    assert h2.engine.run() == 0
    # a is done but its consumer b is not done => a re-runs (SPEC §9.2), then b.
    assert [c.node_id for c in h2.fake.calls] == ["a", "b"]


def test_external_edit_reruns_leaf_even_when_all_done(tmp_path, git_repo):
    # Audit r6 major: a LEAF node has no consumers, so it is trivially "not
    # yet consumed downstream" and must re-run on an external edit — it is
    # the flow's user-visible artifact. Its upstream, consumed by the leaf's
    # completed run at re-mark time, stays cached.
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    (git_repo / "a.txt").write_text("edited externally\n", encoding="utf-8")
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert [c.node_id for c in h2.fake.calls] == ["b"], "leaf re-runs; consumed upstream does not"


def test_resume_defers_dispatch_until_upstream_revalidation_settles(tmp_path, git_repo):
    # B1 (docs/notes/LESSONS-TO-MECHANISMS.md): on resume, a pending node must
    # not dispatch while one of its dependencies is done-but-awaiting-hash-
    # revalidation behind an invalidated upstream. Dispatching there consumes
    # the previous attempt's cached output — and nothing ever re-checks the
    # downstream node afterwards, so the run exits 0 with a stale result.
    def flow(task_a, out_a, out_b, out_c):
        return {
            "name": "stale",
            "nodes": [
                {"id": "a", "kind": "fake", "spec": {"task": task_a, "outputs": [out_a]}},
                {"id": "b", "kind": "fake", "depends_on": ["a"],
                 "spec": {"task": "consume {steps.a.output}", "outputs": [out_b]}},
                {"id": "c", "kind": "fake", "depends_on": ["b"],
                 "spec": {"task": "consume {steps.b.output}", "outputs": [out_c]}, "final": True},
            ],
        }

    h1 = build(tmp_path, flow("produce", "A1", "B1", "C1"), git_repo)
    assert h1.engine.run() == 0
    # Crash while c was running; a's task then changed (its hash will differ).
    # b's cached completion must revalidate against a's FRESH output before c
    # may start. `outputs` is not part of the fingerprint, so changing b's
    # output alone invalidates nothing — only the a->b->c cascade reaches it.
    st = load_state(h1.run_dir)
    st.nodes["c"].status = "running"
    from lockstep.state import write_state

    write_state(h1.run_dir, st)
    h2 = rebuild(tmp_path, flow("produce v2", "A2", "B2", "C2"), git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    c_calls = calls_of(h2, "c")
    assert len(c_calls) == 1, "c must run exactly once, after b settles"
    assert "B2" in c_calls[0].prompt and "B1" not in c_calls[0].prompt, (
        "c consumed b's stale cached output — dispatched before b revalidated"
    )
    assert [x.node_id for x in h2.fake.calls] == ["a", "b", "c"]


def test_match_means_no_warning(tmp_path, git_repo):
    f = chain_flow()
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert not any("OUTSIDE" in line for line in h2.logs)
