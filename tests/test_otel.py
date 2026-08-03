"""OTLP-shaped span export, without an OpenTelemetry dependency.

SPEC §16.3 reserved an OTel exporter. What changed since is that the GenAI
semantic conventions stabilized for client spans, so there is now a real
attribute vocabulary to target. We emit OTLP/JSON lines a collector can ingest
directly — `pydantic` stays the only runtime dependency, which is the point.

Spans are advisory, exactly like progress (§16.1): nothing here influences
scheduling, hashing, gating, budgets, or retries.
"""

from __future__ import annotations

import json

import pytest

from lockstep import EXIT_OK, __version__
from lockstep.cli import main
from lockstep.state import configure_spans

from conftest import PY, build

FLOW = {
    "name": "otel",
    "nodes": [
        {"id": "agent", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "sh", "kind": "shell", "final": True, "depends_on": ["agent"],
         "spec": {"cmd": [PY, "-c", "print('hi')"]}},
    ],
}


@pytest.fixture(autouse=True)
def _reset_spans():
    yield
    configure_spans(None, "")


def _spans(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        envelope = json.loads(line)
        for rs in envelope["resourceSpans"]:
            for ss in rs["scopeSpans"]:
                out.extend(ss["spans"])
    return out


def _attrs(span):
    return {a["key"]: list(a["value"].values())[0] for a in span["attributes"]}


def test_no_spans_are_written_unless_configured(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    assert not list(tmp_path.glob("**/spans.jsonl"))


def test_one_span_per_terminal_node(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    names = sorted(s["name"] for s in _spans(target))
    assert names == ["agent", "sh"]


def test_span_shape_is_ingestible_otlp(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    span = _spans(target)[0]
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    int(span["traceId"], 16), int(span["spanId"], 16)  # hex, per OTLP/JSON
    assert span["kind"] == 1  # SPAN_KIND_INTERNAL
    assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"]) > 0
    envelope = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
    scope = envelope["resourceSpans"][0]["scopeSpans"][0]["scope"]
    assert scope["name"] == "lockstep" and scope["version"] == __version__


def test_lockstep_attributes_are_present(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    span = next(s for s in _spans(target) if s["name"] == "agent")
    attrs = _attrs(span)
    assert attrs["lockstep.node_id"] == "agent"
    assert attrs["lockstep.role"] == "work"
    assert attrs["lockstep.kind"] == "fake"
    assert attrs["lockstep.run_id"] == "run-1"
    assert attrs["lockstep.input_hash"]


def test_agent_nodes_carry_genai_attributes(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    agent = _attrs(next(s for s in _spans(target) if s["name"] == "agent"))
    assert agent["gen_ai.operation.name"] == "invoke_agent"
    assert agent["gen_ai.agent.name"] == "agent"


def test_shell_nodes_are_not_labelled_as_model_calls(tmp_path, git_repo):
    """A subprocess spends no tokens; calling it a GenAI operation would make
    every downstream cost dashboard wrong."""
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    shell = _attrs(next(s for s in _spans(target) if s["name"] == "sh"))
    assert "gen_ai.operation.name" not in shell


def test_all_nodes_share_one_trace(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    spans = _spans(target)
    assert len({s["traceId"] for s in spans}) == 1
    assert len({s["spanId"] for s in spans}) == len(spans)


def test_trace_id_is_derived_from_the_run_id(tmp_path, git_repo):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    configure_spans(a, "run-1")
    build(tmp_path / "1", FLOW, git_repo).engine.run()
    configure_spans(b, "run-2")
    build(tmp_path / "2", FLOW, git_repo).engine.run()
    assert _spans(a)[0]["traceId"] != _spans(b)[0]["traceId"]
    configure_spans(a, "run-1")  # same run id ⇒ same trace, so a resume joins it
    assert _spans(a)[0]["traceId"] == _spans(a)[-1]["traceId"]


def test_a_failed_node_gets_error_status(tmp_path, git_repo):
    target = tmp_path / "spans.jsonl"
    configure_spans(target, "run-1")
    flow = {
        "name": "otel-fail",
        "nodes": [
            {"id": "n", "kind": "fake", "final": True, "output": "json",
             "contract": "StepResult", "spec": {"outputs": ["garbage", "still garbage"]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 3
    span = _spans(target)[0]
    assert span["status"]["code"] == 2  # STATUS_CODE_ERROR
    assert span["status"]["message"]


def test_cli_flag_writes_spans_into_the_run_dir(tmp_path, git_repo, capsys):
    flow_file = git_repo / "o.tg.json"
    flow_file.write_text(json.dumps(FLOW), encoding="utf-8")
    runs = tmp_path / "runs"
    assert main(["run", str(flow_file), "--runs-dir", str(runs),
                 "--repo-root", str(git_repo), "--otel-file"]) == EXIT_OK
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    assert (run_dir / "spans.jsonl").exists()
    assert len(_spans(run_dir / "spans.jsonl")) == 2
