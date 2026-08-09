#!/usr/bin/env python
"""torture_suite.py — drive the engine's failure paths on purpose.

    python contrib/torture_suite.py                 # all scenarios
    python contrib/torture_suite.py --only heal     # substring filter
    python contrib/torture_suite.py --keep          # leave the temp repos for inspection

`contrib/replay_suite.py` proves recorded runs still replay. This proves the
paths a recording never takes: healing, rollback, cascade invalidation, the
corrective re-spawn, write-scope quarantine, timeouts. Those only happen when
something goes wrong, so something is made to go wrong — `contrib/demo/
scripted_agent.py` is a harness stanza whose behaviour is a script rather than
a model. Zero tokens, seconds, repeatable.

Each scenario runs in its own throwaway git repo with its own generated
`lockstep.toml`, so the suite never touches the working tree it is launched
from and cannot be perturbed by whatever is in it.

An expected exit code alone would be a weak check — a flow can exit 2 for the
wrong reason. Every scenario also asserts on the run directory: how many times
a node was actually invoked, which events were journalled, which artifacts the
engine left behind.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FLOWS = REPO / "flows" / "demo" / "torture"


# --------------------------------------------------------------- run-dir readers

def invocations(run_dir: Path, node: str) -> list[dict]:
    """Every recorded invocation of a scripted node, in order.

    The counter file is not one of the names the executor rotates per attempt,
    and the run dir is excluded from heal rollback — so it survives both, which
    is the only reason "this node ran three times" is checkable after the fact.
    """
    p = run_dir / "phases" / node / "scripted-invocations.jsonl"
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def events(run_dir: Path) -> list[dict]:
    p = run_dir / "events.jsonl"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def node_record(run_dir: Path, node: str) -> dict:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    return state.get("nodes", {}).get(node, {})


# --------------------------------------------------------------- the assertions

def check_heal(run_dir: Path, repo: Path) -> list[str]:
    bad = []
    build = invocations(run_dir, "build")
    if len(build) != 3:
        bad.append(f"build ran {len(build)}x, expected 3 (initial + 2 heal rounds)")
    # The point of the whole exercise: findings must reach the target.
    if build[1:] and not all(i["healed"] for i in build[1:]):
        bad.append("a re-spawn of 'build' did not carry the gate's findings")
    sib = invocations(run_dir, "sibling")
    if len(sib) != 3:
        bad.append(f"sibling ran {len(sib)}x, expected 3 — cascade invalidation did not "
                   f"re-run a completed descendant of the heal target")
    if any(i["healed"] for i in sib):
        bad.append("'sibling' got heal text; only heal TARGETS should")
    rounds = [e for e in events(run_dir) if e.get("status") == "heal-round"]
    if len(rounds) != 2:
        bad.append(f"{len(rounds)} heal-round events journalled, expected 2")
    art = repo / "torture" / "app.txt"
    if not art.is_file() or "GOOD" not in art.read_text(encoding="utf-8"):
        bad.append("the healed artifact is not GOOD at the end of a passing run")

    # SPEC §9.4.4: the blocked attempt is preserved as a patch BEFORE the
    # restore, and files created since the baseline are MOVED aside, never
    # `rm`'d. Both are promises about not destroying paid work, and neither
    # shows up in an exit code.
    gate_dir = run_dir / "phases" / "check"
    patches = sorted(gate_dir.glob("attempt-*.patch"))
    if len(patches) != 2:
        bad.append(f"{len(patches)} preserved attempt patch(es), expected 2")
    discarded = sorted(gate_dir.glob("discarded-*"))
    if not discarded:
        bad.append("no discarded-<round>/ directory: created files were deleted, not moved")
    else:
        salvaged = [q for d in discarded for q in d.rglob("*") if q.is_file()]
        if not any("BAD" in q.read_text(encoding="utf-8", errors="replace") for q in salvaged):
            bad.append("the rolled-back BAD artifact was not preserved anywhere")
    return bad


def check_heal_exhausted(run_dir: Path, repo: Path) -> list[str]:
    bad = []
    build = invocations(run_dir, "build")
    if len(build) != 2:
        bad.append(f"build ran {len(build)}x, expected 2 (initial + 1 allowed round)")
    rec = node_record(run_dir, "check")
    # `blocked`, not `done`. SPEC §9.4.7 only says DEPENDENTS are blocked and is
    # silent on the gate's own status; the engine marks the gate too, which is
    # what makes the origin of the stop visible — and `blocked` glosses to
    # "needs you" on MISSION, which is exactly what a terminal block means for
    # the human. Asserted here so nobody "fixes" it to `done`.
    if rec.get("status") != "blocked":
        bad.append(f"gate status is {rec.get('status')!r}, expected 'blocked'")
    if not (rec.get("error") or "").strip():
        bad.append("the terminal block recorded no reason")
    if node_record(run_dir, "final").get("status") not in ("blocked", "pending"):
        bad.append("the node after a terminal block should not have run")
    return bad


def check_contract(run_dir: Path, repo: Path) -> list[str]:
    bad = []
    emit = invocations(run_dir, "emit")
    if len(emit) != 2:
        bad.append(f"emit ran {len(emit)}x, expected 2 (bad JSON, then the corrective re-spawn)")
    if emit[1:] and not emit[1]["corrective"]:
        bad.append("the corrective re-spawn did not carry the validation error")
    if emit[1:] and emit[1]["prompt_chars"] <= emit[0]["prompt_chars"]:
        bad.append("the corrective prompt is not larger than the original — it must embed "
                   "the original task AND the invalid output (headless spawns are stateless)")
    return bad


def check_quarantine(run_dir: Path, repo: Path) -> list[str]:
    bad = []
    rec = node_record(run_dir, "stray")
    if rec.get("status") != "failed":
        bad.append(f"stray status is {rec.get('status')!r}, expected 'failed'")
    if "scope" not in (rec.get("error") or "").lower():
        bad.append(f"the failure does not name the scope violation: {rec.get('error')!r}")
    phase = run_dir / "phases" / "stray"
    kept = list(phase.glob("out-of-scope-*"))
    if not kept:
        bad.append("no out-of-scope artifact preserved in the phase dir")
    escape = repo / "torture" / "escape.txt"
    if escape.is_file():
        bad.append("the out-of-scope file is still in the working tree")
    return bad


def check_timeout(run_dir: Path, repo: Path) -> list[str]:
    bad = []
    rec = node_record(run_dir, "sleeper")
    if rec.get("status") != "failed":
        bad.append(f"sleeper status is {rec.get('status')!r}, expected 'failed'")
    if "timeout" not in (rec.get("error") or "").lower():
        bad.append(f"the failure does not name the timeout: {rec.get('error')!r}")
    return bad


SCENARIOS = [
    # flow name, expected exit, assertion
    ("torture-heal", 0, check_heal),
    ("torture-heal-exhausted", 2, check_heal_exhausted),
    ("torture-contract", 0, check_contract),
    ("torture-quarantine", 3, check_quarantine),
    ("torture-timeout", 3, check_timeout),
]


# --------------------------------------------------------------- the harness

def build_repo(dest: Path) -> None:
    """A throwaway git repo with the scripted harness and a config for it."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "contrib" / "demo").mkdir(parents=True, exist_ok=True)
    for f in ("scripted_agent.py", "torture_gate.py"):
        shutil.copy2(REPO / "contrib" / "demo" / f, dest / "contrib" / "demo" / f)
    (dest / ".gitignore").write_text("runs/\nlockstep.toml\n", encoding="utf-8")
    # sys.executable, not "python": only SHELL nodes resolve a bare interpreter
    # name (DEVIATIONS 2026-08-05); a harness stanza's argv is spawned verbatim.
    (dest / "lockstep.toml").write_text(
        'default = "scripted"\n\n'
        "[executors.scripted]\n"
        f"argv = [{json.dumps(sys.executable)}, \"contrib/demo/scripted_agent.py\"]\n"
        'prompt_via = "stdin"\n'
        'readonly_argv = ["--readonly"]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "torture", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "torture", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True, env=env)


def latest_run(runs: Path) -> Path | None:
    if not runs.is_dir():
        return None
    dirs = [d for d in runs.iterdir() if d.is_dir() and (d / "state.json").is_file()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def run_one(name: str, expected: int, assertion, *, keep: bool) -> tuple[bool, list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix=f"lockstep-torture-{name}-"))
    try:
        build_repo(tmp)
        proc = subprocess.run(
            # `-m lockstep` needs a __main__; the console script may not be on
            # PATH when the suite is run from a checkout. Call main() directly.
            [sys.executable, "-c", "import sys; from lockstep.cli import main; sys.exit(main())",
             "run", str(FLOWS / f"{name}.tg.json"),
             "--repo-root", str(tmp), "--runs-dir", str(tmp / "runs"),
             "--config", str(tmp / "lockstep.toml")],
            capture_output=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONPATH": str(REPO / "src")},
            stdin=subprocess.DEVNULL, timeout=600,
        )
        problems: list[str] = []
        if proc.returncode != expected:
            problems.append(f"exit {proc.returncode}, expected {expected}")
            problems.append("  stderr tail: " + (proc.stderr or "").strip()[-400:])
        run_dir = latest_run(tmp / "runs")
        if run_dir is None:
            problems.append("no run directory was created")
            problems.append("  stdout tail: " + (proc.stdout or "").strip()[-600:])
            problems.append("  stderr tail: " + (proc.stderr or "").strip()[-600:])
        else:
            problems.extend(assertion(run_dir, tmp))
        if problems and keep:
            problems.append(f"  kept: {tmp}")
        return not problems, problems
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default=None, help="substring filter on the scenario name")
    ap.add_argument("--keep", action="store_true", help="leave temp repos behind")
    ns = ap.parse_args(argv)

    picked = [s for s in SCENARIOS if not ns.only or ns.only in s[0]]
    if not picked:
        print(f"torture suite: nothing matches {ns.only!r}", file=sys.stderr)
        return 2

    passed = 0
    for name, expected, assertion in picked:
        ok, problems = run_one(name, expected, assertion, keep=ns.keep)
        if ok:
            passed += 1
            print(f"ok   {name}")
        else:
            print(f"FAIL {name}")
            for p in problems:
                print(f"       {p}")
    print(f"\ntorture suite: {passed}/{len(picked)} passed", file=sys.stderr)
    return 0 if passed == len(picked) else 1


if __name__ == "__main__":
    raise SystemExit(main())
