"""Test 1 (SPEC §13.1): ordering, exclusion serialization, readonly fan-out,
readonly_argv on spawns."""

from __future__ import annotations

from pathlib import Path

from lockstep.executors.harness import HarnessExecutor
from lockstep.protocols import RenderCtx
from lockstep.registry import ExecutorStanza
from lockstep.taskgraph import Node

from conftest import PY, build, make_config


def _overlap(c1, c2) -> bool:
    return c1.started < c2.ended and c2.started < c1.ended


def test_topological_order(tmp_path, git_repo):
    f = {
        "name": "chain",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}},
            {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {"outputs": ["B"]}},
            {"id": "c", "kind": "fake", "depends_on": ["b"], "spec": {"outputs": ["C"]}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert [c.node_id for c in h.fake.calls] == ["a", "b", "c"]
    assert all(h.state.nodes[n].status == "done" for n in ("a", "b", "c"))


def test_exclusive_token_serializes(tmp_path, git_repo):
    f = {
        "name": "excl",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"readonly": True, "sleep_s": 0.2}, "exclusive": ["db"]},
            {"id": "b", "kind": "fake", "spec": {"readonly": True, "sleep_s": 0.2}, "exclusive": ["db"], "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo, max_workers=2)
    assert h.engine.run() == 0
    a, b = h.fake.calls
    assert not _overlap(a, b), "nodes sharing an exclusive token must never run concurrently"


def test_tree_default_serializes_writers(tmp_path, git_repo):
    f = {
        "name": "writers",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"sleep_s": 0.2}},
            {"id": "b", "kind": "fake", "spec": {"sleep_s": 0.2}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo, max_workers=2)
    assert h.engine.run() == 0
    a, b = h.fake.calls
    assert not _overlap(a, b), "non-readonly fake/harness nodes hold 'tree' by default"


def test_readonly_nodes_fan_out_in_parallel(tmp_path, git_repo):
    f = {
        "name": "reviewers",
        "nodes": [
            {"id": "r1", "kind": "fake", "spec": {"readonly": True, "sleep_s": 0.4}},
            {"id": "r2", "kind": "fake", "spec": {"readonly": True, "sleep_s": 0.4}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo, max_workers=2)
    assert h.engine.run() == 0
    a, b = h.fake.calls
    assert _overlap(a, b), "readonly nodes must run concurrently (the flagship case)"


def test_readonly_spawn_carries_readonly_argv(tmp_path):
    config = make_config(
        ro=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"], readonly_argv=["--no-tools", "Edit,Write"])
    )
    ex = HarnessExecutor(config=config, repo_root=tmp_path)
    node = Node(id="rev", kind="harness", spec={"task": "review", "readonly": True}, output="text")
    ctx = RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "personas", phase_dir=tmp_path / "ph",
        max_interp_chars=20000, config_digest="d", executor_default="ro",
    )
    work = ex.plan(node, ctx)
    assert work.meta["argv_template"][-2:] == ["--no-tools", "Edit,Write"]
    assert work.exclusive == []  # readonly removes the "tree" contribution
    node_rw = Node(id="w", kind="harness", spec={"task": "write"}, output="text")
    assert ex.plan(node_rw, ctx).exclusive == ["tree"]


def test_fake_prompt_is_fenced(tmp_path, git_repo):
    f = {
        "name": "fence",
        "args": {"x": "payload"},
        "nodes": [{"id": "a", "kind": "fake", "spec": {"task": "do {args.x}"}, "final": True}],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    prompt = h.fake.calls[0].prompt
    assert "--- begin data: args.x (untrusted) ---" in prompt
    assert "--- end data ---" in prompt


# ------------------------------------------------- E1: contract shape in prompt

def _plan_ctx(tmp_path, executor_default="test-exec"):
    return RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "personas", phase_dir=tmp_path / "ph",
        max_interp_chars=20000, config_digest="d", executor_default=executor_default,
    )


def test_json_contract_shape_is_stated_in_the_prompt(tmp_path):
    # E1 (LESSONS-TO-MECHANISMS): the driver resolved the contract it will
    # validate against — the prompt states it, generated from the same model,
    # so field names cannot drift between what a node is told and what the
    # validator demands.
    ex = HarnessExecutor(config=make_config(), repo_root=tmp_path)
    node = Node(id="rev", kind="harness", spec={"task": "review"},
                output="json", contract="Verdict")
    work = ex.plan(node, _plan_ctx(tmp_path))
    prompt = str(work.render)
    assert "Output contract Verdict" in prompt
    assert '"verdict": "pass" | "block"' in prompt
    assert '"severity": "blocker" | "major" | "minor" | "nit"' in prompt
    assert "prompt.contract" in work.meta["hash_detail"]


def test_text_output_gets_no_contract_block(tmp_path):
    ex = HarnessExecutor(config=make_config(), repo_root=tmp_path)
    node = Node(id="t", kind="harness", spec={"task": "x"}, output="text")
    assert "Output contract" not in str(ex.plan(node, _plan_ctx(tmp_path)).render)


def test_describe_contract_array_and_optional_fields():
    from lockstep.contracts import describe_contract, resolve_contract

    text = describe_contract(resolve_contract("Finding[]"))
    assert "a JSON ARRAY of Finding objects" in text
    assert '"line": integer or null (optional)' in text
    assert '"claim": string' in text
    verdict = describe_contract(resolve_contract("Verdict"))
    assert '"schema_version": string (optional)' in verdict
    assert '"findings": array of object {' in verdict
