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

import json
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
    if rec.adopted is not None:
        # S3: the one node whose freshness is NOT governed by the parts below.
        # Say it before the hash detail, or the detail reads as the decision.
        out(f"settled-by-adoption: {len(rec.adopted.paths)} path(s) adopted "
            f"{rec.adopted.ts} ({rec.adopted.source}) — this node will not re-run "
            f"even if the parts below have moved; `adopt --release` restores "
            f"hash-governed revalidation")
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
    output names it per-node ("conditionally fresh … — depends on always-rerun
    shell …", S4) with a trailing note for the general caveat. Anything that
    cannot be planned or proven fresh — a
    missing (gc'd) upstream result, an unfinished node, a plan error —
    reports as stale: fail toward re-running, never toward a false
    "unchanged" (proposal finding 20).
    """
    from .cli import _liveness_lines, _registry_for, _same_root, _workspace_for
    from .roles import Engine
    from .state import compose_hash, root_present
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
    # OPEN-WORK item 8: a harvested lane's run records a root that is gone.
    # The dry run still plans against THIS tree — that is what --graph is for —
    # but a reader must know the record came from another tree before reading
    # "every node moved" as edits. Said here, ahead of the per-node verdicts.
    # Empty (legacy) is unknown and says nothing, like `_same_root`; so does a
    # root the probe cannot answer for (`root_present` is None).
    recorded = state.repo_root
    present = root_present(recorded)
    if present is False:
        out(f"root gone: this run was recorded against {recorded}, which no longer "
            f"exists (a harvested worktree?); planned against {Path(repo_root)} — a moved "
            f"part below may be the difference between those trees, not an edit")
    elif present and not _same_root(recorded, Path(repo_root)):
        out(f"note: this run was recorded against another tree, {recorded}; planned "
            f"against {Path(repo_root)} — a moved part below may be the difference "
            f"between those trees, not an edit")

    stale: dict[str, list[str]] = {}   # node -> reasons (directly stale)
    transitive: dict[str, str] = {}    # node -> the upstream that made it so
    fresh: list[str] = []
    rerun: list[str] = []              # shell / approval: re-run regardless
    # S4 (upstream-response-ow07-feedback): "fresh" downstream of an
    # always-rerun shell is an ASSUMPTION wearing a certain word — the OW-07
    # pre-run explain said three fresh, the shell printed a different duration
    # string, two re-billed. Name the assumption per-node. Taint follows
    # CONSUMPTION ({steps.<shell>.…} in the spec), not mere ordering edges:
    # a sequencing-only dependent's hash cannot move with the shell's output.
    shellish: set[str] = set()         # always-rerun, argv unchanged: output assumed
    conditional: dict[str, str] = {}   # fresh node -> the root shell it leans on
    pinned: list[str] = []             # settled-by-adoption: never re-runs (S3)

    def _consumes(node, upstream_id: str) -> bool:
        text = json.dumps(node.spec, ensure_ascii=False)
        if node.when:
            text += node.when
        if getattr(node, "over", None):
            text += node.over
        return f"{{steps.{upstream_id}." in text

    def _taint_of(node) -> str | None:
        for d in node.depends_on:
            if d in shellish and _consumes(node, d):
                return d
            if d in conditional and _consumes(node, d):
                return conditional[d]  # the ROOT shell, not the intermediate
        return None

    with tempfile.TemporaryDirectory(prefix="lockstep-explain-") as td:
        tmp = Path(td)
        for node in _topo_order(tg):
            rec = state.nodes.get(node.id)
            if rec is not None and rec.adopted is not None:
                # S3: the pin wins over EVERYTHING this dry run could compute —
                # including a stale upstream — because that is what the engine
                # does (_settle short-circuits before re-planning). Checked
                # before the transitive sweep so dependents correctly plan
                # against the recorded result rather than reporting blocked.
                pinned.append(node.id)
                continue
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
                        root = _taint_of(node)
                        if root:
                            conditional[node.id] = root
                        else:
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
                    else:
                        shellish.add(node.id)  # output ASSUMED to reproduce (S4)
                    continue
                if computed == rec.input_hash:
                    root = _taint_of(node)
                    if root:
                        conditional[node.id] = root
                    else:
                        fresh.append(node.id)
                else:
                    stale[node.id] = diff_labels(rec.hash_parts, new_parts)
            except Exception as e:  # missing result, spill error, interpolation —
                # a node that cannot be planned cannot be proven fresh.
                stale[node.id] = [f"cannot plan: {type(e).__name__}: {e}"]

    out("")
    out(f"fresh: {len(fresh) + len(conditional)}"
        + (f" ({len(conditional)} conditionally)" if conditional else "")
        + f"   stale: {len(stale) + len(transitive)} "
        f"({len(stale)} directly, {len(transitive)} transitively)   "
        f"re-runs regardless: {len(rerun)}"
        + (f"   settled by adoption: {len(pinned)}" if pinned else ""))
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
    for nid, sid in conditional.items():
        out(f"conditionally fresh {nid} — depends on always-rerun shell {sid!r}; "
            f"its actual output at run time may invalidate this")
    for nid in pinned:
        out(f"settled by adoption {nid} — will NOT re-run regardless of hash "
            f"(`adopt --release` restores hash-governed revalidation)")
    if any("shell — always re-runs)" in e for e in rerun):
        out("note: an unchanged-argv shell node is assumed to reproduce its recorded "
            "output; if it prints differently at run time, its readers re-bill then")
    return EXIT_OK
