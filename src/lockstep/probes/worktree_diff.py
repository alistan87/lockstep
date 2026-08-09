"""What changed in the working tree -> text, for a readonly reviewer.

Usage from a flow:
    ["python", "-m", "lockstep.probes.worktree_diff"]
    ["python", "-m", "lockstep.probes.worktree_diff", "--base", "HEAD", "--max-lines", "4000"]

Reviewer nodes used to be told to run `git status` and `git diff` themselves,
which needs shell execution — and a node that can run shell commands can write
files, so it cannot be `readonly`, so it holds the `tree` token and serializes
against every other reviewer. Capturing the diff here instead lets the reviewer
be genuinely readonly and run in parallel.

`git diff` alone would hide new work: files the implementer CREATED are
untracked and absent from it. They are listed explicitly, with their contents,
because "you added a file and nobody reviewed it" is the failure this exists to
prevent.

Always exits 0. A dirty tree, a clean tree and a non-git directory are all
observations; the reviewer downstream is what decides.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Big enough for a real change, small enough that the reviewer's prompt does
# not become the diff of a vendored directory somebody committed by accident.
DEFAULT_MAX_LINES = 4000
DEFAULT_MAX_UNTRACKED_BYTES = 40_000


def _git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "--no-pager", *args],
            capture_output=True, encoding="utf-8", errors="replace",
        )
    except OSError as e:  # git absent
        return 127, f"git could not be run: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _cap(lines: list[str], limit: int, what: str) -> list[str]:
    if limit <= 0 or len(lines) <= limit:
        return lines
    dropped = len(lines) - limit
    # Say that it truncated. A silent cut reads as "that is the whole change".
    return lines[:limit] + [f"", f"[... {dropped} more line(s) of {what} not shown ...]"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.probes.worktree_diff")
    ap.add_argument("--base", default="HEAD", help="what to diff against")
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ap.add_argument("--max-untracked-bytes", type=int, default=DEFAULT_MAX_UNTRACKED_BYTES)
    ap.add_argument("--no-untracked", action="store_true",
                    help="skip the contents of created files")
    ns = ap.parse_args(argv)

    # --untracked-files=all: the default collapses a new directory to one
    # `?? src/pkg/` entry, and the probe then tried to read a directory. A
    # reviewer needs the files, not the folder name.
    rc, status = _git("status", "--short", "--untracked-files=all")
    if rc == 127 or "not a git repository" in status.lower():
        print("WORKTREE: not a git repository — no diff available")
        print(status.strip())
        return 0

    out: list[str] = ["=== changed paths (git status --short) ==="]
    out.append(status.rstrip() or "(nothing changed)")

    rc, diff = _git("diff", ns.base)
    out.append("")
    out.append(f"=== diff against {ns.base} (tracked files) ===")
    out.extend(_cap((diff.rstrip() or "(no tracked changes)").splitlines(),
                    ns.max_lines, "diff"))

    if not ns.no_untracked:
        untracked = [
            ln[3:] for ln in status.splitlines() if ln.startswith("?? ")
        ]
        out.append("")
        out.append("=== created files (untracked — absent from the diff above) ===")
        if not untracked:
            out.append("(none)")
        for rel in untracked:
            path = Path(rel.strip().strip('"'))
            out.append("")
            out.append(f"--- {path} ---")
            try:
                raw = path.read_bytes()
            except OSError as e:
                out.append(f"(could not read: {e})")
                continue
            if len(raw) > ns.max_untracked_bytes:
                out.append(f"({len(raw)} bytes — too large to inline; read the file directly)")
                continue
            text = raw.decode("utf-8", errors="replace")
            if "\x00" in text:
                out.append(f"(binary, {len(raw)} bytes)")
                continue
            out.extend(_cap(text.rstrip().splitlines(), ns.max_lines, "file"))

    sys.stdout.flush()
    # UTF-8 bytes, not print(): a redirected stdout on Windows defaults to
    # cp1252, and source files contain arrows, dashes and non-Latin strings.
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
