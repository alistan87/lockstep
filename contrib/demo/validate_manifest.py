#!/usr/bin/env python
"""validate_manifest.py — the deterministic gate over a proposed manifest.

    python contrib/demo/validate_manifest.py <manifest-json-or-@file>

A machine-decidable gate, so it is a SHELL gate: deterministic, token-free, and
the spec's preferred form. It emits a Verdict, which is what makes it a gate the
engine can heal against — a block here sends the classifier back with the
specific violations in its re-prompt, and costs no human attention at all.

What it refuses, and why each one is a real failure mode rather than a
formality:

- **schema**: already enforced by the contract, re-checked because a heal round
  must never proceed on a shape the apply engine cannot execute.
- **path collisions**: two files proposed into the same destination silently
  destroys one of them.
- **illegal targets**: a destination outside the allowed roots, absolute, or
  containing `..`, is an escape from the repo — the one thing an apply engine
  must never be asked to do.
- **self-moves and no-op moves**: a move whose target equals its source is a
  classifier that did not understand the question.
- **missing targets**: `move` without `target_path` is unexecutable.
- **duplicates**: the same path dispositioned twice, two different ways.
- **ordering**: entries must be sorted by path, so the same repo state yields a
  byte-identical manifest and re-running proposes nothing new (idempotency).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_ROOTS = ("docs/", "atoms/", "flows/", "personas/", "notes/", "archive/")
MOVE_ACTIONS = {"move", "rename", "move+inject_okf"}


def load_entries(spec: str) -> list[dict]:
    """The map node's collected output is an ARRAY OF ARRAYS — one per item —
    and large values spill to a file, in which case the argv carries the path."""
    text = spec
    if spec.startswith("@"):
        text = Path(spec[1:]).read_text(encoding="utf-8", errors="replace")
    else:
        candidate = Path(spec)
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    try:
        value = json.loads(text)
    except ValueError as e:
        raise ValueError(f"manifest is not JSON: {e}") from e

    entries: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            entries.append(node)

    walk(value)
    return entries


def validate(entries: list[dict]) -> tuple[list[dict], str]:
    findings: list[dict] = []

    def add(severity: str, category: str, path: str, claim: str) -> None:
        findings.append({"severity": severity, "category": category, "file": path,
                         "line": None, "claim": claim, "evidence": "deterministic check",
                         "fix_hint": "re-emit this entry corrected"})

    seen_targets: dict[str, str] = {}
    seen_paths: set[str] = set()

    for e in entries:
        path = str(e.get("path", "") or "")
        action = str(e.get("action", "") or "")
        target = e.get("target_path")

        if not path:
            add("blocker", "schema", "?", "entry has no path")
            continue
        if path in seen_paths:
            add("blocker", "duplicate", path, "the same file is dispositioned more than once")
        seen_paths.add(path)

        if action in MOVE_ACTIONS:
            if not target:
                add("blocker", "unexecutable", path, f"action '{action}' with no target_path")
                continue
            t = str(target).replace("\\", "/")
            if t.startswith("/") or (len(t) > 1 and t[1] == ":") or ".." in t.split("/"):
                add("blocker", "escape", path,
                    f"target '{t}' leaves the repo (absolute or contains '..')")
            elif not t.startswith(ALLOWED_ROOTS):
                add("blocker", "illegal-target", path,
                    f"target '{t}' is outside the allowed roots {', '.join(ALLOWED_ROOTS)}")
            if t == path.replace("\\", "/"):
                add("major", "no-op", path, "move whose target equals its source")
            if t in seen_targets and seen_targets[t] != path:
                add("blocker", "collision", path,
                    f"target '{t}' is already claimed by {seen_targets[t]} — one would be lost")
            seen_targets[t] = path

        if e.get("confidence") not in ("high", "low"):
            add("major", "schema", path, "confidence must be 'high' or 'low'")
        if not str(e.get("why", "") or "").strip():
            add("minor", "schema", path, "no reason given")

    paths = [str(e.get("path", "")) for e in entries if e.get("path")]
    if paths != sorted(paths):
        add("major", "nondeterminism", "-",
            "entries are not sorted by path — the manifest is not reproducible")

    blockers = [f for f in findings if f["severity"] == "blocker"]
    if blockers:
        reason = (f"{len(blockers)} blocking violation(s) across {len(entries)} entries "
                  f"— the manifest is not safe to apply")
        return findings, "block"
    reason = f"{len(entries)} entries validated; {len(findings)} non-blocking note(s)"
    return findings, "pass"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest")
    ns = ap.parse_args(argv)

    try:
        entries = load_entries(ns.manifest)
    except (OSError, ValueError) as e:
        print(json.dumps({
            "findings": [{"severity": "blocker", "category": "unreadable", "file": "-",
                          "line": None, "claim": str(e), "evidence": "deterministic check",
                          "fix_hint": "emit a JSON array of manifest entries"}],
            "verdict": "block",
            "reason": f"manifest unreadable: {e}",
        }))
        return 0   # the VERDICT carries the failure; the node itself succeeded

    findings, verdict = validate(entries)
    blockers = len([f for f in findings if f["severity"] == "blocker"])
    reason = (f"{blockers} blocking violation(s) across {len(entries)} entries"
              if verdict == "block"
              else f"{len(entries)} entries validated, {len(findings)} non-blocking note(s)")
    print(json.dumps({"findings": findings, "verdict": verdict, "reason": reason}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
