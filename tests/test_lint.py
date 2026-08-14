"""A2 — `verify --lint`: advisory warnings with incidents behind them, never a
changed exit code."""

from __future__ import annotations

import json

from lockstep import EXIT_OK
from lockstep.cli import main
from lockstep.registry import ExecutorStanza, LockstepConfig
from lockstep.taskgraph import TaskGraph, lint_flow


def codes(issues):
    return [i.code for i in issues]


def tg(flow: dict) -> TaskGraph:
    return TaskGraph.model_validate(flow)


def test_w1_token_work_downstream_of_an_approval():
    flow = tg({
        "name": "w1",
        "nodes": [
            {"id": "work", "kind": "fake", "spec": {}},
            {"id": "ok", "role": "approval", "depends_on": ["work"]},
            {"id": "after", "kind": "fake", "depends_on": ["ok"], "spec": {}},
            {"id": "quick", "kind": "shell", "final": True, "depends_on": ["after"],
             "spec": {"cmd": ["git", "status"]}},
        ],
    })
    found = codes(lint_flow(flow))
    assert found.count("lint-work-after-approval") == 1  # `after`, not `quick` or `work`


def test_w2_map_over_pathmanifest_reminds_about_fingerprints():
    flow = tg({
        "name": "w2",
        "budget": {"max_agent_spawns": 5, "max_run_minutes": 10},
        "nodes": [
            {"id": "ls", "kind": "shell", "output": "json", "contract": "PathManifest",
             "spec": {"cmd": ["python", "-c", "pass"]}},
            {"id": "fan", "role": "map", "kind": "fake", "final": True, "concurrency": 1,
             "depends_on": ["ls"], "over": "{steps.ls.json.files}",
             "spec": {"task": "{item}", "readonly": True}},
        ],
    })
    assert "lint-map-over-manifest" in codes(lint_flow(flow))


def test_w3_map_without_budget():
    base = {
        "name": "w3",
        "nodes": [
            {"id": "ls", "kind": "shell", "output": "json", "contract": "PathManifest",
             "spec": {"cmd": ["python", "-c", "pass"]}},
            {"id": "fan", "role": "map", "kind": "fake", "final": True, "concurrency": 1,
             "depends_on": ["ls"], "over": "{steps.ls.json.files}",
             "spec": {"task": "{item}", "readonly": True}},
        ],
    }
    assert "lint-map-without-budget" in codes(lint_flow(tg(base)))
    with_budget = {**base, "budget": {"max_agent_spawns": 5, "max_run_minutes": 10}}
    assert "lint-map-without-budget" not in codes(lint_flow(tg(with_budget)))


def test_w5_parallel_map_that_would_serialize():
    flow = tg({
        "name": "w5",
        "budget": {"max_agent_spawns": 5, "max_run_minutes": 10},
        "nodes": [
            {"id": "ls", "kind": "shell", "output": "json", "contract": "PathManifest",
             "spec": {"cmd": ["python", "-c", "pass"]}},
            {"id": "fan", "role": "map", "kind": "fake", "final": True, "concurrency": 3,
             "depends_on": ["ls"], "over": "{steps.ls.json.files}",
             "spec": {"task": "{item}"}},
        ],
    })
    assert "lint-serialized-map" in codes(lint_flow(flow))


def test_w4_argv_prompting_flagged_once_per_stanza():
    flow = tg({
        "name": "w4",
        "nodes": [
            {"id": "one", "kind": "harness", "spec": {"task": "x"}},
            {"id": "two", "kind": "harness", "final": True, "depends_on": ["one"],
             "spec": {"task": "y"}},
        ],
    })
    argv_cfg = LockstepConfig(
        default="c", executors={"c": ExecutorStanza(argv=["c", "{prompt}"], prompt_via="argv")}
    )
    assert codes(lint_flow(flow, argv_cfg)).count("lint-argv-prompt") == 1
    stdin_cfg = LockstepConfig(
        default="c", executors={"c": ExecutorStanza(argv=["c"], prompt_via="stdin")}
    )
    assert "lint-argv-prompt" not in codes(lint_flow(flow, stdin_cfg))
    assert "lint-argv-prompt" not in codes(lint_flow(flow))  # no config: skipped


def test_cli_lint_never_changes_the_exit_code(tmp_path, capsys):
    flow = {
        "name": "clean-but-linted",
        "nodes": [
            {"id": "work", "kind": "fake", "spec": {}},
            {"id": "gate", "role": "approval", "depends_on": ["work"]},
            {"id": "after", "kind": "fake", "final": True, "depends_on": ["gate"], "spec": {}},
        ],
    }
    path = tmp_path / "f.tg.json"
    path.write_text(json.dumps(flow), encoding="utf-8")
    code = main(["verify", str(path), "--repo-root", str(tmp_path), "--lint"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "lint-work-after-approval" in out
    assert "SKIPPED" in out  # no lockstep.toml in tmp repo root: config lints named as skipped


def test_v1_missing_write_scope_fires_only_on_unscoped_mutators():
    flow = tg({
        "name": "v1",
        "nodes": [
            {"id": "w", "kind": "harness", "spec": {"task": "edit stuff"}},
            {"id": "scoped", "kind": "harness", "depends_on": ["w"],
             "spec": {"task": "x", "writes": ["docs/notes.md"]}},
            {"id": "probe", "kind": "shell", "depends_on": ["scoped"],
             "spec": {"cmd": ["git", "status"], "writes": []}},
            {"id": "ro", "kind": "harness", "depends_on": ["probe"], "final": True,
             "spec": {"task": "r", "readonly": True}},
        ],
    })
    found = codes(lint_flow(flow))
    assert found.count("lint-missing-write-scope") == 1, found  # only `w`


def test_v1_whole_tree_writes_requires_rationale():
    def node(spec):
        return tg({"name": "v1b", "nodes": [
            {"id": "w", "kind": "harness", "final": True, "spec": spec}]})

    bare = node({"task": "x", "writes": ["**"]})
    assert "lint-unscoped-writes" in codes(lint_flow(bare))
    stated = node({"task": "x", "writes": ["**"],
                   "writes_rationale": "genuinely generic codemod; scope set per-use"})
    issues = codes(lint_flow(stated))
    assert "lint-unscoped-writes" not in issues
    assert "lint-missing-write-scope" not in issues


def test_l1_ungated_mutation_variants():
    mut = {"id": "w", "kind": "harness", "spec": {"task": "x", "writes": ["src"]}}
    gate = {"id": "g", "role": "gate", "kind": "shell", "depends_on": ["w"], "final": True,
            "output": "json", "contract": "Verdict",
            "spec": {"cmd": ["python", "-c", "pass"], "writes": []}}
    # no gate/approval on either side -> fires
    f1 = tg({"name": "l1", "nodes": [{**mut, "final": True}]})
    assert "lint-ungated-mutation" in codes(lint_flow(f1))
    # downstream gate (review closure) -> silent
    f2 = tg({"name": "l1b", "nodes": [mut, gate]})
    assert "lint-ungated-mutation" not in codes(lint_flow(f2))
    # upstream approval (the evidence-approval deliver pattern) -> silent
    f3 = tg({"name": "l1c", "nodes": [
        {"id": "ok", "role": "approval"},
        {**mut, "depends_on": ["ok"], "final": True}]})
    assert "lint-ungated-mutation" not in codes(lint_flow(f3))
    # declared 'ungated' in the description -> silent
    f4 = tg({"name": "l1d",
             "description": "ungated by design - a human reads the output directly",
             "nodes": [{**mut, "final": True}]})
    assert "lint-ungated-mutation" not in codes(lint_flow(f4))
    # writes: [] is a declared non-mutation -> silent
    f5 = tg({"name": "l1e", "nodes": [
        {"id": "p", "kind": "shell", "final": True,
         "spec": {"cmd": ["git", "status"], "writes": []}}]})
    assert "lint-ungated-mutation" not in codes(lint_flow(f5))


# --- consumer report 2026-08-13 --------------------------------------------


def _capture(node_id, dep=None):
    n = {"id": node_id, "kind": "shell", "output": "text",
         "spec": {"cmd": ["python", "-m", "lockstep.probes.worktree_diff"], "writes": []}}
    if dep:
        n["depends_on"] = [dep]
    return n


def test_w5_a_multi_phase_flow_capturing_the_live_tree_twice():
    """Item 1: two `worktree_diff` captures in one flow means the second one
    runs against a tree the first phase's reviewer was never shown — and on
    resume the FIRST capture re-runs (shell nodes always do) against that same
    later tree, so a passed review re-bills and comes back contaminated."""
    flow = tg({
        "name": "phases",
        "nodes": [
            _capture("capture-1"),
            {"id": "review-1", "kind": "harness", "depends_on": ["capture-1"],
             "spec": {"task": "review {steps.capture-1.output}", "readonly": True}},
            {"id": "fix", "kind": "harness", "depends_on": ["review-1"],
             "spec": {"task": "fix", "writes": ["src"]}},
            _capture("capture-2", "fix"),
            {"id": "review-2", "kind": "harness", "final": True, "depends_on": ["capture-2"],
             "spec": {"task": "review {steps.capture-2.output}", "readonly": True}},
        ],
    })
    found = codes(lint_flow(flow))
    assert found.count("lint-live-diff-per-phase") == 1
    msg = next(i.message for i in lint_flow(flow) if i.code == "lint-live-diff-per-phase")
    assert "node_diff" in msg


def test_w5_is_silent_for_a_single_capture():
    """The single-phase shape every starter flow uses: capture once at the end,
    review it. Nothing has moved underneath it."""
    flow = tg({
        "name": "one",
        "nodes": [
            {"id": "impl", "kind": "harness", "spec": {"task": "do", "writes": ["src"]}},
            _capture("capture", "impl"),
            {"id": "review", "kind": "harness", "final": True, "depends_on": ["capture"],
             "spec": {"task": "review {steps.capture.output}", "readonly": True}},
        ],
    })
    assert "lint-live-diff-per-phase" not in codes(lint_flow(flow))


def test_w6_a_tools_allowlist_that_drops_the_extensions_own_tool():
    """Item 5: `--tools` covers EXTENSION tools too, so an allowlist that does
    not name `submit_result` silently removes the guard extension's structured
    output channel — the node still answers on stdout (§8.3), so nothing fails
    loudly; the envelope simply stops being enforced."""
    flow = tg({
        "name": "ro",
        "nodes": [
            {"id": "rev", "kind": "harness", "final": True, "output": "json",
             "contract": "Verdict", "spec": {"task": "judge", "readonly": True}},
        ],
    })
    cfg = LockstepConfig(default="pi", executors={"pi": ExecutorStanza(
        argv=["pi", "--extension", "guard.ts", "{prompt}"],
        readonly_argv=["--tools", "read,grep,find,ls"],
    )})
    assert codes(lint_flow(flow, cfg)).count("lint-tools-drops-result-channel") == 1
    ok = LockstepConfig(default="pi", executors={"pi": ExecutorStanza(
        argv=["pi", "--extension", "guard.ts", "{prompt}"],
        readonly_argv=["--tools", "read,submit_result"],
    )})
    assert "lint-tools-drops-result-channel" not in codes(lint_flow(flow, ok))


def test_w6_needs_an_extension_before_it_can_complain():
    """No `--extension` means no extension tool to drop; a bare allowlist is
    just a tool restriction, which is the whole point of readonly_argv."""
    flow = tg({"name": "ro2", "nodes": [
        {"id": "rev", "kind": "harness", "final": True, "spec": {"task": "j", "readonly": True}}]})
    cfg = LockstepConfig(default="c", executors={"c": ExecutorStanza(
        argv=["c", "{prompt}"], readonly_argv=["--tools", "read,grep"])})
    assert "lint-tools-drops-result-channel" not in codes(lint_flow(flow, cfg))
    assert "lint-tools-drops-result-channel" not in codes(lint_flow(flow))  # no config: skipped


def test_w6_also_catches_an_explicit_exclusion():
    flow = tg({"name": "ro3", "nodes": [
        {"id": "rev", "kind": "harness", "final": True, "spec": {"task": "j", "readonly": True}}]})
    cfg = LockstepConfig(default="c", executors={"c": ExecutorStanza(
        argv=["c", "--extension", "g.ts", "--exclude-tools", "write,submit_result", "{prompt}"],
        readonly_argv=["--tools", "read,submit_result"])})
    assert "lint-tools-drops-result-channel" in codes(lint_flow(flow, cfg))


# --- consumer report 2026-08-14 --------------------------------------------


READONLY_PERSONA = """---
name: judge
description: Reads and reports; writes nothing.
readonly: true
---
You are a judge. You read the evidence and report findings.
"""

WRITER_PERSONA = """---
name: builder
description: Writes code.
---
You are a builder.
"""


def _repo(tmp_path, **personas):
    d = tmp_path / "personas"
    d.mkdir(parents=True)
    for name, body in personas.items():
        (d / f"{name}.md").write_text(body, encoding="utf-8")
    return tmp_path


def _judge_node(spec_extra=None):
    spec = {"task": "judge it", "persona": "judge", **(spec_extra or {})}
    return {"id": "rev", "kind": "harness", "final": True, "spec": spec}


def test_w7_readonly_persona_on_a_node_that_never_says_readonly(tmp_path):
    """Item 1: `spec.persona` and `spec.readonly` are independent fields, so a
    node can name a persona whose own frontmatter says it is readonly while the
    node silently keeps the stanza's full write tools AND the exclusive tree
    token. The persona's restriction is then prose with nothing behind it."""
    root = _repo(tmp_path, judge=READONLY_PERSONA)
    flow = tg({"name": "w7", "nodes": [_judge_node()]})
    found = lint_flow(flow, repo_root=root)
    assert codes(found).count("lint-persona-not-readonly") == 1
    msg = next(i.message for i in found if i.code == "lint-persona-not-readonly")
    assert "spec.readonly" in msg and "tree" in msg

    honest = tg({"name": "w7b", "nodes": [_judge_node({"readonly": True})]})
    assert "lint-persona-not-readonly" not in codes(lint_flow(honest, repo_root=root))


def test_w7_a_declared_write_scope_is_a_deliberate_decision(tmp_path):
    """`writes: []` (quarantine-enforced write-nothing, e.g. a reviewer that
    must run `git diff` so cannot drop bash) and a real scope (a reviewer
    persona reused on a node that writes its report file) are both explicit
    statements about the tree — the trap is only the node that says NOTHING."""
    root = _repo(tmp_path, judge=READONLY_PERSONA)
    empty = tg({"name": "w7c", "nodes": [_judge_node({"writes": []})]})
    assert "lint-persona-not-readonly" not in codes(lint_flow(empty, repo_root=root))
    scoped = tg({"name": "w7d", "nodes": [_judge_node({"writes": ["REPORT.md"]})]})
    assert "lint-persona-not-readonly" not in codes(lint_flow(scoped, repo_root=root))
    # explicit `readonly: false` is a stated override, not an omission
    overridden = tg({"name": "w7f", "nodes": [_judge_node({"readonly": False})]})
    assert "lint-persona-not-readonly" not in codes(lint_flow(overridden, repo_root=root))


def test_w7_an_unreadable_persona_file_never_crashes_the_lint(tmp_path):
    root = _repo(tmp_path)
    (root / "personas" / "judge.md").write_bytes(b"---\nreadonly: true\n---\n\xff\xfe garbage")
    flow = tg({"name": "w7g", "nodes": [_judge_node()]})
    assert "lint-persona-not-readonly" not in codes(lint_flow(flow, repo_root=root))


def test_w7_needs_the_frontmatter_key_and_a_repo_root(tmp_path):
    root = _repo(tmp_path, judge=WRITER_PERSONA.replace("builder", "judge"))
    flow = tg({"name": "w7e", "nodes": [_judge_node()]})
    # persona without the key: not declared readonly, no opinion
    assert "lint-persona-not-readonly" not in codes(lint_flow(flow, repo_root=root))
    # no repo_root: persona lints skipped, like the config lints without config
    assert "lint-persona-not-readonly" not in codes(lint_flow(flow))
    # persona file missing entirely: verify's persona-missing ERROR owns that
    ghost = _repo(tmp_path / "other")
    assert "lint-persona-not-readonly" not in codes(lint_flow(flow, repo_root=ghost))


def test_w7_this_repos_own_readonly_personas_carry_the_key():
    """reviewer and arbiter never mutate by contract; their files must say so
    in frontmatter, or the lint cannot protect the flows that name them."""
    from pathlib import Path

    from lockstep.taskgraph import persona_declares_readonly

    personas = Path(__file__).resolve().parents[1] / "personas"
    assert persona_declares_readonly(personas, "reviewer")
    assert persona_declares_readonly(personas, "arbiter")
    # planner writes the plan file; implementer writes code; verifier runs
    # checks (bash is a write vector) — none of them may claim readonly.
    for name in ("planner", "implementer", "verifier"):
        assert not persona_declares_readonly(personas, name)
