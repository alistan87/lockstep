"""`--detach`: a run that outlives the process that started it.

Consumer report 2026-08-13, item 3. Backgrounding was the caller's problem, and
on Windows under an agent harness the usual POSIX incantations do not solve it:
the reported run died 2.5 minutes into a 40-minute node when its tool call
ended, and `state.json` then said `running` for another 97 minutes.

These tests spawn REAL child processes — that is the whole feature; an
in-process double would test nothing about surviving a parent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from lockstep import EXIT_CONFIG, EXIT_OK
from lockstep.cli import main as cli_main
from lockstep.state import inspect_lock, load_state

PY = sys.executable

FLOW = {
    "name": "detached",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"], "spec": {"outputs": ["B"]}},
    ],
}


def write_flow(tmp_path: Path, flow=None) -> Path:
    p = tmp_path / "f.tg.json"
    p.write_text(json.dumps(flow or FLOW), encoding="utf-8")
    return p


def settle(run_dir: Path, timeout: float = 60.0) -> None:
    """Block until the detached driver releases the lock."""
    deadline = time.monotonic() + timeout
    while (run_dir / "lock").exists() and time.monotonic() < deadline:
        time.sleep(0.1)


def run_dir_from(out: str) -> Path:
    line = next(ln for ln in out.splitlines() if "run dir:" in ln)
    return Path(line.split("run dir:", 1)[1].strip())


def test_module_entry_point_exists():
    """`--detach` re-invokes `python -m lockstep`; without __main__.py the child
    dies instantly with a message no caller would connect to the flag."""
    p = subprocess.run([PY, "-m", "lockstep", "--help"], capture_output=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0
    assert "taskgraph driver" in p.stdout


def test_detached_run_returns_immediately_and_finishes_on_its_own(tmp_path, capsys):
    flow = write_flow(tmp_path)
    runs = tmp_path / "runs"
    assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                     "--repo-root", str(tmp_path), "--detach"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "detached: launched" in out
    run_dir = run_dir_from(out)
    assert (run_dir / "state.json").exists()
    settle(run_dir)
    st = load_state(run_dir)
    assert [r.status for r in st.nodes.values()] == ["done", "done"], st.nodes


def test_the_parent_confirms_the_child_took_the_lock(tmp_path, capsys):
    """"A process was spawned" and "a driver is driving this run" are different
    claims; only the second one is worth printing."""
    flow = write_flow(tmp_path, {
        "name": "slow",
        "nodes": [{"id": "s", "kind": "shell", "final": True,
                   "spec": {"cmd": [PY, "-c", "import time; time.sleep(2)"], "writes": []}}],
    })
    runs = tmp_path / "runs"
    assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                     "--repo-root", str(tmp_path), "--detach"]) == EXIT_OK
    out = capsys.readouterr().out
    run_dir = run_dir_from(out)
    info = inspect_lock(run_dir)
    assert info.state == "alive"
    # The pid worth printing is the DRIVER's, read back from the lock — the
    # pid Popen returns can be a launcher shim (this machine's uv-built venv
    # `python.exe` is one: it re-execs, and its pid never appears in the lock).
    # A pid the operator cannot find in the process table is worse than none.
    assert f"driver pid: {info.pid}" in out
    settle(run_dir)


def test_a_launch_that_dies_reports_here_not_only_to_the_log(tmp_path, capsys):
    """A held lock is the common case: the child exits 8 within milliseconds,
    and a `--detach` that printed success would strand the caller."""
    flow = write_flow(tmp_path)
    runs = tmp_path / "runs"
    assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                     "--repo-root", str(tmp_path)]) == EXIT_OK
    run_dir = next(d for d in runs.iterdir() if d.is_dir())
    capsys.readouterr()
    # Hold the lock with a process that is alive, so it is not cleared as stale.
    holder = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        (run_dir / "lock").write_text(
            json.dumps({"pid": holder.pid, "hostname": __import__("socket").gethostname(),
                        "started": "2026-08-13T00:00:00Z"}), encoding="utf-8")
        # --repo-root matches the recorded root: this test is about the lock,
        # and the Batch 1 wrong-root refusal would otherwise fire first.
        code = cli_main(["resume", str(run_dir), "--detach", "--repo-root", str(tmp_path)])
        err = capsys.readouterr().err
        assert code != EXIT_OK
        assert "exited" in err
    finally:
        holder.kill()
        (run_dir / "lock").unlink(missing_ok=True)


def test_detach_refuses_the_free_synchronous_modes(tmp_path, capsys):
    flow = write_flow(tmp_path)
    runs = tmp_path / "runs"
    for extra in (["--dry-run"], ["--estimate"]):
        assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                         "--repo-root", str(tmp_path), "--detach", *extra]) == EXIT_CONFIG
    assert "nothing to detach" in capsys.readouterr().err


def test_detached_resume_continues_a_stopped_run(tmp_path, capsys):
    flow = write_flow(tmp_path, {
        "name": "budgeted",
        "budget": {"max_agent_spawns": 1, "max_run_minutes": 30},
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}},
            {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
             "spec": {"outputs": ["B"]}},
        ],
    })
    runs = tmp_path / "runs"
    assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                     "--repo-root", str(tmp_path)]) == 4  # spawn budget stops it
    run_dir = next(d for d in runs.iterdir() if d.is_dir())
    capsys.readouterr()
    # Raise the ceiling the same way a foreground resume would see it.
    flow_copy = json.loads((run_dir / "flow.tg.json").read_text(encoding="utf-8"))
    assert flow_copy["budget"]["max_agent_spawns"] == 1
    # --repo-root matters: `resume` resolves lockstep.toml against it, and a
    # different config digest invalidates every cached node ("config: changed").
    assert cli_main(["resume", str(run_dir), "--repo-root", str(tmp_path), "--detach"]) == EXIT_OK
    out = capsys.readouterr().out
    assert str(run_dir) in out
    settle(run_dir)
    st = load_state(run_dir)
    assert st.nodes["a"].status == "done"


def test_a_spawn_that_never_happens_exits_with_a_frozen_code(tmp_path, capsys, monkeypatch):
    """This machine's AV holds new files transiently; an OSError out of the
    spawn must not become a traceback and exit 1 — SPEC §3 freezes the codes,
    and 7 is the one for "could not run the executor"."""
    import lockstep.detach as detach

    def boom(*a, **k):
        raise PermissionError("the file is in use by another process")

    monkeypatch.setattr(detach, "spawn_detached", boom)
    flow = write_flow(tmp_path)
    assert cli_main(["run", str(flow), "--runs-dir", str(tmp_path / "runs"),
                     "--repo-root", str(tmp_path), "--detach"]) == EXIT_CONFIG
    assert "could not launch a driver" in capsys.readouterr().err


def test_an_abbreviated_flag_does_not_fork_bomb(tmp_path, capsys):
    """argparse accepts unambiguous PREFIXES: `--det` sets detach=True. A
    filter that only removed the literal `--detach` would hand the child a
    command line that detaches another child, forever."""
    flow = write_flow(tmp_path)
    runs = tmp_path / "runs"
    assert cli_main(["run", str(flow), "--runs-dir", str(runs),
                     "--repo-root", str(tmp_path), "--det"]) == EXIT_OK
    out = capsys.readouterr().out
    run_dir = run_dir_from(out)
    settle(run_dir)
    log = next(runs.glob("detached-*.log"))
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "detached: launched" not in text, "the child re-detached — that is the bomb"
    assert [d for d in runs.iterdir() if d.is_dir()] == [run_dir], "exactly one run"


def test_a_detached_refusal_is_echoed_here_and_exits_7(tmp_path, git_repo, monkeypatch, capsys):
    """S6 (upstream-response-ow07-feedback): the grace window. The parent used
    to report a clean launch while the child's dirty-scope refusal went only
    to a log nobody was told to read — and `wait` then said exit 4. The
    refusal belongs in the launching terminal, like a launch that never took
    the lock."""
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "a.py").write_text("operator edit, uncommitted\n", encoding="utf-8")
    flow = {
        "name": "refuse-detached",
        "nodes": [{
            "id": "w", "kind": "fake", "final": True,
            "spec": {"outputs": ["ok"], "write_files": {"src/a.py": "x"},
                     "writes": ["src"]},
        }],
    }
    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.chdir(git_repo)
    code = cli_main(["run", str(flow_path), "--runs-dir", str(tmp_path / "runs"), "--detach"])
    captured = capsys.readouterr()
    assert code == 7, captured.err
    assert "refused" in captured.err
    assert "--allow-dirty-scope" in captured.err


# --- the start-check window: a signal, not a guess ----------------------------
#
# Portability check 2026-09-24: under full-suite load in a fresh venv the
# child's dirty-scope preflight (a whole-tree git snapshot) took longer than
# the fixed 3 s grace window, and the parent reported a clean launch of a run
# that was refusing (0, not 7). The window now waits for the driver's own
# `preflight-passed` marker; the timeout is only a safety net.

def _bare_run(tmp_path: Path) -> Path:
    from lockstep.state import PhaseRecord, RunState, utcnow, write_state

    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    write_state(run_dir, RunState(
        flow_name="x", flow_hash="h", format_version="1.0", args={},
        nodes={"a": PhaseRecord(node_id="a", role="work", kind="fake")},
        started_at=utcnow()))
    return run_dir


def test_a_refusal_slower_than_the_old_window_is_still_caught(tmp_path):
    import threading

    from lockstep.cli import _await_start_checks
    from lockstep.state import record_terminal

    run_dir = _bare_run(tmp_path)
    t = threading.Timer(3.5, record_terminal,
                        args=(run_dir, 7, "dirty_scope", "slow refusal"))
    t.start()
    try:
        outcome, terminal = _await_start_checks(run_dir, driver_pid=4242, timeout=15)
    finally:
        t.cancel()
    assert outcome == "refused"
    assert terminal.exit_code == 7 and terminal.message == "slow refusal"


def test_the_drivers_own_marker_ends_the_wait_promptly(tmp_path):
    from lockstep.cli import _await_start_checks
    from lockstep.state import append_event

    run_dir = _bare_run(tmp_path)
    append_event(run_dir, {"kind": "drive", "op": "preflight-passed", "pid": 4242})
    t0 = time.monotonic()
    assert _await_start_checks(run_dir, driver_pid=4242, timeout=15)[0] == "started"
    assert time.monotonic() - t0 < 2


def test_an_earlier_drives_marker_does_not_count(tmp_path):
    """A resumed run's journal already holds a previous drive's marker."""
    from lockstep.cli import _await_start_checks
    from lockstep.state import append_event

    run_dir = _bare_run(tmp_path)
    append_event(run_dir, {"kind": "drive", "op": "preflight-passed", "pid": 1111})
    assert _await_start_checks(run_dir, driver_pid=4242, timeout=1)[0] == "unconfirmed"


def test_the_engine_journals_the_marker_after_its_refusal_checks(tmp_path):
    import os

    from lockstep.state import read_events

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import build

    repo = tmp_path / "plain"
    repo.mkdir()
    h = build(tmp_path, FLOW, repo)
    assert h.engine.run() == EXIT_OK
    marks = [e for e in read_events(h.run_dir)
             if e.get("kind") == "drive" and e.get("op") == "preflight-passed"]
    assert [m["pid"] for m in marks] == [os.getpid()]
    # Before any node moved: it marks the checks, not the work.
    lines = (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    first_status = next(i for i, l in enumerate(lines) if '"status"' in l)
    assert next(i for i, l in enumerate(lines) if "preflight-passed" in l) < first_status
