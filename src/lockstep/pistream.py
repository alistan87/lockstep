"""pi's `--mode json` JSONL event stream — the driver's parsers (D,
DEVIATIONS 2026-09-09).

Three readers of one stream shape (probed against pi 0.83.0/0.85.1):
`pi_stream_result` is the RESULT channel (`envelope = "pi-stream"` on a
stanza redefines the §8.3 stdout-fallback leg and nothing else — the file
channel still wins), `pi_stream_usage` and `pi_stream_tools` are the cost
path, moved here from contrib/cost_report.py so the driver and the cockpit
cannot drift apart; contrib imports these and keeps a standalone fallback
for a cockpit copied without the driver (the missing-part honesty rule).

The stream shape is pi's, not ours: after a pi upgrade, re-run
`lockstep doctor` — its probe exercises this parser whenever the stanza
answers on the stdout leg (readonly stanzas always do; a writing stanza
whose model obeys the footer satisfies the probe on the file channel
first), including the no-assistant-text edge below.
"""

from __future__ import annotations

import json


def _events(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue  # a truncated line (crash mid-write) is not the stream's fault
        if isinstance(obj, dict) and isinstance(obj.get("type"), str):
            yield obj


def pi_stream_result(text: str) -> tuple[str | None, str | None]:
    """(result_text, error) — exactly one is None.

    The result is the concatenation of TEXT-typed content blocks of the LAST
    assistant `message_end`. Thinking blocks are excluded — or a reviewer's
    chain of thought becomes its verdict. A stream that settles with tool
    activity and no assistant text (observed; documented in the cost parser)
    is a NAMED error, loud like `readonly-unenforced` — never a silent empty
    result. Provider error events stay in the raw stdout, where
    `diagnose_provider_error`'s marker scan already reads them."""
    last_assistant: dict | None = None
    saw_events = False
    for obj in _events(text):
        saw_events = True
        if obj["type"] != "message_end":
            continue
        msg = obj.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_assistant = msg
    if not saw_events:
        return None, "not a pi event stream (no events parsed from stdout)"
    if last_assistant is None:
        return None, "stream ended with no assistant text (no assistant message_end)"
    content = last_assistant.get("content")
    if isinstance(content, str):
        # A string-content shorthand (round-2 finding 7): the answer IS the
        # string; iterating it as blocks would destroy a real answer into
        # the no-assistant-text error.
        result = content
    else:
        parts = [
            b.get("text", "")
            for b in (content or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        result = "".join(parts)
    if not result.strip():
        return None, "stream ended with no assistant text (empty text blocks)"
    return result, None


def _dig(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj if isinstance(obj, (int, float)) and not isinstance(obj, bool) else None


def pi_stream_usage(text: str) -> tuple[dict[str, float], int, dict[str, float]]:
    """Sum per-message usage from a pi `--mode json` event stream: each
    assistant `message_end` carries usage{input, output, cacheRead,
    cacheWrite, cost{total}} — provider-computed, so on Copilot these are the
    credit-accurate dollars. Only `message_end` is summed:
    `turn_end`/`agent_end` repeat the same messages and would double-count.

    Second return: the count of usage-bearing assistant messages (G2's
    honest per-node correlate for request-metered work — NOT the billed
    premium-request unit). Third: model id -> weight (dollars, else output
    tokens, else a count) — `message.model` is per-message, so a stream can
    name several; the weight picks the dominant one for display."""
    sums: dict[str, float] = {}
    models: dict[str, float] = {}
    seen = 0
    for obj in _events(text):
        if obj["type"] != "message_end":
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        usage = msg.get("usage") or {}
        seen += 1
        for field, path in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_read_tokens", "cacheRead"),
            ("cache_write_tokens", "cacheWrite"),
            ("cost", "cost.total"),
        ):
            v = _dig(usage, path)
            if v is not None:
                sums[field] = sums.get(field, 0.0) + v
        model = msg.get("model")
        if isinstance(model, str) and model:
            w = _dig(usage, "cost.total") or _dig(usage, "output") or 1
            models[model] = models.get(model, 0.0) + float(w)
    return sums, seen, models


def pi_stream_tools(text: str) -> dict[str, int] | None:
    """tool name -> executions. `None` when this is not an event stream at
    all; `{}` when it is one and no tool ran — the drawer must never print
    "0 tool calls" for a harness that said nothing about tools.

    Counted from `tool_execution_start`, which fires exactly once per call
    (probed 2026-08-15). The `toolCall` CONTENT BLOCKS are NOT countable:
    one `read` call appeared as six blocks across message_update /
    message_end / turn_end — the same repeat that makes `pi_stream_usage`
    sum `message_end` only."""
    out: dict[str, int] = {}
    events = 0
    for obj in _events(text):
        events += 1
        if obj["type"] != "tool_execution_start":
            continue
        name = obj.get("toolName")
        if isinstance(name, str) and name:
            out[name] = out.get(name, 0) + 1
    return out if events else None
