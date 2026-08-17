"""Offline tests for contrib/lane.py and contrib/who_holds.py (fleet Batch 2).

The lane launcher is the one place the worktree-per-run recipe lives; these
pin the parts a fleet depends on: the persisted lane record, the start-lock
serialization, the refusal to harvest under a live driver, and worktree
cleanup. The one end-to-end start uses the real CLI via `-m lockstep` (fake
executor — zero tokens) because the whole point of `start` is the seam
between git, --detach, and the runs-dir listing."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import PY, git

_here = Path(__file__).resolve().parents[1] / "contrib"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _here / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


who_holds = _load("who_holds")
lane = _load("lane")

FLOW = {
    "name": "lane-smoke",
    "nodes": [{"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}, "final": True}],
}


# ----------------------------------------------------------------- who_holds

def test_who_holds_matrix(tmp_path):
    target = tmp_path / "data.duckdb"
    target.write_text("x", encoding="utf-8")
    hp = who_holds.holder_path(target)
    assert who_holds.classify(target) == "NONE"
    hp.write_text(json.dumps({"pid": os.getpid(), "purpose": "test"}), encoding="utf-8")
    assert who_holds.classify(target) == f"LIVE {os.getpid()} test"
    hp.write_text(json.dumps({"pid": 999999999, "purpose": "test"}), encoding="utf-8")
    assert who_holds.classify(target) == "STALE 999999999 test"
    hp.write_text(json.dumps({"pid": 1, "hostname": "elsewhere"}), encoding="utf-8")
    assert who_holds.classify(target) == "FOREIGN 1 elsewhere"
    hp.write_text("not json", encoding="utf-8")
    assert who_holds.classify(target).startswith("UNKNOWN")
    hp.write_text(json.dumps({"pid": "not-an-int"}), encoding="utf-8")
    assert who_holds.classify(target).startswith("UNKNOWN")


def test_who_holds_always_exits_0(tmp_path, capsys):
    assert who_holds.main([str(tmp_path / "missing.db")]) == 0
    assert capsys.readouterr().out.strip() == "NONE"


# ---------------------------------------------------------------- start lock

def test_start_lock_stale_clear_and_timeout(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    # A dead holder is cleared and the lock acquired.
    (runs / lane.START_LOCK).write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
    lock = lane._acquire_start_lock(runs, timeout=5.0)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    # A LIVE holder makes a second acquire time out with a named refusal.
    with pytest.raises(lane.LaneError, match="serialize"):
        lane._acquire_start_lock(runs, timeout=1.0)
    lock.unlink()


# ------------------------------------------------------- run-dir attribution

def test_confirm_run_dir_matches_by_recorded_root_only(tmp_path):
    """The identity check is the run's recorded root — a decoy run for the
    same flow with another root never matches, two claimants refuse, and a
    timeout KEEPS the worktree (AbortKeepWorktree) instead of deleting a
    tree a slow-starting driver may be about to use."""
    runs = tmp_path / "runs"
    runs.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    decoy = runs / "fleet-decoy"
    decoy.mkdir()
    (decoy / "state.json").write_text(
        json.dumps({"repo_root": str(tmp_path / "another-tree")}), encoding="utf-8"
    )
    with pytest.raises(lane.AbortKeepWorktree, match="KEPT"):
        lane._confirm_run_dir(runs, set(), wt, timeout=1.0)
    mine = runs / "fleet-mine"
    mine.mkdir()
    (mine / "state.json").write_text(json.dumps({"repo_root": str(wt)}), encoding="utf-8")
    assert lane._confirm_run_dir(runs, set(), wt, timeout=5.0) == mine
    # A pre-existing dir (in `before`) is never a candidate.
    with pytest.raises(lane.AbortKeepWorktree, match="KEPT"):
        lane._confirm_run_dir(runs, {mine.name}, wt, timeout=1.0)
    clone = runs / "fleet-clone"
    clone.mkdir()
    (clone / "state.json").write_text(json.dumps({"repo_root": str(wt)}), encoding="utf-8")
    with pytest.raises(lane.AbortKeepWorktree, match="claim"):
        lane._confirm_run_dir(runs, set(), wt, timeout=5.0)


# -------------------------------------------------------------- start (e2e)

def _main_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mainrepo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def test_start_end_to_end_records_the_lane(tmp_path, capsys):
    repo = _main_repo(tmp_path)
    flow = repo / "lane-smoke.tg.json"
    flow.write_text(json.dumps(FLOW), encoding="utf-8")
    rc = lane.main([
        "start", str(flow), "--main-repo", str(repo),
        "--lockstep-exe", f'"{PY}" -m lockstep',  # quoted: spaced paths must survive
    ])
    out = capsys.readouterr()
    assert rc == 0, out.err
    record = json.loads(out.out.strip().splitlines()[-1])  # the one machine line
    worktree = Path(record["worktree"])
    run_dir = Path(record["run_dir"])
    assert worktree.is_dir() and (worktree / lane.LANE_RECORD).is_file()
    assert json.loads((worktree / lane.LANE_RECORD).read_text(encoding="utf-8")) == record
    assert run_dir.is_dir() and (run_dir / "state.json").is_file()
    assert run_dir.parent == repo / "runs", "runs stay CENTRAL, in the main repo"
    # The Batch 1 seam: the run recorded the WORKTREE as its root.
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert os.path.normcase(state["repo_root"]) == os.path.normcase(str(worktree))
    assert record["branch"].startswith("lane/lane-smoke-")
    # gc.auto is unset in a fresh test repo — start must have warned (§6.3).
    assert "gc.auto" in out.err
    # Let the detached driver finish before abandoning (same discipline the
    # tool itself enforces: no teardown under a live driver).
    subprocess.run([PY, "-m", "lockstep", "wait", str(run_dir), "--timeout", "60"],
                   capture_output=True)
    # Cleanup for the fixture tree (also exercises abandon's happy path).
    assert lane.main(["abandon", str(worktree), "--main-repo", str(repo)]) == 0
    assert not worktree.exists()
    assert "lane/" not in subprocess.run(
        ["git", "-C", str(repo), "branch"], capture_output=True, text=True).stdout


def test_post_launch_failure_keeps_worktree_and_driver(tmp_path, capsys, monkeypatch):
    """Once the launch subprocess has returned 0 a detached driver EXISTS —
    an unexpected exception after that (a Ctrl+C in the confirm window, a
    bug of ours) must keep the worktree rather than deleting the tree the
    driver is running against, and must say so."""
    repo = _main_repo(tmp_path)
    flow = repo / "lane-smoke.tg.json"
    flow.write_text(json.dumps(FLOW), encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("synthetic post-launch failure")

    monkeypatch.setattr(lane, "_confirm_run_dir", boom)
    with pytest.raises(RuntimeError, match="synthetic"):
        lane.main([
            "start", str(flow), "--main-repo", str(repo),
            "--lockstep-exe", f'"{PY}" -m lockstep',
        ])
    err = capsys.readouterr().err
    assert "KEPT" in err
    lanes_root = tmp_path / "mainrepo-lanes"
    worktrees = [p for p in lanes_root.rglob(".git") if p.is_file()]
    assert worktrees, "the worktree must survive a post-launch failure"
    worktree = worktrees[0].parent
    # Settle the driver, then clean up (abandon works without a record).
    run_dir = next((repo / "runs").glob("lane-smoke-*"))
    subprocess.run([PY, "-m", "lockstep", "wait", str(run_dir), "--timeout", "120"],
                   capture_output=True)
    assert lane.main(["abandon", str(worktree), "--main-repo", str(repo)]) == 0


def test_two_concurrent_starts_attribute_their_own_runs(tmp_path):
    """The work-order test: two simultaneous starts of the SAME flow produce
    two lane records, each pointing at the run that recorded ITS worktree —
    the start-lock serializes the launches and the recorded-root confirm
    makes cross-wiring structurally impossible."""
    repo = _main_repo(tmp_path)
    flow = repo / "lane-smoke.tg.json"
    flow.write_text(json.dumps(FLOW), encoding="utf-8")
    lane_script = _here / "lane.py"

    def spawn(branch: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, str(lane_script), "start", str(flow),
             "--main-repo", str(repo), "--lockstep-exe", f'"{PY}" -m lockstep',
             "--branch", branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    p1, p2 = spawn("lane/one"), spawn("lane/two")
    out1, err1 = p1.communicate(timeout=300)
    out2, err2 = p2.communicate(timeout=300)
    assert p1.returncode == 0, err1
    assert p2.returncode == 0, err2
    r1 = json.loads(out1.strip().splitlines()[-1])
    r2 = json.loads(out2.strip().splitlines()[-1])
    assert r1["run_dir"] != r2["run_dir"]
    assert r1["worktree"] != r2["worktree"]
    for rec in (r1, r2):
        state = json.loads(
            (Path(rec["run_dir"]) / "state.json").read_text(encoding="utf-8")
        )
        assert os.path.normcase(state["repo_root"]) == os.path.normcase(rec["worktree"]), (
            "a lane record must point at the run that recorded ITS worktree"
        )
    for rec in (r1, r2):
        subprocess.run([PY, "-m", "lockstep", "wait", rec["run_dir"], "--timeout", "120"],
                       capture_output=True)
        assert lane.main(["abandon", rec["worktree"], "--main-repo", str(repo)]) == 0


# ------------------------------------------------------------------- harvest

def _fake_lane(tmp_path: Path, repo: Path, *, with_change: bool = True) -> tuple[Path, Path]:
    """A worktree + lane record WITHOUT a live run: harvest/abandon unit rig."""
    worktree = tmp_path / "lanes" / "wt"
    git(repo, "worktree", "add", "-b", "lane/test", str(worktree))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    record = {
        "worktree": str(worktree), "branch": "lane/test", "run_dir": str(run_dir),
        "driver_pid": 999999999, "flow": "f", "flow_name": "lane-smoke", "args": [],
        "started": "t",
    }
    (worktree / lane.LANE_RECORD).write_text(json.dumps(record), encoding="utf-8")
    if with_change:
        (worktree / "delivered.txt").write_text("work\n", encoding="utf-8")
    return worktree, run_dir


def test_harvest_refuses_live_driver(tmp_path, capsys):
    repo = _main_repo(tmp_path)
    worktree, run_dir = _fake_lane(tmp_path, repo)
    (run_dir / "lock").write_text(
        json.dumps({"pid": os.getpid(), "hostname": socket.gethostname()}), encoding="utf-8"
    )
    assert lane.main(["harvest", str(worktree), "--main-repo", str(repo)]) == 1
    assert "driver" in capsys.readouterr().err
    assert worktree.is_dir(), "a refused harvest must not touch the worktree"


def test_harvest_commits_excludes_record_and_removes(tmp_path, capsys):
    repo = _main_repo(tmp_path)
    worktree, run_dir = _fake_lane(tmp_path, repo)
    # A DEAD lock (stale driver) must not block a harvest.
    (run_dir / "lock").write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
    assert lane.main(["harvest", str(worktree), "--main-repo", str(repo)]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["harvested"] and result["commit"]
    assert not worktree.exists(), "harvest removes the worktree"
    show = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=%s", "lane/test"],
        capture_output=True, text=True,
    ).stdout
    assert "delivered.txt" in show
    assert lane.LANE_RECORD not in show, "the lane record is plumbing, never cargo"
    assert "lane-smoke" in show  # commit message names the flow


def test_harvest_without_record_is_exit_2(tmp_path, capsys):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "plainwt"
    git(repo, "worktree", "add", "-b", "lane/bare", str(worktree))
    assert lane.main(["harvest", str(worktree), "--main-repo", str(repo)]) == 2
    assert lane.LANE_RECORD in capsys.readouterr().err


def test_abandon_refuses_live_driver_without_force(tmp_path, capsys):
    repo = _main_repo(tmp_path)
    worktree, run_dir = _fake_lane(tmp_path, repo)
    (run_dir / "lock").write_text(
        json.dumps({"pid": os.getpid(), "hostname": socket.gethostname()}), encoding="utf-8"
    )
    assert lane.main(["abandon", str(worktree), "--main-repo", str(repo)]) == 1
    assert "--force" in capsys.readouterr().err
    assert worktree.is_dir()
