"""Offline tests for contrib/deliver.py — the egress node.

It runs downstream of a human approval, so its failure modes are unusually
consequential: a hard failure there leaves a run looking rejected when the
human said yes, and a silent one loses the deliverable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "deliver", Path(__file__).resolve().parents[1] / "contrib" / "deliver.py"
)
deliver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(deliver)


def make_run(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    phase = run / "phases" / "record"
    phase.mkdir(parents=True)
    (run / "approval-evidence.txt").write_text("the evidence\n", encoding="utf-8")
    return run, phase


def test_relative_source_resolves_against_the_run_dir(tmp_path, monkeypatch):
    """Shell nodes run with the repo root as cwd, but approval artifacts live
    in the run dir and no {run_dir} interpolation form exists."""
    run, phase = make_run(tmp_path)
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(phase))
    monkeypatch.chdir(tmp_path)
    assert deliver.resolve_source("approval-evidence.txt") == run / "approval-evidence.txt"


def test_cwd_is_still_used_when_the_run_dir_has_no_such_file(tmp_path, monkeypatch):
    run, phase = make_run(tmp_path)
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(phase))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PLAN.md").write_text("plan", encoding="utf-8")
    assert deliver.resolve_source("PLAN.md") == Path("PLAN.md")
    del run


def test_copies_into_the_deliverables_folder(tmp_path, monkeypatch, capsys):
    run, phase = make_run(tmp_path)
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(phase))
    monkeypatch.chdir(tmp_path)
    assert deliver.main(["approval-evidence.txt", "--to", str(tmp_path / "Deliverables")]) == 0
    assert (tmp_path / "Deliverables" / "approval-evidence.txt").read_text(
        encoding="utf-8") == "the evidence\n"
    assert "approved" in capsys.readouterr().out
    del run


def test_a_missing_source_still_prints_to_stdout(tmp_path, monkeypatch, capsys):
    """A shell node declaring output: "text" with empty stdout is failed by the
    engine as 'no result emitted' — so the graceful path must still say
    something on stdout, not only on stderr."""
    monkeypatch.chdir(tmp_path)
    assert deliver.main(["nope.txt"]) == 0
    out = capsys.readouterr()
    assert out.out.strip(), "stdout was empty; the engine would fail this node"
    assert "not found" in out.out
