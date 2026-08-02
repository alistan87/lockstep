#!/usr/bin/env python
"""hygiene_catalog.py — deterministic file catalog + rule engine (demo).

    python contrib/demo/hygiene_catalog.py --area docs --max-ambiguous 12

A miniature of the repo-hygiene work order's §1.1 catalog and §1.3 rule engine,
built to demonstrate the cockpit end to end without DuckDB, OKF, or an apply
engine. Everything here is deterministic and free: it walks tracked markdown,
reads front matter, applies ordered rules, and assigns each file a disposition.

The cost-escalation principle is the whole point: thousands of files can pass
through the deterministic stage, and only the AMBIGUOUS residue ever reaches a
model spawn. This script's job is to make that residue small and to say exactly
how small, in units the human agreed to.

Emits a PathManifest on stdout so a map node can fan out over `files`, and
writes `phases/<node>/mission.txt` — one plain line the cockpit's MISSION pane
renders verbatim (still mechanical: a file copy, no model).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Ordered rules. First match wins, and the ORDER is the policy: a file matching
# two rules is not ambiguous, it is governed by the earlier one. Genuine
# conflicts are the ones two rules claim with equal precedence, which is what
# the clarify gate exists to ask about.
RULES = [
    ("R-001", r"^docs/SPEC\.md$",                    "spec",        "conforming"),
    ("R-002", r"^docs/AMENDMENTS-.*\.md$",           "amendment",   "conforming"),
    ("R-003", r"^docs/AUDIT-.*\.md$",                "report",      "conforming"),
    ("R-004", r"^docs/PROPOSAL-.*\.md$",             "proposal",    "conforming"),
    ("R-005", r"^docs/ADDENDUM-.*\.md$",             "addendum",    "conforming"),
    ("R-010", r"^flows/.*/README\.md$",              "index",       "conforming"),
    ("R-011", r"^personas/.*\.md$",                  "persona",     "conforming"),
    ("R-012", r"^\.claude/skills/.*/SKILL\.md$",     "skill",       "conforming"),
    ("R-020", r"^README\.md$",                       "index",       "conforming"),
    ("R-021", r"^CLAUDE\.md$",                       "index",       "conforming"),
    # Deliberately overlapping with R-004 on one shape, to exercise the clarify
    # gate: a work order IS a proposal by path but a plan by content.
    ("R-030", r"^docs/.*work-order\.md$",            "plan",        "ambiguous"),
    ("R-031", r"^docs/.*-NOTES\.md$",                "notes",       "ambiguous"),
    # R-032 overlaps R-030 on purpose: a per-subject rule and a per-document-type
    # rule can both legitimately claim the same file, and NEITHER is wrong. This
    # is what a rule conflict actually looks like in practice, and no amount of
    # model judgment can settle it — only the person who owns the taxonomy can.
    # It is the clarify gate's reason to exist.
    ("R-032", r"^docs/repo-.*\.md$",                 "wiki",        "ambiguous"),
]

# Scope: markdown only. The work order's real catalog spans every `kind` and
# resolves binaries to `excluded` by deterministic rule so their CONTENT is
# never model input; this demo narrows to `.md` at the walk instead. An earlier
# version also carried a binary-suffix exclusion list here, which was dead code
# — nothing surviving the `.md` filter can end in `.png` — and dead safety
# checks are worse than absent ones, because they read as protection that is
# not there.
CATALOGUED_SUFFIX = ".md"


def tracked_files(area: str) -> list[str]:
    """Tracked files PLUS untracked-but-not-ignored ones.

    git decides the boundary — so `runs/`, `.venv/`, and everything else in
    .gitignore stays invisible, which on a machine with proprietary data is the
    line that matters. But a hygiene audit that only sees COMMITTED files is
    useless in exactly the case it is wanted: the new, unfiled document nobody
    has decided where to put yet is the whole reason to run this.
    """
    out: list[str] = []
    for extra in (["ls-files", "-z"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        try:
            proc = subprocess.run(["git", "-c", "core.quotepath=off", *extra],
                                  capture_output=True, encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"git unavailable: {e}", file=sys.stderr)
            return []
        if proc.returncode != 0:
            print(f"git {extra[0]} failed: {proc.stderr}", file=sys.stderr)
            continue
        for f in proc.stdout.split("\x00"):
            f = f.strip()
            if not f or not f.endswith(CATALOGUED_SUFFIX):
                continue
            if area and not f.startswith(area):
                continue
            out.append(f)
    return sorted(set(out))


def classify(path: str) -> tuple[str, str, str]:
    """(rule_id, okf_type, disposition). Unmatched is `unknown`, not a guess."""
    for rule_id, pattern, okf_type, disposition in RULES:
        if re.match(pattern, path.replace("\\", "/")):
            return rule_id, okf_type, disposition
    return "-", "unclassified", "unknown"


def conflicts_for(path: str) -> list[str]:
    hits = [r[0] for r in RULES if re.match(r[1], path.replace("\\", "/"))]
    return hits if len(hits) > 1 else []


def inspect(path: str) -> dict:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return {"path": path, "kind": "unreadable", "sha": "unreadable",
                "has_frontmatter": False, "links": 0}
    text = raw.decode("utf-8", errors="replace")
    return {
        "path": path,
        "sha": hashlib.sha256(raw).hexdigest()[:16],
        "size": len(raw),
        "has_frontmatter": bool(FRONTMATTER.match(text)),
        "links": len(WIKILINK.findall(text)) + len(MDLINK.findall(text)),
        "headings": len([ln for ln in text.splitlines() if ln.startswith("#")]),
    }


def write_mission(line: str) -> None:
    """One plain line for the cockpit's MISSION pane. The extension the work
    order asked for (§6.1): any node may write phases/<node>/mission.txt and
    the DE-tier renderer includes it verbatim."""
    phase = os.environ.get("LOCKSTEP_PHASE_DIR")
    if not phase:
        return
    try:
        Path(phase, "mission.txt").write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--area", default="docs", help="path prefix to catalog")
    ap.add_argument("--max-ambiguous", type=int, default=12,
                    help="cap on files sent to model classification")
    ap.add_argument("--out", default=None, help="also write the full catalog here")
    ns = ap.parse_args(argv)

    files = tracked_files(ns.area)
    rows, conflicts = [], []
    counts = {"conforming": 0, "ambiguous": 0, "unknown": 0, "excluded": 0}
    for f in files:
        rule_id, okf_type, disposition = classify(f)
        row = {**inspect(f), "rule_id": rule_id, "okf_type": okf_type,
               "disposition": disposition}
        rows.append(row)
        counts[disposition] = counts.get(disposition, 0) + 1
        clash = conflicts_for(f)
        if clash:
            conflicts.append({"path": f, "rules": clash})

    # Only the residue is sent onward, and the cap is honest about truncation:
    # a silently truncated fan-out reads as "we checked everything".
    residue = [r for r in rows if r["disposition"] in ("ambiguous", "unknown")]
    residue.sort(key=lambda r: r["path"])
    sent = residue[:ns.max_ambiguous]
    entries = [f"{r['path']}|{r['sha']}" for r in sent]

    notes = (f"{len(files)} files catalogued in {ns.area or 'repo'}; "
             f"{counts['conforming']} resolved by rules; "
             f"{len(residue)} need judgment; {len(sent)} sent to classification")
    if len(residue) > len(sent):
        notes += f" (TRUNCATED — {len(residue) - len(sent)} not sent; raise --max-ambiguous)"
    if conflicts:
        notes += f"; {len(conflicts)} rule conflict(s)"

    write_mission(f"catalog: {len(files)} files, {counts['conforming']} by rule, "
                  f"{len(residue)} need judgment")

    if ns.out:
        try:
            Path(ns.out).write_text(json.dumps(
                {"files": rows, "conflicts": conflicts, "counts": counts}, indent=2),
                encoding="utf-8")
        except OSError as e:
            print(f"warning: could not write {ns.out}: {e}", file=sys.stderr)

    print(json.dumps({
        "schema_version": "1.0",
        "files": entries,
        "notes": notes,
        "conflicts": conflicts,
        "counts": counts,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
