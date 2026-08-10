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
import re
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


_FENCE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)(?:\n[ \t]*```|\Z)", re.DOTALL)
_DRIVER_MARKERS = ("begin data", "end data")


def extract_code(text: str) -> tuple[str, str]:
    """Pull the code out of a chatty answer. Returns (text, what-it-did).

    The greedy sibling of `strip_fence`, and it exists because of two heal
    rounds that were spent on formatting rather than on correctness
    (`webapp-local`, 2026-08-09): a 24B wrapped its module in prose plus a
    ```python block — which `strip_fence` leaves alone, correctly, because the
    fence does not enclose the whole result — and on the next round echoed
    lockstep's own `begin data` fence marker into its answer. Both produced a
    SyntaxError on line 1 and burned a round that had nothing to do with the
    task.

    So: if there are fenced blocks, take the LARGEST — a model that shows its
    work puts the answer in the biggest block, and picking by size beats
    picking the first. Otherwise drop the driver's own markers and any stray
    fence lines. Use this for nodes whose result is SOURCE CODE; `--strip-fence`
    stays the conservative choice everywhere else, because for a result that is
    prose or JSON this would happily throw away most of it.
    """
    blocks = _FENCE_BLOCK.findall(text)
    if blocks:
        best = max(blocks, key=len).strip()
        return best + "\n", f"took the largest of {len(blocks)} fenced block(s)"
    kept, dropped = [], 0
    for ln in text.splitlines():
        s = ln.strip()
        if s in _DRIVER_MARKERS or s.startswith("```"):
            dropped += 1
            continue
        kept.append(ln)
    body = "\n".join(kept).strip() + "\n"
    return body, (f"dropped {dropped} marker line(s)" if dropped else "no fence found")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node", required=True, help="node id whose result to publish")
    ap.add_argument("--out", required=True, help="repo-relative destination file")
    ap.add_argument("--drop-empty", default=None, metavar="KEY",
                    help="if the result is a JSON array, drop entries whose KEY is falsy")
    ap.add_argument("--strip-fence", action="store_true",
                    help="unwrap a ``` fence around the WHOLE result (see the module docstring)")
    ap.add_argument("--extract-code", action="store_true",
                    help="greedier: take the LARGEST fenced block, or drop the driver's own "
                         "markers. For nodes whose result is source code (see extract_code)")
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
    extracted = ""
    if ns.extract_code:
        text, extracted = extract_code(text)
    elif ns.strip_fence:
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
    if extracted:
        # Say what was thrown away. A normalisation that edits the model's
        # answer in silence is one nobody can debug from the run dir.
        note += f" ({extracted})"
    print(f"published {ns.node} -> {ns.out}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
