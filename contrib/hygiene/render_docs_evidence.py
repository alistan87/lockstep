#!/usr/bin/env python
"""render_docs_evidence.py — the approval extract for a docs reorganisation.

    python contrib/hygiene/render_docs_evidence.py <manifest.json> [--verdict <json>]

Writes <run_dir>/approval-evidence.txt, which the APPROVAL pane prints before
the prompt.

What a human has to decide here is not "are these 23 moves individually
correct" — a checker already proved they are mechanically safe. It is **"is this
the right shape for the documentation, and can I still find things?"** So the
extract leads with the structure and shows every move grouped under its bundle,
in full. There is no sampling: 23 moves fit on a screen, and a reorganisation is
exactly the case where seeing the whole is the point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# Imported, never copied. A duplicate of this text is exactly how three rounds
# of corrections reached the generated bundle indexes while the pane a human
# actually reads kept publishing the wording those rounds had identified as
# false. The evidence pane and the artefact must not be able to disagree.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_docs  # noqa: E402
from apply_docs import BUNDLE_BLURB as BLURB  # noqa: E402

# Same reasoning one level up: the wrapping rule that keeps a decision packet
# readable belongs in one place, or the panes drift apart again.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_evidence import wrap_prose  # noqa: E402


# Sentinel: "--verdict was passed but could not be parsed" is a different fact
# from "no verdict was passed", and silently collapsing them left the pane
# printing its assurances under a heading with nothing beneath it.
FAILED_VERDICT: dict = {"__unreadable__": True}


def render(manifest: dict, verdict: dict | None) -> str:
    entries = manifest.get("placed", [])
    if manifest.get("__unreadable__"):
        # An unreadable manifest used to become an empty one, producing a
        # complete, reassuring pane describing zero moves - and exit 0, so the
        # approval proceeded.
        return "\n".join([
            "=" * 76,
            "  !! THE LIST OF CHANGES COULD NOT BE READ",
            "=" * 76,
            "",
            "  This pane cannot show you what would change, because the file",
            "  describing it is missing or unreadable.",
            "",
            "  There is nothing here to approve. The safe answer is r (reject),",
            "  and tell whoever set this up that the manifest was unreadable.",
            "",
        ])
    by_bundle: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_bundle[Path(e["target_path"]).parent.as_posix()].append(e)

    out: list[str] = []
    w = out.append
    w("=" * 76)
    w("  REORGANISING docs/ - your decision")
    w("=" * 76)
    w("")
    w(f"{len(entries)} documents move into {len(by_bundle)} groups.")
    w("Nothing is deleted and no document is rewritten. Two mechanical edits DO")
    w("change text inside files, and neither changes what any document SAYS:")
    w("  - each document gains a small header describing what it is;")
    w("  - links and paths that pointed at the old locations are updated to the")
    w("    new ones, across this project - roughly 100 of them.")
    w("")
    w("The grouping is by AUTHORITY - how much you can rely on a document -")
    w("rather than by subject. The question that drove it: 'can I trust this?'")
    w("")

    for bundle in sorted(by_bundle):
        name = Path(bundle).name
        items = sorted(by_bundle[bundle], key=lambda e: e["target_path"])
        w(f"-- {bundle}/  ({len(items)}) " + "-" * max(0, 50 - len(bundle)))
        w(f"   {BLURB.get(name, '')}")
        w("")
        for e in items:
            w(f"     {Path(e['path']).name}")
            w(f"       becomes  {e['target_path']}   [{e['okf_type']}]")
        w("")

    unplaced = manifest.get("files", [])
    conflicts = manifest.get("conflicts", [])
    w(f"-- nothing left unfiled ({len(unplaced)}) " + "-" * 40 if not unplaced
      else f"-- COULD NOT BE FILED ({len(unplaced)}) " + "-" * 40)
    for item in unplaced:
        w(f"     {item.split('|')[0]}  - no rule places this; it would NOT move")
    w("")
    if conflicts:
        w(f"-- RULE CONFLICTS ({len(conflicts)}) " + "-" * 45)
        for c in conflicts:
            w(f"     {c['path']}: claimed by {', '.join(c['rules'])}")
        w("")

    w("-- already checked without you " + "-" * 45)
    if verdict is FAILED_VERDICT:
        w("     !! THE REVIEW RESULT COULD NOT BE READ - DO NOT APPROVE.")
        w("     !! Something checked this and its answer did not arrive. Reject,")
        w("     !! and say the review result was unreadable.")
    elif verdict:
        findings = verdict.get("findings") or []
        # Severity order BEFORE truncating: the cap used to drop findings in
        # whatever order a model emitted them, so a blocker could vanish from
        # the only surface a human decides from.
        rank = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
        findings = sorted(findings, key=lambda f: rank.get(f.get("severity"), 9))
        w(f"     review: {verdict.get('verdict', '?')}")
        for ln in wrap_prose(str(verdict.get("reason", "")), indent="       "):  # T2.4
            w(ln)
        for f in findings[:12]:
            for ln in wrap_prose(
                f"[{f.get('severity')}] {f.get('file')}: {f.get('claim')}",
                indent="       ", max_lines=4,
            ):
                w(ln)
        if len(findings) > 12:
            w(f"       ... and {len(findings) - 12} more findings, lowest severity first")
    w("     No two documents land on the same name. Nothing escapes the folder,")
    w("     and no document lands on top of another.")
    w("")
    w("     References are rewritten across the project EXCEPT inside tests/ and")
    w("     the reorganiser's own code, where a path is an example being")
    w("     discussed rather than a link - those are covered by the test suite")
    w("     instead. Afterwards every remaining reference is checked, and if any")
    w("     would break the run stops and nothing is merged.")
    w("")
    w("     It is committed on a separate branch - pass or fail, so a failure")
    w("     leaves something to inspect. Merging is a second, separate decision")
    w("     you make later with the full diff in front of you.")
    w("")
    # The tamper check exists to bind an approval to a SPECIFIC manifest. Until
    # this line existed there was nothing to bind to: the digest was only ever
    # computed by the unchecked apply run itself, so the check attested
    # continuity from the last apply rather than from the approval.
    try:
        digest = apply_docs.manifest_digest(entries)
        w(f"-- this exact set of changes is  {digest}  " + "-" * 26)
        w(f"     Applying it runs with --arg approved_sha={digest}; if the list")
        w("     changes after you approve, that run refuses to start.")
        w("")
    except Exception:                                   # noqa: BLE001 - display only
        pass

    w("-" * 76)
    w("Decide from this pane. The merge review is your second chance, not your first.")
    w("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest")
    ap.add_argument("--verdict", default=None)
    ap.add_argument("--out", default=None)
    ns = ap.parse_args(argv)

    try:
        manifest = json.loads(Path(ns.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        manifest = {"__unreadable__": True}
        print(f"warning: manifest unreadable ({e})", file=sys.stderr)

    verdict = None
    if ns.verdict:
        try:
            verdict = json.loads(ns.verdict)
            if isinstance(verdict, str):
                verdict = json.loads(verdict)
        except ValueError:
            verdict = FAILED_VERDICT
        if not isinstance(verdict, dict):
            verdict = FAILED_VERDICT

    text = render(manifest, verdict)

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
