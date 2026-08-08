"""Tamper-evident events.jsonl: every line is chained to its predecessor's
digest, and `lockstep verify-trace` recomputes the chain.

The property is tamper-EVIDENCE, not tamper-proofing: anyone who can rewrite
events.jsonl can also recompute a consistent chain. What the chain buys is that
a *partial* edit — changing one line, dropping one, appending one — cannot go
unnoticed, and that a head digest recorded off-box pins the whole file.
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep import EXIT_OK, EXIT_VERIFY
from lockstep.cli import main
from lockstep.state import append_event, chain_head, read_events, trace_status, verify_trace

from conftest import build

FLOW = {
    "name": "chain",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"outputs": ["two"]}},
    ],
}


def _lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()


def _rewrite(run_dir: Path, lines: list[str]) -> None:
    (run_dir / "events.jsonl").write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8"
    )


def _run(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    return h.run_dir


# ------------------------------------------------------------- the chain


def test_every_event_carries_a_chain_digest(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    events = read_events(run_dir)
    assert events, "the run emitted no events"
    assert all(len(e["h"]) == 64 for e in events)


def test_a_clean_run_verifies(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    ok, head, bad, detail = verify_trace(run_dir)
    assert ok, detail
    assert head == chain_head(run_dir)
    assert bad is None


def test_editing_an_event_breaks_the_chain(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    lines = _lines(run_dir)
    target = 1
    record = json.loads(lines[target])
    record["status"] = "done-but-actually-not"
    lines[target] = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    _rewrite(run_dir, lines)
    ok, _head, bad, detail = verify_trace(run_dir)
    assert not ok
    assert bad == target + 1  # 1-indexed for humans
    assert "line 2" in detail


def test_deleting_an_event_breaks_the_chain(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    lines = _lines(run_dir)
    del lines[1]
    _rewrite(run_dir, lines)
    ok, _head, bad, _detail = verify_trace(run_dir)
    assert not ok and bad == 2


def test_appending_a_forged_event_breaks_the_chain(tmp_path, git_repo):
    """A forged line cannot carry the right prev-digest without recomputing
    it, which is exactly the work the chain is meant to make visible."""
    run_dir = _run(tmp_path, git_repo)
    lines = _lines(run_dir)
    forged = json.loads(lines[-1])
    forged["node"] = "never-ran"
    lines.append(json.dumps(forged, separators=(",", ":"), ensure_ascii=False))
    _rewrite(run_dir, lines)
    ok, _head, bad, _detail = verify_trace(run_dir)
    assert not ok and bad == len(lines)


def test_truncating_the_tail_changes_the_head(tmp_path, git_repo):
    """Dropping trailing lines leaves a self-consistent chain — that is what
    the recorded head digest is for."""
    run_dir = _run(tmp_path, git_repo)
    full_head = chain_head(run_dir)
    lines = _lines(run_dir)
    _rewrite(run_dir, lines[:-1])
    ok, head, _bad, _detail = verify_trace(run_dir)
    assert ok, "a truncated prefix is still internally consistent"
    assert head != full_head, "but the head must differ"


def test_trailing_partial_line_is_tolerated(tmp_path, git_repo):
    """SPEC §10.3: readers tolerate a torn last line after a crash."""
    run_dir = _run(tmp_path, git_repo)
    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts":"2026-01-01T00:00:00.0Z","kind":"trans')
    ok, _head, _bad, detail = verify_trace(run_dir)
    assert ok, detail


def test_chain_continues_across_a_resume(tmp_path, git_repo):
    """A fresh process must pick the chain up from the file, not restart it."""
    run_dir = _run(tmp_path, git_repo)
    head_before = chain_head(run_dir)
    append_event(run_dir, {"kind": "note", "node": "later"})
    ok, head_after, _bad, detail = verify_trace(run_dir)
    assert ok, detail
    assert head_after != head_before
    assert read_events(run_dir)[-1]["h"] == head_after


# --------------------------------------------------------------- the CLI


def test_verify_trace_cli_passes_on_a_clean_run(tmp_path, git_repo, capsys):
    run_dir = _run(tmp_path, git_repo)
    assert main(["verify-trace", str(run_dir)]) == EXIT_OK
    assert chain_head(run_dir) in capsys.readouterr().out


def test_verify_trace_cli_fails_on_tampering(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    lines = _lines(run_dir)
    record = json.loads(lines[0])
    record["node"] = "forged"
    lines[0] = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    _rewrite(run_dir, lines)
    assert main(["verify-trace", str(run_dir)]) == EXIT_VERIFY


def test_expected_head_is_checked(tmp_path, git_repo):
    run_dir = _run(tmp_path, git_repo)
    assert main(["verify-trace", str(run_dir), "--head", chain_head(run_dir)]) == EXIT_OK
    assert main(["verify-trace", str(run_dir), "--head", "0" * 64]) == EXIT_VERIFY


def test_wholesale_rewrite_is_caught_by_the_recorded_head(tmp_path, git_repo):
    """Strip the chain entirely and re-chain a doctored file: internally
    consistent, but it cannot reproduce the original head."""
    run_dir = _run(tmp_path, git_repo)
    original_head = chain_head(run_dir)
    events = [dict(e) for e in read_events(run_dir)]
    events[0]["node"] = "forged"
    (run_dir / "events.jsonl").unlink()
    for e in events:
        e.pop("h", None)
        append_event(run_dir, e)
    ok, _head, _bad, _detail = verify_trace(run_dir)
    assert ok, "the re-chained file is self-consistent"
    assert main(["verify-trace", str(run_dir), "--head", original_head]) == EXIT_VERIFY


def test_unchained_run_is_reported_not_claimed_verified(tmp_path, git_repo, capsys):
    """A run dir predating chaining must not be described as verified."""
    run_dir = _run(tmp_path, git_repo)
    stripped = []
    for e in read_events(run_dir):
        e.pop("h", None)
        stripped.append(json.dumps(e, separators=(",", ":"), ensure_ascii=False))
    _rewrite(run_dir, stripped)
    assert main(["verify-trace", str(run_dir)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "unchained" in out.lower()
    assert "verified" not in out.lower()


# ---------------------------------------------- the four-way render rule

def test_trace_status_reports_total_and_chained(tmp_path, git_repo):
    """`ok` alone cannot be rendered. A tamper returns ok=False with a
    NON-EMPTY head (the last good digest) and a healthy fresh run returns
    ok=True with an empty one, so "green tick iff head" is wrong in both
    directions. `total`/`chained` are what tell the three ok cases apart."""
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == EXIT_OK
    s = trace_status(h.run_dir)
    assert s["ok"] is True
    assert s["head"] and s["total"] == s["chained"] > 0
    assert s["first_bad_line"] is None


def test_a_healthy_empty_journal_is_nothing_to_verify(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    s = trace_status(run)
    assert (s["ok"], s["head"], s["total"], s["chained"]) == (True, "", 0, 0)


def test_an_unchained_journal_is_reported_as_unchained(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "events.jsonl").write_text(
        json.dumps({"ts": "2026-08-01T10:00:00Z", "node": "a", "status": "done"}) + "\n",
        encoding="utf-8",
    )
    s = trace_status(run)
    assert s["ok"] is True and s["total"] == 1 and s["chained"] == 0
    assert "unchained" in s["detail"]


def test_a_tamper_is_broken_even_though_the_head_is_not_empty(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == EXIT_OK
    path = h.run_dir / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    doc = json.loads(lines[2])
    doc["status"] = "skipped"
    lines[2] = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    s = trace_status(h.run_dir)
    assert s["ok"] is False
    assert s["head"], "a tamper leaves the last GOOD digest — rendering it as verified is the bug"
    assert s["first_bad_line"] == 3


def test_verify_trace_is_the_four_tuple_view_of_trace_status(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == EXIT_OK
    s = trace_status(h.run_dir)
    assert verify_trace(h.run_dir) == (
        s["ok"], s["head"], s["first_bad_line"], s["detail"]
    )
