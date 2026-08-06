"""Coverage non-regression -> Verdict.

Usage from a flow:
    ["python", "-m", "lockstep.gates.coverage_delta", "--baseline", "coverage-baseline.json"]

Reads `totals.percent_covered` from coverage.py's JSON report (a bare number
in the baseline file is also accepted). Blocks when current coverage falls
more than --tolerance below the baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._common import emit, finding


def _percent(path: str) -> tuple[float | None, dict | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, finding("blocker", "coverage", path, "coverage file unreadable",
                             str(e), "run pytest with --cov --cov-report=json first")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return float(data), None
    if isinstance(data, dict):
        pct = (data.get("totals") or {}).get("percent_covered")
        if isinstance(pct, (int, float)):
            return float(pct), None
    return None, finding("blocker", "coverage", path,
                         "no totals.percent_covered in coverage file",
                         f"top-level keys: {sorted(data) if isinstance(data, dict) else type(data).__name__}",
                         "point at a coverage.py JSON report or a bare number")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.coverage_delta")
    ap.add_argument("--baseline", required=True, help="baseline coverage JSON (or bare number)")
    ap.add_argument("--current", default="coverage.json", help="current coverage.py JSON report")
    ap.add_argument("--tolerance", type=float, default=0.0,
                    help="allowed drop in percentage points (default 0)")
    ns = ap.parse_args(argv)
    base, problem = _percent(ns.baseline)
    if problem:
        return emit([problem], "")
    cur, problem = _percent(ns.current)
    if problem:
        return emit([problem], "")
    findings: list[dict] = []
    if cur < base - ns.tolerance:
        findings.append(
            finding("blocker", "coverage-drop", ns.current,
                    f"coverage fell to {cur:.2f}% from baseline {base:.2f}%",
                    f"tolerance {ns.tolerance:.2f} points",
                    "add tests for the uncovered branches; do not lower the baseline")
        )
    return emit(findings, f"coverage {cur:.2f}% holds the {base:.2f}% baseline",
                "coverage regressed")


if __name__ == "__main__":
    sys.exit(main())
