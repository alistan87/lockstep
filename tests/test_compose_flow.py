"""B2 — the fragment composer: id-prefix, edge-splice, verify. Nothing more."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lockstep.executors.fake import FakeExecutor
from lockstep.policy import AllowAllPolicy
from lockstep.registry import Registry
from lockstep.taskgraph import TaskGraph, verify_flow

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
spec = importlib.util.spec_from_file_location("compose_flow", CONTRIB / "compose_flow.py")
compose_flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose_flow)

HOST = {
    "format_version": "1.0",
    "name": "host",
    "args": {"task": None},
    "nodes": [
        {"id": "draft", "kind": "fake", "spec": {"task": "{args.task}"}},
        {"id": "report", "kind": "fake", "final": True, "depends_on": ["draft"],
         "spec": {"task": "from {steps.draft.output}"}},
    ],
}

FRAGMENT = {
    "format_version": "1.0",
    "name": "checker",
    "args": {"strictness": "high"},
    "nodes": [
        {"id": "check", "kind": "fake", "spec": {"task": "check it at {args.strictness}"}},
        {"id": "digest", "kind": "fake", "final": True, "depends_on": ["check"],
         "spec": {"task": "digest {steps.check.output}"}},
    ],
}


def _verify(flow: dict):
    tg = TaskGraph.model_validate(flow)
    reg = Registry()
    reg.register(FakeExecutor(repo_root=Path(".")))
    issues = verify_flow(tg, registry=reg, config=None, repo_root=Path("."),
                         policy=AllowAllPolicy())
    return tg, [i for i in issues if i.level == "error"]


def test_compose_feed_splices_and_verifies():
    composed = compose_flow.compose(HOST, FRAGMENT, "frag", after="draft", feed="report")
    tg, errors = _verify(composed)
    # The fragment's internal ref was rewritten and its root now hangs off `draft`.
    frag_digest = tg.node("frag-digest")
    assert frag_digest.spec["task"] == "digest {steps.frag-check.output}"
    assert tg.node("frag-check").depends_on == ["draft"]
    assert "frag-digest" in tg.node("report").depends_on
    assert not frag_digest.final, "--feed strips the fragment's final flag"
    assert tg.final_node_id == "report"
    assert not errors, [str(e) for e in errors]
    assert composed["args"] == {"task": None, "strictness": "high"}


def test_compose_without_feed_ends_the_flow():
    composed = compose_flow.compose(HOST, FRAGMENT, "frag", after="report", feed=None)
    tg, errors = _verify(composed)
    assert tg.final_node_id == "frag-digest"
    assert not errors, [str(e) for e in errors]


def test_compose_rewrites_bare_node_ids_in_argv():
    """The gate library and render_evidence address siblings as plain argv
    strings (--node X, --approval X); those must follow the prefix too."""
    fragment = {
        "format_version": "1.0",
        "name": "gated",
        "nodes": [
            {"id": "check", "kind": "fake", "output": "json", "contract": "Finding[]",
             "spec": {"outputs": [[]]}},
            {"id": "gate", "role": "gate", "kind": "shell", "final": True,
             "depends_on": ["check"], "output": "json", "contract": "Verdict",
             "spec": {"cmd": ["python", "-m", "lockstep.gates.block_on_severity",
                              "--at", "major", "--node", "check"]}},
        ],
    }
    composed = compose_flow.compose(HOST, fragment, "frag", after="draft", feed="report")
    gate = next(n for n in composed["nodes"] if n["id"] == "frag-gate")
    assert gate["spec"]["cmd"][-1] == "frag-check"
    assert gate["spec"]["cmd"][2] == "lockstep.gates.block_on_severity", \
        "only EXACT node-id matches are rewritten"


def test_compose_refuses_bad_wiring():
    with pytest.raises(SystemExit, match="--after"):
        compose_flow.compose(HOST, FRAGMENT, "frag", after="nope", feed=None)
    clashing = {**FRAGMENT, "nodes": [{"id": "x", "kind": "fake", "spec": {}}]}
    host_with_clash = {
        **HOST,
        "nodes": HOST["nodes"] + [{"id": "frag-x", "kind": "fake",
                                   "depends_on": ["draft"], "spec": {}}],
    }
    with pytest.raises(SystemExit, match="collide"):
        compose_flow.compose(host_with_clash, clashing, "frag", after="draft", feed=None)
    conflicting = {**FRAGMENT, "args": {"task": "different-default"}}
    with pytest.raises(SystemExit, match="different defaults"):
        compose_flow.compose(HOST, conflicting, "frag", after="draft", feed=None)
