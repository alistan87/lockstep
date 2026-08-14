"""`lockstep explain` — which hash inputs moved (the cache-miss explainer).

Reads the labelled part digests the engine records beside every `input_hash`
(`PhaseRecord.hash_parts`) and answers the question a re-billed node raises:
WHICH input changed? Three modes:

  explain <run_dir> <node>                    the node's recorded parts, plus
                                              why its last revalidation re-ran it
  explain <run_dir> <node> --against <other>  label-level diff between two runs
  explain <run_dir> --graph                   the whole-graph staleness dry run
                                              (parity 3.2): plan every node
                                              against the CURRENT tree and
                                              config, compare to the record

The two node modes read recorded state only — they never plan, never spawn,
never recompute a hash. `--graph` DOES plan (that is its whole point), into a
THROWAWAY directory so spill files and timing lines never land in the run it
is reading — a read-only command that mutates the artifact it inspects is
worse than no command. It still spawns nothing and spends nothing. Runs
recorded before part recording existed render as `unrecorded` (the UNCHAINED
precedent from trace chaining).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import EXIT_CONFIG, EXIT_OK
from .state import PhaseRecord, RunState, diff_labels, label_parts, load_state


def _load(run_dir: Path) -> RunState | None:
    try:
        return load_state(run_dir)
    except (OSError, ValueError):
        return None


def _print_parts(rec: PhaseRecord, out) -> None:
    out(f"node: {rec.node_id}   status: {rec.status}   heal_round: {rec.heal_round}")
    out(f"input_hash: {rec.input_hash or '(none — never planned)'}")
    if rec.hash_parts is None:
        out("parts: unrecorded (run predates part recording)")
    else:
        out("parts:")
        for label in sorted(rec.hash_parts):
            out(f"  {label:<28} {rec.hash_parts[label][:16]}")
    if rec.invalidated_by:
        out("this node last re-ran because:")
        for reason in rec.invalidated_by:
            out(f"  {reason}")
    if rec.items:
        recorded = sum(1 for i in rec.items.values() if i.hash_parts is not None)
        out(f"items: {len(rec.items)} ({recorded} with recorded parts)")


def _diff_recs(rec: PhaseRecord, other: PhaseRecord, out) -> None:
    if rec.input_hash == other.input_hash:
        out("input_hash: identical in both runs")
    else:
        out(f"input_hash: {str(rec.input_hash)[:16]}… vs {str(other.input_hash)[:16]}…")
    for line in diff_labels(other.hash_parts, rec.hash_parts):
        out(f"  {line}")
    indices = sorted(set(rec.items) | set(other.items), key=int)
    for idx in indices:
        a, b = rec.items.get(idx), other.items.get(idx)
        if a is None or b is None:
            out(f"  item [{idx}]: only in {'this run' if a else 'the other run'}")
            continue
        if a.input_hash == b.input_hash:
            continue
        out(f"  item [{idx}]:")
        for line in diff_labels(b.hash_parts, a.hash_parts):
            out(f"    {line}")


def explain_node(run_dir: Path, node_id: str, against: Path | None = None, out=print) -> int:
    state = _load(run_dir)
    if state is None:
        out(f"lockstep: cannot read state in {run_dir}")
        return EXIT_CONFIG
    if node_id not in state.nodes:
        out(f"lockstep: unknown node {node_id!r} (nodes: {sorted(state.nodes)})")
        return EXIT_CONFIG
    rec = state.nodes[node_id]
    if against is None:
        _print_parts(rec, out)
        return EXIT_OK
    other_state = _load(against)
    if other_state is None:
        out(f"lockstep: cannot read state in {against}")
        return EXIT_CONFIG
    if node_id not in other_state.nodes:
        out(f"lockstep: node {node_id!r} does not exist in {against}")
        return EXIT_CONFIG
    out(f"node: {node_id}   this: {run_dir}   other: {against}")
    _diff_recs(rec, other_state.nodes[node_id], out)
    return EXIT_OK


# ------------------------------------------------------- --graph (parity 3.2)


def _topo_order(tg) -> list:
    done: list = []
    placed: set[str] = set()
    remaining = list(tg.nodes)
    while remaining:
        progressed = False
        for n in list(remaining):
            if all(d in placed for d in n.depends_on):
                done.append(n)
                placed.add(n.id)
                remaining.remove(n)
                progressed = True
        if not progressed:  # pragma: no cover — verify rejects cycles
            done.extend(remaining)
            break
    return done


def explain_graph(run_dir: Path, *, repo_root: Path, config, out=print) -> int:
    """Plan every node against the current tree and config; report which would
    re-run and WHY, with the moved part named. Zero spawns, zero tokens.

    Semantics, stated because they are the honest limit of a dry run: this
    predicts what a resume's revalidation would decide GIVEN THE RECORDED
    RESULTS. Nodes downstream of a stale upstream cannot have their hash
    computed (their prompts would embed results that do not exist yet), so
    they report transitively stale rather than pretending. A shell node whose
    argv is unchanged is assumed to reproduce its recorded output — shell
    always re-runs, so if it prints differently at run time its readers
    re-bill then; a false "unchanged" is only possible for that case, and the
    output says so. Anything that cannot be planned or proven fresh — a
    missing (gc'd) upstream result, an unfinished node, a plan error —
    reports as stale: fail toward re-running, never toward a false
    "unchanged" (proposal finding 20).
    """
    from .cli import _liveness_lines, _registry_for, _workspace_for
    from .roles import Engine
    from .state import compose_hash
    from .store import FileStore
    from .taskgraph import load_flow
    from .policy import AllowAllPolicy

    state = _load(run_dir)
    if state is None:
        out(f"lockstep: cannot read state in {run_dir}")
        return EXIT_CONFIG
    flow_copy = Path(run_dir) / "flow.tg.json"
    if not flow_copy.exists():
        out(f"lockstep: {run_dir} carries no flow.tg.json copy")
        return EXIT_CONFIG
    tg, _ = load_flow(flow_copy)
    engine = Engine(
        tg=tg,
        registry=_registry_for(config, repo_root),
        config=config,
        workspace=_workspace_for(repo_root),
        store=FileStore(Path(run_dir), state),
        policy=AllowAllPolicy(),
        repo_root=Path(repo_root),
        log=lambda *a, **k: None,
    )

    out(f"graph vs {Path(run_dir).name} (flow: {state.flow_name}) — planned "
        f"against the current tree; nothing was executed")
    for line in _liveness_lines(Path(run_dir), state):
        out(line)

    stale: dict[str, list[str]] = {}   # node -> reasons (directly stale)
    transitive: dict[str, str] = {}    # node -> the upstream that made it so
    fresh: list[str] = []
    rerun: list[str] = []              # shell / approval: re-run regardless

    with tempfile.TemporaryDirectory(prefix="lockstep-explain-") as td:
        tmp = Path(td)
        for node in _topo_order(tg):
            rec = state.nodes.get(node.id)
            bad_dep = next(
                (d for d in node.depends_on if d in stale or d in transitive), None)
            if bad_dep is not None:
                transitive[node.id] = bad_dep
                continue
            if node.role == "approval":
                rerun.append(f"{node.id} (approval — prompts every run)")
                continue
            if rec is None or rec.status not in ("done", "skipped"):
                status = rec.status if rec is not None else "unrecorded"
                stale[node.id] = [f"recorded status is {status!r} — it will run"]
                continue
            phase_dir = tmp / "phases" / node.id
            phase_dir.mkdir(parents=True, exist_ok=True)
            executor = engine.registry.get(node.kind)
            if rec.status == "skipped":
                # A `when` skip re-evaluates on every run; unchanged inputs
                # mean it stays skipped, which is as fresh as skipped gets.
                from .interpolate import InterpolationError, eval_when
                if node.when is None:
                    fresh.append(f"{node.id} (skipped)")
                    continue
                try:
                    if eval_when(node.when, engine._resolve_ctx(node)):
                        stale[node.id] = ["`when` now selects this node"]
                    else:
                        fresh.append(f"{node.id} (skipped)")
                except InterpolationError as e:
                    stale[node.id] = [f"cannot evaluate `when`: {e}"]
                continue
            try:
                if node.role == "map":
                    array = engine._resolve_over(node)
                    computed = engine._map_node_hash(node, array)
                    if computed == rec.input_hash:
                        fresh.append(f"{node.id} (map — {len(rec.items)} recorded "
                                     f"item(s) then cache individually)")
                    else:
                        new_parts = label_parts(engine._map_parts(node, array))
                        stale[node.id] = diff_labels(rec.hash_parts, new_parts)
                    continue
                ctx = engine._render_ctx(node, phase_dir)
                work = executor.plan(node, ctx)
                computed = compose_hash(
                    node.role, node.kind, node.contract, work.fingerprint_parts)
                new_parts = label_parts(
                    work.fingerprint_parts, work.meta.get("hash_detail"))
                if not getattr(executor, "cacheable", False):
                    argv_moved = computed != rec.input_hash
                    rerun.append(
                        f"{node.id} (shell — always re-runs"
                        + ("; argv CHANGED" if argv_moved else "") + ")")
                    if argv_moved:
                        # A changed argv means the recorded output is not what
                        # the re-run will print: readers cannot be proven fresh.
                        stale[node.id] = diff_labels(rec.hash_parts, new_parts)
                    continue
                if computed == rec.input_hash:
                    fresh.append(node.id)
                else:
                    stale[node.id] = diff_labels(rec.hash_parts, new_parts)
            except Exception as e:  # missing result, spill error, interpolation —
                # a node that cannot be planned cannot be proven fresh.
                stale[node.id] = [f"cannot plan: {type(e).__name__}: {e}"]

    out("")
    out(f"fresh: {len(fresh)}   stale: {len(stale) + len(transitive)} "
        f"({len(stale)} directly, {len(transitive)} transitively)   "
        f"re-runs regardless: {len(rerun)}")
    for nid, reasons in stale.items():
        out(f"stale {nid}")
        for r in reasons:
            out(f"  {r}")
    for nid, cause in transitive.items():
        out(f"transitively stale {nid} — upstream {cause!r} is stale, so its "
            f"inputs cannot be computed yet")
    for entry in rerun:
        out(f"re-runs {entry}")
    for entry in fresh:
        out(f"fresh {entry}")
    if any("shell — always re-runs)" in e for e in rerun):
        out("note: an unchanged-argv shell node is assumed to reproduce its recorded "
            "output; if it prints differently at run time, its readers re-bill then")
    return EXIT_OK
