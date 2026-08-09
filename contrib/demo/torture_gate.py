#!/usr/bin/env python
"""torture_gate.py — "does this file say GOOD?" -> Verdict.

    python contrib/demo/torture_gate.py torture/app.txt

The deterministic half of the torture flows: a gate with no judgement in it at
all, so that when a run heals twice and then passes, the only thing that can
have changed is the engine doing its job. A model-shaped gate would blur the
very thing the suite is measuring.

Blocks with one Finding whose `fix_hint` is a specific, checkable instruction —
the heal round appends findings verbatim to the target's next prompt, so this
doubles as the payload that proves findings actually arrive.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="torture_gate")
    ap.add_argument("path")
    ap.add_argument("--want", default="GOOD")
    ns = ap.parse_args(argv)

    p = Path(ns.path)
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    ok = ns.want in text

    findings = [] if ok else [{
        "severity": "blocker",
        "category": "torture",
        "file": ns.path,
        "line": None,
        "claim": f"{ns.path} does not contain {ns.want!r}",
        "evidence": f"contents: {text.strip()[:120]!r}" if text else f"{ns.path} does not exist",
        "fix_hint": f"write {ns.want!r} as the first line of {ns.path}",
    }]
    out = {
        "findings": findings,
        "verdict": "pass" if ok else "block",
        "reason": (f"{ns.path} contains {ns.want!r}" if ok
                   else f"{ns.path} is not {ns.want!r} yet"),
    }
    sys.stdout.flush()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
