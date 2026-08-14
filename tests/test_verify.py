"""Test 8, verification half (SPEC §13.1.8, §6): each rule rejected with a
distinct named error."""

from __future__ import annotations

from pathlib import Path

import pytest

from lockstep.registry import ExecutorStanza
from lockstep.taskgraph import FlowError, TaskGraph, load_flow, verify_flow

from conftest import PY, make_config

from lockstep.executors.fake import FakeExecutor
from lockstep.executors.harness import HarnessExecutor
from lockstep.executors.shell import ShellExecutor
from lockstep.registry import Registry


def codes(flow: dict, tmp_path: Path, config=None, level=None) -> set[str]:
    tg = TaskGraph.model_validate(flow)
    config = config or make_config()
    reg = Registry()
    reg.register(FakeExecutor(repo_root=tmp_path))
    reg.register(ShellExecutor(repo_root=tmp_path))
    reg.register(HarnessExecutor(config=config, repo_root=tmp_path))
    issues = verify_flow(tg, registry=reg, config=config, repo_root=tmp_path)
    if level:
        issues = [i for i in issues if i.level == level]
    return {i.code for i in issues}


def flow(nodes: list[dict], **kw) -> dict:
    return {"name": "t", "nodes": nodes, **kw}


FAKE = {"kind": "fake", "spec": {}}


def test_duplicate_and_bad_ids(tmp_path):
    got = codes(flow([{"id": "a", **FAKE}, {"id": "a", **FAKE}, {"id": "Bad_Id", **FAKE}]), tmp_path)
    assert {"duplicate-id", "bad-id"} <= got


def test_final_rules(tmp_path):
    assert "multiple-final" in codes(
        flow([{"id": "a", **FAKE, "final": True}, {"id": "b", **FAKE, "final": True}]), tmp_path
    )
    assert "default-final" in codes(flow([{"id": "a", **FAKE}]), tmp_path, level="warning")


def test_unknown_dep_and_cycle(tmp_path):
    assert "unknown-dep" in codes(flow([{"id": "a", **FAKE, "depends_on": ["zzz"]}]), tmp_path)
    got = codes(
        flow([
            {"id": "a", **FAKE, "depends_on": ["b"]},
            {"id": "b", **FAKE, "depends_on": ["a"], "final": True},
        ]),
        tmp_path,
    )
    assert "cycle" in got


def test_unlisted_step_ref(tmp_path):
    got = codes(
        flow([
            {"id": "a", **FAKE},
            {"id": "b", "kind": "fake", "spec": {"task": "use {steps.a.output}"}, "final": True},
        ]),
        tmp_path,
    )
    assert "unlisted-step-ref" in got


def test_arg_rules(tmp_path):
    got = codes(
        flow(
            [{"id": "a", "kind": "fake", "spec": {"task": "{args.missing}"}, "final": True}],
            args={"unused": "x"},
        ),
        tmp_path,
    )
    assert "undeclared-arg" in got
    # §6.4 is an error, not lint (audit-caught downgrade).
    assert "unused-arg" in codes(
        flow([{"id": "a", **FAKE, "final": True}], args={"unused": "x"}), tmp_path, level="error"
    )


def test_role_kind_cross_checks(tmp_path):
    assert "map-field-on-nonmap" in codes(
        flow([{"id": "a", **FAKE, "over": "{steps.x.json}", "final": True}]), tmp_path
    )
    assert "map-missing-over" in codes(
        flow([{"id": "a", "role": "map", **FAKE, "final": True}]), tmp_path
    )
    assert "heal-on-nongate" in codes(
        flow([{"id": "a", **FAKE, "heal": {"max_rounds": 1}, "final": True}]), tmp_path
    )
    assert "approval-with-kind" in codes(
        flow([{"id": "a", "role": "approval", "kind": "shell", "final": True}]), tmp_path
    )
    assert "gate-contract" in codes(
        flow([{"id": "a", "role": "gate", **FAKE, "final": True}]), tmp_path
    )
    assert "item-outside-map" in codes(
        flow([{"id": "a", "kind": "fake", "spec": {"task": "{item}"}, "final": True}]), tmp_path
    )
    assert "previous-needs-one-dep" in codes(
        flow([{"id": "a", "kind": "fake", "spec": {"task": "{previous.output}"}, "final": True}]),
        tmp_path,
    )


def test_unknown_kind_and_bad_spec(tmp_path):
    assert "unknown-kind" in codes(flow([{"id": "a", "kind": "quantum", "final": True}]), tmp_path)
    assert "spec-invalid" in codes(
        flow([{"id": "a", "kind": "shell", "spec": {"nope": 1}, "final": True}]), tmp_path
    )


def test_contract_rules(tmp_path):
    assert "json-without-contract" in codes(
        flow([{"id": "a", **FAKE, "output": "json", "final": True}]), tmp_path
    )
    assert "contract-unresolvable" in codes(
        flow([{"id": "a", **FAKE, "output": "json", "contract": "NoSuch", "final": True}]), tmp_path
    )


def test_over_shape_and_when_grammar(tmp_path):
    assert "over-not-json" in codes(
        flow([
            {"id": "s", **FAKE},
            {"id": "a", "role": "map", "kind": "fake", "depends_on": ["s"],
             "over": "{steps.s.output}", "spec": {}, "final": True},
        ]),
        tmp_path,
    )
    assert "when-grammar" in codes(
        flow([{"id": "a", **FAKE, "when": "{args.x} > 5", "final": True}], args={"x": "1"}), tmp_path
    )


def _healable(targets, extra_nodes=(), max_rounds=1):
    return flow(
        [
            {"id": "impl", "kind": "fake", "spec": {}},
            *extra_nodes,
            {
                "id": "g", "role": "gate", "kind": "fake", "depends_on": ["impl"],
                "output": "json", "contract": "Verdict", "spec": {},
                "heal": {"max_rounds": max_rounds, "targets": targets},
            },
            {"id": "z", "kind": "fake", "depends_on": ["g"], "final": True},
        ]
    )


def test_heal_target_rules(tmp_path):
    assert "heal-targets-required" in codes(_healable([]), tmp_path)
    assert "heal-target-unknown" in codes(_healable(["nope"]), tmp_path)
    assert "heal-target-not-ancestor" in codes(_healable(["z"]), tmp_path)
    shell_target = flow(
        [
            {"id": "impl", "kind": "shell", "spec": {"cmd": ["x"]}},
            {
                "id": "g", "role": "gate", "kind": "fake", "depends_on": ["impl"],
                "output": "json", "contract": "Verdict", "spec": {},
                "heal": {"max_rounds": 1, "targets": ["impl"]},
            },
        ]
    )
    assert "heal-target-kind" in codes(shell_target, tmp_path)


def test_heal_targets_may_not_overlap_across_gates(tmp_path):
    f = flow(
        [
            {"id": "impl", "kind": "fake", "spec": {}},
            {
                "id": "g1", "role": "gate", "kind": "fake", "depends_on": ["impl"],
                "output": "json", "contract": "Verdict", "spec": {},
                "heal": {"max_rounds": 1, "targets": ["impl"]},
            },
            {
                "id": "g2", "role": "gate", "kind": "fake", "depends_on": ["impl", "g1"],
                "output": "json", "contract": "Verdict", "spec": {},
                "heal": {"max_rounds": 1, "targets": ["impl"]},
            },
        ]
    )
    assert "heal-target-overlap" in codes(f, tmp_path)


def test_readonly_unenforced_is_an_error(tmp_path):
    config = make_config(noro=ExecutorStanza(argv=[PY, "-c", "pass"]))  # no readonly_argv
    f = flow([{"id": "a", "kind": "harness", "spec": {"task": "t", "readonly": True}, "final": True}])
    assert "readonly-unenforced" in codes(f, tmp_path, config=config)
    config2 = make_config(ro=ExecutorStanza(argv=[PY, "-c", "pass"], readonly_argv=["--no-write"]))
    assert "readonly-unenforced" not in codes(f, tmp_path, config=config2)


def test_exclusive_collision_is_warning_not_error(tmp_path):
    f = flow([
        {"id": "a", "kind": "fake", "spec": {"readonly": True}, "exclusive": ["db"]},
        {"id": "b", "kind": "fake", "spec": {"readonly": True}, "exclusive": ["db"], "final": True},
    ])
    assert "exclusive-collision" in codes(f, tmp_path, level="warning")
    assert "exclusive-collision" not in codes(f, tmp_path, level="error")


def test_heal_rollback_nongit_is_warning(tmp_path):
    assert "heal-rollback-nongit" in codes(_healable(["impl"]), tmp_path, level="warning")


def test_unknown_role_and_format_version_rejected(tmp_path):
    with pytest.raises(Exception):
        TaskGraph.model_validate(flow([{"id": "a", "role": "wizard"}]))
    bad = tmp_path / "bad.tg.json"
    bad.write_text('{"format_version": "2.0", "name": "t", "nodes": [{"id": "a"}]}', encoding="utf-8")
    with pytest.raises(FlowError, match="format_version"):
        load_flow(bad)


def test_x_lockstep_merge_and_x_namespaces_ignored(tmp_path):
    p = tmp_path / "x.tg.json"
    p.write_text(
        '{"name": "t", "x-other": {"junk": 1}, '
        '"nodes": [{"id": "a", "kind": "fake", "final": true, "x-lockstep": {"timeout_s": 5}, '
        '"x-vendor": {"ignored": true}}]}',
        encoding="utf-8",
    )
    tg, _ = load_flow(p)
    assert tg.nodes[0].timeout_s == 5


def test_fixture_flows_verify_clean(tmp_path):
    from lockstep.registry import load_config

    root = Path(__file__).resolve().parent.parent
    (tmp_path / "personas").mkdir()
    for persona in (root / "personas").iterdir():
        (tmp_path / "personas" / persona.name).write_text(persona.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(root / "lockstep.toml.example")
    reg = Registry()
    reg.register(FakeExecutor(repo_root=tmp_path))
    reg.register(ShellExecutor(repo_root=tmp_path))
    reg.register(HarnessExecutor(config=config, repo_root=tmp_path))
    for name in ("hello-chain", "map-summarize", "gated-build"):
        tg, _ = load_flow(root / "flows" / f"{name}.tg.json")
        errors = [
            i for i in verify_flow(tg, registry=reg, config=config, repo_root=tmp_path)
            if i.level == "error"
        ]
        assert errors == [], f"{name}: {errors}"


# --------------------------------------------- lint-concurrent-heal-rollback

def _two_healers(ui_deps):
    """Two branches, each with its own healing rollback gate. `ui_deps` decides
    whether the second branch is forced to wait for the first."""
    return {
        "format_version": "1.0", "name": "two-healers",
        "nodes": [
            {"id": "a", "role": "work", "kind": "fake", "spec": {"task": "a"},
             "output": "text"},
            {"id": "gate-a", "role": "gate", "kind": "shell", "depends_on": ["a"],
             "spec": {"cmd": ["true"]}, "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["a"], "rollback": True}},
            {"id": "b", "role": "work", "kind": "fake", "spec": {"task": "b"},
             "depends_on": ui_deps, "output": "text"},
            {"id": "gate-b", "role": "gate", "kind": "shell", "depends_on": ["b"],
             "spec": {"cmd": ["true"]}, "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["b"], "rollback": True},
             "final": True},
        ],
    }


def _lint_codes(flow: dict) -> list[str]:
    from lockstep.taskgraph import TaskGraph, lint_flow

    return [i.code for i in lint_flow(TaskGraph.model_validate(flow))]


def test_concurrent_healing_rollback_gates_are_linted():
    """Recorded twice on flows/demo/webapp-local. A rollback's scope is every
    path changed since ITS baseline (SPEC §9.4.4), not just its target's, so
    two gates healing over one tree discard each other's work. The second
    occurrence exited 0 with half the deliverable missing — every gate had
    passed, and a later rollback then removed files an earlier one approved.

    `heal-target-overlap` cannot catch it: the targets ARE disjoint. It is the
    baselines that collide.
    """
    assert "lint-concurrent-heal-rollback" in _lint_codes(_two_healers([]))


def test_serialised_healing_gates_are_not_linted():
    """One dependency edge — carrying no data — forces the first gate to settle
    before the second's target can start, which makes the windows disjoint."""
    assert "lint-concurrent-heal-rollback" not in _lint_codes(_two_healers(["gate-a"]))


def test_a_single_healing_gate_is_never_linted():
    flow = _two_healers([])
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] != "gate-b"]
    flow["nodes"][-1]["final"] = True
    assert "lint-concurrent-heal-rollback" not in _lint_codes(flow)


def test_gates_that_do_not_roll_back_cannot_collide():
    """No restore, no scope, nothing to clobber."""
    flow = _two_healers([])
    for n in flow["nodes"]:
        if n.get("heal"):
            n["heal"]["rollback"] = False
    assert "lint-concurrent-heal-rollback" not in _lint_codes(flow)


def test_the_shipped_flows_are_all_clean_of_it():
    """Whatever else they warn about, no shipped flow may carry this one: it
    produces a run that claims success while destroying its own output."""
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    for path in root.glob("flows/**/*.tg.json"):
        flow = _json.loads(path.read_text(encoding="utf-8"))
        assert "lint-concurrent-heal-rollback" not in _lint_codes(flow), path.name


def test_verify_cli_accepts_shared_config(tmp_path):
    # C1 (LESSONS-TO-MECHANISMS, lesson 7): verify resolved stanzas only from
    # <repo-root>/lockstep.toml, so a flow whose stanzas live in a shared
    # --config file always false-positived no-executor-stanza here while
    # run --config resolved it fine.
    import json as _json

    from lockstep import EXIT_OK, EXIT_VERIFY
    from lockstep.cli import main

    f = {"name": "c1", "nodes": [
        {"id": "n", "kind": "harness", "final": True, "spec": {"task": "x"}}]}
    path = tmp_path / "f.tg.json"
    path.write_text(_json.dumps(f), encoding="utf-8")
    shared = tmp_path / "shared.toml"
    shared.write_text(
        'default = "x"\n[executors.x]\nargv = ["python", "-c", "pass", "{prompt}"]\n',
        encoding="utf-8",
    )
    assert main(["verify", str(path), "--repo-root", str(tmp_path)]) == EXIT_VERIFY
    assert main(
        ["verify", str(path), "--repo-root", str(tmp_path), "--config", str(shared)]
    ) == EXIT_OK


def test_declared_empty_write_scope_is_verified_not_skipped(tmp_path):
    # V1 presence-keying: writes: [] is an enforced declaration now, so the
    # map-level error must fire for it exactly as for a non-empty scope.
    got = codes(flow([
        {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest", "spec": {}},
        {"id": "m", "role": "map", "kind": "fake", "final": True, "concurrency": 1,
         "depends_on": ["src"], "over": "{steps.src.json.files}",
         "spec": {"task": "{item}", "writes": []}},
    ]), tmp_path)
    assert "write-scope-on-map" in got


def test_on_exhausted_rules(tmp_path):
    """Parity 2.1: "pass" is forbidden with rollback (a gate that rolls back
    and then passes accepts a tree the work is no longer in) and dead without
    rounds (same posture as target validation at max_rounds == 0)."""
    def healable(heal):
        return flow([
            {"id": "impl", "kind": "fake", "spec": {}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["impl"],
             "output": "json", "contract": "Verdict", "spec": {}, "heal": heal},
            {"id": "z", "kind": "fake", "depends_on": ["g"], "final": True},
        ])
    with_rollback = healable(
        {"max_rounds": 1, "targets": ["impl"], "rollback": True, "on_exhausted": "pass"})
    assert "on-exhausted-with-rollback" in codes(with_rollback, tmp_path)
    dead = healable({"max_rounds": 0, "rollback": False, "on_exhausted": "pass"})
    assert "on-exhausted-without-rounds" in codes(dead, tmp_path)
    ok = healable(
        {"max_rounds": 1, "targets": ["impl"], "rollback": False, "on_exhausted": "pass"})
    assert not [c for c in codes(ok, tmp_path) if c.startswith("on-exhausted")]


def test_read_scope_rules(tmp_path):
    """Parity 3.1: reads inherit the writes entry grammar - {args.NAME} only,
    repo-root jailed - and land on shell nodes as spec-invalid (ShellSpec has
    no reads field: a shell node always re-runs, so a declared read set there
    is a cache key for a cache that does not exist)."""
    def reader(reads):
        return flow([
            {"id": "a", "kind": "fake", "spec": {"reads": reads}, "final": True},
        ])
    assert "bad-read-scope" in codes(reader(["/abs/path"]), tmp_path)
    assert "bad-read-scope" in codes(reader(["../escape.txt"]), tmp_path)
    assert "bad-read-scope" in codes(reader(["  "]), tmp_path)
    assert "dynamic-read-scope" in codes(reader(["{steps.a.output}"]), tmp_path)
    ok = codes(reader(["src/**", "docs/{args.name}"]), tmp_path)
    assert not [c for c in ok if "read-scope" in c]
    shell = flow([
        {"id": "s", "kind": "shell", "final": True,
         "spec": {"cmd": ["git", "status"], "reads": ["src/**"]}},
    ])
    assert "spec-invalid" in codes(shell, tmp_path)
