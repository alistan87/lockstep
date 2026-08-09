"""Offline tests for contrib/session_spend.py — the session block: the
orchestrator's own transcript spend plus every run started this session.

Synthetic transcripts and run dirs only; a fake HOME so nothing reads the
developer's real ~/.pi or ~/.claude.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

_CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
if str(_CONTRIB) not in sys.path:
    sys.path.insert(0, str(_CONTRIB))

_SPEC = importlib.util.spec_from_file_location("session_spend", _CONTRIB / "session_spend.py")
session_spend = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(session_spend)
cost_report = session_spend.cost_report


def pi_dirname(repo: Path) -> str:
    """The observed pi flattening: separators -> '-', spaces kept, '--' pad."""
    return "--" + re.sub(r"[:\\/]", "-", str(repo)) + "--"


def cc_dirname(repo: Path) -> str:
    """The observed claude code flattening: every non-alphanumeric -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo))


def pi_transcript(started: str, cost: float, model: str = "qwen3.6:35b") -> str:
    def msg(role, usage=None):
        m = {"role": role, "content": []}
        if usage:
            m["usage"] = usage
            m["model"] = model
        return {"type": "message", "timestamp": started, "message": m}
    usage = {"input": 1000, "output": 200, "cacheRead": 50, "cacheWrite": 5,
             "cost": {"total": cost}}
    lines = [msg("user"), msg("assistant", usage), msg("assistant", usage)]
    return "\n".join(json.dumps(x) for x in lines)


def cc_transcript(started: str) -> str:
    usage = {"input_tokens": 10, "output_tokens": 500,
             "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 100}
    entry = {"type": "assistant", "timestamp": started, "requestId": "req-1",
             "message": {"id": "msg-1", "model": "claude-opus-5", "usage": usage}}
    other = {"type": "assistant", "timestamp": started, "requestId": "req-2",
             "message": {"id": "msg-2", "model": "claude-opus-5", "usage": usage}}
    lines = [
        {"type": "summary", "timestamp": started},
        entry,
        entry,   # tool-use segmentation repeats the same response: dedupe
        other,
    ]
    return "\n".join(json.dumps(x) for x in lines)


def make_home(tmp_path: Path, repo: Path) -> Path:
    home = tmp_path / "home"
    (home / ".pi" / "agent" / "sessions" / pi_dirname(repo)).mkdir(parents=True)
    (home / ".claude" / "projects" / cc_dirname(repo)).mkdir(parents=True)
    return home


def make_run(runs_root: Path, name: str, started_at: str) -> Path:
    run = runs_root / name
    (run / "phases" / "n").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "flow_name": name, "flow_hash": "h", "format_version": "1.0",
        "args": {}, "token_spawns": 3, "started_at": started_at,
        "nodes": {"n": {"node_id": "n", "role": "work", "kind": "harness",
                        "status": "done", "attempts": 1}},
    }), encoding="utf-8")
    node = run / "phases" / "n"
    (node / "argv.json").write_text(json.dumps(["claude", "-p"]), encoding="utf-8")
    (node / "stdout.log").write_text(json.dumps({
        "type": "result", "usage": {"input_tokens": 40, "output_tokens": 8},
        "total_cost_usd": 0.5, "result": "OK",
    }), encoding="utf-8")
    return run


MAPS = {"claude": {"input_tokens": "usage.input_tokens",
                   "output_tokens": "usage.output_tokens",
                   "cost": "total_cost_usd"}}


def test_transcript_matching_both_harness_layouts(tmp_path):
    repo = tmp_path / "Work Repo" / "lockstep"
    repo.mkdir(parents=True)
    home = make_home(tmp_path, repo)
    (home / ".pi" / "agent" / "sessions" / pi_dirname(repo) / "a.jsonl").write_text(
        pi_transcript("2026-08-07T10:00:00+00:00", 0.1), encoding="utf-8")
    (home / ".claude" / "projects" / cc_dirname(repo) / "b.jsonl").write_text(
        cc_transcript("2026-08-07T11:00:00+00:00"), encoding="utf-8")
    found = dict(session_spend.find_transcripts(repo, home))
    assert set(found) == {"pi", "claude"}


def test_newest_transcript_wins(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = make_home(tmp_path, repo)
    pi_file = home / ".pi" / "agent" / "sessions" / pi_dirname(repo) / "a.jsonl"
    cc_file = home / ".claude" / "projects" / cc_dirname(repo) / "b.jsonl"
    pi_file.write_text(pi_transcript("2026-08-07T10:00:00+00:00", 0.1), encoding="utf-8")
    cc_file.write_text(cc_transcript("2026-08-07T11:00:00+00:00"), encoding="utf-8")
    os.utime(pi_file, (1_800_000_000, 1_800_000_000))
    os.utime(cc_file, (1_800_000_100, 1_800_000_100))
    assert session_spend.newest_transcript(repo, home)[0] == "claude"
    os.utime(pi_file, (1_800_000_200, 1_800_000_200))
    assert session_spend.newest_transcript(repo, home)[0] == "pi"


def test_pi_transcript_sums_assistant_usage_and_models(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(pi_transcript("2026-08-07T10:00:00+00:00", 0.05), encoding="utf-8")
    got = session_spend.read_pi_transcript(p)
    assert got["sums"]["input_tokens"] == 2000        # two assistant messages
    assert got["sums"]["cost"] == pytest.approx(0.10)
    assert got["models"] == ["qwen3.6:35b"]
    assert got["started"] is not None


def test_claude_transcript_dedupes_and_reports_no_fake_dollars(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(cc_transcript("2026-08-07T10:00:00+00:00"), encoding="utf-8")
    got = session_spend.read_claude_transcript(p)
    # 3 assistant entries but only 2 distinct (requestId, message id) pairs
    assert got["messages"] == 2
    assert got["sums"]["output_tokens"] == 1000
    assert "cost" not in got["sums"]                  # no costUSD -> no fake $0
    assert got["models"] == ["claude-opus-5"]


def test_collect_session_includes_only_runs_started_after(tmp_path):
    repo = tmp_path / "repo"
    runs_root = repo / "runs"
    runs_root.mkdir(parents=True)
    home = make_home(tmp_path, repo)
    (home / ".pi" / "agent" / "sessions" / pi_dirname(repo) / "a.jsonl").write_text(
        pi_transcript("2026-08-07T10:00:00+00:00", 0.2), encoding="utf-8")
    make_run(runs_root, "old-run", "2026-08-07T09:00:00+00:00")
    make_run(runs_root, "new-run", "2026-08-07T10:30:00+00:00")

    got = session_spend.collect_session(repo, runs_root, MAPS, home)
    assert got["source"] == "pi"
    assert [r["name"] for r in got["runs"]] == ["new-run"]
    assert got["runs"][0]["cost"] == pytest.approx(0.5)

    lines = session_spend.session_lines(repo, runs_root, MAPS, home)
    text = "\n".join(lines)
    assert "orchestrator pi (qwen3.6:35b)" in text
    assert "run new-run" in text and "old-run" not in text
    assert "$0.90 notional (orchestrator + runs)" in text  # 0.4 orch + 0.5 run


def test_no_transcript_is_reported_not_invented(tmp_path):
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    lines = session_spend.session_lines(repo, repo / "runs", MAPS, home)
    assert lines == ["this session: no orchestrator transcript found for this repo"]


def test_the_run_list_is_capped_newest_first_and_the_tail_is_summed(monkeypatch, tmp_path):
    """A working session starts a run a minute. Unbounded, this block was
    fifteen lines deep and pushing the decision below the fold on a page whose
    whole job is that the decision is above it. The tail is SUMMED, never
    dropped: a spend total that quietly excludes older runs is the one thing a
    spend figure may not do."""
    runs = [{"name": f"r{i:02d}", "token_spawns": 2, "input_tokens": None,
             "output_tokens": None, "cost": None} for i in range(10)]
    monkeypatch.setattr(session_spend, "collect_session", lambda *a, **k: {
        "source": "claude", "started": None, "runs": runs,
        "orchestrator": {"sums": {"input_tokens": 1, "output_tokens": 1}, "models": []},
    })
    lines = session_spend.session_lines(tmp_path, tmp_path)
    listed = [ln for ln in lines if ln.strip().startswith("run ")]
    assert len(listed) == session_spend.RUN_LINES
    assert "r09" in listed[0] and "r06" in listed[-1], "newest first"
    tail = [ln for ln in lines if "earlier run" in ln]
    assert tail and "6 earlier run(s)" in tail[0] and "12 agent tasks" in tail[0]
