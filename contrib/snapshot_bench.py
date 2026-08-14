#!/usr/bin/env python
"""snapshot_bench.py — what does a lockstep tree snapshot actually cost?

    python contrib/snapshot_bench.py                 # synthetic repo, default sizes
    python contrib/snapshot_bench.py --files 4000 --kb 8
    python contrib/snapshot_bench.py --repo .        # THIS repo, read-only timings

P1-perf (docs/notes/LESSONS-TO-MECHANISMS.md, lesson 20). A gate command was
reported growing 13 -> 32 minutes across resumes of one long-lived run while
the same command run by hand stayed fast. `GitWorkspace.snapshot()` was the
suspect, and "measure before any fix" was the plan. This is the measurement.

What it measures
----------------
`snapshot()` is `git add -A` into a FRESH temporary index, then `git write-tree`.
A fresh index has no stat cache, so git cannot know any file is unchanged: it
reads and hashes EVERY file in the working tree on every call. The cost is
therefore O(bytes in the tree), not O(what changed) — and it is paid twice per
scoped node (baseline + diff), once per heal target, and three more times per
heal round (patch, diff, restore).

The `seeded` column tests the fix candidate: copy the repo's real index into
the temp file first (`GIT_INDEX_FILE=<copy>`), so git's stat cache applies and
only genuinely-changed files are re-hashed. The tree written is identical --
`add -A` still stages everything -- so this is a pure cost question, but it is
NOT free: seeding trusts the caller's index, and a stale-stat file (same size,
same mtime, different content) would be staged at its OLD blob. That is why
this script prints a tree-equality check per round rather than just times.

Nothing here spends a token or touches a run dir.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def git(args: list[str], cwd: Path, env: dict | None = None) -> str:
    full = dict(os.environ)
    full.update(env or {})
    out = subprocess.run(["git", *args], cwd=str(cwd), env=full,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def snapshot(repo: Path, seed_index: bool) -> tuple[str, float]:
    """One snapshot; returns (tree sha, seconds). `seed_index` copies the repo's
    real index in first, which is the whole proposal under test."""
    with tempfile.TemporaryDirectory() as td:
        tmp_index = Path(td) / "index"
        if seed_index:
            real = repo / ".git" / "index"
            if real.is_file():
                shutil.copy2(real, tmp_index)
        env = {"GIT_INDEX_FILE": str(tmp_index)}
        t0 = time.perf_counter()
        git(["add", "-A"], repo, env)
        tree = git(["write-tree"], repo, env).strip()
        return tree, time.perf_counter() - t0


def build_repo(root: Path, files: int, kb: int) -> None:
    git(["init", "-q"], root)
    git(["config", "user.email", "bench@example.invalid"], root)
    git(["config", "user.name", "bench"], root)
    blob = ("x" * 1023 + "\n") * kb
    for i in range(files):
        d = root / f"pkg{i // 100:03d}"
        d.mkdir(exist_ok=True)
        (d / f"mod{i:05d}.txt").write_text(blob, encoding="utf-8")
    git(["add", "-A"], root)
    git(["commit", "-qm", "base"], root)


def add_churn(root: Path, n: int, round_no: int, kb: int) -> None:
    """What a run does to a tree: a handful of edits, plus new untracked files
    that no later snapshot can avoid hashing."""
    blob = ("y" * 1023 + "\n") * kb
    for i in range(n):
        (root / f"pkg000/mod{i:05d}.txt").write_text(blob, encoding="utf-8")
        (root / f"build-r{round_no}-{i:03d}.out").write_text(blob, encoding="utf-8")


def bench_synthetic(files: int, kb: int, rounds: int, churn: int) -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        print(f"building a {files}-file repo, {kb} KB each "
              f"(~{files * kb / 1024:.0f} MB) ...", flush=True)
        build_repo(root, files, kb)

        print()
        print("round   tree files    fresh-index    seeded-index    same tree?")
        print("-" * 66)
        for r in range(rounds):
            if r:
                add_churn(root, churn, r, kb)
            tracked = len(git(["ls-files"], root).splitlines())
            untracked = len([p for p in root.rglob("*")
                             if p.is_file() and ".git" not in p.parts])
            fresh_tree, fresh_s = snapshot(root, seed_index=False)
            seeded_tree, seeded_s = snapshot(root, seed_index=True)
            print(f"{r:>5}   {untracked:>10}    {fresh_s:>9.3f}s    {seeded_s:>10.3f}s"
                  f"    {'yes' if fresh_tree == seeded_tree else 'NO — DIVERGED'}")
            if fresh_tree != seeded_tree:
                print("\nSeeding produced a DIFFERENT tree. Do not adopt it.")
                return 1
            _ = tracked
        print()
        print("Each row is one snapshot of the whole tree. lockstep takes two per")
        print("scoped node and three more per heal round.")
    return 0


def bench_repo(repo: Path, rounds: int) -> int:
    print(f"timing snapshots of {repo.resolve()} (no writes) ...")
    print()
    print("round    fresh-index    seeded-index    same tree?")
    print("-" * 52)
    for r in range(rounds):
        fresh_tree, fresh_s = snapshot(repo, seed_index=False)
        seeded_tree, seeded_s = snapshot(repo, seed_index=True)
        print(f"{r:>5}    {fresh_s:>9.3f}s    {seeded_s:>10.3f}s"
              f"    {'yes' if fresh_tree == seeded_tree else 'NO — DIVERGED'}")
        if fresh_tree != seeded_tree:
            return 1
    print()
    print("A warm OS page cache flatters the fresh column; a run's tree is")
    print("bigger and colder than this one.")
    return 0


def bench_reads(root: Path, pattern: str, rounds: int) -> int:
    """Parity 3.1's measured ceiling: what one `spec.reads` glob costs at plan
    time, cold (first plan of a process — every resume starts here) vs warm
    (the stat-keyed memo, which is what every LATER plan in the same process
    pays, including the `_settle` revalidation of every done node)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from lockstep import reads as reads_mod

    print(f"hashing reads pattern {pattern!r} under {root.resolve()} ...")
    print()
    print("round    files      cold        memo-warm")
    print("-" * 46)
    for r in range(rounds):
        reads_mod.clear_memo()
        _, _, cold = reads_mod.hash_reads(root, [pattern])
        _, _, warm = reads_mod.hash_reads(root, [pattern])
        print(f"{r:>5}    {cold['files']:>5}    {cold['ms']:>7.1f}ms    {warm['ms']:>9.1f}ms")
    print()
    print("Cold is what a resume pays per declaring node before the memo warms;")
    print("warm is every later plan in the same process. The journal's")
    print('`kind: "timing", op: "reads-hash"` lines are the live view of this.')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="time an existing repo instead of a synthetic one")
    ap.add_argument("--files", type=int, default=2000, help="synthetic tree size")
    ap.add_argument("--kb", type=int, default=4, help="KB per synthetic file")
    ap.add_argument("--rounds", type=int, default=6, help="snapshots to take")
    ap.add_argument("--churn", type=int, default=5,
                    help="files edited + created between synthetic rounds")
    ap.add_argument("--reads", metavar="GLOB",
                    help="time hashing a spec.reads glob (parity 3.1) instead of snapshots; "
                         "combines with --repo, else uses a synthetic tree")
    ns = ap.parse_args(argv)
    if ns.reads:
        if ns.repo:
            return bench_reads(Path(ns.repo), ns.reads, ns.rounds)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            print(f"building a {ns.files}-file repo, {ns.kb} KB each ...", flush=True)
            build_repo(root, ns.files, ns.kb)
            return bench_reads(root, ns.reads, ns.rounds)
    if ns.repo:
        return bench_repo(Path(ns.repo), ns.rounds)
    return bench_synthetic(ns.files, ns.kb, ns.rounds, ns.churn)


if __name__ == "__main__":
    sys.exit(main())
