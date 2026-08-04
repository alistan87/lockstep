#!/usr/bin/env python
"""plan_card.py — the consent beat, backed by an artifact (proposal T1.9).

    python contrib/plan_card.py flows/starter/evidence-approval.tg.json
    python contrib/plan_card.py <flow> --runs-dir runs --out runs/plan-card.txt

Renders, before anything spends: the shape of the work, the ceiling that was
agreed, and what prior runs of this flow actually cost. Spawns nothing and
writes nothing but the card.

WHY THIS EXISTS. The cockpit protocol has the orchestrator open with a budget
sentence — "up to 25 agent tasks; last week's similar run was about $N; shall I
start?" That last clause is the orchestrator quoting its own memory. It is the
one number in the entire system with no artifact behind it, and the standing
bargain the domain expert was given says every number they are quoted is one
they can verify in the mechanical pane.

`lockstep run --estimate` has computed exactly that figure from prior runs since
r7, and `--dry-run` has produced the layered plan for longer than that. Neither
ever reached the person being asked to consent. This is the join.

Two deliberate refusals:

- **No dollars.** The estimator counts agent tasks and wall time because that
  is what the run dirs record; both target machines bill in quota and one
  harness never reports usage at all. A dollar figure here would be exactly the
  kind of precise-looking wrong number the spend line already refuses to print.
- **No prediction of THIS run.** Prior runs are reported as the range they were,
  not as a forecast. A flow whose inputs changed can cost anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lockstep.estimate import estimate_flow  # noqa: E402
from lockstep.taskgraph import TaskGraph  # noqa: E402

WIDTH = 72


def _human_secs(s: float) -> str:
    s = int(s)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def shape(tg: TaskGraph) -> list[str]:
    """The graph, counted in the domain expert's vocabulary.

    Note what is NOT counted as a step: gates and approvals. A gate is "a check
    on the work" and an approval is "your decision" — calling both of them
    steps would make the headline number meaningless as a measure of how much
    work there is.
    """
    work = [n for n in tg.nodes if n.role in ("work", "map")]
    gates = [n for n in tg.nodes if n.role == "gate"]
    approvals = [n for n in tg.nodes if n.role == "approval"]
    heal_rounds = max([n.heal.max_rounds for n in gates], default=0)

    out = [f"{len(work)} step{'s' if len(work) != 1 else ''} of work"]
    if gates:
        out.append(f"{len(gates)} automatic check{'s' if len(gates) != 1 else ''}")
    if approvals:
        out.append(
            f"{len(approvals)} decision from you"
            if len(approvals) == 1
            else f"{len(approvals)} decisions from you"
        )
    else:
        out.append("no decision point - this one runs to the end on its own")
    if heal_rounds:
        out.append(f"up to {heal_rounds} rework round{'s' if heal_rounds != 1 else ''}")
    return out


def history(tg: TaskGraph, runs_dir: Path, flow_hash: str) -> list[str]:
    """What prior runs of this flow actually cost — a range, never a forecast."""
    try:
        est = estimate_flow(tg, runs_dir, flow_hash)
    except (OSError, ValueError) as e:
        return [f"prior runs: unavailable ({e})"]

    if est.matched_runs == 0:
        return [
            "prior runs of this flow: none on this machine",
            "  the ceiling above is the only number available - there is no history to check it against",
        ]
    lines = [
        f"prior runs of this flow: {est.matched_runs} "
        f"(matched by {est.matched_by.replace('_', ' ')})",
        f"  they used about {est.agent_tasks:.0f} agent tasks and {_human_secs(est.wall_s)} of node time",
    ]
    missing = est.without_history
    if missing:
        # An estimate that silently omits the nodes it knows nothing about reads
        # as a complete floor. It is not one.
        lines.append(
            f"  {len(missing)} step(s) have never run before, so the real figure is HIGHER: "
            + ", ".join(missing[:4])
            + ("..." if len(missing) > 4 else "")
        )
    return lines


def render(tg: TaskGraph, runs_dir: Path, flow_hash: str) -> str:
    out: list[str] = ["=" * WIDTH, "  BEFORE ANYTHING SPENDS - what you are agreeing to", "=" * WIDTH, ""]
    out.append("  " + "  -  ".join(shape(tg)))
    out.append("")

    cap = getattr(tg.budget, "max_agent_spawns", None)
    if cap:
        out.append(f"  ceiling: {cap} agent tasks. The run stops itself at that number.")
    else:
        out.append("  ceiling: none declared by this flow - it will run until it finishes.")
    out.append("")
    for line in history(tg, runs_dir, flow_hash):
        out.append("  " + line)
    out.append("")
    out.append("-" * WIDTH)
    out.append("  Nothing has started. Say yes and it begins; say no and nothing is spent.")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flow")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--out", default=None,
                    help="also write the card here (default: <runs-dir>/plan-card.txt)")
    ns = ap.parse_args(argv)

    try:
        raw = Path(ns.flow).read_text(encoding="utf-8")
        tg = TaskGraph.model_validate(json.loads(raw))
    except (OSError, ValueError) as e:
        print(f"cannot read {ns.flow}: {e}", file=sys.stderr)
        return 2

    # The estimator matches on flow_hash first and flow_name second, and reports
    # which it used — so an approximate match is visible as one rather than
    # passed off as history of this exact flow.
    from lockstep.cli import _load  # noqa: PLC0415  (CLI-internal, deliberately reused)

    try:
        _, flow_hash = _load(Path(ns.flow))
    except Exception:  # noqa: BLE001 - a card must never be the reason nothing starts
        flow_hash = ""

    text = render(tg, Path(ns.runs_dir), flow_hash)
    out_path = Path(ns.out) if ns.out else Path(ns.runs_dir) / "plan-card.txt"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"warning: could not write {out_path}: {e}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
