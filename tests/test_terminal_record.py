"""S6 (upstream-response-ow07-feedback, 0.10.1): a refusal AFTER the lock was
taken must leave an authoritative run-level terminal record.

The reported bug: a detached dirty-scope refusal left all nodes `pending`, no
journal, and `wait` reconstructed exit 4 — "stopped with runnable work
remaining; a plain resume continues". That advice is live ammunition: the
dirty-scope preflight is fresh-runs-only (E9), so the resume it recommends
skips the exact protection that refused the run, and a hash-missed writer
would legally overwrite the operator's edits. The record makes `wait`,
`status` and `active` report what actually happened; MISSION reads the same
field (test_mission_render pins that surface).
"""

from __future__ import annotations

import json

from lockstep.cli import main
from lockstep.state import load_state, trace_status

from conftest import git


def _write_flow(git_repo):
    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(
        json.dumps({
            "name": "refuse",
            "nodes": [{
                "id": "w", "kind": "fake", "final": True,
                "spec": {"outputs": ["ok"], "write_files": {"src/a.py": "x"},
                         "writes": ["src"]},
            }],
        }),
        encoding="utf-8",
    )
    return flow_path


def _refused_run(tmp_path, git_repo, monkeypatch):
    """Drive a fresh run into the dirty-scope refusal; return (runs, run_dir)."""
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "a.py").write_text("operator edit, uncommitted\n", encoding="utf-8")
    flow_path = _write_flow(git_repo)
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 7
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    return flow_path, runs, run_dir


def test_dirty_scope_refusal_is_recorded_and_wait_reports_7(tmp_path, git_repo, monkeypatch):
    _, _, run_dir = _refused_run(tmp_path, git_repo, monkeypatch)
    st = load_state(run_dir)
    # The refusal happened before any node event — exactly the shape that used
    # to read as exit 4.
    assert all(r.status == "pending" for r in st.nodes.values())
    t = st.terminal
    assert t is not None
    assert t.status == "refused"
    assert t.exit_code == 7
    assert t.reason == "dirty_scope"
    assert "--allow-dirty-scope" in t.message
    # Acceptance 4: a refusal event exists and the chain verifies.
    ts = trace_status(run_dir)
    assert ts["ok"] and ts["chained"] >= 1, ts
    assert '"refusal"' in (run_dir / "events.jsonl").read_text(encoding="utf-8")
    # Acceptance 2: `wait` returns 7, never 4, with all nodes pending.
    assert main(["wait", str(run_dir)]) == 7


def test_record_clears_on_the_next_drive(tmp_path, git_repo, monkeypatch):
    _, _, run_dir = _refused_run(tmp_path, git_repo, monkeypatch)
    # The operator commits the edit and resumes — the drive must dissolve the
    # stale refusal, or every later `wait` reports a refusal that no longer
    # describes the run.
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-m", "operator adopts the edit")
    assert main(["resume", str(run_dir)]) == 0
    st = load_state(run_dir)
    assert st.terminal is None
    assert st.nodes["w"].status == "done"
    assert main(["wait", str(run_dir)]) == 0


def test_status_names_the_refusal(tmp_path, git_repo, monkeypatch, capsys):
    _, _, run_dir = _refused_run(tmp_path, git_repo, monkeypatch)
    capsys.readouterr()
    assert main(["status", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "refused: dirty scope" in out
    # The message is the evidence — the operator must see the named paths and
    # the named way out, not a category.
    assert "--allow-dirty-scope" in out


def test_active_surfaces_a_refused_run_by_default(tmp_path, git_repo, monkeypatch, capsys):
    _, runs, _ = _refused_run(tmp_path, git_repo, monkeypatch)
    capsys.readouterr()
    # No --all: a refusal is recent, operator-relevant news, not an old idle
    # run to bury.
    assert main(["active", str(runs)]) == 0
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "dirty scope" in out


def test_wait_and_status_word_an_auto_reject_as_awaiting_a_human(tmp_path, git_repo, capsys):
    """S5 (upstream-response-ow07-feedback): "nobody was there" is a different
    fact from "the human said no", and the record already encodes it
    (roles.py's own comment). The surfaces must stop rendering the two
    identically. Exit 6 is unchanged — it is the documented handoff signal."""
    from conftest import build
    from lockstep.state import write_state

    f = {"name": "w", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["A"]}}]}
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    st.nodes["a"].role = "approval"
    st.nodes["a"].status = "blocked"
    st.nodes["a"].error = "approval auto-rejected (non-TTY stdin)"
    write_state(h.run_dir, st)
    capsys.readouterr()
    assert main(["wait", str(h.run_dir)]) == 6
    assert "resume from a terminal to answer" in capsys.readouterr().out
    assert main(["status", str(h.run_dir)]) == 0
    assert "awaiting a human decision" in capsys.readouterr().out
    # A real rejection is a decision, not a parked question — no awaiting line.
    st.nodes["a"].error = "approval rejected"
    write_state(h.run_dir, st)
    assert main(["wait", str(h.run_dir)]) == 6
    assert "resume from a terminal to answer" not in capsys.readouterr().out
    assert main(["status", str(h.run_dir)]) == 0
    assert "awaiting a human decision" not in capsys.readouterr().out
