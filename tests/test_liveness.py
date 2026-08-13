"""Is anything actually driving this run? (consumer report 2026-08-13, item 4)

A live session reported `lockstep status` saying `running` for 97 minutes while
the process recorded in `<run_dir>/lock` was long dead. The determination was
already implementable — `acquire_lock` clears a same-host dead-pid lock, and
`cockpit.ps1`'s boot path cross-references the pid itself — but no read-only
command ever surfaced it, so it had to be diagnosed by hand against the OS
process table. These tests pin the surfacing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from lockstep.cli import main as cli_main
from lockstep.state import inspect_lock, load_state, write_state

from conftest import build

PY = sys.executable


def dead_pid() -> int:
    """A pid that ran and exited. Reuse is possible in principle and vanishingly
    unlikely inside one test process."""
    p = subprocess.Popen([PY, "-c", "pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait()
    return p.pid


def write_lock(run_dir: Path, **fields) -> None:
    holder = {"pid": os.getpid(), "hostname": socket.gethostname(), "started": "2026-08-13T00:00:00Z"}
    holder.update(fields)
    (run_dir / "lock").write_text(json.dumps(holder), encoding="utf-8")


# ------------------------------------------------------------------ inspect_lock


def test_no_lock_file_is_none(tmp_path):
    assert inspect_lock(tmp_path).state == "none"


def test_own_pid_reads_alive(tmp_path):
    write_lock(tmp_path)
    info = inspect_lock(tmp_path)
    assert info.state == "alive"
    assert info.pid == os.getpid()


def test_exited_pid_on_this_host_reads_dead(tmp_path):
    write_lock(tmp_path, pid=dead_pid())
    assert inspect_lock(tmp_path).state == "dead"


def test_another_host_is_never_called_dead(tmp_path):
    """Same rule as `acquire_lock`: a cross-host pid says nothing about the
    holder, and calling it dead would invite an unlock that stomps a live run."""
    write_lock(tmp_path, pid=dead_pid(), hostname="some-other-box")
    assert inspect_lock(tmp_path).state == "foreign"


def test_a_half_written_lock_is_unknown_not_dead(tmp_path):
    """`acquire_lock` creates the file and writes the pid in two syscalls, so a
    reader can catch it empty. Unknown must never read as dead — `wait` would
    stop waiting on a run that is about to start."""
    (tmp_path / "lock").write_text("", encoding="utf-8")
    assert inspect_lock(tmp_path).state == "unknown"
    (tmp_path / "lock").write_text("{}", encoding="utf-8")
    assert inspect_lock(tmp_path).state == "unknown"


# ------------------------------------------------------------------ status


def running_run(tmp_path, git_repo):
    f = {"name": "live", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"]}}]}
    h = build(tmp_path, f, git_repo)
    st = load_state(h.run_dir)
    st.nodes["a"].status = "running"
    write_state(h.run_dir, st)
    return h


def test_status_names_a_dead_holder_and_the_remedy(tmp_path, git_repo, capsys):
    h = running_run(tmp_path, git_repo)
    pid = dead_pid()
    write_lock(h.run_dir, pid=pid)
    assert cli_main(["status", str(h.run_dir)]) == 0
    out = capsys.readouterr().out
    assert "STALE" in out
    assert str(pid) in out
    assert "resume" in out
    assert "a" in out


def test_status_says_alive_when_the_driver_is_alive(tmp_path, git_repo, capsys):
    h = running_run(tmp_path, git_repo)
    write_lock(h.run_dir)
    assert cli_main(["status", str(h.run_dir)]) == 0
    out = capsys.readouterr().out
    assert "STALE" not in out
    assert "alive" in out and str(os.getpid()) in out


def test_status_flags_running_nodes_with_no_lock_at_all(tmp_path, git_repo, capsys):
    """The hard-kill shape: the driver died without releasing, or the lock was
    cleared by hand. `running` with nobody holding the run is still nobody
    driving it."""
    h = running_run(tmp_path, git_repo)
    assert cli_main(["status", str(h.run_dir)]) == 0
    out = capsys.readouterr().out
    assert "STALE" in out and "resume" in out


def test_status_is_quiet_about_a_settled_run(tmp_path, git_repo, capsys):
    f = {"name": "q", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"]}}]}
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert cli_main(["status", str(h.run_dir)]) == 0
    assert "STALE" not in capsys.readouterr().out


# ------------------------------------------------------------------ wait


def test_wait_stops_on_a_dead_holder_instead_of_hanging(tmp_path, git_repo, capsys):
    """The 97-minute stall: `wait` polled a lock nothing would ever release."""
    h = running_run(tmp_path, git_repo)
    write_lock(h.run_dir, pid=dead_pid())
    # No --timeout: the point is that it returns at all.
    assert cli_main(["wait", str(h.run_dir), "--poll", "0.05"]) == 4
    out = capsys.readouterr().out
    assert "STALE" in out


def test_wait_still_blocks_on_a_live_holder(tmp_path, git_repo):
    h = running_run(tmp_path, git_repo)
    write_lock(h.run_dir)
    assert cli_main(["wait", str(h.run_dir), "--timeout", "0.2", "--poll", "0.05"]) == 1


# ------------------------------------------------------------------ active


def test_active_lists_unfinished_runs_with_liveness(tmp_path, git_repo, capsys):
    h = running_run(tmp_path, git_repo)
    write_lock(h.run_dir, pid=dead_pid())
    assert cli_main(["active", str(h.run_dir.parent)]) == 0
    out = capsys.readouterr().out
    assert h.run_dir.name in out
    assert "STALE" in out


def test_active_skips_settled_runs(tmp_path, git_repo, capsys):
    f = {"name": "settled", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"]}}]}
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert cli_main(["active", str(h.run_dir.parent)]) == 0
    out = capsys.readouterr().out
    assert h.run_dir.name not in out
    assert "nothing is driving a run" in out


def test_active_tolerates_a_missing_runs_dir(tmp_path, capsys):
    assert cli_main(["active", str(tmp_path / "nope")]) == 0
    assert "nothing is driving a run" in capsys.readouterr().out


def test_active_hides_idle_unfinished_runs_but_counts_them(tmp_path, git_repo, capsys):
    """Every run stopped at a gate stays unfinished forever. Listing them by
    default buried the one question this command exists to answer — on this
    repo's own runs/ that was seven months-old rows and nothing running."""
    f = {"name": "stopped", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"]}}]}
    h = build(tmp_path, f, git_repo)
    st = load_state(h.run_dir)
    st.nodes["a"].status = "blocked"
    write_state(h.run_dir, st)
    assert cli_main(["active", str(h.run_dir.parent)]) == 0
    out = capsys.readouterr().out
    assert h.run_dir.name not in out
    assert "1 idle unfinished run(s) not shown" in out
    assert cli_main(["active", str(h.run_dir.parent), "--all"]) == 0
    out = capsys.readouterr().out
    assert h.run_dir.name in out and "IDLE" in out
