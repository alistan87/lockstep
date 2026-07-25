"""GitWorkspace + NullWorkspace (SPEC §8.1, §9.2, §9.4).

Snapshot = `git add -A` into a TEMPORARY index, then `git write-tree`: a real
tree object that includes untracked files (which `git stash create` misses —
most of what a code-writing agent produces). The caller's real index is never
touched. Restore checks out baseline versions and MOVES created files aside —
rollback never deletes (SPEC §0.1 item 2).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .protocols import SnapshotRef

MAX_FINGERPRINT_FILE_BYTES = 1_000_000  # SPEC §9.2: content-hash skip above 1 MB


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
        paths: list[str] = []
        for entry in out.split("\0"):
            if len(entry) > 3:
                paths.append(entry[3:])
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

    def changed_paths(self, since: SnapshotRef) -> list[str]:
        with self._lock:
            return self._changed_paths_unlocked(since)

    def _changed_paths_unlocked(self, since: SnapshotRef) -> list[str]:
        current = self.snapshot()
        out = self._git("diff-tree", "-r", "--name-only", "--no-renames", since.ref, current.ref)
        return [line for line in out.splitlines() if line.strip()]

    def diff_patch(self, since: SnapshotRef) -> str:
        """Unified diff of baseline tree vs current tree — the blocked attempt,
        preserved before restore (SPEC §9.4.4)."""
        current = self.snapshot()
        return self._git("diff-tree", "-r", "-p", "--no-renames", since.ref, current.ref)

    def _in_tree(self, tree: str, rel: str) -> bool:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{tree}:{rel}"],
            cwd=str(self.root), capture_output=True, shell=False,
        )
        return proc.returncode == 0

    def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None:
        """Check out the baseline version of each in-scope path; paths created
        since baseline are MOVED into discard_dir, never rm'd."""
        discard_dir = Path(discard_dir)
        with self._lock:
            for rel in scope:
                if self._in_tree(ref.ref, rel):
                    self._git("checkout", ref.ref, "--", rel)
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

    def changed_paths(self, since: SnapshotRef) -> list[str]:
        raise WorkspaceError("NullWorkspace cannot diff")

    def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None:
        raise WorkspaceError("NullWorkspace cannot restore")
