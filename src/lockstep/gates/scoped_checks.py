"""Diff-scoped checks -> Verdict: lint/test only the files THIS run changed.

The E4/lesson-13 companion (docs/notes/LESSONS-TO-MECHANISMS.md): a gate wired
to an absolute target (`ruff check .`, a named test file) blocks on
pre-existing debt in files the run never touched, and each false block costs a
full heal round. Scoping the check to the worktree's own changed files makes
the gate measure the CHANGE, not the repository's history. (For a check that
cannot be scoped per-file, use `baseline: true` on the gate instead — the
engine subtracts pre-run findings.)

Usage from a flow:
    ["python", "-m", "lockstep.gates.scoped_checks", "--run", "ruff check {files}"]
    ["python", "-m", "lockstep.gates.scoped_checks",
     "--run", "ruff check {files}", "--run", "python -m pyflakes {files}",
     "--suffix", ".py"]

Each --run template is split shlex-style; `{files}` expands to one argument
per changed file (the whole command is skipped, as a pass, when no changed
file survives the --suffix filter). Changed files = `git status --porcelain`
(tracked modifications + untracked non-ignored files) relative to the CWD the
driver runs the gate in. A nonzero exit becomes one blocker Finding carrying
the output tail; exit 0 contributes nothing.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys

from ._common import emit, finding


def changed_files() -> tuple[list[str], str | None]:
    # `-z`: porcelain v1 C-quotes non-ASCII paths on line output, and a
    # mangled name handed to the lint command errors into a false blocker.
    # A rename's SOURCE follows as its own NUL record and is skipped; any
    # 'D' in the two status columns means the worktree file is gone (" D",
    # "D ", "AD", "MD") and cannot be linted.
    p = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        return [], (p.stderr or "git status failed").strip()
    files: list[str] = []
    records = p.stdout.split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, path = rec[:2], rec[3:]
        if xy[0] in ("R", "C"):
            i += 1  # skip the rename/copy source record
        if "D" in xy:
            continue
        files.append(path)
    return files, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.scoped_checks")
    ap.add_argument("--run", action="append", required=True, metavar="TEMPLATE",
                    help="command template; {files} expands to the changed files")
    ap.add_argument("--suffix", action="append", default=[], metavar=".EXT",
                    help="only include changed files with this suffix (repeatable)")
    ns = ap.parse_args(argv)

    files, err = changed_files()
    if err is not None:
        return emit(
            [finding("blocker", "gate-error", ".", "cannot list changed files", err,
                     "run inside a git worktree")],
            "", "cannot list changed files",
        )
    if ns.suffix:
        files = [f for f in files if any(f.endswith(s) for s in ns.suffix)]

    findings: list[dict] = []
    ran = 0
    for template in ns.run:
        parts = shlex.split(template)
        if "{files}" in parts and not files:
            continue  # nothing in scope changed: the check has no subject
        argv_cmd = [p for tok in parts for p in (files if tok == "{files}" else [tok])]
        ran += 1
        p = subprocess.run(argv_cmd, capture_output=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            tail = ((p.stdout or "") + "\n" + (p.stderr or ""))[-2000:]
            findings.append(
                finding(
                    "blocker", "scoped-check", ", ".join(files) or ".",
                    f"{template!r} failed with exit {p.returncode} on the run's own changes",
                    tail, "fix the change; these files are the ones this run touched",
                )
            )
    if ran == 0:
        return emit([], f"no changed files match the scope ({len(ns.run)} check(s) skipped)")
    return emit(findings, f"{ran} check(s) green on {len(files)} changed file(s)",
                f"{len(findings)} scoped check(s) failed")


if __name__ == "__main__":
    sys.exit(main())
