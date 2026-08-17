#!/usr/bin/env python
"""lane.py — one worktree, one detached lockstep run, one durable record.

    python contrib/lane.py start <flow.tg.json> [--branch NAME] [--arg k=v ...]
    python contrib/lane.py harvest <worktree> [--patch FILE]
    python contrib/lane.py abandon <worktree> [--force]

The fleet recipe (concurrent-orchestration work order, Batch 2), mechanized so
no agent improvises it. `start` creates a fresh worktree on its own branch,
verifies the flow against it, launches a DETACHED run whose runs dir stays in
the MAIN repo (the cockpit, seeds and session_spend keep seeing everything),
confirms which run dir is actually ours, and persists the lane record —
`<worktree>/.lockstep-lane.json` — that every later step keys on. `harvest`
refuses while the driver lives, commits the branch (lane record excluded),
and removes the worktree. `abandon` is the explicit destructive sibling.

Why `start` does not trust `--detach`'s own output: the detach parent locates
its run by NEWEST (flow_hash, args) match in the shared runs dir, so two lanes
starting the same flow near-simultaneously could each print the other's run
dir. `start` therefore holds a start-lock (a lockfile under the main runs dir)
across launch+confirm, and cross-checks the new run dir's lock holder against
the printed driver pid. Mismatch = abort loudly, kill the driver, remove the
worktree.

Known gotchas this file owns so agents don't:
  - gitignored `.venv` / `lockstep.toml` / `runs/` do not exist in a fresh
    worktree — every path handed to the driver is absolute, the config is the
    MAIN repo's, and the binary is the main venv's.
  - the detached driver re-runs the argv with cwd = wherever this script ran,
    which is another reason every path is absolute.
  - this machine's AV throws transient PermissionError on file replaces and
    git object writes — worktree add/remove retry once.
  - `gc.auto` should be 0 in the main repo while fleets run (work order §6.3:
    recorded snapshot trees are unreferenced loose objects); `start` warns
    when it is not.

Exit codes (this tool's own; NOT lockstep's frozen set): 0 ok, 1 refusal or
failure (the message says which), 2 missing/unreadable lane record.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# Same-file convention as who_holds.py; import kept file-local so lane.py can
# be copied alone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from who_holds import pid_alive  # noqa: E402

LANE_RECORD = ".lockstep-lane.json"
START_LOCK = "lane-start.lock"


# ------------------------------------------------------------------ plumbing

def _utcstamp() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(name: str) -> str:
    # Mirrors lockstep.state._slug so the run-dir prefix filter matches.
    return re.sub(r"[^a-z0-9-]", "-", name.lower()) or "flow"


def _git(repo: Path, *args: str, retry: bool = False) -> subprocess.CompletedProcess:
    """Run git; with retry=True, retry ONCE on failure (the AV quirk hits
    worktree add/remove through git's object and file replaces)."""
    for attempt in (0, 1):
        cp = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, shell=False
        )
        if cp.returncode == 0 or not retry or attempt == 1:
            return cp
        time.sleep(1.0)
    return cp  # pragma: no cover — loop always returns


def _lockstep_argv(main_repo: Path, override: str | None) -> list[str]:
    """The driver command. Priority: explicit override (tests, odd layouts) >
    the main venv's console script > this interpreter's `-m lockstep`."""
    if override:
        # posix=False: POSIX-mode shlex eats Windows backslashes. Non-POSIX
        # mode keeps quote characters in the token, so strip them — a quoted
        # spaced path survives both.
        return [tok.strip('"') for tok in shlex.split(override, posix=False)]
    for candidate in (
        main_repo / ".venv" / "Scripts" / "lockstep.exe",  # Windows venv
        main_repo / ".venv" / "bin" / "lockstep",  # POSIX venv
    ):
        if candidate.exists():
            return [str(candidate)]
    return [sys.executable, "-m", "lockstep"]


def _kill_pid_tree(pid: int) -> None:
    """Best-effort containment for an aborted launch. taskkill /T /F first and
    unconditionally on Windows — same reasoning as executors/proc.kill_tree."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, shell=False
        )
    else:
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _run_dirs(runs_dir: Path) -> set[str]:
    if not runs_dir.is_dir():
        return set()
    return {d.name for d in runs_dir.iterdir() if (d / "state.json").exists()}


class LaneError(Exception):
    """A refusal or failure whose message is the whole diagnosis."""


class RecordMissing(Exception):
    """No .lockstep-lane.json — exit 2, distinct from a refusal (exit 1)."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """subprocess.run that turns a missing binary into a named LaneError
    instead of leaking WinError 2 up as if a lane record were missing."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, shell=False)
    except FileNotFoundError:
        raise LaneError(f"driver command not found: {cmd[0]!r}")


# ---------------------------------------------------------------- start lock

def _acquire_start_lock(runs_dir: Path, timeout: float) -> Path:
    """Serialize `lane.py start` across processes for the seconds a launch
    takes. O_CREAT|O_EXCL with pid, stale-cleared when the pid is dead — the
    same shape as lockstep's run lock, same-host only (lanes are one machine)."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    lock = runs_dir / START_LOCK
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "started": _utcstamp()}).encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                holder = json.loads(lock.read_text(encoding="utf-8"))
                holder_pid = holder.get("pid")
            except (OSError, ValueError):
                holder_pid = None
            if isinstance(holder_pid, int) and not pid_alive(holder_pid):
                lock.unlink(missing_ok=True)  # stale: a dead lane.py
                continue
            if time.monotonic() >= deadline:
                raise LaneError(
                    f"another `lane.py start` holds {lock} (pid {holder_pid}) and did "
                    f"not finish within {timeout:.0f}s — launches serialize on purpose; "
                    f"wait or investigate"
                )
            time.sleep(0.5)


# --------------------------------------------------------------------- start

def cmd_start(ns) -> int:
    main_repo = Path(ns.main_repo).resolve()
    if not (main_repo / ".git").exists():
        raise LaneError(f"--main-repo {main_repo} is not a git repo")
    flow_path = Path(ns.flow).resolve()
    if not flow_path.is_file():
        raise LaneError(f"flow not found: {flow_path}")
    try:
        flow_name = json.loads(flow_path.read_text(encoding="utf-8")).get("name") or "flow"
    except ValueError as e:
        raise LaneError(f"flow is not JSON: {flow_path}: {e}")

    exe = _lockstep_argv(main_repo, ns.lockstep_exe)
    runs_dir = Path(ns.runs_dir).resolve() if ns.runs_dir else main_repo / "runs"
    config = main_repo / "lockstep.toml"
    config_args = ["--config", str(config)] if config.is_file() else []
    if not config_args:
        print(f"warning: {config} not found — the driver will use built-in defaults",
              file=sys.stderr)

    gc_auto = _git(main_repo, "config", "--get", "gc.auto").stdout.strip()
    if gc_auto != "0":
        print("warning: gc.auto is not 0 in the main repo — recorded snapshot trees are "
              "unreferenced loose objects, and auto-gc during a fleet can prune them "
              "(work order §6.3: `git config gc.auto 0`)", file=sys.stderr)

    branch = ns.branch or f"lane/{_slug(flow_name)}-{_utcstamp()}"
    lanes_root = (
        Path(ns.worktrees).resolve() if ns.worktrees
        else main_repo.parent / f"{main_repo.name}-lanes"
    )
    worktree = lanes_root / branch.replace("/", os.sep)
    if worktree.exists():
        raise LaneError(f"worktree path already exists: {worktree}")

    lock = _acquire_start_lock(runs_dir, ns.lock_timeout)
    driver_pid: int | None = None
    worktree_made = False
    try:
        before = _run_dirs(runs_dir)
        cp = _git(main_repo, "worktree", "add", "-b", branch, str(worktree), retry=True)
        if cp.returncode != 0:
            raise LaneError(f"git worktree add failed: {cp.stderr.strip()}")
        worktree_made = True

        cp = _run([*exe, "verify", str(flow_path), "--repo-root", str(worktree), *config_args])
        if cp.returncode != 0:
            raise LaneError(
                f"verify failed (exit {cp.returncode}) — nothing was launched:\n"
                f"{cp.stdout.strip()}\n{cp.stderr.strip()}"
            )

        launch = [
            *exe, "run", str(flow_path),
            "--repo-root", str(worktree), *config_args,
            "--runs-dir", str(runs_dir), "--fresh", "--detach",
        ]
        for kv in ns.arg or []:
            launch += ["--arg", kv]
        if ns.max_workers is not None:
            launch += ["--max-workers", str(ns.max_workers)]
        cp = _run(launch)
        if cp.returncode != 0:
            raise LaneError(
                f"detached launch failed (exit {cp.returncode}):\n"
                f"{cp.stdout.strip()}\n{cp.stderr.strip()}"
            )
        printed_run = printed_pid = None
        for line in cp.stdout.splitlines():
            line = line.strip()
            if line.startswith("run dir:"):
                printed_run = line.split(":", 1)[1].strip()
            elif line.startswith("driver pid:"):
                printed_pid = int(line.split(":", 1)[1].strip())

        # Confirm which run dir is OURS — by listing diff under the start-lock,
        # never by trusting the parent's newest-match lookup alone.
        prefix = f"{_slug(flow_name)}-"
        candidate: Path | None = None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            fresh = [n for n in _run_dirs(runs_dir) - before if n.startswith(prefix)]
            if len(fresh) > 1:
                raise LaneError(
                    f"ambiguous launch: {len(fresh)} new run dirs appeared for "
                    f"{flow_name!r} ({', '.join(sorted(fresh))}) — something else is "
                    f"launching this flow outside lane.py; aborting"
                )
            if fresh:
                candidate = runs_dir / fresh[0]
                break
            time.sleep(0.5)
        if candidate is None:
            raise LaneError(
                f"the detached driver never registered a run dir under {runs_dir} "
                f"within 15s — see the newest detached-*.log there"
            )
        if printed_run and Path(printed_run).name != candidate.name:
            driver_pid = printed_pid
            raise LaneError(
                f"cross-wired lane: --detach printed {Path(printed_run).name} but the "
                f"listing diff says {candidate.name} — aborting rather than filing a "
                f"record that points at someone else's run"
            )
        lock_file = candidate / "lock"
        if lock_file.exists():
            try:
                lock_pid = json.loads(lock_file.read_text(encoding="utf-8")).get("pid")
            except (OSError, ValueError):
                lock_pid = None
            if printed_pid is not None and lock_pid is not None and lock_pid != printed_pid:
                driver_pid = printed_pid
                raise LaneError(
                    f"cross-wired lane: run dir {candidate.name} is locked by pid "
                    f"{lock_pid} but --detach printed driver pid {printed_pid} — aborting"
                )
            driver_pid = lock_pid or printed_pid
        else:
            driver_pid = printed_pid  # already finished — a fast flow is not an error

        record = {
            "worktree": str(worktree),
            "branch": branch,
            "run_dir": str(candidate),
            "driver_pid": driver_pid,
            "flow": str(flow_path),
            "flow_name": flow_name,
            "args": list(ns.arg or []),
            "started": _utcstamp(),
        }
        (worktree / LANE_RECORD).write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        print(f"lane up. attention: pwsh -File contrib\\attention.ps1 -RunDir {candidate}",
              file=sys.stderr)
        print(f"         block:     {' '.join(exe)} wait {candidate}", file=sys.stderr)
        print(json.dumps(record))  # the one machine line, last on stdout
        return 0
    except LaneError:
        if driver_pid:
            _kill_pid_tree(driver_pid)
        if worktree_made:
            _git(main_repo, "worktree", "remove", "--force", str(worktree), retry=True)
            _git(main_repo, "branch", "-D", branch)
        raise
    finally:
        lock.unlink(missing_ok=True)


# ------------------------------------------------------------------- harvest

def _read_record(worktree: Path) -> dict:
    path = worktree / LANE_RECORD
    if not path.is_file():
        raise RecordMissing(
            f"{path} not found — not a lane worktree (or the record was deleted); "
            f"`lane.py start` writes it, and harvest/abandon key on it"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _driver_alive(record: dict) -> int | None:
    """The live driver's pid, else None. The run lock is the truth (the
    recorded driver_pid is only a cross-check — drivers exit, pids recycle)."""
    run_dir = Path(record["run_dir"])
    lock = run_dir / "lock"
    if not lock.exists():
        return None
    try:
        pid = json.loads(lock.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        return None
    if isinstance(pid, int) and pid_alive(pid):
        return pid
    return None


def cmd_harvest(ns) -> int:
    worktree = Path(ns.worktree).resolve()
    record = _read_record(worktree)
    pid = _driver_alive(record)
    if pid is not None:
        raise LaneError(
            f"the run's driver (pid {pid}) is still alive — a harvest under a live "
            f"driver would commit a tree it is still writing; `lockstep wait "
            f"{record['run_dir']}` first"
        )
    main_repo = Path(ns.main_repo).resolve()

    _git(worktree, "add", "-A")
    _git(worktree, "reset", "-q", "--", LANE_RECORD)  # the record is lane plumbing
    staged = _git(worktree, "diff", "--cached", "--quiet").returncode != 0
    commit_sha = None
    patch_path = None
    if staged:
        if ns.patch:
            patch_path = str(Path(ns.patch).resolve())
            diff = _git(worktree, "diff", "--cached")
            Path(patch_path).write_text(diff.stdout, encoding="utf-8")
            _git(worktree, "reset", "-q")
        else:
            msg = f"lane harvest: {record.get('flow_name', '?')} ({Path(record['run_dir']).name})"
            cp = _git(worktree, "commit", "-q", "-m", msg)
            if cp.returncode != 0:
                raise LaneError(f"commit failed: {cp.stderr.strip()}")
            commit_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    (worktree / LANE_RECORD).unlink(missing_ok=True)

    dirty = _git(worktree, "status", "--porcelain").stdout.strip()
    if dirty and not ns.patch:
        raise LaneError(
            f"worktree still dirty after commit — refusing to remove it:\n{dirty}"
        )
    cp = _git(main_repo, "worktree", "remove",
              *(["--force"] if ns.patch else []), str(worktree), retry=True)
    if cp.returncode != 0:
        raise LaneError(f"git worktree remove failed: {cp.stderr.strip()}")
    print(json.dumps({
        "harvested": True, "branch": record["branch"], "commit": commit_sha,
        "patch": patch_path, "run_dir": record["run_dir"],
        "note": None if staged else "nothing to commit — the run wrote no tracked changes",
    }))
    return 0


# ------------------------------------------------------------------- abandon

def cmd_abandon(ns) -> int:
    worktree = Path(ns.worktree).resolve()
    main_repo = Path(ns.main_repo).resolve()
    try:
        record = _read_record(worktree)
    except (RecordMissing, ValueError):
        record = {}
    branch = record.get("branch") or _git(worktree, "branch", "--show-current").stdout.strip()
    if record:
        pid = _driver_alive(record)
        if pid is not None:
            if not ns.force:
                raise LaneError(
                    f"the run's driver (pid {pid}) is still alive — `lockstep cancel` "
                    f"or wait it out, or pass --force to kill it"
                )
            _kill_pid_tree(pid)
            time.sleep(1.0)
    print(f"abandoning: worktree {worktree}" + (f", branch {branch}" if branch else "")
          + (f"; run dir {record['run_dir']} is KEPT (gc owns run retention)"
             if record.get("run_dir") else ""))
    cp = _git(main_repo, "worktree", "remove", "--force", str(worktree), retry=True)
    if cp.returncode != 0:
        raise LaneError(f"git worktree remove failed: {cp.stderr.strip()}")
    if branch:
        _git(main_repo, "branch", "-D", branch)
    return 0


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("start", help="worktree + verify + detached run + lane record")
    ps.add_argument("flow")
    ps.add_argument("--branch", default=None)
    ps.add_argument("--arg", action="append", metavar="k=v",
                    help="forwarded to the driver (the flow must declare the arg)")
    ps.add_argument("--main-repo", default=".")
    ps.add_argument("--worktrees", default=None,
                    help="lanes root (default: <main>-lanes beside the main repo)")
    ps.add_argument("--runs-dir", default=None, help="default: <main>/runs")
    ps.add_argument("--max-workers", type=int, default=None)
    ps.add_argument("--lockstep-exe", default=None,
                    help="override the driver command (tests; odd layouts)")
    ps.add_argument("--lock-timeout", type=float, default=120.0, help=argparse.SUPPRESS)
    ps.set_defaults(fn=cmd_start)

    ph = sub.add_parser("harvest", help="commit the branch and remove the worktree")
    ph.add_argument("worktree")
    ph.add_argument("--main-repo", default=".")
    ph.add_argument("--patch", default=None,
                    help="export staged changes to FILE instead of committing")
    ph.set_defaults(fn=cmd_harvest)

    pa = sub.add_parser("abandon", help="delete the worktree AND its branch")
    pa.add_argument("worktree")
    pa.add_argument("--main-repo", default=".")
    pa.add_argument("--force", action="store_true", help="kill a live driver first")
    pa.set_defaults(fn=cmd_abandon)

    ns = ap.parse_args(argv)
    try:
        return ns.fn(ns)
    except RecordMissing as e:
        print(f"lane: {e}", file=sys.stderr)
        return 2
    except LaneError as e:
        print(f"lane: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
