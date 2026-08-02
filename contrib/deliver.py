#!/usr/bin/env python
"""deliver.py — copy an approved deliverable out to a folder the human owns.

    python contrib/deliver.py <source> [--to Deliverables]

Runs as the last shell node of a flow, downstream of the human approval. Two
rules it exists to enforce:

- **Egress is always human-approved.** This script must never appear upstream
  of the approval node. `runs/` holds prompts, diffs, and model output and is
  sensitive; this is the one sanctioned way something leaves it.
- **It must be trivial.** Everything downstream of an approval executes inside
  the human's own resume process. A copy is fine. Anything that spawns, or
  takes more than a moment, belongs in the next segment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def resolve_source(source: str) -> Path:
    """A relative source is resolved against the RUN DIR first, then the cwd.

    Shell nodes run with the repo root as cwd, but the artifacts an approval
    produces (`approval-evidence.txt`) live in the run dir — and there is no
    `{run_dir}` interpolation form to write into the flow. The run dir is two
    levels above LOCKSTEP_PHASE_DIR, which every spawned node carries.
    """
    src = Path(source)
    if src.is_absolute():
        return src
    phase = os.environ.get("LOCKSTEP_PHASE_DIR")
    if phase:
        candidate = Path(phase).resolve().parents[1] / source
        if candidate.is_file():
            return candidate
    return src


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source")
    ap.add_argument("--to", default="Deliverables")
    ns = ap.parse_args(argv)

    src = resolve_source(ns.source)
    if not src.is_file():
        # Do not fail the node: the approval already happened, and a hard fail
        # here would leave the run looking rejected when the human said yes.
        # But DO print to stdout — a shell node declaring `output: "text"` with
        # an empty stdout is treated as "no result emitted" and fails anyway,
        # which is the same bad outcome by a longer route.
        print(f"approved - but {ns.source} was not found, nothing copied")
        print(f"approved, but {src} was not found - nothing copied", file=sys.stderr)
        return 0
    dest_dir = Path(ns.to)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    print("approved - copied " + str(src) + " to " + str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
