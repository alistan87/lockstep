"""Declared file inputs as hash parts (`spec.reads`, parity 3.1, adopted
2026-08-13).

A node that declares `"reads": ["src/**", "pyproject.toml"]` gets ONE extra
fingerprint part — `reads:` + the JSON of every matched file as
`path|content-sha256`, sorted — so editing a declared file invalidates exactly
the nodes that declared it. Per-file digests additionally ride in
`hash_detail` (`reads.<path>`), which is what lets `lockstep explain` name the
exact file that moved instead of one opaque part.

**This is a precision feature, not a correctness feature.** The scope
statement from the proposal, repeated wherever reads is documented: lockstep
cannot observe what an opaque agent subprocess actually opens, so an
UNDECLARED read stays invisible — the same limitation `spec.writes` has, and
the same discipline applies: declare honestly, and treat the declaration as a
statement about the flow, not a guarantee about the model.

**Additivity is load-bearing (M3: hash composition is frozen).** A node with
no `reads` key — or an empty list — contributes NOTHING: no part, no detail,
byte-identical `fingerprint_parts` to every release before this one. Pinned
by test and by the replay fixture passing without re-recording.

**Cost discipline** (the lesson-20 shape: resume re-plans every done node, so
glob hashing runs at every `_settle` revalidation):

- A per-process memo keyed on `(path, mtime_ns, size)`: overlapping globs
  across nodes hash each file once, and a file a mid-run node WROTE re-hashes
  because its stat moved — the proposal's literal "each path hashed once"
  memo would have served a digest of a tree that no longer exists. The memo
  deliberately does not survive the process: a resume must re-read the tree
  or the whole feature is a lie (proposal finding 19; the stat key makes the
  within-process version of that statement true as well).
- Planning executors journal a `kind: "timing"` line (`op: "reads-hash"`)
  beside the workspace timings, so a creep is visible in the journal instead
  of discovered as a 13→32-minute gate.
- `verify --lint` warns when a pattern set matches more than
  `BROAD_READS_THRESHOLD` files (`lint-broad-reads`).

Glob semantics are `pathlib.Path.glob` (`**` crosses directories, `*` does
not cross `/`) — reads ENUMERATE the live filesystem, unlike `writes`, whose
`fnmatch` patterns test paths the engine already has. `.git` is always
excluded; callers exclude the runs root via `exclude_roots` (a `**` glob must
never hash run dirs: sensitive, volatile, and self-invalidating). Entries may
interpolate `{args.NAME}` and nothing else, exactly like a write scope —
rendered through the same `render_scope`.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

BROAD_READS_THRESHOLD = 200  # lint-broad-reads fires above this many files

_memo: dict[str, tuple[int, int, str]] = {}  # abs path -> (mtime_ns, size, sha256)
_memo_lock = threading.Lock()


def clear_memo() -> None:
    """Test hook. The memo is process-lifetime by design; nothing in the
    engine calls this."""
    with _memo_lock:
        _memo.clear()


def _digest_file(path: Path) -> str:
    """sha256 of the file, memoized on (mtime_ns, size). One retry on OSError:
    this machine's AV throws transient PermissionError on fresh files (see
    CLAUDE.md ops notes); a persistent failure hashes as `unreadable`, which
    is stable until the file becomes readable — at which point the stat key
    has usually moved and the real digest replaces it."""
    key = str(path)
    try:
        st = path.stat()
        stat_key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return "unreadable"
    with _memo_lock:
        hit = _memo.get(key)
        if hit is not None and (hit[0], hit[1]) == stat_key:
            return hit[2]
    for attempt in (0, 1):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            break
        except OSError:
            if attempt == 1:
                return "unreadable"
            time.sleep(0.05)
    with _memo_lock:
        _memo[key] = (stat_key[0], stat_key[1], digest)
    return digest


def matched_files(repo_root: Path, patterns: list[str],
                  exclude_roots: tuple[Path, ...] = ()) -> list[Path]:
    """Every regular file matched by any pattern, deduplicated, sorted by
    repo-relative posix path. `.git` and `exclude_roots` never match."""
    repo_root = Path(repo_root)
    resolved_excludes = tuple(Path(e).resolve() for e in exclude_roots)
    seen: dict[str, Path] = {}
    for pattern in patterns:
        try:
            matches = repo_root.glob(pattern)
        except (ValueError, NotImplementedError):
            continue  # a malformed pattern matches nothing; verify names it
        for p in matches:
            if not p.is_file():
                continue
            rel = p.relative_to(repo_root)
            if ".git" in rel.parts:
                continue
            resolved = p.resolve()
            if any(resolved.is_relative_to(ex) for ex in resolved_excludes):
                continue
            seen[rel.as_posix()] = p
    return [seen[k] for k in sorted(seen)]


def hash_reads(repo_root: Path, patterns: list[str],
               exclude_roots: tuple[Path, ...] = ()) -> tuple[str, dict[str, str], dict]:
    """(fingerprint part, hash_detail entries, stats).

    The part is `reads:` + compact JSON of sorted `path|sha256` strings — one
    part regardless of file count, so `compose_hash`'s part list stays flat.
    detail maps `reads.<path>` -> digest so `explain` names the moved file.
    stats: {"files": n, "ms": elapsed} for the caller's timing line. Only
    called with non-empty patterns; the no-reads path never reaches here."""
    import json

    t0 = time.perf_counter()
    entries: list[str] = []
    detail: dict[str, str] = {}
    for p in matched_files(repo_root, patterns, exclude_roots):
        rel = p.relative_to(repo_root).as_posix()
        digest = _digest_file(p)
        entries.append(f"{rel}|{digest}")
        detail[f"reads.{rel}"] = digest
    part = "reads:" + json.dumps(entries, ensure_ascii=False)
    stats = {"files": len(entries), "ms": round((time.perf_counter() - t0) * 1000, 3)}
    return part, detail, stats


def apply_reads(reads: list[str], ctx, node_id: str) -> tuple[list[str], dict[str, str]]:
    """The executor-side integration, shared by every planning executor that
    supports `spec.reads`. Renders `{args.NAME}` entries through the same
    `render_scope` a write scope uses, hashes the matches (runs root excluded
    — a `**` glob must never hash run dirs), and journals the timing line
    into the run dir derived from `ctx.phase_dir` (`<run_dir>/phases/<node>`,
    the same derivation `node_diff` uses). Returns `([], {})` for an absent
    or empty declaration — the additive no-op M3 requires."""
    if not reads:
        return [], {}
    from .interpolate import render_scope
    from .state import append_event

    rendered = render_scope(list(reads), ctx.args)
    run_dir = Path(ctx.phase_dir).parents[1]
    part, detail, stats = hash_reads(
        Path(ctx.repo_root), rendered, exclude_roots=(run_dir.parent,)
    )
    try:
        append_event(run_dir, {"kind": "timing", "node": node_id, "op": "reads-hash", **stats})
    except OSError:
        pass  # a throwaway planning dir with no journal is not an error
    return [part], detail
