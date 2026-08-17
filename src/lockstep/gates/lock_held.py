"""Lock preflight -> Verdict: can this file be opened for exclusive writing
RIGHT NOW, and does anything claim to hold it?

Usage from a flow (first node of anything that will write a shared resource,
e.g. a DuckDB database):
    ["python", "-m", "lockstep.gates.lock_held", "path/to/knowledge.duckdb"]

Two checks, either blocks:
  1. A non-blocking exclusive lock attempt over the whole file
     (`msvcrt.locking` LK_NBLCK on Windows, `fcntl.lockf` LOCK_EX|LOCK_NB
     with len 0 elsewhere), released immediately on success. A refused lock
     blocks as `lock-held`. An open refused outright — after ONE retry,
     because this machine's AV throws transient PermissionError on ordinary
     file operations — blocks as `open-refused`: an exclusive-mode holder (a
     DuckDB writer is one) causes it, but so can an ACL or a read-only
     attribute, and the verdict says so instead of asserting a holder that
     may not exist. Either way `<path>.holder.json` is quoted when present,
     so the common failure NAMES its holder.
  2. A holder file naming a pid that is ALIVE on this host blocks even when
     the OS lock succeeds — the holder-file convention (write after a
     successful acquire, remove only your own) marks the resource claimed;
     between its open and its lock is a window this check refuses to race.
     A FOREIGN holder file (another hostname) also blocks: its liveness is
     unknowable from here, and who_holds.py says FOREIGN for the same file.

A STALE holder file (dead pid, this host) does not block: the OS released
the dead process's locks, and the pass reason says the stale file is there
to clean up.

What this is NOT, stated because it will be misread otherwise: not a mutex
and not a reservation — the resource's own lock is the lock, and the world
can change between this verdict and the write (that race is why designated
writers retry-until-acquired at the open itself). It is a DIAGNOSTIC that
turns the common failure — a known long-lived holder — into a fast, named
refusal at the head of the flow instead of a traceback mid-run. Known blind
spots: a process that merely has the file open without an OS lock (POSIX,
or share-friendly Windows opens) is invisible to check 1 — that is what the
holder-file convention (check 2) exists to cover; and check 1 speaks only
the byte-range-lock family from offset 0 — a holder using `flock()` on
Linux, or range locks entirely above our range, does not conflict and
passes silently.

A missing file passes: nothing can hold what does not exist yet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ._common import emit, finding, pid_alive

# From offset 0. Whole-file on POSIX (len 0 = to EOF and beyond); on Windows
# a fixed large range — DuckDB's and SQLite's Windows locks both live below
# it. Locking beyond EOF is legal on both platforms (empty files included).
_WIN_LOCK_BYTES = 0x7FFF0000


def _holder_line(path: Path) -> tuple[str, bool]:
    """(one-line description of <path>.holder.json, blocks). Vocabulary
    mirrors contrib/who_holds.py — LIVE/STALE/FOREIGN — so a human who runs
    the reporting tool after this verdict reads the same words."""
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
    host = holder.get("hostname")
    if host:
        import socket

        if host != socket.gethostname():
            # Liveness is unknowable from here; failing open would pass a
            # file a remote writer may hold. who_holds prints FOREIGN too.
            return f"FOREIGN pid {pid} on {host} ({purpose})", True
    alive = pid_alive(pid)
    return f"{'LIVE' if alive else 'STALE'} pid {pid} ({purpose})", alive


def _try_exclusive(path: Path) -> tuple[bool, str, str]:
    """(acquired, category, detail). Non-blocking; releases immediately on
    success. The open gets one retry — AV PermissionError transients are a
    documented quirk of this machine class, and a preflight that converts
    one into a named 'holder' would block flows over nothing."""
    fd = None
    for attempt in (0, 1):
        try:
            fd = os.open(path, os.O_RDWR)
            break
        except FileNotFoundError:
            return True, "", "file does not exist yet — nothing can hold it"
        except OSError as e:
            if attempt == 1:
                return False, "open-refused", (
                    f"open for writing refused twice ({e}) — an exclusive-mode "
                    f"holder (a DuckDB writer is one), an ACL, or a read-only "
                    f"attribute; whichever it is, a write here fails right now"
                )
            time.sleep(1.0)
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, _WIN_LOCK_BYTES)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, _WIN_LOCK_BYTES)
            except OSError as e:
                return False, "lock-held", f"exclusive byte-range lock refused ({e})"
        else:
            import fcntl

            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.lockf(fd, fcntl.LOCK_UN)
            except OSError as e:
                return False, "lock-held", f"exclusive lock refused ({e})"
        return True, "", "exclusive lock acquired and released"
    finally:
        if fd is not None:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.lock_held")
    ap.add_argument("path", help="the file the flow intends to write")
    ns = ap.parse_args(argv)
    path = Path(ns.path)

    acquired, category, detail = _try_exclusive(path)
    holder_desc, holder_blocks = _holder_line(path)

    if not acquired:
        return emit(
            [finding(
                "blocker", category, str(path),
                "the file cannot be opened for exclusive writing",
                f"{detail}; holder: {holder_desc}",
                "wait for the holder to finish (who_holds.py watches it), or "
                "serialize this flow into the resource's writer lane",
            )],
            "", f"cannot write {path}",
        )
    if holder_blocks:
        return emit(
            [finding(
                "blocker", "holder-live", str(path),
                "a holder claims this file even though no OS lock is held",
                f"holder: {holder_desc}; lock attempt: {detail}",
                "the holder may be between open and lock (or on another "
                "machine) — wait for it, or investigate with "
                "contrib/who_holds.py",
            )],
            "", f"holder claims {path}",
        )
    note = "" if holder_desc.startswith("no holder") else f" ({holder_desc} — stale file left behind)"
    return emit([], f"{path}: {detail}{note}")


if __name__ == "__main__":
    sys.exit(main())
