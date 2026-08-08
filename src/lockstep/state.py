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


_LABEL_RE = re.compile(r"^[a-z][a-z0-9_.\-]*:")


def part_digest(part: str) -> str:
    return hashlib.sha256(part.encode("utf-8")).hexdigest()


def label_parts(parts: list[str], detail: dict[str, str] | None = None) -> dict[str, str]:
    """Labelled digests of the fingerprint parts (the cache-miss explainer).

    A RECORD of the inputs to `compose_hash`, never an input to it: M3 hash
    composition does not move. Digests rather than raw parts, deliberately —
    parts embed full prompts and upstream results, and state.json must not
    become a second copy of sensitive text. `detail` carries executor-supplied
    sub-part digests (e.g. prompt.heal) that are folded INSIDE a top-level
    part, so a reader can see which component of the prompt moved.
    """
    out: dict[str, str] = {}

    def put(label: str, digest: str) -> None:
        key, n = label, 2
        while key in out:
            key, n = f"{label}#{n}", n + 1
        out[key] = digest

    for part in parts:
        m = _LABEL_RE.match(part)
        put(m.group(0)[:-1] if m else "part", part_digest(part))
    for k, v in (detail or {}).items():
        put(k, v)
    return out


def diff_labels(old: dict[str, str] | None, new: dict[str, str] | None) -> list[str]:
    """Which labelled parts differ between two recordings. `None` on either
    side means the run predates part recording (the UNCHAINED precedent)."""
    if old is None or new is None:
        return ["unrecorded (run predates part recording)"]
    out: list[str] = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            out.append(f"{k}: only in old")
        elif k not in old:
            out.append(f"{k}: only in new")
        elif old[k] != new[k]:
            out.append(f"{k}: changed")
    return out or [
        "no labelled part differs (role, kind, contract, or an unlabelled part moved)"
    ]


# --- records (SPEC §10.2, AMENDMENTS A3) ---------------------------------------

class ItemRecord(BaseModel):
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    input_hash: str | None = None
    result_path: str | None = None
    attempts: int = 0
    error: str | None = None
    # label -> sha256(part) for the parts that composed input_hash (see
    # `label_parts`). A record for `lockstep explain`; never a hash input.
    hash_parts: dict[str, str] | None = None


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
    # label -> sha256(part) for the parts that composed input_hash (see
    # `label_parts`). A record for `lockstep explain`; never a hash input.
    hash_parts: dict[str, str] | None = None
    # Why the LAST revalidation re-ran this done node: the labels that moved.
    # Set only on a hash-mismatch (or failed replan) at revalidation time —
    # a wrongly re-billed node is otherwise silent (the r7 heal-text lesson).
    invalidated_by: list[str] | None = None
    # In-scope paths this node changed, for a node that declared spec.writes:
    # a COUNT and a run-dir-relative path to the list, never the list. Every
    # `record` call rewrites the whole of state.json, so a path list on one
    # node is re-serialised on every subsequent record — and a file is better
    # evidence at an approval on a 3 000-file codemod anyway.
    touched_count: int | None = None
    touched_path: str | None = None


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
    # Proactive heal baselines (§9.4.2), gate_id -> git tree sha — PERSISTED so
    # a resumed process restores to the true pre-attempt state instead of
    # snapshotting a tree that already contains the blocked attempt (audit r6
    # blocker). Cleared when the gate passes.
    heal_baselines: dict[str, str] = {}
    # Heal text per TARGET node id (the block reason + fenced findings appended
    # to its prompt on a heal round) — PERSISTED because it folds into the
    # target's input_hash. A fresh process must re-plan the prompt the spawn
    # actually saw, or every healed node re-runs on the next resume. Same
    # reasoning as r6 C2's whole-mailbox rendering, and likewise NOT cleared when
    # the gate passes: clearing it would change the hash of a result it helped
    # produce. Latest round wins.
    heal_texts: dict[str, str] = {}


# --- events.jsonl --------------------------------------------------------------

_events_lock = threading.Lock()


def _chain_link(prev: str, payload: str) -> str:
    """One link: sha256 over the predecessor's digest and this line's bytes."""
    return hashlib.sha256(f"{prev}\n{payload}".encode("utf-8")).hexdigest()


def _last_head_unlocked(run_dir: Path) -> str:
    """The chain head recorded in the file: the last COMPLETE line's `h`.

    Read from the tail rather than the whole file so appending stays O(1) in
    run length. Deliberately not cached: a run dir can be replaced underneath
    us (a --fresh run, a restored backup), and a stale head would silently
    fork the chain.
    """
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return ""
    size = path.stat().st_size
    if size == 0:
        return ""
    window = 65536
    while True:
        start = max(0, size - window)
        with open(path, "rb") as f:
            f.seek(start)
            tail = f.read(size - start).decode("utf-8", errors="replace")
        head, found = "", False
        for line in tail.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a partial first line in the window, or a torn tail
            found = True
            head = record.get("h", "") or ""
        if found or start == 0:
            return head
        window *= 4  # one event longer than the window; widen and retry


def chain_head(run_dir: Path) -> str:
    """The current head of the run's event chain ("" if unchained/empty)."""
    with _events_lock:
        return _last_head_unlocked(Path(run_dir))


def append_event(run_dir: Path, event: dict) -> None:
    event = {"ts": utcnow(), "kind": event.pop("kind", "transition"), **event}
    event.pop("h", None)  # never re-chain a digest carried in by a caller
    with _events_lock:
        prev = _last_head_unlocked(Path(run_dir))
        payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        # `h` is appended LAST so a verifier can pop it and re-serialize the
        # remaining keys in insertion order to reproduce these exact bytes.
        event["h"] = _chain_link(prev, payload)
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        with open(run_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")


def trace_status(run_dir: Path) -> dict:
    """Recompute the event chain and report everything a reader needs.

    `{ok, head, first_bad_line, detail, total, chained}`. `verify_trace` is the
    4-tuple view of this; `cli.py` unpacks that arity, so it does not move.

    `total` and `chained` exist because `ok` alone cannot be rendered. A tamper
    returns `ok=False` with a NON-EMPTY head (the last good digest), and a
    healthy fresh run returns `ok=True` with an empty one — so "green tick iff
    head" is wrong in both directions. The four-way rule is: not ok → BROKEN;
    ok with no events → nothing to verify; ok with events but none chained →
    unchained; otherwise verified, with the head.

    `first_bad_line` is 1-indexed for humans. A torn trailing line is tolerated
    (SPEC §10.3). A file whose lines carry no `h` is UNCHAINED — reported as
    such, never as verified.

    This is tamper EVIDENCE, not tamper proofing: whoever can rewrite the file
    can also re-chain it. What the chain gives is that no *partial* edit — one
    line changed, dropped, or appended — survives, and that a head digest
    recorded elsewhere pins the whole file.
    """

    def result(ok, head, bad, detail, total, chained) -> dict:
        return {
            "ok": ok, "head": head, "first_bad_line": bad,
            "detail": detail, "total": total, "chained": chained,
        }

    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return result(True, "", None, "no events.jsonl", 0, 0)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    prev, chained, total = "", 0, 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue  # torn trailing line after a crash (§10.3)
            return result(False, prev, i + 1, f"line {i + 1} is not valid JSON", total, chained)
        total += 1
        recorded = record.pop("h", None)
        if recorded is None:
            continue  # predates chaining; counted by `total`, not by `chained`
        chained += 1
        payload = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        expected = _chain_link(prev, payload)
        if expected != recorded:
            return result(False, prev, i + 1, (
                f"line {i + 1} does not match the chain "
                f"(expected {expected[:12]}…, found {str(recorded)[:12]}…)"
            ), total, chained)
        prev = recorded
    if chained == 0 and total:
        return result(True, "", None, f"unchained: {total} events carry no chain digest", total, 0)
    return result(True, prev, None, f"{chained} events verified", total, chained)


def verify_trace(run_dir: Path) -> tuple[bool, str, int | None, str]:
    """(ok, head, first_bad_line, detail) — the `cli.py verify-trace` view of
    `trace_status`. Frozen arity; new fields go on the dict, not here."""
    s = trace_status(run_dir)
    return s["ok"], s["head"], s["first_bad_line"], s["detail"]


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


_span_lock = threading.Lock()
_span_target: Path | None = None
_span_run_id: str = ""

# Kinds that actually invoke a model. Shell nodes are deliberately excluded
# from the GenAI attributes below: a subprocess spends no tokens, and labelling
# it a model call would make every cost dashboard downstream wrong.
_AGENT_KINDS = ("harness", "fake")


def configure_spans(path: Path | None, run_id: str) -> None:
    """Point `emit_span` at a file, or turn it off again with None."""
    global _span_target, _span_run_id
    with _span_lock:
        _span_target = Path(path) if path else None
        _span_run_id = run_id


def _unix_nanos(ts: str | None) -> int:
    if not ts:
        return 0
    try:
        return int(_dt.datetime.fromisoformat(ts).timestamp() * 1_000_000_000)
    except ValueError:
        return 0


def _attr(key: str, value) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def emit_span(record: PhaseRecord) -> None:
    """Append one OTLP/JSON span for a finished node (SPEC §10.3, §16.3).

    Off unless `configure_spans` has been called, so the seam stays a no-op by
    default. The shape is a full ExportTraceServiceRequest per line, which a
    collector ingests directly — and writing it by hand is what keeps
    `pydantic` the only runtime dependency.

    Advisory only. Like structured progress (§16.1), a span never influences
    scheduling, hashing, gating, budgets, or retries.
    """
    with _span_lock:
        target, run_id = _span_target, _span_run_id
    if target is None:
        return
    from . import __version__

    trace_id = hashlib.sha256(f"trace:{run_id}".encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(
        f"span:{run_id}:{record.node_id}:{record.heal_round}:{record.attempts}".encode("utf-8")
    ).hexdigest()[:16]
    start = _unix_nanos(record.started_at)
    end = _unix_nanos(record.ended_at) or start
    attributes = [
        _attr("lockstep.run_id", run_id),
        _attr("lockstep.node_id", record.node_id),
        _attr("lockstep.role", record.role),
        _attr("lockstep.kind", record.kind),
        _attr("lockstep.status", record.status),
        _attr("lockstep.attempts", record.attempts),
        _attr("lockstep.heal_round", record.heal_round),
    ]
    if record.input_hash:
        attributes.append(_attr("lockstep.input_hash", record.input_hash))
    if record.kind in _AGENT_KINDS and record.role != "approval":
        attributes.append(_attr("gen_ai.operation.name", "invoke_agent"))
        attributes.append(_attr("gen_ai.agent.name", record.node_id))
    envelope = {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr("service.name", "lockstep")]},
                "scopeSpans": [
                    {
                        "scope": {"name": "lockstep", "version": __version__},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": record.node_id,
                                "kind": 1,  # SPAN_KIND_INTERNAL
                                "startTimeUnixNano": str(start),
                                "endTimeUnixNano": str(end),
                                "attributes": attributes,
                                "status": (
                                    {"code": 2, "message": record.error or record.status}
                                    if record.status in ("failed", "blocked")
                                    else {"code": 1}
                                ),
                            }
                        ],
                    }
                ],
            }
        ]
    }
    line = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    with _span_lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(line + "\n")


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
