#!/usr/bin/env python
"""save_result.py — publish a sibling node's result to a repo file (D-series).

    python contrib/save_result.py --node propose --out codemod-orders.json
    python contrib/save_result.py --node propose --out codemod-orders.json --drop-empty change

Run as a shell node. Resolves <run_dir>/phases/<node>/result.json|result.txt
via LOCKSTEP_PHASE_DIR (the same mechanism the gate library uses), optionally
filters a JSON array by dropping entries whose named key is empty, and writes
the result to --out. Prints a one-line summary. Deterministic: a file copy
with an optional filter, never a rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node", required=True, help="node id whose result to publish")
    ap.add_argument("--out", required=True, help="repo-relative destination file")
    ap.add_argument("--drop-empty", default=None, metavar="KEY",
                    help="if the result is a JSON array, drop entries whose KEY is falsy")
    ns = ap.parse_args(argv)
    phase = os.environ.get("LOCKSTEP_PHASE_DIR", "")
    if not phase:
        print("LOCKSTEP_PHASE_DIR is not set — run this as a shell node", file=sys.stderr)
        return 1
    node_dir = Path(phase).parent / ns.node
    src = next((node_dir / n for n in ("result.json", "result.txt")
                if (node_dir / n).exists()), None)
    if src is None:
        print(f"node {ns.node!r} left no result in {node_dir}", file=sys.stderr)
        return 1
    text = src.read_text(encoding="utf-8")
    dropped = 0
    if ns.drop_empty:
        value = json.loads(text)
        if not isinstance(value, list):
            print(f"--drop-empty needs a JSON array result; got {type(value).__name__}",
                  file=sys.stderr)
            return 1
        kept = [v for v in value if isinstance(v, dict) and v.get(ns.drop_empty)]
        dropped = len(value) - len(kept)
        text = json.dumps(kept, ensure_ascii=False, indent=2)
    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    note = f" ({dropped} empty entr{'y' if dropped == 1 else 'ies'} dropped)" if dropped else ""
    print(f"published {ns.node} -> {ns.out}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
