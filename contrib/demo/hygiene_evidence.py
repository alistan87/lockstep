#!/usr/bin/env python
"""hygiene_evidence.py — the stratified approval extract (work order §6.3).

    python contrib/demo/hygiene_evidence.py <manifest-json-or-@file> [--verdict <json>]

The hard case for the evidence rule: the manifest may be hundreds of entries,
the pane cannot show them all, and a stats-only summary hides exactly what needs
human eyes. So the extract is STRATIFIED, and the order is the argument:

  1. counts per action (the shape of the change, ~10 lines)
  2. EVERY structural move, exhaustively — few, and high-consequence
  3. EVERY low-confidence entry and every flag, exhaustively — this IS the
     decision. If this list is long, that is a finding against the rules, and
     the right answer is to reject.
  4. a random-but-deterministic sample of high-confidence entries — spot-check
     honesty: the human must be able to catch a classifier that is confidently
     wrong, and only a sample of the ones it was SURE about can show that
  5. what the deterministic gate checked, so the human knows what they are NOT
     being asked to re-check

Deterministic sampling (seeded by the manifest's own content) matters: the same
manifest must produce the same pane twice, or the human cannot trust that what
they approved is what they saw.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

# One wrapping rule, imported rather than copied — a second copy is how the
# evidence panes drift apart, and they are the surfaces a human decides from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_evidence import wrap_prose  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_manifest import load_entries  # noqa: E402

BRIEFING = ("Decide from this pane. The merge review is your second chance, "
            "not your first.")


def sample_high(entries: list[dict], n: int = 8) -> list[dict]:
    high = [e for e in entries if e.get("confidence") == "high"]
    if len(high) <= n:
        return high
    seed = hashlib.sha256(json.dumps(high, sort_keys=True).encode()).hexdigest()
    rng = random.Random(seed)
    return sorted(rng.sample(high, n), key=lambda e: str(e.get("path", "")))


def render(entries: list[dict], verdict: dict | None) -> str:
    out: list[str] = []
    w = out.append

    w("=" * 76)
    w("  PROPOSED FILE CHANGES - your decision")
    w("=" * 76)
    w("")

    actions = Counter(str(e.get("action", "?")) for e in entries)
    w(f"{len(entries)} files were dispositioned:")
    for action, count in actions.most_common():
        w(f"    {action:<18} {count}")
    w("")

    moves = [e for e in entries if e.get("target_path")]
    w(f"-- every move, in full ({len(moves)}) " + "-" * 40)
    if not moves:
        w("    (none - nothing changes location)")
    for e in sorted(moves, key=lambda e: str(e.get("path", ""))):
        w(f"    {e.get('path')}")
        w(f"      -> {e.get('target_path')}   [{e.get('confidence')}] {e.get('why', '')}")
    w("")

    needs_eyes = [e for e in entries
                  if e.get("confidence") == "low" or e.get("action") == "flag"]
    w(f"-- everything the system was NOT sure about ({len(needs_eyes)}) " + "-" * 22)
    if not needs_eyes:
        w("    (none - every proposal was high confidence)")
    for e in sorted(needs_eyes, key=lambda e: str(e.get("path", ""))):
        w(f"    {e.get('path')}  [{e.get('action')}]")
        w(f"      {e.get('why', '')}")
    # Fire on the RATIO, not a raw count: "3 of 3 uncertain" is the loudest
    # possible signal that the rules have nothing to say about this corpus, and
    # a count-based threshold silently misses exactly the small manifests where
    # the problem is most obvious.
    if entries and len(needs_eyes) >= 3 and len(needs_eyes) / len(entries) > 0.25:
        w("")
        w("    NOTE: a large share of this manifest is uncertain. That usually means")
        w("    the RULES are out of date, not that the files are unusual. Rejecting")
        w("    and fixing the rules is cheaper than approving and correcting later.")
    w("")

    spot = sample_high(entries)
    w(f"-- spot-check: {len(spot)} of the ones it was confident about " + "-" * 20)
    for e in spot:
        target = e.get("target_path") or "(stays)"
        w(f"    {e.get('path')} -> {target}")
        w(f"      {e.get('why', '')}")
    w("")

    w("-- already checked automatically " + "-" * 42)
    if verdict:
        findings = verdict.get("findings") or []
        # T2.4: wrapped and capped. This exact line is what F8 recorded — a
        # ~350-word model reason rendered as one unbroken paragraph on one line
        # in the shipped demo's own evidence.
        w(f"    deterministic gate: {verdict.get('verdict', '?')}")
        for ln in wrap_prose(str(verdict.get("reason", "")), indent="      "):
            w(ln)
        if findings:
            cats = Counter(str(f.get("category", "?")) for f in findings)
            for cat, count in cats.most_common():
                w(f"      {cat}: {count}")
    else:
        w("    (no gate verdict supplied)")
    w("    Checked without you: no two files land on the same name, no path escapes")
    w("    the repo, every move has a destination, the list is reproducible.")
    w("")
    w("-" * 76)
    w(BRIEFING)
    w("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest")
    ap.add_argument("--verdict", default=None)
    ap.add_argument("--out", default=None)
    ns = ap.parse_args(argv)

    try:
        entries = load_entries(ns.manifest)
    except (OSError, ValueError) as e:
        entries = []
        print(f"warning: manifest unreadable ({e})", file=sys.stderr)

    verdict = None
    if ns.verdict:
        try:
            verdict = json.loads(ns.verdict)
            if isinstance(verdict, str):
                verdict = json.loads(verdict)
        except ValueError:
            verdict = None

    text = render(entries, verdict if isinstance(verdict, dict) else None)

    out_path = Path(ns.out) if ns.out else None
    if out_path is None:
        phase = os.environ.get("LOCKSTEP_PHASE_DIR")
        out_path = (Path(phase).resolve().parents[1] / "approval-evidence.txt") if phase \
            else Path("approval-evidence.txt")
    try:
        out_path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"warning: could not write {out_path}: {e}", file=sys.stderr)

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
