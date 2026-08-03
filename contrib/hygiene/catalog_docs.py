#!/usr/bin/env python
"""catalog_docs.py — deterministic catalog + rules for this repo's docs/.

    python contrib/hygiene/catalog_docs.py [--root docs]

Emits a PathManifest of the files rules cannot place, plus the full proposal for
the ones they can. Zero model cost.

THE STRUCTURE, and why it is this one. Documents are grouped by **authority and
lifecycle**, not by subject, because that is the question a reader actually has:
"can I rely on this?"

    docs/spec/       the contract and what qualifies it — spec + amendments
                     bind, addenda are informative, and the deviations register
                     records where implementation departs. `type` says which.
    docs/guides/     how to use it. Cheap to correct; but some are promises to
                     a reader, and changing what they promise is not a fix.
    docs/proposals/  design documents and accepted work orders. A proposal
                     carries no authority by sitting here; an accepted plan's
                     authority comes from the commit that adopted it.
    docs/audits/     point-in-time findings, kept as written.
    docs/notes/      working material. Nothing here binds.

The alternative — grouping by subject, e.g. everything about the cockpit
together — was rejected: it puts a proposal next to a specification, and the
whole reason this repo has an audit gate is that those two must never be
confused.
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

# (rule_id, filename pattern, bundle, okf type). First match wins; ORDER IS THE
# POLICY. A file matching two rules is governed by the earlier one, not
# ambiguous — genuine conflicts are same-precedence claims, reported separately.
RULES = [
    ("D-001", r"^SPEC\.md$",                    "spec",      "specification"),
    ("D-002", r"^AMENDMENTS-r\d+\.md$",         "spec",      "amendment"),
    ("D-003", r"^ADDENDUM-[A-Z]-.*\.md$",       "spec",      "addendum"),
    ("D-004", r"^DEVIATIONS\.md$",              "spec",      "register"),
    ("D-010", r"^THEORY-OF-OPERATIONS\.md$",    "guides",    "theory-of-ops"),
    ("D-011", r"^COCKPIT-THEORY-OF-OPERATIONS\.md$", "guides", "theory-of-ops"),
    ("D-012", r"^COCKPIT-FOR-DOMAIN-EXPERTS\.md$",   "guides", "guide"),
    ("D-013", r"^DRIVING-LOCKSTEP\.md$",        "guides",    "guide"),
    ("D-014", r"^FLOW-AUTHORING\.md$",          "guides",    "guide"),
    ("D-020", r"^PROPOSAL-.*\.md$",             "proposals", "proposal"),
    ("D-021", r".*work-order\.md$",             "proposals", "plan"),
    ("D-030", r"^AUDIT-.*\.md$",                "audits",    "report"),
    ("D-040", r".*-NOTES\.md$",                 "notes",     "notes"),
]

# Vendored third-party references are not ours to reorganise or annotate.
EXCLUDE_DIRS = {"okf"}

# H1 only. Falling back to any heading level looked like a fix and was worse:
# two audits open with `## Verdict: PASS`, so they got titled by their outcome
# rather than their subject. A document with no H1 is named from its filename,
# which is at least about what it covers.
TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# OKF lifecycle. A superseded revision that sits as an undifferentiated peer of
# the current one is a trap: the reader has no way to tell which is live. The
# format has `status` for exactly this, so use it rather than relying on a
# filename suffix nobody has explained.
SUPERSEDED = {
    "PROPOSAL-domain-cockpit.md": "PROPOSAL-domain-cockpit-rev7.md",
    "PROPOSAL-domain-cockpit-rev5.md": "PROPOSAL-domain-cockpit-rev7.md",
    "PROPOSAL-domain-cockpit-rev6.md": "PROPOSAL-domain-cockpit-rev7.md",
}

# Explicit lifecycle where the default would assert something untrue. OKF
# defaults `status` to `stable`, which for a point-in-time audit would claim
# currency the document never had, and for a deferred design would claim
# settledness it explicitly disclaims.
STATUS = {
    "PROPOSAL-unattended-mode.md": "draft",       # declares itself unscheduled
    "repo-hygiene-work-order.md": "stable",       # accepted, partly executed
}


def tracked_docs(root: Path) -> list[Path]:
    """Tracked plus untracked-not-ignored, so a doc written today is catalogued.
    git decides the boundary, which keeps runs/ and build artifacts invisible."""
    found: set[str] = set()
    for extra in (["ls-files", "-z"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        try:
            proc = subprocess.run(["git", "-c", "core.quotepath=off", *extra, str(root)],
                                  capture_output=True, encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"git unavailable: {e}", file=sys.stderr)
            return []
        if proc.returncode == 0:
            found.update(f.strip() for f in proc.stdout.split("\x00") if f.strip())
    out = []
    for f in sorted(found):
        p = Path(f)
        if p.suffix != ".md" or p.name in ("index.md", "log.md"):
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if len(p.parts) != 2:      # already inside a bundle; nothing to place
            continue
        out.append(p)
    return out


def classify(name: str) -> tuple[str, str, str]:
    for rule_id, pattern, bundle, okf_type in RULES:
        if re.match(pattern, name):
            return rule_id, bundle, okf_type
    return "-", "", ""


def conflicts_for(name: str) -> list[str]:
    hits = [r[0] for r in RULES if re.match(r[1], name)]
    return hits if len(hits) > 1 else []


def title_of(path: Path) -> str:
    try:
        m = TITLE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    title = m.group(1) if m else path.stem.replace("-", " ").replace("_", " ")
    # Several audits share a generic H1 ("Spec-vs-Implementation Audit Report").
    # An index whose rows are indistinguishable does not identify anything, so
    # fold in the date the filename already carries.
    stamp = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if stamp and stamp.group(1) not in title:
        title = f"{title} ({stamp.group(1)})"
    return title


def write_mission(line: str) -> None:
    phase = os.environ.get("LOCKSTEP_PHASE_DIR")
    if phase:
        try:
            Path(phase, "mission.txt").write_text(line + "\n", encoding="utf-8")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="docs")
    ap.add_argument("--out", default=None, help="write the full proposal here")
    ns = ap.parse_args(argv)

    root = Path(ns.root)
    files = tracked_docs(root)

    placed, residue, conflicts = [], [], []
    for p in files:
        rule_id, bundle, okf_type = classify(p.name)
        clash = conflicts_for(p.name)
        if clash:
            conflicts.append({"path": str(p).replace("\\", "/"), "rules": clash})
        entry = {
            "path": str(p).replace("\\", "/"),
            "target_path": f"{root.as_posix()}/{bundle}/{p.name}" if bundle else None,
            "okf_type": okf_type,
            "title": title_of(p),
            "rule_ref": rule_id,
            "status": ("deprecated" if p.name in SUPERSEDED
                       else STATUS.get(p.name)),
            "superseded_by": SUPERSEDED.get(p.name),
            "sha": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
        }
        (placed if bundle and not clash else residue).append(entry)

    manifest = {
        "schema_version": "1.0",
        "files": [f"{e['path']}|{e['sha']}" for e in residue],
        "notes": (f"{len(files)} documents; {len(placed)} placed by rule; "
                  f"{len(residue)} need judgment; {len(conflicts)} rule conflict(s)"),
        "placed": placed,
        "conflicts": conflicts,
    }
    write_mission(f"catalog: {len(files)} docs, {len(placed)} by rule, "
                  f"{len(residue)} need judgment")
    if ns.out:
        Path(ns.out).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
