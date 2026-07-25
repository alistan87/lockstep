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
