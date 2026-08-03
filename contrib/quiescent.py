#!/usr/bin/env python
"""quiescent.py — is this run safe to hand to the domain expert?

    python contrib/quiescent.py <run_dir>
      exit 0  quiescent-except-approval — prints the approval node id
      exit 1  NOT quiescent — prints every blocker and why
      exit 2  the run dir cannot be read

The cockpit hands an approval to the DE by spawning a pane with `lockstep
resume <run_dir>` pre-typed. Whatever the engine finds runnable at that moment
runs IN THE DE'S PROCESS: if a work node is still pending, the DE's terminal
becomes the host of a multi-minute agent spawn they did not ask for, cannot
interpret, and will close. The rule (proposal rev 6/7, B2 + R-B3) is therefore
that the ONLY runnable node at handoff is the approval itself.

This is a predicate over state.json and the mailbox, so it is CODE with an exit
code rather than a procedure in a document that an agent is trusted to follow.
Callers act on the exit code and never re-derive the answer themselves.

What "runnable after resume" means is taken from the engine's own resume pass
(roles.py `_resume_reset`), not invented here:
  - running / failed / blocked  -> pending   (any node)
  - skipped                     -> pending   (its `when` re-evaluates, so it CAN run)
  - done approval               -> pending   (approvals are never skipped)
  - done WITH unconsumed mail   -> pending   (r6 C2: a steer re-runs its target)
  - done map node with a pending/failed item -> that item re-runs

Known limit, stated rather than hidden: a done node can ALSO be invalidated by
hash revalidation (an upstream re-run, an edited flow, an external edit to the
workspace). Computing that requires the engine, the config, and the workspace
fingerprint. So a clean exit 0 means "nothing in the recorded state will run",
not "the engine is incapable of finding work". The practical trigger for
revalidation is a steer or a re-run, and both of those this check does see.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Statuses that the engine's resume pass turns back into `pending`.
#
# This list MIRRORS roles.py `_resume_reset` and must be kept in step with it.
# `skipped` was missing for the first several revisions of this file: the engine
# resets a skipped node to pending so its `when` re-evaluates against
# (possibly re-run) upstreams, which means a skipped node CAN run after a
# resume. Omitting it made the check fail open — the one direction it must
# never fail — and a flow with a `when`-gated node would have been reported
# safe to hand over.
#
# `when` cannot be evaluated here without the engine, the config, and the
# upstream results, so a skipped node is treated as awaiting. Usually it will
# just skip again; "usually" is not the standard for a predicate that decides
# whether a live queue lands in someone's terminal.
REACTIVATED = ("running", "failed", "blocked", "skipped")

# Two different questions, and conflating them is a bug in both directions:
#
#   REACTIVATED — "could this node RUN if we resume?"   (includes skipped)
#   OUTSTANDING — "is there work left in this run?"     (excludes skipped)
#
# A skipped node can run, so it must block a handoff. But a run whose only
# non-done node is skipped is FINISHED — resuming it would just re-ask an
# approval the human already answered. Treating skipped as outstanding would
# re-open every completed run that ever had a `when` on it.
OUTSTANDING = ("running", "failed", "blocked", "pending")


def read_state(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8"))


def unconsumed_mail(run_dir: Path, node_id: str) -> int:
    """Count steer messages not yet folded into a spawn. Trailing partial lines
    are tolerated the same way the driver tolerates them."""
    path = Path(run_dir) / "mailbox" / f"{node_id}.jsonl"
    if not path.is_file():
        return 0
    n = 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:
                continue
            raise
        if not msg.get("consumed"):
            n += 1
    return n


def descendants_of(run_dir: Path, node_id: str) -> tuple[set[str], bool]:
    """Everything reachable downstream of a node, from the run's own flow copy.

    Needed because the segmentation rule is not "nothing may follow an
    approval" — it is "nothing NON-TRIVIAL may follow one". A deliverable copy
    or a summary print running in the human's process for a second is the
    sanctioned shape (and the shape flows/starter/evidence-approval.tg.json
    uses). Without this, every well-formed flow would look non-quiescent
    forever and the handoff could never happen.

    Returns (descendants, flow_was_readable). With no flow copy we cannot tell
    a trivial tail from a second implement phase, and the safe answer is to
    treat every awaiting node as a blocker.
    """
    for name in ("flow.tg.json", "flow.json"):
        try:
            flow = json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        children: dict[str, list[str]] = {}
        for n in flow.get("nodes") or []:
            for dep in n.get("depends_on") or []:
                children.setdefault(dep, []).append(n["id"])
        seen: set[str] = set()
        stack = list(children.get(node_id, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(children.get(cur, []))
        return seen, True
    return set(), False


def check(run_dir: Path) -> tuple[list[str], list[str]]:
    """Returns (approvals_awaiting, blockers). Quiescent iff exactly one
    approval awaits and there are no blockers."""
    state = read_state(run_dir)
    nodes = state.get("nodes", {})
    approvals: list[str] = []
    blockers: list[str] = []

    # A run with nothing left to do is not "ready for a decision" — it is over.
    # This matters because an approval record stays `done` after the human
    # answers, and a done approval re-runs on resume (SPEC §9.3, approvals are
    # never resume-skipped). Without this guard a FINISHED run reports
    # quiescent, the cockpit spawns an APPROVAL pane, and the human is asked to
    # decide something they already decided and that was already delivered.
    # "Needs you" has to mean something actually needs them.
    resumable = any(rec.get("status") in OUTSTANDING for rec in nodes.values())
    if not resumable:
        return [], ["__finished__"]

    # Identify the awaiting approval first: what counts as a blocker depends on
    # whether a node sits downstream of it.
    awaiting_approvals = [
        nid for nid, rec in nodes.items()
        if rec.get("role") == "approval"
        and (rec.get("status") in REACTIVATED or rec.get("status") in ("pending", "done"))
    ]
    tail: set[str] = set()
    flow_readable = False
    if len(awaiting_approvals) == 1:
        tail, flow_readable = descendants_of(run_dir, awaiting_approvals[0])

    for node_id, rec in nodes.items():
        role = rec.get("role", "?")
        status = rec.get("status", "?")
        kind = rec.get("kind", "")
        is_approval = role == "approval"
        awaiting = status in REACTIVATED or status == "pending"

        if is_approval:
            if awaiting or status == "done":
                # A done approval re-runs on resume (SPEC §9.3) — it counts as
                # awaiting, which is exactly why a flow may not put work after it.
                approvals.append(node_id)
            continue

        if awaiting:
            if node_id in tail:
                if kind == "shell":
                    # The sanctioned trivial tail: it runs in the human's
                    # process for a second after they answer. This is the shape
                    # the segmentation rule prescribes, not a violation of it.
                    continue
                blockers.append(
                    f"{node_id}: {kind or role} node runs AFTER the approval — the "
                    f"segmentation rule allows only a seconds-long shell node there; "
                    f"split the flow into two segments"
                )
                continue
            if not flow_readable:
                blockers.append(
                    f"{node_id}: {status} — would run in the DE's process "
                    f"(no flow copy in the run dir, so a trivial tail cannot be told "
                    f"apart from real work)"
                )
                continue
            blockers.append(f"{node_id}: {status} — would run in the DE's process")
            continue

        if status == "done":
            mail = unconsumed_mail(run_dir, node_id)
            if mail:
                blockers.append(
                    f"{node_id}: done with {mail} unconsumed steer message(s) — "
                    f"re-runs on resume (r6 C2); resume detached first"
                )
            for item_id, irec in (rec.get("items") or {}).items():
                if irec.get("status") in ("pending", *REACTIVATED):
                    blockers.append(
                        f"{node_id}[{item_id}]: {irec.get('status')} — map item re-enters on resume"
                    )

    # Mail addressed to a node that is itself still pending is already covered
    # by that node being a blocker; mail to a DONE node is the subtle case and
    # is caught above. Mail to the approval's own id is not a blocker: it folds
    # into the prompt the DE is about to answer.
    return sorted(approvals), sorted(blockers)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    ns = ap.parse_args(argv)
    run_dir = Path(ns.run_dir)

    try:
        approvals, blockers = check(run_dir)
    except (OSError, ValueError) as e:
        if not ns.quiet:
            print(f"unreadable: {run_dir}: {e}", file=sys.stderr)
        return 2

    def say(msg: str, err: bool = False) -> None:
        if not ns.quiet:
            print(msg, file=sys.stderr if err else sys.stdout)

    # First stderr line of any refusal is a stable machine tag. Callers act on
    # the tag, not on prose: the cockpit used to print "resume detached first"
    # for EVERY exit 1, including a finished run where resuming cannot produce
    # a handoff — advice that contradicted the diagnosis printed directly above
    # it.
    if blockers == ["__finished__"]:
        say("reason: finished", err=True)
        say("NOT quiescent — this run is FINISHED (every node is done or skipped). "
            "There is no decision left to hand over; do not spawn an approval pane.",
            err=True)
        return 1
    if blockers:
        say("reason: blockers", err=True)
        say("NOT quiescent — resume detached first, then re-check:", err=True)
        for b in blockers:
            say(f"  - {b}", err=True)
        if not approvals:
            # Worth saying explicitly: with no approval anywhere in the graph,
            # burning the queue down will never produce a handoff. An
            # orchestrator that keeps resuming and re-checking would loop
            # forever waiting for a decision point that does not exist.
            say("  note: this flow has NO approval awaiting — resuming will finish "
                "the run, but it will never yield a handoff.", err=True)
        return 1
    if not approvals:
        say("reason: no-approval", err=True)
        say("NOT quiescent — no approval is awaiting a decision.", err=True)
        return 1
    if len(approvals) > 1:
        say("reason: multiple-approvals", err=True)
        say(f"NOT quiescent — {len(approvals)} approvals would run in one resume: "
            f"{', '.join(approvals)}. Segment the flow (rev 7 §A step 4).", err=True)
        return 1
    say(approvals[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
