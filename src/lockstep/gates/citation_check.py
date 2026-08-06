"""Citation integrity -> Verdict (for research-report and run-postmortem: the
"never narrate in place of evidence" rule, promoted into the flow layer).

Two modes:

    ["python", "-m", "lockstep.gates.citation_check", "report.md",
     "--sources", "sources.json", "--per-section"]

Citations are `[S<n>]` tokens. Every cited id must exist in the manifest —
a JSON array of ids, an array of {"id": ...} objects, or {"sources": [...]}.
With --per-section, every `##` section must cite at least once (headings
containing "reference", "source", or "appendix" are exempt).

    ["python", "-m", "lockstep.gates.citation_check", "postmortem.md",
     "--paths", "{args.run_dir}"]

Citations are `[artifact: <relpath>]` tokens; every cited path must exist
under the given root. In both modes a document with no citations at all is a
blocker: an evidence-free document is exactly what this gate exists to stop.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ._common import emit, finding, flatten_text, read_doc, resolve_node_result

_EXEMPT_HEADING = re.compile(r"reference|source|appendix", re.I)


def _load_doc(ns) -> tuple[str | None, str, dict | None]:
    """(text, display_name, problem). --doc-node reads a sibling node's result
    via LOCKSTEP_PHASE_DIR; a JSON result (e.g. a draft map's aggregated array
    of section texts) is flattened back to prose so ^##-style scanning works."""
    if ns.doc_node:
        path, problem = resolve_node_result(ns.doc_node)
        if problem:
            return None, ns.doc_node, problem
        source = str(path)
    else:
        source = ns.doc
    text, problem = read_doc(source)
    if problem:
        return None, source, problem
    try:
        text = flatten_text(json.loads(text)) or text
    except ValueError:
        pass  # ordinary prose
    return text, source, None


def _manifest_ids(path: str) -> tuple[set[str] | None, dict | None]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, finding("blocker", "manifest", path, "sources manifest unreadable",
                             str(e), "check --sources")
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        return None, finding("blocker", "manifest", path,
                             "sources manifest is not a list",
                             type(data).__name__, "emit a JSON array of sources")
    ids: set[str] = set()
    for entry in data:
        if isinstance(entry, str):
            ids.add(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("id"), str):
            ids.add(entry["id"])
    return ids, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.citation_check")
    ap.add_argument("doc", nargs="?", default=None, help="the document to check")
    ap.add_argument("--doc-node", default=None,
                    help="read the document from this node's result (via LOCKSTEP_PHASE_DIR)")
    ap.add_argument("--sources", default=None, help="sources manifest (JSON); [S<n>] mode")
    ap.add_argument("--sources-node", default=None,
                    help="read the manifest from this node's result (via LOCKSTEP_PHASE_DIR)")
    ap.add_argument("--paths", default=None, help="root dir; [artifact: <relpath>] mode")
    ap.add_argument("--per-section", action="store_true",
                    help="in sources mode: every ## section must cite at least once")
    ns = ap.parse_args(argv)
    if bool(ns.doc) == bool(ns.doc_node):
        ap.error("pass exactly one of a document path or --doc-node")
    sources_mode = bool(ns.sources or ns.sources_node)
    if sources_mode == bool(ns.paths):
        ap.error("pass exactly one of --sources/--sources-node or --paths")
    text, doc_name, problem = _load_doc(ns)
    if problem:
        return emit([problem], "")
    if ns.sources_node:
        path, problem = resolve_node_result(ns.sources_node)
        if problem:
            return emit([problem], "")
        ns.sources = str(path)
    ns.doc = doc_name
    findings: list[dict] = []

    if sources_mode:
        ids, problem = _manifest_ids(ns.sources)
        if problem:
            return emit([problem], "")
        cited = set(re.findall(r"\[(S\d+)\]", text))
        if not cited:
            findings.append(
                finding("blocker", "no-citations", ns.doc,
                        "document contains no [S#] citations",
                        "an evidence-free document is what this gate exists to stop",
                        "cite the gathered sources")
            )
        for c in sorted(cited - ids):
            findings.append(
                finding("major", "dangling-citation", ns.doc,
                        f"cited source [{c}] does not exist in the manifest",
                        f"manifest ids: {sorted(ids)[:10]}",
                        "cite only gathered sources, or gather the missing one")
            )
        if ns.per_section:
            # Split on h2; the preamble before the first ## is exempt.
            pieces = re.split(r"^##\s+(.+)$", text, flags=re.M)
            for i in range(1, len(pieces) - 1, 2):
                heading, body = pieces[i].strip(), pieces[i + 1]
                if _EXEMPT_HEADING.search(heading):
                    continue
                if not re.search(r"\[S\d+\]", body):
                    findings.append(
                        finding("major", "uncited-section", ns.doc,
                                f'section "{heading}" cites no source',
                                "every claim-bearing section must point at evidence",
                                "add [S#] citations or fold the section away")
                    )
    else:
        root = Path(ns.paths)
        tokens = re.findall(r"\[artifact:\s*([^\]]+)\]", text)
        if not tokens:
            findings.append(
                finding("blocker", "no-citations", ns.doc,
                        "document contains no [artifact: <path>] citations",
                        "an evidence-free document is what this gate exists to stop",
                        "cite the run dir's artifacts")
            )
        for token in tokens:
            rel = token.strip()
            if not (root / rel).exists():
                findings.append(
                    finding("major", "dangling-citation", ns.doc,
                            f"cited artifact {rel!r} does not exist under {root}",
                            "a citation must point at a real file",
                            "fix the path or drop the claim")
                )

    return emit(findings, "every citation resolves", f"{len(findings)} citation problem(s)")


if __name__ == "__main__":
    sys.exit(main())
