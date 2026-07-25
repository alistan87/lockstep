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
from .state import ItemRecord, append_event, compose_hash, utcnow
from .store import FileStore
from .taskgraph import Node, TaskGraph
from .workspace import GitWorkspace, WorkspaceError

SETTLED = {"done", "skipped"}
TERMINAL_BAD = {"failed", "blocked"}


class BudgetTripped(Exception):
    pass


class RunRefusal(Exception):
    """Run-time refusal (exit 7), e.g. heal.rollback on a non-git tree."""


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

        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._budget_guard = threading.Lock()
        self._snapshot_guard = threading.Lock()
        self.needs_check: set[str] = set()
        self.heal_texts: dict[str, str] = {}
        self._gate_outcomes: list[_GateOutcome] = []
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

        self._dependents: dict[str, list[str]] = {n.id: [] for n in tg.nodes}
        for n in tg.nodes:
            for dep in n.depends_on:
                self._dependents[dep].append(n.id)

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
        tokens — corrective re-spawns and heal rounds included."""
        if not work.costs_tokens:
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
            heal_text=self.heal_texts.get(node.id, ""),
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
        for node in self.tg.nodes:
            rec = st.nodes[node.id]
            if rec.status in ("running", "failed", "blocked"):
                rec.status = "pending"
                rec.error = None
            elif rec.status == "done":
                if node.role == "approval":
                    rec.status = "pending"  # never skipped (SPEC §9.3)
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
                        and ex is not None
                        and getattr(ex, "cacheable", False)
                        and any(st.nodes[d].status != "done" for d in self._dependents[node.id])
                    ):
                        rec.status = "pending"
                        self.needs_check.discard(node.id)
        self.store.mutate(lambda s: None)  # persist

    # ------------------------------------------------------------------ settle

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
                    if not all(r.status in SETTLED for r in dep_recs.values()):
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
                    if not all(r.status in SETTLED for r in dep_recs.values()):
                        continue
                    executor = self.registry.get(node.kind)
                    invalidate = executor is None or not getattr(executor, "cacheable", False)
                    if not invalidate:
                        if node.role == "map":
                            # A done map always re-enters _run_map: the node-level
                            # hash cannot see upstream refs inside the item body;
                            # PER-ITEM hashes (which render the body) do the
                            # caching, so this costs nothing for unchanged items.
                            invalidate = True
                        else:
                            try:
                                ctx = self._render_ctx(node, self.store.phase_dir(node.id))
                                work = executor.plan(node, ctx)
                                new_hash = compose_hash(
                                    node.role, node.kind, node.contract, work.fingerprint_parts
                                )
                                invalidate = new_hash != rec.input_hash
                            except (SkippedReference, InterpolationError, Exception):
                                invalidate = True
                    self.needs_check.discard(node.id)
                    if invalidate:
                        rec.status = "pending"
                        self.store.record(rec)
                    else:
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
        self._start_monotonic = time.monotonic()
        token_pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="lockstep-tok")
        other_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lockstep-oth")
        try:
            while True:
                self._settle()
                if self.flags["budget"]:
                    break
                if self._wall_exceeded():
                    self.flags["budget"] = True
                    break
                wave = [
                    n
                    for n in self.tg.nodes
                    if self._rec(n.id).status == "pending"
                    and all(self._rec(d).status in SETTLED for d in n.depends_on)
                ]
                if not wave:
                    break
                futures = []
                for node in wave:
                    self._set_status(node.id, "running")
                    pool = token_pool if self._costs_tokens_hint(node) else other_pool
                    futures.append(pool.submit(self._run_node_safe, node))
                futures_wait(futures)
                self._process_gate_outcomes()
        finally:
            token_pool.shutdown(wait=True)
            other_pool.shutdown(wait=True)
            self.store.mutate(lambda s: None)
        return self._exit_code()

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

    def _run_node_safe(self, node: Node) -> None:
        try:
            self._run_node(node)
        except BudgetTripped:
            # No new spawns; this node goes back to pending; in-flight peers finish.
            rec = self._rec(node.id)
            rec.status = "pending"
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
        self.store.record(rec)
        decision = self.policy.allows(node, ACTOR_LOCAL_USER)
        if not decision.allowed:
            self._set_status(node.id, "failed", error=f"policy denied: {decision.reason}")
            return
        if isinstance(work.render, str):
            pass  # prompt.txt written by the executor at execute time
        tokens = sorted(set(node.exclusive) | set(work.exclusive))
        locks = self._acquire(tokens)
        try:
            self._maybe_snapshot(node)
            raw = self._execute_with_retries(node, executor, work, phase_dir)
        finally:
            self._release(locks)
        self._finish(node, executor, work, phase_dir, raw)

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
                self.snapshots[gate_id] = self.workspace.snapshot()
                append_event(
                    self.store.run_dir,
                    {"node": gate_id, "status": "snapshot", "error": None, "ref": self.snapshots[gate_id].ref},
                )

    @staticmethod
    def _effective_retry(node: Node, executor):
        """AMENDMENTS-r5 B2: a node that sets `retry` in the flow file (field
        present, even {"max": 0}) uses it verbatim; otherwise the executor's
        kind-level default_retry (harness: 2 × minute-scale backoff for
        transient 429/529s); otherwise the model default."""
        if "retry" in node.model_fields_set:
            return node.retry
        return getattr(executor, "default_retry", None) or node.retry

    def _execute_with_retries(self, node: Node, executor, work: PlannedWork, phase_dir: Path) -> RawResult:
        """RetrySpec covers nonzero exits and timeouts with backoff; PLUS one
        automatic retry on timeout or empty result, additive, even when
        retry.max == 0 (SPEC §9.3, AMENDMENTS M4)."""
        rec = self._rec(node.id)
        retry = self._effective_retry(node, executor)
        retries_left = retry.max
        backoff_s = retry.backoff_ms / 1000.0
        auto_used = False
        while True:
            self._spend_spawn(work)
            raw = executor.execute(work, phase_dir, node.timeout_s)
            rec.attempts += 1
            self.store.record(rec)
            ok = (not raw.timed_out) and raw.exit_code == 0 and raw.result_text is not None
            if ok:
                return raw
            if (raw.timed_out or raw.result_text is None) and not auto_used:
                auto_used = True
                continue
            if retries_left > 0 and (raw.exit_code != 0 or raw.timed_out):
                retries_left -= 1
                time.sleep(backoff_s)
                backoff_s *= retry.factor
                continue
            return raw

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
        if not getattr(executor, "supports_corrective_respawn", False):
            rec.error = f"contract validation failed: {first_error}"
            return None
        corrective = work.model_copy(
            update={
                "render": self._corrective_prompt(node, work, raw.result_text, first_error),
                "meta": {**work.meta, "corrective": True},
            }
        )
        try:
            self._spend_spawn(corrective)
        except BudgetTripped:
            rec.error = f"contract validation failed: {first_error} (budget tripped before re-spawn)"
            raise
        raw2 = executor.execute(corrective, phase_dir, node.timeout_s)
        rec.attempts += 1
        try:
            return validate_result(raw2.result_text or "", ref), raw2.result_text or ""
        except ContractError as e2:
            rec.error = f"contract validation failed twice: {e2}"
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
            if node.role == "gate":
                # Fail-closed for termination, never a healing trigger (§9.4.3).
                self._queue_gate_outcome(node, None, "no valid verdict emitted")
                return
            self._set_status(node.id, "failed", error=reason)
            return
        if node.output == "json":
            validated = self._validate_with_respawn(node, executor, work, phase_dir, raw)
            if validated is None:
                if node.role == "gate":
                    self._queue_gate_outcome(node, None, "no valid verdict emitted")
                else:
                    self._set_status(node.id, "failed", error=rec.error)
                return
            value, text = validated
        else:
            value, text = None, raw.result_text
        result_path = self.store.write_result(node.id, text, json_output=node.output == "json")
        rec.result_path = str(result_path)
        self._record_fingerprint(rec)
        if node.role == "gate":
            verdict = Verdict.model_validate(value)
            self._queue_gate_outcome(node, verdict, verdict.reason)
            return
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

    def _heal(self, gate: Node, rec, verdict: Verdict) -> None:
        round_n = rec.heal_round + 1
        gate_phase = self.store.phase_dir(gate.id)
        if gate.heal.rollback:
            baseline = self.snapshots.get(gate.id)
            if baseline is None:
                # Targets all hash-skipped this session; the current tree IS the
                # pre-attempt state for the coming round.
                baseline = self.workspace.snapshot()
                self.snapshots[gate.id] = baseline
            # Preserve the attempt, THEN restore (§9.4.4): the failed work stays
            # inspectable; scope is git-derived, never StepResult.files_written.
            patch = self.workspace.diff_patch(baseline)
            (gate_phase / f"attempt-{round_n}.patch").write_text(patch, encoding="utf-8")
            scope = self.workspace.changed_paths(baseline)
            discard = gate_phase / f"discarded-{round_n}"
            self.workspace.restore(baseline, scope, discard)
            for p in scope:
                append_event(self.store.run_dir, {"node": gate.id, "status": "restored", "path": p})
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
        heal_text = (
            f"A quality gate blocked with: {verdict.reason}. Address this precisely.\n"
            + fence_block("gate.findings", findings_json)
        )
        for nid in sorted(invalid):
            nrec = self._rec(nid)
            if nid in gate.heal.targets:
                self.heal_texts[nid] = heal_text  # folds into the prompt AND the hash
            if nrec.status in ("done", "skipped", "failed", "blocked") or nid in gate.heal.targets:
                nrec.status = "pending"
                nrec.error = None
                # A3.4/A3.5: heal invalidation clears item records — for map
                # TARGETS (all items re-run, §9.4.6) and equally for invalidated
                # DESCENDANT maps: after a rollback, a descendant item whose
                # prompt doesn't reference the restored content could hash-match
                # and wrongly skip. (Caught by the audit-spec arbiter gate.)
                if self.tg.node(nid).role == "map":
                    nrec.items = {}
                self.store.record(nrec)
                self.needs_check.discard(nid)
        rec.heal_round = round_n
        rec.status = "pending"
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

    def _map_node_hash(self, node: Node, array) -> str:
        return compose_hash(
            node.role,
            node.kind,
            node.contract,
            [
                f"over:{compact_json(array)}",
                f"spec:{json.dumps(node.spec, sort_keys=True, ensure_ascii=False)}",
            ],
        )

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
            except Exception as e:
                with items_guard:
                    irec = rec.items.get(str(i)) or ItemRecord()
                    rec.items[str(i)] = irec
                    irec.status = "failed"
                    irec.error = f"{type(e).__name__}: {e}"
                    errors[i] = irec.error

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
            self.store.record(rec)
            tokens = sorted(set(node.exclusive) | set(work.exclusive))
            locks = self._acquire(tokens)  # items inherit the node's tokens:
            try:  # a tree-mutating map is inherently serial (SPEC §9.3)
                self._maybe_snapshot(node)
                raw = self._item_execute(node, executor, work, phase_dir, irec)
            finally:
                self._release(locks)
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
                    if getattr(executor, "supports_corrective_respawn", False):
                        corrective = work.model_copy(
                            update={
                                "render": self._corrective_prompt(node, work, text, str(e)),
                                "meta": {**work.meta, "corrective": True},
                            }
                        )
                        self._spend_spawn(corrective)
                        raw2 = executor.execute(corrective, phase_dir, node.timeout_s)
                        irec.attempts += 1
                        try:
                            validate_result(raw2.result_text or "", contract_ref)
                            text = raw2.result_text or ""
                        except ContractError as e2:
                            irec.status = "failed"
                            irec.error = f"contract validation failed twice: {e2}"
                            errors[i] = irec.error
                            self.store.record(rec)
                            return
                    else:
                        irec.status = "failed"
                        irec.error = f"contract validation failed: {e}"
                        errors[i] = irec.error
                        self.store.record(rec)
                        return
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
        self._set_status(node.id, "done")

    def _item_execute(self, node: Node, executor, work: PlannedWork, phase_dir: Path, irec: ItemRecord) -> RawResult | None:
        retry = self._effective_retry(node, executor)
        retries_left = retry.max
        backoff_s = retry.backoff_ms / 1000.0
        auto_used = False
        while True:
            self._spend_spawn(work)
            raw = executor.execute(work, phase_dir, node.timeout_s)
            irec.attempts += 1
            ok = (not raw.timed_out) and raw.exit_code == 0 and raw.result_text is not None
            if ok:
                return raw
            if (raw.timed_out or raw.result_text is None) and not auto_used:
                auto_used = True
                continue
            if retries_left > 0 and (raw.exit_code != 0 or raw.timed_out):
                retries_left -= 1
                time.sleep(backoff_s)
                backoff_s *= retry.factor
                continue
            return raw

    # ------------------------------------------------------------------ approval

    def _run_approval(self, node: Node) -> None:
        """Core-handled, no executor (SPEC §9.3). Non-TTY stdin ⇒ auto-reject,
        exit 6. Never resume-skipped."""
        if not (sys.stdin and sys.stdin.isatty()):
            self.flags["approval_rejected"] = True
            self._set_status(node.id, "blocked", error="approval auto-rejected (non-TTY stdin)")
            return
        prompt = f"[approval:{node.id}] [a]pprove / [r]eject / [e]dit: "
        while True:
            try:
                answer = input(prompt).strip().lower()
            except EOFError:
                answer = "r"
            if answer in ("a", "approve"):
                text = "approved"
                break
            if answer in ("r", "reject"):
                self.flags["approval_rejected"] = True
                self._set_status(node.id, "blocked", error="approval rejected")
                return
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
