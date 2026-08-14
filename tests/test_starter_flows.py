"""Invariants the starter templates must keep.

They are copied into other repos and imitated by every flow written after
them, so a defect here propagates. Each test below pins a defect the
2026-08-09 adversarial review actually found — `lockstep verify` passed all
ten the whole time, because none of these is a schema question.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

STARTER = Path(__file__).resolve().parents[1] / "flows" / "starter"


def flow(name: str) -> dict:
    return json.loads((STARTER / f"{name}.tg.json").read_text(encoding="utf-8"))


def nodes(name: str) -> dict[str, dict]:
    return {n["id"]: n for n in flow(name)["nodes"]}


@pytest.mark.parametrize("path", sorted(STARTER.glob("*.tg.json")), ids=lambda p: p.stem)
def test_every_interpolated_step_is_a_declared_dependency(path):
    """`verify` enforces this (`unlisted-step-ref`); asserting it offline keeps
    the templates honest without a config on the machine running the suite."""
    import re

    d = json.loads(path.read_text(encoding="utf-8"))
    for n in d["nodes"]:
        deps = set(n.get("depends_on") or [])
        refs = set(re.findall(r"\{steps\.([a-z0-9][a-z0-9-]*)", json.dumps(n.get("spec", {}))))
        refs |= set(re.findall(r"\{steps\.([a-z0-9][a-z0-9-]*)", n.get("over") or ""))
        assert refs <= deps, f"{path.stem}:{n['id']} references {refs - deps} without depending on it"


def test_no_starter_flow_reads_a_file_no_starter_node_writes():
    """`retrospect`'s final node rendered `PLAN.md` — a file only
    `plan-adversarial` writes. On a clean repo it printed "NOT FOUND"; on a repo
    where an earlier planning run had left a PLAN.md it rendered that unrelated
    document as the improvement batch, with nothing saying it was stale.
    """
    batch = nodes("retrospect")["batch"]
    blob = json.dumps(batch)
    assert "PLAN.md" not in blob, "retrospect must not render another flow's artifact"
    assert "{steps.arbiter.json}" in blob, "it should render its own arbiter's upheld proposals"
    assert "arbiter" in (batch.get("depends_on") or [])


def test_the_evidence_approval_reversibility_line_matches_what_the_flow_does():
    """The human decides from `--reversible`. The template shipped
    "nothing is saved outside the run folder until you approve" while `produce`
    writes the deliverable to the REPO ROOT before the approval runs — so the
    one sentence that tells them how much care this needs was false, in the
    template whose entire purpose is to demonstrate honest evidence.
    """
    cmd = nodes("evidence-approval")["render-evidence"]["spec"]["cmd"]
    text = cmd[cmd.index("--reversible") + 1]
    assert "run folder until you approve" not in text
    assert "{args.deliverable}" in text, "name the artifact that is already on disk"
    produce = nodes("evidence-approval")["produce"]["spec"]["task"]
    assert "repo root" in produce, "if this moves, the reversibility line must move with it"


def test_the_audit_fan_out_survives_one_bad_file():
    """A map without `optional` fails the whole node when any single item
    fails, so one unreadable file threw away up to 39 completed audits and the
    arbiter never ran. The arbiter must also be told what a failed slot looks
    like, or it reads one as a clean file."""
    audit = nodes("file-audit")["audit"]
    assert audit.get("optional") is True
    arbiter_task = nodes("file-audit")["arbiter"]["spec"]["task"]
    assert '"status": "failed"' in arbiter_task
    assert "do not\ntreat it as clean" in arbiter_task or "do not treat it as clean" in arbiter_task


def test_approval_nodes_declare_no_kind_and_no_spec():
    """`approval-with-kind` is a verify error, but the templates are what people
    copy: keep the shape correct at the source."""
    for path in STARTER.glob("*.tg.json"):
        for n in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
            if n.get("role") == "approval":
                assert "kind" not in n and "spec" not in n, f"{path.stem}:{n['id']}"


def test_the_manifest_lint_does_not_fire_on_the_flow_that_teaches_it():
    """`lint-map-over-manifest` fired on `file-audit`, which emits
    `path|content-fingerprint` exactly as the lint's own message instructs. A
    lint that is wrong on its canonical example is one readers learn to skip —
    and the repo's lint admission standard is that each names a real incident.
    """
    from lockstep.taskgraph import TaskGraph, lint_flow

    tg = TaskGraph.model_validate(flow("file-audit"))
    codes = [i.code for i in lint_flow(tg)]
    assert "lint-map-over-manifest" not in codes


def test_a_map_over_a_manifest_with_no_fingerprint_still_warns():
    """The other half: the lint must still catch the defect it exists for."""
    from lockstep.taskgraph import TaskGraph, lint_flow

    d = flow("file-audit")
    audit = next(n for n in d["nodes"] if n["id"] == "audit")
    audit["spec"]["task"] = "Audit exactly one file: {item}. Report Finding objects."
    codes = [i.code for i in lint_flow(TaskGraph.model_validate(d))]
    assert "lint-map-over-manifest" in codes


# ------------------------------------------- the readonly tiering (2026-08-09)

JUDGEMENT_NODES = {
    "plan-adversarial": ["rev-feasibility", "rev-completeness", "plan-gate"],
    "proposal-gate": ["rev-completeness", "rev-ambiguity", "arbiter"],
    "retrospect": ["analyst", "arbiter"],
    "file-audit": ["audit", "arbiter"],
    "implement-heal": ["review"],
    "bugfix-heal": ["diagnose", "review"],
    "sdlc-e2e": ["plan-review", "plan-gate", "review", "report"],
    "two-phase-remediation": ["review-repro", "review-fix"],
    "tournament-judge": ["judge"],
}


@pytest.mark.parametrize("name,ids", sorted(JUDGEMENT_NODES.items()))
def test_every_judgement_node_is_readonly(name, ids):
    """A node whose product is a judgement holds no `tree` token, cannot
    corrupt what it is judging, and on a request-metered plan cannot spend a
    round trip attempting an edit it is not there to make."""
    ns = nodes(name)
    for nid in ids:
        assert ns[nid]["spec"].get("readonly") is True, f"{name}:{nid}"


def test_no_readonly_node_is_told_to_run_a_command():
    """`readonly_argv` removes the shell — bash is a write vector, and
    `readonly` is what licenses the scheduler to drop the tree token. So a
    readonly node CANNOT run `git diff` or the repro, and a prompt that tells
    it to is the honour-system defect in reverse: an instruction the tools
    forbid. `diagnose` was told to run the repro; `review` to run git.
    """
    forbidden = ("run `git", "run git ", "Run `git", "run the repro", "Run the repro")
    for path in STARTER.glob("*.tg.json"):
        for n in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
            spec = n.get("spec", {})
            if not spec.get("readonly"):
                continue
            task = spec.get("task", "")
            hits = [f for f in forbidden if f in task]
            assert not hits, f"{path.stem}:{n['id']} is readonly but told to {hits}"


@pytest.mark.parametrize("name", ["implement-heal", "bugfix-heal", "sdlc-e2e"])
def test_the_diff_reaches_the_reviewer_as_data(name):
    """Tier B: a shell probe captures the change, the readonly reviewer judges
    it. The reviewer must depend on the probe and interpolate its output —
    otherwise it is a readonly node with no way to see what changed."""
    ns = nodes(name)
    assert ns["capture-diff"]["kind"] == "shell"
    assert ns["capture-diff"]["spec"]["cmd"][:3] == ["python", "-m", "lockstep.probes.worktree_diff"]
    review = ns["review"]
    assert "capture-diff" in review["depends_on"]
    assert "{steps.capture-diff.output}" in review["spec"]["task"]


def test_the_repro_is_observed_before_the_diagnosis():
    """`diagnose` was told to run the repro itself. A shell probe runs it now,
    which is also why the diagnostician can be readonly."""
    ns = nodes("bugfix-heal")
    probe = ns["observe-repro"]
    assert probe["kind"] == "shell"
    assert probe["spec"]["cmd"][:3] == ["python", "-m", "lockstep.probes.command_output"]
    assert "{args.repro}" in probe["spec"]["cmd"]
    diag = ns["diagnose"]
    assert "observe-repro" in diag["depends_on"]
    assert "{steps.observe-repro.output}" in diag["spec"]["task"]


@pytest.mark.parametrize("name", ["implement-heal", "bugfix-heal", "sdlc-e2e"])
def test_flows_that_interpolate_a_diff_raise_the_spill_cap(name):
    """A real diff blows past the 20000-char default, and the reviewer's prompt
    then carries a stub path instead of the change. Raised, and the prompt says
    what to do if it spills anyway."""
    d = flow(name)
    assert d.get("max_interp_chars", 20000) >= 60000
    assert "spilled" in nodes(name)["review"]["spec"]["task"]


# ------------------------------- the two-phase pattern (consumer report 2026-08-13)


def test_the_two_phase_template_never_captures_the_live_tree():
    """Its whole reason to exist. `worktree_diff` reports the tree AS IT IS NOW,
    and shell nodes always re-run on resume (SPEC 0.1.7) — so in a two-phase
    flow the phase-1 capture eventually describes phase 2's tree, the reviewer
    above it re-bills, and it blocks on violations that never existed. Both
    captures here read the trees the ENGINE recorded for one step.
    """
    ns = nodes("two-phase-remediation")
    for probe_id, target in (("repro-diff", "reproduce"), ("fix-diff", "remediate")):
        cmd = ns[probe_id]["spec"]["cmd"]
        assert cmd[:3] == ["python", "-m", "lockstep.probes.node_diff"], probe_id
        assert cmd[cmd.index("--node") + 1] == target, probe_id
    # Nodes only: the flow's DESCRIPTION names `worktree_diff` on purpose, to
    # say what this template is choosing against and why.
    assert "worktree_diff" not in json.dumps(flow("two-phase-remediation")["nodes"])


def test_the_two_phase_reviewers_read_their_own_phase():
    ns = nodes("two-phase-remediation")
    for reviewer, probe_id in (("review-repro", "repro-diff"), ("review-fix", "fix-diff")):
        spec = ns[reviewer]["spec"]
        assert probe_id in ns[reviewer]["depends_on"]
        assert "{steps.%s.output}" % probe_id in spec["task"]
        assert spec.get("readonly") is True
        assert "spilled" in spec["task"], "a real diff blows past the interpolation cap"
    assert flow("two-phase-remediation").get("max_interp_chars", 20000) >= 60000


# --------------------------------- the tournament template (parity phase A, 2026-08-14)

CANDIDATES = ["cand-simple", "cand-robust", "cand-rethink"]


def test_tournament_candidates_are_readonly():
    """Readonly is the tournament's load-bearing wall twice over: it is what
    lets three candidates share one wave (no `tree` token), and what makes N
    answers over one shared tree coherent at all — write-capable candidates
    would serialize into a slow sequence, each mutating the tree the next one
    reads. (The judge is covered by JUDGEMENT_NODES above.)"""
    ns = nodes("tournament-judge")
    for cid in CANDIDATES:
        assert ns[cid]["spec"].get("readonly") is True, cid


def test_tournament_pick_gate_is_the_library_call_with_the_real_candidate_ids():
    """The gate blocks a null winner and an invented one — but only if its
    --candidates list is the flow's actual candidate ids. If they drift apart,
    every legitimate winner is 'unknown' and the flow can never pass."""
    ns = nodes("tournament-judge")
    cmd = ns["pick-gate"]["spec"]["cmd"]
    assert cmd[:3] == ["python", "-m", "lockstep.gates.tournament_pick"]
    assert cmd[cmd.index("--candidates") + 1].split(",") == CANDIDATES
    assert "{steps.judge.json}" in cmd


def test_the_publish_step_never_puts_an_answer_on_the_command_line():
    """The first draft interpolated all three answers into publish's argv —
    the exact anti-pattern FLOW-AUTHORING rule 2 forbids: shell argv is
    neither capped nor spilled, so three answers approaching the advisory
    120-line cap could clear Windows' ~32k limit and fail the spawn (exit 127)
    AFTER every token was spent, deterministically on every resume (2026-08-14
    adversarial review, blocker). publish gets the winner ID via argv and
    reads the answer from the winner's phase dir via LOCKSTEP_PHASE_DIR — the
    same env-not-argv route the gate library and node_diff use. Every
    candidate id must still appear in argv (the eligibility list), each must
    still be a dependency (its result file must exist before publish runs),
    and no candidate OUTPUT may appear anywhere in the node."""
    ns = nodes("tournament-judge")
    publish = ns["publish"]
    cmd = publish["spec"]["cmd"]
    assert "{steps.judge.json.winner}" in cmd
    assert "LOCKSTEP_PHASE_DIR" in cmd[cmd.index("-c") + 1]
    for cid in CANDIDATES:
        assert cid in cmd, cid
        assert cid in publish["depends_on"], cid
        assert "{steps.%s.output}" % cid not in json.dumps(publish), cid


def test_the_judge_sees_every_answer_the_criteria_and_the_no_winner_exit():
    """A judge shown two of three answers silently judges a smaller
    tournament; a judge never told that null is a legal winner crowns the
    least-bad instead of blocking; and three answers can spill past the
    interpolation cap."""
    d = flow("tournament-judge")
    task = nodes("tournament-judge")["judge"]["spec"]["task"]
    for cid in CANDIDATES:
        assert "{steps.%s.output}" % cid in task, cid
    assert "{args.criteria}" in task
    assert "null" in task
    assert "spilled" in task
    assert d.get("max_interp_chars", 20000) >= 60000


def test_the_tournament_contract_resolves_from_the_file_beside_the_flow():
    """The starter set's one custom contract: `contracts_module` is a
    repo-root-relative path, so the flow and its module must travel together
    (the README says so; this pins it)."""
    d = flow("tournament-judge")
    assert d["contracts_module"] == "flows/starter/tournament_contracts.py"
    module_path = STARTER / "tournament_contracts.py"
    assert module_path.exists()
    from lockstep.contracts import resolve_contract

    ref = resolve_contract(f"{module_path}:TournamentPick")
    assert {"winner", "ranking", "rationale"} <= set(ref.model.model_fields)
    assert nodes("tournament-judge")["judge"]["contract"] == "TournamentPick"


def test_the_candidate_lenses_actually_differ():
    """Three copies of one prompt is paying triple for one answer plus a
    judge; the assigned angles are the tournament's entire value."""
    tasks = [nodes("tournament-judge")[c]["spec"]["task"] for c in CANDIDATES]
    assert len(set(tasks)) == 3
    for t in tasks:
        assert "{args.task}" in t and "{args.criteria}" in t


def test_node_diff_targets_are_scoped_and_serialized():
    """`node_diff` can only answer for a node the engine recorded trees for:
    one that declares `spec.writes` and is NOT readonly (a readonly node holds
    no `tree` token, and the write-scope baseline is only taken inside it).
    A template whose targets drifted would print an explanation instead of a
    diff, and the reviewer above it would judge nothing at all."""
    ns = nodes("two-phase-remediation")
    for target in ("reproduce", "remediate"):
        spec = ns[target]["spec"]
        assert "writes" in spec, target
        assert not spec.get("readonly"), target
