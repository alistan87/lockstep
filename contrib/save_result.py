#!/usr/bin/env python
"""save_result.py — publish a sibling node's result to a repo file (D-series).

    python contrib/save_result.py --node propose --out codemod-orders.json
    python contrib/save_result.py --node propose --out codemod-orders.json --drop-empty change
    python contrib/save_result.py --node core --out sudoku_core.py --strip-fence

Run as a shell node. Resolves <run_dir>/phases/<node>/result.json|result.txt
via LOCKSTEP_PHASE_DIR (the same mechanism the gate library uses), optionally
filters a JSON array by dropping entries whose named key is empty, and writes
the result to --out. Prints a one-line summary. Deterministic: a file copy
with an optional filter, never a rewrite.

`--strip-fence` is the one normalisation it will do: if the whole result is a
single ``` fenced block, unwrap it. This exists because small local models
wrap source in a fence however plainly the prompt forbids it, and the
alternative is a correctness gate that blocks forever on a formatting detail
instead of on correctness. It is opt-in, it touches nothing that is not a
fence around the ENTIRE result, and it says so when it fires.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def strip_fence(text: str) -> tuple[str, bool]:
    """Unwrap a ``` fence that encloses the WHOLE result. Anything else — a
    fence around part of it, no fence, an unterminated one — is returned
    untouched, because a partial unwrap is a corruption a later gate would
    report as a syntax error with no clue where it came from."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        inner = lines[1:-1]
        if not any(ln.startswith("```") for ln in inner):
            return "\n".join(inner) + "\n", True
    return text, False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node", required=True, help="node id whose result to publish")
    ap.add_argument("--out", required=True, help="repo-relative destination file")
    ap.add_argument("--drop-empty", default=None, metavar="KEY",
                    help="if the result is a JSON array, drop entries whose KEY is falsy")
    ap.add_argument("--strip-fence", action="store_true",
                    help="unwrap a ``` fence around the WHOLE result (see the module docstring)")
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
    unfenced = False
    if ns.strip_fence:
        text, unfenced = strip_fence(text)
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
    if unfenced:
        note += " (unwrapped a ``` fence)"
    print(f"published {ns.node} -> {ns.out}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
