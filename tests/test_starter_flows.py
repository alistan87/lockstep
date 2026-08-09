"""Invariants the ten starter templates must keep.

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
