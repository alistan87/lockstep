"""Number provenance -> Verdict (for status-digest: "never quote a cost from
memory", mechanised for a whole document class).

Usage from a flow:
    ["python", "-m", "lockstep.gates.numbers_check", "digest.md",
     "--from", "phases/collect-git/result.json", "--from", "phases/collect-cost/result.json"]

Every numeral in the document must appear in at least one collector's JSON
output. Collector numbers are gathered recursively: JSON numbers in canonical
form(s), plus digit runs inside JSON strings. Deliberate allowances, so prose
stays writable: integers 0-12 (list positions, "step 3"), years 1900-2100,
and anything inside ISO dates (2026-08-04), clock times (14:30), or dotted
version strings (0.3.1) — those are masked before scanning. --allow adds a
regex whose matches are masked too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ._common import emit, finding, read_doc, resolve_node_result

_MASKS = [
    re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?"),  # ISO dates / timestamps
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),  # clock times
    # Dotted versions (0.3.1). No \b on the left: "v0.3.1" has no word
    # boundary between the v and the 0, and version strings usually arrive
    # exactly like that.
    re.compile(r"\d+(?:\.\d+){2,}"),
]
_NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _known_forms(value, strings: set[str], values: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        # By VALUE, not string form: "87.50%" in prose is the same sourced
        # number as collector 87.5, and a display-format gate failure would
        # break the promise that prose stays writable.
        values.add(float(value))
    elif isinstance(value, str):
        for m in _NUMERAL.finditer(value):
            token = m.group(0).replace(",", "")
            strings.add(token)
            try:
                values.add(float(token))
            except ValueError:
                pass
    elif isinstance(value, list):
        for v in value:
            _known_forms(v, strings, values)
    elif isinstance(value, dict):
        for k, v in value.items():
            _known_forms(k, strings, values)
            _known_forms(v, strings, values)


def _allowed(token: str) -> bool:
    try:
        value = float(token)
    except ValueError:
        return False
    if 0 <= value <= 12 and value == int(value):
        return True  # list positions, tiny counts — words in disguise
    return 1900 <= value <= 2100 and value == int(value)  # bare years


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.numbers_check")
    ap.add_argument("doc", nargs="?", default=None, help="the document to check")
    ap.add_argument("--doc-node", default=None,
                    help="read the document from this node's result (via LOCKSTEP_PHASE_DIR)")
    ap.add_argument("--from", dest="sources", action="append", default=[],
                    metavar="JSON", help="a collector output file (repeatable)")
    ap.add_argument("--from-node", dest="source_nodes", action="append", default=[],
                    metavar="NODE", help="a collector node's result (repeatable)")
    ap.add_argument("--allow", action="append", default=[], metavar="REGEX",
                    help="mask matches of this regex before scanning (repeatable)")
    ns = ap.parse_args(argv)
    if bool(ns.doc) == bool(ns.doc_node):
        ap.error("pass exactly one of a document path or --doc-node")
    if not ns.sources and not ns.source_nodes:
        ap.error("at least one --from file or --from-node is required")
    for node_id in ns.source_nodes:
        path, problem = resolve_node_result(node_id)
        if problem:
            return emit([problem], "")
        ns.sources.append(str(path))
    if ns.doc_node:
        path, problem = resolve_node_result(ns.doc_node)
        if problem:
            return emit([problem], "")
        ns.doc = str(path)
    text, problem = read_doc(ns.doc)
    if problem:
        return emit([problem], "")

    known: set[str] = set()
    known_values: set[float] = set()
    for src in ns.sources:
        try:
            _known_forms(json.loads(Path(src).read_text(encoding="utf-8")),
                         known, known_values)
        except (OSError, ValueError) as e:
            return emit(
                [finding("blocker", "collector", src, "collector output unreadable",
                         str(e), "check the --from path; collectors must emit JSON")],
                "",
            )

    masked = text
    for pattern in _MASKS + [re.compile(p) for p in ns.allow]:
        masked = pattern.sub(" ", masked)

    findings: list[dict] = []
    seen: set[str] = set()
    for m in _NUMERAL.finditer(masked):
        token = m.group(0).replace(",", "").rstrip(".")
        if not token or token in seen:
            continue
        seen.add(token)
        if _allowed(token) or token in known:
            continue
        try:
            if float(token) in known_values:
                continue
        except ValueError:
            pass
        start = max(0, m.start() - 40)
        context = masked[start:m.end() + 40].replace("\n", " ").strip()
        findings.append(
            finding("major", "unsourced-number", ns.doc,
                    f"numeral {m.group(0)!r} appears in no collector's output",
                    f"…{context}…",
                    "quote only collector numbers, or add the collector that produces it")
        )
    return emit(findings, f"every numeral traces to a collector ({len(ns.sources)} consulted)",
                f"{len(findings)} unsourced number(s)")


if __name__ == "__main__":
    sys.exit(main())
