"""`lockstep adopt` — settle a human-remediated artifact into a run (S3).

The scenario (OW-07, observed live): a gate blocks, a human legitimately
edits the writer's artifact by hand, and every road is bad — `resume`
re-runs the producer if its hash missed and overwrites the edit, `--seed`
correctly refuses the dirty scope, `--allow-dirty-scope` waives the one
protection standing between the producer and the edit, a second flow severs
lineage. `adopt` records the fact the engine had no way to hold: this
artifact in the tree is settled, a human put it there, and its consumers
have not seen it yet.

Semantics are Sol's, not Gemini's (DESIGN-NOTE-adopt §1): the unit is an
ARTIFACT adopted into a completed writer whose consumers re-run unweakened —
never a failed node marked done because the tree looks right. Provenance is
`source: external-approved-remediation`; the record never pretends the
harness produced the edit.

The decisions this file implements (DESIGN-NOTE-adopt §4, adopted):

- D1: refuse when any node's templates interpolate the writer's RECORDED
  result text ({steps.<id>.output}/.json/{previous.output}) — adoption does
  not rewrite recorded results, so a re-run consumer would read the model's
  superseded output while the tree says otherwise. `--force` downgrades to a
  warning and journals the override. The scanner is `_node_templates` — the
  same enumerator §6 verification uses, so a site it misses is a site the
  verifier misses, and there is exactly one list to keep honest.
- D2: the pin is `PhaseRecord.adopted` (additive, optional); `input_hash` is
  deliberately never rewritten. The engine half lives in roles.py.
- D3: `fingerprint_detail` is refreshed for the adopted paths ONLY, so the
  M7 external-edit sweep neither re-pends the writer over its own adoption
  nor goes blind to unrelated edits made in the same window.
- D4: `--release` dissolves the pin, journaled, never deleting the original
  adoption event (the journal is append-only; the history is the point).
- D5 lives in cli.py's seed path: a seed source's adoptions are named, never
  transferred.

Every refusal is exit 7 (EXIT_CONFIG) — the code that already means "the
run was refused, and here is why".
"""

from __future__ import annotations

import os
from pathlib import Path

from . import EXIT_CONFIG, EXIT_OK
from .interpolate import extract_refs, render_scope
from .state import (
    AdoptionRecord,
    LockHeld,
    acquire_lock,
    append_event,
    load_state,
    release_lock,
    utcnow,
    write_state,
)
from .taskgraph import TaskGraph, _node_templates, load_flow
from .workspace import GitWorkspace, WorkspaceError, path_in_scope

SOURCE = "external-approved-remediation"


def _same_root(recorded: str, invoked: Path) -> bool:
    """cli._same_root's rule (empty = unknown, never a mismatch; normcase so a
    case-different Windows spelling is one tree, not two). Duplicated here
    because cli imports this module — five lines against an import cycle."""
    if not recorded:
        return True
    return os.path.normcase(str(Path(recorded))) == os.path.normcase(str(invoked))


def _dependents_cone(tg: TaskGraph, node_id: str) -> list[str]:
    """Transitive consumers of `node_id`, in declaration order."""
    dependents: dict[str, list[str]] = {n.id: [] for n in tg.nodes}
    for n in tg.nodes:
        for d in n.depends_on:
            if d in dependents:
                dependents[d].append(n.id)
    cone: set[str] = set()
    frontier = [node_id]
    while frontier:
        for dep in dependents[frontier.pop()]:
            if dep not in cone:
                cone.add(dep)
                frontier.append(dep)
    return [n.id for n in tg.nodes if n.id in cone]


def result_text_consumers(tg: TaskGraph, writer_id: str) -> list[str]:
    """D1: every "node (site)" whose template interpolates the writer's
    recorded result text. Scans ALL nodes, not just the cone — §6 requires a
    {steps.X...} ref to declare its dependency, but this check must not
    inherit that assumption to stay safe against a graph verify would reject.
    """
    hits: list[str] = []
    for n in tg.nodes:
        if n.id == writer_id:
            continue
        for where, template in _node_templates(n):
            for ref in extract_refs(template):
                parts = ref.split(".")
                if parts[0] == "steps" and len(parts) >= 2 and parts[1] == writer_id:
                    hits.append(f"{n.id} ({where}: {{{ref}}})")
                elif (
                    parts[0] == "previous"
                    and len(n.depends_on) == 1
                    and n.depends_on[0] == writer_id
                ):
                    hits.append(f"{n.id} ({where}: {{previous.output}})")
    return hits


def _writes_of(tg: TaskGraph, node_id: str, args: dict[str, str]) -> list[str]:
    """The declared scope, rendered exactly as the engine renders it
    (render_scope: {args.NAME} and nothing else)."""
    node = tg.node(node_id)
    return render_scope([str(w) for w in (node.spec.get("writes") or [])], args)


def _outside_run_dir(paths: list[str], run_dir: Path, repo_root: Path) -> list[str]:
    """Drop the run dir's own contents — same exclusion as the engine's
    preflight, for the same reason (an un-ignored runs/ tree)."""
    try:
        rel = run_dir.resolve().relative_to(repo_root.resolve()).as_posix() + "/"
    except ValueError:
        return paths
    return [p for p in paths if not p.startswith(rel)]


def adopt(
    run_dir: Path,
    node_id: str,
    *,
    repo_root: Path,
    reason_file: Path | None = None,
    paths: list[str] | None = None,
    force: bool = False,
    release: bool = False,
    out=print,
) -> int:
    run_dir = Path(run_dir)
    try:
        state = load_state(run_dir)
    except (OSError, ValueError) as e:
        out(f"lockstep: cannot read state in {run_dir}: {e}")
        return EXIT_CONFIG

    # Refused while a driver is live — the same lock a resume takes, held for
    # the whole mutation because adopt rewrites state.json itself.
    try:
        acquire_lock(run_dir)
    except LockHeld as e:
        out(f"lockstep: refusing to adopt while a driver may be live — lock {e.holder}")
        out("  wait for the run (or `lockstep wait`), or clear a dead lock via resume --force-unlock")
        return EXIT_CONFIG
    try:
        return _adopt_locked(
            run_dir, node_id, state,
            repo_root=repo_root, reason_file=reason_file, paths=paths,
            force=force, release=release, out=out,
        )
    finally:
        release_lock(run_dir)


def _adopt_locked(
    run_dir: Path, node_id: str, state, *,
    repo_root: Path, reason_file, paths, force: bool, release: bool, out,
) -> int:
    if node_id not in state.nodes:
        out(f"lockstep: unknown node {node_id!r} (nodes: {sorted(state.nodes)})")
        return EXIT_CONFIG
    rec = state.nodes[node_id]

    if release:
        # D4: dissolve the pin. Journaled, never deleted — and no repo-root
        # check, deliberately: releasing touches no tree paths, and a lane
        # whose worktree was harvested must still be releasable.
        if rec.adopted is None:
            out(f"lockstep: {node_id!r} carries no adoption to release")
            return EXIT_CONFIG
        n_paths = len(rec.adopted.paths)
        rec.adopted = None
        write_state(run_dir, state)
        append_event(run_dir, {"kind": "adoption", "op": "release", "node": node_id})
        out(f"released: {node_id} revalidates by hash on the next resume "
            f"({n_paths} path(s) no longer pinned)")
        return EXIT_OK

    if reason_file is None:
        out("lockstep: adopt requires --reason-file — the decision is recorded in the "
            "human's own words, never synthesized")
        return EXIT_CONFIG
    try:
        reason = Path(reason_file).read_text(encoding="utf-8").strip()
    except OSError as e:
        out(f"lockstep: cannot read --reason-file: {e}")
        return EXIT_CONFIG
    if not reason:
        out(f"lockstep: --reason-file {reason_file} is empty — an empty reason is no reason")
        return EXIT_CONFIG

    repo_root = Path(repo_root).resolve()
    if not _same_root(state.repo_root, repo_root):
        out(f"lockstep: this run was created against {state.repo_root} but adopt was "
            f"invoked with --repo-root {repo_root} — the adopted paths are relative to "
            f"the recorded tree; adopt from there")
        return EXIT_CONFIG

    flow_path = run_dir / "flow.tg.json"
    if not flow_path.exists():
        out(f"lockstep: {run_dir} carries no flow.tg.json copy — adopt needs the run's "
            f"own graph to check scopes and consumers")
        return EXIT_CONFIG
    tg, _ = load_flow(flow_path)
    try:
        node = tg.node(node_id)
    except KeyError:
        out(f"lockstep: node {node_id!r} is not in this run's flow copy")
        return EXIT_CONFIG

    if node.role == "map":
        out(f"lockstep: {node_id!r} is a map node — a map cannot declare spec.writes "
            f"(items share one tree), so there is no scope to adopt against")
        return EXIT_CONFIG
    if rec.status != "done":
        # G2's decline, enforced: adoption settles an artifact a completed
        # writer produced and a human then remediated. A failed node adopted
        # as done would let "the tree looks right" stand in for "the recorded
        # result is what produced this".
        out(f"lockstep: {node_id!r} is {rec.status!r}, not done — adopt settles a "
            f"completed writer's artifact; it never marks a failed node done "
            f"(that provenance is what the trace chain protects)")
        return EXIT_CONFIG
    scope = _writes_of(tg, node_id, state.args)
    if not scope:
        out(f"lockstep: {node_id!r} declares no spec.writes — adopt accepts paths "
            f"against the writer's declared scope, and this writer has none")
        return EXIT_CONFIG

    try:
        workspace = GitWorkspace(repo_root)
        dirty = set(_outside_run_dir(workspace.dirty_paths(), run_dir, repo_root))
    except WorkspaceError as e:
        out(f"lockstep: {e} — adoption reads the working tree, so a git tree is required")
        return EXIT_CONFIG

    if paths:
        adopt_paths = [Path(p).as_posix() for p in paths]
        for p in adopt_paths:
            if p not in dirty:
                out(f"lockstep: {p} is not modified in the working tree — nothing to adopt")
                return EXIT_CONFIG
            if not path_in_scope(p, scope):
                out(f"lockstep: {p} falls outside {node_id!r}'s declared write scope "
                    f"{scope} — adopt cannot attribute it to this writer")
                return EXIT_CONFIG
    else:
        adopt_paths = sorted(p for p in dirty if path_in_scope(p, scope))
        if not adopt_paths:
            out(f"lockstep: no modified paths fall inside {node_id!r}'s declared write "
                f"scope {scope} — nothing to adopt")
            return EXIT_CONFIG

    # A path a SECOND writer may legally overwrite is a guarantee the engine
    # cannot keep — the pin protects against the adopted writer only.
    for other in tg.nodes:
        if other.id == node_id or "writes" not in other.spec:
            continue
        other_scope = _writes_of(tg, other.id, state.args)
        hits = sorted(p for p in adopt_paths if path_in_scope(p, other_scope))
        if hits:
            out(f"lockstep: {', '.join(hits)} also fall(s) inside {other.id!r}'s declared "
                f"write scope — a second writer may legally overwrite an adopted path, "
                f"and adopt cannot promise otherwise; narrow the scopes first")
            return EXIT_CONFIG

    # D1: recorded-result-text consumers. Adoption rewrites the TREE, never a
    # recorded result — a consumer that interpolates {steps.<writer>.output}
    # would re-run against the model's superseded text.
    forced: list[str] = []
    text_consumers = result_text_consumers(tg, node_id)
    if text_consumers:
        if not force:
            out(f"lockstep: refusing — these nodes interpolate {node_id!r}'s RECORDED "
                f"result text, which adoption does not rewrite:")
            for hit in text_consumers:
                out(f"  {hit}")
            out("  a re-run would feed them the superseded model output while the tree "
                "holds the human's version; restructure them to read the file "
                "(spec.reads), or pass --force to proceed with this named risk")
            return EXIT_CONFIG
        forced = text_consumers
        out(f"warning: --force — {len(text_consumers)} node(s) interpolate the recorded "
            f"result text and will see the superseded output: {', '.join(text_consumers)}")

    # The evidence: per-path content hashes, computed by the SAME hasher the
    # M7 fingerprint uses, so the journal entry and the refreshed lineage
    # head cannot disagree about what the human's bytes were.
    _, current_detail = workspace.fingerprint_detail()
    path_evidence = {
        p: {
            "before": state.fingerprint_detail.get(p, "(not in lineage head)"),
            "after": current_detail.get(p, "(missing)"),
        }
        for p in adopt_paths
    }

    ts = utcnow()
    rec.adopted = AdoptionRecord(
        paths=adopt_paths, reason=reason, ts=ts, source=SOURCE,
        forced_result_text_consumers=forced,
    )

    # Consumers re-run unweakened, EXPLICITLY — reads-hashes cannot see a
    # consumer that opens the artifact without declaring it (reads.py: an
    # undeclared read stays invisible), so nothing is left to hashing.
    cone = _dependents_cone(tg, node_id)
    repended: list[str] = []
    for cid in cone:
        crec = state.nodes[cid]
        if crec.status in ("done", "failed", "blocked", "skipped"):
            crec.status = "pending"
            crec.error = None
            crec.invalidated_by = [
                f"upstream artifact adopted: {node_id} ({SOURCE})"
            ]
            if tg.node(cid).role == "map":
                # Same rule as heal invalidation (§9.4.6 sibling): a per-item
                # hash that never read the adopted file would match and skip.
                crec.items = {}
            repended.append(cid)

    # D3: refresh the lineage head for the adopted paths ONLY. The adoption
    # IS an external edit by construction; without this the next resume's M7
    # sweep warns and re-pends the writer — the exact overwrite this command
    # exists to prevent, arriving by the second road. Unrelated edits keep
    # their stale entries and still warn by name.
    if state.fingerprint_detail:
        for p in adopt_paths:
            if p in current_detail:
                state.fingerprint_detail[p] = current_detail[p]
            else:
                state.fingerprint_detail.pop(p, None)

    # The human's words survive gc like rejection.txt does — append, never
    # clobber, so a second adoption in the same run keeps the first's record.
    reason_path = run_dir / "adoption-reason.txt"
    header = f"--- adoption: {node_id} at {ts} ---\n"
    with reason_path.open("a", encoding="utf-8") as f:
        f.write(header + reason + "\n")

    write_state(run_dir, state)
    append_event(run_dir, {
        "kind": "adoption", "node": node_id, "paths": path_evidence,
        "reason": reason, "source": SOURCE,
        "forced_result_text_consumers": forced,
    })

    out(f"adopted: {len(adopt_paths)} path(s) into {node_id} (source: {SOURCE})")
    for p in adopt_paths:
        out(f"  {p}")
    if repended:
        out(f"consumers marked pending ({len(repended)}): {', '.join(repended)} — "
            f"they re-run unweakened on the next resume")
    else:
        out("no consumers to re-run (the writer is a leaf)")
    out(f"{node_id} is settled-by-adoption: it will not re-run even on a hash miss, "
        f"until `adopt {run_dir.name} {node_id} --release`, a heal round, or a "
        f"steering message dissolves the pin")
    return EXIT_OK
