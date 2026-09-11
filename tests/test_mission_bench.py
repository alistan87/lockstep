"""Phase 0 of the MISSION-scale work: the cost centres, pinned in BYTES.

These are CHARACTERIZATION tests. They assert what a render costs today, so
that S1 (incremental projections) is measured rather than believed — the
acceptance criterion the downstream request asked for in as many words: "a
test asserts bytes read, not only elapsed time."

Two of them are written to FAIL when S1 lands. That is their job: the quiet
heartbeat reading the whole journal prefix is the defect, and the test that
pins it is the one that will prove the fix. Each says so, so nobody
"repairs" the suite by deleting the evidence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"
if str(CONTRIB) not in sys.path:
    sys.path.insert(0, str(CONTRIB))

_SPEC = importlib.util.spec_from_file_location(
    "mission_bench", CONTRIB / "mission_bench.py")
mission_bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mission_bench)


@pytest.fixture()
def fixture_runs(tmp_path):
    """A small retained-history fixture: 4 runs, a 400-line journal, rotated
    attempt logs. Small enough for the suite, shaped like the real thing."""
    return mission_bench.synthesize(
        tmp_path / "runs", runs=4, nodes=3, events=400, attempts=2, log_kb=4)


@pytest.fixture()
def report(fixture_runs, tmp_path):
    return mission_bench.profile(fixture_runs, tmp_path)


# ------------------------------------------------------- the counters work at all


def test_counting_reports_real_bytes(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("a" * 5000, encoding="utf-8")
    with mission_bench.counting() as c:
        p.read_text(encoding="utf-8")
    assert c.bytes == 5000
    assert c.files == 1


def test_counting_restores_the_primitives(tmp_path):
    import builtins
    import pathlib

    before = (pathlib.Path.read_text, pathlib.Path.stat, builtins.open)
    with mission_bench.counting():
        pass
    assert (pathlib.Path.read_text, pathlib.Path.stat, builtins.open) == before


def test_counting_restores_after_an_exception(tmp_path):
    """A benchmark that leaves `open` monkeypatched on the way out poisons
    every test after it — worse than no benchmark."""
    import builtins

    before = builtins.open
    with pytest.raises(ValueError):
        with mission_bench.counting():
            raise ValueError("boom")
    assert builtins.open is before


# ------------------------------------------------------- today's cost, pinned


def test_quiet_heartbeat_reads_the_whole_journal_today(report):
    """THE defect, pinned: a heartbeat with NOTHING new to report still reads
    essentially the entire journal, once a second, forever.

    *** THIS TEST IS EXPECTED TO FAIL WHEN S1.1 LANDS. ***
    The byte-offset cursor should drop `quiet` to near zero. When it does,
    invert this assertion (quiet < journal * 0.1) rather than deleting it —
    the pin is what proves the fix did something.
    """
    quiet = report["centres"]["quiet_heartbeat"]["bytes"]
    journal = report["shape"]["journal_bytes"]
    assert journal > 0
    assert quiet >= journal * 0.5, (
        "if this fails, S1.1 may have landed - invert the assertion, do not "
        "delete it")
    assert report["verdict"]["quiet_is_prefix_proportional"] is True
    assert report["verdict"]["first_fix"] == "events_cursor"


def test_shared_projection_keeps_drawers_free(report):
    """The optimization that ALREADY exists, guarded: the page computes
    state/labels/usage once and hands them down, so rendering every drawer
    reads no files at all. S1's lazy-detail work must not regress this —
    a per-drawer fetch that rebuilds the projection would reintroduce the
    `drawers_unshared` cost one request at a time.
    """
    shared = report["centres"]["drawers_shared"]
    unshared = report["centres"]["drawers_unshared"]
    assert shared["bytes"] == 0, "drawers must read nothing when handed a projection"
    assert unshared["bytes"] > shared["bytes"], "the guard rail is the contrast"


def test_full_render_is_dominated_by_usage_not_drawers(report):
    """Where the render's bytes actually go — the fact that decides what S1
    caches first INSIDE the render (the immutable attempt-log memo), as
    distinct from the heartbeat fix."""
    centres = report["centres"]
    assert centres["usage_only"]["bytes"] > centres["rail_only"]["bytes"]
    assert centres["usage_only"]["bytes"] > centres["drawers_shared"]["bytes"]


# ------------------------------------------------------- the growth claim


def test_cost_grows_with_retained_history(tmp_path):
    """The downstream claim under review — "slows down as history
    accumulates" — measured rather than believed. A 4x bigger fixture must
    cost materially more on the two centres that scale.

    *** The quiet-heartbeat assertion here is ALSO expected to flip with
    S1.1: a cursor-based heartbeat should stay flat as the journal grows. ***
    """
    small = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "a", runs=2, nodes=2, events=200,
                                 attempts=1, log_kb=2), tmp_path)
    big = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "b", runs=8, nodes=2, events=800,
                                 attempts=1, log_kb=2), tmp_path)
    assert (big["centres"]["quiet_heartbeat"]["bytes"]
            > small["centres"]["quiet_heartbeat"]["bytes"] * 2)
    assert (big["centres"]["rail_only"]["stats"]
            > small["centres"]["rail_only"]["stats"])


# ------------------------------------------------------- the report is sendable


def test_json_report_carries_no_identifiers(report):
    """The report is meant to be sent to a maintainer from a work machine
    under the same sanitization the feature request itself observed: counts,
    sizes and timings only. A run name or node id leaking into it would make
    it unsendable, and nobody would notice until it had been sent."""
    import json

    blob = json.dumps(report)
    assert "bench-flow" not in blob, "run names must not reach the report"
    assert "n0" not in blob and "n1" not in blob, "node ids must not reach the report"
    for key in ("shape", "centres", "verdict"):
        assert key in report
    for centre in report["centres"].values():
        assert set(centre) == {"bytes", "files", "stats", "ms"}
