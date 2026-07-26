"""AMENDMENTS-r6 test deltas: structured progress (C1), checkpoint steering
(C2), cancel (C3)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from lockstep.state import append_steer, load_state, read_events, read_mailbox

from conftest import PY, build, calls_of, rebuild


# ---------------------------------------------------------------- C1 progress

def test_progress_lines_tailed_into_events(tmp_path, git_repo):
    f = {
        "name": "prog",
        "nodes": [
            {
                "id": "n", "kind": "fake", "final": True,
                "spec": {
                    "outputs": ["done"],
                    "progress": [
                        {"step": "reading", "pct": 10},
                        {"step": "writing", "pct": 90, "note": "almost"},
                    ],
                },
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    progress = [e for e in read_events(h.run_dir) if e.get("kind") == "progress"]
    assert [p["step"] for p in progress] == ["reading", "writing"]
    assert progress[1]["pct"] == 90 and progress[1]["note"] == "almost"
    # status renders the latest entry
    from lockstep.cli import main

    assert main(["status", str(h.run_dir)]) == 0


def test_progress_is_advisory_only(tmp_path, git_repo):
    # A node reporting 100% and then failing is simply failed (§16.1 hard rule).
    f = {
        "name": "prog-fail",
        "nodes": [
            {
                "id": "n", "kind": "fake", "final": True,
                "output": "json", "contract": "StepResult",
                "spec": {"outputs": ["garbage", "more garbage"], "progress": [{"step": "all done", "pct": 100}]},
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    assert load_state(h.run_dir).nodes["n"].status == "failed"


def test_garbage_progress_lines_skipped(tmp_path, git_repo):
    f = {"name": "prog-bad", "nodes": [{"id": "n", "kind": "fake", "spec": {"outputs": ["ok"]}, "final": True}]}
    h = build(tmp_path, f, git_repo)
    # Pre-write a phase dir with garbage progress before the run.
    phase = h.run_dir / "phases" / "n"
    phase.mkdir(parents=True, exist_ok=True)
    (phase / "progress.jsonl").write_text('not json\n{"pct": "NaN"}\n{"step": "real"}\n', encoding="utf-8")
    assert h.engine.run() == 0
    progress = [e for e in read_events(h.run_dir) if e.get("kind") == "progress"]
    assert [p["step"] for p in progress] == ["real"], "unparseable lines skipped silently"


# ---------------------------------------------------------------- C2 steering

def steer_flow():
    return {
        "name": "steered",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"task": "work", "outputs": ["A"]}, "final": True}
        ],
    }


def test_steering_renders_and_folds_into_hash(tmp_path, git_repo):
    f = steer_flow()
    h = build(tmp_path, f, git_repo)
    append_steer(h.run_dir, "a", "prefer approach X")
    assert h.engine.run() == 0
    prompt = calls_of(h, "a")[0].prompt
    assert "--- steering ---" in prompt and "prefer approach X" in prompt
    assert all(m["consumed"] for m in read_mailbox(h.run_dir, "a")), "marked consumed at spawn"
    # Unchanged mailbox => stable hash => resume skips.
    h2 = rebuild(tmp_path, f, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert h2.fake.calls == [], "consumed steering renders identically; no spurious re-run"
    # A NEW message grows the block => invalidates => re-run with both messages.
    append_steer(h.run_dir, "a", "also handle Y")
    h3 = rebuild(tmp_path, f, git_repo, h.run_dir)
    h3.engine.prepare_resume()
    assert h3.engine.run() == 0
    prompt3 = calls_of(h3, "a")[0].prompt
    assert "prefer approach X" in prompt3 and "also handle Y" in prompt3


def test_steering_done_node_reruns_on_resume(tmp_path, git_repo):
    f = steer_flow()
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    append_steer(h.run_dir, "a", "redo it differently")
    h2 = rebuild(tmp_path, f, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.store.state.nodes["a"].status == "pending", "steered done node re-marked (r6 C2)"
    assert h2.engine.run() == 0
    assert "redo it differently" in calls_of(h2, "a")[0].prompt


def test_steer_cli(tmp_path, git_repo, monkeypatch):
    from lockstep.cli import main

    flow_path = git_repo / "s.tg.json"
    flow_path.write_text(json.dumps(steer_flow()), encoding="utf-8")
    monkeypatch.chdir(git_repo)
    assert main(["run", str(flow_path), "--runs-dir", str(tmp_path / "runs")]) == 0
    run_dir = next((tmp_path / "runs").iterdir())
    assert main(["steer", str(run_dir), "a", "note for next run"]) == 0
    assert main(["steer", str(run_dir), "nope", "x"]) == 7
    msgs = read_mailbox(run_dir, "a")
    assert msgs and msgs[-1]["message"] == "note for next run" and not msgs[-1]["consumed"]


# ---------------------------------------------------------------- C3 cancel

def test_cancel_kills_running_node_no_retries(tmp_path, git_repo):
    from lockstep.cli import cmd_cancel
    from types import SimpleNamespace

    f = {
        "name": "cancelme",
        "nodes": [
            {
                "id": "slow", "kind": "shell", "final": True, "timeout_s": 60,
                "retry": {"max": 2, "backoff_ms": 10},
                "spec": {"cmd": [PY, "-c", "import time; time.sleep(50)"]},
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    codes: list[int] = []
    t = threading.Thread(target=lambda: codes.append(h.engine.run()))
    t.start()
    pid_file = h.run_dir / "phases" / "slow" / "pid.txt"
    deadline = time.monotonic() + 20
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid_file.exists(), "node never spawned"
    time.sleep(0.3)  # let the process settle
    rc = cmd_cancel(SimpleNamespace(run_dir=str(h.run_dir), node_id="slow"))
    assert rc == 0
    t.join(timeout=30)
    assert codes == [3]
    rec = load_state(h.run_dir).nodes["slow"]
    assert rec.status == "failed" and rec.error == "cancelled"
    assert rec.attempts == 1, "cancel consumes no retries (r6 C3)"
    # And it is resumable: replace the command with a fast one? No — same flow;
    # the stale CANCELLED marker must be cleared on the next spawn.
    assert (h.run_dir / "phases" / "slow" / "CANCELLED").exists()


def test_cancel_without_live_pid_exits_7(tmp_path, git_repo):
    from lockstep.cli import cmd_cancel
    from types import SimpleNamespace

    f = steer_flow()
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    # fake executor spawns no process => no pid.txt
    assert cmd_cancel(SimpleNamespace(run_dir=str(h.run_dir), node_id="a")) == 7
