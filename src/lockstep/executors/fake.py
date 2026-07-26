"""Test double (SPEC §13.1): the offline suite's executor.

Behavior is driven by the node's own spec so fixtures are self-describing, and
every spawn is recorded in `calls` so tests can assert ordering, overlap,
readonly flags, and corrective-respawn prompts.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..interpolate import render_template
from ..protocols import PlannedWork, RawResult, RenderCtx
from ..taskgraph import Node
from .shell import resolve_ctx_of


class FakeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = ""
    # Successive results, one per spawn of this node (corrective re-spawns and
    # heal rounds advance the sequence); the last entry repeats. A string is the
    # raw result text; anything else is JSON-serialized.
    outputs: list[Any] = []
    readonly: bool = False
    write_files: dict[str, str] = {}  # rel path -> content, written on execute
    exit_code: int = 0
    costs_tokens: bool = True
    sleep_s: float = 0.0
    empty_result: bool = False  # emit no result at all (tests the auto-retry)
    progress: list[dict] = []  # ProgressEvent dicts appended to progress.jsonl (r6 C1)


@dataclass
class FakeCall:
    node_id: str
    prompt: str
    readonly: bool
    corrective: bool
    started: float = 0.0
    ended: float = 0.0
    thread: str = ""


@dataclass
class FakeExecutor:
    repo_root: Path
    kind: str = "fake"
    cacheable: bool = True
    supports_corrective_respawn: bool = True
    SpecModel: type = FakeSpec
    calls: list[FakeCall] = field(default_factory=list)
    _counts: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork:
        spec = FakeSpec.model_validate(node.spec)
        rendered = render_template(
            spec.task,
            resolve_ctx_of(ctx),
            fence=True,
            max_interp_chars=ctx.max_interp_chars,
            spill_dir=ctx.phase_dir / "inputs",
            null_for_skipped=ctx.allow_null_for_skipped,
        )
        heal = f"\n{ctx.heal_text}" if ctx.heal_text else ""
        steer = f"\n{ctx.steer_text}" if ctx.steer_text else ""
        return PlannedWork(
            render=rendered.prompt_text + heal + steer,
            fingerprint_parts=[
                f"prompt:{rendered.hash_text}{heal}{steer}",
                f"config:{ctx.config_digest}",
            ],
            costs_tokens=spec.costs_tokens,
            exclusive=[] if spec.readonly else ["tree"],
            meta={
                "node_id": node.id,
                "spec": spec.model_dump(),
                "output": node.output,
                "corrective": False,
            },
        )

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        spec = FakeSpec.model_validate(work.meta["spec"])
        node_id = work.meta["node_id"]
        call = FakeCall(
            node_id=node_id,
            prompt=str(work.render),
            readonly=spec.readonly,
            corrective=bool(work.meta.get("corrective")),
            started=time.monotonic(),
            thread=threading.current_thread().name,
        )
        with self._lock:
            attempt = self._counts.get(node_id, 0)
            self._counts[node_id] = attempt + 1
            self.calls.append(call)
        if spec.progress:
            with open(phase_dir / "progress.jsonl", "a", encoding="utf-8") as f:
                for ev in spec.progress:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if spec.sleep_s:
            time.sleep(spec.sleep_s)
        for rel, content in spec.write_files.items():
            target = Path(self.repo_root) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        call.ended = time.monotonic()
        if spec.empty_result:
            return RawResult(exit_code=spec.exit_code, result_text=None, source="none")
        if not spec.outputs:
            out: Any = ""
        else:
            out = spec.outputs[min(attempt, len(spec.outputs) - 1)]
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        fname = "result.json" if work.meta["output"] == "json" else "result.txt"
        (phase_dir / fname).write_text(text, encoding="utf-8")
        return RawResult(exit_code=spec.exit_code, result_text=text, source="file")
