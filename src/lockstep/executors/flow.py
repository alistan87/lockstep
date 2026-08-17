"""Run a saved flow as ONE node — `kind: "flow"` (PROPOSAL-flow-composition,
adopted 2026-08-14).

The child is a REAL run in a deterministic, hash-named dir
(`<parent_run>/children/<node_id>-<input_hash[:12]>/`): `status`, `explain`,
`steer` and `verify-trace` descend with zero new plumbing, `gc`/`estimate`/
`active` never see it (single-level scans — never collected apart from its
parent), and resume-mid-child IS child resume: a re-executed flow node whose
parent hash is unchanged attaches to the existing child dir and the child's
own revalidation hash-skips completed work. A moved parent hash names a
different dir, so a fresh child lineage starts beside the old evidence.

Concurrency, budget and locks are RUN-scoped, not engine-scoped: the child
engine receives `RunResources.for_child()` — shared exclusive tokens (a child
tree-writer serializes against a parent-level writer), shared worker
semaphore (`--max-workers` bounds the whole tree of engines), and the ROOT
wallet (`BudgetTripped` from a child is a run-level stop, exit 4, never a
node failure). The flow node itself holds no token and no worker slot; it
parks on a dedicated thread (see `_costs_tokens_hint` — flow is neither
harness nor fake, and the engine gives non-token work its own dispatch).

Cancel is cooperative: `cancel` writes the parent phase dir's CANCELLED
marker (marker-only for flow nodes — there is no pid to kill); a watcher
thread here translates marker-or-parent-abort into the CHILD's abort, which
its wave loop checks between waves. The engine's existing marker handling
then records the parent node as cancelled, no retries (r6 C3).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..interpolate import render_template
from ..protocols import PlannedWork, RawResult, RenderCtx
from ..state import (
    LockHeld,
    PhaseRecord,
    RunState,
    acquire_lock,
    compose_hash,
    load_state,
    release_lock,
    utcnow,
    write_state,
)
from ..taskgraph import Node, load_flow
from .shell import resolve_ctx_of

MAX_DEPTH = 5  # verify enforces (flow-depth); this is the runtime backstop


class FlowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # LITERAL repo-root-relative path — no interpolation (§6 dynamic-flow-path):
    # cycle and depth checks must stay decidable at verify time.
    flow: str
    # Child args. VALUES may interpolate ({args.NAME}, {steps...}) — data, not
    # structure; every rendered value folds into the parent node's hash.
    args: dict[str, str] = {}


class FlowExecutor:
    kind = "flow"
    cacheable = True
    supports_corrective_respawn = False  # the child's result is what it is;
    # a contract mismatch means the CHILD flow's final node is wrong, and a
    # re-spawn would just re-run the same deterministic mapping.
    auto_retry = False  # M4's free retry is for spawns that produced nothing;
    # a result-less flow failure is a child that genuinely blocked or failed,
    # and re-entering it would convert a child gate block into a retry.
    SpecModel = FlowSpec

    def __init__(self, config, repo_root: Path, make_registry):
        self.config = config
        self.repo_root = Path(repo_root)
        # A zero-arg factory for CHILD registries: the CLI passes its own
        # registry builder (so children resolve every kind the parent can);
        # tests inject a factory that shares one observable FakeExecutor.
        self.make_registry = make_registry
        self.resources = None  # bound by the engine (bind_run)

    def bind_run(self, resources) -> None:
        self.resources = resources

    # ------------------------------------------------------------------ plan

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork:
        spec = FlowSpec.model_validate(node.spec)
        flow_path = self.repo_root / spec.flow
        child_tg, child_hash = load_flow(flow_path)  # FlowError -> node fails; verify names it first
        rctx = resolve_ctx_of(ctx)
        rendered_args: dict[str, str] = {}
        for key in sorted(spec.args):
            rendered = render_template(
                str(spec.args[key]), rctx, fence=False,
                max_interp_chars=ctx.max_interp_chars, spill_dir=None,
                null_for_skipped=ctx.allow_null_for_skipped,
            )
            rendered_args[key] = rendered.prompt_text
        return PlannedWork(
            render=f"flow:{spec.flow}",
            # Fingerprint: the child flow's own file hash (editing the child
            # re-bills the parent node and everything downstream — said in
            # FLOW-AUTHORING because it is surprising), the rendered args,
            # and the config digest (a child's behaviour is its stanzas').
            fingerprint_parts=[
                f"flow:{child_hash}",
                f"args:{json.dumps(rendered_args, sort_keys=True, ensure_ascii=False)}",
                f"config:{ctx.config_digest}",
            ],
            costs_tokens=False,  # the flow node spawns nothing; its children
            # debit the root wallet directly
            exclusive=[],  # finding 4: a parked parent holding `tree` while
            # its child queues for the same lock is the deadlock
            meta={
                # role/kind/contract stashed so execute() can recompute the
                # input hash for the child-dir name — ReplayExecutor.plan()'s
                # exact pattern, same reason (execute never sees the node).
                "node_id": node.id, "role": node.role, "kind": node.kind,
                "contract": node.contract, "output": node.output,
                "flow_path": str(flow_path), "child_flow_hash": child_hash,
                "child_args": rendered_args,
                "hash_detail": {},
            },
        )

    # --------------------------------------------------------------- execute

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        # Engine internals imported here: executors.flow -> roles at module
        # level would couple import order for every executor user.
        from ..policy import AllowAllPolicy
        from ..roles import BudgetTripped, Engine
        from ..store import FileStore
        from .. import EXIT_BUDGET

        meta = work.meta

        def refuse(message: str) -> RawResult:
            return RawResult(exit_code=7, result_text=None, source="none", error=message)

        if self.resources is None:
            return refuse("flow executor was never bound to a run (bind_run) — driver bug")
        if self.resources.depth + 1 > MAX_DEPTH:
            return refuse(f"composition depth cap ({MAX_DEPTH}) exceeded at {meta['node_id']}")

        input_hash = compose_hash(
            meta["role"], meta["kind"], meta["contract"], work.fingerprint_parts)
        run_dir = Path(phase_dir).parents[1]
        child_dir = run_dir / "children" / f"{meta['node_id']}-{input_hash[:12]}"
        flow_path = Path(meta["flow_path"])
        child_tg, child_hash = load_flow(flow_path)
        if child_hash != meta["child_flow_hash"]:
            return refuse(
                f"child flow {flow_path} changed between plan and execute — re-run "
                f"(the new content will hash a new child lineage)")

        root = self.resources.root_engine
        attaching = (child_dir / "state.json").exists()
        if attaching:
            state = load_state(child_dir)
            if state.flow_hash != child_hash:  # unreachable by construction; loud beats subtle
                return refuse(f"child dir {child_dir} carries a different flow_hash — refusing")
        else:
            required = {k for k, v in child_tg.args.items() if v is None}
            missing = sorted(required - set(meta["child_args"]))
            if missing:
                return refuse(
                    f"child flow {flow_path} requires arg(s) this node does not pass: "
                    f"{', '.join(missing)}")
            args = {k: v for k, v in child_tg.args.items() if v is not None}
            args.update(meta["child_args"])
            state = RunState(
                flow_name=child_tg.name,
                flow_hash=child_hash,
                format_version=child_tg.format_version,
                args=args,
                nodes={n.id: PhaseRecord(node_id=n.id, role=n.role, kind=n.kind)
                       for n in child_tg.nodes},
                started_at=utcnow(),
                workspace_kind=root.store.state.workspace_kind if root else "null",
                # Batch 1: a child runs against its parent's tree, and can be
                # resumed directly — the guardrail should cover it too.
                repo_root=str(root.repo_root) if root else "",
            )
            child_dir.mkdir(parents=True, exist_ok=True)
            write_state(child_dir, state)
            (child_dir / "flow.tg.json").write_bytes(flow_path.read_bytes())

        try:
            acquire_lock(child_dir)
        except LockHeld as e:
            return refuse(
                f"child run dir {child_dir} is locked by {e.holder} — someone resumed "
                f"the child directly; let it finish or free the lock")

        child_res = self.resources.for_child()
        marker = Path(phase_dir) / "CANCELLED"
        parent_abort = self.resources.abort
        if marker.exists() or parent_abort.is_set():
            # Cancelled before the child ever started: do not start it.
            release_lock(child_dir)
            return RawResult(exit_code=1, result_text=None, source="none", error="cancelled")
        stop = threading.Event()

        def watch() -> None:
            # Chains cancellation down the tree, one watcher per level: the
            # marker is this node's own cancel; the parent abort is a cancel
            # somewhere above.
            while not stop.is_set():
                if marker.exists() or parent_abort.is_set():
                    child_res.abort.set()
                    return
                time.sleep(0.25)

        watcher = threading.Thread(target=watch, daemon=True, name=f"flow-watch-{meta['node_id']}")
        watcher.start()
        node_id = meta["node_id"]
        try:
            engine = Engine(
                tg=child_tg,
                registry=self.make_registry(),
                config=self.config,
                workspace=root.workspace if root else None,
                store=FileStore(child_dir, state),
                policy=AllowAllPolicy(),
                repo_root=self.repo_root,
                max_workers=child_res.max_workers,
                log=lambda *a, **k: print(f"[{node_id}]", *a, **k),
                resources=child_res,
            )
            if attaching:
                engine.prepare_resume()
            exit_code = engine.run()
        finally:
            stop.set()
            release_lock(child_dir)

        if child_res.abort.is_set():
            # The engine's own marker handling records the parent node as
            # cancelled (no retries, r6 C3); this result is what it inspects.
            return RawResult(exit_code=1, result_text=None, source="none", error="cancelled")
        if exit_code == EXIT_BUDGET:
            # One wallet: a child trip is a RUN-level stop, never a node
            # failure. _run_node_safe re-pends this node and flags the loop.
            raise BudgetTripped()
        if exit_code == 0:
            final_id = child_tg.final_node_id
            text = FileStore(child_dir, load_state(child_dir)).result_of(final_id)
            if text is None:
                return refuse(
                    f"child flow completed but its final node {final_id!r} left no "
                    f"result — inspect {child_dir}")
            fname = "result.json" if meta["output"] == "json" else "result.txt"
            (Path(phase_dir) / fname).write_text(text, encoding="utf-8")
            return RawResult(exit_code=0, result_text=text, source="file")
        meanings = {2: "child gate blocked", 3: "child node failed",
                    6: "child approval rejected", 7: "child run refused"}
        st = load_state(child_dir)
        bad = ", ".join(sorted(
            f"{n} ({r.status}: {r.error})" if r.error else f"{n} ({r.status})"
            for n, r in st.nodes.items() if r.status in ("failed", "blocked")))
        return RawResult(
            exit_code=exit_code, result_text=None, source="none",
            error=f"{meanings.get(exit_code, f'child exit {exit_code}')}: "
                  f"{bad or 'see child state'} — inspect {child_dir} "
                  f"(a parent resume re-enters the child and re-runs what blocked)")
