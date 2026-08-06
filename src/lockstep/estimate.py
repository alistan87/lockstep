"""Cost preflight: what a run will cost, from what prior runs actually cost.

Honest units only. The driver knows spawns and wall time for every node on
every executor; tokens and dollars exist only where a harness reports them,
and that parsing lives in `contrib/cost_report.py` with the field maps. So
this module estimates in **agent tasks** (token-costing spawns) and wall time,
and says plainly what it cannot see.

The total is a FLOOR, never a forecast: nodes with no history contribute
nothing, map fan-out is resolved at run time, and heal rounds are unbounded.
Saying so is the point — an estimate a domain expert cannot trust is worse
than none, because consent is given against it.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .state import PhaseRecord, RunState
from .taskgraph import TaskGraph

# Kinds that spend model tokens. `fake` is the offline double and is treated as
# token-costing for the same reason verification does (DEVIATIONS 2026-07-25):
# the offline suite must exercise the paths a harness would take.
TOKEN_COSTING_KINDS = ("harness", "fake")


@dataclass
class NodeEstimate:
    node_id: str
    kind: str
    role: str
    token_costing: bool
    runs: int = 0  # prior runs that actually reached this node
    spawns: float = 0.0  # median spawns per run (attempts, so retries count)
    wall_s: float = 0.0


@dataclass
class Estimate:
    flow_name: str
    matched_runs: int
    matched_by: str  # "flow_hash" | "flow_name" | "none"
    nodes: list[NodeEstimate] = field(default_factory=list)
    # A6: per-RUN totals across the matched runs, so the band reflects real
    # whole-run variance (summing per-node minima would fabricate a run that
    # never happened). One entry per matched run, same order.
    run_agent_tasks: list[float] = field(default_factory=list)
    run_wall_s: list[float] = field(default_factory=list)

    @property
    def without_history(self) -> list[str]:
        return [n.node_id for n in self.nodes if n.runs == 0]

    @property
    def spawns(self) -> float:
        return sum(n.spawns for n in self.nodes)

    @property
    def agent_tasks(self) -> float:
        return sum(n.spawns for n in self.nodes if n.token_costing)

    @property
    def wall_s(self) -> float:
        return sum(n.wall_s for n in self.nodes)

    def tasks_band(self) -> tuple[float, float] | None:
        """(min, max) agent tasks over the matched runs; None without history."""
        if not self.run_agent_tasks:
            return None
        return min(self.run_agent_tasks), max(self.run_agent_tasks)

    def wall_band(self) -> tuple[float, float] | None:
        if not self.run_wall_s:
            return None
        return min(self.run_wall_s), max(self.run_wall_s)


def _load_runs(runs_dir: Path) -> list[RunState]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    out: list[RunState] = []
    for d in sorted(runs_dir.iterdir()):
        state_path = d / "state.json"
        if not state_path.exists():
            continue  # `lineages/` and stray dirs have none
        try:
            out.append(RunState.model_validate_json(state_path.read_text(encoding="utf-8")))
        except Exception:
            continue  # a half-written state is not worth failing a preflight over
    return out


def _node_spawns(rec: PhaseRecord) -> float:
    """Attempts, so corrective re-spawns and heal rounds are counted — they
    were paid for. Map nodes count their items, not the parent."""
    if rec.items:
        return float(sum(i.attempts for i in rec.items.values()))
    return float(rec.attempts)


def _wall_seconds(rec: PhaseRecord) -> float:
    if not rec.started_at or not rec.ended_at:
        return 0.0
    try:
        start = _dt.datetime.fromisoformat(rec.started_at)
        end = _dt.datetime.fromisoformat(rec.ended_at)
    except ValueError:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def estimate_flow(tg: TaskGraph, runs_dir: Path, flow_hash: str) -> Estimate:
    """Estimate `tg` from prior runs under `runs_dir`.

    Prefers runs of the identical definition (flow_hash). Falls back to the
    flow NAME so an edited flow still gets a number — but records which, so
    the renderer can say the definition has changed.
    """
    states = _load_runs(runs_dir)
    matched = [s for s in states if s.flow_hash == flow_hash]
    matched_by = "flow_hash"
    if not matched:
        matched = [s for s in states if s.flow_name == tg.name]
        matched_by = "flow_name" if matched else "none"

    est = Estimate(flow_name=tg.name, matched_runs=len(matched), matched_by=matched_by)
    for state in matched:
        # Per-run totals come from the RECORDED state, not the current tg:
        # for a name-matched (edited) definition, filtering by current node
        # ids would silently drop renamed nodes' spend from "what those runs
        # used". The record's own kind/role decide token-costing.
        est.run_agent_tasks.append(sum(
            _node_spawns(r) for r in state.nodes.values()
            if r.kind in TOKEN_COSTING_KINDS and r.role != "approval"
        ))
        est.run_wall_s.append(sum(_wall_seconds(r) for r in state.nodes.values()))
    for node in tg.nodes:
        token_costing = node.kind in TOKEN_COSTING_KINDS and node.role != "approval"
        ne = NodeEstimate(
            node_id=node.id, kind=node.kind, role=node.role, token_costing=token_costing
        )
        spawns: list[float] = []
        walls: list[float] = []
        for state in matched:
            rec = state.nodes.get(node.id)
            if rec is None or rec.status == "pending":
                continue  # never reached: no evidence either way
            spawns.append(_node_spawns(rec))
            walls.append(_wall_seconds(rec))
        if spawns:
            ne.runs = len(spawns)
            ne.spawns = statistics.median(spawns)
            ne.wall_s = statistics.median(walls)
        est.nodes.append(ne)
    return est


def _human_secs(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def render_estimate(est: Estimate) -> str:
    if est.matched_by == "none":
        return (
            f"estimate: {est.flow_name} — no history for this flow, so there is "
            f"nothing to estimate from.\n"
            f"  The first run of a flow has no floor; watch the cost pane while it runs."
        )
    source = (
        "this flow definition"
        if est.matched_by == "flow_hash"
        else "a DIFFERENT definition of this flow (matched by name — the flow file "
        "has changed since those runs)"
    )
    n = est.matched_runs
    lines = [
        f"estimate: {est.flow_name} — from {n} prior run{'' if n == 1 else 's'} of {source}",
        f"  {'node':<24} {'runs':>4} {'tasks':>6} {'wall':>8}",
    ]
    for ne in est.nodes:
        if ne.runs == 0:
            lines.append(f"  {ne.node_id:<24} {'-':>4} {'-':>6} {'-':>8}   no history")
            continue
        note = "" if ne.token_costing else "   (no tokens)"
        lines.append(
            f"  {ne.node_id:<24} {ne.runs:>4} {ne.spawns:>6.0f} "
            f"{_human_secs(ne.wall_s):>8}{note}"
        )
    lines.append(
        f"  {'TOTAL':<24} {'':>4} {est.agent_tasks:>6.0f} {_human_secs(est.wall_s):>8}"
    )
    lines.append(
        f"  agent tasks: {est.agent_tasks:.0f}   all spawns: {est.spawns:.0f}   "
        f"wall: {_human_secs(est.wall_s)}"
    )
    band, wall_band = est.tasks_band(), est.wall_band()
    if band and est.matched_runs > 1:
        lines.append(
            f"  across those runs: {band[0]:.0f}-{band[1]:.0f} agent tasks, "
            f"{_human_secs(wall_band[0])}-{_human_secs(wall_band[1])} wall — a range, "
            "never a forecast"
        )
    missing = est.without_history
    if missing:
        lines.append(
            f"  FLOOR ONLY — {len(missing)} node(s) have no history and contribute "
            f"nothing here: {', '.join(missing)}"
        )
    else:
        lines.append(
            "  A floor, not a forecast: map fan-out and heal rounds resolve at run time."
        )
    lines.append(
        "  Tokens and dollars: contrib/cost_report.py, for harnesses that report them."
    )
    return "\n".join(lines)
