#!/usr/bin/env python
"""question_card.py — the clarification questions, verbatim (proposal T2.1).

    python contrib/question_card.py <run_dir>            # find the blocked gate
    python contrib/question_card.py <run_dir> --gate ask-the-expert

Writes `<run_dir>/question-card.txt`, which the ACTIVITY pane displays while a
gate is blocked. **Display only.** There is no input path here and none is
coming: the answer still travels chat -> `lockstep steer` -> detached resume, so
nothing about the human channel changes.

WHAT CHANGES IS WHEN THE ORIGINAL WORDS ARE VISIBLE.

Rev 7 §A.3 gave clarifications no pane, on the grounds that a clarification is a
conversation and CHAT carries it. That holds for the conversation. It does not
hold for the source text: the obligation at a clarification is that the domain
expert receives the finding *verbatim* alongside the plain-language relay, and
today that obligation is discharged by orchestrator discipline and checked after
the fact by retrospect.py's token-overlap tripwire.

That is precisely the arrangement the evidence rule already rejected for
approvals — a narrated relay at a decision point, audited later. An answer is
effectively permanent (it renders into every later prompt and folds into the
hash), which makes a clarification at least as consequential as the approval
that gets a whole pane of its own.

A finding that cannot be read as one or two lines is a defect in the gate's
contract. This tool renders what is there and does not paraphrase around it —
if the card is unreadable, that is the finding.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_evidence import wrap_prose  # noqa: E402

WIDTH = 72

BRIEFING = (
    "These are the system's own words. The assistant will also put them to you "
    "in plain language in the chat - answer there, not here. If the two do not "
    "match, the words on this card are the ones that count."
)


def blocked_gates(state: dict) -> list[str]:
    return sorted(
        nid for nid, rec in (state.get("nodes") or {}).items()
        if rec.get("role") == "gate" and rec.get("status") == "blocked"
    )


def questions_of(result: dict) -> list[dict]:
    """Findings a clarify gate raised as questions.

    `category: "question"` is the §A.1 convention. A gate that blocks with
    findings carrying no category is still saying something the human needs, so
    those are shown too rather than filtered into silence — labelled, so the
    difference between "this was asked of you" and "this was reported" stays
    visible.
    """
    return [f for f in (result.get("findings") or []) if isinstance(f, dict)]


def render(gate_id: str, result: dict) -> str:
    out = ["=" * WIDTH, "  QUESTIONS ABOUT YOUR FIELD - nothing is spending", "=" * WIDTH, ""]

    reason = str(result.get("reason") or "").strip()
    if reason:
        out.append("  why it stopped:")
        out.extend(wrap_prose(reason, indent="    "))
        out.append("")

    findings = questions_of(result)
    if not findings:
        out.append("  The check stopped but recorded no question. That is a defect in the")
        out.append("  flow, not something for you to work around - say so in the chat.")
        out.append("")
    for i, f in enumerate(findings, 1):
        label = str(f.get("category") or "").strip()
        head = f"  {i}." if label == "question" else f"  {i}.  [{label or 'no category'}]"
        out.append(head)
        # Verbatim, wrapped only. Never summarised, never reordered.
        claim = str(f.get("claim") or f.get("message") or "").strip()
        out.extend(wrap_prose(claim, indent="      ", max_lines=0))
        where = str(f.get("file") or "").strip()
        if where:
            out.append(f"      (about: {where})")
        out.append("")

    out.append("-" * WIDTH)
    out.extend(wrap_prose(BRIEFING, indent="  ", max_lines=0))
    out.append("")
    out.append(f"  (from phases/{gate_id}/result.json)")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--gate", default=None, help="the gate to render (default: the blocked one)")
    ap.add_argument("--out", default=None)
    ns = ap.parse_args(argv)

    run_dir = Path(ns.run_dir)
    out_path = Path(ns.out) if ns.out else run_dir / "question-card.txt"

    try:
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read {run_dir}: {e}", file=sys.stderr)
        return 2

    blocked = blocked_gates(state)
    if ns.gate and ns.gate not in blocked:
        # `--gate` used to bypass the blocked check entirely, so a card could be
        # freshly written for a gate the human already answered — the exact stale
        # card the deletion path below exists to prevent. Still allowed (a flow
        # author inspecting a card is legitimate), but never silently.
        print(f"warning: '{ns.gate}' is not a blocked gate — this card will not "
              f"reflect a question anyone is waiting on", file=sys.stderr)
    gates = [ns.gate] if ns.gate else blocked
    if not gates:
        # Stale cards are worse than no card: a question the human already
        # answered, still on screen, reads as an unanswered one.
        #
        # But only ever delete OUR OWN card. `--out` can name any path, and the
        # first cut would unlink it — pointed at approval-evidence.txt, the
        # stale-card cleanup would have eaten the artifact a human decides from.
        # A tool does not delete a file it did not create.
        if out_path.name == "question-card.txt":
            out_path.unlink(missing_ok=True)
        else:
            print(f"no blocked gate; leaving {out_path} alone (not a question card)",
                  file=sys.stderr)
        print("no blocked gate - nothing to ask", file=sys.stderr)
        return 1
    if len(gates) > 1:
        print(f"more than one blocked gate ({', '.join(gates)}) - pass --gate", file=sys.stderr)
        return 1

    gate_id = gates[0]
    try:
        result = json.loads(
            (run_dir / "phases" / gate_id / "result.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as e:
        print(f"cannot read the gate result for {gate_id}: {e}", file=sys.stderr)
        return 2

    text = render(gate_id, result if isinstance(result, dict) else {})
    try:
        out_path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"warning: could not write {out_path}: {e}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
