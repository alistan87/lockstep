"""The per-step agent block: what model answered, at what reasoning level,
with which tools, at what cost.

Two layers, one feature. `cost_report` reads it out of the artifacts a run
already leaves behind (argv.json, the harness's stdout); `mission_view` renders
it into the step drawer that both the TUI and the MISSION page draw.

The event shapes below are VERBATIM from a live pi 0.83.0 `--mode json` probe
(2026-08-15) and a claude `--output-format json` envelope taken from
runs/audit-spec-*. They are fixtures of someone else's format, so a harness
upgrade that changes them should fail here rather than silently zero a column.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
sys.path.insert(0, str(CONTRIB))

import mission_view as mv  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("cost_report", CONTRIB / "cost_report.py")
cost_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cost_report)


FIELDS_TOML = """\
[claude]
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"
cache_read_tokens = "usage.cache_read_input_tokens"
cost = "total_cost_usd"
[pi]
format = "pi-stream"
"""


@pytest.fixture()
def fields_cwd(tmp_path, monkeypatch):
    """Pin the field map. `load_field_maps(None)` prefers ./cost-fields.toml,
    and this developer machine carries a real contrib/cost-fields.toml."""
    (tmp_path / "cost-fields.toml").write_text(FIELDS_TOML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# One `read` call in the live probe produced SIX toolCall content blocks (four
# `message_update` as its arguments streamed in, one `message_end`, one
# `turn_end`) and exactly ONE tool_execution_start. That ratio is why the
# parser counts executions.
PI_TOOL_STREAM = "\n".join(json.dumps(o) for o in [
    {"type": "session", "version": 3},
    {"type": "agent_start"},
    {"type": "message_update", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "id": "call_1", "name": "read", "partialArgs": '{"pa'}]}},
    {"type": "message_end", "message": {
        "role": "assistant", "model": "qwen3.6:35b",
        "content": [{"type": "toolCall", "id": "call_1", "name": "read"}],
        "usage": {"input": 100, "output": 20, "cost": {"total": 0.0}}}},
    {"type": "tool_execution_start", "toolCallId": "call_1", "toolName": "read",
     "args": {"path": "x"}},
    {"type": "tool_execution_end", "toolCallId": "call_1", "toolName": "read"},
    {"type": "tool_execution_start", "toolCallId": "call_2", "toolName": "read"},
    {"type": "tool_execution_end", "toolCallId": "call_2", "toolName": "read"},
    {"type": "tool_execution_start", "toolCallId": "call_3", "toolName": "edit"},
    {"type": "turn_end", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "id": "call_1", "name": "read"}]}, "toolResults": []},
    {"type": "agent_settled"},
])

CLAUDE_ENVELOPE = json.dumps({
    "type": "result",
    "usage": {"input_tokens": 26, "output_tokens": 5109, "cache_read_input_tokens": 164803},
    "total_cost_usd": 0.44,
    "num_turns": 10,
    "permission_denials": [],
    "modelUsage": {"claude-opus-5": {"outputTokens": 5109, "costUSD": 0.44}},
    "result": "OK",
})


# --------------------------------------------- what the spawn asked for (argv)

def test_split_thinking_only_splits_a_real_level():
    """An ollama tag is colon-suffixed too. Splitting a model id on ':'
    unconditionally reports `qwen3.6:35b` as model `qwen3.6` at reasoning level
    `35b` — a level that does not exist, on the half of this machine's stanzas
    that are local."""
    assert cost_report.split_thinking("claude-opus-5:high") == ("claude-opus-5", "high")
    assert cost_report.split_thinking("qwen3.6:35b") == ("qwen3.6:35b", None)
    assert cost_report.split_thinking("sonnet") == ("sonnet", None)
    assert cost_report.split_thinking("anthropic/claude-opus-5:max") == (
        "anthropic/claude-opus-5", "max")


def test_argv_facts_reads_model_reasoning_and_tool_policy(tmp_path):
    d = tmp_path / "phase"
    d.mkdir()
    (d / "argv.json").write_text(json.dumps([
        "pi.cmd", "-p", "--mode", "json", "--no-session", "--provider", "ollama",
        "--model", "anthropic/claude-opus-5:high", "--tools", "read,grep",
    ]), encoding="utf-8")
    facts = cost_report.argv_facts(d)
    assert facts["binary"] == "pi"
    assert facts["model"] == "anthropic/claude-opus-5"
    assert facts["reasoning"] == "high"
    assert facts["provider"] == "ollama"
    assert facts["tool_policy"] == "only read,grep"


@pytest.mark.parametrize("argv", [
    ["pi.cmd", "--model", "sonnet:low", "--thinking", "XHIGH"],
    ["pi.cmd", "--thinking", "XHIGH", "--model", "sonnet:low"],
])
def test_the_explicit_thinking_flag_wins_whatever_the_argv_order(tmp_path, argv):
    """Read in one pass, the `:level` shorthand overwrote the flag whenever it
    came second — so the same stanza reported two different levels depending on
    how its author had ordered the line."""
    d = tmp_path / "phase"
    d.mkdir()
    (d / "argv.json").write_text(json.dumps(argv), encoding="utf-8")
    facts = cost_report.argv_facts(d)
    assert facts["reasoning"] == "xhigh"
    assert facts["model"] == "sonnet"


def test_argv_facts_says_nothing_about_a_model_no_stanza_pinned(tmp_path):
    """The point of the whole block: an unpinned stanza leaves no model in
    argv, so there is none to report — the same absence that keeps the harness's
    own choice out of `input_hash`."""
    d = tmp_path / "phase"
    d.mkdir()
    (d / "argv.json").write_text(json.dumps(["pi.cmd", "-p", "--mode", "json"]),
                                 encoding="utf-8")
    facts = cost_report.argv_facts(d)
    assert facts["binary"] == "pi"
    assert "model" not in facts and "reasoning" not in facts


def test_argv_facts_dedupes_a_repeated_readonly_switch(tmp_path):
    """`readonly_argv` deliberately repeats `--no-tools` already present in the
    base argv (ops notes), so the most careful stanzas were exactly the ones
    rendering "none, none"."""
    d = tmp_path / "phase"
    d.mkdir()
    (d / "argv.json").write_text(json.dumps(["pi.cmd", "--no-tools", "--no-tools"]),
                                 encoding="utf-8")
    assert cost_report.argv_facts(d)["tool_policy"] == "none"


def test_argv_facts_falls_back_to_a_map_items_argv(tmp_path):
    d = tmp_path / "phase"
    (d / "items" / "0").mkdir(parents=True)
    (d / "items" / "0" / "argv.json").write_text(json.dumps(
        ["claude", "--model", "claude-haiku-4-5"]), encoding="utf-8")
    assert cost_report.argv_facts(d)["model"] == "claude-haiku-4-5"


def test_argv_facts_is_empty_for_a_node_that_spawned_nothing(tmp_path):
    d = tmp_path / "phase"
    d.mkdir()
    assert cost_report.argv_facts(d) == {}


# ------------------------------------------------------------------ tool calls

def test_pi_stream_tools_counts_executions_not_repeated_blocks():
    assert cost_report.pi_stream_tools(PI_TOOL_STREAM) == {"read": 2, "edit": 1}


def test_pi_stream_tools_distinguishes_a_quiet_stream_from_no_stream():
    """`{}` = the harness reported tool events and there were none. `None` = it
    reported nothing, so no count may be printed at all. The field map is keyed
    by BINARY, so this parser is selected for EVERY pi stanza — including the
    readonly ones, which must omit `--mode json` (§8.3) and print prose."""
    quiet = json.dumps({"type": "agent_start"}) + "\n" + json.dumps({"type": "agent_settled"})
    assert cost_report.pi_stream_tools(quiet) == {}
    assert cost_report.pi_stream_tools("just prose from a stanza with no json mode") is None
    assert cost_report.pi_stream_tools('{"not": "an event"}') is None


def test_envelope_turns_never_claims_to_count_tool_calls():
    """claude's `--output-format json` envelope carries no tool events at all.
    `num_turns` moves with tool use without measuring it, so it is reported
    under its own name and the tool count stays absent."""
    env = {"num_turns": 10, "permission_denials": [{"tool_name": "Write"}]}
    got = cost_report.envelope_turns(env)
    assert got == {"turns": 10, "denials": 1}
    assert "tools" not in got


# ------------------------------------------------------------ run collection

def make_run(tmp_path: Path) -> Path:
    """A mixed run: one claude node (envelope, turns, no tool events), one pi
    node (stream, tool events), one shell node (no agent at all)."""
    run = tmp_path / "run"
    (run / "phases").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "flow_name": "mixed",
        "started_at": "2026-08-15T10:00:00+00:00",
        "token_spawns": 2,
        "nodes": {
            "review": {"node_id": "review", "role": "work", "kind": "harness",
                       "status": "done", "attempts": 1},
            "build": {"node_id": "build", "role": "work", "kind": "harness",
                      "status": "done", "attempts": 1},
            "check": {"node_id": "check", "role": "gate", "kind": "shell",
                      "status": "done", "attempts": 1},
        },
    }), encoding="utf-8")

    review = run / "phases" / "review"
    review.mkdir()
    (review / "argv.json").write_text(json.dumps([
        "claude", "-p", "--output-format", "json", "--model", "claude-opus-5",
        "--disallowed-tools", "Edit,Write"]), encoding="utf-8")
    (review / "stdout.log").write_text("chatty preamble\n" + CLAUDE_ENVELOPE, encoding="utf-8")

    build = run / "phases" / "build"
    build.mkdir()
    (build / "argv.json").write_text(json.dumps([
        "pi.cmd", "-p", "--mode", "json", "--provider", "ollama",
        "--model", "qwen3.6:35b", "--tools", "read,edit"]), encoding="utf-8")
    (build / "stdout.log").write_text(PI_TOOL_STREAM, encoding="utf-8")
    return run


def rows_of(run):
    got = cost_report.collect_run(run, cost_report.load_field_maps(None))
    return {r["node"]: r for r in got["rows"]}


def test_collect_run_carries_tools_turns_and_argv(tmp_path, fields_cwd):
    rows = rows_of(make_run(tmp_path))

    build = rows["build"]
    assert build["tools"] == {"read": 2, "edit": 1}
    assert build["tool_calls"] == 3
    assert build["argv"]["model"] == "qwen3.6:35b"
    assert build["argv"]["tool_policy"] == "only read,edit"
    assert "reasoning" not in build["argv"]        # a 35B tag is not a level

    # The claude node reports turns and NO tool count — the distinction the
    # drawer renders as "not reported by this harness" rather than as "0".
    assert rows["review"]["tool_calls"] is None
    assert rows["review"]["tools"] is None
    assert rows["review"]["turns"] == 10
    assert rows["review"]["argv"]["tool_policy"] == "all but Edit,Write"

    # A shell node has none of it, and asking does not invent a phase dir.
    assert rows["check"]["tool_calls"] is None
    assert rows["check"]["argv"] == {}


def test_tool_calls_sum_over_every_attempt(tmp_path, fields_cwd):
    """Like the token columns: a corrective re-spawn's tool calls happened, and
    a tally that hid them would understate exactly the nodes that went wrong."""
    run = make_run(tmp_path)
    (run / "phases" / "build" / "stdout-attempt1.log").write_text(
        PI_TOOL_STREAM, encoding="utf-8")
    row = rows_of(run)["build"]
    assert row["tool_calls"] == 6
    assert [a["tools"] for a in row["attempts_detail"]] == [
        {"read": 2, "edit": 1}, {"read": 2, "edit": 1}]


def test_the_table_never_prints_a_tool_count_it_does_not_have(tmp_path, fields_cwd, capsys):
    run = make_run(tmp_path)
    assert cost_report.main([str(run)]) == 0
    out = capsys.readouterr().out
    assert "n/r" in out                       # claude: no tool events, and it says so
    assert "3 (read 2, edit 1)" in out        # pi: the real tally
    assert "qwen3.6:35b" in out               # argv fills in what a local provider omits


# ------------------------------------------------------------- the step drawer

def test_the_drawer_names_the_model_the_tools_and_the_cost(tmp_path, fields_cwd):
    run = make_run(tmp_path)
    body = "\n".join(mv.node_detail(run, "review"))
    assert "  agent" in body
    assert "asked for  : claude | claude-opus-5" in body
    assert "tools      : all but Edit,Write" in body
    assert "tokens     : 26 in / 5,109 out / 164,803 cached" in body
    assert "$0.44 notional" in body


def test_the_drawer_reports_a_reasoning_level_when_the_stanza_pinned_one(tmp_path, fields_cwd):
    run = make_run(tmp_path)
    (run / "phases" / "review" / "argv.json").write_text(json.dumps(
        ["claude", "--model", "claude-opus-5:high"]), encoding="utf-8")
    assert "reasoning high" in "\n".join(mv.node_detail(run, "review"))


def test_the_drawer_says_a_stanza_pinned_no_model(tmp_path, fields_cwd):
    run = make_run(tmp_path)
    (run / "phases" / "build" / "argv.json").write_text(json.dumps(
        ["pi.cmd", "-p", "--mode", "json"]), encoding="utf-8")
    body = "\n".join(mv.node_detail(run, "build"))
    assert "no model pinned in the stanza" in body


def test_the_drawer_never_prints_a_tool_count_it_does_not_have(tmp_path, fields_cwd):
    """A harness that cannot report tool calls and one that made none are
    different facts. The run where that matters most is the one where an agent
    was meant to touch files and did not."""
    run = make_run(tmp_path)
    review = "\n".join(mv.node_detail(run, "review"))
    assert "tool calls : not reported by this harness" in review
    assert "turns      : 10" in review

    build = "\n".join(mv.node_detail(run, "build"))
    assert "tool calls : 3 - read 2, edit 1" in build


def test_the_drawer_grows_nothing_for_a_step_with_no_agent(tmp_path, fields_cwd):
    run = make_run(tmp_path)
    assert mv.node_agent_lines(run, "check") == []
    assert "  agent" not in "\n".join(mv.node_detail(run, "check"))


def test_the_drawer_separators_stay_ascii(tmp_path, fields_cwd):
    """These lines are read in the TUI and in cmd.exe consoles, where a middle
    dot arrives as a question mark and reads like a rendering fault — the same
    rule `compact_block` states for the spend line."""
    run = make_run(tmp_path)
    for node in ("review", "build"):
        for ln in mv.node_agent_lines(run, node):
            assert ln.isascii(), ln


def test_the_drawer_says_so_when_the_cost_reader_is_missing(tmp_path, fields_cwd, monkeypatch):
    """A cockpit copied without cost_report.py must not render an agent-less
    step drawer that reads exactly like a step with no agent."""
    run = make_run(tmp_path)
    monkeypatch.setattr(mv, "_usage", lambda _rd: None)
    monkeypatch.setattr(mv, "_reader_missing", lambda: True)
    body = "\n".join(mv.node_agent_lines(run, "review"))
    assert "not installed here" in body
    assert "cost_report.py" in body


def test_the_drawer_takes_a_precomputed_usage_and_reads_nothing_twice(tmp_path, fields_cwd):
    """The page renders one drawer per step and already holds a `collect_run`;
    without this the agent block walked every phase dir once per step."""
    run = make_run(tmp_path)
    usage = cost_report.collect_run(run, cost_report.load_field_maps(None))

    def explode(_rd):
        raise AssertionError("node_detail re-read usage it was handed")

    import unittest.mock as m
    with m.patch.object(mv, "_usage", explode):
        body = "\n".join(mv.node_detail(run, "build", usage=usage))
    assert "qwen3.6:35b" in body
