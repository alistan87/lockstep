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
