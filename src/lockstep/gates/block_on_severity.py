"""Finding[] -> Verdict at a severity threshold (replaces the thrice-duplicated
inline review gate).

Usage from a flow:
    ["python", "-m", "lockstep.gates.block_on_severity", "--at", "major", "--node", "review"]
    ["python", "-m", "lockstep.gates.block_on_severity", "--at", "blocker", "findings.json"]

--node resolves the sibling node's result via LOCKSTEP_PHASE_DIR (set by the
lockstep spawn), i.e. <run_dir>/phases/<node>/result.json — the same path the
inline gates computed by hand. Findings at or above the threshold are kept in
the Verdict and it blocks; findings below the threshold are dropped from the
Verdict (they remain in the producing node's own result). A finding whose
severity is missing or unrecognized BLOCKS: a malformed severity must fail
closed, not slip under the threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._common import SEVERITIES, emit, finding, resolve_node_result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.block_on_severity")
    ap.add_argument("--at", default="major", choices=list(SEVERITIES),
                    help="block on findings at or above this severity (default: major)")
    ap.add_argument("--node", default=None,
                    help="node id whose result.json holds the Finding[] (via LOCKSTEP_PHASE_DIR)")
    ap.add_argument("path", nargs="?", default=None, help="explicit findings-file path")
    ns = ap.parse_args(argv)
    if bool(ns.node) == bool(ns.path):
        ap.error("pass exactly one of --node <id> or a findings-file path")
    if ns.node:
        p, problem = resolve_node_result(ns.node)
        if problem:
            return emit([problem], "")
    else:
        p = Path(ns.path)
    try:
        findings = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return emit(
            [finding(
                "blocker", "gate-error", str(p), "could not read findings", str(e),
                "inspect the producing node's phase dir",
            )],
            "",
        )
    if not isinstance(findings, list):
        return emit(
            [finding(
                "blocker", "gate-error", str(p), "result is not a Finding array",
                type(findings).__name__, "inspect the producing node's phase dir",
            )],
            "",
        )
    keep = SEVERITIES[: SEVERITIES.index(ns.at) + 1]
    kept = [f for f in findings if isinstance(f, dict) and f.get("severity") in keep]
    malformed = [
        f for f in findings
        if not isinstance(f, dict) or f.get("severity") not in SEVERITIES
    ]
    if malformed:
        # Fail closed: an unrecognized severity ("critical", a typo, absent)
        # must not slip under the threshold.
        kept.append(finding(
            "blocker", "gate-error", str(p),
            f"{len(malformed)} finding(s) with missing or unknown severity",
            json.dumps(malformed[:3], ensure_ascii=False)[:500],
            "fix the producer to emit the Finding contract's severities",
        ))
    return emit(
        kept,
        f"no findings at or above '{ns.at}'",
        f"{len(kept)} blocking finding(s) - fix and resume",
    )


if __name__ == "__main__":
    sys.exit(main())
