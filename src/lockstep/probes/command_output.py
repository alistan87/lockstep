"""Run one command and report what it did -> text, for a readonly judge.

Usage from a flow:
    ["python", "-m", "lockstep.probes.command_output", "{args.repro}"]
    ["python", "-m", "lockstep.probes.command_output", "--label", "repro", "{args.repro}"]

The observation half of a repro: a diagnostician needs to SEE the failure, and
seeing it means running it — which needs shell execution, which is a write
vector, which means the node cannot be `readonly`. Running it here instead lets
the diagnostician be readonly, and makes the observed failure a cached,
inspectable artifact rather than something re-derived on every attempt.

**Always exits 0.** A command that fails is the point; a non-zero exit here
would fail the node and the flow would never reach the diagnosis. The exit code
is reported in the text so the judge can read it.

The command arrives as ONE string (it comes from `--arg`), so it is split with
`shlex` — POSIX rules off on Windows, where backslashes are path separators
rather than escapes.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

DEFAULT_TIMEOUT_S = 600
DEFAULT_MAX_LINES = 400


def split_command(text: str) -> list[str]:
    """`shlex` with the Windows caveat, then strip paired quotes a shell would
    have removed (`pytest -k "a or b"` arrives with the quotes attached)."""
    tokens = shlex.split(text, posix=(os.name != "nt"))
    return [
        t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "'\"" else t
        for t in tokens
    ]


def _cap(text: str, limit: int) -> str:
    lines = text.splitlines()
    if limit <= 0 or len(lines) <= limit:
        return text.rstrip()
    head = limit // 2
    tail = limit - head
    dropped = len(lines) - limit
    # Keep BOTH ends: a traceback's cause is at the top and its assertion at
    # the bottom, and a middle-out cut is the only one that keeps them both.
    return "\n".join(
        lines[:head] + ["", f"[... {dropped} middle line(s) omitted ...]", ""] + lines[-tail:]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.probes.command_output")
    ap.add_argument("command", help="the command to run, as one string")
    ap.add_argument("--label", default="command")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ns = ap.parse_args(argv)

    out: list[str] = []
    try:
        cmd = split_command(ns.command)
    except ValueError as e:
        cmd = []
        out.append(f"{ns.label}: could not be parsed as a command ({e})")
    if not cmd and not out:
        out.append(f"{ns.label}: empty — nothing to run")

    if cmd:
        out.append(f"=== {ns.label}: {' '.join(cmd)} ===")
        try:
            p = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace",
                timeout=ns.timeout,
            )
            body = (p.stdout or "") + (p.stderr or "")
            out.append(f"exit code: {p.returncode}"
                       f"{'  (FAILED — this is the observation)' if p.returncode else '  (passed)'}")
            out.append("")
            out.append(_cap(body, ns.max_lines) or "(no output)")
        except subprocess.TimeoutExpired:
            out.append(f"exit code: none — TIMED OUT after {ns.timeout}s")
        except OSError as e:
            out.append(f"exit code: none — could not run it: {e}")

    sys.stdout.flush()
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
