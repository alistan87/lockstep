"""Shared helpers for the gate library. See the package docstring for the
conventions every gate follows."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SEVERITIES = ("blocker", "major", "minor", "nit")  # most to least severe


def pid_alive(pid: int) -> bool:
    """Mirrors lockstep.state._pid_alive (and contrib/who_holds.py) rather
    than importing it: gates import ._common + stdlib only, never engine
    privates. Inherits the same accepted weakness: a recycled pid reads as
    alive."""
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


def finding(
    severity: str,
    category: str,
    file: str,
    claim: str,
    evidence: str,
    fix_hint: str,
    line: int | None = None,
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "claim": claim,
        "evidence": evidence,
        "fix_hint": fix_hint,
    }


def emit(findings: list[dict], pass_reason: str, block_reason: str | None = None) -> int:
    """Print the Verdict and return the gate's exit code (always 0: a blocking
    verdict is a result, not a failure).

    Written as UTF-8 BYTES rather than via `print`. A gate's stdout is a
    redirected pipe, and on Windows a redirected Python stdout defaults to the
    locale encoding (cp1252) — so `ensure_ascii=False` plus one arrow, curly
    quote, `>=` sign or non-Latin filename in a model-written finding raised
    UnicodeEncodeError, killed the gate with exit 1, and left stdout EMPTY.
    The driver then reported a failed node, with the real cause visible only in
    stderr.log. `block_on_severity` re-emits model prose verbatim, so this was
    reachable on any run whose reviewer used a character cp1252 lacks.
    """
    verdict = "pass" if not findings else "block"
    reason = pass_reason if verdict == "pass" else (block_reason or f"{len(findings)} finding(s)")
    line = json.dumps(
        {"findings": findings, "verdict": verdict, "reason": reason}, ensure_ascii=False
    )
    sys.stdout.flush()
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0


def resolve_node_result(node_id: str) -> tuple[Path | None, dict | None]:
    """A sibling node's result file, via the LOCKSTEP_PHASE_DIR the spawn
    exports: <run_dir>/phases/<node>/result.json|result.txt (§8.3 order).
    Returns (path, None) or (None, blocker_finding)."""
    phase = os.environ.get("LOCKSTEP_PHASE_DIR", "")
    if not phase:
        return None, finding(
            "blocker", "gate-error", ".", "LOCKSTEP_PHASE_DIR is not set",
            "node-relative arguments need the lockstep spawn environment",
            "pass an explicit file path instead",
        )
    node_dir = Path(phase).parent / node_id
    for name in ("result.json", "result.txt"):
        p = node_dir / name
        if p.exists():
            return p, None
    return None, finding(
        "blocker", "gate-error", str(node_dir), f"node {node_id!r} left no result",
        "neither result.json nor result.txt exists in its phase dir",
        "check the node id and that it ran",
    )


def flatten_text(value) -> str:
    """Join every string leaf of a JSON value with blank lines — a map node's
    aggregated result is an array of texts, and gates that scan prose need the
    real newlines back."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(flatten_text(v) for v in value)
    if isinstance(value, dict):
        return "\n\n".join(flatten_text(v) for v in value.values())
    return ""


def read_doc(path: str) -> tuple[str | None, dict | None]:
    """BOM- and UTF-16-tolerant text read (documents arrive from editors the
    flow does not control). Returns (text, None) or (None, blocker_finding)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, finding(
            "blocker", "unreadable", str(path), "file could not be read", str(e),
            "check the path argument",
        )
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
    except ValueError:
        text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return None, finding(
            "blocker", "empty-doc", str(path), "file is empty",
            f"{len(raw)} byte(s), no text content", "write the document before gating it",
        )
    return text, None
