#!/usr/bin/env python
"""render_evidence.py — build the extract a human decides from (proposal B1).

    python contrib/render_evidence.py --headings PLAN.md --title "the plan"
    python contrib/render_evidence.py --diffstat --title "what changed"
    python contrib/render_evidence.py --full NOTES.md --max-lines 60

Run as a shell node immediately before a terminal approval. It writes
`<run_dir>/approval-evidence.txt` and prints the same text to its own stdout.

Why a file and not just stdout: a shell node's stdout goes to `phases/<node>/
stdout.log`, NOT to the terminal, and the approval prompt itself is a single
`input()` line. So nothing the flow prints ever reaches the pane on its own.
The cockpit's APPROVAL pane runs `contrib/approve.ps1`, which prints this file
and then calls `lockstep resume` — one pre-typed command, evidence first,
prompt second. (The rev-7 proposal asserted the approval node renders evidence
"into its own TTY output"; it cannot, and this file is the mechanism that makes
the rule true.)

The extract is deterministic — file text in, file text out, no model — so it
carries the same trust status as the mechanical MISSION tier. The flow author
chooses WHICH extract; they never get to choose a narrated summary.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BRIEFING = "Decide from this pane. The chat is a gloss; this is the thing itself."


def run_dir_from_env() -> Path | None:
    """Shell nodes get LOCKSTEP_PHASE_DIR; the run dir is two levels up. There
    is no {run_dir} interpolation form, by design."""
    phase = os.environ.get("LOCKSTEP_PHASE_DIR")
    return Path(phase).resolve().parents[1] if phase else None


def headings_extract(path: Path, max_sections: int = 40) -> list[str]:
    """Markdown headings plus the first non-empty line under each — the shape
    of the document and what each part claims, without the bulk."""
    out: list[str] = []
    current: str | None = None
    wants_line = False
    sections = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            if sections >= max_sections:
                out.append(f"  … {max_sections}+ sections; full document at {path}")
                break
            current = line
            out.append(line)
            sections += 1
            wants_line = True
            continue
        if wants_line and line.strip():
            out.append(f"    {line.strip()[:150]}")
            wants_line = False
    if not out:
        out.append(f"(no headings found in {path})")
    del current
    return out


def full_extract(path: Path, max_lines: int) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return lines
    return lines[:max_lines] + [f"… {len(lines) - max_lines} more lines; full text at {path}"]


def diffstat(cwd: Path) -> list[str]:
    try:
        proc = subprocess.run(["git", "diff", "--stat", "HEAD"], cwd=str(cwd),
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"(git diff unavailable: {e})"]
    body = (proc.stdout or "").strip()
    return body.splitlines() if body else ["(no changes against HEAD)"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headings", metavar="FILE", help="headings + first line of each section")
    ap.add_argument("--full", metavar="FILE", help="the document itself (short documents)")
    ap.add_argument("--diffstat", action="store_true", help="git diff --stat against HEAD")
    ap.add_argument("--title", default="what you are approving")
    ap.add_argument("--max-lines", type=int, default=80)
    ap.add_argument("--out", default=None, help="override the evidence path")
    ns = ap.parse_args(argv)

    body: list[str] = []
    missing: list[str] = []
    for label, path_str, fn in (
        ("headings", ns.headings, lambda p: headings_extract(p)),
        ("full", ns.full, lambda p: full_extract(p, ns.max_lines)),
    ):
        if not path_str:
            continue
        path = Path(path_str)
        if not path.is_file():
            # A missing deliverable is itself the evidence: it means the flow
            # did not produce what it promised, and the DE should reject.
            missing.append(str(path))
            body.append(f"!! expected {label} source {path} — NOT FOUND")
            continue
        body.extend(fn(path))
    if ns.diffstat:
        body.extend(diffstat(Path.cwd()))
    if not body:
        body.append("(nothing requested — pass --headings, --full, or --diffstat)")

    text = "\n".join([
        "=" * 72,
        f"  {ns.title}",
        "=" * 72,
        "",
        *body,
        "",
        "-" * 72,
        BRIEFING,
        "",
    ])

    out_path = Path(ns.out) if ns.out else None
    if out_path is None:
        run_dir = run_dir_from_env()
        out_path = (run_dir / "approval-evidence.txt") if run_dir else Path("approval-evidence.txt")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"warning: could not write {out_path}: {e}", file=sys.stderr)

    print(text)
    # Exit 0 even when a source is missing: the approval is the decision point,
    # and a failed shell node would deny the human the chance to see why.
    if missing:
        print(f"note: {len(missing)} evidence source(s) missing", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
