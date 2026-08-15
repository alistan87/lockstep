"""Replay a prior run's recorded results instead of spawning.

This costs almost nothing to build because the engine already does the hard
part: every node is content-addressed by `input_hash` (SPEC §9.2) and its
validated result is persisted under `phases/<node>/`. Replay is therefore a
lookup, not a simulation — and it reproduces failures as faithfully as
successes, which is what makes it useful for support.

Strict by default. A recording whose `input_hash` no longer matches describes
DIFFERENT work — a flow edit, a changed persona, another lockstep.toml — and
silently serving it would turn a regression test green for the wrong reason.
`--replay-any` relaxes the check and logs every stale hit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .protocols import PlannedWork, RawResult, RenderCtx
from .state import RunState, compose_hash
from .taskgraph import Node


@dataclass
class Recording:
    status: str
    input_hash: str | None
    result_text: str | None
    error: str | None = None
    json_output: bool = False


@dataclass
class ReplayIndex:
    """(node_id, item_index) -> Recording, read out of a run directory."""

    source: Path
    entries: dict[tuple[str, int | None], Recording] = field(default_factory=dict)

    @classmethod
    def from_run_dir(cls, run_dir: Path) -> ReplayIndex:
        run_dir = Path(run_dir)
        state = RunState.model_validate_json(
            (run_dir / "state.json").read_text(encoding="utf-8")
        )
        index = cls(source=run_dir)
        for node_id, rec in state.nodes.items():
            phase = run_dir / "phases" / node_id
            text, is_json = _read_result(phase)
            index.entries[(node_id, None)] = Recording(
                status=rec.status,
                input_hash=rec.input_hash,
                result_text=text,
                error=rec.error,
                json_output=is_json,
            )
            for idx, irec in rec.items.items():
                item_phase = phase / "items" / str(idx)
                itext, i_is_json = _read_result(item_phase)
                index.entries[(node_id, int(idx))] = Recording(
                    status=irec.status,
                    input_hash=irec.input_hash,
                    result_text=itext,
                    error=irec.error,
                    json_output=i_is_json,
                )
        return index

    def get(self, node_id: str, item_index: int | None) -> Recording | None:
        return self.entries.get((node_id, item_index))


def _read_result(phase_dir: Path) -> tuple[str | None, bool]:
    """Read from the phase directory rather than the recorded `result_path`:
    an absolute path from the machine that produced the run does not resolve
    on the machine replaying it."""
    for name, is_json in (("result.json", True), ("result.txt", False)):
        p = phase_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8"), is_json
    return None, False


def _item_index(phase_dir: Path) -> int | None:
    """Map items live at `phases/<node>/items/<i>` (store.phase_dir), which is
    the only place the item index is available to an executor."""
    p = Path(phase_dir)
    if p.parent.name == "items":
        try:
            return int(p.name)
        except ValueError:
            return None
    return None


class ReplayExecutor:
    """Wraps a real executor: plans through it (so hashing is bit-identical),
    then serves the recording instead of executing."""

    def __init__(self, inner, index: ReplayIndex, *, strict: bool = True, log=print):
        self.inner = inner
        self.index = index
        self.strict = strict
        self.log = log
        self.kind = inner.kind
        self.cacheable = inner.cacheable
        # Nothing to correct: the recording is whatever it is, and a second
        # spawn would defeat the purpose of replaying.
        self.supports_corrective_respawn = False
        self.SpecModel = inner.SpecModel

    def bind_run(self, resources) -> None:
        """Same explicit delegation as SeedExecutor.bind_run, same reason
        (composition review finding 1): the wrapper forwards nothing."""
        bind = getattr(self.inner, "bind_run", None)
        if callable(bind):
            bind(resources)

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork:
        work = self.inner.plan(node, ctx)
        work.meta = {
            **work.meta,
            "_replay": {
                "node_id": node.id,
                "role": node.role,
                "kind": node.kind,
                "contract": node.contract,
            },
        }
        return work

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        info = work.meta["_replay"]
        node_id = info["node_id"]
        item = _item_index(phase_dir)
        recording = self.index.get(node_id, item)
        label = node_id if item is None else f"{node_id}[{item}]"
        if recording is None:
            return self._miss(
                f"no recorded result for {label} in {self.index.source} — "
                f"the flow has a node the recording does not"
            )

        parts = list(work.fingerprint_parts)
        if item is not None:
            parts.append(f"index:{item}")  # the engine's per-item hash (A3.1)
        computed = compose_hash(info["role"], info["kind"], info["contract"], parts)
        if recording.input_hash and computed != recording.input_hash:
            if self.strict:
                return self._miss(
                    f"recording for {label} has a different input_hash "
                    f"(recorded {recording.input_hash[:12]}…, now {computed[:12]}…): the "
                    f"flow, a persona, or the executor config changed since it was "
                    f"made. Re-record, or pass --replay-any to use it anyway"
                )
            self.log(
                f"replay: stale recording for {label} "
                f"(recorded {recording.input_hash[:12]}…, now {computed[:12]}…) — serving it anyway"
            )

        if recording.result_text is None:
            # A node that failed before producing anything replays as exactly
            # that, rather than as a replay-infrastructure error.
            return RawResult(
                exit_code=1,
                result_text=None,
                source="none",
                error=f"replayed failure: {recording.error or recording.status}",
            )
        name = "result.json" if recording.json_output else "result.txt"
        target = phase_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(recording.result_text, encoding="utf-8")
        return RawResult(
            exit_code=0 if recording.status in ("done", "skipped") else 1,
            result_text=recording.result_text,
            source="file",
            error=None if recording.status in ("done", "skipped") else f"replayed failure: {recording.error}",
        )

    def _miss(self, message: str) -> RawResult:
        return RawResult(exit_code=127, result_text=None, source="none", error=message)


def wrap_registry(registry, index: ReplayIndex, *, strict: bool = True, log=print):
    """Replace every registered executor with a replay wrapper, in place."""
    for kind in registry.kinds():
        inner = registry.get(kind)
        registry.register(ReplayExecutor(inner, index, strict=strict, log=log))
    return registry
