"""Staleness gate -> Verdict: the tree still matches what was approved.

Usage from a flow (the codemod-apply preflight):
    ["python", "-m", "lockstep.gates.fingerprint_check", "codemod-orders.json"]

The file is a JSON array (or {"sources": [...]}) of entries carrying a
`path` or `file` plus a `fingerprint` — the first 16 hex chars of the sha256
of the file content the entry was written AGAINST. Any mismatch is a blocker:
propose/approve/apply spans sessions, and the span is exactly where the tree
drifts under the approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from ._common import emit, finding


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.fingerprint_check")
    ap.add_argument("orders", help="JSON file of entries with path/file + fingerprint")
    ap.add_argument("--root", default=".", help="paths resolve relative to this")
    ns = ap.parse_args(argv)
    try:
        data = json.loads(Path(ns.orders).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return emit([finding("blocker", "orders", ns.orders, "orders file unreadable",
                             str(e), "check the path; run the propose flow first")], "")
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        return emit([finding("blocker", "orders", ns.orders, "orders file is not a list",
                             type(data).__name__, "regenerate with the propose flow")], "")
    findings: list[dict] = []
    checked = 0
    for entry in data:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path") or entry.get("file")
        expected = entry.get("fingerprint")
        if not rel or not expected:
            continue
        checked += 1
        target = Path(ns.root) / rel
        if not target.exists():
            findings.append(finding(
                "blocker", "stale", rel, "file no longer exists",
                f"the approved order was written against fingerprint {expected}",
                "re-run the propose flow against the current tree"))
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
        if actual != expected:
            findings.append(finding(
                "blocker", "stale", rel,
                "file changed since the orders were written",
                f"approved against {expected}, tree now has {actual}",
                "re-run the propose flow; the human approved a different tree"))
    if checked == 0:
        findings.append(finding(
            "blocker", "orders", ns.orders, "no fingerprinted entries to check",
            "an empty staleness check would pass vacuously",
            "regenerate with the propose flow"))
    return emit(findings, f"all {checked} fingerprint(s) match the approved tree",
                f"{len(findings)} staleness problem(s)")


if __name__ == "__main__":
    sys.exit(main())
