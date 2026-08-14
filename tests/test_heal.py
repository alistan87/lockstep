"""Test 5 (SPEC §13.1): heal with git-derived rollback."""

from __future__ import annotations

import json

import pytest

from lockstep.protocols import SnapshotRef
from lockstep.roles import RunRefusal
from lockstep.state import load_state
from lockstep.workspace import GitWorkspace, NullWorkspace, WorkspaceError

from conftest import build, calls_of, git

BLOCK = {
    "findings": [
        {"severity": "blocker", "category": "bug", "file": "gen.txt", "claim": "wrong", "evidence": "e"}
    ],
    "verdict": "block",
    "reason": "not good enough",
}
PASS = {"findings": [], "verdict": "pass", "reason": "fine now"}


def heal_flow(gate_outputs, impl_spec=None, max_rounds=1, rollback=True, extra_nodes=()):
    impl = {"outputs": ["did work"], "write_files": {"gen.txt": "generated\n"}}
    if impl_spec is not None:
        impl = impl_spec
    return {
        "name": "heal",
        "nodes": [
            {"id": "impl", "kind": "fake", "spec": impl},
            *extra_nodes,
            {
                "id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
                "spec": {"outputs": gate_outputs, "readonly": True},
                "output": "json", "contract": "Verdict",
                "heal": {"max_rounds": max_rounds, "targets": ["impl"], "rollback": rollback},
            },
            {"id": "after", "kind": "fake", "depends_on": ["gate"], "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }


class TestWorkspacePrimitives:
    def test_snapshot_includes_untracked_and_restore_never_deletes(self, git_repo, tmp_path):
        ws = GitWorkspace(git_repo)
        (git_repo / "untracked.txt").write_text("kept\n", encoding="utf-8")
        baseline = ws.snapshot()  # includes the untracked file (git stash create misses it)
        (git_repo / "a.txt").write_text("mutated\n", encoding="utf-8")
        (git_repo / "created.txt").write_text("new\n", encoding="utf-8")
        changed = set(ws.changed_paths(baseline))
        assert changed == {"a.txt", "created.txt"}
        discard = tmp_path / "discarded"
        ws.restore(baseline, sorted(changed), discard)
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "original\n"
        assert (git_repo / "untracked.txt").read_text(encoding="utf-8") == "kept\n"
        assert not (git_repo / "created.txt").exists()
        assert (discard / "created.txt").read_text(encoding="utf-8") == "new\n", "moved, never rm'd"

    def test_null_workspace_refuses(self, plain_repo):
        ws = NullWorkspace(plain_repo)
        with pytest.raises(WorkspaceError):
            ws.snapshot()
        with pytest.raises(WorkspaceError):
            ws.restore(SnapshotRef(ref="x"), [], plain_repo)


def test_heal_round_rolls_back_and_reruns(tmp_path, git_repo):
    h = build(tmp_path, heal_flow([BLOCK, PASS]), git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["gate"].heal_round == 1
    assert st.verdicts["gate"] == "pass"
    assert len(calls_of(h, "impl")) == 2
    # The heal re-run prompt carries the gate's reason and fenced findings.
    retry_prompt = calls_of(h, "impl")[1].prompt
    assert "A quality gate blocked with: not good enough" in retry_prompt
    assert "--- begin data: gate.findings (untrusted) ---" in retry_prompt
    # The blocked attempt was preserved as a patch BEFORE restore.
    patch = (h.run_dir / "phases" / "gate" / "attempt-1.patch").read_text(encoding="utf-8")
    assert "gen.txt" in patch
    # The blocked attempt's created file was moved aside, then re-created by the re-run.
    assert (h.run_dir / "phases" / "gate" / "discarded-1" / "gen.txt").exists()
    assert (git_repo / "gen.txt").exists()


def test_snapshot_is_proactive_baseline_is_pre_attempt(tmp_path, git_repo):
    # impl mutates a committed file; if the snapshot were taken at block time it
    # would bless the bad attempt. The discarded/restore evidence proves the
    # baseline predates the first attempt.
    spec = {"outputs": ["w"], "write_files": {"a.txt": "attempt\n", "gen.txt": "g\n"}}
    h = build(tmp_path, heal_flow([BLOCK, PASS], impl_spec=spec), git_repo)
    assert h.engine.run() == 0
    patch = (h.run_dir / "phases" / "gate" / "attempt-1.patch").read_text(encoding="utf-8")
    assert "original" in patch and "attempt" in patch, "patch diffs baseline vs blocked attempt"


def test_malformed_verdict_is_terminal_no_rollback_no_round(tmp_path, git_repo):
    h = build(tmp_path, heal_flow(["garbage", "more garbage"]), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.verdicts["gate"] == "block: no valid verdict emitted"
    assert st.nodes["gate"].heal_round == 0, "no round consumed"
    assert len(calls_of(h, "impl")) == 1, "no heal re-run"
    assert (git_repo / "gen.txt").exists(), "no rollback: the work is untouched"
    assert not (h.run_dir / "phases" / "gate" / "discarded-1").exists()


def test_scope_from_git_not_files_written(tmp_path, git_repo):
    # A deliberately OVER-reporting fake: claims it wrote pre-existing files it
    # never touched. Rollback must not revert them (SPEC §9.4.4).
    (git_repo / "pre.txt").write_text("precious\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "pre")
    over_reporting = {
        "outputs": [
            {"step_id": "impl", "status": "done", "files_written": ["pre.txt", "a.txt", "gen.txt"]}
        ],
        "write_files": {"gen.txt": "only this\n"},
    }
    f = heal_flow([BLOCK, PASS], impl_spec=over_reporting)
    f["nodes"][0]["output"] = "json"
    f["nodes"][0]["contract"] = "StepResult"
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert (git_repo / "pre.txt").read_text(encoding="utf-8") == "precious\n"
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "original\n"
    # Only the actually-changed file went through the discard path.
    discarded = h.run_dir / "phases" / "gate" / "discarded-1"
    assert (discarded / "gen.txt").exists()
    assert not (discarded / "pre.txt").exists() and not (discarded / "a.txt").exists()


def test_cascade_invalidates_completed_sibling(tmp_path, git_repo):
    # A PASSED sibling that consumed the target's output is re-marked pending on
    # heal — restoring the tree beneath it would orphan its outputs (§9.4.5).
    sibling = {
        "id": "sib", "kind": "fake", "depends_on": ["impl"],
        "spec": {"task": "read {steps.impl.output}", "outputs": ["sib-result"], "readonly": True},
    }
    h = build(tmp_path, heal_flow([BLOCK, PASS], extra_nodes=(sibling,)), git_repo)
    assert h.engine.run() == 0
    assert len(calls_of(h, "sib")) == 2, "sibling re-ran after cascade invalidation"
    assert load_state(h.run_dir).nodes["sib"].status == "done"


def test_max_rounds_respected_then_terminal(tmp_path, git_repo):
    h = build(tmp_path, heal_flow([BLOCK, BLOCK, BLOCK], max_rounds=1), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.nodes["gate"].heal_round == 1
    assert len(calls_of(h, "impl")) == 2
    assert st.nodes["after"].status == "blocked"


def test_rollback_on_nongit_tree_refuses_exit7(tmp_path, plain_repo):
    h = build(tmp_path, heal_flow([BLOCK, PASS]), plain_repo)
    with pytest.raises(RunRefusal):
        h.engine.run()


def test_heal_baseline_survives_resume(tmp_path, git_repo):
    # Audit r6 blocker: the baseline must be PERSISTED. Session 1: the target
    # executes (baseline snapshotted pre-attempt, persisted), the gate emits
    # garbage => terminal block, tree still holds the attempt's gen.txt.
    # Session 2 (a fresh process = no in-memory snapshots): the gate blocks
    # validly => heal must restore to session 1's pre-attempt baseline. If a
    # block-time snapshot were taken instead, gen.txt would be IN the baseline
    # and discarded-1/ would stay empty.
    from conftest import rebuild
    from lockstep.state import load_state

    f1 = heal_flow(["garbage"])
    h1 = build(tmp_path, f1, git_repo)
    assert h1.engine.run() == 2
    st1 = load_state(h1.run_dir)
    assert st1.heal_baselines.get("gate"), "baseline persisted at snapshot time"
    assert (git_repo / "gen.txt").exists()

    # Same flow shape; only outputs differ (not a fingerprint input for fake).
    f2 = heal_flow([BLOCK, PASS])
    h2 = rebuild(tmp_path, f2, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert (h1.run_dir / "phases" / "gate" / "discarded-1" / "gen.txt").exists(), (
        "restore used the TRUE pre-attempt baseline from session 1"
    )
    assert load_state(h1.run_dir).heal_baselines == {}, "baseline cleared on gate pass"


def test_healed_node_hash_is_stable_across_resume(tmp_path, git_repo):
    # Heal text folds into the target's input_hash, so it lives in RunState, not
    # in the Runner: a fresh process must re-plan the prompt the healed spawn
    # actually saw. Same reasoning as r6 C2's whole-mailbox rendering.
    # Session 1: the gate blocks once, `impl` heals, the gate passes. rollback is
    # off to isolate hashing from restore.
    from conftest import rebuild

    f = heal_flow([BLOCK, PASS], rollback=False)
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    assert len(calls_of(h1, "impl")) == 2, "first attempt + one heal re-run"

    # Session 2: fresh process, nothing changed on disk. The heal text is part of
    # what produced `impl`'s stored result, so re-planning must reproduce it —
    # otherwise a healed node is permanently uncacheable.
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "impl") == [], "a healed node must not re-run on an unchanged resume"


def test_missing_baseline_fails_closed(tmp_path, git_repo):
    # A rollback heal whose target hash-skipped (so no fresh snapshot fires)
    # and whose lineage has no persisted baseline must terminal-block — never
    # snapshot at block time (§9.4.2). Simulates a pre-persistence lineage.
    from conftest import rebuild
    from lockstep.state import load_state, write_state

    h1 = build(tmp_path, heal_flow(["garbage"]), git_repo)
    assert h1.engine.run() == 2  # terminal invalid-verdict block; impl ran once, no heal_text
    st = load_state(h1.run_dir)
    st.heal_baselines = {}
    write_state(h1.run_dir, st)
    h2 = rebuild(tmp_path, heal_flow([BLOCK, PASS]), git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 2
    rec = load_state(h1.run_dir).nodes["gate"]
    assert "baseline missing" in rec.error
    assert h2.fake.calls and all(c.node_id == "gate" for c in h2.fake.calls), (
        "impl hash-skipped (so no new snapshot); only the gate re-ran"
    )


def test_map_target_heal_clears_items(tmp_path, git_repo):
    # A3/§9.4.6: a map heal target re-runs ALL items.
    f = {
        "name": "map-heal",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'], "readonly": True}, "output": "json", "contract": "PathManifest"},
            {
                "id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
                "over": "{steps.src.json.files}", "concurrency": 1,
                "spec": {"task": "handle {item}", "outputs": ["r"]},
            },
            {
                "id": "gate", "role": "gate", "kind": "fake", "depends_on": ["m"],
                "spec": {"outputs": [BLOCK, PASS], "readonly": True},
                "output": "json", "contract": "Verdict",
                "heal": {"max_rounds": 1, "targets": ["m"], "rollback": False},
            },
            {"id": "after", "kind": "fake", "depends_on": ["gate"], "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert len(calls_of(h, "m")) == 4, "2 items x 2 rounds — all items re-run on heal"


def test_cascade_clears_descendant_map_items(tmp_path, git_repo):
    # A3.5: the cascade clears item records of invalidated DESCENDANT maps too.
    # impl's re-run output is identical, so without the clear the items would
    # hash-match and wrongly skip.
    f = {
        "name": "cascade-map",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'], "readonly": True}, "output": "json", "contract": "PathManifest"},
            {"id": "impl", "kind": "fake", "spec": {"outputs": ["same output every round"]}},
            {
                "id": "m", "role": "map", "kind": "fake", "depends_on": ["src", "impl"],
                "over": "{steps.src.json.files}", "concurrency": 1,
                "spec": {"task": "handle {item} given {steps.impl.output}", "outputs": ["r"]},
            },
            {
                "id": "gate", "role": "gate", "kind": "fake", "depends_on": ["m"],
                "spec": {"outputs": [BLOCK, PASS], "readonly": True},
                "output": "json", "contract": "Verdict",
                "heal": {"max_rounds": 1, "targets": ["impl"], "rollback": False},
            },
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert len(calls_of(h, "impl")) == 2
    assert len(calls_of(h, "m")) == 4, "descendant map items cleared: 2 items x 2 rounds"


def test_heal_retry_restates_declared_write_scope(tmp_path, git_repo):
    # E5 (LESSONS-TO-MECHANISMS): gate findings + "address this precisely" read
    # as authorization to chase a finding into files outside the node's scope.
    # The engine restates the target's own declared scope inside the heal text,
    # so no flow author has to remember a defensive clause.
    impl = {"outputs": ["did work"], "write_files": {"gen.txt": "generated\n"},
            "writes": ["gen.txt"]}
    h = build(tmp_path, heal_flow([BLOCK, PASS], impl_spec=impl), git_repo)
    assert h.engine.run() == 0
    retry_prompt = calls_of(h, "impl")[1].prompt
    assert "you may modify only gen.txt" in retry_prompt
    assert "do not edit it" in retry_prompt
    # A target with no declared scope gets no scope line (nothing to restate).
    h2 = build(tmp_path, heal_flow([BLOCK, PASS]), git_repo)
    assert h2.engine.run() == 0
    assert "write scope is UNCHANGED" not in calls_of(h2, "impl")[1].prompt


def test_heal_retry_carries_attempt_notes(tmp_path, git_repo):
    # E3 (LESSONS-TO-MECHANISMS): heal preserves the phase dir by design but
    # fed none of it back — a retried node re-derived from zero what a prior
    # attempt spent real evidence establishing. attempt-notes.md is the carry.
    impl = {"outputs": ["did work"], "write_files": {"gen.txt": "generated\n"},
            "write_phase_files": {
                "attempt-notes.md": "failure X pre-dates this change, confirmed via stash"}}
    h = build(tmp_path, heal_flow([BLOCK, PASS], impl_spec=impl), git_repo)
    assert h.engine.run() == 0
    first, retry = calls_of(h, "impl")[0].prompt, calls_of(h, "impl")[1].prompt
    assert "prior.attempt.notes" in retry
    assert "pre-dates this change" in retry
    assert "prior.attempt.notes" not in first


# --------------------------------------------------------- E4: baseline gates

F_OLD = {"severity": "blocker", "category": "lint", "file": "old.py",
         "claim": "pre-existing debt", "evidence": "e"}
F_NEW = {"severity": "blocker", "category": "lint", "file": "new.py",
         "claim": "fresh defect", "evidence": "e"}


def baseline_flow(gate_outputs):
    return {
        "name": "base",
        "nodes": [
            {"id": "impl", "kind": "fake", "spec": {"outputs": ["w"], "writes": []}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "spec": {"outputs": gate_outputs, "readonly": True, "baseline": True},
             "output": "json", "contract": "Verdict"},
            {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    }


def test_baseline_gate_passes_when_every_finding_predates_the_run(tmp_path, git_repo):
    base_v = {"findings": [F_OLD], "verdict": "block", "reason": "1 finding"}
    run_v = {"findings": [F_OLD], "verdict": "block", "reason": "1 finding"}
    h = build(tmp_path, baseline_flow([base_v, run_v]), git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.verdicts["gate"] == "pass"
    assert st.baseline_findings["gate"], "pre-run findings persisted"


def test_baseline_gate_blocks_only_on_new_findings(tmp_path, git_repo):
    base_v = {"findings": [F_OLD], "verdict": "block", "reason": "1 finding"}
    run_v = {"findings": [F_OLD, F_NEW], "verdict": "block", "reason": "2 findings"}
    h = build(tmp_path, baseline_flow([base_v, run_v]), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert "suppressed by the pre-run baseline" in st.verdicts["gate"]


def test_baseline_survives_resume_not_remeasured(tmp_path, git_repo):
    # The persisted baseline is the run's baseline: a resume filters against
    # what the run STARTED from, and does not re-run the baseline body.
    base_v = {"findings": [F_OLD], "verdict": "block", "reason": "1"}
    run_v = {"findings": [F_OLD], "verdict": "block", "reason": "1"}
    h1 = build(tmp_path, baseline_flow([base_v, run_v]), git_repo)
    assert h1.engine.run() == 0
    from conftest import rebuild

    h2 = rebuild(tmp_path, baseline_flow([base_v, run_v]), git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "gate") == [], "neither baseline body nor gate re-ran"


def test_baseline_flip_is_visible_downstream(tmp_path, git_repo):
    # Spec-audit finding: a block->pass flip that only reached state.verdicts
    # left {steps.gate.json.verdict} reading the raw "block" — the stored
    # result must be the adjudicated verdict.
    import json as _json

    base_v = {"findings": [F_OLD], "verdict": "block", "reason": "1 finding"}
    run_v = {"findings": [F_OLD], "verdict": "block", "reason": "1 finding"}
    h = build(tmp_path, baseline_flow([base_v, run_v]), git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    stored = _json.loads(
        open(st.nodes["gate"].result_path, encoding="utf-8").read())
    assert stored["verdict"] == "pass", "downstream references read the adjudicated verdict"
    assert stored["findings"] == []


def test_budget_trip_during_baseline_recording_exits_4(tmp_path, git_repo):
    # Spec-audit finding: BudgetTripped escaping _record_gate_baselines was an
    # unhandled traceback (exit 1) — §9.5 freezes the trip at exit 4, and a
    # resume with headroom records the baseline against the still-pre-run tree.
    def flow(cap):
        f = baseline_flow([
            {"findings": [F_OLD], "verdict": "block", "reason": "1"},
            {"findings": [F_OLD], "verdict": "block", "reason": "1"},
        ])
        f["budget"] = {"max_agent_spawns": cap, "max_run_minutes": 120}
        return f

    h = build(tmp_path, flow(0), git_repo)
    assert h.engine.run() == 4
    st = load_state(h.run_dir)
    assert "gate" not in st.baseline_findings, "nothing recorded under a tripped budget"
    assert all(r.status == "pending" for r in st.nodes.values())
    from conftest import rebuild

    h2 = rebuild(tmp_path, flow(10), git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert load_state(h2.run_dir).baseline_findings["gate"], "baseline recorded on resume"


def test_baseline_body_never_runs_under_replay(tmp_path, git_repo):
    # Adversarial-review finding 5: under --replay the wrapper would serve the
    # gate's recorded (post-run, adjudicated) verdict as the "pre-run"
    # baseline. Recorded gate results are already adjudicated, so replay skips
    # the baseline machinery entirely.
    base_v = {"findings": [F_OLD], "verdict": "block", "reason": "1"}
    run_v = {"findings": [F_OLD], "verdict": "block", "reason": "1"}
    h = build(tmp_path, baseline_flow([base_v, run_v]), git_repo)
    h.engine.replaying = True
    h.engine.run()
    st = load_state(h.run_dir)
    assert st.baseline_findings == {}, "no baseline recorded under replay"
    # Exactly one gate spawn: the evaluation, not a baseline body.
    assert len(calls_of(h, "gate")) == 1


def test_baseline_gate_when_referencing_steps_is_legal(tmp_path):
    # Nit 9a: `when` gates the EVALUATION (post-deps); the baseline body never
    # renders it — verify must not reject it.
    from lockstep.taskgraph import TaskGraph, verify_flow

    from conftest import make_config

    flow = TaskGraph.model_validate({
        "name": "bw",
        "nodes": [
            {"id": "impl", "kind": "fake", "spec": {"outputs": ["w"], "writes": []}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "when": "{steps.impl.output} == \"w\"",
             "spec": {"outputs": [], "readonly": True, "baseline": True},
             "output": "json", "contract": "Verdict"},
            {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True},
        ],
    })
    from lockstep.executors.fake import FakeExecutor
    from lockstep.registry import Registry

    reg = Registry()
    reg.register(FakeExecutor(repo_root=tmp_path))
    codes = {i.code for i in verify_flow(flow, registry=reg, config=make_config())}
    assert "baseline-gate-references-steps" not in codes
    # but a steps ref in the BODY still errors
    flow2 = TaskGraph.model_validate({
        "name": "bw2",
        "nodes": [
            {"id": "impl", "kind": "fake", "spec": {"outputs": ["w"], "writes": []}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "spec": {"task": "judge {steps.impl.output}", "outputs": [],
                      "readonly": True, "baseline": True},
             "output": "json", "contract": "Verdict", "final": True},
        ],
    })
    codes2 = {i.code for i in verify_flow(flow2, registry=reg, config=make_config())}
    assert "baseline-gate-references-steps" in codes2


def test_rollback_walks_the_tree_once_for_both_answers(tmp_path, git_repo):
    """P1-perf: `diff_patch` and `changed_paths` each snapshot internally, so a
    rollback used to walk and hash the whole working tree TWICE for one
    decision — the expensive operation in the engine, measured at 1.4s per walk
    on a 47MB tree (contrib/snapshot_bench.py).

    Sharing one snapshot is also the more correct reading of §9.4.4: the
    preserved patch and the restore scope now describe the SAME tree, where two
    snapshots described two moments and a file written between them would be
    restored without appearing in the evidence.
    """
    import json

    h = build(tmp_path, heal_flow([BLOCK, PASS]), git_repo)
    assert h.engine.run() == 0
    events = [json.loads(ln) for ln in
              (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    ops = [e["op"] for e in events if e.get("kind") == "timing" and e.get("node") == "gate"]
    # One baseline (proactive, before the target ran) + one shared current.
    assert ops.count("heal-baseline") == 1, ops
    assert ops.count("heal-current") == 1, ops
    # The two consumers of that snapshot still run — they just don't take one.
    assert ops.count("heal-patch") == 1 and ops.count("heal-diff") == 1, ops
    # And the evidence still agrees with what was rolled back.
    patch = (h.run_dir / "phases" / "gate" / "attempt-1.patch").read_text(encoding="utf-8")
    assert "gen.txt" in patch
    assert (h.run_dir / "phases" / "gate" / "discarded-1" / "gen.txt").exists()


# ---------------------- heal.on_exhausted (parity phase B, 2026-08-14)


def exhaust_flow(gate_outputs, *, on_exhausted="pass", max_rounds=1, after_when=None):
    """A loop that never converges: the gate blocks every round. rollback is
    False throughout - on_exhausted: "pass" is forbidden with rollback by
    verify (on-exhausted-with-rollback), and these tests exercise the engine
    path behind that rule."""
    after = {"id": "after", "kind": "fake", "depends_on": ["gate"],
             "spec": {"outputs": ["ok"], "readonly": True}, "final": True}
    if after_when:
        after["when"] = after_when
    return {
        "name": "exhaust",
        "nodes": [
            {"id": "impl", "kind": "fake",
             "spec": {"outputs": ["v1", "v2"], "write_files": {"gen.txt": "generated\n"}}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "spec": {"outputs": gate_outputs, "readonly": True},
             "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": max_rounds, "targets": ["impl"],
                      "rollback": False, "on_exhausted": on_exhausted}},
            after,
        ],
    }


def test_exhausted_pass_accepts_best_so_far_and_says_so_everywhere(tmp_path, git_repo):
    """on_exhausted: "pass" must never record a plain pass. Three channels,
    each checked: the STORED result is the adjudicated verdict (downstream
    refs and `when` read it), state.verdicts carries the same reason (so
    `status` never shows a blocked gate as satisfied), and the journal gets
    its own event (so the trace distinguishes acceptance from success)."""
    h = build(tmp_path, exhaust_flow(
        [BLOCK, BLOCK], after_when='{steps.gate.json.verdict} == "pass"'), git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["gate"].status == "done"
    assert st.nodes["gate"].heal_round == 1
    assert len(calls_of(h, "impl")) == 2, "the one heal round ran"
    stored = json.loads(
        (h.run_dir / "phases" / "gate" / "result.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == "pass"
    assert stored["reason"] == "accepted after 1 round(s) without resolving: not good enough"
    assert len(stored["findings"]) == 1, "the unresolved findings ARE what was accepted"
    assert st.verdicts["gate"] == (
        "pass: accepted after 1 round(s) without resolving: not good enough")
    events = [json.loads(ln) for ln in
              (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
              if ln.strip()]
    accepted = [e for e in events if e.get("status") == "heal-exhausted-pass"]
    assert len(accepted) == 1 and accepted[0]["node"] == "gate" and accepted[0]["round"] == 1
    # The downstream `when` read the ADJUDICATED verdict, not the raw block.
    assert st.nodes["after"].status == "done"


def test_exhausted_block_stays_the_default(tmp_path, git_repo):
    h = build(tmp_path, exhaust_flow([BLOCK, BLOCK], on_exhausted="block"), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.nodes["gate"].status == "blocked"
    assert st.verdicts["gate"].startswith("block: not good enough")


def test_a_gate_that_never_decided_cannot_exhaust_to_pass(tmp_path, git_repo):
    """SPEC 9.4.3 extended to acceptance: a malformed verdict (or a timeout)
    is not a block whose rounds ran out - it is a gate that never decided,
    and it terminal-blocks whatever on_exhausted says."""
    h = build(tmp_path, exhaust_flow([BLOCK, "garbage", "more garbage"]), git_repo)
    assert h.engine.run() == 2
    st = load_state(h.run_dir)
    assert st.nodes["gate"].status == "blocked"
    assert st.verdicts["gate"] == "block: no valid verdict emitted"


def test_the_heal_prompt_names_the_round(tmp_path, git_repo):
    """Parity 2.1's first gap: the body could not tell which round it was in.
    The round rides in the ENGINE-COMPOSED heal text (finding 17: not a
    {round} interpolation form - reference forms are a SPEC 7 surface, and
    heal_texts already folds into both the prompt and the hash)."""
    h = build(tmp_path, heal_flow([BLOCK, PASS]), git_repo)
    assert h.engine.run() == 0
    retry_prompt = calls_of(h, "impl")[1].prompt
    assert "This is repair round 1 of 1 for gate 'gate'." in retry_prompt


def test_an_accepted_gate_survives_resume_without_rerunning(tmp_path, git_repo):
    """The quiet-untruth risk in on_exhausted: "pass" is a RESUME that re-runs
    the gate and re-blocks - or worse, silently replaces the adjudicated
    result with a raw block. An unchanged resume must hash-match the gate,
    keep it done, and keep the rewritten verdict (2026-08-14 adversarial
    review of phase B)."""
    from conftest import rebuild

    h1 = build(tmp_path, exhaust_flow([BLOCK, BLOCK]), git_repo)
    assert h1.engine.run() == 0
    h2 = rebuild(tmp_path, exhaust_flow([BLOCK, BLOCK]), git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "impl") == [], "healed target must stay cached"
    assert calls_of(h2, "gate") == [], "accepted gate must stay done"
    stored = json.loads(
        (h1.run_dir / "phases" / "gate" / "result.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == "pass"
    assert stored["reason"].startswith("accepted after 1 round(s)")
