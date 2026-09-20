"""What a MISSION render actually costs — per cost centre, in BYTES.

Phase 0 of the MISSION-scale work (upstream-response-mission-scale.md). Five
plausible cost centres have been named across two independent reports, and
optimizing the wrong one is placebo work that still costs a review. This
measures them instead.

    python contrib\\mission_bench.py                        # profile ./runs in place
    python contrib\\mission_bench.py --runs-root <dir>      # profile another runs dir
    python contrib\\mission_bench.py --json                 # machine-readable
    python contrib\\mission_bench.py --synthetic            # generate + measure a
                                                            # retained-history fixture

BYTES FIRST, wall time second. Wall time on a Windows machine with AV in the
path is not comparable across machines and barely comparable across runs on
one; bytes read and files opened are deterministic, and they are what the
proposed caches actually remove. A fix that halves the wall clock without
moving the byte count moved nothing but the weather.

Read-only and token-free: it renders pages into memory and throws them away.
It writes only under `--synthetic`, and only inside a temp dir it creates.

The `--json` report is safe to send to a maintainer under ordinary
sanitization rules: it carries counts, sizes and timings only — no run
names, no node ids, no paths beyond the runs root you passed, no prompts, no
findings, no file contents.
"""

from __future__ import annotations

import argparse
import builtins
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONTRIB = Path(__file__).resolve().parent
if str(CONTRIB) not in sys.path:
    sys.path.insert(0, str(CONTRIB))

import mission_server as ms  # noqa: E402


# --------------------------------------------------------------- counting

class Counters:
    """Bytes read, files opened, stats taken — during one measured block."""

    def __init__(self) -> None:
        self.bytes = 0
        self.files = 0
        self.stats = 0

    def as_dict(self, seconds: float) -> dict:
        return {"bytes": self.bytes, "files": self.files, "stats": self.stats,
                "ms": round(seconds * 1000, 1)}


class _CountingFile:
    """A file proxy that counts what is actually READ.

    The first cut credited `os.fstat(...).st_size` at open time, which made
    every partial read look like a full-file read - and would have reported a
    byte-offset cursor as no improvement at all. A benchmark that cannot see
    the fix it exists to measure is worse than no benchmark; this is why the
    counters have their own tests.
    """

    def __init__(self, fh, counters):
        self._fh = fh
        self._c = counters

    def read(self, *a, **kw):
        data = self._fh.read(*a, **kw)
        self._c.bytes += (len(data) if isinstance(data, (bytes, bytearray))
                          else len(data.encode("utf-8", "replace")))
        return data

    def readline(self, *a, **kw):
        data = self._fh.readline(*a, **kw)
        self._c.bytes += (len(data) if isinstance(data, (bytes, bytearray))
                          else len(data.encode("utf-8", "replace")))
        return data

    def __iter__(self):
        for line in self._fh:
            self._c.bytes += (len(line) if isinstance(line, (bytes, bytearray))
                              else len(line.encode("utf-8", "replace")))
            yield line

    def __enter__(self):
        self._fh.__enter__()
        return self

    def __exit__(self, *a):
        return self._fh.__exit__(*a)

    def __getattr__(self, name):
        return getattr(self._fh, name)


@contextmanager
def counting():
    """Count real I/O by wrapping the primitives the cockpit actually uses.

    Wrapping is crude and deliberate: the alternative is trusting a reading of
    the code about which paths touch disk, which is exactly the assumption
    Phase 0 exists to replace. `Path.read_text` carries most of it,
    `builtins.open` catches the rest, and `Path.stat`/`iterdir` catch the run
    rail, whose cost is directory traversal rather than content.
    """
    c = Counters()
    p_read_text = pathlib.Path.read_text
    p_read_bytes = pathlib.Path.read_bytes
    p_stat = pathlib.Path.stat
    p_iterdir = pathlib.Path.iterdir
    real_open = builtins.open

    def read_text(self, *a, **kw):
        out = p_read_text(self, *a, **kw)
        c.bytes += len(out.encode("utf-8", "replace"))
        c.files += 1
        return out

    def read_bytes(self, *a, **kw):
        out = p_read_bytes(self, *a, **kw)
        c.bytes += len(out)
        c.files += 1
        return out

    def stat(self, *a, **kw):
        c.stats += 1
        return p_stat(self, *a, **kw)

    def iterdir(self):
        for entry in p_iterdir(self):
            c.stats += 1
            yield entry

    def open_(file, mode="r", *a, **kw):
        fh = real_open(file, mode, *a, **kw)
        if "r" in mode and "w" not in mode and "a" not in mode:
            c.files += 1
            return _CountingFile(fh, c)
        return fh

    pathlib.Path.read_text = read_text        # type: ignore[method-assign]
    pathlib.Path.read_bytes = read_bytes      # type: ignore[method-assign]
    pathlib.Path.stat = stat                  # type: ignore[method-assign]
    pathlib.Path.iterdir = iterdir            # type: ignore[method-assign]
    builtins.open = open_                     # type: ignore[assignment]
    try:
        yield c
    finally:
        pathlib.Path.read_text = p_read_text          # type: ignore[method-assign]
        pathlib.Path.read_bytes = p_read_bytes        # type: ignore[method-assign]
        pathlib.Path.stat = p_stat                    # type: ignore[method-assign]
        pathlib.Path.iterdir = p_iterdir              # type: ignore[method-assign]
        builtins.open = real_open                     # type: ignore[assignment]


def measure(fn) -> tuple[dict, object]:
    """(counter dict, return value) for one call."""
    with counting() as c:
        t0 = time.perf_counter()
        out = fn()
        seconds = time.perf_counter() - t0
    return c.as_dict(seconds), out


# --------------------------------------------------------------- the cost centres

def clear_caches() -> None:
    """Drop every process-local cache MISSION keeps.

    Measurement hygiene, and load-bearing: once S1.3/S1.4 landed, the cost
    centres stopped being independent - measuring `usage` after a render had
    already warmed the log memo reported a WARM number under a cold name.
    That is the placebo-measurement failure this whole tool exists to
    prevent, so cold and warm are now stated separately and explicitly.
    """
    missed = []
    try:
        import cost_report

        with cost_report._LOG_MEMO_LOCK:
            cost_report._LOG_MEMO.clear()
    except Exception as e:  # noqa: BLE001
        missed.append(f"cost_report log memo ({e})")
    try:
        with ms._RAIL_LOCK:
            ms._RAIL_MEMBERS.clear()
            ms._RAIL_ROWS.clear()
            ms._TRACE_MEMO.clear()
    except Exception as e:  # noqa: BLE001
        missed.append(f"mission_server rail cache ({e})")
    if missed:
        # LOUD, not swallowed. A silent no-op here prints warm numbers under
        # cold names - the exact placebo measurement this pairing exists to
        # prevent, and invisible to the maintainer reading the output.
        raise RuntimeError(
            "mission_bench cannot clear: " + "; ".join(missed)
            + " - a cache was renamed or removed, so cold/warm numbers would "
              "be a lie. Update clear_caches().")


def profile(runs_root: Path, repo_root: Path) -> dict:
    """Every cost centre a MISSION tick can pay, COLD and WARM.

    The headline is `quiet_heartbeat`: what `/api/events` costs when NOTHING
    has changed. That is the number that fires once a second forever.

    Everything else is reported as a cold/warm pair, because after S1.3/S1.4
    the page pays a cold price once and a warm price on every tick after -
    and "sluggish after weeks of use" is a claim about the warm number.
    """
    run_dir = ms.resolve_run(runs_root, None, None)
    if run_dir is None:
        raise SystemExit(f"no run directories under {runs_root} - nothing to measure")

    events_path = run_dir / "events.jsonl"
    journal_lines = 0
    if events_path.exists():
        journal_lines = sum(1 for _ in events_path.read_text(
            encoding="utf-8", errors="replace").splitlines())

    out: dict = {"shape": run_shape(runs_root, run_dir, journal_lines), "centres": {}}
    centres = out["centres"]

    def cold(name: str, fn) -> None:
        clear_caches()
        centres[name], _ = measure(fn)

    def cold_then_warm(name: str, fn) -> None:
        clear_caches()
        centres[name + "_cold"], _ = measure(fn)
        centres[name + "_warm"], _ = measure(fn)

    import mission_cursor

    # The heartbeat holds no cache: its cost is the cursor's, cold or warm.
    at_end = mission_cursor.parse_cursor(mission_cursor.initial(run_dir))
    centres["quiet_heartbeat"], _ = measure(lambda: ms._events_after(run_dir, at_end))
    # Computed OUTSIDE the measured block: the helper reads the file to find
    # the line boundary, and counting that would report the harness's cost as
    # the route's.
    one_back = _cursor_one_line_back(run_dir, at_end)
    centres["heartbeat_one_new"], _ = measure(lambda: ms._events_after(run_dir, one_back))
    centres["heartbeat_cold"], _ = measure(
        lambda: ms._events_after(run_dir, mission_cursor.START))

    cold_then_warm("full_render",
                   lambda: ms.render_wrap(run_dir, repo_root, runs_root))
    cold_then_warm("first_paint",
                   lambda: ms.render_page(run_dir, repo_root, runs_root))
    cold_then_warm("rail", lambda: ms.run_list(runs_root, run_dir))
    cold_then_warm("usage", lambda: ms._collect(run_dir))
    cold("intervals", lambda: ms._intervals(run_dir))

    clear_caches()
    state = ms.mv.read_json(run_dir / "state.json") or {}
    labels = ms.mv.load_labels(run_dir, repo_root)
    usage = ms._collect(run_dir)
    node_ids = list((state.get("nodes") or {}).keys())
    # As the PAGE calls it: state, labels and usage are computed once per
    # render and handed down. Measuring the standalone call instead would
    # report a cost production does not pay.
    centres["drawers_shared"], _ = measure(
        lambda: ms._drawers(run_dir, node_ids, repo_root,
                            state=state, labels=labels, usage=usage))
    # The same call WITHOUT the shared projection - not a cost the page pays
    # today, but the one S1.5's lazy detail would reintroduce per drawer if
    # built without passing a projection through. Kept as the guard rail:
    # the panel's per-click fetch is ONE node, and it must not quietly
    # become N. (Post-Batch-2, `drawers_shared`/`drawers_unshared` measure
    # the INLINE stack, whose settled drawers degrade over
    # ms.DRAWER_INLINE_MAX - the threshold's byte win shows up in
    # full_render on an over-threshold fixture.)
    clear_caches()
    centres["drawers_unshared"], _ = measure(
        lambda: ms._drawers(run_dir, node_ids, repo_root))

    # Batch 2: what one panel click costs, cold and warm. This is the request
    # the page makes when a reader opens a step - and re-makes after each
    # swap while the panel is open, so the warm number is the recurring one.
    if node_ids:
        first = node_ids[0]
        clear_caches()
        centres["node_fetch_cold"], _ = measure(
            lambda: ms.handle(f"/api/node/{first}", runs_root, run_dir, repo_root))
        centres["node_fetch_warm"], _ = measure(
            lambda: ms.handle(f"/api/node/{first}", runs_root, run_dir, repo_root))

    out["verdict"] = verdict(centres, out["shape"])
    return out


def _cursor_one_line_back(run_dir: Path, at_end) -> tuple:
    """A cursor just before the journal's final complete line, so the "one new
    event" tick reads exactly one line."""
    import mission_cursor

    gen, offset, ordinal = at_end
    data = (Path(run_dir) / "events.jsonl").read_bytes()[:offset]
    nl = data.rstrip(b"\n").rfind(b"\n")
    if nl < 0:
        return mission_cursor.START
    return (gen, nl + 1, max(0, ordinal - 1))


def run_shape(runs_root: Path, run_dir: Path, journal_lines: int) -> dict:
    """Sizes only — no names. What the numbers above have to be read against."""
    def tree_bytes(root: Path, pattern: str) -> tuple[int, int]:
        total = files = 0
        for p in root.rglob(pattern):
            try:
                if p.is_file():
                    total += p.stat().st_size
                    files += 1
            except OSError:
                continue
        return total, files

    log_bytes, log_files = tree_bytes(run_dir / "phases", "stdout*.log")
    state = ms.mv.read_json(run_dir / "state.json") or {}
    nodes = state.get("nodes") or {}
    try:
        run_dirs = sum(1 for d in runs_root.iterdir()
                       if d.is_dir() and (d / "state.json").is_file())
    except OSError:
        run_dirs = 0
    events_path = run_dir / "events.jsonl"
    return {
        "run_dirs_total": run_dirs,
        "journal_lines": journal_lines,
        "journal_bytes": events_path.stat().st_size if events_path.exists() else 0,
        "nodes": len(nodes),
        "attempts_total": sum(int(r.get("attempts") or 0) for r in nodes.values()),
        "stdout_logs": log_files,
        "stdout_log_bytes": log_bytes,
    }


def verdict(centres: dict, shape: dict) -> dict:
    """Which cost centre to fix first, argued from the measurement.

    Deliberately mechanical: it names the biggest reader and says whether the
    quiet heartbeat is proportional to the journal, because those are the two
    questions the S1 design hangs on. It does not rank anything it cannot
    measure.
    """
    # Rank WARM costs only: the cold pass happens once per process, the warm
    # one happens forever. `drawers_unshared` is a guard rail, not a cost the
    # page pays, and ranking it would aim the next fix at a problem nobody has.
    heavy = max(
        (k for k in centres
         if k.endswith("_warm") and not k.startswith("drawers")),
        key=lambda k: centres[k]["bytes"], default="")
    quiet = centres["quiet_heartbeat"]["bytes"]
    journal = shape["journal_bytes"]
    # "Proportional" = the quiet tick reads most of the journal, which is what
    # a line-count cursor did. S1.1 made this false by construction; the check
    # stays because a regression here would be silent and expensive.
    proportional = journal > 0 and quiet >= journal * 0.5
    notes = []
    if proportional:
        notes.append(
            f"REGRESSION: the quiet heartbeat reads {quiet:,} bytes against a "
            f"{journal:,}-byte journal - it is paying for the whole prefix once "
            "a second. The byte cursor (S1.1) is not being honoured.")
    else:
        notes.append(
            f"the quiet heartbeat reads {quiet:,} bytes against a {journal:,}-byte "
            f"journal ({(100.0 * quiet / journal) if journal else 0:.1f}%): the "
            "byte cursor is holding.")
    if heavy:
        notes.append(f"heaviest WARM centre: {heavy} at "
                     f"{centres[heavy]['bytes']:,} bytes / "
                     f"{centres[heavy]['files']:,} files.")
    for base in ("full_render", "rail", "usage"):
        c, w = centres.get(base + "_cold"), centres.get(base + "_warm")
        if c and w and c["bytes"]:
            notes.append(f"{base}: {c['bytes']:,} B cold -> {w['bytes']:,} B warm "
                         f"({100.0 * w['bytes'] / c['bytes']:.1f}%).")
    return {"first_fix": "events_cursor" if proportional else heavy,
            "quiet_is_prefix_proportional": proportional, "notes": notes}


# --------------------------------------------------------------- synthetic fixture

def synthesize(dest: Path, *, runs: int, nodes: int, events: int, attempts: int,
               log_kb: int) -> Path:
    """A retained-history fixture: many runs, a long journal, rotated logs.

    Exists so the cost curve can be seen WITHOUT waiting weeks for a real one,
    and so a regression test has a fixture whose shape it controls. The newest
    run is the one the page opens.
    """
    dest.mkdir(parents=True, exist_ok=True)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)  # + r hours per run
    node_ids = [f"n{i}" for i in range(nodes)]
    for r in range(runs):
        # Stamped like `state.new_run_dir` writes them. The first cut named
        # these `bench-flow-0000`, which matches no stamp - so every rail test
        # fell into the legacy branch and would have passed with the
        # stamp-parsing path deleted.
        stamp = (t0 + timedelta(hours=r)).strftime("%Y%m%dT%H%M%SZ")
        run = dest / f"bench-flow-{stamp}"
        (run / "phases").mkdir(parents=True, exist_ok=True)
        start = t0 + timedelta(hours=r)
        state = {
            "schema_version": "1.0", "flow_name": "bench-flow", "flow_hash": "b" * 12,
            "format_version": "1.0", "args": {}, "token_spawns": nodes,
            "started_at": start.isoformat(), "workspace_kind": "git",
            "nodes": {
                n: {"node_id": n, "role": "work", "kind": "harness", "status": "done",
                    "attempts": attempts,
                    "started_at": (start + timedelta(minutes=i)).isoformat(),
                    "ended_at": (start + timedelta(minutes=i + 1)).isoformat()}
                for i, n in enumerate(node_ids)
            },
        }
        (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (run / "flow.tg.json").write_text(json.dumps({
            "name": "bench-flow", "format_version": "1.0",
            "nodes": [{"id": n, "role": "work", "kind": "harness",
                       "depends_on": ([node_ids[i - 1]] if i else []),
                       "spec": {"task": "t"}} for i, n in enumerate(node_ids)],
        }), encoding="utf-8")
        lines = []
        for i in range(events):
            n = node_ids[i % nodes]
            ts = (start + timedelta(seconds=i * 7)).isoformat()
            status = "running" if i % 2 == 0 else "done"
            lines.append(json.dumps({"ts": ts, "kind": "transition",
                                     "node": n, "status": status}))
        (run / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        envelope = json.dumps({
            "type": "result", "usage": {"input_tokens": 1000, "output_tokens": 50},
            "total_cost_usd": 0.02, "num_turns": 3, "result": "OK",
        })
        filler = "chatter line, the kind a harness prints before its envelope\n"
        body = filler * max(1, (log_kb * 1024) // len(filler))
        for n in node_ids:
            phase = run / "phases" / n
            phase.mkdir(parents=True, exist_ok=True)
            (phase / "argv.json").write_text(json.dumps(["claude", "-p"]),
                                             encoding="utf-8")
            (phase / "stdout.log").write_text(body + envelope, encoding="utf-8")
            for a in range(1, attempts):
                (phase / f"stdout-attempt{a}.log").write_text(body + envelope,
                                                              encoding="utf-8")
    return dest


# --------------------------------------------------------------- reporting

def human(report: dict) -> str:
    s = report["shape"]
    out = [
        "MISSION render cost - bytes first, wall time second",
        "",
        f"  fixture: {s['run_dirs_total']} run dir(s) retained | open run: "
        f"{s['nodes']} nodes, {s['attempts_total']} attempts, "
        f"{s['stdout_logs']} stdout log(s) ({s['stdout_log_bytes']:,} bytes)",
        f"  journal: {s['journal_lines']:,} lines ({s['journal_bytes']:,} bytes)",
        "",
        f"  {'cost centre':<22}{'bytes read':>14}{'files':>8}{'stats':>8}{'ms':>9}",
    ]
    for name, c in report["centres"].items():
        out.append(f"  {name:<22}{c['bytes']:>14,}{c['files']:>8,}"
                   f"{c['stats']:>8,}{c['ms']:>9.1f}")
    out += ["", "  verdict"]
    for note in report["verdict"]["notes"]:
        out.append(f"    - {note}")
    out += ["",
            "  ms is indicative only: AV and page cache move it between runs on one",
            "  machine. Compare the byte and file columns - those are what a cache",
            "  removes, and they are the same on every machine."]
    return "\n".join(out)


def sweep(sizes: list[tuple[int, int]], *, nodes: int, attempts: int,
          log_kb: int, axis: str = "breadth") -> dict:
    """Measure the same centres across GROWING fixtures.

    One profile says what a render costs; a sweep says what it costs NEXT
    month, which is the actual claim under review ("slows down as history
    accumulates"). Each row is a fresh fixture, built and destroyed.

    TWO axes, because a run grows in two directions and they cost different
    things:

    - `breadth` - more runs retained, longer journals. This is what the run
      rail and the event cursor scale against.
    - `depth` - one run RESUMED and RETRIED for weeks: more attempts per
      node, bigger harness logs. This is what the usage walk scales against,
      and the first cut of this sweep did not vary it at all - so the usage
      curve was reported as x1.4 when the fixture simply held the thing
      usage depends on constant. A benchmark that measures the wrong axis
      reports a fixture's shape as a finding.
    """
    rows = []
    for runs, events in sizes:
        tmp = Path(tempfile.mkdtemp(prefix="lockstep-sweep-"))
        try:
            if axis == "depth":
                # `runs` and `events` carry the depth pair: attempts per node
                # and KB per log. History stays small so the growth that shows
                # up is the RUN's, not the archive's.
                n_att, kb = runs, events
                root = synthesize(tmp / "runs", runs=2, nodes=nodes, events=200,
                                  attempts=n_att, log_kb=kb)
            else:
                n_att, kb = attempts, log_kb
                root = synthesize(tmp / "runs", runs=runs, nodes=nodes, events=events,
                                  attempts=attempts, log_kb=log_kb)
            rep = profile(root, Path("."))
            rows.append({
                "runs": runs, "events": events,
                "attempts": n_att, "log_kb": kb,
                "log_bytes": rep["shape"]["stdout_log_bytes"],
                "journal_bytes": rep["shape"]["journal_bytes"],
                "quiet_bytes": rep["centres"]["quiet_heartbeat"]["bytes"],
                "rail_stats": rep["centres"]["rail_warm"]["stats"],
                "render_bytes": rep["centres"]["full_render_warm"]["bytes"],
                "usage_bytes": rep["centres"]["usage_warm"]["bytes"],
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"sweep": rows}


def human_sweep(report: dict) -> str:
    depth = report.get("axis") == "depth"
    head = ("MISSION cost vs RUN DEPTH - attempts and log size" if depth
            else "MISSION cost vs RETAINED HISTORY - runs and journal length")
    first_cols = (f"  {'attempts':>9}{'log KB':>8}{'log B':>12}" if depth
                  else f"  {'runs':>6}{'events':>9}{'journal B':>12}")
    out = [head, "",
           first_cols + f"{'quiet B':>12}{'rail stats':>12}"
           f"{'render B':>12}{'usage B':>12}"]
    for r in report["sweep"]:
        lead = (f"  {r['attempts']:>9,}{r['log_kb']:>8,}{r['log_bytes']:>12,}"
                if depth else
                f"  {r['runs']:>6,}{r['events']:>9,}{r['journal_bytes']:>12,}")
        out.append(lead + f"{r['quiet_bytes']:>12,}{r['rail_stats']:>12,}"
                   f"{r['render_bytes']:>12,}{r['usage_bytes']:>12,}")
    first, last = report["sweep"][0], report["sweep"][-1]
    out += ["", "  growth across the sweep"]
    for label, key in (("quiet heartbeat", "quiet_bytes"),
                       ("run rail stats", "rail_stats"),
                       ("full render", "render_bytes")):
        a, b = first[key] or 1, last[key]
        out.append(f"    {label:<18} x{b / a:>6.1f}")
    out += ["",
            "  A cache that works flattens its column. Anything still rising",
            "  here after S1 lands is a cost centre S1 did not actually fix."]
    return chr(10).join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default=None,
                    help="runs directory to profile (default: [driver] runs_dir in "
                         "<repo>/lockstep.toml, else <repo>/runs)")
    ap.add_argument("--repo-root", default=".", help="repo root (for step labels)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--synthetic", action="store_true",
                    help="generate a retained-history fixture in a temp dir and "
                         "measure that instead (removed afterwards)")
    ap.add_argument("--runs", type=int, default=60, help="--synthetic: run dirs")
    ap.add_argument("--nodes", type=int, default=8, help="--synthetic: nodes per run")
    ap.add_argument("--events", type=int, default=4000,
                    help="--synthetic: journal lines in the newest run")
    ap.add_argument("--attempts", type=int, default=3,
                    help="--synthetic: attempts per node (rotated logs)")
    ap.add_argument("--log-kb", type=int, default=64,
                    help="--synthetic: KB per stdout log")
    ap.add_argument("--axis", choices=("breadth", "depth"), default="breadth",
                    help="--sweep axis: `breadth` grows retained history, "
                         "`depth` grows one run's attempts and log size")
    ap.add_argument("--sweep", action="store_true",
                    help="measure across growing synthetic fixtures and report "
                         "the growth factor per cost centre")
    ns = ap.parse_args(argv)

    if ns.sweep:
        if ns.axis == "depth":
            # (attempts, log_kb) pairs: one run resumed and retried for weeks.
            report = sweep([(1, 16), (4, 64), (16, 256)], nodes=ns.nodes,
                           attempts=ns.attempts, log_kb=ns.log_kb, axis="depth")
        else:
            report = sweep([(10, 500), (40, 2000), (160, 8000)], nodes=ns.nodes,
                           attempts=ns.attempts, log_kb=ns.log_kb)
        report["axis"] = ns.axis
        print(json.dumps(report, indent=2) if ns.json else human_sweep(report))
        return 0

    tmp: Path | None = None
    try:
        if ns.synthetic:
            tmp = Path(tempfile.mkdtemp(prefix="lockstep-bench-"))
            runs_root = synthesize(tmp / "runs", runs=ns.runs, nodes=ns.nodes,
                                   events=ns.events, attempts=ns.attempts,
                                   log_kb=ns.log_kb)
            if not ns.json:
                print(f"synthetic fixture: {ns.runs} runs x {ns.nodes} nodes, "
                      f"{ns.events} journal lines, {ns.attempts} attempts, "
                      f"{ns.log_kb}KB logs\n")
        else:
            runs_root = mv.default_runs_root(Path(ns.repo_root), ns.runs_root)
            if not runs_root.is_dir():
                print(f"no such runs directory: {runs_root}", file=sys.stderr)
                return 2
        report = profile(runs_root, Path(ns.repo_root))
        print(json.dumps(report, indent=2) if ns.json else human(report))
        return 0
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
