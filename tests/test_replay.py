"""Replay: serve a prior run's recorded results instead of spawning.

Two uses, both of which fall out of the fact that every node is already
content-addressed by input_hash with its result persisted:

  1. flow regression tests in CI at zero token cost;
  2. reproducing someone else's failure from a run dir they sent you — the
     case the cockpit proposal lists as an irreducible support gap.

Strict by default: a recording whose input_hash no longer matches is refused,
because a flow edit or a different lockstep.toml means the recording describes
different work. `--replay-any` relaxes that and says so per node.
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep import EXIT_OK
from lockstep.cli import main
from lockstep.replay import ReplayIndex, wrap_registry
from lockstep.state import load_state

from conftest import build

FLOW = {
    "name": "rep",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"outputs": ["two"]}},
    ],
}


def _record(tmp_path, git_repo, flow=FLOW):
    h = build(tmp_path, flow, git_repo)
    h.engine.run()
    return h.run_dir


def _replay(tmp_path, git_repo, source: Path, flow=FLOW, *, strict=True):
    """A fresh engine whose executors are replay wrappers over the fake."""
    h = build(tmp_path / "replayed", flow, git_repo)
    wrap_registry(
        h.engine.registry,
        ReplayIndex.from_run_dir(source),
        strict=strict,
        log=h.engine.log,
    )
    return h


# ----------------------------------------------------------------- replay


def test_replay_serves_results_without_spawning(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    h = _replay(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    assert h.fake.calls == [], "replay must not execute the wrapped executor"


def test_replayed_results_match_the_recording(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    h = _replay(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    for node_id in ("a", "b"):
        original = (source / "phases" / node_id / "result.txt").read_text(encoding="utf-8")
        assert (h.run_dir / "phases" / node_id / "result.txt").read_text(
            encoding="utf-8"
        ) == original


def test_replay_writes_the_result_into_the_new_phase_dir(tmp_path, git_repo):
    """Downstream nodes read the §8.3 file-first channel, so the replayed
    result has to land on disk, not only in memory."""
    source = _record(tmp_path, git_repo)
    h = _replay(tmp_path, git_repo, source)
    h.engine.run()
    assert (h.run_dir / "phases" / "a" / "result.txt").exists()


def test_replay_refuses_a_recording_whose_input_changed(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    edited = json.loads(json.dumps(FLOW))
    edited["nodes"][0]["spec"]["task"] = "a different prompt entirely"
    h = _replay(tmp_path, git_repo, source, flow=edited)
    assert h.engine.run() == 3
    err = load_state(h.run_dir).nodes["a"].error or ""
    assert "input_hash" in err
    assert "--replay-any" in err


def test_replay_any_serves_despite_drift(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    edited = json.loads(json.dumps(FLOW))
    edited["nodes"][0]["spec"]["task"] = "a different prompt entirely"
    h = _replay(tmp_path, git_repo, source, flow=edited, strict=False)
    assert h.engine.run() == 0
    assert any("stale recording" in line for line in h.logs), h.logs


def test_replay_reports_a_node_with_no_recording(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    extended = json.loads(json.dumps(FLOW))
    extended["nodes"][1]["final"] = False
    extended["nodes"].append(
        {"id": "c", "kind": "fake", "final": True, "depends_on": ["b"],
         "spec": {"outputs": ["three"]}}
    )
    h = _replay(tmp_path, git_repo, source, flow=extended)
    assert h.engine.run() == 3
    assert "no recorded result" in (load_state(h.run_dir).nodes["c"].error or "")


def test_replay_reproduces_a_recorded_failure(tmp_path, git_repo):
    """The support case: a run dir that failed must fail the same way here."""
    flow = {
        "name": "rep-fail",
        "nodes": [
            {"id": "n", "kind": "fake", "final": True, "output": "json",
             "contract": "StepResult",
             "spec": {"outputs": ["garbage", "still garbage"]}},
        ],
    }
    source = _record(tmp_path, git_repo, flow=flow)
    assert load_state(source).nodes["n"].status == "failed"
    h = _replay(tmp_path, git_repo, source, flow=flow)
    assert h.engine.run() == 3
    assert load_state(h.run_dir).nodes["n"].status == "failed"
    assert h.fake.calls == []


def test_replay_covers_map_items(tmp_path, git_repo):
    # `output: "json"` requires a contract (the verifier's json-without-contract
    # rule), so this mirrors flows/map-summarize.tg.json: a PathManifest source
    # feeding a map over one of its fields.
    flow = {
        "name": "rep-map",
        "nodes": [
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": [{"files": ["x", "y"], "notes": ""}]}},
            {"id": "m", "role": "map", "kind": "fake", "final": True,
             "depends_on": ["src"], "over": "{steps.src.json.files}", "concurrency": 1,
             "spec": {"task": "handle {item}", "outputs": ["done"]}},
        ],
    }
    source = _record(tmp_path, git_repo, flow=flow)
    assert load_state(source).nodes["m"].status == "done"
    h = _replay(tmp_path, git_repo, source, flow=flow)
    assert h.engine.run() == 0
    assert h.fake.calls == []
    assert len(load_state(h.run_dir).nodes["m"].items) == 2


# -------------------------------------------------------------------- CLI


def test_replay_cli_round_trip(tmp_path, git_repo, capsys):
    flow_file = git_repo / "r.tg.json"
    flow_file.write_text(json.dumps(FLOW), encoding="utf-8")
    recorded = tmp_path / "recorded"
    assert main(["run", str(flow_file), "--runs-dir", str(recorded),
                 "--repo-root", str(git_repo)]) == EXIT_OK
    source = next(d for d in recorded.iterdir() if (d / "state.json").exists())

    replayed = tmp_path / "replayed"
    capsys.readouterr()
    assert main(["run", str(flow_file), "--runs-dir", str(replayed),
                 "--repo-root", str(git_repo), "--replay", str(source)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "replay" in out.lower()
    target = next(d for d in replayed.iterdir() if (d / "state.json").exists())
    assert (target / "phases" / "b" / "result.txt").read_text(encoding="utf-8") == (
        source / "phases" / "b" / "result.txt"
    ).read_text(encoding="utf-8")
