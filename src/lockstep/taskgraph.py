"""Taskgraph format models (SPEC §4) and static verification (SPEC §6).

`lockstep verify` is TaskGraph.model_validate_json plus the §6 rules. Consults
only the flow file, personas/, and lockstep.toml — never runtime flags.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .contracts import ContractError, Verdict, resolve_contract
from .interpolate import InterpolationError, extract_refs, parse_when

Role = Literal["work", "gate", "approval", "map"]  # v1; DAG semantics, core-owned

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_OVER_RE = re.compile(r"^\{steps\.([a-z0-9][a-z0-9-]*)\.json(?:\.[A-Za-z0-9_.\-]+)?\}$")


class RetrySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    max: int = 0
    backoff_ms: int = 500
    factor: float = 2.0


class HealSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    max_rounds: int = 0  # gate-triggered re-run of `targets` (SPEC §9.4)
    targets: list[str] = []  # explicit harness-node ids; required when max_rounds > 0
    rollback: bool = True  # restore the proactive baseline snapshot


class Node(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    role: Role = "work"
    kind: str = "harness"  # executor registry key
    spec: dict = {}  # kind-specific; validated by the executor's SpecModel
    depends_on: list[str] = []
    when: str | None = None  # "{ref} ==|!= <literal>" only
    over: str | None = None  # role=map: "{steps.X.json}" / "{steps.X.json.path}"
    item_var: str = "item"  # role=map
    output: Literal["text", "json"] = "text"
    contract: str | None = None  # required when output == "json"
    exclusive: list[str] = []  # resources held while running (SPEC §9.1)
    retry: RetrySpec = RetrySpec()
    heal: HealSpec = HealSpec()  # role=gate only
    timeout_s: int = 900
    concurrency: int | None = None  # role=map fan-out cap
    optional: bool = False
    final: bool = False


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    max_agent_spawns: int = 40  # counts spawns of token-costing kinds only
    max_run_minutes: int = 120


class TaskGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format_version: str = "1.0"
    name: str
    description: str = ""
    args: dict[str, str | None] = {}  # arg -> default; None = required
    contracts_module: str | None = None
    executor_default: str | None = None  # stanza name in lockstep.toml, for kind="harness"
    concurrency: int = 4  # layer fan-out default
    max_interp_chars: int = 20000  # per interpolated value before spill (SPEC §7)
    budget: Budget = Budget()
    nodes: list[Node]

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    @property
    def final_node_id(self) -> str:
        for n in self.nodes:
            if n.final:
                return n.id
        return self.nodes[-1].id


class FlowError(Exception):
    """Flow file unreadable or structurally invalid (exit 5)."""


def _merge_x_lockstep(obj: Any) -> Any:
    """Merge "x-lockstep" objects into their parent before validation; drop other
    x-* namespaces so a file can be shared with another runtime while
    extra="forbid" still catches typos (SPEC §4)."""
    if isinstance(obj, dict):
        merged: dict = {}
        for k, v in obj.items():
            if k == "x-lockstep":
                continue
            if isinstance(k, str) and k.startswith("x-"):
                continue
            merged[k] = _merge_x_lockstep(v)
        ext = obj.get("x-lockstep")
        if isinstance(ext, dict):
            for k, v in ext.items():
                merged[k] = _merge_x_lockstep(v)
        return merged
    if isinstance(obj, list):
        return [_merge_x_lockstep(v) for v in obj]
    return obj


def load_flow(path: Path) -> tuple[TaskGraph, str]:
    """Load a flow file. Returns (graph, flow_hash). flow_hash is the sha256 of
    the file's raw bytes (AMENDMENTS M5)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise FlowError(f"cannot read flow file {path}: {e}")
    flow_hash = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FlowError(f"flow file {path} is not valid JSON: {e}")
    data = _merge_x_lockstep(data)
    try:
        tg = TaskGraph.model_validate(data)
    except ValidationError as e:
        raise FlowError(f"flow file {path} failed validation:\n{e}")
    if not tg.nodes:
        raise FlowError(f"flow file {path} has no nodes")
    major = tg.format_version.split(".")[0]
    if major != "1":
        raise FlowError(
            f"unsupported format_version {tg.format_version!r}: this lockstep understands 1.x only"
        )
    return tg, flow_hash


# --- static verification (SPEC §6) ---------------------------------------------


@dataclass(frozen=True)
class VerifyIssue:
    level: Literal["error", "warning"]
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}[{self.code}]: {self.message}"


def _topo_depths(tg: TaskGraph) -> dict[str, int]:
    depths: dict[str, int] = {}

    def depth(nid: str, seen: tuple[str, ...] = ()) -> int:
        if nid in depths:
            return depths[nid]
        if nid in seen:
            return 0  # cycle; reported separately
        node = tg.node(nid)
        d = 1 + max((depth(p, seen + (nid,)) for p in node.depends_on if any(n.id == p for n in tg.nodes)), default=-1)
        depths[nid] = d
        return d

    for n in tg.nodes:
        depth(n.id)
    return depths


def _find_cycle(tg: TaskGraph) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in tg.nodes}
    ids = set(color)

    def dfs(nid: str, stack: list[str]) -> list[str] | None:
        color[nid] = GRAY
        stack.append(nid)
        for dep in tg.node(nid).depends_on:
            if dep not in ids:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = dfs(dep, stack)
                if found:
                    return found
        stack.pop()
        color[nid] = BLACK
        return None

    for n in tg.nodes:
        if color[n.id] == WHITE:
            found = dfs(n.id, [])
            if found:
                return found
    return None


def _ancestors(tg: TaskGraph, node_id: str) -> set[str]:
    ids = {n.id for n in tg.nodes}
    out: set[str] = set()
    frontier = [node_id]
    while frontier:
        nid = frontier.pop()
        for dep in tg.node(nid).depends_on:
            if dep in ids and dep not in out:
                out.add(dep)
                frontier.append(dep)
    return out


def _node_templates(node: Node) -> list[tuple[str, str]]:
    """(where, template) pairs whose {steps...} refs must be declared deps."""
    out: list[tuple[str, str]] = []
    task = node.spec.get("task")
    if isinstance(task, str):
        out.append(("spec.task", task))
    cmd = node.spec.get("cmd")
    if isinstance(cmd, list):
        for i, part in enumerate(cmd):
            if isinstance(part, str):
                out.append((f"spec.cmd[{i}]", part))
    if node.when:
        out.append(("when", node.when))
    if node.over:
        out.append(("over", node.over))
    return out


def verify_flow(
    tg: TaskGraph,
    *,
    registry: Any = None,  # registry.Registry; Any to avoid an import cycle
    config: Any = None,  # registry.LockstepConfig | None
    repo_root: Path | None = None,
) -> list[VerifyIssue]:
    """All §6 findings at once. Errors ⇒ exit 5."""
    issues: list[VerifyIssue] = []
    err = lambda code, msg: issues.append(VerifyIssue("error", code, msg))
    warn = lambda code, msg: issues.append(VerifyIssue("warning", code, msg))
    ids = [n.id for n in tg.nodes]
    idset = set(ids)

    # 1. ids and final
    seen: set[str] = set()
    for nid in ids:
        if nid in seen:
            err("duplicate-id", f"node id {nid!r} appears more than once")
        seen.add(nid)
        if not _ID_RE.match(nid):
            err("bad-id", f"node id {nid!r} does not match ^[a-z0-9][a-z0-9-]*$")
    finals = [n.id for n in tg.nodes if n.final]
    if len(finals) > 1:
        err("multiple-final", f"more than one final node: {finals}")
    elif not finals:
        warn("default-final", f"no final node declared; defaulting to last node {tg.nodes[-1].id!r}")

    # 2. deps + acyclicity
    for n in tg.nodes:
        for dep in n.depends_on:
            if dep not in idset:
                err("unknown-dep", f"node {n.id!r} depends on unknown node {dep!r}")
            if dep == n.id:
                err("self-dep", f"node {n.id!r} depends on itself")
    cycle = _find_cycle(tg)
    if cycle:
        err("cycle", "dependency cycle: " + " -> ".join(cycle))

    # 3 + 4. reference discipline
    referenced_args: set[str] = set()
    for n in tg.nodes:
        for where, template in _node_templates(n):
            for ref in extract_refs(template):
                parts = ref.split(".")
                if parts[0] == "steps" and len(parts) >= 2:
                    if parts[1] not in n.depends_on:
                        err(
                            "unlisted-step-ref",
                            f"node {n.id!r} {where} references {{steps.{parts[1]}...}} "
                            f"but {parts[1]!r} is not in depends_on",
                        )
                elif parts[0] == "args":
                    if len(parts) == 2:
                        referenced_args.add(parts[1])
                        if parts[1] not in tg.args:
                            err("undeclared-arg", f"node {n.id!r} {where} references undeclared {{args.{parts[1]}}}")
                elif parts[0] == "previous":
                    if len(n.depends_on) != 1:
                        err(
                            "previous-needs-one-dep",
                            f"node {n.id!r} uses {{previous.output}} with {len(n.depends_on)} dependencies",
                        )
                elif parts[0] == "item":
                    if n.role != "map":
                        err("item-outside-map", f"node {n.id!r} {where} uses {{{ref}}} but role is not \"map\"")
    for arg in tg.args:
        if arg not in referenced_args:
            # §6.4 is an error, not lint — only rule 9 is designated a warning.
            # (Silently downgraded to a warning at first; caught by the
            # audit-spec arbiter gate.)
            err("unused-arg", f"declared arg {arg!r} is never referenced")

    # 5. role/kind cross-checks
    for n in tg.nodes:
        set_fields = n.model_fields_set
        if n.role != "map":
            for f in ("over", "item_var", "concurrency"):
                if f in set_fields:
                    err("map-field-on-nonmap", f"node {n.id!r} sets {f!r} but role is {n.role!r}")
        else:
            if not n.over:
                err("map-missing-over", f"map node {n.id!r} has no `over`")
        if n.role != "gate" and "heal" in set_fields:
            err("heal-on-nongate", f"node {n.id!r} sets `heal` but role is {n.role!r}")
        if n.role == "approval":
            if "kind" in set_fields or n.spec:
                err("approval-with-kind", f"approval node {n.id!r} must not set kind/spec (core-handled)")
        if n.role == "gate":
            ok = n.output == "json" and n.contract is not None
            if ok:
                try:
                    ref = resolve_contract(n.contract, tg.contracts_module)
                    ok = (not ref.is_array) and issubclass(ref.model, Verdict)
                except ContractError:
                    ok = False
            if not ok:
                err(
                    "gate-contract",
                    f"gate node {n.id!r} requires output \"json\" with a contract resolving to Verdict",
                )

    # 6. kind registry, SpecModel, executor stanza, persona
    personas_dir = (repo_root or Path(".")) / "personas"
    for n in tg.nodes:
        if n.role == "approval":
            continue
        executor = registry.get(n.kind) if registry else None
        if registry and executor is None:
            err("unknown-kind", f"node {n.id!r} has unknown kind {n.kind!r}")
            continue
        spec_obj = None
        if executor is not None:
            try:
                spec_obj = executor.SpecModel.model_validate(n.spec)
            except ValidationError as e:
                err("spec-invalid", f"node {n.id!r} spec fails {n.kind} validation: {e}")
        if n.kind == "harness":
            stanza_name = (n.spec.get("executor") or tg.executor_default or (config.default if config else None))
            stanza = config.executors.get(stanza_name) if (config and stanza_name) else None
            if stanza is None:
                err(
                    "no-executor-stanza",
                    f"node {n.id!r}: no executor stanza {stanza_name!r} in lockstep.toml "
                    "(set spec.executor, executor_default, or the config default)",
                )
            # 11. readonly enforcement
            if n.spec.get("readonly") and stanza is not None and not stanza.readonly_argv:
                err(
                    "readonly-unenforced",
                    f"node {n.id!r} declares spec.readonly but executor stanza {stanza_name!r} "
                    "has no readonly_argv — declared-but-unenforced readonly is a race condition",
                )
            persona = n.spec.get("persona")
            if persona and not (personas_dir / f"{persona}.md").exists():
                err("persona-missing", f"node {n.id!r}: persona {persona!r} not found in {personas_dir}")

    # 7. output/contract, over shape
    for n in tg.nodes:
        if n.output == "json":
            if not n.contract:
                err("json-without-contract", f"node {n.id!r} has output \"json\" but no contract")
            else:
                try:
                    resolve_contract(n.contract, tg.contracts_module)
                except ContractError as e:
                    err("contract-unresolvable", f"node {n.id!r}: {e}")
        elif n.contract and n.role != "gate":
            warn("contract-ignored", f"node {n.id!r} names a contract but output is \"text\"")
        if n.over is not None and not _OVER_RE.match(n.over):
            err("over-not-json", f"node {n.id!r}: `over` must be a {{steps.X.json...}} reference, got {n.over!r}")

    # 8. when grammar
    for n in tg.nodes:
        if n.when is not None:
            try:
                parse_when(n.when)
            except InterpolationError as e:
                err("when-grammar", f"node {n.id!r}: {e}")

    # 9. lints
    depths = _topo_depths(tg)
    by_depth: dict[int, list[Node]] = {}
    for n in tg.nodes:
        by_depth.setdefault(depths.get(n.id, 0), []).append(n)
    for depth, layer in by_depth.items():
        held: dict[str, list[str]] = {}
        for n in layer:
            eff = list(n.exclusive)
            if n.kind in ("harness", "fake") and not n.spec.get("readonly"):
                eff.append("tree")
            for token in set(eff):
                held.setdefault(token, []).append(n.id)
        for token, holders in held.items():
            if len(holders) > 1:
                warn(
                    "exclusive-collision",
                    f"nodes {holders} in the same layer share exclusive resource {token!r}; "
                    "the scheduler will serialize them",
                )
    git_managed = repo_root is not None and (Path(repo_root) / ".git").exists()
    for n in tg.nodes:
        if n.role == "gate" and n.heal.max_rounds > 0 and n.heal.rollback and not git_managed:
            warn(
                "heal-rollback-nongit",
                f"gate {n.id!r} has heal.rollback but the workspace is not git-managed; "
                "this errors at run time (exit 7)",
            )

    # 10. heal targets
    target_owner: dict[str, str] = {}
    for n in tg.nodes:
        if n.role != "gate" or n.heal.max_rounds <= 0:
            continue
        if not n.heal.targets:
            err("heal-targets-required", f"gate {n.id!r} has heal.max_rounds > 0 but empty heal.targets")
            continue
        ancestors = _ancestors(tg, n.id)
        for t in n.heal.targets:
            if t not in idset:
                err("heal-target-unknown", f"gate {n.id!r} heal target {t!r} does not exist")
                continue
            tnode = tg.node(t)
            if tnode.kind not in ("harness", "fake"):
                err("heal-target-kind", f"gate {n.id!r} heal target {t!r} must be harness-kind, is {tnode.kind!r}")
            if t not in ancestors:
                err("heal-target-not-ancestor", f"gate {n.id!r} heal target {t!r} is not an ancestor of the gate")
            if t in target_owner and target_owner[t] != n.id:
                err(
                    "heal-target-overlap",
                    f"node {t!r} appears in heal.targets of both {target_owner[t]!r} and {n.id!r}; "
                    "overlapping heal scopes have no sound restore ordering",
                )
            target_owner.setdefault(t, n.id)

    return issues
