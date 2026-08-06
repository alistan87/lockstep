"""Required markdown sections -> Verdict (replaces proposal-gate's inline
structure gate).

Usage from a flow:
    ["python", "-m", "lockstep.gates.required_sections", "{args.file}", "{args.sections}"]

The second argument is a comma-separated section list; a section is present
when any markdown heading contains it (case-insensitive), matching the
original inline gate's semantics exactly.
"""

from __future__ import annotations

import argparse
import re
import sys

from ._common import emit, finding, read_doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.required_sections")
    ap.add_argument("path", help="the document to check")
    ap.add_argument("sections", help="comma-separated required section names")
    ns = ap.parse_args(argv)
    text, problem = read_doc(ns.path)
    if problem:
        return emit([problem], "")
    findings: list[dict] = []
    heads = [m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)]
    for s in (s.strip() for s in ns.sections.split(",")):
        if s and not any(s.lower() in h for h in heads):
            findings.append(
                finding(
                    "blocker", "missing-section", ns.path,
                    f'required section "{s}" not found',
                    f'no markdown heading contains "{s}"',
                    f'add a "## {s}" section',
                )
            )
    return emit(
        findings, "all required sections present", f"{len(findings)} structural problem(s)"
    )


if __name__ == "__main__":
    sys.exit(main())
