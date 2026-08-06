"""`lockstep explain` — which hash inputs moved (the cache-miss explainer).

Reads the labelled part digests the engine records beside every `input_hash`
(`PhaseRecord.hash_parts`) and answers the question a re-billed node raises:
WHICH input changed? Two modes:

  explain <run_dir> <node>                    the node's recorded parts, plus
                                              why its last revalidation re-ran it
  explain <run_dir> <node> --against <other>  label-level diff between two runs

Everything printed comes from recorded state — this command never plans, never
spawns, and never recomputes a hash. Runs recorded before part recording
existed render as `unrecorded` (the UNCHAINED precedent from trace chaining).
"""

from __future__ import annotations

from pathlib import Path

from . import EXIT_CONFIG, EXIT_OK
from .state import PhaseRecord, RunState, diff_labels, load_state


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
