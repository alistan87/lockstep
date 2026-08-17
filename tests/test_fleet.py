"""Fleet guardrails (concurrent-orchestration work order, Batch 1): the
recorded resume root, the run-attach fall-through, and the pin on attaching
under a live lock. One worktree per concurrent run means the newest lineage
for a flow often lives in ANOTHER tree — these are the rails that make that
safe without bricking plain `lockstep run` from the main checkout."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from lockstep.cli import main
from lockstep.state import load_state

FLOW = {
    "name": "fleet",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["A"]}, "final": True},
    ],
}


def _write_flow(dirpath: Path) -> Path:
    p = dirpath / "fleet.tg.json"
    p.write_text(json.dumps(FLOW), encoding="utf-8")
    return p


def _run_dirs(runs: Path) -> list[Path]:
    return sorted(d for d in runs.iterdir() if (d / "state.json").exists())


def _first_run(tmp_path, git_repo, monkeypatch) -> tuple[Path, Path, Path]:
    """One completed CLI run; returns (flow_path, runs_dir, run_dir)."""
    flow = _write_flow(git_repo)
    runs = tmp_path / "runs"
    monkeypatch.chdir(git_repo)
    assert main(["run", str(flow), "--runs-dir", str(runs)]) == 0
    (run_dir,) = _run_dirs(runs)
    return flow, runs, run_dir


# ------------------------------------------------------------- recorded root

def test_run_records_resolved_repo_root(tmp_path, git_repo, monkeypatch):
    _, _, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    st = load_state(run_dir)
    assert st.repo_root, "a fresh run must record the root it ran against"
    assert os.path.normcase(st.repo_root) == os.path.normcase(str(git_repo.resolve()))


def test_pre_field_state_loads_and_attaches(tmp_path, git_repo, monkeypatch, capsys):
    # A run recorded before the field existed: loads, and attach treats the
    # unknown root as "no mismatch" — never as one.
    flow, runs, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    raw = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    raw.pop("repo_root", None)
    (run_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
    assert load_state(run_dir).repo_root == ""
    assert main(["run", str(flow), "--runs-dir", str(runs)]) == 0
    assert _run_dirs(runs) == [run_dir], "unknown root must attach, not fork a lineage"
    assert "attaching" in capsys.readouterr().out


# ---------------------------------------------------------------- resume root

def test_resume_refuses_wrong_root(tmp_path, git_repo, monkeypatch, capsys):
    _, _, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rc = main(["resume", str(run_dir), "--repo-root", str(elsewhere)])
    assert rc == 7, "wrong-tree resume is a refusal (EXIT_CONFIG), not a warning"
    err = capsys.readouterr().err
    # Both paths named, so the operator can see which tree to go back to.
    assert str(git_repo.resolve()) in err
    assert str(elsewhere.resolve()) in err


def test_resume_same_root_case_insensitive(tmp_path, git_repo, monkeypatch):
    # Windows: a case-different spelling of the SAME tree is not a mismatch.
    _, _, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    spelling = str(git_repo.resolve())
    swapped = spelling.swapcase() if os.name == "nt" else spelling
    assert main(["resume", str(run_dir), "--repo-root", swapped]) == 0


# ------------------------------------------------------- run-attach behaviour

def test_run_attach_falls_through_on_root_mismatch(tmp_path, git_repo, monkeypatch, capsys):
    flow, runs, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    other = tmp_path / "otherroot"
    other.mkdir()
    rc = main(["run", str(flow), "--repo-root", str(other), "--runs-dir", str(runs)])
    assert rc == 0
    dirs = _run_dirs(runs)
    assert len(dirs) == 2, "a root mismatch starts a NEW lineage instead of refusing"
    out = capsys.readouterr().out
    assert "new lineage" in out
    # The original lineage was not touched.
    assert os.path.normcase(load_state(run_dir).repo_root) == os.path.normcase(
        str(git_repo.resolve()))


def test_run_attach_under_live_lock_exits_8_and_writes_nothing(
    tmp_path, git_repo, monkeypatch, capsys
):
    # Pin (work order Batch 1 item 2): a second `run` for the same flow+args
    # while the lineage's lock is LIVE must exit 8 having neither written
    # state nor taken/altered the lock nor forked a new lineage. (It DOES
    # read state.json — the invariant is "never writes or locks".)
    flow, runs, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    lock_payload = json.dumps(
        {"pid": os.getpid(), "hostname": socket.gethostname(), "started": "t"}
    )
    (run_dir / "lock").write_text(lock_payload, encoding="utf-8")
    state_bytes = (run_dir / "state.json").read_bytes()
    rc = main(["run", str(flow), "--runs-dir", str(runs)])
    assert rc == 8
    assert (run_dir / "state.json").read_bytes() == state_bytes
    assert (run_dir / "lock").read_text(encoding="utf-8") == lock_payload
    assert _run_dirs(runs) == [run_dir]
    capsys.readouterr()


# ------------------------------------------------------------------ surfacing

def test_status_and_active_print_recorded_root(tmp_path, git_repo, monkeypatch, capsys):
    _, runs, run_dir = _first_run(tmp_path, git_repo, monkeypatch)
    assert main(["status", str(run_dir)]) == 0
    assert str(git_repo.resolve()) in capsys.readouterr().out
    # active only lists unfinished/locked runs; make this one look driven.
    (run_dir / "lock").write_text(
        json.dumps({"pid": os.getpid(), "hostname": socket.gethostname(), "started": "t"}),
        encoding="utf-8",
    )
    assert main(["active", str(runs)]) == 0
    assert str(git_repo.resolve()) in capsys.readouterr().out
