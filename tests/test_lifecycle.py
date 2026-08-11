"""Test 8, lifecycle half (SPEC §13.1): approval, map semantics, budgets,
locks, events tolerance, kill_tree — plus A3 per-item resume."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lockstep.state import (
    LockHeld,
    acquire_lock,
    load_state,
    read_events,
    release_lock,
    write_state,
)

from conftest import PY, build, calls_of, rebuild


# ---------------------------------------------------------------- approval

def test_approval_auto_rejects_on_non_tty(tmp_path, git_repo):
    f = {
        "name": "appr",
        "nodes": [
            {"id": "ask", "role": "approval"},
            {"id": "after", "kind": "fake", "depends_on": ["ask"], "spec": {}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 6  # pytest stdin is not a TTY
    st = load_state(h.run_dir)
    assert "non-TTY" in st.nodes["ask"].error
    assert st.nodes["after"].status == "blocked"


def test_approval_never_resume_skipped(tmp_path, git_repo):
    f = {"name": "appr2", "nodes": [{"id": "ask", "role": "approval", "final": True}]}
    h = build(tmp_path, f, git_repo)
    st = h.store.state
    st.nodes["ask"].status = "done"
    write_state(h.run_dir, st)
    h2 = rebuild(tmp_path, f, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.store.state.nodes["ask"].status == "pending"


# ---------------------------------------------------------------- map

MAP_FLOW = {
    "name": "mapper",
    "nodes": [
        {
            "id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
            "spec": {"outputs": ['{"files": ["p", "q", "r"], "notes": ""}'], "readonly": True},
        },
        {
            "id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
            "over": "{steps.src.json.files}", "concurrency": 1,
            "spec": {"task": "handle {item}", "outputs": ["done-item"]}, "final": True,
        },
    ],
}


def test_map_order_at_concurrency_1(tmp_path, git_repo):
    h = build(tmp_path, json.loads(json.dumps(MAP_FLOW)), git_repo)
    assert h.engine.run() == 0
    prompts = [c.prompt for c in calls_of(h, "m")]
    assert len(prompts) == 3
    assert "p" in prompts[0] and "q" in prompts[1] and "r" in prompts[2]
    result = json.loads(open(load_state(h.run_dir).nodes["m"].result_path, encoding="utf-8").read())
    assert result == ["done-item"] * 3


def test_map_item_failure_vs_optional(tmp_path, git_repo):
    f = json.loads(json.dumps(MAP_FLOW))
    f["nodes"][1]["output"] = "json"
    f["nodes"][1]["contract"] = "StepResult"
    good = {"step_id": "s", "status": "done", "files_written": []}
    # c=1: item p ok; item q invalid twice (initial + corrective); item r ok.
    f["nodes"][1]["spec"]["outputs"] = [good, "bad", "still bad", good]
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["m"]
    assert rec.status == "failed" and "item 1" in rec.error
    assert rec.items["1"].status == "failed"

    f["nodes"][1]["optional"] = True
    h2 = build(tmp_path, f, git_repo)
    assert h2.engine.run() == 0
    result = json.loads(open(load_state(h2.run_dir).nodes["m"].result_path, encoding="utf-8").read())
    assert result[0]["status"] == "done"
    assert result[1]["status"] == "failed", "failed slot holds a StepResult placeholder"
    assert result[2]["status"] == "done"


def test_map_per_item_resume(tmp_path, git_repo):
    # A3: done items with matching hashes are reused; only changed items re-run.
    f = json.loads(json.dumps(MAP_FLOW))
    h1 = build(tmp_path, f, git_repo)
    assert h1.engine.run() == 0
    assert len(calls_of(h1, "m")) == 3
    # Unchanged resume: zero item executions.
    h2 = rebuild(tmp_path, f, git_repo, h1.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert calls_of(h2, "m") == []
    # Change one item in the source array: only that item runs.
    f3 = json.loads(json.dumps(MAP_FLOW))
    f3["nodes"][0]["spec"]["outputs"] = ['{"files": ["p", "q", "ZZZ"], "notes": ""}']
    f3["nodes"][0]["spec"]["task"] = "v2"  # invalidates src so the new array is produced
    h3 = rebuild(tmp_path, f3, git_repo, h1.run_dir)
    h3.engine.prepare_resume()
    assert h3.engine.run() == 0
    m_calls = calls_of(h3, "m")
    assert len(m_calls) == 1 and "ZZZ" in m_calls[0].prompt


# ---------------------------------------------------------------- budget

def test_budget_trip_exit_4_no_new_spawns_state_persisted(tmp_path, git_repo):
    f = {
        "name": "budget",
        "budget": {"max_agent_spawns": 1, "max_run_minutes": 120},
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}},
            {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {"outputs": ["B"]}},
            {"id": "c", "kind": "fake", "depends_on": ["b"], "spec": {"outputs": ["C"]}, "final": True},
        ],
    }
    h = build(tmp_path, f, git_repo, max_workers=1)
    assert h.engine.run() == 4
    st = load_state(h.run_dir)
    assert st.token_spawns == 1
    assert len(h.fake.calls) == 1, "no new spawns after the trip"
    assert st.nodes["a"].status == "done"
    assert st.nodes["b"].status == "pending", "persisted, resumable"


# ---------------------------------------------------------------- locks

def test_lock_rejects_second_process(tmp_path):
    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    acquire_lock(run_dir)
    with pytest.raises(LockHeld):
        acquire_lock(run_dir)
    release_lock(run_dir)
    acquire_lock(run_dir)  # re-acquirable after release
    release_lock(run_dir)


def test_cross_host_staleness_requires_force_unlock(tmp_path):
    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    (run_dir / "lock").write_text(
        json.dumps({"pid": 999999999, "hostname": "some-other-host", "started": "t"}),
        encoding="utf-8",
    )
    # pid checks are meaningless cross-host: NOT treated as stale.
    with pytest.raises(LockHeld):
        acquire_lock(run_dir)
    acquire_lock(run_dir, force=True)
    release_lock(run_dir)


def test_same_host_stale_lock_is_cleared(tmp_path):
    import socket

    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    (run_dir / "lock").write_text(
        json.dumps({"pid": 999999999, "hostname": socket.gethostname(), "started": "t"}),
        encoding="utf-8",
    )
    acquire_lock(run_dir)  # dead same-host pid => stale => cleared
    release_lock(run_dir)


# ---------------------------------------------------------------- events

def test_events_reader_tolerates_trailing_partial_line(tmp_path, git_repo):
    f = {"name": "ev", "nodes": [{"id": "a", "kind": "fake", "spec": {}, "final": True}]}
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    with open(h.run_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-01-01T00:00:00Z", "kind": "transi')  # crash mid-write
    events = read_events(h.run_dir)
    assert events, "partial trailing line skipped, not an error"
    from lockstep.cli import main

    assert main(["status", str(h.run_dir)]) == 0


# ---------------------------------------------------------------- kill_tree / timeout

def test_kill_tree_reaps_grandchild_on_timeout(tmp_path):
    from lockstep.executors.proc import spawn, wait_or_kill

    heartbeat = tmp_path / "hb.txt"
    grandchild = (
        "import time, sys\n"
        "while True:\n"
        "    open(sys.argv[1], 'a').write('x')\n"
        "    time.sleep(0.1)\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}, sys.argv[1]])\n"
        "time.sleep(60)\n"
    )
    proc = spawn(
        [PY, "-c", parent, str(heartbeat)],
        cwd=tmp_path,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    exit_code, timed_out = wait_or_kill(proc, timeout_s=3)
    assert timed_out
    time.sleep(1.0)
    size1 = heartbeat.stat().st_size if heartbeat.exists() else 0
    time.sleep(1.0)
    size2 = heartbeat.stat().st_size if heartbeat.exists() else 0
    assert size1 == size2, "grandchild still writing => not reaped"


def test_job_object_reaps_tree_when_only_the_top_pid_dies(tmp_path):
    """The reported Windows failure (DEVIATIONS 2026-08-10): kill ONLY the
    top-level pid and the descendants survive, because a `cmd.exe`-style shim
    exits as soon as it has launched its child and the PPID walk loses it.

    No taskkill and no kill_tree here on purpose — nothing walks the tree. On
    Windows the only thing that can reap the grandchild is KILL_ON_JOB_CLOSE
    firing when the driver's job handle closes (so the Windows branch issues no
    kill at all — closing the handle IS the mechanism under test); on POSIX it
    is the process group. Both assert the same observable outcome.
    """
    import os
    import signal as _signal
    import sys as _sys

    from lockstep.executors.proc import _close_job, job_unavailable_reason, spawn

    heartbeat = tmp_path / "hb.txt"
    grandchild = (
        "import time, sys\n"
        "while True:\n"
        "    open(sys.argv[1], 'a').write('x')\n"
        "    time.sleep(0.05)\n"
    )
    # The shim shape: spawn the grandchild, then EXIT immediately, orphaning it.
    shim = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}, sys.argv[1]])\n"
    )
    proc = spawn(
        [PY, "-c", shim, str(heartbeat)],
        cwd=tmp_path,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    try:
        if _sys.platform == "win32" and not getattr(proc, "_lockstep_job", None):
            # Nested job without breakaway, container, locked-down policy: the
            # taskkill fallback is correct there and this guarantee simply does
            # not apply. Skip rather than fail — but say WHY, so a machine that
            # silently lost the guarantee is not mistaken for a passing one.
            pytest.skip(f"no job object available here: {job_unavailable_reason(proc)}")
        # Let the shim exit on its own and the grandchild get going.
        proc.wait(timeout=30)
        deadline = time.time() + 20
        while time.time() < deadline and not heartbeat.exists():
            time.sleep(0.05)
        assert heartbeat.exists(), "grandchild never started"

        if _sys.platform == "win32":
            _close_job(proc)  # what driver death does implicitly
        else:
            # NOT getpgid(proc.pid): proc.wait() above already reaped the shim,
            # so that lookup raises. The group outlives it, and is named by the
            # leader's pid because spawn() uses start_new_session.
            os.killpg(proc.pid, _signal.SIGKILL)

        time.sleep(1.0)
        size1 = heartbeat.stat().st_size
        time.sleep(1.0)
        assert heartbeat.stat().st_size == size1, "orphaned grandchild still writing"
    finally:
        # wait_or_kill is bypassed here, so nothing else closes these.
        for fh in getattr(proc, "_lockstep_files", ()):
            fh.close()
        _close_job(proc)


def test_a_backgrounded_process_survives_its_node_and_dies_with_the_driver(tmp_path):
    """A node may deliberately leave a process running for LATER nodes — a
    DuckDB connection holder is the motivating case, and DuckDB's single-writer
    file lock makes both halves of this expensive to get wrong.

    Both halves are asserted here, because each is a different failure:
      - killing it at the node's clean exit breaks the flow (and diverges from
        POSIX, where kill_tree fires only on timeout);
      - letting it outlive the RUN recreates the reported bug — an orphan
        holding the database lock, which no later run can take.
    """
    import sys as _sys

    from lockstep.executors.proc import _close_job, spawn, wait_or_kill

    heartbeat = tmp_path / "hb.txt"
    child = (
        "import time, sys\n"
        "while True:\n"
        "    open(sys.argv[1], 'a').write('x')\n"
        "    time.sleep(0.05)\n"
    )
    node = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}, sys.argv[1]])\n"
    )
    proc = spawn(
        [PY, "-c", node, str(heartbeat)],
        cwd=tmp_path,
        stdout_path=tmp_path / "o.log",
        stderr_path=tmp_path / "e.log",
    )
    try:
        exit_code, timed_out = wait_or_kill(proc, timeout_s=30)
        assert (exit_code, timed_out) == (0, False)

        deadline = time.time() + 20
        while time.time() < deadline and not heartbeat.exists():
            time.sleep(0.05)
        assert heartbeat.exists(), "backgrounded process never started"

        # Half one: it outlived the node's clean exit.
        size1 = heartbeat.stat().st_size
        time.sleep(1.0)
        assert heartbeat.stat().st_size > size1, "backgrounded process was killed at node exit"

        if _sys.platform != "win32" or not getattr(proc, "_lockstep_job", None):
            pytest.skip("driver-exit reap is the Windows job object's half")

        # Half two: and dies when the driver does.
        _close_job(proc)
        time.sleep(1.0)
        size2 = heartbeat.stat().st_size
        time.sleep(1.0)
        assert heartbeat.stat().st_size == size2, "survivor outlived the run"
    finally:
        for fh in getattr(proc, "_lockstep_files", ()):
            fh.close()
        _close_job(proc)


def test_empty_job_is_not_reported_as_a_kill(tmp_path):
    """`lockstep cancel` keys its CANCELLED marker off kill_pid_tree's return,
    and a marker left behind rewrites a node that SUCCEEDED as failed(cancelled).

    TerminateJobObject returns TRUE against a job with no live members, so
    "the job existed" must not be reported as "a kill was issued". The pid path
    could never do this — taskkill on a dead pid fails — which is what
    cmd_cancel's marker-unlink has always relied on.
    """
    import sys as _sys

    from lockstep.executors.proc import (
        _close_job,
        _create_job,
        _terminate_job_by_name,
        job_name_of,
        kill_pid_tree,
        spawn,
    )

    if _sys.platform != "win32":
        pytest.skip("job objects are Windows-only")

    job, why = _create_job()
    assert job is not None, f"could not create a job object at all: {why}"
    try:
        # A live, openable, EMPTY job — the state a node's job is in for the
        # window between the child exiting and the driver closing its handle.
        assert _terminate_job_by_name(job[1]) is False
    finally:
        from lockstep.executors.proc import _kernel32

        import ctypes as _ct

        _kernel32().CloseHandle(_ct.c_void_p(job[0]))

    # End to end: a finished node's recorded handles must not yield a success.
    proc = spawn(
        [PY, "-c", "pass"],
        cwd=tmp_path,
        stdout_path=tmp_path / "o.log",
        stderr_path=tmp_path / "e.log",
    )
    jn = job_name_of(proc)
    try:
        proc.wait(timeout=30)
        assert kill_pid_tree(proc.pid, jn) is False, "reported a kill of a dead node"
    finally:
        for fh in getattr(proc, "_lockstep_files", ()):
            fh.close()
        _close_job(proc)


def test_spawn_handles_are_recorded_and_stale_job_names_cleared(tmp_path):
    """cancel reads pid.txt and prefers job_name.txt; a stale name from a
    previous attempt would send it at a different object than the live pid."""
    import sys as _sys

    from lockstep.executors.proc import _close_job, job_name_of, record_spawn_handles, spawn

    phase = tmp_path / "phase"
    phase.mkdir()
    proc = spawn(
        [PY, "-c", "pass"],
        cwd=tmp_path,
        stdout_path=tmp_path / "o.log",
        stderr_path=tmp_path / "e.log",
    )
    try:
        record_spawn_handles(phase, proc)
        assert (phase / "pid.txt").read_text(encoding="utf-8").strip() == str(proc.pid)
        jn = job_name_of(proc)
        if jn:
            assert (phase / "job_name.txt").read_text(encoding="utf-8").strip() == jn
            assert not (phase / "job-unavailable.txt").exists()
        elif _sys.platform == "win32":
            # The fallback must be visible in the run dir, not silent.
            assert (phase / "job-unavailable.txt").read_text(encoding="utf-8").strip()
        proc.wait(timeout=30)
    finally:
        for fh in getattr(proc, "_lockstep_files", ()):
            fh.close()
        _close_job(proc)

    # A later spawn that gets no job must REMOVE the earlier name, not leave it.
    (phase / "job_name.txt").write_text("Local\\lockstep-stale", encoding="utf-8")

    class _NoJob:
        pid = 4242

    record_spawn_handles(phase, _NoJob())  # type: ignore[arg-type]
    assert not (phase / "job_name.txt").exists(), "stale job name survived a jobless spawn"
    assert (phase / "pid.txt").read_text(encoding="utf-8").strip() == "4242"


def test_spawn_leaks_no_job_handle_when_popen_rejects_the_argv(tmp_path):
    """`except OSError` around Popen was too narrow: a NUL anywhere in argv —
    reachable from any interpolated value — raises ValueError, which escaped
    with the job handle unreferenced. Each leak pins a KILL_ON_JOB_CLOSE kernel
    object for the driver's lifetime."""
    import sys as _sys

    from lockstep.executors.proc import spawn

    if _sys.platform != "win32":
        pytest.skip("job objects are Windows-only")

    import ctypes as _ct

    def handles() -> int:
        n = _ct.c_uint32(0)
        _ct.WinDLL("kernel32").GetProcessHandleCount(
            _ct.WinDLL("kernel32").GetCurrentProcess(), _ct.byref(n)
        )
        return int(n.value)

    for _ in range(5):  # warm the interpreter's own handle churn
        with pytest.raises((ValueError, OSError)):
            spawn([PY, "-c", "pass\x00"], cwd=tmp_path,
                  stdout_path=tmp_path / "o.log", stderr_path=tmp_path / "e.log")
    before = handles()
    for _ in range(60):
        with pytest.raises((ValueError, OSError)):
            spawn([PY, "-c", "pass\x00"], cwd=tmp_path,
                  stdout_path=tmp_path / "o.log", stderr_path=tmp_path / "e.log")
    grew = handles() - before
    assert grew < 30, f"leaked ~{grew} handles over 60 rejected spawns"


def test_job_unavailable_reason_is_per_spawn_not_global(tmp_path):
    """The reason is written into a node's OWN phase dir, and nodes run
    concurrently on a thread pool — a module global let one node's artifact
    report another node's failure, which is worse than reporting none."""
    from lockstep.executors.proc import _close_job, job_name_of, job_unavailable_reason, spawn

    procs = []
    try:
        for _ in range(3):
            p = spawn([PY, "-c", "pass"], cwd=tmp_path,
                      stdout_path=tmp_path / "o.log", stderr_path=tmp_path / "e.log")
            procs.append(p)
        for p in procs:
            # Each proc answers for itself: a job means no reason, and no job
            # means a reason that is this proc's own.
            assert bool(job_name_of(p)) is (not job_unavailable_reason(p))
    finally:
        for p in procs:
            p.wait(timeout=30)
            for fh in getattr(p, "_lockstep_files", ()):
                fh.close()
            _close_job(p)


def test_record_spawn_handles_never_raises_into_a_live_child(tmp_path):
    """It runs between spawn() and wait_or_kill(); an escaping OSError there
    abandons a LIVE child unwaited, with its job handle held for the driver's
    lifetime. This machine's AV throws transient PermissionError on writes."""
    from lockstep.executors.proc import record_spawn_handles

    class _Proc:
        pid = 7

    # A path that cannot be written: the phase dir does not exist.
    record_spawn_handles(tmp_path / "missing" / "phase", _Proc())  # type: ignore[arg-type]


def test_shell_timeout_auto_retry_then_failed(tmp_path, git_repo):
    f = {
        "name": "to",
        "nodes": [
            {
                "id": "slow", "kind": "shell", "timeout_s": 1,
                "spec": {"cmd": [PY, "-c", "import time; time.sleep(30)"]}, "final": True,
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    rec = load_state(h.run_dir).nodes["slow"]
    assert rec.status == "failed"
    assert rec.attempts == 2, "M4: one automatic retry on timeout, even with retry.max=0"


# ---------------------------------------------------------------- kill-between-nodes / CLI

def test_kill_between_nodes_is_resumable_via_cli(tmp_path, git_repo, monkeypatch):
    from lockstep.cli import main

    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(
        json.dumps(
            {
                "name": "clirun",
                "nodes": [
                    {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}},
                    {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {"outputs": ["B"]}, "final": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(git_repo)
    assert main(["run", str(flow_path), "--runs-dir", str(tmp_path / "runs")]) == 0
    run_dir = next((tmp_path / "runs").iterdir())
    # Simulate a crash between nodes: b back to running, lock left behind.
    st = load_state(run_dir)
    st.nodes["b"].status = "running"
    write_state(run_dir, st)
    (run_dir / "lock").write_text(
        json.dumps({"pid": 999999999, "hostname": "elsewhere", "started": "t"}), encoding="utf-8"
    )
    assert main(["resume", str(run_dir)]) == 8, "cross-host lock => exit 8 without --force-unlock"
    assert main(["resume", str(run_dir), "--force-unlock"]) == 0
    assert load_state(run_dir).nodes["b"].status == "done"


def test_audit_findings_regressions(tmp_path):
    # Three minors upheld by the audit-spec arbiter gate (2026-07-25).
    from lockstep.cli import main
    from lockstep.contracts import ContractError, resolve_contract
    from lockstep.interpolate import InterpolationError, fence_context_file

    # 1. Usage errors exit 7 (config), never 2 (frozen: gate BLOCK).
    with pytest.raises(SystemExit) as exc:
        main(["no-such-command"])
    assert exc.value.code == 7
    # 2. A broken contracts module is a ContractError, not a crash.
    bad = tmp_path / "bad_contracts.py"
    bad.write_text("this is not python ((((", encoding="utf-8")
    with pytest.raises(ContractError, match="failed to load"):
        resolve_contract(f"{bad}:Whatever")
    # 3. Over-cap context file with no spill dir errors, mirroring render_template.
    with pytest.raises(InterpolationError, match="exceeds cap"):
        fence_context_file("big.py", "x" * 200, max_interp_chars=100, spill_dir=None)


def test_cli_verify_and_dry_run_and_render(tmp_path, git_repo, monkeypatch, capsys):
    from lockstep.cli import main

    flow_path = git_repo / "v.tg.json"
    flow_path.write_text(
        json.dumps(
            {
                "name": "v",
                "nodes": [
                    {"id": "a", "kind": "fake", "spec": {}},
                    {"id": "b", "kind": "fake", "depends_on": ["a"], "spec": {}, "final": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(git_repo)
    assert main(["verify", str(flow_path)]) == 0
    assert main(["run", str(flow_path), "--dry-run", "--runs-dir", str(tmp_path / "r2")]) == 0
    out = capsys.readouterr().out
    assert "wave" in out
    assert main(["render", str(flow_path)]) == 0
    assert "flowchart TD" in capsys.readouterr().out
    bad = git_repo / "bad.tg.json"
    bad.write_text('{"name": "x", "nodes": [{"id": "a", "kind": "nope"}]}', encoding="utf-8")
    assert main(["verify", str(bad)]) == 5
