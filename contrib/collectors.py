#!/usr/bin/env python
"""collectors.py — deterministic JSON collectors for the factory flows (D-series).

    python contrib/collectors.py git-log [--days N | --since-last-tag]
    python contrib/collectors.py runs [--runs-dir runs]
    python contrib/collectors.py pytest
    python contrib/collectors.py sources <dir> [--suffixes .md,.txt,.rst]

Each subcommand prints one JSON object to stdout and exits 0 (a collector that
found nothing reports an empty collection, not a failure — the narrative and
its number-provenance gate decide what emptiness means). No model, no tokens:
these are the mechanical half of a status digest or a report, and every number
a narrative may quote must appear in one of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip()


def collect_git_log(days: int | None, since_last_tag: bool) -> dict:
    code, tag = _git("describe", "--tags", "--abbrev=0")
    last_tag = tag if code == 0 else None
    # \x1f (unit separator), not NUL: Windows CreateProcess rejects argv
    # containing embedded NULs.
    log_args = ["log", "--pretty=format:%h\x1f%s"]
    if since_last_tag and last_tag:
        rng = f"{last_tag}..HEAD"
        log_args.append(rng)
    elif days is not None:
        rng = f"last {days} days"
        log_args.append(f"--since={days} days ago")
    else:
        rng = "all"
    code, out = _git(*log_args)
    commits = []
    if code == 0 and out:
        for line in out.splitlines():
            sha, _, subject = line.partition("\x1f")
            commits.append({"sha": sha, "subject": subject})
    return {"range": rng, "last_tag": last_tag, "count": len(commits), "commits": commits}


def collect_runs(runs_dir: Path) -> dict:
    runs = []
    if runs_dir.is_dir():
        for d in sorted(runs_dir.iterdir()):
            state_path = d / "state.json"
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            statuses = [n.get("status") for n in state.get("nodes", {}).values()]
            runs.append({
                "dir": d.name,
                "flow": state.get("flow_name", ""),
                "started": state.get("started_at", ""),
                "agent_spawns": state.get("token_spawns", 0),
                "done": sum(1 for s in statuses if s == "done"),
                "failed": sum(1 for s in statuses if s == "failed"),
            })
    return {"count": len(runs), "runs": runs}


def collect_pytest() -> dict:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    lines = [l for l in ((p.stdout or "") + (p.stderr or "")).splitlines() if l.strip()]
    return {
        "command": "pytest -q",
        "exit_code": p.returncode,
        "summary": lines[-1] if lines else "no output",
    }


def collect_grep(pattern: str, suffixes: list[str], root: Path) -> dict:
    """Files whose CONTENT matches `pattern`, as a SourceManifest — the same
    id/path/fingerprint shape as `sources`, so per-item caching invalidates on
    edits (the codemod pair's discovery step)."""
    import re as _re

    rx = _re.compile(pattern)
    entries = []
    i = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in suffixes:
            continue
        # Exclusions apply to the path UNDER root only: p.parts includes the
        # absolute prefix, and a repo living under a directory named "runs"
        # would otherwise silently discover zero sites.
        parts = {q.lower() for q in p.relative_to(root).parts}
        if {".git", "runs", ".venv", "node_modules", "__pycache__"} & parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if rx.search(text):
            i += 1
            entries.append({
                "id": f"S{i}",
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "fingerprint": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
            })
    return {"schema_version": "1.0", "sources": entries}


def collect_run_facts(run_dir: Path) -> dict:
    """Mechanical facts about a run dir, for the post-mortem flow (D7):
    statuses, attempts, errors, verdicts, and the journal's integrity."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from lockstep.state import verify_trace

    facts: dict = {"run_dir": str(run_dir), "artifacts": []}
    try:
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        facts["error"] = f"state.json unreadable: {e}"
        return facts
    facts["flow"] = state.get("flow_name", "")
    facts["started"] = state.get("started_at", "")
    facts["agent_spawns"] = state.get("token_spawns", 0)
    facts["verdicts"] = state.get("verdicts", {})
    facts["nodes"] = {
        nid: {
            "status": rec.get("status"),
            "attempts": rec.get("attempts", 0),
            "heal_round": rec.get("heal_round", 0),
            "error": rec.get("error"),
            "invalidated_by": rec.get("invalidated_by"),
        }
        for nid, rec in state.get("nodes", {}).items()
    }
    ok, head, bad, detail = verify_trace(run_dir)
    facts["trace"] = {"ok": ok, "detail": detail, "first_bad_line": bad}
    everything = sorted(
        str(p.relative_to(run_dir)).replace("\\", "/")
        for p in run_dir.rglob("*") if p.is_file()
    )
    facts["artifacts"] = everything[:400]
    facts["artifacts_truncated"] = max(0, len(everything) - 400)  # no silent caps
    return facts


def collect_sources(root: Path, suffixes: list[str]) -> dict:
    entries = []
    if root.is_dir():
        files = sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes
        )
        for i, p in enumerate(files, start=1):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            entries.append({
                "id": f"S{i}",
                "path": str(p).replace("\\", "/"),
                "fingerprint": digest,
            })
    return {"schema_version": "1.0", "sources": entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("git-log")
    g.add_argument("--days", type=int, default=None)
    g.add_argument("--since-last-tag", action="store_true")
    r = sub.add_parser("runs")
    r.add_argument("--runs-dir", default="runs")
    sub.add_parser("pytest")
    s = sub.add_parser("sources")
    s.add_argument("dir")
    s.add_argument("--suffixes", default=".md,.txt,.rst")
    gr = sub.add_parser("grep")
    gr.add_argument("pattern")
    gr.add_argument("--suffixes", default=".py")
    gr.add_argument("--root", default=".")
    c = sub.add_parser("cat", help="print a JSON file verbatim (a file becomes a node result)")
    c.add_argument("file")
    rf = sub.add_parser("run-facts")
    rf.add_argument("run_dir")
    ns = ap.parse_args(argv)
    if ns.cmd == "git-log":
        out = collect_git_log(ns.days, ns.since_last_tag)
    elif ns.cmd == "runs":
        out = collect_runs(Path(ns.runs_dir))
    elif ns.cmd == "pytest":
        out = collect_pytest()
    elif ns.cmd == "grep":
        suffixes = [s.strip() for s in ns.suffixes.split(",") if s.strip()]
        out = collect_grep(ns.pattern, suffixes, Path(ns.root))
    elif ns.cmd == "cat":
        out = json.loads(Path(ns.file).read_text(encoding="utf-8"))
    elif ns.cmd == "run-facts":
        out = collect_run_facts(Path(ns.run_dir))
    else:
        suffixes = [s.strip() for s in ns.suffixes.split(",") if s.strip()]
        out = collect_sources(Path(ns.dir), suffixes)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
