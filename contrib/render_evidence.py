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
import textwrap
from pathlib import Path

BRIEFING = "Decide from this pane. The chat is a gloss; this is the thing itself."

WIDTH = 78
MAX_VERDICT_LINES = 12

# T3.3 — approval tiers. A tier changes PRESENTATION and what evidence is
# REQUIRED. It never changes whether a human is asked: auto-passing on a
# self-declared tier would let a flow author quietly remove the human, which is
# the one thing the trust model does not permit. Proportion is achieved by
# making the serious ones louder, not the routine ones absent.
TIERS = {
    "routine": "ROUTINE - reversible, and small",
    "standard": None,
    "irreversible": "IRREVERSIBLE - this cannot be undone by this flow",
}


def wrap_prose(text: str, indent: str = "    ", max_lines: int = MAX_VERDICT_LINES) -> list[str]:
    """Hard-wrap and cap a block of model prose for a pane.

    This exists because of a real artifact: the shipped hygiene demo's approval
    evidence carried a gate verdict as a single unbroken ~350-word paragraph on
    one line. The domain expert's guide tells them that not understanding what
    they are approving is a defect in the work rather than in them — an
    unreadable decision packet is that defect, and it is the failure mode that
    turns a gate into theatre.

    Capped, not merely wrapped: past a dozen lines the human stops reading, and
    a truncation that SAYS it truncated is more honest than a wall that dares
    them to.
    """
    body = " ".join((text or "").split())
    if not body:
        return []
    lines = textwrap.wrap(body, width=max(20, WIDTH - len(indent)))
    if max_lines > 0 and len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... {dropped} more lines - ask for the full text"]
    return [indent + ln for ln in lines]


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


def impact(cwd: Path) -> list[str]:
    """Blast radius, in counts a non-programmer can weigh.

    The recurring finding in the human-in-the-loop literature is not that people
    decide badly; it is that the packet they decide from is missing the two
    facts that determine how much care a decision needs — how much it touches,
    and whether it can be undone. A diffstat carries neither in a form somebody
    who has never seen a diff can use. `4 files changed, 1 DELETED` is legible
    to anyone.

    Deletion is called out separately and unconditionally, because it is the
    only change in the set that destroys something.
    """
    try:
        proc = subprocess.run(["git", "diff", "--name-status", "HEAD"], cwd=str(cwd),
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"scale of the change: unavailable ({e})"]
    if proc.returncode != 0:
        return ["scale of the change: unavailable (not a git working tree)"]

    added = modified = deleted = renamed = 0
    for line in (proc.stdout or "").splitlines():
        code = line.split("\t", 1)[0][:1]
        if code == "A":
            added += 1
        elif code == "M":
            modified += 1
        elif code == "D":
            deleted += 1
        elif code == "R":
            renamed += 1
    total = added + modified + deleted + renamed
    if total == 0:
        return ["scale of the change: nothing changed against the last saved state"]

    parts = []
    if added:
        parts.append(f"{added} new")
    if modified:
        parts.append(f"{modified} edited")
    if renamed:
        parts.append(f"{renamed} moved")
    if deleted:
        parts.append(f"{deleted} DELETED")
    out = [f"scale of the change: {total} file{'s' if total != 1 else ''} - " + ", ".join(parts)]
    if deleted:
        out.append("  something is deleted by this change - read the list above carefully")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headings", metavar="FILE", help="headings + first line of each section")
    ap.add_argument("--full", metavar="FILE", help="the document itself (short documents)")
    ap.add_argument("--diffstat", action="store_true", help="git diff --stat against HEAD")
    ap.add_argument("--title", default="what you are approving")
    ap.add_argument("--max-lines", type=int, default=80)
    ap.add_argument("--out", default=None, help="override the evidence path")
    ap.add_argument("--impact", action="store_true",
                    help="blast radius: how many files, and whether anything is deleted")
    ap.add_argument("--reversible", metavar="TEXT", default=None,
                    help="literal statement of how to undo this; absent renders as 'not stated'")
    ap.add_argument("--tier", choices=sorted(TIERS), default="standard",
                    help="approval tier (T3.3); changes presentation and required evidence only")
    ap.add_argument("--max-verdict-lines", type=int, default=MAX_VERDICT_LINES)
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

    # The decision packet: what changes, how much, and whether it can be undone.
    packet: list[str] = []
    if ns.impact:
        packet.extend(impact(Path.cwd()))
    elif ns.tier == "irreversible":
        # An irreversible approval with no impact block is a flow that declined
        # to characterise what it is about to do permanently. Say that, rather
        # than let a silent omission read as "nothing much happens".
        packet.append("scale of the change: NOT CHARACTERISED by this flow")
    if ns.reversible:
        packet.append(f"if this turns out wrong: {ns.reversible}")
    else:
        packet.append("if this turns out wrong: not stated by this flow")

    banner = TIERS.get(ns.tier)
    header = ["=" * 72, f"  {ns.title}"]
    if banner:
        header.append(f"  {banner}")
    header.append("=" * 72)

    text = "\n".join([
        *header,
        "",
        *body,
        "",
        "-" * 72,
        *packet,
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
