"""A tournament judge's pick -> Verdict: was an eligible winner crowned?

Usage from a flow (the tournament-judge starter):
    ["python", "-m", "lockstep.gates.tournament_pick",
     "--candidates", "cand-simple,cand-robust,cand-rethink",
     "{steps.judge.json}"]

The pick arrives as one argv — small by construction (ids and a rationale,
never the answers themselves). Two blocks, both fail-closed:

- `winner` is null: the judge found no answer acceptable. Crowning the
  least-bad anyway is exactly the quiet untruth a gate exists to stop, so the
  flow blocks with the judge's rationale as the evidence.
- `winner` names an id outside `--candidates`: a model-output defect. Without
  this check it surfaces one node later, when the publish step cannot find the
  answer — a config-error diagnosis for what is really a judge that invented
  a candidate.

The rationale is carried into the verdict reason either way, so `status` and
the cockpit show WHY without opening the judge's result.
"""

from __future__ import annotations

import argparse
import json
import sys

from ._common import emit, finding


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.tournament_pick")
    ap.add_argument("--candidates", required=True,
                    help="comma-separated candidate node ids the judge chose among")
    ap.add_argument("pick", help="the judge's TournamentPick JSON (interpolate {steps.<judge>.json})")
    ns = ap.parse_args(argv)
    candidates = [c.strip() for c in ns.candidates.split(",") if c.strip()]
    parse_error = ""
    try:
        pick = json.loads(ns.pick)
    except ValueError as e:
        pick = None
        parse_error = str(e)
    if not isinstance(pick, dict):
        return emit(
            [finding(
                "blocker", "gate-error", "tournament", "pick is not a JSON object",
                parse_error or type(pick).__name__,
                "interpolate {steps.<judge>.json} as this gate's positional argument",
            )],
            "",
        )
    winner = pick.get("winner")
    rationale = str(pick.get("rationale") or "")[:400]
    if winner is None:
        return emit(
            [finding(
                "blocker", "no-winner", "tournament",
                "the judge found no candidate acceptable",
                rationale or "(the judge gave no rationale)",
                "revise the task or the criteria and re-run; every candidate's full answer is in the run dir",
            )],
            "",
        )
    if winner not in candidates:
        return emit(
            [finding(
                "blocker", "gate-error", "tournament",
                f"the judge named unknown candidate {winner!r}",
                "known candidates: " + ", ".join(candidates),
                "the judge's winner must be one of the candidate node ids, exactly as prompted",
            )],
            "",
        )
    return emit([], f"winner: {winner} - {rationale}")


if __name__ == "__main__":
    sys.exit(main())
