"""GitWorkspace + NullWorkspace (SPEC §8.1, §9.2, §9.4).

Snapshot = `git add -A` into a TEMPORARY index, then `git write-tree`: a real
tree object that includes untracked files (which `git stash create` misses —
most of what a code-writing agent produces). The caller's real index is never
touched. Restore checks out baseline versions and MOVES created files aside —
rollback never deletes (SPEC §0.1 item 2).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .protocols import SnapshotRef

MAX_FINGERPRINT_FILE_BYTES = 1_000_000  # SPEC §9.2: content-hash skip above 1 MB


def path_in_scope(rel_path: str, scope: list[str]) -> bool:
    """Is a changed path covered by a declared write scope?

    An entry matches when the path equals it, sits under it (the entry read as
    a directory), or matches it as a glob. Separators are normalized so a
    scope written `src/x` works on Windows.
    """
    p = rel_path.replace("\\", "/").strip("/")
    for entry in scope:
        e = str(entry).replace("\\", "/").strip("/")
        if not e:
            continue
        if p == e or p.startswith(e + "/") or fnmatch.fnmatch(p, e):
            return True
    return False


class WorkspaceError(Exception):
    """Workspace operation impossible (e.g. rollback on a non-git tree; exit 7)."""


class GitWorkspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not (self.root / ".git").exists():
            raise WorkspaceError(f"{self.root} is not a git repository")
        # Nodes complete concurrently; git operations on one repo must not
        # interleave (index.lock contention). RLock: methods compose.
        self._lock = threading.RLock()

    def _git(self, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
        with self._lock:
            return self._git_unlocked(*args, env=env, check=check)

    def _git_unlocked(self, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            env=full_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if check and proc.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    # -- fingerprint (SPEC §9.2, AMENDMENTS M7) --

    def _head(self) -> str:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.root), capture_output=True, text=True, shell=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "no-head"

    def _dirty_paths(self) -> list[str]:
        out = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        # -z format: `XY <path>` NUL-terminated; for renames/copies (X in R/C)
        # the NEXT NUL field is the bare ORIGINAL path with no XY prefix —
        # naive [3:] slicing would corrupt it (audit r5 finding).
        paths: list[str] = []
        fields = out.split("\0")
        i = 0
        while i < len(fields):
            entry = fields[i]
            i += 1
            if len(entry) < 4:
                continue
            xy = entry[:2]
            paths.append(entry[3:])
            if xy[0] in "RC":
                if i < len(fields) and fields[i]:
                    paths.append(fields[i])
                i += 1
        return paths

    def fingerprint_detail(self) -> tuple[str, dict[str, str]]:
        """(digest, path -> content hash). Honors .gitignore (porcelain does);
        files > 1 MB contribute size only, not content."""
        with self._lock:
            return self._fingerprint_detail_unlocked()

    def _fingerprint_detail_unlocked(self) -> tuple[str, dict[str, str]]:
        detail: dict[str, str] = {"HEAD": self._head()}
        for rel in sorted(set(self._dirty_paths())):
            p = self.root / rel
            if not p.exists():
                detail[rel] = "deleted"
            elif p.is_dir():
                detail[rel] = "dir"
            elif p.stat().st_size > MAX_FINGERPRINT_FILE_BYTES:
                detail[rel] = f"size:{p.stat().st_size}"
            else:
                detail[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        digest = hashlib.sha256(
            "\x00".join(f"{k}={v}" for k, v in sorted(detail.items())).encode("utf-8")
        ).hexdigest()
        return digest, detail

    def fingerprint(self) -> str:
        return self.fingerprint_detail()[0]

    # -- snapshot / restore (SPEC §9.4) --

    def snapshot(self) -> SnapshotRef:
        with self._lock, tempfile.TemporaryDirectory() as td:
            tmp_index = str(Path(td) / "index")
            env = {"GIT_INDEX_FILE": tmp_index}
            self._git("add", "-A", env=env)
            tree = self._git("write-tree", env=env).strip()
        return SnapshotRef(ref=tree)

    def changed_paths(self, since: SnapshotRef, current: SnapshotRef | None = None) -> list[str]:
        with self._lock:
            return self._changed_paths_unlocked(since, current)

    def _changed_paths_unlocked(self, since: SnapshotRef,
                               current: SnapshotRef | None = None) -> list[str]:
        current = current or self.snapshot()
        out = self._git("diff-tree", "-r", "--name-only", "--no-renames", since.ref, current.ref)
        return [line for line in out.splitlines() if line.strip()]

    def diff_patch(self, since: SnapshotRef, current: SnapshotRef | None = None) -> str:
        """Unified diff of baseline tree vs current tree — the blocked attempt,
        preserved before restore (SPEC §9.4.4).

        A caller that also wants `changed_paths` for the same tree should
        snapshot ONCE and pass it to both: a snapshot walks and hashes the
        whole working tree (P1-perf measured 1.4 s on a 47 MB tree), and two
        of them describe two slightly different moments — sharing one makes the
        preserved patch and the restore scope answers about the same tree,
        which is what §9.4.4 means by both.
        """
        current = current or self.snapshot()
        return self._git("diff-tree", "-r", "-p", "--no-renames", since.ref, current.ref)

    def dirty_paths(self) -> list[str]:
        """Working-tree paths differing from HEAD — tracked modifications plus
        untracked (non-ignored) files. The E9 preflight input: an in-scope
        write legally OVERWRITES a file the operator already edited, and only
        knowing the overlap before the run closes that gap.

        `-z` (NUL-separated), NOT line-splitting: porcelain v1 C-quotes any
        path with a non-ASCII byte or a quote character, and stripping quotes
        without unescaping mangles them ("café.md" became "caf/303/251.md" —
        adversarial-review finding 6, repro'd live). With `-z` paths arrive
        verbatim; a rename's ORIGINAL path follows as its own NUL record."""
        out = self._git("status", "--porcelain", "-z", "--untracked-files=all")
        paths: list[str] = []
        records = out.split("\0")
        i = 0
        while i < len(records):
            rec = records[i]
            i += 1
            if len(rec) < 4:
                continue
            xy, path = rec[:2], rec[3:]
            if xy[0] in ("R", "C"):
                i += 1  # the next record is the rename/copy SOURCE, not dirt
            paths.append(path)
        return paths

    def staged_paths(self) -> set[str]:
        """Paths whose INDEX entry differs from HEAD — work someone staged and
        has not committed.

        Captured before a node runs so quarantine can tell the agent's staging
        from the operator's: a path the operator had already staged is named in
        the failure message and left exactly as they left it.
        """
        with self._lock:
            if self._head() == "no-head":
                # Unborn branch: every index entry is uncommitted by definition.
                out = self._git("ls-files", "--cached", "-z")
            else:
                out = self._git("diff-index", "--cached", "--name-only", "-z", "HEAD")
        return {p for p in out.split("\0") if p}

    def unstage(self, paths: list[str]) -> None:
        """Reset each path's index entry to HEAD (removing it where HEAD has no
        such path).

        An index-safe restore puts the WORKTREE back but deliberately leaves the
        index alone — which would strand a violating blob in the index, where
        the next commit picks it up and `snapshot()` (which reads the worktree)
        can never see it. Quarantine calls this for the paths it reverted.
        """
        if not paths:
            return
        with self._lock:
            if self._head() == "no-head":
                self._git("rm", "--cached", "-q", "--ignore-unmatch", "--", *paths, check=False)
            else:
                self._git("reset", "-q", "HEAD", "--", *paths, check=False)

    def _in_tree(self, tree: str, rel: str) -> bool:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{tree}:{rel}"],
            cwd=str(self.root), capture_output=True, shell=False,
        )
        return proc.returncode == 0

    def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None:
        """Check out the baseline version of each in-scope path; paths created
        since baseline are MOVED into discard_dir, never rm'd.

        The checkout runs against a THROWAWAY index, the way `snapshot()` does:
        `git checkout` writes the index as well as the worktree, and the
        baseline tree was built from the WORKTREE — so index-only content was
        never captured, and taking it is pure loss. A hunk the operator staged
        but never wrote out survives a rollback.

        It stays `checkout` rather than becoming a `cat-file blob` write: only
        checkout runs the smudge filters, so eol/`.gitattributes`, clean-smudge
        pairs (git-lfs), file modes and symlinks all round-trip. A blob write
        corrupts every one of those, and `git status` reports nothing.
        """
        discard_dir = Path(discard_dir)
        with self._lock, tempfile.TemporaryDirectory() as td:
            env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
            for rel in scope:
                if self._in_tree(ref.ref, rel):
                    self._git("checkout", ref.ref, "--", rel, env=env)
                else:
                    src = self.root / rel
                    if src.exists():
                        dest = discard_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest))


class NullWorkspace:
    """For non-git trees. Fingerprint is constant, so external-edit detection is
    OFF by design (AMENDMENTS M6); snapshot/restore raise — heal.rollback on a
    non-git tree is a run-time refusal with exit 7 (SPEC §9.4.1)."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def fingerprint(self) -> str:
        return "null-workspace"

    def fingerprint_detail(self) -> tuple[str, dict[str, str]]:
        return "null-workspace", {}

    def snapshot(self) -> SnapshotRef:
        raise WorkspaceError("NullWorkspace cannot snapshot: heal.rollback requires a git-managed tree")

    def changed_paths(self, since: SnapshotRef, current: SnapshotRef | None = None) -> list[str]:
        raise WorkspaceError("NullWorkspace cannot diff")

    def diff_patch(self, since: SnapshotRef, current: SnapshotRef | None = None) -> str:
        raise WorkspaceError("NullWorkspace cannot diff")

    def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None:
        raise WorkspaceError("NullWorkspace cannot restore")

    def staged_paths(self) -> set[str]:
        return set()  # no index to protect

    def unstage(self, paths: list[str]) -> None:
        raise WorkspaceError("NullWorkspace has no index")
