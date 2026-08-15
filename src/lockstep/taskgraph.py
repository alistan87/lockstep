"""Taskgraph format models (SPEC §4) and static verification (SPEC §6).

`lockstep verify` is TaskGraph.model_validate_json plus the §6 rules. Consults
only the flow file, personas/, and lockstep.toml — never runtime flags.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
    # "pass" = exhausting max_rounds accepts the best-so-far instead of
    # blocking; the recorded verdict names the truth ("accepted after N rounds
    # without resolving: ..."), never a plain pass. Forbidden with rollback
    # (§6 on-exhausted-with-rollback) and lint-visible. Optional field within
    # 1.x: every existing flow keeps its meaning; a flow that uses it fails on
    # an older driver at load (extra="forbid") — recorded in DEVIATIONS
    # 2026-08-14 (PROPOSAL-taskflow-parity-tiers 2.1, adopted 2026-08-13).
    on_exhausted: Literal["block", "pass"] = "block"


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
    # NOTE: baseline gates (E4) are declared as `spec.baseline`, not a
    # first-class field — same §15 reasoning as `spec.writes` (a first-class
    # field bumps format_version 1.0 → 1.1; a spec key is validated by the
    # executor's SpecModel, and an older verifier rejects it with a named
    # spec-invalid §6 error rather than a pydantic traceback).
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
    writes = node.spec.get("writes")
    if isinstance(writes, list):
        # Scopes are rendered too (args only), so their refs face the same
        # declaration rules as any other template — an `{args.k}` in a scope
        # is a real reference, and `unused-arg` must see it as one.
        for i, w in enumerate(writes):
            if isinstance(w, str):
                out.append((f"spec.writes[{i}]", w))
    reads = node.spec.get("reads")
    if isinstance(reads, list):
        # Same rule for read scopes (parity 3.1): rendered with args, so an
        # arg used only in a reads glob is a real reference.
        for i, r in enumerate(reads):
            if isinstance(r, str):
                out.append((f"spec.reads[{i}]", r))
    if node.kind == "flow":
        # Child-arg VALUES render like any template (composition §4), so
        # {args.K} there is a real reference and {steps.X...} needs the dep.
        child_args = node.spec.get("args")
        if isinstance(child_args, dict):
            for key in sorted(child_args):
                v = child_args[key]
                if isinstance(v, str):
                    out.append((f"spec.args[{key}]", v))
    return out


def _serialized_on_tree(node: Node) -> bool:
    """Does this node's executor take the `tree` token unconditionally?

    Mirrors `PlannedWork.exclusive` at the three write-capable executors:
    `shell` always (`shell.py`), `harness`/`fake` unless readonly. An approval
    spawns nothing and takes none. Write-scope detection is only sound while
    the node is serialized on the tree, so this is what decides whether a
    declared scope is enforceable — keep it in step with the executors.
    """
    if node.role == "approval":
        return False
    if node.kind == "shell":
        return True
    return node.kind in ("harness", "fake") and not node.spec.get("readonly")


def verify_flow(
    tg: TaskGraph,
    *,
    registry: Any = None,  # registry.Registry; Any to avoid an import cycle
    config: Any = None,  # registry.LockstepConfig | None
    repo_root: Path | None = None,
    policy: Any = None,  # Policy; §8.1: consulted at verify time AND pre-execute
    _flow_stack: tuple = (),  # composition: ancestor flow FILES, for cycle/depth
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
        if n.spec.get("baseline"):
            # E4: the baseline body runs BEFORE any node executes, so it may
            # not read another step's output — it measures the pre-run tree.
            if n.role != "gate":
                err("baseline-not-gate", f"node {n.id!r} sets `spec.baseline` but role is {n.role!r}")
            else:
                for where, template in _node_templates(n):
                    if where == "when":
                        continue  # `when` gates the EVALUATION (post-deps), never the baseline body
                    refs = [r for r in extract_refs(template)
                            if r.split(".")[0] in ("steps", "previous")]
                    if refs:
                        err(
                            "baseline-gate-references-steps",
                            f"baseline gate {n.id!r} {where} references {refs}; its body runs "
                            f"once before any node executes, so step outputs do not exist yet",
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

    # 7b. write-scope permits (spec.writes). Presence-keyed: a DECLARED-empty
    # scope (writes: []) is enforced by the engine ("this node writes
    # nothing"), so it gets the same map/serialization checks as a non-empty
    # one — skipping it would leave a declaration the engine honours invisible
    # to verification (V1; DEVIATIONS 2026-08-11).
    for n in tg.nodes:
        if "writes" not in n.spec:
            continue
        writes = n.spec.get("writes")
        if not isinstance(writes, list):
            err("bad-write-scope", f"node {n.id!r}: spec.writes must be a list of paths")
            continue
        for w in writes:
            if isinstance(w, str):
                for ref in extract_refs(w):
                    parts = ref.split(".")
                    if parts[0] != "args" or len(parts) != 2:
                        err(
                            "dynamic-write-scope",
                            f"node {n.id!r}: spec.writes entry {w!r} references "
                            f"{{{ref}}} — a scope may only interpolate {{args.NAME}}. "
                            f"Anything resolved from a step's output would let the "
                            f"graph decide what it is allowed to write",
                        )
            if not isinstance(w, str) or not w.strip():
                err("bad-write-scope", f"node {n.id!r}: spec.writes has an empty entry")
            elif w.startswith(("/", "\\")) or PurePosixPath(w.replace("\\", "/")).is_absolute():
                err(
                    "bad-write-scope",
                    f"node {n.id!r}: spec.writes entry {w!r} is absolute; scopes are "
                    f"relative to the repo root",
                )
            elif ".." in PurePosixPath(w.replace("\\", "/")).parts:
                err(
                    "bad-write-scope",
                    f"node {n.id!r}: spec.writes entry {w!r} escapes the repo root",
                )
        if n.role == "map":
            err(
                "write-scope-on-map",
                f"map node {n.id!r} declares spec.writes; per-item write scopes are not "
                f"supported (the items share one tree and one diff)",
            )
        elif "tree" not in n.exclusive and not _serialized_on_tree(n):
            # The declaration still reaches the spawn as LOCKSTEP_WRITE_SCOPE,
            # so an in-harness extension can enforce it; only the driver's
            # after-the-fact detection needs serialization.
            warn(
                "write-scope-unenforced",
                f"node {n.id!r} declares spec.writes but does not hold the 'tree' token, so a "
                f"concurrent node's writes would be misattributed and driver-side detection "
                f"is off; add exclusive: [\"tree\"] to enable it",
            )

    # Parity 3.1: spec.reads gets the writes checks it can inherit — same
    # entry grammar ({args.NAME} and nothing else), same repo-root jail. No
    # map prohibition (reads share nothing) and no serialization concern
    # (hashing a file needs no token).
    for n in tg.nodes:
        if "reads" not in n.spec:
            continue
        reads = n.spec.get("reads")
        if not isinstance(reads, list):
            err("bad-read-scope", f"node {n.id!r}: spec.reads must be a list of glob patterns")
            continue
        for r in reads:
            if isinstance(r, str):
                for ref in extract_refs(r):
                    parts = ref.split(".")
                    if parts[0] != "args" or len(parts) != 2:
                        err(
                            "dynamic-read-scope",
                            f"node {n.id!r}: spec.reads entry {r!r} references "
                            f"{{{ref}}} — reads may only interpolate {{args.NAME}}. "
                            f"A read set resolved from a step's output would let the "
                            f"graph decide what this node's hash depends on",
                        )
            if not isinstance(r, str) or not r.strip():
                err("bad-read-scope", f"node {n.id!r}: spec.reads has an empty entry")
            elif r.startswith(("/", "\\")) or PurePosixPath(r.replace("\\", "/")).is_absolute():
                err(
                    "bad-read-scope",
                    f"node {n.id!r}: spec.reads entry {r!r} is absolute; reads are "
                    f"relative to the repo root",
                )
            elif ".." in PurePosixPath(r.replace("\\", "/")).parts:
                err(
                    "bad-read-scope",
                    f"node {n.id!r}: spec.reads entry {r!r} escapes the repo root",
                )

    # PROPOSAL-flow-composition §4: rules for kind="flow" nodes. String-keyed
    # on the kind (the registry check above already errors when no executor
    # claims it); the file walk needs repo_root and is skipped without one.
    flow_nodes = [n for n in tg.nodes if n.kind == "flow"]
    for n in flow_nodes:
        if n.role == "map":
            err("flow-in-map",
                f"map node {n.id!r} has kind \"flow\"; a fan-out of engines is deferred — "
                f"if a map item needs a subgraph, the subgraph wants to be the flow")
        if n.exclusive:
            err("exclusive-on-flow",
                f"flow node {n.id!r} declares exclusive: {n.exclusive} — a parked parent "
                f"holding a token its child's nodes need is the composition deadlock; "
                f"the child's own nodes acquire what they declare")
        if "writes" in n.spec:
            err("write-scope-on-flow",
                f"flow node {n.id!r} declares spec.writes; scopes belong to the child's "
                f"own nodes, which declare and enforce them exactly as they do standalone")
        if n.timeout_s != 900:
            err("timeout-on-flow",
                f"flow node {n.id!r} sets timeout_s; an in-process child engine has no "
                f"process to kill, so the limit would not limit — the child's own "
                f"budget.max_run_minutes and the root wall clock are what bound it")
        spec_flow = n.spec.get("flow")
        if isinstance(spec_flow, str) and "{" in spec_flow:
            err("dynamic-flow-path",
                f"flow node {n.id!r}: spec.flow {spec_flow!r} interpolates; the path must "
                f"be literal — flow-cycle and flow-depth must stay decidable at verify "
                f"time (spec.args VALUES may interpolate freely)")
    if flow_nodes:
        # flow-in-rollback-cone: the cone is descendants(targets), exactly as
        # the W5b lint computes it. A parent rollback's cascade would re-attach
        # a child whose records say done for tree work the rollback removed.
        children_of: dict[str, list[str]] = {}
        for n in tg.nodes:
            for d in n.depends_on:
                children_of.setdefault(d, []).append(n.id)
        flow_ids = {n.id for n in flow_nodes}
        for g in tg.nodes:
            if g.role != "gate" or g.heal.max_rounds == 0 or not g.heal.rollback:
                continue
            cone: set[str] = set()
            frontier = list(g.heal.targets)
            while frontier:
                nid = frontier.pop()
                for c in children_of.get(nid, []):
                    if c not in cone:
                        cone.add(c)
                        frontier.append(c)
            for fid in sorted(cone & flow_ids):
                err("flow-in-rollback-cone",
                    f"flow node {fid!r} is downstream of gate {g.id!r}'s rollback heal "
                    f"targets; a rollback would restore tree work the child's records "
                    f"still call done — rollback healing and composition do not mix "
                    f"until scope-narrowing exists (r7)")
    if flow_nodes and repo_root is not None:
        for n in flow_nodes:
            spec_flow = n.spec.get("flow")
            if not isinstance(spec_flow, str) or "{" in spec_flow:
                continue  # dynamic-flow-path already fired
            child_path = (Path(repo_root) / spec_flow).resolve()
            key = str(child_path)
            if key in _flow_stack:
                err("flow-cycle",
                    f"flow node {n.id!r}: {spec_flow} is already on the composition "
                    f"path ({' -> '.join(Path(p).name for p in _flow_stack)})")
                continue
            if len(_flow_stack) + 1 > 5:
                err("flow-depth",
                    f"flow node {n.id!r}: composition deeper than 5 at {spec_flow}")
                continue
            if not child_path.is_file():
                err("flow-file-missing", f"flow node {n.id!r}: {spec_flow} does not exist")
                continue
            try:
                child_tg, _ = load_flow(child_path)
            except FlowError as e:
                err("flow-file-invalid", f"flow node {n.id!r}: {e}")
                continue
            # rollback-heal-in-child: a child rollback's restore scope brackets
            # parent-sibling writes on the ONE shared tree.
            for cn in child_tg.nodes:
                if cn.role == "gate" and cn.heal.max_rounds > 0 and cn.heal.rollback:
                    err("rollback-heal-in-child",
                        f"flow node {n.id!r}: child {spec_flow} gate {cn.id!r} heals with "
                        f"rollback: true — a child rollback restores everything since its "
                        f"baseline, including a parent-level sibling's completed writes; "
                        f"use rollback: false (the loop pattern) or run the flow standalone")
            declared = set((n.spec.get("args") or {}))
            required = {k for k, v in child_tg.args.items() if v is None}
            missing = sorted(required - declared)
            if missing:
                err("flow-args-missing",
                    f"flow node {n.id!r}: child {spec_flow} requires arg(s) "
                    f"{', '.join(missing)} that spec.args does not pass")
            # Recursive §6 over the child, its issues prefixed with the child's
            # path; the stack makes cycles terminate before recursion.
            for issue in verify_flow(
                child_tg, registry=registry, config=config, repo_root=repo_root,
                policy=policy, _flow_stack=_flow_stack + (key,),
            ):
                issues.append(VerifyIssue(issue.level, issue.code,
                                          f"[{spec_flow}] {issue.message}"))

    # §8.1: the Policy seam is consulted at verify time too (audit r6.2 nit).
    if policy is not None:
        for n in tg.nodes:
            if n.role == "approval":
                continue
            decision = policy.allows(n, "local-user")
            if not getattr(decision, "allowed", True):
                err("policy-denied", f"policy denies node {n.id!r}: {getattr(decision, 'reason', '')}")

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
            if n.role == "approval":
                continue  # core-handled, no executor, holds nothing (audit r6.2)
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

    # 10. heal targets (lint_flow's advisory pass lives separately: §6 is
    # frozen, and opt-in warnings must not drift into it)
    target_owner: dict[str, str] = {}
    for n in tg.nodes:
        if n.role != "gate":
            continue
        if n.heal.on_exhausted == "pass" and n.heal.rollback:
            # A gate that rolls back and then passes has thrown away the work
            # it just accepted (PROPOSAL-taskflow-parity-tiers 2.1, finding 8).
            err(
                "on-exhausted-with-rollback",
                f"gate {n.id!r} has heal.on_exhausted: \"pass\" with heal.rollback: true — "
                "accepting the best-so-far after rolling it back accepts a tree the work "
                "is no longer in; set rollback: false (build on each round) or drop on_exhausted",
            )
        if n.heal.on_exhausted == "pass" and n.heal.max_rounds == 0:
            # Same posture as target validation at max_rounds == 0 (audit r5):
            # dead config should not hide a wrong belief about what it does.
            err(
                "on-exhausted-without-rounds",
                f"gate {n.id!r} has heal.on_exhausted: \"pass\" but heal.max_rounds is 0 — "
                "rounds can never exhaust, so the key would never apply; a plain block "
                "is what this gate actually is",
            )
        if n.heal.max_rounds > 0 and not n.heal.targets:
            err("heal-targets-required", f"gate {n.id!r} has heal.max_rounds > 0 but empty heal.targets")
            continue
        # Declared targets are validated even at max_rounds == 0 — dead config
        # should not hide a bogus target id (audit r5 finding).
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


# --- advisory lints (`verify --lint`) -------------------------------------------
#
# NOT part of §6: every finding here is a warning, never changes the exit code,
# and encodes a rule with a recorded incident or a shipped convention behind it
# (see docs/proposals/PROPOSAL-factory-programme.md §A2 for each anchor). A lint
# with no incident behind it does not get added.

_TOKEN_KINDS = ("harness", "fake")  # kinds that spend tokens

# `readonly: true` in a persona's YAML frontmatter — the persona's own,
# self-documenting statement that any node wearing it should never mutate.
# Scanned with a line regex rather than a YAML parser: pydantic is the only
# runtime dependency, and the key is a fixed literal, not structured data.
_PERSONA_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
_PERSONA_READONLY_RE = re.compile(r"^readonly\s*:\s*true\s*$", re.MULTILINE)


def persona_declares_readonly(personas_dir: Path, name: str) -> bool:
    """True iff personas/<name>.md exists and its frontmatter says
    `readonly: true`. A missing file is False — verify's `persona-missing`
    ERROR owns that case — and so is an unreadable one (ValueError covers
    UnicodeDecodeError): a lint may stay silent, never crash verify."""
    try:
        text = (personas_dir / f"{name}.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    m = _PERSONA_FM_RE.match(text)
    return bool(m and _PERSONA_READONLY_RE.search(m.group(1)))


def lint_flow(
    tg: TaskGraph, config: Any = None, repo_root: Path | None = None
) -> list[VerifyIssue]:
    """Advisory warnings only. `config` (a LockstepConfig) enables the
    executor-config lints, and `repo_root` enables the persona lints; without
    them those passes are skipped, and the caller should say so — a lint
    silently not run reads as clean."""
    issues: list[VerifyIssue] = []
    warn = lambda code, msg: issues.append(VerifyIssue("warning", code, msg))
    idset = {n.id for n in tg.nodes}
    approvals = {n.id for n in tg.nodes if n.role == "approval"}
    by_contract = {n.id: n.contract for n in tg.nodes}

    # W1 — token-spending work strictly downstream of an approval. Everything
    # after an approval runs inside the human's own resume process (the
    # evidence-approval rule): only seconds-long shell nodes belong there.
    for n in tg.nodes:
        if n.kind in _TOKEN_KINDS and n.role != "approval":
            upstream_approvals = approvals & _ancestors(tg, n.id)
            if upstream_approvals:
                warn(
                    "lint-work-after-approval",
                    f"node {n.id!r} ({n.kind}) runs downstream of approval "
                    f"{sorted(upstream_approvals)}; everything after an approval executes in "
                    "the human's own resume — keep it to seconds-long shell nodes (fine for "
                    "a deliberately ATTENDED flow like sdlc-e2e; the cockpit's detached "
                    "pattern wants the approval last)",
                )

    # V1 (LESSONS-TO-MECHANISMS) — a mutating node with NO declared write
    # scope: driver-side quarantine and the in-harness guard are both OFF for
    # it, and a well-written prompt is then only as safe as the model's
    # judgement under gate pressure. The work-repo audit found the guardrail
    # everyone believed verify provided here did not exist. Lint now; becomes
    # a verify ERROR at format_version 1.1.
    for n in tg.nodes:
        if n.role != "work" or n.kind not in ("harness", "shell"):
            continue
        if n.spec.get("readonly"):
            continue
        if "writes" not in n.spec:
            warn(
                "lint-missing-write-scope",
                f"node {n.id!r} ({n.kind}) can write but declares no spec.writes — scope "
                f"quarantine is OFF for it; declare the paths it may touch ([] for a node "
                f"that writes nothing, [\"**\"] plus spec.writes_rationale for deliberate "
                f"whole-tree access)",
            )
        elif any(str(w).strip() == "**" for w in (n.spec.get("writes") or [])) and not str(
            n.spec.get("writes_rationale") or ""
        ).strip():
            warn(
                "lint-unscoped-writes",
                f"node {n.id!r} declares writes: [\"**\"] (whole tree) without "
                f"spec.writes_rationale — state why unrestricted writes are needed, so a "
                f"reviewer can see the omission of a real scope was deliberate",
            )

    # L1 (LESSONS-TO-MECHANISMS) — a mutating node with no gate or approval on
    # EITHER side of it: nothing downstream can block its result and nothing
    # upstream authorized it, so "use an independent reviewer" is
    # unenforceable prose for it. An upstream approval counts (the
    # evidence-approval pattern: `deliver` after an approval IS the approved
    # act); a downstream gate counts (review closure). Legitimate for an
    # investigatory/demo flow whose final output a human reads directly — say
    # so with the word "ungated" in the flow description and this stays quiet.
    if "ungated" not in (tg.description or "").lower():
        blockers = {n.id for n in tg.nodes if n.role in ("gate", "approval")}
        dependents_of: dict[str, list[str]] = {n.id: [] for n in tg.nodes}
        for n in tg.nodes:
            for d in n.depends_on:
                if d in dependents_of:
                    dependents_of[d].append(n.id)

        def _descendants(nid: str) -> set[str]:
            out: set[str] = set()
            frontier = [nid]
            while frontier:
                for y in dependents_of.get(frontier.pop(), []):
                    if y not in out:
                        out.add(y)
                        frontier.append(y)
            return out

        for n in tg.nodes:
            if n.role != "work" or n.kind not in ("harness", "shell"):
                continue
            if n.spec.get("readonly"):
                continue
            writes = n.spec.get("writes") if "writes" in n.spec else None
            if writes == []:  # declared "writes nothing": not a mutation
                continue
            if not ((_descendants(n.id) | _ancestors(tg, n.id)) & blockers):
                warn(
                    "lint-ungated-mutation",
                    f"node {n.id!r} mutates the tree with no gate or approval upstream or "
                    f"downstream — nothing authorized it and nothing can block it. Add a "
                    f"reviewer + deterministic gate, an approval, or the word 'ungated' to "
                    f"the flow description if a human reads the output directly by design",
                )

    for n in tg.nodes:
        if n.role != "map":
            continue
        # W2 — map over a PathManifest: per-item cache keys are the item
        # STRINGS. Bare paths do not invalidate when file content changes;
        # fingerprint them (the file-audit `path|fingerprint` convention).
        m = _OVER_RE.match(n.over or "")
        # The lint cannot see the manifest's runtime CONTENT, so it asks the
        # only static question available: does this flow know about the
        # convention at all? A map whose item text explains the
        # `path|fingerprint` split is following it — and firing on the
        # reference implementation (`flows/starter/file-audit`) is worse than
        # not firing, because a warning that is wrong on the canonical example
        # is one people learn to skip. Weak signal, deliberately chosen over a
        # false positive on the flow the lint exists to teach.
        knows_convention = "fingerprint" in str(n.spec.get("task", "")).lower()
        if m and by_contract.get(m.group(1)) == "PathManifest" and not knows_convention:
            warn(
                "lint-map-over-manifest",
                f"map node {n.id!r} fans out over PathManifest node {m.group(1)!r}: item "
                "strings are the per-item cache keys, so bare paths will NOT re-run when "
                "file content changes — emit 'path|content-fingerprint' entries",
            )
        # W5 — a parallel map whose items all hold the tree token serializes;
        # the fan-out buys nothing and reads as a hang.
        eff_concurrency = n.concurrency if n.concurrency is not None else tg.concurrency
        if n.kind in _TOKEN_KINDS and not n.spec.get("readonly") and eff_concurrency > 1:
            warn(
                "lint-serialized-map",
                f"map node {n.id!r} has concurrency {eff_concurrency} but its items are not "
                "readonly, so each holds the 'tree' token and they serialize anyway; set "
                "spec.readonly (with a readonly_argv stanza) or concurrency 1",
            )

    # W6 — two healing gates whose ROLLBACK WINDOWS can be open at the same
    # time. A rollback's scope is every path changed since ITS baseline
    # (SPEC §9.4.4), not just the paths its own target wrote — so two gates
    # that snapshot the same tree and then heal concurrently discard each
    # other's output.
    #
    # Recorded twice on flows/demo/webapp-local (2026-08-09 and -08-10). The
    # first time, the backend gate's restore removed the frontend's module
    # three rounds running and the frontend gate blocked on a file the other
    # branch had deleted. The fix moved the collision one gate along rather
    # than removing it, and the second time the run EXITED 0 with half the
    # deliverable missing, because every gate had genuinely passed before a
    # later rollback undid an earlier one's approved work.
    #
    # `heal-target-overlap` does not catch this: the targets are disjoint. It
    # is the baselines that collide. A window opens when a gate's first heal
    # target may start and closes when the gate settles, so two windows are
    # disjoint exactly when one gate is an ancestor of EVERY target of the
    # other — that is what forces it to have finished first.
    healers = [
        n for n in tg.nodes
        if n.role == "gate" and n.heal and n.heal.max_rounds > 0 and n.heal.rollback
    ]
    if len(healers) > 1:
        anc = {n.id: _ancestors(tg, n.id) for n in tg.nodes}

        def finishes_before(gate: Node, other: Node) -> bool:
            targets = [t for t in other.heal.targets if t in idset]
            return bool(targets) and all(gate.id in anc.get(t, set()) for t in targets)

        for i, g1 in enumerate(healers):
            for g2 in healers[i + 1:]:
                if finishes_before(g1, g2) or finishes_before(g2, g1):
                    continue
                warn(
                    "lint-concurrent-heal-rollback",
                    f"healing gates {g1.id!r} and {g2.id!r} both roll back, and neither is "
                    f"forced to finish before the other's targets start — their rollback "
                    f"windows can overlap, and a rollback discards every path changed since "
                    f"its own baseline, not just its target's. Each will undo the other's "
                    f"work; the run can even exit 0 with files a passed gate approved already "
                    f"deleted. Add a dependency edge that serialises the branches, even where "
                    f"no data flows along it",
                )

    # W3 — a map's width is data-dependent at runtime; the spawn budget is the
    # only ceiling, so an explicit one beats the default.
    if any(n.role == "map" for n in tg.nodes) and "budget" not in tg.model_fields_set:
        warn(
            "lint-map-without-budget",
            "flow contains a map node but declares no budget; fan-out width is decided by "
            "runtime data, so set budget.max_agent_spawns explicitly",
        )

    # W4 (config) — argv prompting caps corrective prompts at the platform
    # command-line limit (observed live at 59,028 chars vs Windows' 32,767);
    # prompt_via = "stdin" removes the ceiling entirely.
    if config is not None and getattr(config, "executors", None):
        flagged: set[str] = set()
        for n in tg.nodes:
            if n.kind != "harness" or n.role == "approval":
                continue
            name = n.spec.get("executor") or tg.executor_default or config.default
            stanza = config.executors.get(name) if name else None
            if stanza is not None and stanza.prompt_via == "argv" and name not in flagged:
                flagged.add(name)
                warn(
                    "lint-argv-prompt",
                    f"stanza {name!r} uses prompt_via = \"argv\": corrective re-spawn prompts "
                    "are several times the original and can exceed the platform argv limit "
                    "(ArgvTooLong fails cleanly, but prompt_via = \"stdin\" removes the "
                    "ceiling)",
                )

    # W5 (consumer report 2026-08-13 item 1) — more than one live-tree capture
    # in one flow. `worktree_diff` reports the tree AS IT IS NOW, and shell
    # nodes always re-run on resume (SPEC §0.1.7), so on the first resume after
    # phase 2 has written anything, phase 1's capture re-runs against phase 2's
    # tree; the reviewer's prompt embeds that text, its input hash moves, and a
    # review that had PASSED re-bills and comes back with violations that never
    # existed. Cost when it happened: two full restarts of an hour-plus.
    captures = [
        n.id for n in tg.nodes
        if n.kind == "shell"
        and any("lockstep.probes.worktree_diff" in str(c) for c in (n.spec.get("cmd") or []))
    ]
    if len(captures) > 1:
        warn(
            "lint-live-diff-per-phase",
            f"{len(captures)} nodes capture the LIVE working tree "
            f"({', '.join(sorted(captures))}); a re-run capture describes the tree as it is "
            f"THEN, not as it was for the phase it was meant to evidence — use "
            f"`lockstep.probes.node_diff --node <the step that made the change>`, which reads "
            f"the trees the engine recorded and cannot be moved by a later phase",
        )

    # W5b (PROPOSAL-taskflow-parity-tiers 2.1, finding 13) — a live capture
    # INSIDE a heal loop body: the nodes between a healing gate's targets and
    # the gate itself re-run every round, so from round 2 a `worktree_diff`
    # there describes the cumulative tree — and, on resume, whatever else has
    # moved — never just the round it evidences. ONE capture is enough to be
    # wrong here, so this fires below W5's 2+ threshold. (A capture DOWNSTREAM
    # of the gate runs after the loop settles and stays quiet — implement-heal's
    # single post-loop capture is correct.)
    if captures:
        children: dict[str, list[str]] = {}
        for n in tg.nodes:
            for d in n.depends_on:
                children.setdefault(d, []).append(n.id)
        for g in tg.nodes:
            if g.role != "gate" or g.heal.max_rounds == 0:
                continue
            body: set[str] = set()
            frontier = list(g.heal.targets)
            while frontier:
                nid = frontier.pop()
                for c in children.get(nid, []):
                    if c not in body:
                        body.add(c)
                        frontier.append(c)
            looped = sorted(set(captures) & body & _ancestors(tg, g.id))
            if looped:
                warn(
                    "lint-live-diff-per-phase",
                    f"node(s) {', '.join(looped)} capture the LIVE working tree inside "
                    f"gate {g.id!r}'s heal loop; the loop body re-runs every round, so from "
                    f"round 2 the capture describes the cumulative tree, not the round it "
                    f"evidences — use `lockstep.probes.node_diff --node <heal target>`, "
                    f"which reads the trees the engine recorded for that round's attempt",
                )

    # W6 (consumer report 2026-08-13 item 5) — a `--tools` allowlist covers
    # EXTENSION tools too. A stanza that attaches an extension and then hands
    # readonly nodes a list without `submit_result` silently removes the
    # structured-output tool that extension registers. Nothing fails loudly:
    # the node answers on stdout (SPEC §8.3), which is the designed readonly
    # channel — but the envelope stops being ENFORCED, which is the whole
    # reason the extension is attached (ADDENDUM-A A.3.2).
    if config is not None and getattr(config, "executors", None):
        flagged: set[str] = set()
        for n in tg.nodes:
            if n.kind != "harness":
                continue
            name = n.spec.get("executor") or tg.executor_default or config.default
            stanza = config.executors.get(name) if name else None
            if stanza is None or name in flagged:
                continue
            argv = [str(a) for a in (list(stanza.argv) + list(stanza.readonly_argv or []))]
            if not any(a == "--extension" or a.startswith("--extension=") for a in argv):
                continue
            allow = [argv[i + 1] for i, a in enumerate(argv)
                     if a == "--tools" and i + 1 < len(argv)]
            deny = [argv[i + 1] for i, a in enumerate(argv)
                    if a == "--exclude-tools" and i + 1 < len(argv)]
            drops = (allow and not any("submit_result" in v for v in allow)) or any(
                "submit_result" in v for v in deny
            )
            if drops:
                flagged.add(name)
                warn(
                    "lint-tools-drops-result-channel",
                    f"stanza {name!r} attaches an --extension but its tool list "
                    f"({', '.join(allow + deny) or 'empty'}) does not permit `submit_result`; "
                    f"the allowlist covers extension tools, so the extension's structured-output "
                    f"channel is disabled and nodes fall back to the stdout parse (§8.3) with "
                    f"the envelope unenforced",
                )

    # W7 (consumer report 2026-08-14 item 1) — a node wearing a persona whose
    # own frontmatter declares `readonly: true`, while the node itself says
    # NOTHING about the tree: no spec.readonly, no spec.writes. spec.persona
    # and spec.readonly are independent fields, so "You fix nothing" in the
    # persona coexists silently with the stanza's full write tools — and the
    # node holds the exclusive `tree` token, serializing against every writer.
    # ANY explicit statement stays quiet — the trap is only the node that says
    # nothing: `writes: []` is quarantine-enforced write-nothing (the shape for
    # a reviewer that must run `git diff`, which readonly's tool allowlist
    # would forbid), a real scope is a stated decision to reuse the persona on
    # a node that writes its report, and an explicit `readonly: false` is a
    # stated override. Approvals never spawn an executor, so they are exempt
    # like W1/W4.
    if repo_root is not None:
        personas_dir = repo_root / "personas"
        for n in tg.nodes:
            if (
                n.kind != "harness"
                or n.role == "approval"
                or "readonly" in n.spec
                or "writes" in n.spec
            ):
                continue
            persona = n.spec.get("persona")
            if persona and persona_declares_readonly(personas_dir, str(persona)):
                warn(
                    "lint-persona-not-readonly",
                    f"node {n.id!r} names persona {persona!r}, whose frontmatter declares "
                    f"readonly: true, but sets neither spec.readonly nor spec.writes — the "
                    f"persona's restriction is prose only: the node keeps the stanza's full "
                    f"write tools and holds the exclusive `tree` token. Set spec.readonly: "
                    f"true (verify then enforces it via readonly_argv, and the node fans out "
                    f"in parallel), or declare spec.writes if writing is intended",
                )

    # W9 (parity 3.1) — a reads glob that matches a big slice of the tree is
    # hashed at EVERY plan, including the resume revalidation of every done
    # node: exactly the 13→32-minute creep shape (lesson 20). The stat-keyed
    # memo caps repeat cost within a process, but the first pass still reads
    # every byte, and every resume is a first pass.
    if repo_root is not None:
        from .reads import BROAD_READS_THRESHOLD, matched_files

        for n in tg.nodes:
            reads = n.spec.get("reads")
            if not isinstance(reads, list) or not reads:
                continue
            literal = [r for r in reads if isinstance(r, str) and "{" not in r]
            count = len(matched_files(
                Path(repo_root), literal, exclude_roots=(Path(repo_root) / "runs",)))
            if count > BROAD_READS_THRESHOLD:
                warn(
                    "lint-broad-reads",
                    f"node {n.id!r}: spec.reads matches {count} files "
                    f"(> {BROAD_READS_THRESHOLD}); every plan hashes them — including the "
                    f"revalidation of every done node on resume — narrow the globs, or "
                    f"watch the journal's reads-hash timing lines grow with the tree",
                )

    # W10 (PROPOSAL-flow-composition §5) — an approval buried in a composed
    # child is an approval the operator did not see coming: the TTY prompt
    # appears mid-run, one level removed from what was launched. Direct
    # children only; deeper levels get their own warning when the child is
    # itself verified.
    if repo_root is not None:
        for n in tg.nodes:
            if n.kind != "flow":
                continue
            spec_flow = n.spec.get("flow")
            if not isinstance(spec_flow, str) or "{" in spec_flow:
                continue
            p = Path(repo_root) / spec_flow
            if not p.is_file():
                continue
            try:
                child_tg, _ = load_flow(p)
            except FlowError:
                continue
            approvals = [cn.id for cn in child_tg.nodes if cn.role == "approval"]
            if approvals:
                warn(
                    "lint-approval-in-child",
                    f"flow node {n.id!r}: child {spec_flow} contains approval node(s) "
                    f"{', '.join(approvals)} — the prompt appears mid-run on the parent's "
                    f"TTY, one level removed from what the operator launched; run attended, "
                    f"or lift the approval to the top level",
                )

    # W8 (PROPOSAL-taskflow-parity-tiers 2.1, finding 8) — heal.on_exhausted:
    # "pass" converts a blocking gate into a passing one after its repair
    # rounds run out, and gates exist to stop bad work. It is legal by design
    # (a refinement loop accepts the best-so-far), but every gate that can
    # wave work through gets named so a reviewer of the flow sees the full
    # list in one pass.
    for n in tg.nodes:
        if n.role == "gate" and n.heal.on_exhausted == "pass":
            warn(
                "lint-on-exhausted-pass",
                f"gate {n.id!r} accepts the best-so-far when its {n.heal.max_rounds} heal "
                f"round(s) exhaust (heal.on_exhausted: \"pass\"); the recorded verdict will "
                f"name what happened, but nothing will block — confirm this gate is a "
                f"refinement loop, not a quality bar",
            )

    return issues
