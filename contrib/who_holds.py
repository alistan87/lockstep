#!/usr/bin/env python
"""who_holds.py — who claims to hold a write lock on this file?

    python contrib/who_holds.py <path>

Prints exactly one line and ALWAYS exits 0 — this reports a fact, it never
decides (the gate that decides is `lockstep.gates.lock_held`):

    NONE                          no <path>.holder.json
    LIVE <pid> <purpose>          holder file names a pid that is alive here
    STALE <pid> <purpose>         holder file names a pid that is dead here
    FOREIGN <pid> <host>          holder file names another host; liveness
                                  unknown from this machine
    UNKNOWN (<why>)               holder file present but unreadable

The convention (shared with the work-repo MIMIR runbook, and deliberately
mirroring lockstep's own run-lock vocabulary): a process that has
SUCCESSFULLY opened <path> read-write writes `<path>.holder.json` with
`{"pid": ..., "started": ..., "purpose": ..., "run_dir": ...}` and removes it
on clean exit only if it still names its own pid. The holder file is
advisory — the file's own OS lock is the lock — so a STALE report means "the
named holder died without cleaning up", not "the file is locked".

Same accepted weakness as lockstep's run lock: a recycled pid reads as LIVE.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path


def pid_alive(pid: int) -> bool:
    """Mirrors lockstep.state._pid_alive — contrib stays stdlib-standalone."""
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            still_active = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(still_active))
            kernel32.CloseHandle(handle)
            return bool(ok) and still_active.value == 259  # STILL_ACTIVE
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holder_path(target: Path) -> Path:
    return Path(str(target) + ".holder.json")


def classify(target: Path) -> str:
    """The one line. Pure over the filesystem; main() just prints it."""
    hp = holder_path(target)
    if not hp.exists():
        return "NONE"
    try:
        holder = json.loads(hp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"UNKNOWN (holder file unreadable: {e})"
    pid = holder.get("pid")
    if not isinstance(pid, int):
        return "UNKNOWN (holder file has no integer pid)"
    purpose = holder.get("purpose") or "?"
    host = holder.get("hostname")
    if host and host != socket.gethostname():
        return f"FOREIGN {pid} {host}"
    return f"{'LIVE' if pid_alive(pid) else 'STALE'} {pid} {purpose}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="the guarded file (NOT the .holder.json)")
    ns = ap.parse_args(argv)
    print(classify(Path(ns.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
