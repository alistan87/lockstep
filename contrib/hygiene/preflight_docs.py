#!/usr/bin/env python
"""preflight_docs.py — the tamper check between approval and apply.

    python contrib/hygiene/preflight_docs.py <manifest.json> --approved-sha <sha>

Segmentation means the human approves in one run and the work happens in
another. Between those two moments the manifest is an ordinary file on disk that
anything could rewrite — a re-run of the catalog after a doc was added, a stray
edit, a different branch. Applying a manifest that is not the one that was
approved would make the approval a formality, and the record would still say a
human agreed.

So: recompute the digest, compare, and hard-block on any difference. Passing
`--approved-sha -` skips the comparison and says so loudly in the verdict, which
is the honest shape for a first run where no digest was captured yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def manifest_sha(path: Path) -> str:
    """Digest the SEMANTIC content — the moves — not the file bytes.

    Re-running the catalog reformats or reorders nothing of consequence, but it
    does rewrite timestamps and shas of unchanged files elsewhere in the JSON.
    Hashing raw bytes would cry tamper at a no-op, and a tamper check that fires
    on nothing is one people learn to bypass.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("placed", []) if isinstance(data, dict) else data
    canonical = json.dumps(
        sorted(((e.get("path"), e.get("target_path"), e.get("okf_type")) for e in entries)),
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest")
    ap.add_argument("--approved-sha", required=True)
    ns = ap.parse_args(argv)

    def verdict(v: str, reason: str, findings: list[dict] | None = None) -> int:
        print(json.dumps({"findings": findings or [], "verdict": v, "reason": reason}))
        return 0

    path = Path(ns.manifest)
    if not path.is_file():
        return verdict("block", f"{path} is missing — re-run the propose segment", [{
            "severity": "blocker", "category": "missing-manifest", "file": str(path),
            "line": None, "claim": "no manifest to apply", "evidence": "deterministic check",
            "fix_hint": "run docs-okf-propose first",
        }])

    try:
        actual = manifest_sha(path)
    except (OSError, ValueError) as e:
        return verdict("block", f"manifest unreadable: {e}")

    if ns.approved_sha in ("-", ""):
        return verdict("pass",
                       f"NO approved digest supplied — applying {actual} unchecked. "
                       f"Pass --arg approved_sha={actual} to make the next run tamper-evident.")

    if actual != ns.approved_sha:
        return verdict("block",
                       f"manifest changed after approval: approved {ns.approved_sha}, "
                       f"found {actual}", [{
                           "severity": "blocker", "category": "tamper", "file": str(path),
                           "line": None,
                           "claim": "the manifest on disk is not the one that was approved",
                           "evidence": "deterministic check",
                           "fix_hint": "re-run propose and approve the new manifest",
                       }])
    return verdict("pass", f"manifest digest {actual} matches the approved one")


if __name__ == "__main__":
    raise SystemExit(main())
