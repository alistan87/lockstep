"""Executor / Workspace / Store / Policy protocols (SPEC §8.1, AMENDMENTS M2).

Seams, not proven abstractions: the rule of two says only Executor has a second
real implementation in v1. Core behaviors (topological ordering, hash
composition, skip propagation, gate adjudication, retry/heal orchestration,
budget accounting) are never pluggable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .taskgraph import Node


class PlannedWork(BaseModel):
    """What an executor intends to do, plus everything that identifies it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    render: str | list[str]  # rendered prompt, or argv
    fingerprint_parts: list[str]  # executor-contributed hash inputs (SPEC §9.2)
    costs_tokens: bool = False
    exclusive: list[str] = []  # resources the executor itself requires
    meta: dict[str, Any] = {}  # executor-private carry-through (cwd, stdin, env…)


class RenderCtx(BaseModel):
    """Everything an executor needs to plan a node (AMENDMENTS M2)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    args: dict[str, str]
    outputs: dict[str, Any]  # node_id -> raw result text
    json_results: dict[str, Any]  # node_id -> parsed JSON result
    skipped: set[str]  # node_ids with status skipped
    deps: list[str]
    item: Any = None
    has_item: bool = False
    item_var: str = "item"
    allow_null_for_skipped: bool = False  # the optional:true path
    repo_root: Path
    personas_dir: Path
    phase_dir: Path
    max_interp_chars: int
    config_digest: str
    executor_default: str | None = None
    heal_text: str = ""  # gate findings appended on heal re-runs; folds into the hash
    steer_text: str = ""  # rendered steering block (r6 C2); folds into the hash
    # The flow's contracts_module, so an executor can resolve the node's own
    # contract and STATE its shape in the prompt (E1) — the driver knows the
    # schema it will validate against; staying silent about it charged authors
    # a corrective re-spawn for every guessed field name.
    contracts_module: str | None = None


class RawResult(BaseModel):
    """Executor -> driver. Contract validation is the driver's job, never the
    executor's (and never the model's)."""

    exit_code: int
    result_text: str | None  # result file content, or stdout fallback, or None
    source: Literal["file", "stdout", "none"]
    stdout_path: str = ""
    stderr_path: str = ""
    timed_out: bool = False
    error: str | None = None


class SnapshotRef(BaseModel):
    ref: str  # GitWorkspace: a real tree object sha (includes untracked files)


class Decision(BaseModel):
    allowed: bool
    reason: str = ""


@runtime_checkable
class Executor(Protocol):
    kind: str
    cacheable: bool  # False ⇒ always re-run (shell); True ⇒ hash-skip eligible
    supports_corrective_respawn: bool  # harness-kind mechanism (AMENDMENTS A4)
    SpecModel: type[BaseModel]

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork: ...

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult: ...


@runtime_checkable
class Workspace(Protocol):
    """The side-effect domain."""

    def fingerprint(self) -> str: ...

    def fingerprint_detail(self) -> tuple[str, dict[str, str]]: ...

    def snapshot(self) -> SnapshotRef: ...

    def changed_paths(self, since: SnapshotRef) -> list[str]: ...

    def diff_patch(self, since: SnapshotRef) -> str: ...

    def restore(self, ref: SnapshotRef, scope: list[str], discard_dir: Path) -> None: ...

    def staged_paths(self) -> set[str]: ...

    def unstage(self, paths: list[str]) -> None: ...


@runtime_checkable
class Store(Protocol):
    """Run state."""

    def record(self, rec: Any) -> None: ...

    def result_of(self, node_id: str) -> Any: ...

    def load_run(self, run_dir: Path) -> Any: ...


@runtime_checkable
class Policy(Protocol):
    """Authorization seam. Consulted at verify time AND immediately pre-execute.
    v1: actor is the constant "local-user" until a supervisor exists (SPEC §16.2)."""

    def allows(self, node: Node, actor: str) -> Decision: ...
