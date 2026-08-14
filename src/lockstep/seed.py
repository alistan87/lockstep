"""Warm-start a NEW lineage from a prior run's results (E7).

The problem this exists for: editing a flow file changes `flow_hash`, which
starts a new lineage, which re-runs and re-BILLS every completed node — so a
one-word fix to the last node's prompt costs the whole graph again. `resume`
refusing the edited flow is correct (hash integrity is the cache's whole
basis); the cost is the honest consequence, and it made authors avoid editing
flows mid-project, which is the opposite of what the tool is for.

`run <flow> --seed <old_run_dir>` keeps the refusal and removes the cost. Each
node is planned normally, its `input_hash` composed exactly as the engine
composes it, and if the seed run recorded a SUCCESSFUL result under that same
hash, the result is served instead of spawned. A node whose prompt changed
hashes differently and runs for real; so does everything downstream of it,
because a re-run node's result feeds the next node's hash. Nothing is trusted
except the hash.

Distinct from `--replay`, which serves EVERY node and errors on a miss (it
reproduces one run exactly, for support and regression). A seed is a cache: a
miss is the normal case and falls through to the real executor.

Two deliberate limits:

- **Map items are never seeded.** A map's per-item hash includes `index:i`,
  which the engine appends AFTER the executor plans, so a plan-time decision
  cannot see it — and deciding at execute time would spend the spawn budget
  for a spawn that never happened. Per-item caching within a lineage already
  exists; this is the cross-lineage gap, and it stays open.
- **Only `done`/`skipped` recordings are served.** A failure is not a result;
  re-running it is the point of running again.
"""

from __future__ import annotations

from pathlib import Path

from .protocols import PlannedWork, RawResult, RenderCtx
from .replay import ReplayIndex, _item_index
from .state import compose_hash
from .taskgraph import Node

SeedIndex = ReplayIndex  # same shape, read the same way: (node, item) -> Recording


def forced_set(tg, names: list[str]) -> set[str]:
    """`--force-stale` (parity 3.3): the named nodes plus everything
    downstream of them — a re-run node's result feeds its readers' hashes, so
    a frontier that stopped at the named node would serve its readers results
    computed from inputs this run is about to replace."""
    ids = {n.id for n in tg.nodes}
    unknown = sorted(set(names) - ids)
    if unknown:
        raise ValueError(f"--force-stale names unknown node(s): {', '.join(unknown)}")
    children: dict[str, list[str]] = {}
    for n in tg.nodes:
        for d in n.depends_on:
            children.setdefault(d, []).append(n.id)
    out = set(names)
    frontier = list(names)
    while frontier:
        nid = frontier.pop()
        for c in children.get(nid, []):
            if c not in out:
                out.add(c)
                frontier.append(c)
    return out


class SeedExecutor:
    """Wraps a real executor: plans through it (so hashing is bit-identical),
    serves a hash-matched recording, and otherwise gets out of the way."""

    def __init__(self, inner, index: SeedIndex, *, log=print, on_hit=None,
                 forced: set[str] | None = None, on_forced=None):
        self.inner = inner
        self.index = index
        self.log = log
        self.on_hit = on_hit
        # Parity 3.3: node ids the seed must DECLINE regardless of hash match
        # (--force-stale <node> + descendants). Declining is a plan-time
        # decision like serving, and for the same reason: honesty about what
        # spends budget.
        self.forced = forced or set()
        self.on_forced = on_forced
        self._forced_noted: set[str] = set()
        self.kind = inner.kind
        self.cacheable = inner.cacheable
        # A MISS runs for real, so the fallthrough keeps every capability of
        # the executor it wraps — corrective re-spawn included.
        self.supports_corrective_respawn = inner.supports_corrective_respawn
        self.SpecModel = inner.SpecModel

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork:
        work = self.inner.plan(node, ctx)
        if not self.cacheable:
            # Shell nodes always re-run (SPEC §0.1.7) — cheap, and it kills the
            # silent-skip footgun. A seed is a cache, so it obeys the same rule
            # the in-lineage cache does; serving one here would make `--seed`
            # skip work that a plain resume would have re-run.
            return work
        if node.role == "map":
            # Every item plans through this node; a (node, None) hit here would
            # mark all of them free and serve one aggregate result to each.
            return work
        if node.id in self.forced:
            # Forced ≠ hash-missed, and the distinction is recorded (once) —
            # a reader of `status` or `explain` must be able to tell why this
            # node re-billed when its inputs may not have moved at all.
            if node.id not in self._forced_noted:
                self._forced_noted.add(node.id)
                self.log(f"seed: {node.id} forced stale (--force-stale) — runs for real")
                if self.on_forced is not None:
                    self.on_forced(node.id)
            return work
        recording = self.index.get(node.id, None)
        if (
            recording is None
            or recording.status not in ("done", "skipped")
            or recording.result_text is None
            or not recording.input_hash
        ):
            return work
        computed = compose_hash(node.role, node.kind, node.contract, work.fingerprint_parts)
        if computed != recording.input_hash:
            return work
        work.meta = {**work.meta, "_seed": {"node_id": node.id, "recording": recording}}
        # The decision is made at PLAN time precisely so this can be honest:
        # a served node spawns nothing, so it must not spend from the spawn
        # budget (§9.5 counts spawns whose work costs tokens — this one has
        # no work). `--estimate` and `status` then agree with reality.
        work.costs_tokens = False
        return work

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        seed = work.meta.get("_seed")
        if seed is None or _item_index(phase_dir) is not None:
            return self.inner.execute(work, phase_dir, timeout_s)
        recording = seed["recording"]
        name = "result.json" if recording.json_output else "result.txt"
        target = Path(phase_dir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(recording.result_text, encoding="utf-8")
        self.log(f"seed: {seed['node_id']} served from {self.index.source} (no spawn)")
        if self.on_hit is not None:
            self.on_hit(seed["node_id"], str(self.index.source))
        return RawResult(exit_code=0, result_text=recording.result_text, source="file", error=None)


def wrap_registry(registry, index: SeedIndex, *, log=print, on_hit=None,
                  forced: set[str] | None = None, on_forced=None):
    """Replace every registered executor with a seed wrapper, in place."""
    for kind in registry.kinds():
        inner = registry.get(kind)
        registry.register(SeedExecutor(inner, index, log=log, on_hit=on_hit,
                                       forced=forced, on_forced=on_forced))
    return registry
