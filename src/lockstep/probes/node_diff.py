"""What ONE STEP changed -> text, for a readonly reviewer. Deterministic.

Usage from a flow:
    ["python", "-m", "lockstep.probes.node_diff", "--node", "implement"]
    ["python", "-m", "lockstep.probes.node_diff", "--node", "implement",
     "--run-dir", "runs/x", "--max-lines", "4000"]

The difference from `worktree_diff`, and the reason this exists: that probe
captures the tree AS IT IS NOW. In a single-phase flow the two are the same
thing. In a multi-phase one — capture, review, gate, remediate, capture,
review, gate — they stop being the same the moment phase 2 writes a file.
Shell nodes always re-run on resume (SPEC §0.1.7), so a resumed run re-captures
the CURRENT tree; the reviewer's prompt embeds that text, so its input hash
legitimately moves and it re-runs — now judging phase 1 against a tree that
contains phase 2. Reported live (consumer report 2026-08-13 item 1) as two
full restarts: a reviewer that had passed came back with scope violations that
had never existed, because the evidence underneath it had moved.

This probe reads the two git tree objects the engine RECORDED for the node:
its write-scope baseline, and the tree it left on success. Both are already
computed for any node that declares `spec.writes` and is serialized on the
tree; nothing here re-measures anything. The answer is therefore the same on
every resume, in every later phase, forever — which is what makes the
reviewer's input hash stable and stops it re-billing at all.

Created files need no special handling (unlike `worktree_diff`, where they are
untracked and absent from `git diff`): the snapshot is `git add -A` into a
throwaway index, so a file the step created is a normal addition in this diff.

Always exits 0. A node with no recorded trees, an unknown node and a missing
run dir are all observations to report; the reviewer downstream is what
decides.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_LINES = 4000


def _out(lines: list[str]) -> int:
    sys.stdout.flush()
    # UTF-8 bytes, not print(): a redirected stdout on Windows defaults to
    # cp1252, and source files contain arrows, dashes and non-Latin strings.
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def _cap(lines: list[str], limit: int, what: str) -> list[str]:
    if limit <= 0 or len(lines) <= limit:
        return lines
    dropped = len(lines) - limit
    # Say that it truncated. A silent cut reads as "that is the whole change".
    return lines[:limit] + ["", f"[... {dropped} more line(s) of {what} not shown ...]"]


def _git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "--no-pager", *args],
            capture_output=True, encoding="utf-8", errors="replace",
        )
    except OSError as e:  # git absent
        return 127, f"git could not be run: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _exclude_run_dir(run_dir: Path) -> list[str]:
    """Pathspec that keeps the driver's OWN bookkeeping out of the diff.

    `snapshot()` is `git add -A`, which honours `.gitignore` — so where `runs/`
    is ignored (this repo's convention) the recorded trees never contained the
    run dir and this is a no-op. Where it is NOT ignored, every prompt, log and
    `state.json` write the node's own run made lands between the two trees, and
    a reviewer would be handed the driver's bookkeeping as if the step had
    written it. The engine excludes the run dir from the write-scope diff for
    exactly this reason (`roles._outside_run_dir`); this is the same exclusion
    on the read side.
    """
    rc, top = _git("rev-parse", "--show-toplevel")
    if rc != 0:
        return []
    try:
        rel = run_dir.resolve().relative_to(Path(top.strip()).resolve())
    except (ValueError, OSError):
        return []  # the run dir is outside the work tree: nothing to exclude
    posix = str(rel).replace("\\", "/").strip("/")
    return ["--", ".", f":(exclude){posix}/**", f":(exclude){posix}"] if posix else []


def _run_dir(explicit: str | None) -> tuple[Path | None, str]:
    """The run dir, or why we cannot name one.

    A flow node does not know its own run dir and cannot interpolate one — but
    every spawn already gets `LOCKSTEP_PHASE_DIR` (`<run_dir>/phases/<node>`),
    so the run dir is its grandparent. Environment, not argv: it never enters
    the input hash, so this probe's command stays identical across runs.
    """
    if explicit:
        return Path(explicit), ""
    env = os.environ.get("LOCKSTEP_PHASE_DIR")
    if not env:
        return None, ("no --run-dir given and LOCKSTEP_PHASE_DIR is not set "
                      "(this probe is meant to run as a lockstep shell node)")
    return Path(env).parents[1], ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.probes.node_diff")
    ap.add_argument("--node", required=True, help="the node whose change to show")
    ap.add_argument("--run-dir", default=None,
                    help="default: derived from LOCKSTEP_PHASE_DIR")
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ns = ap.parse_args(argv)

    run_dir, why = _run_dir(ns.run_dir)
    if run_dir is None:
        return _out([f"NODE DIFF: {why}"])
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return _out([f"NODE DIFF: no state.json under {run_dir} — nothing recorded"])
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _out([f"NODE DIFF: could not read {state_path}: {e}"])

    nodes = state.get("nodes") or {}
    rec = nodes.get(ns.node)
    if rec is None:
        return _out([f"NODE DIFF: no node {ns.node!r} in this run "
                     f"(nodes: {', '.join(sorted(nodes)) or 'none'})"])
    before, after = rec.get("tree_before"), rec.get("tree_after")
    if not before or not after:
        # Name the condition rather than the absence: an author who wanted this
        # and declared no scope should be told which one to add.
        return _out([
            f"NODE DIFF: node {ns.node!r} (status {rec.get('status')!r}) has no recorded "
            f"before/after trees.",
            "Both are recorded only for a node that declares `spec.writes`, is serialized "
            "on the `tree` token, and SUCCEEDED — a quarantined attempt was rolled back, so "
            "there is no tree it left (its patch is preserved in its phase dir instead).",
        ])

    out = [f"=== node {ns.node!r}: changed paths (recorded trees {before[:7]}..{after[:7]}) ==="]
    limit = _exclude_run_dir(run_dir)
    rc, names = _git("diff-tree", "-r", "--name-status", "--no-renames", before, after, *limit)
    if rc != 0:
        # The tree objects are unreferenced git objects: `git gc --prune` can
        # eventually remove them. Say so instead of printing an empty diff,
        # which reads as "this step changed nothing".
        return _out(out + [
            f"(could not read the recorded trees: {names.strip()})",
            "unreferenced git tree objects can be pruned by `git gc`; the recorded "
            "answer is gone, not empty",
        ])
    names = names.rstrip()
    if not names:
        return _out(out + ["(this step changed nothing)"])
    # Capped like the patch: a 3 000-file codemod's path list alone would
    # dominate the reviewer's prompt, and it is the same failure the diff cap
    # exists to prevent.
    out.extend(_cap(names.splitlines(), ns.max_lines, "changed path"))

    rc, patch = _git("diff-tree", "-r", "-p", "--no-renames", before, after, *limit)
    out.append("")
    out.append(f"=== node {ns.node!r}: diff ===")
    out.extend(_cap((patch.rstrip() or "(no textual change)").splitlines(),
                    ns.max_lines, "diff"))
    return _out(out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
