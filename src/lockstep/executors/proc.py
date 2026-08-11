"""Subprocess handling shared by executors (SPEC §8.5, AMENDMENTS A5).

argv lists only, shell=False everywhere (SPEC §11). Timeouts kill the WHOLE
process tree: POSIX start_new_session + os.killpg; Windows
CREATE_NEW_PROCESS_GROUP + a Job Object, with taskkill /T /F as the fallback.
Both branches are implemented (A5: the Windows escape hatch is withdrawn —
this is the development platform).

The Windows Job Object is a containment primitive, not just a faster kill.
`taskkill /T` depends on two things a job does not: that the live parent-pid
table still describes the tree, and that the termination call is permitted.
Windows does NOT reparent orphans — a dead shim leaves its children pointing
at a pid that is dead and eventually recycled — so once the top process is
gone the walk either finds nothing or walks a stranger's tree. And in the
reported incidents the walk enumerated the chain correctly and then failed at
the termination call with ERROR_ACCESS_DENIED. A job needs neither: membership
is recorded by the kernel at assignment and survives the parent's death.

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE is the half that matters most: when the
driver dies — killed, crashed, or exited uncleanly — the last handle closes
and the KERNEL reaps the tree. No user-mode TerminateProcess call is made
from lockstep on that path, so it is not a call anything can deny. See
DEVIATIONS.md (2026-08-10) for the reported failure this closes.

A node's clean exit does NOT tear its job down: a process the node deliberately
left running survives into later nodes, as it would on POSIX, and is reaped
only when the driver exits (`_release_job_if_empty`). The guarantee is that
nothing outlives the RUN — not that nothing outlives its node.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path


class PathEscapeError(Exception):
    """A configured path resolves outside the repo root (SPEC §11)."""


class ArgvTooLong(OSError):
    """The assembled command line exceeds the platform limit.

    Subclasses OSError deliberately: both executors already funnel a failed
    spawn into RawResult(exit_code=127, error=...), so the guard rides the
    existing path instead of crashing the run.
    """


# Windows CreateProcess caps the ENTIRE command line at 32,767 chars. POSIX
# exec is bounded by ARG_MAX (typically ~2 MB); the conservative floor below
# is what a single argv element may safely carry there.
ARGV_LIMIT = 32_767 if sys.platform == "win32" else 2_097_152


def argv_overflow(argv: list[str]) -> str | None:
    """Diagnose an unspawnable command line, or None if it fits.

    r5 A2 makes a corrective prompt several times larger than the original
    (it embeds the original prompt AND the invalid output), so this is
    reachable on any node passing its prompt through argv — which is why the
    message names the remedy rather than only the limit.
    """
    # Each element costs its length plus a separator and two quotes once the
    # platform re-quotes the list into a single command line.
    total = sum(len(a) + 3 for a in argv)
    if total <= ARGV_LIMIT:
        return None
    worst = max(range(len(argv)), key=lambda i: len(argv[i])) if argv else 0
    return (
        f"assembled command line is {total} chars, over this platform's "
        f"{ARGV_LIMIT}-char limit (argv[{worst}] alone is {len(argv[worst])} chars); "
        f'set prompt_via = "stdin" on the executor stanza to pass the prompt '
        f"off the command line"
    )


def resolve_inside(repo_root: Path, rel: str) -> Path:
    """Resolve `rel` against repo_root; lexical + realpath containment check."""
    root = Path(repo_root).resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapeError(f"path {rel!r} resolves outside the repo root {root}")
    return candidate


# ------------------------------------------------------------------ Job Objects
# All seven calls are plain kernel32 exports, reached through ctypes so the
# Windows branch stays dependency-free (pydantic remains the only runtime
# dependency — see the working agreement in CLAUDE.md).

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectBasicAccountingInformation = 1
_JobObjectExtendedLimitInformation = 9
# OpenJobObjectW needs TERMINATE to kill, QUERY to ask whether anything is
# actually in the job, and SYNCHRONIZE for a well-formed handle; `lockstep
# cancel` asks for nothing more than it uses.
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_QUERY = 0x0004
_SYNCHRONIZE = 0x00100000


def _why(what: str) -> str:
    """Why a job call failed, for the run dir. The degradation path is silent by
    construction — every call just returns None/False — and on the machine class
    this whole mechanism exists for (a security product vetoing calls) an
    operator otherwise cannot tell the kernel guarantee from a silent fallback
    to the behaviour that was reported broken.

    Returned, never stashed in a module global: nodes and map items run
    concurrently on a thread pool, so a global would let one node's phase dir
    report another node's reason — and a wrong reason is worse than none, since
    this artifact exists precisely to stop an operator chasing the wrong cause.
    """
    err = ctypes.get_last_error() if sys.platform == "win32" else 0
    return f"{what} failed (GetLastError={err})"


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        # ULONG_PTR / SIZE_T: c_size_t so the struct lays out correctly on
        # both 32- and 64-bit. A wrong width here silently corrupts LimitFlags
        # of the NEXT field rather than failing loudly.
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    """The kernel32 handle, with argtypes bound. None off Windows."""
    if sys.platform != "win32":
        return None
    k = getattr(_kernel32, "_cached", None)
    if k is not None:
        return k
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.OpenJobObjectW.restype = ctypes.c_void_p
        k.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
        ]
        k.SetInformationJobObject.restype = ctypes.c_int
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.AssignProcessToJobObject.restype = ctypes.c_int
        k.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.TerminateJobObject.restype = ctypes.c_int
        k.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        k.QueryInformationJobObject.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None
    _kernel32._cached = k  # type: ignore[attr-defined]
    return k


def _create_job() -> tuple[tuple[int, str] | None, str]:
    """A named job that kills its members when its last handle closes.

    Returns (job, "") or (None, reason) — the reason travels with the call
    rather than through module state; see `_why`.

    Named, not anonymous, because `lockstep cancel` runs in a DIFFERENT
    process from the driver and cannot be handed a live handle. `Local\\` is
    the per-session namespace: cancel must run in the same logon session as
    the driver, which is true of every path lockstep offers (including the
    cockpit's detached runs, launched from the operator's own session). If it
    ever is not, OpenJobObjectW fails and the caller falls back to taskkill.
    """
    k = _kernel32()
    if k is None:
        return None, "kernel32 unavailable"
    name = f"Local\\lockstep-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    handle = k.CreateJobObjectW(None, name)
    if not handle:
        return None, _why("CreateJobObjectW")
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k.SetInformationJobObject(
        handle,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        reason = _why("SetInformationJobObject(KILL_ON_JOB_CLOSE)")
        # Without KILL_ON_JOB_CLOSE the job buys nothing the pid walk does not
        # already do, and it would silently NOT reap on driver death — which is
        # the guarantee callers will read into it. Refuse the half-job.
        k.CloseHandle(handle)
        return None, reason
    return (int(handle), name), ""


def _assign_job(handle: int, proc: subprocess.Popen) -> tuple[bool, str]:
    k = _kernel32()
    if k is None:
        return False, "kernel32 unavailable"
    # Popen._handle is a _winapi.Handle (an int subclass) owning the child
    # process handle; borrow it without taking ownership.
    raw = getattr(proc, "_handle", None)
    if raw is None:
        return False, "Popen exposed no process handle"
    if k.AssignProcessToJobObject(ctypes.c_void_p(handle), ctypes.c_void_p(int(raw))):
        return True, ""
    # ERROR_ACCESS_DENIED (5) here is normally one of: the process already
    # exited inside the assign window, or a nested job that forbids it.
    return False, _why("AssignProcessToJobObject")


def _close_job(proc: subprocess.Popen) -> None:
    """Drop the driver's handle. With KILL_ON_JOB_CLOSE this reaps whatever is
    left in the job, so it is only ever called once the node is finished."""
    job = getattr(proc, "_lockstep_job", None)
    if not job:
        return
    proc._lockstep_job = None  # type: ignore[attr-defined]
    k = _kernel32()
    if k is not None:
        k.CloseHandle(ctypes.c_void_p(job[0]))


def job_name_of(proc: subprocess.Popen) -> str | None:
    """The spawn's job name, or None when no job could be created."""
    job = getattr(proc, "_lockstep_job", None)
    return job[1] if job else None


def job_unavailable_reason(proc: subprocess.Popen) -> str:
    """Why THIS spawn got no job, for the run dir. '' when one was used."""
    return getattr(proc, "_lockstep_job_error", "")


def _job_active_processes(k, handle: int) -> int | None:
    """Live members of the job, or None if the query itself failed."""
    acct = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = ctypes.c_uint32(0)
    ok = k.QueryInformationJobObject(
        ctypes.c_void_p(handle),
        _JobObjectBasicAccountingInformation,
        ctypes.byref(acct),
        ctypes.sizeof(acct),
        ctypes.byref(returned),
    )
    return int(acct.ActiveProcesses) if ok else None


def _release_job_if_empty(proc: subprocess.Popen) -> None:
    """Close the job handle, but ONLY if nothing is still running in it.

    This is what keeps the success path symmetric with POSIX, where kill_tree
    fires on timeout and never on clean exit. A node that deliberately leaves a
    process running — a DuckDB connection holder shared by later nodes is the
    motivating case, and DuckDB's single-writer file lock makes getting this
    wrong expensive — keeps it, exactly as it would on Linux. Closing here
    unconditionally would have killed it the instant its node returned.

    What the job still buys on that path: the survivor is a job member, so the
    kernel reaps it when the driver exits and the last handle closes. It cannot
    outlive the run and become the unkillable orphan holding a database lock
    that this whole mechanism exists to prevent.

    Holding costs one kernel handle per node that leaves something behind. The
    empty case — every ordinary node — is closed immediately, which is what
    keeps that from growing with the length of the run. A REFUSED query counts
    as non-empty: never kill something because we could not ask about it.
    """
    job = getattr(proc, "_lockstep_job", None)
    if not job:
        return
    k = _kernel32()
    if k is not None and _job_active_processes(k, job[0]) == 0:
        _close_job(proc)


def _terminate_job_by_name(name: str) -> bool:
    """Terminate a named job. True ONLY if it actually had a live member.

    The membership check is the whole point, not a nicety: TerminateJobObject
    returns TRUE for a job with zero live processes, and `lockstep cancel`
    reads a True as "a kill was issued" and therefore KEEPS its CANCELLED
    marker. A node that had just finished successfully would be rewritten as
    failed(cancelled) and its result discarded — inverting the exact outcome
    cmd_cancel's marker-unlink exists to prevent. The pid path could not do
    this: taskkill on a dead pid fails, which is what that guard relied on.
    """
    k = _kernel32()
    if k is None:
        return False
    handle = k.OpenJobObjectW(
        _JOB_OBJECT_TERMINATE | _JOB_OBJECT_QUERY | _SYNCHRONIZE, 0, name
    )
    if not handle:
        return False
    try:
        active = _job_active_processes(k, int(handle))
        if active == 0:
            # Genuinely empty: not a kill. The caller falls through to taskkill,
            # which reports honestly against the recorded pid.
            return False
        # active is None => the QUERY itself was refused. Do NOT treat that as
        # empty: a denied query is the signature of the very machine this
        # mechanism exists for, and forfeiting the kill there would be exactly
        # backwards. Terminate and report what it says.
        return bool(k.TerminateJobObject(ctypes.c_void_p(handle), 1))
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


def spawn(
    argv: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    overflow = argv_overflow(argv)
    if overflow is not None:
        # Fail BEFORE the platform does: CreateProcess reports a generic
        # parameter error that says nothing about which knob fixes it.
        raise ArgvTooLong(overflow)
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
    # Created after the log files (whose open() can raise — this machine's AV
    # does exactly that transiently — and would otherwise strand the handle for
    # the driver's lifetime) but before the spawn, so assignment is the very
    # next thing that happens. Assignment is not atomic with CreateProcess:
    # Popen closes the child's thread handle before returning, so
    # CREATE_SUSPENDED + ResumeThread is out of reach without a Toolhelp thread
    # walk. Measured at ~17 µs against the ~2 ms Popen itself takes. Accepted,
    # not overlooked: a descendant born inside that window is in no job, and
    # since its parent dies with the job the pid walk cannot reach it either —
    # it is uncovered, not covered by the fallback.
    job, why = _create_job()
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
    except BaseException:
        # NOT `except OSError`: Popen also raises ValueError (e.g. "embedded
        # null character") for an argv a prompt can carry, and that leaked one
        # KILL_ON_JOB_CLOSE job per attempt, held for the driver's lifetime.
        stdout_f.close()
        stderr_f.close()
        if job is not None:
            k = _kernel32()
            if k is not None:
                k.CloseHandle(ctypes.c_void_p(job[0]))  # empty job; kills nothing
        raise
    proc._lockstep_files = (stdout_f, stderr_f)  # type: ignore[attr-defined]
    proc._lockstep_job = None  # type: ignore[attr-defined]
    if job is not None:
        assigned, why = _assign_job(job[0], proc)
        if assigned:
            proc._lockstep_job = job  # type: ignore[attr-defined]
        else:
            # Nested inside a job that forbids it, or a policy denial. Not
            # fatal: the taskkill path is exactly what shipped before.
            k = _kernel32()
            if k is not None:
                k.CloseHandle(ctypes.c_void_p(job[0]))
    proc._lockstep_job_error = why  # type: ignore[attr-defined]
    return proc


def record_spawn_handles(phase_dir: Path, proc: subprocess.Popen) -> None:
    """Write the handles `lockstep cancel` needs, plus why a job was not used.

    Best-effort, like the attempt-rotation it sits beside: this runs in the
    window between spawn() and wait_or_kill(), where an escaping OSError would
    abandon a LIVE child — leaving it unwaited, its log files open, and its job
    handle held for the driver's lifetime. This machine's AV throws transient
    PermissionError on exactly this kind of write. Losing the artifacts only
    costs `cancel` its handles, which it reports honestly (exit 7); losing the
    child costs the run.

    `job_name.txt` is REMOVED when this spawn got no job, not merely left
    unwritten: a retry whose assignment failed would otherwise leave the
    previous attempt's name next to the current attempt's pid, and cancel
    prefers the name.

    The job files are reconciled BEFORE `pid.txt` is written, so a write that
    fails part-way can only ever leave `cancel` with fewer handles than it
    wants — never with a fresh pid beside a previous attempt's job name, which
    would point it at a different object than the node it was asked to kill.
    """
    try:
        jn = job_name_of(proc)
        job_file = phase_dir / "job_name.txt"
        note = phase_dir / "job-unavailable.txt"
        if jn:
            job_file.write_text(jn, encoding="utf-8")
            note.unlink(missing_ok=True)
        else:
            job_file.unlink(missing_ok=True)
            if sys.platform == "win32":
                # The degradation is otherwise invisible, and on the machine
                # class this exists for it is the thing an operator most needs
                # to know: this node fell back to the mechanism reported broken.
                note.write_text(
                    job_unavailable_reason(proc) or "no job object", encoding="utf-8"
                )
        (phase_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree, grandchildren included."""
    if proc.poll() is not None:
        _close_job(proc)
        return
    if sys.platform == "win32":
        # BOTH mechanisms, pid walk first, unconditionally. Order and
        # unconditionality are both load-bearing:
        #   - The walk is only useful while the top process is alive, so it has
        #     to run before the job terminate kills it. This is the one moment
        #     a descendant that escaped the assign window is reachable at all.
        #   - Gating the walk on "the job terminate failed" made it unreachable
        #     in exactly the case where an escapee can exist, so the mitigation
        #     documented for that window existed in neither half.
        # taskkill costs ~50 ms against an already-dead tree, and kills are rare.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            shell=False,
        )
        job = getattr(proc, "_lockstep_job", None)
        if job:
            k = _kernel32()
            if k is not None:
                k.TerminateJobObject(ctypes.c_void_p(job[0]), 1)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        _close_job(proc)


def kill_pid_tree(pid: int, job_name: str | None = None) -> bool:
    """Kill a process tree by pid — `lockstep cancel` (r6 C3), which runs in a
    DIFFERENT process from the driver and has only what the phase dir recorded.
    Same platform mechanics as kill_tree. Returns True if a kill was issued.

    `job_name` is the recorded Job Object name (`job_name.txt`) when the spawn
    got one. It needs no live parent-pid table and no permitted TerminateProcess
    call against each member, which is why it is tried at all; absent, stale, or
    unopenable, this is exactly the taskkill path that shipped.

    Both run, for the reason kill_tree runs both. The return value is what
    cmd_cancel keys its CANCELLED marker off, so it must mean "something live
    was killed" and nothing weaker — see _terminate_job_by_name.
    """
    if sys.platform == "win32":
        # pid walk first, for kill_tree's reason: it is only useful while the
        # top process is alive.
        r = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, shell=False
        )
        job_killed = bool(job_name) and _terminate_job_by_name(job_name)
        return job_killed or r.returncode == 0
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
        # NOT an unconditional close: a node may deliberately leave a process
        # running for later nodes, and on POSIX it would survive. Reclaim the
        # handle when the job is empty, hold it when it is not.
        _release_job_if_empty(proc)
    return (proc.returncode if proc.returncode is not None else -1), timed_out
