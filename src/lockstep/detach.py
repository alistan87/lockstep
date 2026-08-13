"""`--detach`: hand the run to a process that outlives this one.

Consumer report 2026-08-13, item 3. A long run has to outlive the shell that
started it, and until now every caller reimplemented that themselves. On
Windows, under an agent harness whose tool calls run in Git-Bash, the usual
POSIX incantations do not do it: `nohup` blocks SIGHUP and nothing else, and
the whole process tree is torn down when the tool call ends. The reported
result was a driver killed 2.5 minutes into a 40-minute node, with `state.json`
saying `running` for another 97 minutes, because the only process that could
have corrected it was the one that died.

Two platform facts decide the implementation:

- Windows: a job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE kills every
  member when the last handle closes — which is exactly how a harness cleans up
  a tool call, and exactly what killed the run. `CREATE_BREAKAWAY_FROM_JOB` is
  the only escape, and it fails outright when the job forbids breakaway, so it
  is attempted and then dropped rather than assumed. `DETACHED_PROCESS` on top
  means no console to be torn down with.
- POSIX: `start_new_session=True` (setsid) puts the child in its own session
  and process group, so neither a SIGHUP to the old group nor the terminal
  closing reaches it. That is the double-fork's actual purpose, without the
  fork.

Deliberately NOT `executors/proc.spawn`: that puts its child in a
kill-on-close job on purpose (nothing outlives the run). This is the one spawn
in the codebase whose entire point is to outlive its parent.

stdin is the null device, always. A detached run must never sit at an approval
prompt: non-TTY stdin auto-rejects with exit 6, which is the documented handoff
signal (COCKPIT-THEORY-OF-OPERATIONS), and a run waiting on a prompt nobody can
see is the failure this replaces.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .state import inspect_lock

# CreateProcess flags subprocess does not name.
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def driver_argv(args: list[str]) -> list[str]:
    """The child command. `-m lockstep` rather than `sys.argv[0]`: the console
    script is not always on PATH (and under pytest argv[0] is pytest), while
    the interpreter running us can always import the package it is running."""
    return [sys.executable, "-m", "lockstep", *args]


def spawn_detached(argv: list[str], *, cwd: Path, log_path: Path) -> tuple[subprocess.Popen, str]:
    """(process, note). `note` names any containment we could NOT escape, so
    the caller can print it — a detach that silently degrades to "dies with the
    parent" is worse than one that says so."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    # ONE handle for both streams: two handles onto the same file interleave by
    # buffer flush, which shreds tracebacks.
    log = open(log_path, "ab")
    try:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
            )
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                shell=False, **kwargs,
            )
        except OSError:
            if sys.platform != "win32":
                raise
            # The job forbids breakaway (CreateProcess reports ACCESS_DENIED).
            # Detaching the console is still worth having; say what we lost.
            kwargs["creationflags"] = DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                shell=False, **kwargs,
            )
            note = (
                "this process is inside a job object that forbids breakaway — the detached "
                "run will still be killed when that job closes (typically when the calling "
                "tool call or shell session ends)"
            )
    finally:
        log.close()  # the child holds its own duplicate
    return proc, note


def _journal_bytes(run_dir: Path | None) -> int:
    """Bytes of the run's journal, or -1. A driver that starts a run appends to
    `events.jsonl`; one that never got the lock does not."""
    if run_dir is None:
        return -1
    try:
        return (run_dir / "events.jsonl").stat().st_size
    except OSError:
        return -1


def mark(run_dir: Path | None) -> tuple[int, int | None]:
    """(journal bytes, lock-holder pid) — what was true BEFORE the spawn.

    Captured by the caller before spawning, never after: a fast child can take
    the lock and finish before the first statement of `await_start` runs, and a
    "before" measured then would mistake the child's own work for pre-existing
    state and report a successful launch as a failed one.
    """
    return _journal_bytes(run_dir), (inspect_lock(run_dir).pid if run_dir is not None else None)


def await_start(
    proc: subprocess.Popen,
    locate,
    *,
    pre: Path | None,
    before: tuple[int, int | None],
    timeout: float = 20.0,
    poll: float = 0.1,
) -> tuple[Path | None, int | None, bool]:
    """(run_dir, child exit code or None, confirmed).

    `confirmed` means the child really became this run's driver, not merely
    that a process was spawned. Four sufficient proofs, because a run can be
    over before the first poll — a fake-executor flow finishes in
    milliseconds, and reporting that as a failed launch would be worse than
    saying nothing:

      * a run dir exists where none did before (it created one);
      * the lock changed hands — nobody held it, or a different pid does now;
      * that pid is the one we spawned (only where the interpreter is not a
        launcher shim, which is why it cannot be the only proof);
      * the journal of the dir we already knew about grew (it ran and exited).

    The lock-changed-hands proof is what makes a failed launch distinguishable:
    when another driver already holds the lock, the holder is unchanged, the
    journal does not move, and the child exits 8 — none of the four fire.

    A launch that genuinely failed — a lock held by someone else, a bad config
    — satisfies none of them, and is reported to the caller's terminal instead
    of only to a log file nobody was told to read.

    `pre`/`before` come from `mark()`, captured by the caller BEFORE the spawn.
    """
    before_bytes, held_by = before

    def confirm(run_dir: Path | None) -> bool:
        if run_dir is None:
            return False
        if run_dir != pre:
            return True
        now = inspect_lock(run_dir)
        if now.state == "alive" and now.pid != held_by:
            return True
        return now.pid == proc.pid or _journal_bytes(run_dir) > before_bytes

    deadline = time.monotonic() + timeout
    while True:
        run_dir = locate()
        if confirm(run_dir):
            return run_dir, proc.poll(), True
        code = proc.poll()
        if code is not None:
            # It is gone. One last look — the exit and the final journal write
            # are not simultaneous from here.
            run_dir = locate()
            return run_dir, code, confirm(run_dir)
        if time.monotonic() >= deadline:
            return run_dir, None, False
        time.sleep(poll)


def tail(path: Path, lines: int = 15) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []
