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


def test_the_depth_axis_is_absorbed_by_the_log_memo(tmp_path):
    """The axis the first sweep could not see, and the question upstream said
    would need the reporter's real-world numbers: does a much-RESUMED run (a
    deep one - many attempts, big harness logs) cost a warm render more?

    Measured: no. The attempt-log memo absorbs it, so a run carrying orders
    of magnitude more log bytes renders warm for the same cost. Without the
    memo this column tracks log size directly.
    """
    shallow = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "a", runs=2, nodes=2, events=100,
                                 attempts=1, log_kb=8), tmp_path)
    deep = mission_bench.profile(
        mission_bench.synthesize(tmp_path / "b", runs=2, nodes=2, events=100,
                                 attempts=6, log_kb=48), tmp_path)
    assert (deep["shape"]["stdout_log_bytes"]
            > shallow["shape"]["stdout_log_bytes"] * 10), "precondition: deeper"
    # Cold still pays for what it reads - that is honest and happens once.
    assert deep["centres"]["usage_cold"]["bytes"] > shallow["centres"]["usage_cold"]["bytes"]
    # Warm must not, whatever the run has accumulated.
    assert (deep["centres"]["usage_warm"]["bytes"]
            <= shallow["centres"]["usage_warm"]["bytes"] * 1.5), (
        f"warm usage grew {shallow['centres']['usage_warm']['bytes']} -> "
        f"{deep['centres']['usage_warm']['bytes']} with run depth")


def test_clear_caches_is_loud_when_a_cache_is_renamed(monkeypatch):
    """A silent no-op here prints WARM numbers under COLD names, which is the
    placebo measurement the cold/warm pairing exists to prevent — and it would
    be invisible in the standalone tool a maintainer actually reads."""
    import mission_server as ms

    monkeypatch.delattr(ms, "_RAIL_MEMBERS", raising=True)
    with pytest.raises(RuntimeError, match="renamed or removed"):
        mission_bench.clear_caches()


def test_a_new_run_appears_in_the_rail_immediately(tmp_path):
    """BLOCKER, reproduced: `new_run_dir` mkdirs FIRST and writes state.json
    after. The mkdir bumps the runs-root mtime, the write does not — so a
    membership scan landing in that window cached a list excluding the new run,
    keyed on an mtime that would never change again. The run stayed missing
    from the rail for the life of the server process."""
    import json

    import mission_server as ms

    runs = mission_bench.synthesize(tmp_path / "runs", runs=1, nodes=1,
                                    events=10, attempts=1, log_kb=1)
    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    before = {r["name"] for r in ms.run_list(runs, None)[0]}

    fresh = runs / "later-20991231T235959Z"
    (fresh / "phases").mkdir(parents=True)          # the window opens here
    assert {r["name"] for r in ms.run_list(runs, None)[0]} == before

    (fresh / "state.json").write_text(json.dumps({      # ... and closes here
        "schema_version": "1.0", "flow_name": "later", "flow_hash": "z",
        "format_version": "1.0", "args": {}, "token_spawns": 0,
        "started_at": "2099-12-31T23:59:59+00:00", "nodes": {},
    }), encoding="utf-8")
    after = {r["name"] for r in ms.run_list(runs, None)[0]}
    assert fresh.name in after, "a run started while the page was open stayed invisible"


def test_the_rail_orders_by_creation_stamp_not_slug(tmp_path):
    """The oracle disagreed with the cache on every realistic runs dir — the
    flow slug won over the timestamp, and `-9` beat `-10` lexically. Both
    sides now derive the same key, and this asserts the ORDER itself."""
    import json

    import mission_server as ms

    runs = tmp_path / "runs"
    runs.mkdir()
    for name in ("zeta-20260101T120000Z", "alpha-20260910T120000Z",
                 "beta-20260501T120000Z-9", "beta-20260501T120000Z-10"):
        d = runs / name
        d.mkdir()
        (d / "state.json").write_text(json.dumps({
            "schema_version": "1.0", "flow_name": name.rsplit("-", 1)[0],
            "flow_hash": "z", "format_version": "1.0", "args": {},
            "token_spawns": 0, "started_at": "2026-01-01T00:00:00+00:00",
            "nodes": {},
        }), encoding="utf-8")
    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    cached = [r["name"] for r in ms.run_list(runs, None)[0]]
    oracle = [r["name"] for r in ms._run_list_uncached(runs, None)[0]]
    assert cached == oracle, "the oracle must order the way the cache does"
    assert cached[0] == "alpha-20260910T120000Z", "newest first, slug irrelevant"
    assert cached.index("beta-20260501T120000Z-10") < cached.index(
        "beta-20260501T120000Z-9"), "-10 is newer than -9"


def test_the_rail_cache_actually_hits(tmp_path):
    """Round 2 BLOCKER: the cache key was shadowed by the per-dir sort tuple,
    so every members list was stored under the last directory's stamp and the
    cache could never hit - the rail rescanned all of runs/ on every call,
    10x measured. Every test in this file compared OUTPUT, which was correct
    the whole time; nothing asserted the cache did anything. This does."""
    import mission_server as ms

    runs = mission_bench.synthesize(tmp_path / "runs", runs=6, nodes=1,
                                    events=20, attempts=1, log_kb=1)
    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    ms.run_list(runs, None)
    assert str(runs) in ms._RAIL_MEMBERS, "the members list is under the wrong key"

    with mission_bench.counting() as c:
        ms.run_list(runs, None)
    warm_stats, warm_files = c.stats, c.files
    ms._RAIL_MEMBERS.clear()
    ms._RAIL_ROWS.clear()
    with mission_bench.counting() as c:
        ms.run_list(runs, None)
    cold_stats, cold_files = c.stats, c.files
    # BOTH layers, because they pay in different currencies and a single
    # metric is blind to one of them: _RAIL_MEMBERS saves directory STATS,
    # _RAIL_ROWS saves state.json READS (it stats unconditionally to build its
    # fingerprint, so it contributes no stat saving at all). Asserting stats
    # alone let the entire rows layer die green - the layer that exists
    # because the parent mtime cannot see a running->done transition.
    assert warm_stats < cold_stats, (
        f"warm {warm_stats} stats vs cold {cold_stats} - membership is rescanning")
    assert warm_files == 0 < cold_files, (
        f"warm rail read {warm_files} files - the row cache is dead")
    # Stated against the COLD cost rather than a constant: a fixture-calibrated
    # threshold went red at 12 runs while nothing was rescanning.


def test_the_journal_is_parsed_once_per_render(tmp_path):
    """S1.2: `render_wrap` read `events.jsonl` THREE times — for the feed, for
    `collect_run`'s wall/heal pass, and again inside `_intervals` for the
    timeline. That was 77% of everything a warm render still touched once the
    caches landed, and it grows with the journal forever.

    Asserted as a multiple of the file, not a byte constant, so it survives a
    change of fixture."""
    import mission_server as ms

    runs = mission_bench.synthesize(tmp_path / "runs", runs=2, nodes=4,
                                    events=400, attempts=2, log_kb=8)
    run = ms.resolve_run(runs, None, None)
    journal = (run / "events.jsonl").stat().st_size
    assert journal > 10_000, "precondition: a journal worth not re-reading"

    mission_bench.clear_caches()
    ms.render_wrap(run, tmp_path, runs)          # warm every cache first

    # Count the JOURNAL's bytes specifically. `counting()` totals every read a
    # render makes (state, flow, logs, argv), so asserting against its total
    # would measure the fixture rather than the claim.
    import pathlib

    seen = []
    real = pathlib.Path.read_text

    def watch(self, *a, **k):
        out = real(self, *a, **k)
        if self.name == "events.jsonl":
            seen.append(len(out.encode("utf-8", "replace")))
        return out

    pathlib.Path.read_text = watch
    try:
        ms.render_wrap(run, tmp_path, runs)
    finally:
        pathlib.Path.read_text = real
    # ONE pass. The chain verification is a second, DIFFERENT read of the same
    # file (it re-chains rather than parsing for display) and is memoized on
    # the journal's identity, so a warm render pays for it once or not at all.
    assert len(seen) <= 1, (
        f"a warm render read the journal in {len(seen)} passes "
        f"({sum(seen)} B of {journal} B) - it is parsing it more than once")


def test_the_projection_does_not_change_what_is_rendered(tmp_path):
    """The rule every cache and every shared projection here answers to:
    handing a reader its input may change timing and NOTHING else."""
    import mission_server as ms

    runs = mission_bench.synthesize(tmp_path / "runs", runs=2, nodes=3,
                                    events=120, attempts=2, log_kb=4)
    run = ms.resolve_run(runs, None, None)
    # A fixed `now`: the geometry extends to the present while anything runs,
    # so two calls a millisecond apart legitimately differ and would make this
    # a test of the clock.
    from datetime import datetime, timezone

    fixed = datetime(2026, 6, 1, tzinfo=timezone.utc)
    shared = ms.waterfall(run, tmp_path, now=fixed, events=ms._events(run))
    alone = ms.waterfall(run, tmp_path, now=fixed)
    assert shared == alone, "the threaded projection rendered something else"
