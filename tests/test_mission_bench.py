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


def test_quiet_heartbeat_does_not_read_the_journal_prefix(report):
    """S1.1, proved by the pin that used to assert the defect.

    This test previously asserted the OPPOSITE - that a heartbeat with
    nothing to report read essentially the whole journal, once a second,
    forever - and carried instructions to invert rather than delete it when
    the byte cursor landed. This is that inversion; the contrast between the
    two revisions is the evidence that the fix did something.
    """
    import mission_cursor

    quiet = report["centres"]["quiet_heartbeat"]["bytes"]
    cold = report["centres"]["heartbeat_cold"]["bytes"]
    journal = report["shape"]["journal_bytes"]
    assert journal > 0
    # The guarantee is a CONSTANT, not a ratio: one small head read to verify
    # the journal's generation, and nothing else. Asserting a fraction of the
    # journal would pass or fail on fixture size rather than on behaviour.
    assert quiet <= mission_cursor._HEAD_BYTES, (
        f"a quiet tick read {quiet} bytes - the byte cursor is not being honoured")
    assert quiet < journal, "a quiet tick must not read the whole journal"
    assert cold > quiet, "a cold cursor must still read the journal it skipped"
    assert report["verdict"]["quiet_is_prefix_proportional"] is False


def test_the_caches_eliminate_the_repeated_reads(report):
    """S1.3/S1.4, in bytes: the rail stops reading entirely on a warm tick,
    and the attempt-log memo removes the bulk of the usage walk. Stated as
    ratios against each centre's own COLD cost, so the assertion survives a
    change of fixture size."""
    c = report["centres"]
    assert c["rail_warm"]["bytes"] == 0, "a warm rail must read nothing"
    assert c["rail_cold"]["bytes"] > 0, "the contrast is the evidence"
    # What the memo removes is exactly the LOG bytes - so assert that, not a
    # ratio. A ratio passes or fails on how much non-log overhead the fixture
    # happens to carry (argv.json and friends dominate a tiny fixture), which
    # would make this test a statement about the fixture rather than the code.
    saved = c["usage_cold"]["bytes"] - c["usage_warm"]["bytes"]
    logs = report["shape"]["stdout_log_bytes"]
    assert logs > 0, "precondition: the fixture has attempt logs to re-read"
    assert saved >= logs * 0.9, (
        f"usage saved {saved} B warm against {logs} B of logs - the attempt-log "
        "memo is not being honoured")
    assert c["full_render_warm"]["bytes"] < c["full_render_cold"]["bytes"]


def test_a_cached_rail_equals_an_uncached_one(tmp_path, fixture_runs):
    """The rule that makes a derived cache legitimate: evicting an entry may
    change timing and NOTHING else. The pre-cache implementation is kept in
    the module as the oracle precisely so this can be asserted rather than
    promised."""
    import mission_server as ms

    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    cached, total = ms.run_list(fixture_runs, None)
    warm, total_warm = ms.run_list(fixture_runs, None)
    oracle = ms._run_list_uncached(fixture_runs, None)
    assert cached == warm, "a warm read must equal the cold one it replaced"
    assert total == total_warm
    assert [r["name"] for r in cached] == [r["name"] for r in oracle[0]]
    for a, b in zip(cached, oracle[0]):
        assert a["word"] == b["word"] and a["cls"] == b["cls"]


def test_the_rail_notices_a_run_finishing(tmp_path, fixture_runs):
    """The trap the downstream review caught: writing INSIDE a run dir does
    not reliably bump the RUNS-ROOT mtime, so a status cached against the
    parent would never see running -> done - the one transition the reader is
    watching for. The status layer keys off the run's own state.json."""
    import json

    import mission_server as ms

    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    target = sorted(p for p in fixture_runs.iterdir() if p.is_dir())[-1]
    state = json.loads((target / "state.json").read_text(encoding="utf-8"))
    first = {r["name"]: r["word"] for r in ms.run_list(fixture_runs, None)[0]}

    for rec in state["nodes"].values():
        rec["status"] = "running"
    (target / "state.json").write_text(json.dumps(state), encoding="utf-8")
    after = {r["name"]: r["word"] for r in ms.run_list(fixture_runs, None)[0]}
    assert after[target.name] != first[target.name], (
        "the rail cached a status against the wrong fingerprint")


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
    assert centres["usage_cold"]["bytes"] > centres["rail_cold"]["bytes"]
    assert centres["usage_cold"]["bytes"] > centres["drawers_shared"]["bytes"]


# ------------------------------------------------------- the growth claim


def test_the_quiet_heartbeat_is_flat_across_journal_size(tmp_path):
    """The inversion of the growth claim, and the sharper statement of S1.1:
    a 4x longer journal must NOT cost a quiet tick more. This is the test
    that catches a future reader reintroducing a whole-file read.

    The rail's growth with retained history was pinned here as still-growing
    until S1.4 landed; that assertion has since been flipped in place, which
    is the same discipline the heartbeat pin followed.
    """
    small = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "a", runs=2, nodes=2, events=200,
                                 attempts=1, log_kb=2), tmp_path)
    big = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "b", runs=8, nodes=2, events=800,
                                 attempts=1, log_kb=2), tmp_path)
    small_quiet = small["centres"]["quiet_heartbeat"]["bytes"]
    big_quiet = big["centres"]["quiet_heartbeat"]["bytes"]
    assert big_quiet <= small_quiet * 1.5, (
        f"quiet tick grew {small_quiet} -> {big_quiet} with journal size")
    # S1.4 has since landed, so the rail pin left here flips too: a warm rail
    # reads nothing whatever the history weighs.
    assert big["centres"]["rail_warm"]["bytes"] == 0
    assert small["centres"]["rail_warm"]["bytes"] == 0


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


def test_the_counter_counts_reads_not_file_sizes(tmp_path):
    """The instrument's own defect, pinned. The first cut credited the whole
    file size at open() time, so a partial read looked like a full read - and
    the byte-offset cursor would have measured as no improvement at all. A
    benchmark that cannot see the fix it exists to measure is worse than no
    benchmark; this is why the counters have tests of their own.
    """
    p = tmp_path / "big.txt"
    p.write_text("y" * 100_000, encoding="utf-8")
    with mission_bench.counting() as c:
        with open(p, "rb") as fh:
            fh.read(500)
    assert c.bytes == 500, f"counted {c.bytes} for a 500-byte read"
