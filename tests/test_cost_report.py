"""Offline tests for contrib/cost_report.py (PROPOSAL-domain-cockpit B-v0).

Synthetic run dirs only — no tokens, no driver involvement. Fixtures mirror
the real artifact layout: state.json, events.jsonl (incl. a trailing partial
line), phases/<node>/stdout*.log with rotated attempts, argv.json for binary
detection, map items under phases/<node>/items/<i>/.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cost_report", Path(__file__).resolve().parents[1] / "contrib" / "cost_report.py"
)
cost_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cost_report)


FIELDS_TOML = """\
[claude]
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"
cost = "total_cost_usd"
[pi]
format = "pi-stream"
"""


def envelope(in_tok: int, out_tok: int, cost: float) -> str:
    return json.dumps({
        "type": "result",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        "total_cost_usd": cost,
        "result": "OK",
    })


def make_run(root: Path, name: str = "flow-a") -> Path:
    run = root / f"{name}-run"
    (run / "phases").mkdir(parents=True)
    state = {
        "schema_version": "1.0",
        "flow_name": name,
        "flow_hash": "abc",
        "format_version": "1.0",
        "args": {},
        "token_spawns": 5,
        "started_at": "2026-08-01T10:00:00+00:00",
        "nodes": {
            "impl": {"node_id": "impl", "role": "work", "kind": "harness",
                     "status": "done", "attempts": 2},
            "gate": {"node_id": "gate", "role": "gate", "kind": "shell",
                     "status": "done", "attempts": 1},
            "audit": {"node_id": "audit", "role": "map", "kind": "harness",
                      "status": "done", "attempts": 0,
                      "items": {"0": {"status": "done", "attempts": 1},
                                "1": {"status": "done", "attempts": 1}}},
            "plain": {"node_id": "plain", "role": "work", "kind": "harness",
                      "status": "done", "attempts": 1},
        },
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")

    events = [
        {"ts": "2026-08-01T10:00:00+00:00", "kind": "transition", "node": "impl", "status": "running"},
        {"ts": "2026-08-01T10:01:30+00:00", "kind": "transition", "node": "impl", "status": "done"},
        {"ts": "2026-08-01T10:01:31+00:00", "kind": "transition", "node": "gate", "status": "running"},
        {"ts": "2026-08-01T10:01:31+00:00", "node": "gate", "status": "heal-round", "round": 1},
        {"ts": "2026-08-01T10:02:00+00:00", "kind": "transition", "node": "impl", "status": "running"},
        {"ts": "2026-08-01T10:02:40+00:00", "kind": "transition", "node": "impl", "status": "done"},
        {"ts": "2026-08-01T10:03:01+00:00", "kind": "transition", "node": "gate", "status": "blocked"},
    ]
    lines = "\n".join(json.dumps(e) for e in events)
    # trailing partial line after a crash must be tolerated
    (run / "events.jsonl").write_text(lines + '\n{"ts": "2026-08-01T10:0', encoding="utf-8")

    impl = run / "phases" / "impl"
    impl.mkdir()
    (impl / "argv.json").write_text(json.dumps(["claude", "-p", "..."]), encoding="utf-8")
    (impl / "stdout.log").write_text("chatty preamble\n" + envelope(100, 20, 0.05), encoding="utf-8")
    (impl / "stdout-attempt1.log").write_text(envelope(80, 10, 0.03), encoding="utf-8")

    gate = run / "phases" / "gate"
    gate.mkdir()
    (gate / "stdout.log").write_text('{"findings": [], "verdict": "pass", "reason": "x"}',
                                     encoding="utf-8")

    audit = run / "phases" / "audit"
    for i in range(2):
        item = audit / "items" / str(i)
        item.mkdir(parents=True)
        (item / "argv.json").write_text(json.dumps(["C:\\\\tools\\\\claude.EXE", "-p"]),
                                        encoding="utf-8")
        (item / "stdout.log").write_text(envelope(50, 5, 0.01), encoding="utf-8")

    # harness node whose executor printed no JSON at all (copilot-style)
    plain = run / "phases" / "plain"
    plain.mkdir()
    (plain / "argv.json").write_text(json.dumps(["copilot", "-p", "x"]), encoding="utf-8")
    (plain / "stdout.log").write_text("just prose, no envelope\n", encoding="utf-8")
    return run


@pytest.fixture()
def fields_file(tmp_path):
    p = tmp_path / "cost-fields.toml"
    p.write_text(FIELDS_TOML, encoding="utf-8")
    return p


def rows_by_node(run):
    return {r["node"]: r for r in run["rows"]}


def test_collect_run_sums_attempts_items_and_envelopes(tmp_path, fields_file):
    run_dir = make_run(tmp_path)
    maps = cost_report.load_field_maps(str(fields_file))
    run = cost_report.collect_run(run_dir, maps)
    rows = rows_by_node(run)

    # impl: both attempts' envelopes summed; wall = 90s + 40s from event pairs
    assert rows["impl"]["input_tokens"] == 180
    assert rows["impl"]["output_tokens"] == 30
    assert rows["impl"]["cost"] == pytest.approx(0.08)
    assert rows["impl"]["wall_s"] == pytest.approx(130.0)
    assert rows["impl"]["attempts"] == 2

    # shell gate: no token parsing, heal round counted, wall from running->blocked
    assert rows["gate"]["input_tokens"] is None
    assert rows["gate"]["heal_rounds"] == 1
    assert rows["gate"]["wall_s"] == pytest.approx(90.0)

    # map node: item envelopes summed (binary detected from item argv, .EXE stripped),
    # item attempts aggregated
    assert rows["audit"]["input_tokens"] == 100
    assert rows["audit"]["attempts"] == 2

    # envelope-less harness node: honest note, not a fake zero
    assert rows["plain"]["input_tokens"] is None
    assert rows["plain"]["note"] == "no envelope"

    assert run["token_spawns"] == 5


def test_no_field_map_is_reported_not_zeroed(tmp_path):
    run_dir = make_run(tmp_path)
    run = cost_report.collect_run(run_dir, {})  # no maps at all
    rows = rows_by_node(run)
    assert rows["impl"]["input_tokens"] is None
    assert rows["impl"]["note"].startswith("no field map")


def test_render_multi_run_deliverable_rollup(tmp_path, fields_file, capsys):
    r1 = make_run(tmp_path / "a", "flow-a")
    r2 = make_run(tmp_path / "b", "flow-b")
    rc = cost_report.main([str(r1), str(r2), "--fields", str(fields_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## deliverable total (all runs)" in out
    assert "token spawns: 10" in out          # 5 + 5
    assert "NOTIONAL" in out                  # units policy
    assert "| impl | harness | 2 | 0 | 130 | 180 | 30 |" in out


def pi_stream(in_tok: int, out_tok: int, cost: float) -> str:
    """Minimal pi --mode json stream (shape probed against pi 0.83.0):
    message_end carries usage; turn_end/agent_end REPEAT the same message and
    must not be double-counted."""
    usage = {"input": in_tok, "output": out_tok, "cacheRead": 7, "cacheWrite": 3,
             "totalTokens": in_tok + out_tok,
             "cost": {"input": 0, "output": 0, "total": cost}}
    msg = {"role": "assistant", "content": [{"type": "text", "text": "OK"}],
           "provider": "copilot", "model": "gpt-x", "usage": usage}
    lines = [
        {"type": "session", "version": 3, "id": "x"},
        {"type": "message_end", "message": {"role": "user", "content": []}},
        {"type": "message_end", "message": msg},
        {"type": "turn_end", "message": msg, "toolResults": []},
        {"type": "agent_end", "messages": [msg]},
        {"type": "agent_settled"},
    ]
    return "\n".join(json.dumps(x) for x in lines)


def test_pi_stream_usage_sums_message_end_only(tmp_path, fields_file):
    run = tmp_path / "pi-run"
    (run / "phases" / "n").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "flow_name": "pi-flow", "flow_hash": "z", "format_version": "1.0",
        "args": {}, "token_spawns": 2,
        "nodes": {"n": {"node_id": "n", "role": "work", "kind": "harness",
                        "status": "done", "attempts": 2}},
    }), encoding="utf-8")
    node = run / "phases" / "n"
    (node / "argv.json").write_text(json.dumps(["pi.cmd", "-p"]), encoding="utf-8")
    (node / "stdout.log").write_text(pi_stream(8646, 16, 0.021), encoding="utf-8")
    (node / "stdout-attempt1.log").write_text(pi_stream(1000, 4, 0.009), encoding="utf-8")

    maps = cost_report.load_field_maps(str(fields_file))
    row = rows_by_node(cost_report.collect_run(run, maps))["n"]
    assert row["input_tokens"] == 9646          # both attempts, message_end only
    assert row["output_tokens"] == 20
    assert row["cache_read_tokens"] == 14       # 7 per attempt
    assert row["cost"] == pytest.approx(0.030)  # turn_end/agent_end not double-counted
    assert row["note"] == ""


def test_last_envelope_ignores_nested_objects(tmp_path):
    text = 'noise {"partial": \n' + json.dumps(
        {"outer": True, "usage": {"input_tokens": 7}, "total_cost_usd": 0.1}
    )
    env = cost_report.last_envelope(text)
    assert env is not None and env.get("outer") is True  # outermost, not usage{}


def test_not_a_run_dir_errors(tmp_path, capsys):
    assert cost_report.main([str(tmp_path)]) == 2
    assert "no state.json" in capsys.readouterr().err
