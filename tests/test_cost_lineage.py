"""Offline tests for the lineage index (rev 7 R-B2) and the live pane mode
(rev 7 §B v0.5) in contrib/cost_report.py.

Both features exist for the same reason: with terminal-approval segmentation a
deliverable is a CHAIN of runs, and the DE asks "what has this cost" while the
chain is still running. So the fixtures here are deliberately mid-flight —
running nodes, truncated logs, a state.json caught mid-replace.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cost_report", Path(__file__).resolve().parents[1] / "contrib" / "cost_report.py"
)
cost_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cost_report)


def write_run(runs_root: Path, name: str, *, spawns: int = 3, running: str | None = None,
              cap: int | None = 25, deliverable: str | None = None) -> Path:
    run = runs_root / name
    (run / "phases").mkdir(parents=True)
    nodes = {
        "plan": {"node_id": "plan", "role": "work", "kind": "harness",
                 "status": "done", "attempts": 1},
    }
    if running:
        nodes[running] = {
            "node_id": running, "role": "work", "kind": "harness", "status": "running",
            "attempts": 1,
            "started_at": (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat(),
        }
    state = {
        "schema_version": "1.0", "flow_name": "hygiene", "flow_hash": "h1",
        "format_version": "1.0", "args": {}, "token_spawns": spawns,
        "started_at": "2026-08-01T10:00:00+00:00", "nodes": nodes,
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run / "events.jsonl").write_text(
        '{"ts": "2026-08-01T10:00:00+00:00", "node": "plan", "status": "running"}\n'
        '{"ts": "2026-08-01T10:00:30+00:00", "node": "plan", "status": "done"}\n',
        encoding="utf-8")
    if cap is not None:
        (run / "flow.tg.json").write_text(
            json.dumps({"name": "hygiene", "budget": {"max_agent_spawns": cap}}), encoding="utf-8")
    plan = run / "phases" / "plan"
    plan.mkdir()
    (plan / "argv.json").write_text(json.dumps(["claude", "-p", "x"]), encoding="utf-8")
    (plan / "stdout.log").write_text(json.dumps({
        "usage": {"input_tokens": 100, "output_tokens": 10}, "total_cost_usd": 0.02,
    }), encoding="utf-8")
    if running:
        rd = run / "phases" / running
        rd.mkdir()
        (rd / "argv.json").write_text(json.dumps(["claude", "-p", "x"]), encoding="utf-8")
        # A node still executing: its envelope is not written yet, and what IS
        # on disk is a partial line. This must not read as "no envelope".
        (rd / "stdout.log").write_text('{"usage": {"input_tok', encoding="utf-8")
    if deliverable:
        (run / "cockpit-journal.jsonl").write_text(json.dumps({
            "kind": "consent", "ts": f"2026-08-01T1{len(name) % 9}:00:00+00:00",
            "deliverable": deliverable, "segment": "1 of 2", "mode": "attended",
        }) + "\n", encoding="utf-8")
    return run


@pytest.fixture()
def fields_file(tmp_path):
    p = tmp_path / "cost-fields.toml"
    p.write_text('[claude]\ninput_tokens = "usage.input_tokens"\n'
                 'output_tokens = "usage.output_tokens"\ncost = "total_cost_usd"\n',
                 encoding="utf-8")
    return p


# --- lineage index (R-B2) ------------------------------------------------------

def test_append_lineage_is_idempotent(tmp_path):
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1")
    cost_report.append_lineage(runs, "weekly-report", a)
    cost_report.append_lineage(runs, "weekly-report", a)
    idx = cost_report.lineage_path(runs, "weekly-report")
    assert idx.read_text(encoding="utf-8").strip().splitlines() == [str(a)]


def test_runs_from_resolves_slug_and_file(tmp_path):
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1")
    b = write_run(runs, "seg2")
    cost_report.append_lineage(runs, "weekly", a)
    cost_report.append_lineage(runs, "weekly", b)

    by_slug, notes = cost_report.resolve_runs_from("weekly", runs)
    assert by_slug == [str(a), str(b)] and not notes

    by_file, notes = cost_report.resolve_runs_from(str(cost_report.lineage_path(runs, "weekly")), runs)
    assert by_file == [str(a), str(b)] and not notes


def test_runs_from_dedupes_a_doubly_listed_run(tmp_path):
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1")
    idx = cost_report.lineage_path(runs, "weekly")
    idx.parent.mkdir(parents=True)
    idx.write_text(f"{a}\n{a}\n", encoding="utf-8")
    resolved, _ = cost_report.resolve_runs_from("weekly", runs)
    assert resolved == [str(a)]


def test_the_same_run_spelled_two_ways_is_one_run(tmp_path, monkeypatch):
    """The index is written relative; a journal rebuild globs an absolute root.
    Comparing raw strings counts the run twice and doubles the deliverable's
    reported spend — the number the consent beat is judged against."""
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1", deliverable="weekly")
    monkeypatch.chdir(tmp_path)
    idx = cost_report.lineage_path(runs, "weekly")
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text("runs/seg1\n", encoding="utf-8")          # relative spelling

    resolved, notes = cost_report.resolve_runs_from("weekly", runs)   # absolute rebuild
    assert len(resolved) == 1, f"same run counted twice: {resolved}"
    assert not any("missing" in n for n in notes)
    del a


def test_missing_index_rebuilds_from_journals(tmp_path):
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1", deliverable="weekly")
    b = write_run(runs, "seg2", deliverable="weekly")
    write_run(runs, "other", deliverable="something-else")

    resolved, notes = cost_report.resolve_runs_from("weekly", runs)
    assert resolved == [str(a), str(b)]
    assert any("rebuilt" in n for n in notes)


def test_partial_index_is_completed_from_journals(tmp_path):
    """The orchestrator died between launching segment 2 and appending it."""
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1", deliverable="weekly")
    b = write_run(runs, "seg2", deliverable="weekly")
    cost_report.append_lineage(runs, "weekly", a)

    resolved, notes = cost_report.resolve_runs_from("weekly", runs)
    assert resolved == [str(a), str(b)]
    assert any("missing 1 run" in n for n in notes)


def test_unknown_slug_says_so_rather_than_reporting_zero(tmp_path):
    resolved, notes = cost_report.resolve_runs_from("nope", tmp_path / "runs")
    assert resolved == []
    assert any("no journal names deliverable" in n for n in notes)


def test_listed_but_vanished_run_is_reported_never_dropped(tmp_path, capsys, fields_file):
    runs = tmp_path / "runs"
    a = write_run(runs, "seg1")
    idx = cost_report.lineage_path(runs, "weekly")
    idx.parent.mkdir(parents=True)
    idx.write_text(f"{a}\n{runs / 'seg-gone'}\n", encoding="utf-8")

    rc = cost_report.main(["--runs-from", "weekly", "--runs-root", str(runs),
                           "--fields", str(fields_file)])
    err = capsys.readouterr().err
    assert rc == 0                      # the readable segment still reports
    assert "seg-gone" in err and "never as zero" in err


# --- live pane mode (§B v0.5) --------------------------------------------------

def test_running_node_renders_in_progress_not_no_envelope(tmp_path, fields_file):
    run = write_run(tmp_path / "runs", "live", running="classify")
    maps = cost_report.load_field_maps(str(fields_file))
    rows = {r["node"]: r for r in cost_report.collect_run(run, maps)["rows"]}
    assert rows["classify"]["note"] == "in progress"
    assert rows["classify"]["input_tokens"] is None      # not a fake 0
    assert rows["classify"]["wall_s"] > 0                # elapsed-so-far exists


def test_compact_block_is_short_and_names_the_running_node(tmp_path, fields_file):
    run = write_run(tmp_path / "runs", "live", spawns=9, cap=25, running="classify")
    maps = cost_report.load_field_maps(str(fields_file))
    text, totals = cost_report.compact_block([cost_report.collect_run(run, maps)], [25])
    assert len(text.splitlines()) <= 4
    assert "agent tasks used 9 of 25" in text
    assert "classify" in text
    assert totals["spawns"] == 9


def test_multi_run_cap_covers_the_same_runs_as_the_spend(tmp_path, fields_file):
    """The numerator is the deliverable total, so the denominator must be too.
    Taking one segment's cap produced 'agent tasks used 38 of 25' — a figure
    that reads as already over budget, on the one number a domain expert was
    quoted at the consent beat."""
    runs = [{"run_dir": "a", "flow": "f", "token_spawns": 20, "rows": []},
            {"run_dir": "b", "flow": "f", "token_spawns": 18, "rows": []}]
    text, _ = cost_report.compact_block(runs, [25, 25])
    assert "agent tasks used 38 of 50" in text


def test_a_segment_without_a_budget_makes_the_ceiling_explicitly_partial(tmp_path):
    runs = [{"run_dir": "a", "flow": "f", "token_spawns": 20, "rows": []},
            {"run_dir": "b", "flow": "f", "token_spawns": 18, "rows": []}]
    text, _ = cost_report.compact_block(runs, [25, None])
    assert "of at least 25" in text


def test_single_run_cap_is_unchanged(tmp_path):
    runs = [{"run_dir": "a", "flow": "f", "token_spawns": 9, "rows": []}]
    text, _ = cost_report.compact_block(runs, [25])
    assert "agent tasks used 9 of 25" in text


def test_totals_never_go_backwards(tmp_path, fields_file):
    """A poll that catches the run mid-write must not render a smaller number
    than the poll before it — the DE reads a shrinking spend as a bug."""
    run = write_run(tmp_path / "runs", "live", spawns=9)
    maps = cost_report.load_field_maps(str(fields_file))
    good = cost_report.collect_run(run, maps)
    _, floor = cost_report.compact_block([good], [25])

    starved = json.loads(json.dumps(good))
    starved["token_spawns"] = 0
    for r in starved["rows"]:
        r["input_tokens"] = r["output_tokens"] = r["cost"] = None
    text, totals = cost_report.compact_block([starved], [25], floor)
    assert totals["spawns"] == 9
    assert "agent tasks used 9 of 25" in text
    assert totals["in"] == floor["in"]


def test_state_json_caught_mid_replace_reads_as_none_not_crash(tmp_path):
    run = write_run(tmp_path / "runs", "live")
    (run / "state.json").write_text('{"flow_name": "hyg', encoding="utf-8")   # half-written
    assert cost_report.read_state(run, retries=1) is None
    with pytest.raises(FileNotFoundError):
        cost_report.collect_run(run, {})


def test_absent_state_json_does_not_take_the_view_down(tmp_path):
    run = write_run(tmp_path / "runs", "live")
    (run / "state.json").unlink()
    assert cost_report.read_state(run, retries=1) is None


def test_tmp_siblings_are_never_parsed_as_logs(tmp_path, fields_file):
    run = write_run(tmp_path / "runs", "live")
    (run / "phases" / "plan" / "stdout.log.tmp").write_text(
        json.dumps({"usage": {"input_tokens": 999999, "output_tokens": 1}}), encoding="utf-8")
    maps = cost_report.load_field_maps(str(fields_file))
    rows = {r["node"]: r for r in cost_report.collect_run(run, maps)["rows"]}
    assert rows["plan"]["input_tokens"] == 100          # the .tmp did not count


def _add_node(run: Path, node_id: str, binary: str, stdout: str) -> None:
    d = run / "phases" / node_id
    d.mkdir()
    (d / "argv.json").write_text(json.dumps([binary, "-p", "x"]), encoding="utf-8")
    (d / "stdout.log").write_text(stdout, encoding="utf-8")
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"][node_id] = {"node_id": node_id, "role": "work", "kind": "harness",
                               "status": "done", "attempts": 1}
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")


def test_harness_without_json_mode_is_not_confused_with_missing_config(tmp_path, fields_file):
    """copilot-cli has no JSON mode and never reports usage — nothing to fix.
    An unmapped binary means the OPERATOR must edit cost-fields.toml. The DE
    reads those two lines differently, so they must not render alike."""
    run = write_run(tmp_path / "runs", "live")
    _add_node(run, "copilot-node", "copilot", "prose only, no JSON at all\n")
    _add_node(run, "unmapped-node", "someagent",
              json.dumps({"usage": {"input_tokens": 5}}))

    maps = cost_report.load_field_maps(str(fields_file))
    text, _ = cost_report.compact_block([cost_report.collect_run(run, maps)], [25])
    assert "harness has no JSON mode" in text          # nothing is wrong
    assert "unmapped harness: someagent" in text       # something IS wrong
    assert "cost-fields.toml" in text                  # ...and what to do about it
