#!/usr/bin/env python
"""validate_docs_manifest.py — the deterministic gate over a docs manifest.

    python contrib/hygiene/validate_docs_manifest.py <manifest.json>

A shell gate: free, deterministic, and unable to be talked out of its answer.
It emits a Verdict, so a block heals nothing here but tells the human exactly
which entries are unsafe.

It also runs the apply engine in `--check` mode, which is the point: the thing
that verifies the manifest is the same code that will execute it. A checker that
approximates the executor eventually disagrees with it, and the disagreement is
discovered by damaging a repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_docs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest")
    ns = ap.parse_args(argv)

    def verdict(v: str, reason: str, findings: list[dict]) -> int:
        print(json.dumps({"findings": findings, "verdict": v, "reason": reason}))
        return 0        # the VERDICT carries the outcome; the node succeeded

    # Parse ONCE and reuse. The second read was wrapped in a bare
    # `except: pass` that silently discarded every conflict and residue note,
    # after which the verdict stated a note count it had no basis for.
    try:
        raw = json.loads(Path(ns.manifest).read_text(encoding="utf-8"))
        entries = apply_docs.load_manifest(ns.manifest)
    except (OSError, ValueError) as e:
        return verdict("block", f"manifest unreadable: {e}", [{
            "severity": "blocker", "category": "unreadable", "file": ns.manifest,
            "line": None, "claim": str(e), "evidence": "deterministic check",
            "fix_hint": "re-run the catalog",
        }])

    problems = apply_docs.check_manifest(entries)

    # Run the EXECUTOR in --check mode. The docstring claimed this all along and
    # it was false: only two pure helpers were called, so every step after
    # manifest checking — the rewrite, the injection, the index write — was
    # unverified until the real apply ran it for the first time.
    import contextlib, io as _io
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = apply_docs.main(["--manifest", ns.manifest, "--check"])
    except Exception as e:                      # noqa: BLE001 - any executor fault blocks
        rc, _ = 1, buf.write(f"{type(e).__name__}: {e}")
    if rc != 0:
        problems.append(f"apply --check returned {rc}: {buf.getvalue().strip()[:300]}")
    findings = [{
        "severity": "blocker", "category": "manifest", "file": p.split(":")[0],
        "line": None, "claim": p, "evidence": "deterministic check",
        "fix_hint": "correct the rule or the entry",
    } for p in problems]

    # Residue and conflicts are the catalog's own admission that rules were
    # insufficient. Not blocking — a human decides — but never silent.
    if True:
        for c in raw.get("conflicts", []):
            findings.append({
                "severity": "major", "category": "rule-conflict", "file": c["path"],
                "line": None,
                "claim": f"rules {', '.join(c['rules'])} claim this file at equal precedence",
                "evidence": "deterministic check",
                "fix_hint": "decide which rule wins and reorder them",
            })
        for item in raw.get("files", []):
            findings.append({
                "severity": "minor", "category": "unplaced", "file": item.split("|")[0],
                "line": None, "claim": "no rule places this document",
                "evidence": "deterministic check",
                "fix_hint": "add a rule, or place it by hand",
            })

    if problems:
        return verdict("block", f"{len(problems)} blocking violation(s) in "
                                f"{len(entries)} entries", findings)
    return verdict("pass", f"{len(entries)} entries validated; "
                           f"{len(findings)} non-blocking note(s)", findings)


if __name__ == "__main__":
    raise SystemExit(main())
