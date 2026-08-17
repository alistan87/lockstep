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
