"""Lint + test suite -> Verdict (replaces the inline `checks` gates).

Usage from a flow:
    ["python", "-m", "lockstep.gates.pytest_verdict"]
    ["python", "-m", "lockstep.gates.pytest_verdict", "--no-ruff", "--", "-k", "not slow"]

Runs `ruff check .` when ruff is on PATH (unless --no-ruff), then
`python -m pytest -q --tb=short` plus anything after `--`. Each failure is a
blocker Finding carrying the last 2000 chars of output.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from ._common import emit, finding


def _run(name: str, argv: list[str], findings: list[dict]) -> None:
    p = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = ((p.stdout or "") + "\n" + (p.stderr or ""))[-2000:]
        findings.append(
            finding(
                "blocker", name, ".", f"{name} failed with exit {p.returncode}", tail,
                f"make {name} pass without weakening the checks",
            )
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.pytest_verdict")
    ap.add_argument("--no-ruff", action="store_true", help="skip the ruff lint step")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER,
                    help="extra pytest args, after --")
    ns = ap.parse_args(argv)
    findings: list[dict] = []
    if not ns.no_ruff and shutil.which("ruff"):
        _run("lint", ["ruff", "check", "."], findings)
    # Drop only the LEADING separator: later "--" belong to pytest itself.
    extra = ns.pytest_args[1:] if ns.pytest_args[:1] == ["--"] else list(ns.pytest_args)
    _run("tests", [sys.executable, "-m", "pytest", "-q", "--tb=short", *extra], findings)
    return emit(findings, "lint and tests green", f"{len(findings)} check(s) failed")


if __name__ == "__main__":
    sys.exit(main())
