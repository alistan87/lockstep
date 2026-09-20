"""work / gate / approval / map orchestration — the engine (SPEC §9).

Core, never pluggable: topological ordering, hash composition, skip propagation,
gate adjudication, retry/heal orchestration, budget accounting (SPEC §8.1).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from pathlib import Path

from . import (
    EXIT_APPROVAL_REJECTED,
    EXIT_BUDGET,
    EXIT_GATE_BLOCK,
    EXIT_NODE_FAILED,
    EXIT_OK,
)
from .contracts import ContractError, Verdict, resolve_contract, validate_result
from .interpolate import (
    render_scope,
    InterpolationError,
    ResolveCtx,
    SkippedReference,
    compact_json,
    eval_when,
    extract_refs,
    fence_block,
)
from .policy import ACTOR_LOCAL_USER
from .protocols import PlannedWork, RawResult, RenderCtx, SnapshotRef
from .registry import LockstepConfig, Registry
from .repair import longest_near_object, repair_json
from .state import (
    ItemRecord,
    append_event,
    compose_hash,
    diff_labels,
    emit_span,
    label_parts,
    mark_mailbox_consumed,
    read_mailbox,
    render_steering,
    utcnow,
)
from .store import FileStore
from .taskgraph import Node, RetrySpec, TaskGraph
from .workspace import GitWorkspace, WorkspaceError, path_in_scope

SETTLED = {"done", "skipped"}
TERMINAL_BAD = {"failed", "blocked"}


class BudgetTripped(Exception):
    pass


class RunResources:
    """Everything that is RUN-scoped rather than engine-scoped
    (PROPOSAL-flow-composition §2). A single engine builds one for itself and
    behaves byte-identically to before this existed; a composed child engine
    receives its parent's, so exclusive tokens, the worker cap, and the spawn
    wallet are shared across the whole tree of engines — the alternative is
    the silent failure the composition review named: a child tree-writer
    running beside a parent-level `tree` holder.

    - `locks`/`locks_guard`: the exclusive-token registry (`tree` included).
    - `worker_slots`: a BoundedSemaphore(max_workers) acquired INSIDE each
      token-costing worker thread (never in the dispatch loop — that would
      serialize wave dispatch). For a single engine the token pool has
      exactly as many threads as there are slots, so it never blocks.
    - `snapshot_guard`: serializes whole-tree snapshot work across engines
      for the same reason it is serialized within one.
    - `root_engine`: the wallet owner. A child's `_spend_spawn` routes here,
      so `token_spawns`, `max_agent_spawns` and the root wall clock are ONE
      budget however deep the composition goes.
    - `abort`: cooperative stop for THIS engine's wave loop. Per-engine by
      construction (a child gets a fresh one chained by its watcher), because
      one shared event would make cancelling one flow node cancel the world.
    - `depth`: composition depth, capped by verify (`flow-depth`).
    """

    def __init__(self, max_workers: int):
        self.locks: dict[str, threading.Lock] = {}
        self.locks_guard = threading.Lock()
        self.worker_slots = threading.BoundedSemaphore(max_workers)
        self.snapshot_guard = threading.Lock()
        self.max_workers = max_workers
        self.root_engine = None  # set by the engine that creates the wallet
        self.abort = threading.Event()
        self.depth = 0

    def for_child(self) -> "RunResources":
        """Same run, one level down: shared locks, slots, guard and wallet;
        a FRESH abort (the parent's watcher chains into it); depth + 1."""
        child = RunResources.__new__(RunResources)
        child.locks = self.locks
        child.locks_guard = self.locks_guard
        child.worker_slots = self.worker_slots
        child.snapshot_guard = self.snapshot_guard
        child.max_workers = self.max_workers
        child.root_engine = self.root_engine
        child.abort = threading.Event()
        child.depth = self.depth + 1
        return child


class RunRefusal(Exception):
    """Run-time refusal (exit 7), e.g. heal.rollback on a non-git tree.

    `reason` is the machine-matchable category the S6 terminal record carries
    (`record_terminal`); the message stays the human evidence. Default is the
    generic "refused" — only refusals a reader dispatches on need a name."""

    def __init__(self, message: str, *, reason: str = "refused"):
        super().__init__(message)
        self.reason = reason


class _ProgressTailer:
    """r6 C1: follows every phase directory's progress.jsonl (and map items')
    by byte offset, appending complete parseable lines to events.jsonl as
    kind="progress". Advisory ONLY — never touches scheduling, hashing,
    gating, budgets, or retries; unparseable lines are skipped silently."""

    def __init__(self, run_dir: Path, cadence_s: float = 1.0):
        self.run_dir = Path(run_dir)
        self.cadence_s = cadence_s
        self._offsets: dict[Path, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="lockstep-progress", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sweep()  # final drain: catch lines the cadence missed

    def _loop(self) -> None:
        while not self._stop.wait(self.cadence_s):
            try:
                self._sweep()
            except Exception:
                pass  # advisory: a tailer hiccup must never disturb the run

    def _iter_files(self):
        phases = self.run_dir / "phases"
        if not phases.exists():
            return
        for node_dir in phases.iterdir():
            p = node_dir / "progress.jsonl"
            if p.exists():
                yield node_dir.name, None, p
            items = node_dir / "items"
            if items.exists():
                for item_dir in items.iterdir():
                    ip = item_dir / "progress.jsonl"
                    if ip.exists():
                        yield node_dir.name, item_dir.name, ip

    def _sweep(self) -> None:
        from .contracts import ProgressEvent

        for node_id, item, path in self._iter_files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            offset = self._offsets.get(path, 0)
            if size <= offset:
                continue
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read(size - offset)
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                continue  # partial line: wait for the newline
            self._offsets[path] = offset + last_nl + 1
            for raw_line in chunk[: last_nl + 1].splitlines():
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = ProgressEvent.model_validate_json(line)
                except Exception:
                    continue  # advisory: skip, never error
                event = {"kind": "progress", "node": node_id, "step": ev.step, "pct": ev.pct, "note": ev.note}
                if item is not None:
                    event["item"] = item
                append_event(self.run_dir, event)


class _GateOutcome:
    def __init__(self, node: Node, verdict: Verdict | None, reason: str):
        self.node = node
        self.verdict = verdict  # None ⇒ no valid verdict emitted (terminal, A4/§9.4.3)
        self.reason = reason


class Engine:
    def __init__(
        self,
        *,
        tg: TaskGraph,
        registry: Registry,
        config: LockstepConfig,
        workspace,
        store: FileStore,
        policy,
        repo_root: Path,
        max_workers: int = 2,
        log=print,
        cockpit: bool = False,
        check_dirty_scope: bool = False,
        resources: RunResources | None = None,
    ):
        self.tg = tg
        self.registry = registry
        self.config = config
        self.workspace = workspace
        self.store = store
        self.policy = policy
        self.repo_root = Path(repo_root)
        self.max_workers = max_workers
        self.log = log
        # Cockpit mode (proposal T1.3). Off by default and SPEC §9.3 behaviour is
        # byte-identical without it; see DEVIATIONS.
        self.cockpit = cockpit
        # E9: refuse a FRESH run whose pre-run dirty paths overlap a declared
        # write scope — an in-scope write legally overwrites the operator's
        # uncommitted edit. CLI sets this for real fresh runs only (a resumed
        # tree is EXPECTED dirty with the run's own prior work).
        self.check_dirty_scope = check_dirty_scope
        # Set by the CLI under --replay: baseline-gate bodies must NOT run
        # there — the replay wrapper would serve the gate's single per-node
        # recording (the post-run ADJUDICATED verdict) as the "pre-run"
        # baseline (adversarial-review finding 5). The recorded gate results
        # are already adjudicated, so replay needs no baseline machinery at
        # all: skipping preserves the recording's own verdicts exactly.
        self.replaying = False

        # RUN-scoped state lives in RunResources (PROPOSAL-flow-composition
        # §2): a root engine creates its own and owns the wallet; a composed
        # child receives its parent's. The aliases keep every existing call
        # site byte-identical.
        if resources is None:
            resources = RunResources(max_workers)
            resources.root_engine = self
        self.resources = resources
        self._locks = resources.locks
        self._locks_guard = resources.locks_guard
        self._budget_guard = threading.Lock()
        self._snapshot_guard = resources.snapshot_guard
        self.needs_check: set[str] = set()
        self._gate_outcomes: list[_GateOutcome] = []
        # S3: nodes this DRIVE has already journalled an attempt for. A
        # `resume` means "a previous drive left attempts behind", not "a spawn
        # already happened" - and a baseline gate spends a real spawn and bumps
        # `attempts` before its first ordinary attempt, which made a FRESH run
        # report a resume that never occurred.
        self._attempted: set[str] = set()
        # S3's heal signal lives in RunState (`heal_pending`), not here: see
        # `_take_heal_round`. `heal_round` is recorded on the GATE, not on the
        # nodes a cascade re-pends, so without a carrier a healed writer's next
        # attempt is indistinguishable from an ordinary resume.
        self._outcomes_guard = threading.Lock()
        self.flags = {"budget": False, "gate_block": False, "approval_rejected": False}
        self._start_monotonic = 0.0

        # Heal plumbing: gate -> proactive baseline snapshot; target -> its gate.
        # §6.10 guarantees targets never overlap across gates.
        self.snapshots: dict[str, SnapshotRef | None] = {}
        self.target_gate: dict[str, str] = {}
        for n in tg.nodes:
            if n.role == "gate" and n.heal.max_rounds > 0:
                self.snapshots[n.id] = None
                for t in n.heal.targets:
                    self.target_gate[t] = n.id
        # Reload persisted baselines: a resumed process must restore to the
        # SAME pre-attempt tree the original session snapshotted (§9.4.2).
        for gate_id, ref in store.state.heal_baselines.items():
            if gate_id in self.snapshots:
                self.snapshots[gate_id] = SnapshotRef(ref=ref)

        self._dependents: dict[str, list[str]] = {n.id: [] for n in tg.nodes}
        for n in tg.nodes:
            for dep in n.depends_on:
                self._dependents[dep].append(n.id)

        # PROPOSAL-flow-composition §2: an executor MAY define
        # `bind_run(resources)`; it is called once, here, for every registered
        # executor that does. The seed/replay wrappers delegate it explicitly
        # (they forward NOTHING dynamically — composition review finding 1).
        for kind in registry.kinds():
            ex = registry.get(kind)
            bind = getattr(ex, "bind_run", None)
            if callable(bind):
                bind(self.resources)

    # ------------------------------------------------------------------ helpers

    def _rec(self, node_id: str):
        return self.store.state.nodes[node_id]

    def _set_status(self, node_id: str, status: str, *, error: str | None = None) -> None:
        rec = self._rec(node_id)
        rec.status = status  # type: ignore[assignment]
        if error is not None:
            rec.error = error
        if status == "running":
            rec.started_at = rec.started_at or utcnow()
        if status in ("done", "failed", "skipped", "blocked"):
            rec.ended_at = utcnow()
        self.store.record(rec)
        append_event(self.store.run_dir, {"node": node_id, "status": status, "error": error})
        if status in ("done", "failed", "skipped", "blocked"):
            emit_span(rec)  # advisory; a no-op unless spans are configured

    def _token_lock(self, token: str) -> threading.Lock:
        with self._locks_guard:
            if token not in self._locks:
                self._locks[token] = threading.Lock()
            return self._locks[token]

    def _acquire(self, tokens: list[str]) -> list[threading.Lock]:
        # Sorted acquisition avoids deadlock (SPEC §9.1).
        locks = [self._token_lock(t) for t in sorted(set(tokens))]
        for lk in locks:
            lk.acquire()
        return locks

    @staticmethod
    def _release(locks: list[threading.Lock]) -> None:
        for lk in reversed(locks):
            lk.release()

    def _wall_exceeded(self) -> bool:
        return (time.monotonic() - self._start_monotonic) > self.tg.budget.max_run_minutes * 60

    def _spend_spawn(self, work: PlannedWork) -> None:
        """Budget accounting (SPEC §9.5): counts every spawn whose work costs
        tokens — corrective re-spawns and heal rounds included. A composed
        child engine routes here to the ROOT's wallet (one budget for the
        whole tree of engines), flagging its own loop on a trip so it stops
        dispatching instead of re-pending the same nodes forever."""
        if not work.costs_tokens:
            return
        root = self.resources.root_engine
        if root is not None and root is not self:
            try:
                root._spend_spawn(work)
            except BudgetTripped:
                self.flags["budget"] = True
                raise
            return
        with self._budget_guard:
            if self.flags["budget"] or self.store.state.token_spawns >= self.tg.budget.max_agent_spawns:
                self.flags["budget"] = True
                raise BudgetTripped()
            if self._wall_exceeded():
                self.flags["budget"] = True
                raise BudgetTripped()
            self.store.mutate(lambda st: setattr(st, "token_spawns", st.token_spawns + 1))

    def _costs_tokens_hint(self, node: Node) -> bool:
        if node.kind == "harness":
            return True
        if node.kind == "fake":
            return bool(node.spec.get("costs_tokens", True))
        return False

    def _resolve_ctx(self, node: Node, *, item=None, has_item=False) -> ResolveCtx:
        outputs: dict[str, str | None] = {}
        json_results: dict[str, object] = {}
        skipped: set[str] = set()
        for dep in node.depends_on:
            rec = self._rec(dep)
            if rec.status == "skipped":
                skipped.add(dep)
                continue
            text = self.store.result_of(dep)
            outputs[dep] = text
            # {steps.X.json} parses the result regardless of X's declared output
            # (SPEC §7 doesn't gate it); a map's collected result is always JSON.
            if text is not None:
                try:
                    json_results[dep] = json.loads(text)
                except json.JSONDecodeError:
                    pass
        return ResolveCtx(
            args=self.store.state.args,
            outputs=outputs,
            json_results=json_results,
            skipped=skipped,
            deps=list(node.depends_on),
            item=item,
            has_item=has_item,
            item_var=node.item_var,
        )

    def _render_ctx(self, node: Node, phase_dir: Path, *, item=None, has_item=False) -> RenderCtx:
        r = self._resolve_ctx(node, item=item, has_item=has_item)
        return RenderCtx(
            args=r.args,
            outputs=r.outputs,
            json_results=r.json_results,
            skipped=r.skipped,
            deps=r.deps,
            item=item,
            has_item=has_item,
            item_var=node.item_var,
            allow_null_for_skipped=node.optional,
            repo_root=self.repo_root,
            personas_dir=self.repo_root / "personas",
            phase_dir=phase_dir,
            max_interp_chars=self.tg.max_interp_chars,
            config_digest=self.config.digest,
            executor_default=self.tg.executor_default or self.config.default,
            # From RunState, never process-local: a resume must re-plan the same
            # prompt the healed spawn saw, or its hash changes and it re-runs.
            heal_text=self.store.state.heal_texts.get(node.id, ""),
            # r6 C2: the WHOLE mailbox renders (consumed + new) so the hash is
            # reproducible on resume; a new message grows the block and
            # correctly invalidates. Re-built per plan, so map items at
            # concurrency 1 re-read the mailbox between items.
            steer_text=render_steering(read_mailbox(self.store.run_dir, node.id)),
            contracts_module=self.tg.contracts_module,
            runs_root=self.store.run_dir.parent,
        )

    def _body_referenced_deps(self, node: Node) -> set[str]:
        """Node ids referenced by the node's body templates (not `when` — A2)."""
        refs: set[str] = set()
        templates: list[str] = []
        task = node.spec.get("task")
        if isinstance(task, str):
            templates.append(task)
        cmd = node.spec.get("cmd")
        if isinstance(cmd, list):
            templates += [c for c in cmd if isinstance(c, str)]
        if node.over:
            templates.append(node.over)
        for t in templates:
            for ref in extract_refs(t):
                parts = ref.split(".")
                if parts[0] == "steps" and len(parts) >= 2:
                    refs.add(parts[1])
                elif parts[0] == "previous" and len(node.depends_on) == 1:
                    refs.add(node.depends_on[0])
        return refs

    # ------------------------------------------------------------------ resume

    def prepare_resume(self) -> None:
        """SPEC §9.2: re-run failed, stale-running, pending; approvals never
        resume-skipped; done nodes get hash revalidation; lineage-head
        fingerprint comparison detects EXTERNAL edits."""
        st = self.store.state
        # S3: a heal signal is consumed by the attempt it describes - but a
        # node the cascade re-pended can end the drive without ever attempting
        # (skipped by `when`, edited out of the flow). Its entry would then
        # outlive the heal cycle and mislabel some later, unrelated attempt as
        # rework.
        #
        # BEFORE the loop below, deliberately: that loop normalises
        # failed/running/blocked/SKIPPED to `pending`, so reading afterwards
        # sees a skipped node as pending and the one genuinely reachable case
        # slips past. Settled here means settled as the previous drive left it.
        for nid in [n for n, _ in (st.heal_pending or {}).items()
                    if n not in st.nodes or st.nodes[n].status in SETTLED]:
            st.heal_pending.pop(nid, None)
        for node in self.tg.nodes:
            rec = st.nodes[node.id]
            if rec.status in ("running", "failed", "blocked"):
                rec.status = "pending"
                rec.error = None
            elif rec.status == "done":
                if node.role == "approval":
                    rec.status = "pending"  # never skipped (SPEC §9.3)
                elif any(
                    not m.get("consumed")
                    for m in read_mailbox(self.store.run_dir, node.id)
                ):
                    rec.status = "pending"  # steered done node re-runs (r6 C2)
                    rec.invalidated_by = ["unconsumed steering message (r6 C2)"]
                    # A steer to an adopted node is the operator asking it to
                    # re-run — an explicit act that supersedes the pin. The pin
                    # must not survive it: it describes the HUMAN's bytes, and
                    # after the re-spawn it would pin model output as
                    # settled-by-adoption, a lie the record cannot carry.
                    self._dissolve_adoption(rec, "steering message")
                else:
                    self.needs_check.add(node.id)
            elif rec.status == "skipped":
                rec.status = "pending"  # `when` re-evaluates against (possibly re-run) upstreams
            for irec in rec.items.values():
                if irec.status in ("running", "failed"):
                    irec.status = "pending"
                    irec.error = None
        # Lineage-head comparison (SPEC §9.2, M6/M7). Only the most recently
        # completed node's fingerprint is compared — every completed node
        # legitimately left a different tree than its predecessors recorded.
        if st.workspace_kind == "git" and st.fingerprint_detail:
            _, current = self.workspace.fingerprint_detail()
            stored = st.fingerprint_detail
            changed = sorted(
                set(k for k in stored if stored.get(k) != current.get(k))
                | set(k for k in current if k not in stored)
            )
            if changed:
                self.log(
                    "WARNING: the working tree changed OUTSIDE lockstep since the last "
                    "completed node (external edits). Changed paths:\n  "
                    + "\n  ".join(changed)
                )
                # Re-run harness nodes not yet consumed downstream, then proceed.
                for node in self.tg.nodes:
                    rec = st.nodes[node.id]
                    ex = self.registry.get(node.kind)
                    if (
                        rec.status == "done"
                        # D3 (DESIGN-NOTE-adopt): an adopted node is settled;
                        # this sweep must not re-pend it over external edits.
                        # Belt to adopt's braces — the command refreshes
                        # fingerprint_detail for the adopted paths, so its own
                        # adoption never registers here; this guard is for an
                        # UNRELATED edit in the same window, which should warn
                        # (above) and re-pend everything except the pin.
                        and rec.adopted is None
                        and ex is not None
                        and getattr(ex, "cacheable", False)
                        # "Not yet consumed downstream" (§9.2): a LEAF node has
                        # no consumers at all, so it always re-runs — it is the
                        # flow's user-visible artifact (audit r6 major).
                        and (
                            not self._dependents[node.id]
                            or any(st.nodes[d].status != "done" for d in self._dependents[node.id])
                        )
                    ):
                        rec.status = "pending"
                        rec.invalidated_by = [
                            "external edits to the working tree (lineage-head fingerprint)"
                        ]
                        self.needs_check.discard(node.id)
        self.store.mutate(lambda s: None)  # persist

    # ------------------------------------------------------------------ settle

    def _dep_settled(self, dep_id: str) -> bool:
        """A dependency licenses downstream work only when it is settled AND
        any pending resume-time hash revalidation has actually happened.
        `done` alone is not enough: on resume a node stays `done` in
        `needs_check` while its own upstream is still re-running, and treating
        that as settled dispatched dependents against the previous attempt's
        cached output (LESSONS-TO-MECHANISMS B1; the work-repo run needed three
        resume cycles before a reviewer ever saw fresh evidence)."""
        return self._rec(dep_id).status in SETTLED and dep_id not in self.needs_check

    def _settle(self) -> bool:
        """Resolve skips, upstream-failure blocks, and cache revalidation until
        a fixed point. Returns True if anything changed."""
        progressed = False
        changed = True
        while changed:
            changed = False
            for node in self.tg.nodes:
                rec = self._rec(node.id)
                dep_recs = {d: self._rec(d) for d in node.depends_on}
                if rec.status == "pending":
                    if any(r.status in TERMINAL_BAD for r in dep_recs.values()):
                        self._set_status(node.id, "blocked", error="upstream failed or blocked")
                        changed = progressed = True
                        continue
                    if not all(self._dep_settled(d) for d in node.depends_on):
                        continue
                    skipped_deps = {d for d, r in dep_recs.items() if r.status == "skipped"}
                    # A2: `when` evaluates FIRST and is exempt from transitive skip.
                    if node.when is not None:
                        try:
                            if not eval_when(node.when, self._resolve_ctx(node)):
                                self._set_status(node.id, "skipped")
                                changed = progressed = True
                                continue
                        except InterpolationError as e:
                            self._set_status(node.id, "failed", error=str(e))
                            changed = progressed = True
                            continue
                    # Transitive skip is by REFERENCE, not mere dependency (SPEC §7).
                    if skipped_deps and not node.optional:
                        if self._body_referenced_deps(node) & skipped_deps:
                            self._set_status(node.id, "skipped")
                            changed = progressed = True
                            continue
                elif rec.status == "done" and node.id in self.needs_check:
                    # Revalidate in topological order: a dep still awaiting its
                    # OWN revalidation could be invalidated after we hash-match
                    # against its stale recorded output — and nothing would
                    # ever re-check this node (B1).
                    if not all(self._dep_settled(d) for d in node.depends_on):
                        continue
                    if rec.adopted is not None:
                        # D2 (DESIGN-NOTE-adopt): settled by a journaled human
                        # adoption, EVEN AGAINST A HASH MISS — "do not re-run
                        # the adopted writer" is not achievable by inaction,
                        # because the OW-07 writer's hash legitimately missed
                        # (volatile upstream shell output) and a plain resume
                        # would re-run it over the human's edit. Before the
                        # plan, deliberately: re-planning just to ignore the
                        # answer would spill files and log a comparison nobody
                        # acts on. Said at the decision site, like every other
                        # revalidation outcome.
                        self.needs_check.discard(node.id)
                        rec.invalidated_by = None
                        self.store.record(rec)
                        self.log(
                            f"settled-by-adoption {node.id!r} — {len(rec.adopted.paths)} "
                            f"path(s) adopted {rec.adopted.ts}; hash comparison does not "
                            f"govern this node until the pin is released"
                        )
                        append_event(self.store.run_dir, {
                            "node": node.id, "status": "done", "settled_by_adoption": True,
                        })
                        changed = progressed = True
                        continue
                    executor = self.registry.get(node.kind)
                    invalidate = executor is None or not getattr(executor, "cacheable", False)
                    if invalidate:
                        rec.invalidated_by = None  # by design (shell/unknown), not by hash
                    if not invalidate:
                        if node.role == "map":
                            # A done map always re-enters _run_map: the node-level
                            # hash cannot see upstream refs inside the item body;
                            # PER-ITEM hashes (which render the body) do the
                            # caching, so this costs nothing for unchanged items.
                            invalidate = True
                            rec.invalidated_by = None  # per-item hashes decide
                        else:
                            try:
                                ctx = self._render_ctx(node, self.store.phase_dir(node.id))
                                work = executor.plan(node, ctx)
                                new_hash = compose_hash(
                                    node.role, node.kind, node.contract, work.fingerprint_parts
                                )
                                invalidate = new_hash != rec.input_hash
                                if invalidate:
                                    # A1: name WHICH part moved at the decision
                                    # site — a wrongly re-billed node is
                                    # otherwise indistinguishable from an
                                    # ordinary cache miss (the heal-text lesson).
                                    rec.invalidated_by = diff_labels(
                                        rec.hash_parts,
                                        label_parts(
                                            work.fingerprint_parts,
                                            work.meta.get("hash_detail"),
                                        ),
                                    )
                            except (SkippedReference, InterpolationError, Exception) as e:
                                invalidate = True
                                rec.invalidated_by = [
                                    f"replan failed: {type(e).__name__}: {e}"
                                ]
                    self.needs_check.discard(node.id)
                    if invalidate:
                        rec.status = "pending"
                        self.store.record(rec)
                        if rec.invalidated_by:
                            # SAY IT, at the moment of the decision. The reason
                            # was already recorded and journalled, but only
                            # `explain` ever read it — so an operator watching a
                            # resume saw a completed node re-bill with no
                            # account of why, and had to reconstruct it
                            # afterwards from a confusing gate block (consumer
                            # report 2026-08-13 item 1(b)). Shell nodes carry no
                            # reason by design (§0.1.7) and stay quiet.
                            self.log(
                                f"re-running {node.id!r} (its cached result no longer matches): "
                                + "; ".join(rec.invalidated_by)
                            )
                            append_event(
                                self.store.run_dir,
                                {
                                    "node": node.id,
                                    "status": "pending",
                                    "invalidated_by": rec.invalidated_by,
                                },
                            )
                    else:
                        if rec.invalidated_by is not None:
                            # This revalidation matched: a reason from an earlier
                            # round would read as current in `explain` (the
                            # journal keeps the history).
                            rec.invalidated_by = None
                            self.store.record(rec)
                        append_event(self.store.run_dir, {"node": node.id, "status": "done", "skipped_by_hash": True})
                    changed = progressed = True
        return progressed

    def _map_hash_matches(self, node: Node, rec) -> bool:
        try:
            array = self._resolve_over(node)
        except Exception:
            return False
        node_hash = self._map_node_hash(node, array)
        return node_hash == rec.input_hash

    # ------------------------------------------------------------------ main loop

    def run(self) -> int:
        # §9.4.1 precondition: heal.rollback requires a git workspace.
        for n in self.tg.nodes:
            if n.role == "gate" and n.heal.max_rounds > 0 and n.heal.rollback:
                if not isinstance(self.workspace, GitWorkspace):
                    raise RunRefusal(
                        f"gate {n.id!r} has heal.rollback: true but the workspace is not "
                        "git-managed; NullWorkspace cannot roll back"
                    )
        self._preflight_dirty_scope()
        self._start_monotonic = time.monotonic()
        # E4: baseline-gate bodies run inside the wall-clock budget window —
        # a pytest baseline is real work, not free bookkeeping.
        self._record_gate_baselines()
        token_pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="lockstep-tok")
        other_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lockstep-oth")
        # Composition review finding 4: flow nodes park for their child's whole
        # duration, and 8 of them on other_pool would queue a 5-second shell
        # probe behind an hour of children. A parked thread costs nothing worth
        # rationing, so they get their own pool, sized by the graph.
        flow_pool = ThreadPoolExecutor(
            max_workers=max(1, sum(1 for n in self.tg.nodes if n.kind == "flow")),
            thread_name_prefix="lockstep-flow")
        tailer = _ProgressTailer(self.store.run_dir)
        tailer.start()
        try:
            while True:
                self._settle()
                if self.resources.abort.is_set():
                    # Cooperative stop (composition §2): set by a flow node's
                    # cancel watcher, never for a plain root run. Between
                    # waves only — in-flight work finishes, nothing new
                    # dispatches; the FlowExecutor translates the marker to
                    # the engine's existing cancelled handling.
                    break
                if self.flags["budget"]:
                    break
                if self._wall_exceeded():
                    self.flags["budget"] = True
                    break
                wave = [
                    n
                    for n in self.tg.nodes
                    if self._rec(n.id).status == "pending"
                    # _dep_settled, not bare SETTLED: a done dep still in
                    # needs_check may yet be invalidated by its own upstream's
                    # re-run — dispatching past it hands this node stale
                    # cached output (B1).
                    and all(self._dep_settled(d) for d in n.depends_on)
                ]
                if not wave:
                    break
                futures = []
                for node in wave:
                    self._set_status(node.id, "running")
                    if node.kind == "flow":
                        futures.append(flow_pool.submit(self._run_node_safe, node))
                    elif self._costs_tokens_hint(node):
                        # The slot is acquired INSIDE the worker (composition
                        # §1): acquiring before submit would serialize wave
                        # dispatch. For a lone engine the pool has exactly as
                        # many threads as there are slots, so this never
                        # blocks; across composed engines the shared
                        # semaphore is what makes --max-workers bound the
                        # whole tree.
                        futures.append(token_pool.submit(self._run_slotted, node))
                    else:
                        futures.append(other_pool.submit(self._run_node_safe, node))
                futures_wait(futures)
                self._process_gate_outcomes()
        finally:
            token_pool.shutdown(wait=True)
            other_pool.shutdown(wait=True)
            flow_pool.shutdown(wait=True)
            tailer.stop()
            self.store.mutate(lambda s: None)
        return self._exit_code()

    def _preflight_dirty_scope(self) -> None:
        """E9 (LESSONS-TO-MECHANISMS): scope enforcement leaves IN-scope writes
        alone — including overwriting a file the operator edited before the
        run. "Preserve user-owned changes" and "a node may overwrite an
        in-scope file" were two different guarantees with a human checklist as
        the only bridge; this is the mechanical one."""
        if not self.check_dirty_scope or not isinstance(self.workspace, GitWorkspace):
            return
        try:
            # Same exclusion as quarantine and heal, for the same reason: where
            # the run dir sits inside an un-ignored work tree, the driver's own
            # just-written state.json/flow copy is "dirty" — and a `["**"]`
            # scope would make every fresh run refuse on its own bookkeeping
            # (adversarial-review finding 1, repro'd live).
            dirty = self._outside_run_dir(self.workspace.dirty_paths())
        except WorkspaceError:
            return
        if not dirty:
            return
        overlaps = []
        for n in self.tg.nodes:
            if "writes" not in n.spec:
                continue
            scope = self._writes_of(n)
            hits = sorted(p for p in dirty if path_in_scope(p, scope))
            if hits:
                overlaps.append(f"{n.id}: {', '.join(hits)}")
        if overlaps:
            raise RunRefusal(
                "uncommitted working-tree changes fall inside declared write scopes and "
                "would be legally overwritten by the run:\n  "
                + "\n  ".join(overlaps)
                + "\ncommit or stash them first, narrow the scopes, or pass --allow-dirty-scope",
                reason="dirty_scope",
            )

    def _record_gate_baselines(self) -> None:
        """E4: run each `baseline: true` gate's body once against the PRE-RUN
        tree and persist its findings. `_apply_baseline` subtracts them at
        evaluation, so the gate blocks only on findings the run introduced.
        Persisted in RunState: a resume filters against the same baseline the
        run started from, never a re-measured one."""
        if self.replaying:
            return  # recorded gate results are already adjudicated (finding 5)
        for node in self.tg.nodes:
            if node.role != "gate" or not node.spec.get("baseline"):
                continue
            if node.id in self.store.state.baseline_findings:
                continue
            executor = self.registry.get(node.kind)
            base_dir = self.store.phase_dir(node.id) / "baseline"
            base_dir.mkdir(parents=True, exist_ok=True)
            findings: list = []
            try:
                ctx = self._render_ctx(node, base_dir)
                work = executor.plan(node, ctx)
                self._spend_spawn(work)
                # A real, billed attempt - and the only one the journal used
                # to miss. Its silent `attempts += 1` then made the gate's
                # FIRST ordinary attempt read `resume` in a fresh run (E4
                # baseline gates ship in the factory flows).
                self._journal_attempt(node, work, "baseline")
                raw = executor.execute(work, base_dir, node.timeout_s)
                # The baseline spawn is a real attempt: keep attempts and
                # token_spawns telling one story in `status`.
                rec = self._rec(node.id)
                rec.attempts += 1
                self.store.record(rec)
                ref = resolve_contract(node.contract, self.tg.contracts_module)
                value = validate_result(raw.result_text or "", ref)
                findings = list(value.get("findings", []))
            except BudgetTripped:
                # §9.5: a trip must exit 4, not escape as a traceback.
                # _spend_spawn already set flags["budget"], so returning lets
                # the main loop break and exit cleanly. NOTHING has executed
                # yet (baselines precede every wave), so a resume with
                # headroom re-records the missing baselines against a tree
                # that is still genuinely pre-run.
                self.log(
                    f"baseline gate {node.id!r}: spawn budget tripped before its baseline "
                    f"could be recorded — resume with headroom to record it"
                )
                return
            except Exception as e:
                # Fail-open to an EMPTY baseline, loudly: a broken baseline
                # body must not bless future findings, and pre-existing
                # failures will then surface as new — visible, not silent.
                self.log(
                    f"baseline gate {node.id!r}: body failed at baseline time "
                    f"({type(e).__name__}: {e}) — recording an EMPTY baseline; "
                    f"pre-existing failures will surface as new findings"
                )
            self.store.mutate(
                lambda st, n=node.id, f=findings: st.baseline_findings.__setitem__(n, f)
            )
            append_event(
                self.store.run_dir,
                {"node": node.id, "status": "baseline", "findings": len(findings)},
            )

    def _apply_baseline(self, gate: Node, verdict: Verdict) -> Verdict:
        """E4 subtraction: drop findings recorded in the gate's pre-run
        baseline, matched on (file, claim) — exact, not fuzzy, so a message
        that changed is a new finding. A block whose findings ALL match the
        baseline flips to pass: the debt predates the run and is not the
        change's to answer for. Heal prompts then carry only NEW findings."""
        base = self.store.state.baseline_findings.get(gate.id)
        if not base:
            return verdict
        keys = {(f.get("file"), f.get("claim")) for f in base if isinstance(f, dict)}
        kept = [f for f in verdict.findings if (f.file, f.claim) not in keys]
        dropped = len(verdict.findings) - len(kept)
        if dropped == 0:
            return verdict
        if verdict.verdict == "block" and not kept:
            return Verdict(
                findings=[],
                verdict="pass",
                reason=f"all {dropped} finding(s) match the pre-run baseline (was: {verdict.reason})",
            )
        return verdict.model_copy(
            update={
                "findings": kept,
                "reason": verdict.reason
                + f" [{dropped} pre-existing finding(s) suppressed by the pre-run baseline]",
            }
        )

    def _exit_code(self) -> int:
        if self.flags["budget"]:
            return EXIT_BUDGET
        if self.flags["gate_block"]:
            return EXIT_GATE_BLOCK
        if self.flags["approval_rejected"]:
            return EXIT_APPROVAL_REJECTED
        if any(r.status == "failed" for r in self.store.state.nodes.values()):
            return EXIT_NODE_FAILED
        return EXIT_OK

    # ------------------------------------------------------------------ node run

    def _run_slotted(self, node: Node) -> None:
        with self.resources.worker_slots:
            self._run_node_safe(node)

    def _run_node_safe(self, node: Node) -> None:
        try:
            self._run_node(node)
        except BudgetTripped:
            # No new spawns; this node goes back to pending; in-flight peers finish.
            # Setting the flag here is a no-op for a plain node (its own
            # _spend_spawn already set it) and load-bearing for a flow node,
            # whose child tripped the ROOT wallet: without it this engine
            # would re-dispatch the pending flow node forever
            # (PROPOSAL-flow-composition §1).
            self.flags["budget"] = True
            rec = self._rec(node.id)
            rec.status = "pending"
            rec.error = None  # e.g. a mid-corrective trip must not leave stale error text
            self.store.record(rec)
        except WorkspaceError as e:
            self._set_status(node.id, "failed", error=str(e))
        except Exception as e:  # a driver bug must not wedge the run silently
            self._set_status(node.id, "failed", error=f"{type(e).__name__}: {e}")

    def _run_node(self, node: Node) -> None:
        if node.role == "approval":
            self._run_approval(node)
            return
        if node.role == "map":
            self._run_map(node)
            return
        executor = self.registry.get(node.kind)
        phase_dir = self.store.phase_dir(node.id)
        ctx = self._render_ctx(node, phase_dir)
        try:
            work = executor.plan(node, ctx)
        except SkippedReference:
            self._set_status(node.id, "skipped")
            return
        rec = self._rec(node.id)
        rec.input_hash = compose_hash(node.role, node.kind, node.contract, work.fingerprint_parts)
        rec.hash_parts = label_parts(work.fingerprint_parts, work.meta.get("hash_detail"))
        self.store.record(rec)
        decision = self.policy.allows(node, ACTOR_LOCAL_USER)
        if not decision.allowed:
            self._set_status(node.id, "failed", error=f"policy denied: {decision.reason}")
            return
        if isinstance(work.render, str):
            pass  # prompt.txt written by the executor at execute time
        tokens = sorted(set(node.exclusive) | set(work.exclusive))
        locks = self._acquire(tokens)
        # Presence-keyed, not truthiness-keyed (V1): a DECLARED-empty scope
        # (`writes: []`) means "this node writes nothing" and is enforced —
        # every change is a violation. Only an ABSENT key is the v1
        # unconstrained behavior (DEVIATIONS 2026-08-11).
        has_scope = "writes" in node.spec
        scope = self._writes_of(node)
        scope_ref = None
        scope_error: str | None = None
        try:
            self._maybe_snapshot(node)
            staged_before: set[str] = set()
            if has_scope and "tree" in tokens:
                # Only while serialized on the tree: otherwise a concurrent
                # node's writes would be attributed to this one, and a false
                # accusation is worse than no check. `verify` warns when a
                # declared scope lands on an unserialized node.
                scope_ref = self._scope_baseline(node)
                if scope_ref is not None:
                    staged_before = self.workspace.staged_paths()
                    rec.tree_before = scope_ref.ref
                    # The pair must always describe the SAME attempt. A heal
                    # round or a resumed re-run takes a new baseline, and a
                    # stale `tree_after` from the previous attempt would make
                    # `node_diff` diff two trees that never bracketed anything
                    # — possibly backwards.
                    rec.tree_after = None
                    self.store.record(rec)
            raw = self._execute_with_retries(node, executor, work, phase_dir)
            if scope_ref is not None:
                # The WHOLE violation sequence — detect, patch, restore, record —
                # is INSIDE the token, not after `finally`. Outside it the
                # baseline comparison measures the next node's tree (so a node
                # that stayed in scope is accused of its peer's writes), the
                # evidence patch captures that peer's work, and the restore
                # reverts the peer's live file while it goes on to record `done`.
                # ONE snapshot of the tree this node left, shared by every
                # question asked about it (`diff_patch`'s own docstring says
                # why: a snapshot walks the whole tree, and two of them
                # describe two different moments). It is also what
                # `node_diff` reads back — the recorded pair is the only
                # description of this step's change that a later phase cannot
                # move (consumer report 2026-08-13 item 1).
                after = self._after_snapshot(node)
                in_scope, violations = self._scope_changes(
                    scope_ref, scope, label=node.id, current=after
                )
                if violations:
                    scope_error, clean = self._quarantine(
                        node, phase_dir, scope, scope_ref, in_scope, violations,
                        staged_before, after,
                    )
                    if raw.error == "cancelled":
                        # r6 C3: a cancelled node consumes no corrective. The
                        # quarantine still happened (it spawns nothing), and
                        # the record says both things in order.
                        scope_error = "cancelled\n" + scope_error
                    elif clean and getattr(executor, "supports_corrective_respawn", False):
                        # G1b: ONE corrective re-spawn from the restored tree.
                        # Inside the token, like the quarantine itself — the
                        # re-spawn writes files. Only after a CLEAN rollback:
                        # re-spawning over a part-way tree builds on wreckage.
                        raw, scope_error = self._scope_corrective(
                            node, executor, work, phase_dir, scope, scope_ref,
                            staged_before, scope_error,
                        )
                elif not raw.timed_out and raw.exit_code == 0 and raw.result_text is not None:
                    # Evidence of what a node touched, on SUCCESS. A failed
                    # spawn's changed paths are the wreckage, not the record.
                    if after is not None:
                        rec.tree_after = after.ref
                    self._record_touched(node, phase_dir, in_scope)
        finally:
            self._release(locks)
        if scope_error is not None:
            self._set_status(node.id, "failed", error=scope_error)
            return
        self._finish(node, executor, work, phase_dir, raw)

    def _writes_of(self, node: Node) -> list[str]:
        """The node's declared scope, with `{args.NAME}` resolved (and nothing
        else — see interpolate.render_scope). One helper so the engine's four
        readers (quarantine, dirty preflight, heal-text restatement, rollback
        warning) can never disagree about what a node was permitted to write.
        """
        return render_scope([str(w) for w in (node.spec.get("writes") or [])],
                            self.store.state.args)

    def _dissolve_adoption(self, rec, cause: str) -> None:
        """Clear an adoption pin because the engine is about to re-spawn the
        node (heal, steer). Journaled like `adopt --release` — an operator
        reading the run must find WHERE the pin went, and the original
        adoption event is never deleted (append-only journal)."""
        if rec.adopted is None:
            return
        rec.adopted = None
        self.store.record(rec)
        append_event(
            self.store.run_dir,
            {"kind": "adoption", "op": "dissolved", "node": rec.node_id, "cause": cause},
        )
        self.log(
            f"adoption pin on {rec.node_id!r} dissolved by {cause} — the next spawn's "
            f"output is model output, and the pin described the human's"
        )

    def note_forced(self, node_id: str) -> None:
        """Parity 3.3 provenance: the seed DECLINED this node on instruction,
        not on a hash miss. Without the distinction, `status` and `explain`
        would show a node that re-billed with inputs that never moved and no
        way to tell whether that was --force-stale or a hash bug."""
        rec = self._rec(node_id)
        rec.invalidated_by = [
            "forced stale (--force-stale): the seed was instructed not to serve "
            "this node or anything downstream of the named frontier"
        ]
        self.store.record(rec)
        append_event(
            self.store.run_dir,
            {"kind": "seed", "node": node_id, "decision": "forced"},
        )

    def note_seeded(self, node_id: str, source: str) -> None:
        """E7 provenance. Called by the seed wrapper when it serves a result:
        the record says where it came from and the journal says when, so a
        reader can tell inherited work from work this run did."""
        rec = self._rec(node_id)
        rec.seeded_from = source
        self.store.record(rec)
        append_event(
            self.store.run_dir,
            {"kind": "seed", "node": node_id, "source": source},
        )

    def _timed_ws(self, label: str, op: str, fn):
        """Run a workspace operation and journal how long it took (P1-perf).

        Every git tree operation the engine performs is here, and each one is
        O(working tree), not O(what changed) — `snapshot()` writes a blob for
        every file into a FRESH temp index, so nothing is cached between calls.
        A run whose nodes write a lot pays that per node, twice for a scoped
        one (baseline + diff), and a long-lived run's later resumes pay it over
        a bigger tree than its first. That growth was reported live as a gate
        creeping 13 → 32 minutes across resumes, with no way to see where the
        time went; `kind: "timing"` lines are that visibility.

        Advisory, like `progress.jsonl`: no reader branches on them, they carry
        no `status`, and dropping them changes no decision. They ARE chained
        into the journal, which costs nothing — every line already carries a
        wall-clock `ts`, so the trace was never byte-reproducible across runs.
        """
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            append_event(
                self.store.run_dir,
                {"kind": "timing", "node": label, "op": op,
                 "ms": round((time.perf_counter() - t0) * 1000)},
            )

    def _scope_baseline(self, node: Node, label: str | None = None) -> SnapshotRef | None:
        """Baseline for write-scope detection. A non-git tree cannot diff, so
        detection is off there — the same honest limitation M6 states for
        external-edit detection. `label` names a map ITEM in the timing line
        (`m[3]`), since each item takes its own baseline."""
        try:
            return self._timed_ws(label or node.id, "scope-baseline", self.workspace.snapshot)
        except WorkspaceError:
            self.log(
                f"write scope: {label or node.id!r} declares one, but this workspace cannot "
                f"snapshot (not a git tree) — detection is off for this node"
            )
            return None

    def _outside_run_dir(self, paths: list[str]) -> list[str]:
        """Drop the driver's OWN bookkeeping from a tree diff.

        `runs/` is gitignored in this repo, so `git add -A` never sees it — but
        that is a convention of one repository, not a property of the design.
        Where the run dir sits inside the work tree and is NOT ignored, every
        prompt, log and `state.json` write shows up as a change the node made.
        Before quarantine that was a spurious accusation; with it the engine
        would move its own `stdout.log` aside and roll `state.json` back
        mid-run. Found by running a deliberate violation end to end.
        """
        try:
            rel = self.store.run_dir.resolve().relative_to(self.repo_root.resolve())
        except (ValueError, OSError):
            return paths  # run dir is outside the tree: nothing to exclude
        prefix = str(rel).replace("\\", "/").strip("/") + "/"
        return [p for p in paths if not p.replace("\\", "/").startswith(prefix)]

    def _after_snapshot(self, node: Node, label: str | None = None) -> SnapshotRef | None:
        """The tree this node left. Same limitation as the baseline: a non-git
        tree cannot snapshot, and then there is nothing to compare or record."""
        try:
            return self._timed_ws(label or node.id, "scope-after", self.workspace.snapshot)
        except WorkspaceError:
            return None

    def _scope_changes(self, since: SnapshotRef, scope: list[str],
                       label: str = "",
                       current: SnapshotRef | None = None) -> tuple[list[str], list[str]]:
        """(in-scope changed paths, violations). Both halves are wanted: the
        violations to quarantine, the in-scope list as touched-path evidence and
        to spot a rename OUT of scope (§0.1 T1.3)."""
        try:
            changed = self._outside_run_dir(
                self._timed_ws(
                    label, "scope-diff", lambda: self.workspace.changed_paths(since, current)
                )
            )
        except WorkspaceError:
            return [], []
        in_scope = sorted(p for p in changed if path_in_scope(p, scope))
        violations = sorted(p for p in changed if not path_in_scope(p, scope))
        return in_scope, violations

    def _scope_violations(self, since: SnapshotRef, scope: list[str]) -> list[str]:
        return self._scope_changes(since, scope)[1]

    def _record_touched(self, node: Node, phase_dir: Path, in_scope: list[str],
                        item: tuple[int, ItemRecord] | None = None) -> None:
        """Write the in-scope changed-path list beside the attempt and record a
        COUNT plus its path — never the list itself (`FileStore.record` rewrites
        all of state.json on every call). Attempt-scoped, because `phase_dir`
        survives resume and heal rounds. For a map item the evidence lands on
        the ItemRecord and under `items/<i>/`."""
        rec = self._rec(node.id)
        target = item[1] if item is not None else rec
        name = f"touched-{target.attempts}.txt"
        (phase_dir / name).write_text(
            "".join(f"{p}\n" for p in in_scope), encoding="utf-8"
        )
        target.touched_count = len(in_scope)
        target.touched_path = (
            f"phases/{node.id}/items/{item[0]}/{name}" if item is not None
            else f"phases/{node.id}/{name}"
        )
        self.store.record(rec)

    def _quarantine(
        self,
        node: Node,
        phase_dir: Path,
        scope: list[str],
        scope_ref: SnapshotRef,
        in_scope: list[str],
        violations: list[str],
        staged_before: set[str],
        current: SnapshotRef | None = None,
        item: tuple[int, ItemRecord] | None = None,
    ) -> tuple[str, bool]:
        """Preserve the blocked attempt, put the tree back, say what happened to
        every path. Returns (failure message, rollback-completed-cleanly) — the
        flag is what licenses a scope-corrective re-spawn (G1b): a re-spawn
        over a part-way-restored tree would build on exactly the wreckage the
        quarantine failed to clear.

        Runs inside the tree token — the mutation is the dangerous half, and
        outside the token it reverts a concurrent node's live file while that
        node goes on to record `done`.

        Artifacts are ATTEMPT-scoped, as heal's are (`roles.py:_heal`):
        `phase_dir` survives resume and heal rounds and `shutil.move` overwrites
        silently, so fixed names would let attempt 2 destroy the evidence
        attempt 1 exists to leave.

        In-scope writes are left exactly as they are.

        `item` = (index, ItemRecord) for a map item: the attempt counter, the
        evidence path and the journal line are the item's, and the map's own
        record is never touched here — the map fails through its item.
        """
        rec = self._rec(node.id)
        target = item[1] if item is not None else rec
        label = f"{node.id}[{item[0]}]" if item is not None else node.id
        rel_dir = f"phases/{node.id}/items/{item[0]}" if item is not None else f"phases/{node.id}"
        stem = f"out-of-scope-{target.attempts}"
        patch_name = f"{stem}.patch"
        discard = phase_dir / stem
        outcomes: list[tuple[str, str]] = []
        failure: str | None = None

        try:
            # BEFORE any restore (§9.4.4): this is the blocked attempt, and
            # after the rollback there is nothing left to write down.
            (phase_dir / patch_name).write_text(
                self.workspace.diff_patch(scope_ref, current), encoding="utf-8"
            )
        except (WorkspaceError, OSError) as e:
            failure = f"could not preserve the attempt patch: {e}"

        if failure is None:
            for p in violations:
                try:
                    # One path per call so a part-way failure is attributable:
                    # a half rollback that reads as a clean one is the failure
                    # mode this feature exists to prevent.
                    self.workspace.restore(scope_ref, [p], discard)
                except (WorkspaceError, OSError) as e:
                    failure = f"{p}: {e}"
                    break
                outcomes.append((
                    p,
                    f"moved aside into {stem}/" if (discard / p).exists()
                    else "restored to its state before this step",
                ))

        handled = [p for p, _ in outcomes]
        agents = [p for p in handled if p not in staged_before]
        operators = [p for p in handled if p in staged_before]
        if agents:
            try:
                self.workspace.unstage(agents)
            except (WorkspaceError, OSError) as e:
                failure = f"{failure + '; ' if failure else ''}index not reset: {e}"

        for p, outcome in outcomes:
            ev = {"node": node.id, "status": "quarantined", "path": p, "outcome": outcome}
            if item is not None:
                ev["item"] = item[0]
            append_event(self.store.run_dir, ev)
        # As heal does after ITS restore: refresh the lineage head, or a
        # crash-then-resume reads the rollback as external edits.
        try:
            _, detail = self.workspace.fingerprint_detail()
            self.store.mutate(lambda st: setattr(st, "fingerprint_detail", detail))
        except WorkspaceError:  # pragma: no cover — git tree by construction here
            pass

        lines = [
            f"write scope violated: {'item ' + label if item is not None else 'this step'} "
            f"may only write "
            f"{', '.join(scope) if scope else 'nothing (declared writes: [])'} "
            f"but wrote {', '.join(violations)}",
            f"the blocked attempt is preserved at {rel_dir}/{patch_name}",
        ]
        lines += [f"  {p} — {outcome}" for p, outcome in outcomes]
        if operators:
            lines.append(
                f"  left staged as you had it, index untouched: {', '.join(operators)}"
            )
        if failure is not None:
            unhandled = [p for p in violations if p not in handled]
            lines.append(
                f"THE ROLLBACK DID NOT COMPLETE: {failure}"
                + (f"; not handled: {', '.join(unhandled)}" if unhandled else "")
                + f" — the tree is part-way back and the whole attempt is in {patch_name}"
            )
        moved = [p for p, o in outcomes if o.startswith("moved aside")]
        deleted_in_scope = [p for p in in_scope if not (self.repo_root / p).exists()]
        if moved and deleted_in_scope:
            lines.append(
                f"an in-scope path is now gone ({', '.join(deleted_in_scope)}) and an "
                f"out-of-scope path was created ({', '.join(moved)}): if that was a "
                f"rename out of scope, the delete was in scope and permitted while only "
                f"the new path was quarantined, so the file is in neither place — its "
                f"content is in {stem}/"
            )
        return "\n".join(lines), failure is None

    _SCOPE_PATCH_CAP = 40_000  # chars of patch embedded as evidence; full file stays on disk

    def _scope_corrective_prompt(self, work: PlannedWork, scope: list[str], patch_text: str) -> str:
        """G1b (upstream-response-ow07-feedback): the scope twin of
        `_corrective_prompt`, for the same reason — a headless spawn is
        stateless, so the correction must carry its own context: the original
        task, the reverted writes as fenced evidence, and the boundary
        restated. Unlike the contract corrective this one is NOT output-only:
        the out-of-scope work is gone and the re-spawn may need to redo it,
        inside the scope this time."""
        if len(patch_text) > self._SCOPE_PATCH_CAP:
            patch_text = (patch_text[: self._SCOPE_PATCH_CAP]
                          + "\n... (truncated; the full patch is preserved in the run dir)")
        original = str(work.render) if isinstance(work.render, str) else json.dumps(work.render)
        return (
            f"{original}\n\n---\n"
            "A previous attempt at this task wrote outside its declared write scope. "
            "Those out-of-scope writes were REVERTED; the tree you are working in now "
            "contains only the in-scope part of that attempt. The reverted changes are "
            "fenced below as evidence of what was attempted — do not recreate them at "
            "those paths:\n"
            + fence_block("scope.violation.patch", patch_text)
            + "\n\nYou may write ONLY these paths (spec.writes): "
            + (", ".join(scope) if scope else "nothing — this task declares writes: []")
            + ".\nComplete the task from the current tree, keeping every edit inside "
              "that scope. If the reverted work was necessary, achieve its purpose "
              "through the declared paths instead."
        )

    def _scope_corrective(
        self,
        node: Node,
        executor,
        work: PlannedWork,
        phase_dir: Path,
        scope: list[str],
        scope_ref: SnapshotRef,
        staged_before: set[str],
        first_error: str,
        item: tuple[int, ItemRecord] | None = None,
    ) -> tuple[RawResult, str | None]:
        """Exactly one corrective re-spawn after a write-scope quarantine (G1b),
        symmetric with the contract-violation shape: bounded (this method never
        recurses), spends a spawn, journaled, and it does not weaken the
        boundary — the quarantine already happened, the retry starts from the
        restored tree, and a second violation quarantines again with no third
        chance. Returns (raw, scope_error): scope_error None means the
        corrective stayed in scope and `_finish` should judge its result.

        The round-2 baseline is the ORIGINAL `scope_ref`, deliberately: the
        quarantine restored every out-of-scope path to it, in-scope writes from
        attempt 1 are legal against it by definition, and `tree_before` keeps
        describing the pre-attempt tree so `node_diff` brackets the node's
        total surviving change, not just the corrective's.

        For a map item (`item` = (index, ItemRecord)) the attempt counter,
        the journal ordinal and the tree pair are the item's."""
        rec = self._rec(node.id)
        target = item[1] if item is not None else rec
        label = f"{node.id}[{item[0]}]" if item is not None else node.id
        patch_path = phase_dir / f"out-of-scope-{target.attempts}.patch"
        try:
            patch_text = patch_path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover — quarantine reported clean, so it wrote
            patch_text = "(the quarantine patch could not be read back)"
        corrective = work.model_copy(update={
            "render": self._scope_corrective_prompt(work, scope, patch_text),
            "meta": {**work.meta, "corrective": True},
        })
        try:
            self._spend_spawn(corrective)
        except BudgetTripped:
            target.error = f"{first_error}\n(budget tripped before the scope-corrective re-spawn)"
            raise
        ev = {"node": node.id, "status": "scope-corrective-respawn"}
        if item is not None:
            ev["item"] = item[0]
        append_event(self.store.run_dir, ev)
        self._journal_attempt(
            node, corrective, "scope-corrective",
            item_index=item[0] if item is not None else None,
            ordinal=(target.attempts + 1) if item is not None else None,
        )
        self.log(
            f"[{label}] write scope violated — one corrective re-spawn from the "
            f"restored tree (declared scope restated, reverted patch embedded)"
        )
        raw2 = executor.execute(corrective, phase_dir, node.timeout_s)
        target.attempts += 1
        self.store.record(rec)
        if (phase_dir / "CANCELLED").exists():
            return raw2, "cancelled"  # r6 C3 covers corrective re-spawns
        after2 = self._after_snapshot(node, label)
        in_scope2, violations2 = self._scope_changes(
            scope_ref, scope, label=label, current=after2
        )
        if violations2:
            msg2, _ = self._quarantine(
                node, phase_dir, scope, scope_ref, in_scope2, violations2,
                staged_before, after2, item=item,
            )
            return raw2, (
                "the corrective re-spawn ALSO violated the write scope — no further "
                "retries (one round, by design)\n" + msg2
            )
        if not raw2.timed_out and raw2.exit_code == 0 and raw2.result_text is not None:
            if after2 is not None:
                target.tree_after = after2.ref
            self._record_touched(node, phase_dir, in_scope2, item=item)
        return raw2, None

    def _maybe_snapshot(self, node: Node) -> None:
        """Baseline snapshot is PROACTIVE: taken immediately before the first
        node in a gate's heal.targets executes — at block time the pre-attempt
        state no longer exists (SPEC §9.4.2). Every heal round restores to this
        same baseline (not retaken on re-rounds)."""
        gate_id = self.target_gate.get(node.id)
        if gate_id is None:
            return
        gate = self.tg.node(gate_id)
        if not gate.heal.rollback:
            return
        with self._snapshot_guard:
            if self.snapshots.get(gate_id) is None:
                ref = self._timed_ws(gate_id, "heal-baseline", self.workspace.snapshot)
                self.snapshots[gate_id] = ref
                # Persist BEFORE the target executes: a later crash + resume
                # must find this baseline, not re-snapshot a mutated tree.
                self.store.mutate(lambda st: st.heal_baselines.__setitem__(gate_id, ref.ref))
                append_event(
                    self.store.run_dir,
                    {"node": gate_id, "status": "snapshot", "error": None, "ref": ref.ref},
                )

    @staticmethod
    def _effective_retry(node: Node, executor, work: PlannedWork | None = None):
        """AMENDMENTS-r5 B2 plus the A2 stanza tier (DEVIATIONS 2026-09-09):
        a node that sets `retry` in the flow file (field present, even
        {"max": 0}) uses it verbatim; otherwise the resolved stanza's
        `default_retry` (carried in work.meta — scheduling-only, unhashed);
        otherwise the executor's kind-level default_retry (harness: 2 ×
        minute-scale backoff for transient 429/529s); otherwise the model
        default."""
        if "retry" in node.model_fields_set:
            return node.retry
        stanza_default = work.meta.get("stanza_default_retry") if work is not None else None
        if stanza_default is not None:
            return RetrySpec.model_validate(stanza_default)
        return getattr(executor, "default_retry", None) or node.retry

    def _execute_with_retries(self, node: Node, executor, work: PlannedWork, phase_dir: Path) -> RawResult:
        """RetrySpec covers nonzero exits and timeouts with backoff; PLUS one
        automatic retry on timeout or empty result, additive, even when
        retry.max == 0 (SPEC §9.3, AMENDMENTS M4)."""
        rec = self._rec(node.id)
        # C2: `repaired` describes the RECORDED result. A new execution is a
        # new result — the flag resets here (and re-sets only if THIS
        # attempt's output is repaired), or a clean re-run after a hash miss
        # would keep claiming a repair on `status` and the mission drawer.
        # It persists across revalidation-kept results, where the repaired
        # bytes are still the recorded ones.
        rec.repaired = False
        retry = self._effective_retry(node, executor, work)
        retries_left = retry.max
        backoff_s = retry.backoff_ms / 1000.0
        auto_used = False
        # r6 C3: a marker from a PREVIOUS session is stale — this spawn starts
        # fresh. (A cancel racing this exact instant degrades to an ordinary
        # failed-then-retried spawn; acceptable.)
        (phase_dir / "CANCELLED").unlink(missing_ok=True)
        mark_mailbox_consumed(self.store.run_dir, node.id)  # r6 C2 bookkeeping
        # PEEKED, not consumed: `_spend_spawn` below can raise BudgetTripped,
        # and a consumed signal would be gone on the resume that actually runs
        # the attempt - turning the heal into a mislabelled `resume`. The
        # durable store removed the phantom event; consuming early would have
        # made the mislabel permanent instead.
        healed = self._peek_heal_round(node.id)
        if self._served(work):
            cause, heal_round = "served", None
        elif healed is not None:
            cause, heal_round = "heal", healed
        elif rec.attempts and node.id not in self._attempted:
            cause, heal_round = "resume", None
        else:
            cause, heal_round = "initial", None
        while True:
            # Spend FIRST: a budget trip raises out of `_spend_spawn`, and
            # journalling before it recorded an attempt that never happened -
            # then the resume journalled the same ordinal again, so one real
            # attempt left two byte-identical events. Exit 4 then resume is a
            # documented normal outcome, not an edge case.
            self._spend_spawn(work)
            # Consumed unconditionally once the spawn is paid for. Guarding on
            # `heal_round is not None` skipped the `served` branch (which
            # forces it to None), so a seeded run finished still holding the
            # signal - a leak into whatever ran next.
            self._take_heal_round(node.id)
            self._journal_attempt(node, work, cause, heal_round=heal_round)
            raw = executor.execute(work, phase_dir, node.timeout_s)
            rec.attempts += 1
            self.store.record(rec)
            if (phase_dir / "CANCELLED").exists():
                # r6 C3: consumes NO retries, no auto-retry, no corrective.
                raw.error = "cancelled"
                return raw
            ok = (not raw.timed_out) and raw.exit_code == 0 and raw.result_text is not None
            if ok:
                return raw
            if (
                (raw.timed_out or raw.result_text is None)
                and not auto_used
                # The M4 auto-retry exists for spawns that PRODUCED NOTHING
                # (a flaky harness). An executor may opt out: a flow node's
                # result-less failure is a child that genuinely blocked, and
                # re-entering it would silently convert a child gate block
                # into a retried success — the exact outcome the composition
                # table freezes as a parent failure (flow-composition §3).
                and getattr(executor, "auto_retry", True)
            ):
                auto_used = True
                cause = "auto-retry"
                continue
            if retries_left > 0 and (raw.exit_code != 0 or raw.timed_out):
                retries_left -= 1
                cause = "retry"
                time.sleep(backoff_s)
                backoff_s *= retry.factor
                continue
            return raw

    def _peek_heal_round(self, node_id: str) -> int | None:
        """The queued heal round WITHOUT consuming it (see the call site)."""
        return (self.store.state.heal_pending or {}).get(node_id)

    def _take_heal_round(self, node_id: str) -> int | None:
        """The heal round a cascade queued for this node, consumed.

        Popped rather than read: `rec.heal_round` is never reset (it is the
        gate's running total), so using it as a fallback made EVERY later
        attempt of a gate that once healed report `heal`, forever, across
        processes. Consuming a durable one-shot signal is the only version of
        this that stays true on the second drive.
        """
        st = self.store.state
        if node_id not in (st.heal_pending or {}):
            return None
        round_n = st.heal_pending[node_id]
        self.store.mutate(lambda s, _n=node_id: s.heal_pending.pop(_n, None))
        return round_n

    @staticmethod
    def _served(work: PlannedWork) -> bool:
        """Was this "attempt" served from a recording rather than spawned?
        `--replay` and `--seed` wrap the executor at the `execute` seam, so a
        served node still walks the attempt loop; journalling it as `initial`
        would have the journal of a replay assert that every node ran."""
        meta = work.meta or {}
        return "_seed" in meta or "_replay" in meta

    def _journal_attempt(self, node: Node, work: PlannedWork, cause: str, *,
                         item_index: int | None = None, ordinal: int | None = None,
                         heal_round: int | None = None) -> None:  # noqa: D401
        """S3: record WHY an attempt happened, in the chained journal.

        Rotated artifact names record THAT an attempt happened; nothing
        recorded why. Recovering "attempt 2 was a contract corrective, attempt
        3 was heal round 1" meant inferring from filenames and correlating
        loosely against transitions - not sound, and a downstream consumer
        asked for a per-attempt manifest artifact to fix it.

        The journal is the right home instead of a new artifact: it is already
        hash-chained (`verify-trace`), already kind-tagged and
        forward-tolerant (an older reader ignores an unknown kind), and
        already read by every cockpit surface. An attempt record is
        engine-recorded FACT, not derived data, so putting it outside trace
        integrity would be the wrong side of the line a derived cache sits on.

        Carries no prompt or context text - only the cause, the ordinal, and
        the NAMES of the hash parts, which a reader resolves against
        `hash_parts`. Never an input to any hash: M3 composition does not move,
        and this is additive to the journal, which no hash covers.
        """
        rec = self._rec(node.id)
        event: dict = {
            "kind": "attempt",
            "node": node.id,
            "cause": cause,
            # Matches what `attempts` counts, so a reader can line an event up
            # with a rotated artifact.
            "ordinal": (rec.attempts + 1) if ordinal is None else ordinal,
        }
        if item_index is not None:
            event["item"] = item_index
        # NO fallback to `rec.heal_round`: that is the gate's running total and
        # is never reset, so every later attempt of a gate that once healed got
        # stamped with a round it was not part of - and the journal pane renders
        # it as "(rework round N)", a rework claim about an attempt that is not
        # one. The cause lost this fallback in the last round; the field kept it.
        # Only on a rework attempt. A retry INSIDE a heal round kept the bound
        # round and rendered as "(rework round 1)", so the field stopped
        # identifying rework - which was the whole point of taking the
        # never-reset fallback off it.
        if heal_round and cause == "heal":
            event["heal_round"] = heal_round
        # No `parts` list. It named the hash parts, which `state.json` already
        # records as `hash_parts` - and it was UNBOUNDED: one key per matched
        # file under `spec.reads` produced a 7 KB event line per attempt, per
        # map item, in a file every cockpit surface reads whole.
        self._attempted.add(node.id)
        append_event(self.store.run_dir, event)

    def _corrective_prompt(self, node: Node, work: PlannedWork, previous_text: str | None, error: str) -> str:
        """Output-only corrective re-spawn (SPEC §9.3). A headless harness spawn
        is STATELESS — without the original task and the invalid output, the
        re-spawn has nothing to correct (found by the audit-spec dogfood run).
        "Output-only" constrains side effects, not context. Wording differs by
        mode: a readonly reviewer must NOT be told "your files are already
        written" — that invites it to imagine work it never did."""
        original = str(work.render) if isinstance(work.render, str) else json.dumps(work.render)
        previous = fence_block("previous.invalid.output", previous_text or "(empty — no result was produced)")
        if node.spec.get("readonly"):
            instruction = f"Emit only the corrected JSON for your previous analysis: {error}"
        else:
            instruction = (
                "Your files are already written. Do NOT modify, create, or delete any "
                f"file. Emit only the corrected JSON describing what you already did: {error}"
            )
        return (
            f"{original}\n\n---\n"
            "A previous attempt at this task produced output that failed contract "
            f"validation. The invalid output was:\n{previous}\n\n"
            f"{instruction}"
        )

    def _try_repair(
        self, node: Node, executor, phase_dir: Path, raw: RawResult, contract_ref, *,
        item_index: int | None = None,
    ) -> tuple[object, str] | None:
        """C2 (DEVIATIONS 2026-09-09): before spending the corrective re-spawn
        — on a request-metered harness, a second billed request — try a
        deterministic deletion-only repair of the bytes already in hand.
        Deletion-only because a synthesized closer would pass a truncated
        review as a clean one (F-E2). Never for gates: a verdict is the one
        result whose consumers act without a human re-reading raw bytes.
        Licensed by the same flag as the corrective (shell stays terminal on
        mismatch, AMENDMENTS A4). Returns (validated value, repaired text) or
        None — including when the pre-repair evidence cannot be preserved."""
        if node.role == "gate" or not getattr(executor, "supports_corrective_respawn", False):
            return None
        # Two candidate inputs, in order: the channel's own pick, then — FILE
        # channel only — the raw result-file bytes. The real executor salvages
        # a non-JSON file down to its last balanced inner value (E2) before
        # validation, so a dangling-comma file arrives here as `[]` and only
        # the raw bytes can be repaired. The stdout channel gets no such
        # fallback: raw stdout is narration, and a narrated example object
        # that happens to validate must never be adopted as the result.
        candidates = [raw.result_text or ""]
        if raw.source == "file":
            file_text = self._raw_channel_text(phase_dir, raw)
            if file_text is not None and file_text not in candidates:
                candidates.append(file_text)
        repaired_text = None
        for candidate in candidates:
            # File channel: single-value posture (see repair_json) — a
            # multi-value result file goes to the corrective, never to a
            # longest-value pick that could displace the real answer.
            attempt = repair_json(candidate, single_value=raw.source == "file")
            if attempt is None:
                continue
            try:
                value = validate_result(attempt[0], contract_ref)
            except ContractError:
                continue
            repaired_text, deletions = attempt
            break
        if repaired_text is None:
            return None
        # Rotation-first is an evidence obligation (F-E3/F-S7): on the file
        # channel the raw bytes exist ONLY in the result file, rotation
        # normally happens at the NEXT execute (which repair's whole point is
        # to avoid), and write_result would clobber them. A rotation that
        # cannot happen refuses the repair — the corrective decides instead.
        rotated = self._rotate_invalid_result(phase_dir, raw)
        if raw.source == "file" and rotated is None:
            return None
        label = node.id if item_index is None else f"{node.id}[{item_index}]"
        append_event(self.store.run_dir, {
            "kind": "repair", "node": label, "rotated": rotated, "deleted": deletions,
        })
        self.log(f"[{label}] invalid output accepted after deletion-only repair "
                 f"(no re-spawn): {'; '.join(deletions)}")
        return value, repaired_text

    @staticmethod
    def _rotate_invalid_result(phase_dir: Path, raw: RawResult) -> str | None:
        """Rotate the invalid result file to the next -attemptN name (the
        executors' own rotation scheme). None when there is nothing to rotate
        (stdout channel: the raw bytes live untouched in stdout.log) or when
        rotation failed."""
        if raw.source != "file":
            return None
        for name in ("result.json", "result.txt"):
            p = phase_dir / name
            if not p.exists():
                continue
            n = 1
            while (phase_dir / f"{p.stem}-attempt{n}{p.suffix}").exists():
                n += 1
            target = phase_dir / f"{p.stem}-attempt{n}{p.suffix}"
            for _ in range(2):  # this machine's AV: one retry on a transient denial
                try:
                    p.rename(target)
                    return target.name
                except OSError:
                    time.sleep(0.1)
            return None
        return None

    @staticmethod
    def _raw_channel_text(phase_dir: Path, raw: RawResult) -> str | None:
        """The result channel's RAW bytes (§8.3): the result file for the file
        channel, stdout for the fallback — what extraction salvaged FROM,
        which one corrupted token can make longer and more useful than what
        it salvaged (C3)."""
        paths = []
        if raw.source == "file":
            paths = [phase_dir / "result.json", phase_dir / "result.txt"]
        elif raw.stdout_path:
            paths = [Path(raw.stdout_path)]
        for p in paths:
            try:
                if p.exists():
                    return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return None

    def _corrective_fence_text(self, phase_dir: Path, raw: RawResult) -> str | None:
        """C3 (ROADMAP-NOTES 2026-08-15, chronicle forensics): when the raw
        channel holds a longer near-object than the salvaged value — one
        corrupted token collapsed extraction to the last balanced INNER value
        — fence the model's own truncated output so it corrects instead of
        re-deriving. Capped at max_interp_chars; §7 fencing is applied by
        _corrective_prompt as always."""
        fence_text = raw.result_text
        src = self._raw_channel_text(phase_dir, raw)
        if src:
            near = longest_near_object(src)
            if near is not None and len(near) > len(fence_text or ""):
                fence_text = near[: self.tg.max_interp_chars]
        return fence_text

    def _validate_with_respawn(
        self, node: Node, executor, work: PlannedWork, phase_dir: Path, raw: RawResult
    ) -> tuple[object, str] | None:
        """Contract-validate; on failure, exactly one output-only corrective
        re-spawn for executors that support it (harness-kind mechanism; shell is
        deterministic and gets none — AMENDMENTS A4). Returns (value, text) or
        None after final failure (error recorded on the record)."""
        ref = resolve_contract(node.contract, self.tg.contracts_module)
        rec = self._rec(node.id)
        try:
            return validate_result(raw.result_text or "", ref), raw.result_text or ""
        except ContractError as e:
            first_error = str(e)
        repaired = self._try_repair(node, executor, phase_dir, raw, ref)
        if repaired is not None:
            rec.repaired = True
            return repaired
        if not getattr(executor, "supports_corrective_respawn", False):
            rec.error = f"contract validation failed: {first_error}"
            return None
        corrective = work.model_copy(
            update={
                "render": self._corrective_prompt(
                    node, work, self._corrective_fence_text(phase_dir, raw), first_error
                ),
                "meta": {**work.meta, "corrective": True},
            }
        )
        try:
            self._spend_spawn(corrective)
        except BudgetTripped:
            rec.error = f"contract validation failed: {first_error} (budget tripped before re-spawn)"
            raise
        self._journal_attempt(node, corrective, "corrective")
        raw2 = executor.execute(corrective, phase_dir, node.timeout_s)
        rec.attempts += 1
        if (phase_dir / "CANCELLED").exists():
            rec.error = "cancelled"  # r6 C3 also covers corrective re-spawns
            return None
        try:
            return validate_result(raw2.result_text or "", ref), raw2.result_text or ""
        except ContractError as e2:
            # If the re-spawn never RAN, that is the diagnosis. Reporting the
            # ContractError instead sends the operator hunting a schema bug in
            # a process that produced no output because it never started —
            # r5 A2's inflated corrective prompt makes this the likely failure
            # on argv-passed stanzas (ROADMAP-NOTES 2026-07-28, defect 2).
            rec.error = (
                f"corrective re-spawn did not run: {raw2.error}"
                if raw2.error
                else f"contract validation failed twice: {e2}"
            )
            return None

    def _finish(self, node: Node, executor, work: PlannedWork, phase_dir: Path, raw: RawResult) -> None:
        rec = self._rec(node.id)
        if raw.timed_out or raw.exit_code != 0 or raw.result_text is None:
            reason = raw.error or f"exit code {raw.exit_code}" + (" (no result emitted)" if raw.result_text is None else "")
            if raw.error and "provider limit/overload" in raw.error:
                # r5 B3: diagnosis only — tell the operator what to do.
                self.log(
                    f"[{node.id}] {raw.error}\n"
                    f"  wait for the limit/incident to clear, then: lockstep resume {self.store.run_dir}"
                )
            if raw.error == "cancelled":
                # r6 C3: cancellation is not a verdict — a cancelled gate fails
                # like any node; it restarts from a known input on resume.
                self._set_status(node.id, "failed", error="cancelled")
                return
            if node.role == "gate":
                # Fail-closed for termination, never a healing trigger (§9.4.3).
                # E6: NAME a timeout — "no valid verdict emitted" sent operators
                # hunting a schema bug in a command that simply ran out of
                # window, and heal's silence looked like a driver defect.
                if raw.timed_out:
                    reason = (
                        f"gate command timed out after {node.timeout_s}s — a timeout is "
                        f"not a verdict, so heal cannot fire (§9.4.3); raise timeout_s or "
                        f"add `retry` to the gate, and re-run the command by hand before "
                        f"blaming the change under review"
                    )
                else:
                    reason = "no valid verdict emitted"
                self._queue_gate_outcome(node, None, reason)
                return
            self._set_status(node.id, "failed", error=reason)
            return
        if node.output == "json":
            validated = self._validate_with_respawn(node, executor, work, phase_dir, raw)
            if validated is None:
                if rec.error == "cancelled":
                    self._set_status(node.id, "failed", error="cancelled")
                elif node.role == "gate":
                    self._queue_gate_outcome(node, None, "no valid verdict emitted")
                else:
                    self._set_status(node.id, "failed", error=rec.error)
                return
            value, text = validated
        else:
            value, text = None, raw.result_text
        if work.meta.get("_served_repaired"):
            # Round-2 finding 3: a served recording's bytes ARE the source
            # run's repaired bytes; the marker travels with them or the new
            # run's status/drawer present repaired output unmarked.
            rec.repaired = True
        if node.role == "gate":
            verdict = Verdict.model_validate(value)
            if self.store.state.baseline_findings.get(node.id):
                # E4: downstream {steps.<gate>.json...} references and `when`
                # conditions must read the ADJUDICATED verdict — a block→pass
                # flip that only reached state.verdicts would leave a
                # dependent's `== "pass"` check reading the raw "block". The
                # spawn's own raw output stays in the phase dir.
                verdict = self._apply_baseline(node, verdict)
                text = json.dumps(verdict.model_dump(), ensure_ascii=False)
            result_path = self.store.write_result(node.id, text, json_output=True)
            rec.result_path = str(result_path)
            self._record_fingerprint(rec)
            self._queue_gate_outcome(node, verdict, verdict.reason)
            return
        result_path = self.store.write_result(node.id, text, json_output=node.output == "json")
        rec.result_path = str(result_path)
        self._record_fingerprint(rec)
        self._set_status(node.id, "done")

    def _record_fingerprint(self, rec) -> None:
        digest, detail = self.workspace.fingerprint_detail()
        rec.workspace_fingerprint = digest

        def _upd(st):
            # Lineage head = most recently completed node (ties harmless, §9.2).
            st.fingerprint_detail = detail

        self.store.mutate(_upd)

    # ------------------------------------------------------------------ gates & heal

    def _queue_gate_outcome(self, node: Node, verdict: Verdict | None, reason: str) -> None:
        with self._outcomes_guard:
            self._gate_outcomes.append(_GateOutcome(node, verdict, reason))
        if verdict is not None and verdict.verdict == "pass":
            self.store.mutate(lambda st: st.verdicts.__setitem__(node.id, "pass"))
            self._set_status(node.id, "done")
        # blocks settle in _process_gate_outcomes (main thread, post-wave)

    def _process_gate_outcomes(self) -> None:
        with self._outcomes_guard:
            outcomes, self._gate_outcomes = self._gate_outcomes, []
        for oc in outcomes:
            if oc.verdict is not None and oc.verdict.verdict == "pass":
                # The heal cycle is over; a future re-run of the targets is a
                # NEW pre-attempt state — drop the old baseline.
                if oc.node.id in self.snapshots:
                    self.snapshots[oc.node.id] = None
                    self.store.mutate(lambda st: st.heal_baselines.pop(oc.node.id, None))
                continue
            gate = oc.node
            rec = self._rec(gate.id)
            valid_block = oc.verdict is not None
            self.store.mutate(lambda st: st.verdicts.__setitem__(gate.id, f"block: {oc.reason}"))
            can_heal = (
                valid_block  # §9.4.3: heal fires only on a VALID block
                and gate.heal.max_rounds > 0
                and rec.heal_round < gate.heal.max_rounds
                and bool(gate.heal.targets)
            )
            if not can_heal:
                # on_exhausted: "pass" applies ONLY to a valid block whose
                # rounds genuinely ran out — a timeout or malformed verdict is
                # not a block that exhausted, it is a gate that never decided
                # (§9.4.3), and it terminal-blocks whatever on_exhausted says.
                exhausted = (
                    valid_block
                    and gate.heal.max_rounds > 0
                    and rec.heal_round >= gate.heal.max_rounds
                    and bool(gate.heal.targets)
                )
                if exhausted and gate.heal.on_exhausted == "pass":
                    self._accept_exhausted(gate, rec, oc.verdict)
                else:
                    self._terminal_block(gate, oc.reason)
                continue
            self._heal(gate, rec, oc.verdict)

    def _terminal_block(self, gate: Node, reason: str) -> None:
        self._set_status(gate.id, "blocked", error=reason)
        self.flags["gate_block"] = True
        # Dependents blocked, reason recorded (SPEC §9.3); _settle also catches
        # transitive cases in later iterations.
        frontier = [gate.id]
        seen = set()
        while frontier:
            nid = frontier.pop()
            for dep_id in self._dependents[nid]:
                if dep_id in seen:
                    continue
                seen.add(dep_id)
                if self._rec(dep_id).status in ("pending", "running"):
                    self._set_status(dep_id, "blocked", error=f"gate {gate.id} blocked: {reason}")
                frontier.append(dep_id)

    def _accept_exhausted(self, gate: Node, rec, verdict: Verdict) -> None:
        """heal.on_exhausted: "pass" — rounds ran out and the gate still
        blocks; accept the best-so-far, but never as a plain pass
        (PROPOSAL-taskflow-parity-tiers 2.1, adopted 2026-08-13).

        Three consumers must all see the truth, and each has its own channel:
        the STORED result is rewritten (same route as the E4 baseline
        adjudication) so downstream `{steps.<gate>.json...}` references and
        `when` conditions read verdict "pass" with a reason naming what
        happened; `state.verdicts` gets the same reason so `status` never
        shows a gate that blocked as a gate that was satisfied; and the
        journal gets its own event so `verify-trace`'s record distinguishes
        an exhausted acceptance from a genuine pass. The unresolved findings
        STAY in the verdict — they are what was accepted."""
        rounds = rec.heal_round
        reason = (
            f"accepted after {rounds} round(s) without resolving: {verdict.reason}"
        )
        adjudicated = verdict.model_copy(update={"verdict": "pass", "reason": reason})
        text = json.dumps(adjudicated.model_dump(), ensure_ascii=False)
        rec.result_path = str(self.store.write_result(gate.id, text, json_output=True))
        self.store.mutate(lambda st: st.verdicts.__setitem__(gate.id, f"pass: {reason}"))
        # The heal cycle is over — drop the baseline exactly like a real pass,
        # or a later re-entry would restore to a tree from a finished cycle.
        if gate.id in self.snapshots:
            self.snapshots[gate.id] = None
            self.store.mutate(lambda st: st.heal_baselines.pop(gate.id, None))
        append_event(
            self.store.run_dir,
            {"node": gate.id, "status": "heal-exhausted-pass", "round": rounds,
             "reason": verdict.reason},
        )
        self._set_status(gate.id, "done")

    _ATTEMPT_NOTES_CAP = 4000  # tail-capped: the latest notes win

    def _heal_scope_line(self, nid: str) -> str:
        """The E5 boundary restatement for a heal target that declares a write
        scope. Empty when it declares none (nothing to restate)."""
        target = self.tg.node(nid)
        if "writes" not in target.spec:
            return ""
        scope = self._writes_of(target)
        allowed = ", ".join(scope) if scope else "nothing (this step declares writes: [])"
        return (
            "\nYour write scope is UNCHANGED by these findings: you may modify only "
            f"{allowed}. If a finding names a file outside that scope, do not edit it — "
            "state in your result that it is out of scope, whatever the finding says."
        )

    def _heal_carry_notes(self, nid: str) -> str:
        """E3: fold the target's own `attempt-notes.md` (phase dir, node-written)
        into its heal re-run prompt. A retried node otherwise re-derives from
        zero what a prior attempt spent real evidence establishing — heal
        rollback deliberately preserves the phase dir, but nothing fed it back."""
        notes = self.store.phase_dir(nid) / "attempt-notes.md"
        try:
            if not notes.exists():
                return ""
            text = notes.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        if len(text) > self._ATTEMPT_NOTES_CAP:
            text = text[-self._ATTEMPT_NOTES_CAP:]
        return (
            "\nYour own previous attempt left these notes — use them instead of "
            "re-deriving what it already established:\n"
            + fence_block("prior.attempt.notes", text)
        )

    def _heal(self, gate: Node, rec, verdict: Verdict) -> None:
        round_n = rec.heal_round + 1
        gate_phase = self.store.phase_dir(gate.id)
        if gate.heal.rollback:
            baseline = self.snapshots.get(gate.id)
            if baseline is None:
                # With persisted baselines (heal_baselines in RunState) this is
                # unreachable in any lineage whose targets ever executed — a
                # block-time snapshot would bless the bad attempt (§9.4.2), so
                # FAIL CLOSED rather than roll back to a wrong tree.
                self._terminal_block(
                    gate, "heal baseline missing — refusing a block-time snapshot (§9.4.2)"
                )
                return
            # Preserve the attempt, THEN restore (§9.4.4): the failed work stays
            # inspectable; scope is git-derived, never StepResult.files_written.
            # ONE snapshot for both answers (P1-perf). Each of these otherwise
            # walks and hashes the whole tree for itself, and the two would
            # describe two different moments: the preserved patch and the
            # restore scope must be the same tree, or a file written between
            # them is restored without appearing in the evidence.
            current = self._timed_ws(gate.id, "heal-current", self.workspace.snapshot)
            patch = self._timed_ws(gate.id, "heal-patch",
                                   lambda: self.workspace.diff_patch(baseline, current))
            (gate_phase / f"attempt-{round_n}.patch").write_text(patch, encoding="utf-8")
            # Same exclusion as the scope check, for the same reason and with a
            # sharper edge: a rollback that reverts the run dir would restore
            # `state.json` from under the engine mid-heal.
            scope = self._outside_run_dir(
                self._timed_ws(gate.id, "heal-diff",
                               lambda: self.workspace.changed_paths(baseline, current))
            )
            discard = gate_phase / f"discarded-{round_n}"
            self._timed_ws(gate.id, "heal-restore",
                           lambda: self.workspace.restore(baseline, scope, discard))
            for p in scope:
                # Label faithfully: created-since-baseline paths were MOVED
                # aside, not restored (audit r5 finding).
                label = "discarded" if (discard / p).exists() else "restored"
                append_event(self.store.run_dir, {"node": gate.id, "status": label, "path": p})
            # E8-interim (LESSONS-TO-MECHANISMS): rollback scope is everything
            # changed since the gate's baseline (§9.4.4) — including an
            # operator's out-of-band edit, silently undone on EVERY round. When
            # every target declares spec.writes, a restored path OUTSIDE their
            # union is exactly that case: say so loudly. (Narrowing the scope
            # itself is the r7 proposal; this is the warning until then.)
            all_declared = all(
                "writes" in self.tg.node(t).spec for t in gate.heal.targets
            )
            if all_declared:
                # Compare against EVERY node's declared scope, not only the
                # targets': the rollback window legitimately contains a
                # non-target sibling's in-scope writes (diamond graphs at
                # max_workers > 1), and naming those "an out-of-band edit"
                # accuses the operator of the graph's own work
                # (adversarial-review finding 7).
                any_scope: list[str] = []
                for n in self.tg.nodes:
                    if "writes" in n.spec:
                        any_scope.extend(self._writes_of(n))
                undeclared = [p for p in scope if not path_in_scope(p, any_scope)]
                for p in undeclared:
                    append_event(
                        self.store.run_dir,
                        {"node": gate.id, "status": "restored-undeclared", "path": p},
                    )
                if undeclared:
                    self.log(
                        f"WARNING: heal rollback of gate {gate.id!r} restored path(s) no "
                        f"target declares in spec.writes: {', '.join(undeclared)} — an "
                        f"out-of-band edit made mid-run was just undone (rollback restores "
                        f"everything changed since the gate's baseline, SPEC §9.4.4)"
                    )
            # Refresh the lineage head AFTER the restore mutated the tree, so a
            # crash-then-resume here doesn't misread the rollback as external
            # edits (audit r6.2: fail-safe but noisy).
            _, detail = self.workspace.fingerprint_detail()
            self.store.mutate(lambda st: setattr(st, "fingerprint_detail", detail))
        # Invalidation cascades to ALL completed descendants of the targets —
        # restoring the tree under a passed sibling would silently orphan its
        # outputs (SPEC §9.4.5, revision-3 loop C).
        invalid: set[str] = set(gate.heal.targets)
        frontier = list(gate.heal.targets)
        while frontier:
            nid = frontier.pop()
            for dep_id in self._dependents[nid]:
                if dep_id not in invalid:
                    invalid.add(dep_id)
                    frontier.append(dep_id)
        findings_json = json.dumps([f.model_dump() for f in verdict.findings], ensure_ascii=False)
        base_heal = (
            f"A quality gate blocked with: {verdict.reason}. Address this precisely.\n"
            # The round number rides in the engine-composed heal text — NOT a
            # {round} interpolation form: reference forms are a §7 surface, and
            # heal_texts already folds into both the prompt and the hash
            # (PROPOSAL-taskflow-parity-tiers 2.1, finding 17).
            f"This is repair round {round_n} of {gate.heal.max_rounds} for gate "
            f"'{gate.id}'.\n"
            + fence_block("gate.findings", findings_json)
        )
        for nid in sorted(invalid):
            nrec = self._rec(nid)
            if nid in gate.heal.targets:
                # E5 (LESSONS-TO-MECHANISMS): gate findings appended with
                # "address this precisely" read as authorization — a node whose
                # scope was two templates edited five core modules chasing a
                # gate-surfaced pre-existing failure. Restate the target's own
                # declared scope INSIDE the heal text, engine-composed so no
                # flow author has to remember a defensive clause. E3: carry the
                # target's own attempt notes forward for the same reason.
                heal_text = base_heal + self._heal_scope_line(nid) + self._heal_carry_notes(nid)
                # Folds into the prompt AND the hash, so it is persisted with the
                # run state rather than held in this process (r7 candidate,
                # ROADMAP-NOTES 2026-07-27).
                self.store.mutate(
                    lambda st, n=nid, t=heal_text: st.heal_texts.__setitem__(n, t)
                )
            if nrec.status in ("done", "skipped", "failed", "blocked") or nid in gate.heal.targets:
                # A heal round re-spawns the node: whatever it writes next is
                # model output, and an adoption pin surviving it would label
                # that output settled-by-adoption. The gate re-reviewed the
                # human's artifact and rejected it — the pin's work (consumers
                # ran unweakened against the human's bytes) is done.
                self._dissolve_adoption(nrec, f"heal round of gate {gate.id!r}")
                nrec.status = "pending"
                nrec.error = None
                # A3.4/A3.5: heal invalidation clears item records — for map
                # TARGETS (all items re-run, §9.4.6) and equally for invalidated
                # DESCENDANT maps: after a rollback, a descendant item whose
                # prompt doesn't reference the restored content could hash-match
                # and wrongly skip. (Caught by the audit-spec arbiter gate.)
                if self.tg.node(nid).role == "map":
                    # Keep each item's ATTEMPT COUNTER, clear everything else.
                    # A work node's counter never resets across heal rounds,
                    # which is what makes `out-of-scope-<n>` / `touched-<n>`
                    # names unique; a reset here let heal round 1's item
                    # quarantine overwrite round 0's preserved attempt
                    # (adversarial review 2026-09-19, finding 1).
                    nrec.items = {
                        k: ItemRecord(attempts=v.attempts) for k, v in nrec.items.items()
                    }
                self.store.mutate(
                    lambda st, _nid=nid, _r=round_n: st.heal_pending.__setitem__(_nid, _r))
                self.store.record(nrec)
                self.needs_check.discard(nid)
        rec.heal_round = round_n
        rec.status = "pending"
        self.store.mutate(
            lambda st, _g=gate.id, _r=round_n: st.heal_pending.__setitem__(_g, _r))
        self.store.record(rec)
        append_event(self.store.run_dir, {"node": gate.id, "status": "heal-round", "round": round_n})

    # ------------------------------------------------------------------ map

    def _resolve_over(self, node: Node):
        assert node.over
        ref = node.over.strip()[1:-1]  # verified shape: {steps.X.json...}
        from .interpolate import resolve_ref

        value, _ = resolve_ref(ref, self._resolve_ctx(node))
        if not isinstance(value, list):
            raise InterpolationError(f"node {node.id!r}: `over` did not resolve to a JSON array")
        return value

    def _map_parts(self, node: Node, array) -> list[str]:
        return [
            f"over:{compact_json(array)}",
            f"spec:{json.dumps(node.spec, sort_keys=True, ensure_ascii=False)}",
        ]

    def _map_node_hash(self, node: Node, array) -> str:
        return compose_hash(node.role, node.kind, node.contract, self._map_parts(node, array))

    def _run_map(self, node: Node) -> None:
        executor = self.registry.get(node.kind)
        try:
            array = self._resolve_over(node)
        except SkippedReference:
            self._set_status(node.id, "skipped")
            return
        except InterpolationError as e:
            self._set_status(node.id, "failed", error=str(e))
            return
        rec = self._rec(node.id)
        rec.input_hash = self._map_node_hash(node, array)
        rec.hash_parts = label_parts(self._map_parts(node, array))
        self.store.record(rec)
        contract_ref = resolve_contract(node.contract, self.tg.contracts_module) if node.output == "json" and node.contract else None
        slots: list = [None] * len(array)
        errors: dict[int, str] = {}
        items_guard = threading.Lock()
        budget_hit = threading.Event()

        def run_item(i: int, item) -> None:
            try:
                _run_item_inner(i, item)
            except BudgetTripped:
                budget_hit.set()
                with items_guard:
                    irec = rec.items.get(str(i))
                    if irec is not None and irec.status == "running":
                        irec.status = "pending"
                        # As the single-node path does: a pending item with a
                        # stale "budget tripped" error string reads as failed.
                        irec.error = None
            except Exception as e:
                with items_guard:
                    irec = rec.items.get(str(i)) or ItemRecord()
                    rec.items[str(i)] = irec
                    irec.status = "failed"
                    irec.error = f"{type(e).__name__}: {e}"
                    errors[i] = irec.error

        # A map target re-pended by a cascade: consume the signal once here,
        # for the whole fan-out. Items go through `_item_execute`, which never
        # saw `heal_pending` - so healed items journalled `initial`, identical
        # to round 0's, and the map's entry was popped by nobody.
        map_heal_round = self._peek_heal_round(node.id)
        # Presence-keyed like `_run_node` (V1): `writes: []` on a map means
        # every item writes nothing. Resolved ONCE here through `_writes_of`,
        # the one reader of a declared scope.
        map_has_scope = "writes" in node.spec
        map_scope = self._writes_of(node)

        def _run_item_inner(i: int, item) -> None:
            with items_guard:
                irec = rec.items.get(str(i)) or ItemRecord()
                rec.items[str(i)] = irec
            phase_dir = self.store.phase_dir(node.id, item_index=i)
            ctx = self._render_ctx(node, phase_dir, item=item, has_item=True)
            try:
                work = executor.plan(node, ctx)
            except SkippedReference:
                irec.status = "skipped"
                return
            item_hash = compose_hash(
                node.role, node.kind, node.contract, work.fingerprint_parts + [f"index:{i}"]
            )
            # Per-item resume (AMENDMENTS A3): done + matching hash ⇒ reuse.
            if (
                irec.status == "done"
                and getattr(executor, "cacheable", False)
                and irec.input_hash == item_hash
                and irec.result_path
                and Path(irec.result_path).exists()
            ):
                text = Path(irec.result_path).read_text(encoding="utf-8")
                slots[i] = json.loads(text) if node.output == "json" else text
                return
            irec.status = "running"
            irec.input_hash = item_hash
            irec.hash_parts = label_parts(
                work.fingerprint_parts + [f"index:{i}"], work.meta.get("hash_detail")
            )
            self.store.record(rec)
            tokens = sorted(set(node.exclusive) | set(work.exclusive))
            locks = self._acquire(tokens)  # items inherit the node's tokens:
            # Per-item write scope (2026-09-19): the map's ONE declared scope,
            # checked per item against a baseline taken for THAT item inside
            # the token — the same sequence `_run_node` runs, for the same
            # reason it runs inside the token there. Before this, a map could
            # not declare a scope at all (`write-scope-on-map`): the items
            # shared one diff, and the class was the one quarantine could not
            # guard (ROADMAP 2026-08-12).
            label = f"{node.id}[{i}]"
            scope_ref = None
            scope_error: str | None = None
            staged_before: set[str] = set()
            try:  # a tree-mutating map is inherently serial (SPEC §9.3)
                self._maybe_snapshot(node)
                if map_has_scope and "tree" in tokens:
                    scope_ref = self._scope_baseline(node, label)
                    if scope_ref is not None:
                        staged_before = self.workspace.staged_paths()
                        irec.tree_before = scope_ref.ref
                        irec.tree_after = None  # the pair describes ONE attempt
                        self.store.record(rec)
                raw = self._item_execute(node, executor, work, phase_dir, irec, i,
                                         heal_round=map_heal_round)
                if scope_ref is not None and raw is not None:
                    after = self._after_snapshot(node, label)
                    in_scope, violations = self._scope_changes(
                        scope_ref, map_scope, label=label, current=after
                    )
                    if violations:
                        scope_error, clean = self._quarantine(
                            node, phase_dir, map_scope, scope_ref, in_scope, violations,
                            staged_before, after, item=(i, irec),
                        )
                        if raw.error == "cancelled":
                            scope_error = "cancelled\n" + scope_error  # r6 C3, as above
                        elif clean and getattr(executor, "supports_corrective_respawn", False):
                            raw, scope_error = self._scope_corrective(
                                node, executor, work, phase_dir, map_scope, scope_ref,
                                staged_before, scope_error, item=(i, irec),
                            )
                    elif not raw.timed_out and raw.exit_code == 0 and raw.result_text is not None:
                        if after is not None:
                            irec.tree_after = after.ref
                        self._record_touched(node, phase_dir, in_scope, item=(i, irec))
            finally:
                self._release(locks)
            if scope_error is not None:
                irec.status = "failed"
                irec.error = scope_error
                errors[i] = scope_error
                self.store.record(rec)
                return
            ok = raw is not None and not raw.timed_out and raw.exit_code == 0 and raw.result_text is not None
            if not ok:
                irec.status = "failed"
                irec.error = (raw.error if raw else None) or "item execution failed"
                errors[i] = irec.error
                self.store.record(rec)
                return
            text = raw.result_text
            if contract_ref is not None:
                try:
                    validate_result(text, contract_ref)
                except ContractError as e:
                    repaired = self._try_repair(
                        node, executor, phase_dir, raw, contract_ref, item_index=i
                    )
                    if repaired is not None:
                        irec.repaired = True
                        text = repaired[1]
                    elif getattr(executor, "supports_corrective_respawn", False):
                        corrective = work.model_copy(
                            update={
                                "render": self._corrective_prompt(
                                    node, work, self._corrective_fence_text(phase_dir, raw), str(e)
                                ),
                                "meta": {**work.meta, "corrective": True},
                            }
                        )
                        self._spend_spawn(corrective)
                        self._journal_attempt(node, corrective, "corrective",
                                              item_index=i, ordinal=irec.attempts + 1)
                        raw2 = executor.execute(corrective, phase_dir, node.timeout_s)
                        irec.attempts += 1
                        if (phase_dir / "CANCELLED").exists():
                            irec.status = "failed"
                            irec.error = errors[i] = "cancelled"
                            self.store.record(rec)
                            return
                        try:
                            validate_result(raw2.result_text or "", contract_ref)
                            text = raw2.result_text or ""
                        except ContractError as e2:
                            irec.status = "failed"
                            # Same masking fix as the single-node path above.
                            irec.error = (
                                f"corrective re-spawn did not run: {raw2.error}"
                                if raw2.error
                                else f"contract validation failed twice: {e2}"
                            )
                            errors[i] = irec.error
                            self.store.record(rec)
                            return
                    else:
                        irec.status = "failed"
                        irec.error = f"contract validation failed: {e}"
                        errors[i] = irec.error
                        self.store.record(rec)
                        return
            if work.meta.get("_served_repaired"):
                irec.repaired = True  # replayed items serve repaired bytes too
            path = self.store.write_result(node.id, text, json_output=node.output == "json", item_index=i)
            irec.result_path = str(path)
            irec.status = "done"
            slots[i] = json.loads(text) if node.output == "json" else text
            self.store.record(rec)

        conc = node.concurrency if node.concurrency is not None else self.tg.concurrency
        if conc <= 1 or len(array) <= 1:
            # concurrency: 1 guarantees array-order sequential execution (§9.3)
            for i, item in enumerate(array):
                run_item(i, item)
                if budget_hit.is_set():
                    break
        else:
            with ThreadPoolExecutor(max_workers=min(conc, max(len(array), 1))) as pool:
                futures_wait([pool.submit(run_item, i, item) for i, item in enumerate(array)])
        if budget_hit.is_set():
            rec.status = "pending"
            self.store.record(rec)
            raise BudgetTripped()
        if errors and not node.optional:
            first = min(errors)
            self._set_status(node.id, "failed", error=f"item {first} failed: {errors[first]}")
            return
        if errors:
            from .contracts import StepResult

            for i in errors:
                slots[i] = StepResult(
                    step_id=f"{node.id}[{i}]", status="failed", files_written=[], notes=errors[i]
                ).model_dump()
        result_text = json.dumps(slots, ensure_ascii=False)
        path = self.store.write_result(node.id, result_text, json_output=True)
        rec.result_path = str(path)
        self._record_fingerprint(rec)
        # The map's heal signal is consumed HERE - when the whole fan-out has
        # finished - not by the first item. Consuming at item 0 meant a budget
        # trip at item 51 of 200 left nothing, and the resume brought items
        # 51..200 back labelled `initial`: the same rework round, half of it
        # reported as a first attempt. The signal has to outlive every item
        # that still has to run.
        self._take_heal_round(node.id)
        self._set_status(node.id, "done")

    def _item_execute(self, node: Node, executor, work: PlannedWork, phase_dir: Path,
                      irec: ItemRecord, item_index: int | None = None,
                      heal_round: int | None = None) -> RawResult | None:
        irec.repaired = False  # C2: same reset rule as _execute_with_retries;
        # the per-item reuse path returns before reaching here, so a kept
        # item's flag persists with its kept bytes.
        retry = self._effective_retry(node, executor, work)
        retries_left = retry.max
        backoff_s = retry.backoff_ms / 1000.0
        auto_used = False
        (phase_dir / "CANCELLED").unlink(missing_ok=True)  # r6 C3 stale marker
        mark_mailbox_consumed(self.store.run_dir, node.id)  # r6 C2 bookkeeping
        if self._served(work):
            cause = "served"
        elif heal_round is not None:
            cause = "heal"
        else:
            cause = "resume" if irec.attempts else "initial"
        while True:
            self._spend_spawn(work)     # see _execute_with_retries: spend first
            self._journal_attempt(node, work, cause, item_index=item_index,
                                  ordinal=irec.attempts + 1, heal_round=heal_round)
            raw = executor.execute(work, phase_dir, node.timeout_s)
            irec.attempts += 1
            if (phase_dir / "CANCELLED").exists():
                raw.error = "cancelled"
                return raw
            ok = (not raw.timed_out) and raw.exit_code == 0 and raw.result_text is not None
            if ok:
                return raw
            if (raw.timed_out or raw.result_text is None) and not auto_used:
                auto_used = True
                cause = "auto-retry"
                continue
            if retries_left > 0 and (raw.exit_code != 0 or raw.timed_out):
                retries_left -= 1
                cause = "retry"
                time.sleep(backoff_s)
                backoff_s *= retry.factor
                continue
            return raw

    # ------------------------------------------------------------------ approval

    def _run_approval(self, node: Node) -> None:
        """Core-handled, no executor (SPEC §9.3). Non-TTY stdin ⇒ auto-reject,
        exit 6. Never resume-skipped.

        Cockpit mode (T1.3) narrows the accepted answers to a/r. `e` exists so an
        OPERATOR can substitute an approval's result text — a coherent thing for
        an operator to want, and an incoherent thing to offer a non-programmer
        who has been told in two places never to use it. The flag makes the
        DE-facing surface match the DE-facing documentation by construction
        instead of by warning; nothing about it changes what a run can do,
        because a cockpit human who wants to say something types `r` and says it.
        """
        if not (sys.stdin and sys.stdin.isatty()):
            self.flags["approval_rejected"] = True
            self._set_status(node.id, "blocked", error="approval auto-rejected (non-TTY stdin)")
            return
        prompt = (
            f"[approval:{node.id}] [a]pprove / [r]eject: "
            if self.cockpit
            else f"[approval:{node.id}] [a]pprove / [r]eject / [e]dit: "
        )
        while True:
            try:
                answer = input(prompt).strip().lower()
            except EOFError:
                # NOBODY WAS THERE — a different fact from "the human said no",
                # and the record has to be able to tell them apart.
                #
                # Found 2026-08-03: on Windows `NUL` is a CHARACTER DEVICE, so
                # `sys.stdin.isatty()` returns True for the cockpit's own
                # documented launch idiom (`lockstep run <flow> < NUL`). The
                # isatty guard above therefore does NOT fire for it; execution
                # reaches here and EOFs on the first read. The OUTCOME was
                # already correct (reject, exit 6) and an orchestrator still
                # cannot approve — writing to that stdin means a pipe, and a
                # pipe is not a character device, so the isatty guard fires —
                # but the run was recorded as "approval rejected", which reads
                # as a person having decided.
                self.flags["approval_rejected"] = True
                self._set_status(
                    node.id, "blocked",
                    error="approval auto-rejected (no answer available on stdin)",
                )
                return
            if answer in ("a", "approve"):
                text = "approved"
                break
            if answer in ("r", "reject"):
                self.flags["approval_rejected"] = True
                self._set_status(node.id, "blocked", error="approval rejected")
                return
            if self.cockpit:
                # Say what to do instead, every time. A prompt that silently
                # re-asks reads as a frozen terminal to someone who does not
                # know they typed something it does not take.
                if answer:
                    self.log("Only a (approve) or r (reject). Type r if something is "
                             "wrong - you will be asked what, in one line.")
                continue
            if answer in ("e", "edit"):
                self.log("Enter text; end with EOF (Ctrl-Z then Enter on Windows, Ctrl-D elsewhere):")
                lines: list[str] = []
                while True:
                    try:
                        lines.append(input())
                    except EOFError:
                        break
                text = "\n".join(lines)
                break
        path = self.store.write_result(node.id, text, json_output=False)
        rec = self._rec(node.id)
        rec.result_path = str(path)
        self._record_fingerprint(rec)
        self._set_status(node.id, "done")
