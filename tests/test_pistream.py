"""Throughput-parity D (PROPOSAL-throughput-and-harness-parity §5): the
pi-stream result channel — `envelope = "pi-stream"` redefines only the §8.3
stdout-fallback leg, structurally, with the no-assistant-text edge loud.

Semantics pinned here (F-E6): the file channel still wins; the result is the
concatenation of TEXT-typed content blocks of the last assistant
`message_end` (thinking excluded, or a reviewer's chain of thought becomes
its verdict); a stream that settles with no assistant text is a NAMED error,
never a silent empty result."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lockstep.executors.harness import HarnessExecutor, stanza_digest
from lockstep.pistream import pi_stream_result
from lockstep.protocols import RenderCtx
from lockstep.registry import ExecutorStanza, LockstepConfig

from conftest import PY


def _line(obj) -> str:
    return json.dumps(obj)


def _assistant(text_blocks, thinking: str | None = None) -> dict:
    content = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking})
    content += [{"type": "text", "text": t} for t in text_blocks]
    return {"type": "message_end",
            "message": {"role": "assistant", "content": content, "model": "gpt-x"}}


# ---------------------------------------------------------------- unit


class TestPiStreamResult:
    def test_text_blocks_of_last_assistant_message(self):
        stream = "\n".join([
            _line({"type": "session", "id": "s"}),
            _line(_assistant(["first answer"])),
            _line(_assistant(["part one, ", "part two"], thinking="secret reasoning")),
            _line({"type": "agent_settled"}),
        ])
        text, err = pi_stream_result(stream)
        assert err is None
        assert text == "part one, part two"
        assert "secret" not in text  # thinking excluded

    def test_no_assistant_text_is_a_named_error(self):
        # Observed: a stream that settles with tool activity and no assistant
        # message_end. Loud, like readonly-unenforced — never a silent empty.
        stream = "\n".join([
            _line({"type": "session", "id": "s"}),
            _line({"type": "tool_execution_start", "name": "read"}),
            _line({"type": "agent_settled"}),
        ])
        text, err = pi_stream_result(stream)
        assert text is None
        assert err is not None and "no assistant text" in err

    def test_not_a_stream_is_a_named_error(self):
        text, err = pi_stream_result("plain prose, no events at all")
        assert text is None
        assert err is not None and "not a pi event stream" in err

    def test_malformed_lines_are_skipped(self):
        stream = "\n".join([
            '{"type": "session"',  # truncated line (crash mid-write)
            _line(_assistant(["ok"])),
        ])
        text, err = pi_stream_result(stream)
        assert (text, err) == ("ok", None)

    def test_user_message_end_is_not_the_answer(self):
        stream = "\n".join([
            _line({"type": "message_end",
                   "message": {"role": "user", "content": [{"type": "text", "text": "the prompt"}]}}),
            _line(_assistant(["the answer"])),
        ])
        assert pi_stream_result(stream)[0] == "the answer"


# ---------------------------------------------------------------- config


class TestEnvelopeKey:
    def test_mutually_exclusive_with_json_field(self):
        with pytest.raises(ValidationError):
            ExecutorStanza(argv=["pi", "{prompt}"], json_field="result", envelope="pi-stream")

    def test_behaviour_bearing_hashed_only_when_set(self):
        # The first behaviour-bearing post-v1 field: setting it re-bills (it
        # changes what the result IS); not setting it changes nothing (A1).
        plain = ExecutorStanza(argv=["pi", "{prompt}"])
        stream = ExecutorStanza(argv=["pi", "{prompt}"], envelope="pi-stream")
        assert stanza_digest("n", plain) != stanza_digest("n", stream)


# ---------------------------------------------------------------- executor


def _ctx(tmp_path, default: str) -> RenderCtx:
    return RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "personas",
        phase_dir=tmp_path / "ph", max_interp_chars=20000,
        config_digest="d", executor_default=default,
    )


def _run_node(tmp_path, stanza: ExecutorStanza, output: str = "json"):
    from lockstep.taskgraph import Node

    cfg = LockstepConfig(default="mine", executors={"mine": stanza})
    cfg.digest = "d"
    ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
    node = Node(id="n", kind="harness", spec={"task": "t"}, output=output)
    work = ex.plan(node, _ctx(tmp_path, "mine"))
    phase = tmp_path / "ph"
    phase.mkdir(exist_ok=True)
    return ex.execute(work, phase, 60)


STREAM_STDOUT = (
    "import json\n"
    "print(json.dumps({'type': 'session'}))\n"
    "msg = {'role': 'assistant', 'content': ["
    "{'type': 'thinking', 'thinking': 'hidden'},"
    "{'type': 'text', 'text': '{\"answer\": 1}'}]}\n"
    "print(json.dumps({'type': 'message_end', 'message': msg}))\n"
)


class TestExecutorStreamChannel:
    def test_stream_result_extracted(self, tmp_path):
        stanza = ExecutorStanza(
            argv=[PY, "-c", STREAM_STDOUT, "{prompt}"], envelope="pi-stream")
        raw = _run_node(tmp_path, stanza)
        assert raw.error is None
        assert raw.source == "stdout"
        assert json.loads(raw.result_text) == {"answer": 1}

    def test_file_channel_still_wins(self, tmp_path):
        # A writer's result.json is never shadowed by its stream chatter.
        script = (
            "import json, sys, pathlib\n"
            "pathlib.Path(sys.argv[1], 'result.json').write_text('{\"from\": \"file\"}')\n"
            + STREAM_STDOUT
        )
        stanza = ExecutorStanza(
            argv=[PY, "-c", script, "{phase_dir}", "{prompt}"], envelope="pi-stream")
        raw = _run_node(tmp_path, stanza)
        assert raw.source == "file"
        assert json.loads(raw.result_text) == {"from": "file"}

    def test_no_assistant_text_is_loud(self, tmp_path):
        script = (
            "import json\n"
            "print(json.dumps({'type': 'session'}))\n"
            "print(json.dumps({'type': 'agent_settled'}))\n"
        )
        stanza = ExecutorStanza(
            argv=[PY, "-c", script, "{prompt}"], envelope="pi-stream")
        raw = _run_node(tmp_path, stanza)
        assert raw.result_text is None
        assert raw.error is not None and "no assistant text" in raw.error

    def test_fenced_json_in_message_text_still_salvaged(self, tmp_path):
        # The assistant narrated and fenced its JSON: the same §8.3 salvage
        # every other stdout result gets applies AFTER stream extraction.
        script = (
            "import json\n"
            "msg = {'role': 'assistant', 'content': [{'type': 'text', "
            "'text': 'Here you go:\\n```json\\n{\"answer\": 2}\\n```\\n'}]}\n"
            "print(json.dumps({'type': 'message_end', 'message': msg}))\n"
        )
        stanza = ExecutorStanza(
            argv=[PY, "-c", script, "{prompt}"], envelope="pi-stream")
        raw = _run_node(tmp_path, stanza)
        assert json.loads(raw.result_text) == {"answer": 2}
