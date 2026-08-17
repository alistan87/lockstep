"""Lock preflight -> Verdict: can this file be opened for exclusive writing
RIGHT NOW, and does anything claim to hold it?

Usage from a flow (first node of anything that will write a shared resource,
e.g. a DuckDB database):
    ["python", "-m", "lockstep.gates.lock_held", "path/to/knowledge.duckdb"]

Two checks, either blocks:
  1. A non-blocking exclusive byte-range lock attempt (`msvcrt.locking`
     LK_NBLCK on Windows, `fcntl.lockf` LOCK_EX|LOCK_NB elsewhere), released
     immediately on success. A refused lock — or an open refused outright, as
     an exclusive-mode holder causes on Windows — blocks, quoting
     `<path>.holder.json` when present so the failure NAMES its holder.
  2. A holder file naming a pid that is ALIVE on this host blocks even when
     the OS lock succeeds — the holder-file convention (write after a
     successful acquire, remove only your own) marks the resource claimed;
     between its open and its lock is a window this check refuses to race.

A STALE holder file (dead pid) does not block: the OS released the dead
process's locks, and the pass reason says the stale file is there to clean up.

What this is NOT, stated because it will be misread otherwise: not a mutex
and not a reservation — the resource's own lock is the lock, and the world
can change between this verdict and the write (that race is why designated
writers retry-until-acquired at the open itself). It is a DIAGNOSTIC that
turns the common failure — a known long-lived holder — into a fast, named
refusal at the head of the flow instead of a traceback mid-run. Known blind
spot: a process that merely has the file open without an OS lock (POSIX, or
share-friendly Windows opens) is invisible to check 1; that is what the
holder-file convention (check 2) exists to cover.

A missing file passes: nothing can hold what does not exist yet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..state import _pid_alive
from ._common import emit, finding


def _holder_line(path: Path) -> tuple[str, bool]:
    """(one-line description of <path>.holder.json, is_live). Mirrors
    contrib/who_holds.py's vocabulary — LIVE/STALE/NONE — so a human who runs
    the reporting tool after seeing this gate's verdict reads the same words."""
    hp = Path(str(path) + ".holder.json")
    if not hp.exists():
        return "no holder file — the holder did not follow the convention, or is not one of ours", False
    try:
        holder = json.loads(hp.read_text(encoding="utf-8"))
        pid = holder.get("pid")
    except (OSError, ValueError) as e:
        return f"holder file unreadable ({e})", False
    if not isinstance(pid, int):
        return "holder file has no integer pid", False
    purpose = holder.get("purpose") or "?"
    alive = _pid_alive(pid)
    return f"{'LIVE' if alive else 'STALE'} pid {pid} ({purpose})", alive


def _try_exclusive(path: Path) -> tuple[bool, str]:
    """(acquired, detail). Non-blocking; releases immediately on success."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return True, "file does not exist yet — nothing can hold it"
    except OSError as e:
        # A Windows exclusive-mode holder (a DuckDB writer is one) refuses the
        # OPEN, before any lock call. That is a held lock in every sense that
        # matters here.
        return False, f"open for writing refused ({e})"
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError as e:
                return False, f"exclusive byte-range lock refused ({e})"
        else:
            import fcntl

            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.lockf(fd, fcntl.LOCK_UN)
            except OSError as e:
                return False, f"exclusive lock refused ({e})"
        return True, "exclusive lock acquired and released"
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.lock_held")
    ap.add_argument("path", help="the file the flow intends to write")
    ns = ap.parse_args(argv)
    path = Path(ns.path)

    acquired, detail = _try_exclusive(path)
    holder_desc, holder_live = _holder_line(path)

    if not acquired:
        return emit(
            [finding(
                "blocker", "lock-held", str(path),
                "the file cannot be opened for exclusive writing",
                f"{detail}; holder: {holder_desc}",
                "wait for the holder to finish (who_holds.py watches it), or "
                "serialize this flow into the resource's writer lane",
            )],
            "", f"lock held on {path}",
        )
    if holder_live:
        return emit(
            [finding(
                "blocker", "holder-live", str(path),
                "a live process claims this file even though no OS lock is held",
                f"holder: {holder_desc}; lock attempt: {detail}",
                "the holder may be between open and lock — wait for it, or "
                "investigate with contrib/who_holds.py",
            )],
            "", f"live holder claims {path}",
        )
    note = "" if holder_desc.startswith("no holder") else f" ({holder_desc} — stale file left behind)"
    return emit([], f"{path}: {detail}{note}")


if __name__ == "__main__":
    sys.exit(main())
