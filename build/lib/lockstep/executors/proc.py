"""Subprocess handling shared by executors (SPEC §8.5, AMENDMENTS A5).

argv lists only, shell=False everywhere (SPEC §11). Timeouts kill the WHOLE
process tree: POSIX start_new_session + os.killpg; Windows
CREATE_NEW_PROCESS_GROUP + taskkill /T /F. Both branches are implemented (A5:
the Windows escape hatch is withdrawn — this is the development platform).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


class PathEscapeError(Exception):
    """A configured path resolves outside the repo root (SPEC §11)."""


def resolve_inside(repo_root: Path, rel: str) -> Path:
    """Resolve `rel` against repo_root; lexical + realpath containment check."""
    root = Path(repo_root).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapeError(f"path {rel!r} resolves outside the repo root {root}")
    return candidate


def spawn(
    argv: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    stdout_f = open(stdout_path, "wb")
    stderr_f = open(stderr_path, "wb")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=stdout_f,
            stderr=stderr_f,
            shell=False,
            **kwargs,
        )
    except OSError:
        stdout_f.close()
        stderr_f.close()
        raise
    proc._lockstep_files = (stdout_f, stderr_f)  # type: ignore[attr-defined]
    return proc


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree, grandchildren included."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            shell=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def kill_pid_tree(pid: int) -> bool:
    """Kill a process tree by pid — `lockstep cancel` (r6 C3), which runs in a
    DIFFERENT process from the driver and has only the recorded pid. Same
    platform mechanics as kill_tree. Returns True if a kill was issued."""
    if sys.platform == "win32":
        r = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, shell=False
        )
        return r.returncode == 0
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def wait_or_kill(proc: subprocess.Popen, timeout_s: int, stdin_text: str | None = None) -> tuple[int, bool]:
    """Returns (exit_code, timed_out)."""
    timed_out = False
    try:
        proc.communicate(
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
    finally:
        for f in getattr(proc, "_lockstep_files", ()):
            f.close()
    return (proc.returncode if proc.returncode is not None else -1), timed_out
