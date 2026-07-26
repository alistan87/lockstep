"""Run state: records, hashing, tree-fingerprint storage, events.jsonl, lockfile
(SPEC §9.2, §10; AMENDMENTS A3, M3, M7).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

NodeStatus = Literal["pending", "running", "done", "failed", "skipped", "blocked"]


def utcnow() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- hashing (AMENDMENTS M3) ---------------------------------------------------

def _lp(part: str) -> str:
    """Length-prefix a hash part; removes concatenation ambiguity."""
    return f"{len(part)}:{part}"


def compose_hash(role: str, kind: str, contract: str | None, parts: list[str]) -> str:
    """input_hash = sha256(role + kind + contract + join(sorted(fingerprint_parts))),
    each component length-prefixed and NUL-joined."""
    pieces = [_lp(role), _lp(kind), _lp(contract or "")] + [_lp(p) for p in sorted(parts)]
    return hashlib.sha256("\x00".join(pieces).encode("utf-8")).hexdigest()


# --- records (SPEC §10.2, AMENDMENTS A3) ---------------------------------------

class ItemRecord(BaseModel):
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    input_hash: str | None = None
    result_path: str | None = None
    attempts: int = 0
    error: str | None = None


class PhaseRecord(BaseModel):
    node_id: str
    role: str
    kind: str
    status: NodeStatus = "pending"
    input_hash: str | None = None
    workspace_fingerprint: str | None = None
    started_at: str | None = None  # ISO 8601 UTC
    ended_at: str | None = None
    attempts: int = 0
    heal_round: int = 0
    result_path: str | None = None
    error: str | None = None
    items: dict[str, ItemRecord] = {}  # map role: zero-based index -> record


class RunState(BaseModel):
    schema_version: str = "1.0"
    flow_name: str
    flow_hash: str
    format_version: str
    args: dict[str, str]
    nodes: dict[str, PhaseRecord]
    verdicts: dict[str, str] = {}  # gate node_id -> "pass" | "block: <reason>"
    token_spawns: int = 0
    started_at: str
    # Lineage head detail (AMENDMENTS M7): path -> content hash at last completion,
    # so the resume warning can NAME externally-changed paths, not just a digest.
    fingerprint_detail: dict[str, str] = {}
    workspace_kind: str = "git"  # "git" | "null" (M6: null ⇒ detection off)


# --- events.jsonl --------------------------------------------------------------

_events_lock = threading.Lock()


def append_event(run_dir: Path, event: dict) -> None:
    event = {"ts": utcnow(), "kind": event.pop("kind", "transition"), **event}
    line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    with _events_lock:
        with open(run_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_events(run_dir: Path) -> list[dict]:
    """Readers tolerate a trailing partial line after a crash (SPEC §10.3)."""
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue  # trailing partial line: skip, never error
            raise
    return out


def emit_span(record: PhaseRecord) -> None:
    """No-op OpenTelemetry seam (SPEC §10.3). A tracing backend maps:
    lockstep.run_id, lockstep.node_id, lockstep.role, lockstep.kind,
    lockstep.input_hash, status, duration (started_at..ended_at), attempts."""


# --- state.json ----------------------------------------------------------------

def write_state(run_dir: Path, state: RunState) -> None:
    """Atomic: temp + os.replace. Retried: on Windows, AV/indexer scans can hold
    the target briefly and fail the replace with a transient PermissionError."""
    tmp = run_dir / "state.json.tmp"
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    import time

    for attempt in range(5):
        try:
            os.replace(tmp, run_dir / "state.json")
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_state(run_dir: Path) -> RunState:
    return RunState.model_validate_json((Path(run_dir) / "state.json").read_text(encoding="utf-8"))


# --- mailbox (SPEC §16.2, AMENDMENTS-r6 C2) ------------------------------------

_mailbox_lock = threading.Lock()


def mailbox_path(run_dir: Path, node_id: str) -> Path:
    return Path(run_dir) / "mailbox" / f"{node_id}.jsonl"


def append_steer(run_dir: Path, node_id: str, message: str, author: str = "local-user") -> None:
    line = json.dumps(
        {"ts": utcnow(), "author": author, "message": message, "consumed": False},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    with _mailbox_lock:
        path = mailbox_path(run_dir, node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_mailbox(run_dir: Path, node_id: str) -> list[dict]:
    """All messages, file order; tolerant of a trailing partial line."""
    path = mailbox_path(run_dir, node_id)
    if not path.exists():
        return []
    out: list[dict] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue
            raise
    return out


def mark_mailbox_consumed(run_dir: Path, node_id: str) -> None:
    """Bookkeeping (C2): records when messages first entered a spawn. The
    steering block always renders the WHOLE mailbox, so this flag never
    affects the rendered prompt or the hash — it drives resume re-marking."""
    with _mailbox_lock:
        messages = read_mailbox(run_dir, node_id)
        if not any(not m.get("consumed") for m in messages):
            return
        for m in messages:
            m["consumed"] = True
        path = mailbox_path(run_dir, node_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            "".join(json.dumps(m, separators=(",", ":"), ensure_ascii=False) + "\n" for m in messages),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def render_steering(messages: list[dict]) -> str:
    """The C2 steering block. Operator instruction — deliberately NOT
    data-fenced; unlike interpolated content it is meant to be followed."""
    if not messages:
        return ""
    lines = [f"{m.get('ts', '?')} {m.get('author', '?')}: {m.get('message', '')}" for m in messages]
    return "--- steering ---\n" + "\n".join(lines) + "\n--- end steering ---"


# --- lockfile (SPEC §10.3) -----------------------------------------------------

class LockHeld(Exception):
    def __init__(self, holder: str):
        super().__init__(holder)
        self.holder = holder


def _pid_alive(pid: int) -> bool:
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


def acquire_lock(run_dir: Path, force: bool = False) -> None:
    """O_CREAT|O_EXCL lock file: pid + hostname + start time. Staleness (pid gone)
    is valid SAME-HOST ONLY; a cross-host lock needs --force-unlock."""
    lock_path = Path(run_dir) / "lock"
    payload = json.dumps({"pid": os.getpid(), "hostname": socket.gethostname(), "started": utcnow()})
    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            try:
                holder = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                holder = {}
            desc = f"pid {holder.get('pid')} on {holder.get('hostname')} since {holder.get('started')}"
            same_host = holder.get("hostname") == socket.gethostname()
            stale = same_host and isinstance(holder.get("pid"), int) and not _pid_alive(holder["pid"])
            if force or stale:
                lock_path.unlink(missing_ok=True)
                continue
            raise LockHeld(desc)
    raise LockHeld("could not acquire lock after clearing a stale one")


def release_lock(run_dir: Path) -> None:
    (Path(run_dir) / "lock").unlink(missing_ok=True)


# --- run directories (SPEC §9.2, §10.1) ----------------------------------------

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()) or "flow"


def new_run_dir(runs_dir: Path, flow_name: str) -> Path:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    base = Path(runs_dir) / f"{_slug(flow_name)}-{stamp}"
    run_dir = base
    n = 1
    while run_dir.exists():
        run_dir = Path(f"{base}-{n}")
        n += 1
    run_dir.mkdir(parents=True)
    (run_dir / "phases").mkdir()
    (run_dir / "mailbox").mkdir()  # empty in v1; reserved (SPEC §16.2)
    return run_dir


def find_attachable_run(runs_dir: Path, flow_hash: str, args: dict[str, str]) -> Path | None:
    """An identical `run` attaches to the existing run dir for the same
    (flow_hash, args) lineage; editing the flow file changes flow_hash and thus
    starts a new lineage by design (SPEC §0.3 loop D)."""
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return None
    candidates: list[tuple[str, Path]] = []
    for d in runs_dir.iterdir():
        state_path = d / "state.json"
        if not state_path.exists():
            continue
        try:
            st = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if st.flow_hash == flow_hash and st.args == args:
            candidates.append((st.started_at, d))
    if not candidates:
        return None
    return max(candidates)[1]
