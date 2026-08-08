#!/usr/bin/env python
"""replay_suite.py — zero-token flow regression over recorded fixtures (A3).

    python contrib/replay_suite.py
    python contrib/replay_suite.py --flows flows --fixtures tests/fixtures/replay

For every fixture directory (a scrubbed run dir produced by
contrib/export_fixture.py), find the flow file whose `name` matches the
fixture's recorded flow_name and run it with `--replay` against the fixture in
a throwaway runs dir. No spawns, no tokens — and STRICT input_hash matching is
the point: a mismatch after an engine or flow change is exactly the regression
being hunted, so there is deliberately no --replay-any here.

A fixture that recorded a non-zero outcome (a gate block, say) declares it in
`expected_exit.txt`; the default expectation is 0.

WHY THE FIXTURE DIRECTORY MAY BE EMPTY, AND WHAT THAT COSTS. Strict matching is
the point, and a harness node's input hash includes the LOCAL config digest —
so a fixture recorded from a harness flow replays only on the machine that
recorded it. Only an all-shell flow yields a portable one. With no fixtures the
suite exits 0, because nothing regressed; it says `0/0 — NOTHING WAS CHECKED`
on stderr so that cannot be read as an all-clear, and `--require-fixtures`
turns it into a failure for a caller that wants coverage.

Exit: 0 = every fixture replayed as expected; 1 = any mismatch (or an empty net
under `--require-fixtures`); 2 = usage.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lockstep.cli import main as lockstep_main  # noqa: E402
from lockstep.state import load_state  # noqa: E402
from lockstep.taskgraph import FlowError, load_flow  # noqa: E402


def find_flow(flows_dir: Path, flow_name: str) -> Path | None:
    for path in sorted(Path(flows_dir).rglob("*.tg.json")):
        try:
            tg, _ = load_flow(path)
        except FlowError:
            continue
        if tg.name == flow_name:
            return path
    return None


def run_fixture(flow_path: Path, fixture: Path, repo_root: Path) -> tuple[bool, str]:
    state = load_state(fixture)
    argv = ["run", str(flow_path), "--replay", str(fixture), "--fresh",
            "--repo-root", str(repo_root)]
    for k, v in state.args.items():
        argv += ["--arg", f"{k}={v}"]
    expected = 0
    marker = fixture / "expected_exit.txt"
    if marker.exists():
        try:
            expected = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as e:
            return False, f"bad expected_exit.txt ({e})"
    # ignore_cleanup_errors: this machine's AV holds handles transiently
    # (CLAUDE.md ops note); a leaked temp dir must not fail the suite.
    with tempfile.TemporaryDirectory(prefix="lockstep-replay-suite-",
                                     ignore_cleanup_errors=True) as td:
        try:
            code = lockstep_main(argv + ["--runs-dir", td])
        except SystemExit as e:  # the CLI parser exits 7 on usage errors
            code = int(e.code or 0)
    return code == expected, f"exit {code} (expected {expected})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flows", default="flows", help="directory searched recursively for *.tg.json")
    ap.add_argument("--fixtures", default="tests/fixtures/replay",
                    help="directory of scrubbed run dirs (export_fixture.py output)")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--require-fixtures", action="store_true",
                    help="fail when there are none, instead of reporting an empty net")
    ns = ap.parse_args(argv)
    fixtures_dir = Path(ns.fixtures)
    fixtures = (sorted(p for p in fixtures_dir.iterdir() if (p / "state.json").exists())
                if fixtures_dir.is_dir() else [])
    if not fixtures:
        # An empty net exits 0 — nothing regressed, because nothing was
        # checked. That distinction has to be LOUD, or this becomes the
        # exit-0 placeholder the SSSF review rejected as "the first thing that
        # will lie to you". It goes to stderr, says zero out of zero, and
        # `--require-fixtures` is there for a caller that wants coverage
        # rather than an all-clear.
        where = fixtures_dir if fixtures_dir.is_dir() else f"{fixtures_dir} (no such directory)"
        print(f"replay suite: 0/0 — NOTHING WAS CHECKED. No fixtures under {where}.",
              file=sys.stderr)
        print("  Record one with: python contrib/export_fixture.py <run_dir> "
              f"{fixtures_dir}/<name>", file=sys.stderr)
        print("  Note that only an ALL-SHELL flow gives a portable fixture: a "
              "harness node's", file=sys.stderr)
        print("  input hash includes the local config digest, so its recorded "
              "hashes will not", file=sys.stderr)
        print("  match on another machine and strict replay will fail there.",
              file=sys.stderr)
        return 1 if ns.require_fixtures else 0
    failures = 0
    for fixture in fixtures:
        try:
            flow_name = load_state(fixture).flow_name
        except (OSError, ValueError) as e:
            print(f"FAIL {fixture.name}: unreadable fixture state ({e})")
            failures += 1
            continue
        flow_path = find_flow(Path(ns.flows), flow_name)
        if flow_path is None:
            print(f"FAIL {fixture.name}: no flow named {flow_name!r} under {ns.flows}")
            failures += 1
            continue
        ok, detail = run_fixture(flow_path, fixture, Path(ns.repo_root))
        print(f"{'ok  ' if ok else 'FAIL'} {fixture.name}: {flow_path.name} {detail}")
        failures += 0 if ok else 1
    print(f"replay suite: {len(fixtures) - failures}/{len(fixtures)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
