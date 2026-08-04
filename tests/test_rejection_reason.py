"""Cockpit UX proposal T1.2: the rejection reason is an artifact, not narration.

The asymmetry this closes: evidence travels human-ward as a file, on the stated
grounds that a narrated summary at a decision point cannot be trusted. The
reason for a REJECTION — the most decision-relevant thing the human produces all
session — travelled back the other way through the orchestrator's account of it.

`rejection.txt` is written by the HUMAN. That is what makes it usable as a
tripwire against the journal, which is written by the orchestrator, and against
state.json, which is written by the engine. Three authors, so two of them can be
checked against the third.

The PowerShell that prompts for it is not reachable from pytest; what is tested
here is the shape it writes and every consumer of that shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
sys.path.insert(0, str(CONTRIB))

import retrospect  # noqa: E402

REJECTION = """\
========================================================================
  WHY THIS WAS REJECTED - in the words of the person who rejected it
========================================================================

  the second table counts suspended accounts as active

  recorded 2026-08-03T09:00:00.0000000Z
"""


def make_run(tmp_path: Path, *, journal: list[dict] | None = None,
             rejection: str | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "state.json").write_text(json.dumps({
        "flow_name": "f", "flow_hash": "abc123def456",
        "token_spawns": 3,
        "nodes": {"ask": {"role": "approval", "status": "blocked",
                          "error": "approval rejected"}},
    }), encoding="utf-8")
    if journal is not None:
        (run / "cockpit-journal.jsonl").write_text(
            "\n".join(json.dumps(e) for e in journal), encoding="utf-8")
    if rejection is not None:
        (run / "rejection.txt").write_text(rejection, encoding="utf-8")
    return run


def test_the_reason_survives_the_framing_around_it():
    assert retrospect._rejection_reason(REJECTION) == (
        "the second table counts suspended accounts as active"
    )


def test_no_rejection_file_is_not_drift(tmp_path):
    run = retrospect.collect_run(make_run(tmp_path, journal=[]))
    assert retrospect.told_vs_state(run) == []


def test_an_unrelayed_rejection_is_drift(tmp_path):
    # The human wrote down why. The orchestrator's record does not mention it.
    run = retrospect.collect_run(make_run(tmp_path, journal=[], rejection=REJECTION))
    drift = retrospect.told_vs_state(run)
    assert any("never mentions it" in d for d in drift)


def test_a_faithful_relay_is_not_drift(tmp_path):
    run = retrospect.collect_run(make_run(
        tmp_path,
        journal=[{"kind": "note",
                  "note": "rejected: the second table counts suspended accounts as active"}],
        rejection=REJECTION))
    assert retrospect.told_vs_state(run) == []


def test_a_relay_about_something_else_is_flagged(tmp_path):
    run = retrospect.collect_run(make_run(
        tmp_path,
        journal=[{"kind": "note", "note": "rejected because the formatting looked odd"}],
        rejection=REJECTION))
    assert any("little in common" in d for d in retrospect.told_vs_state(run))


def test_the_tripwire_never_auto_judges(tmp_path):
    # Same standard as the existing fidelity tripwire: it surfaces candidates
    # for a human to read. It must not read as a verdict.
    run = retrospect.collect_run(make_run(
        tmp_path,
        journal=[{"kind": "note", "note": "rejected because the formatting looked odd"}],
        rejection=REJECTION))
    assert all("worth a human read" in d or "never mentions" in d
               for d in retrospect.told_vs_state(run))


def test_collect_run_tolerates_an_unreadable_rejection(tmp_path):
    run_dir = make_run(tmp_path, journal=[])
    (run_dir / "rejection.txt").write_bytes(b"\xff\xfe not text at all")
    run = retrospect.collect_run(run_dir)      # must not raise
    assert isinstance(run["rejection"], str)
