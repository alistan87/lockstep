#!/usr/bin/env python
"""deliverable_check.py — is the thing actually there? -> Verdict.

    python contrib/demo/deliverable_check.py backend/split.py frontend/index.html

The last node of `webapp-local`, and it is a GATE rather than a work node for
a reason worth writing down.

The first version was a work node that printed a per-file table and then a
success sentence. On 2026-08-10 it printed:

    ok   backend/split.py  2129 bytes
    ok   backend/server.py 3029 bytes
    MISS frontend/split.js
    MISS frontend/index.html
    expense splitter built and gated: ... frontend all passed

and the run exited **0**. Every gate had genuinely passed; a later gate's heal
rollback then removed two files a passed gate had already approved. The report
saw it, said so, and could not act on it, because a `work` node has no verdict
and the driver has nothing to branch on.

A false green is worse than a red: red gets investigated. So the check that
the deliverable exists emits a Verdict like any other check, and an empty
argument list is itself a blocker rather than a vacuous pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deliverable_check")
    ap.add_argument("paths", nargs="*", help="every file the flow promised to produce")
    ap.add_argument("--min-bytes", type=int, default=1,
                    help="a file this small is as good as absent")
    ns = ap.parse_args(argv)

    findings = []
    if not ns.paths:
        findings.append({
            "severity": "blocker", "category": "gate-error", "file": ".", "line": None,
            "claim": "nothing was named as a deliverable",
            "evidence": "the gate was called with no paths, so it would pass whatever happened",
            "fix_hint": "list the files the flow is supposed to produce",
        })
    sizes = []
    for rel in ns.paths:
        p = Path(rel)
        if not p.is_file():
            findings.append({
                "severity": "blocker", "category": "missing", "file": rel, "line": None,
                "claim": f"{rel} was not produced",
                "evidence": "a gate upstream approved this file, so if it is gone now something "
                            "removed it after the fact — check whether a later heal round's "
                            "rollback discarded it (its baseline covers the whole tree, not "
                            "just its own target's paths)",
                "fix_hint": "serialise the branches so no gate's heal window overlaps another's",
            })
            continue
        n = p.stat().st_size
        sizes.append(f"{rel} ({n} bytes)")
        if n < ns.min_bytes:
            findings.append({
                "severity": "blocker", "category": "empty", "file": rel, "line": None,
                "claim": f"{rel} is {n} bytes",
                "evidence": "present but empty, which passes an existence check and nothing else",
                "fix_hint": "check the node that writes it actually had a result to publish",
            })

    out = {
        "findings": findings,
        "verdict": "pass" if not findings else "block",
        "reason": ("every promised file is present: " + ", ".join(sizes)) if not findings
                  else f"{len(findings)} deliverable problem(s)",
    }
    sys.stdout.flush()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
