"""`lockstep gc` (A5) — estimate-aware retention for runs/.

runs/ holds prompts, diffs, and model output — sensitive by designation — and
grows without bound; deleting by hand silently degrades `--estimate`, which
mines those same directories. The retention rules keep exactly the history the
estimator and the humans rely on:

  - the newest run of every LINEAGE, always and unconditionally — attachment
    (`find_attachable_run`) is keyed per (flow_hash, args), so ranking by
    flow_hash alone would let gc delete the head `lockstep run` would attach
    to and silently fork the lineage;
  - the newest N runs per lineage (default 5) — the history estimate_flow
    mines, so the cost floor never silently degrades;
  - anything younger than M days (default 14);
  - any run holding a live lockfile, an unanswered approval, or a
    rejection.txt (human-authored artifacts are not the engine's to expire).

Dry-run by default: the plan prints, per candidate, every rule that FAILED to
protect it — a deletion the operator cannot explain is a deletion that should
not happen. Only --apply deletes. Directories without a state.json are never
touched: they are not runs, whatever they are.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .state import RunState


@dataclass
class GcPlan:
    candidates: list[tuple[Path, str]]  # (run dir, why nothing protected it)
    kept: int
    skipped: int  # dirs without state.json — not runs, never touched


def _age_days(started_at: str, now: _dt.datetime) -> float | None:
    try:
        started = _dt.datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=_dt.UTC)
    return (now - started).total_seconds() / 86400


def plan_gc(
    runs_dir: Path,
    keep_per_flow: int = 5,
    keep_days: int = 14,
    now: _dt.datetime | None = None,
) -> GcPlan:
    runs_dir = Path(runs_dir)
    now = now or _dt.datetime.now(_dt.UTC)
    candidates: list[tuple[Path, str]] = []
    kept = skipped = 0
    # Lineage key = (flow_hash, args), matching find_attachable_run exactly.
    by_flow: dict[tuple, list[tuple[str, Path, RunState]]] = {}
    if not runs_dir.exists():
        return GcPlan([], 0, 0)
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir():
            continue
        state_path = d / "state.json"
        if not state_path.exists():
            skipped += 1
            continue
        try:
            state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1  # unreadable state: not provably a run — never touched
            continue
        lineage = (state.flow_hash, tuple(sorted(state.args.items())))
        by_flow.setdefault(lineage, []).append((state.started_at, d, state))
    for _lineage, runs in by_flow.items():
        runs.sort(reverse=True)  # newest first
        for rank, (started_at, d, state) in enumerate(runs):
            protections: list[str] = []
            unprotected: list[str] = []
            if (d / "lock").exists():
                protections.append("live lockfile")
            else:
                unprotected.append("no lockfile")
            open_approvals = [
                rec.node_id for rec in state.nodes.values()
                if rec.role == "approval" and rec.status in ("pending", "running", "blocked")
            ]
            if open_approvals:
                protections.append(f"unanswered approval {open_approvals}")
            else:
                unprotected.append("no unanswered approval")
            if (d / "rejection.txt").exists():
                protections.append("rejection.txt (human-authored)")
            else:
                unprotected.append("no rejection.txt")
            if rank == 0:
                # Unconditional — not subject to keep_per_flow (even 0): this
                # is the lineage head `lockstep run` would attach to, and
                # deleting it silently forks the lineage and re-bills a prefix.
                protections.append(
                    f"the newest run of this lineage of {state.flow_name!r} (always kept)"
                )
            elif rank < keep_per_flow:
                protections.append(
                    f"newest #{rank + 1} of its lineage (keep_per_flow={keep_per_flow}"
                    " — the history --estimate mines)"
                )
            else:
                unprotected.append(
                    f"#{rank + 1} newest of its {state.flow_name!r} lineage (> {keep_per_flow})"
                )
            age = _age_days(started_at, now)
            if age is None or age < keep_days:
                protections.append(f"younger than keep_days={keep_days}")
            else:
                unprotected.append(f"{age:.0f} days old (> {keep_days})")
            if protections:
                kept += 1
            else:
                candidates.append((d, "; ".join(unprotected)))
    return GcPlan(candidates=candidates, kept=kept, skipped=skipped)


def apply_gc(plan: GcPlan, log=print) -> int:
    """Delete the planned candidates. Returns the number actually deleted.
    One retry per dir: this machine's AV holds handles transiently."""
    deleted = 0
    for d, _reason in plan.candidates:
        for attempt in (0, 1):
            try:
                shutil.rmtree(d)
                deleted += 1
                break
            except OSError as e:
                if attempt == 1:
                    log(f"gc: could not delete {d}: {e} (left in place)")
                else:
                    time.sleep(0.2)
    return deleted
