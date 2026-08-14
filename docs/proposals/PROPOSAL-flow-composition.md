---
type: proposal
title: "Proposal: flow composition — run a saved flow as one node (kind: \"flow\")"
description: The successor to PROPOSAL-taskflow-parity-tiers §2.2, written because its third-pass review (findings 24–26) found three engine questions the sketch never answered. This proposal answers them — a shared worker semaphore, a RunResources seam bound to executors at engine start, and resume-as-child-resume via a hash-named child run dir — plus the two findings that were already right (4: the token rule; 21: gc invisibility by construction). Not adopted; adoption is a commit that says so.
resource: docs/proposals/PROPOSAL-flow-composition.md
status: draft
---
# Proposal: flow composition (`kind: "flow"`)

**Status: draft, not adopted.** A proposal carries no authority; adoption is a
commit that says so. This document supersedes PROPOSAL-taskflow-parity-tiers
§2.2, which was deliberately NOT adopted with the rest of that plan: its
third-pass review (findings 23–27, 2026-08-13) found that the sketch's three
hardest questions were design decisions it had listed as implementation
details. The rule for this document: **every mechanism below is stated
precisely enough to be wrong** — a reviewer should be able to refute it
against `roles.py` without asking what was meant.

**What it buys.** The one item in the parity plan that shrinks the repo. Seven
factory flows exist partly because no flow can call another; the starter README
already documents `plan-adversarial` then `implement-heal` as "the two-stage
version of `sdlc-e2e`" — with composition, `sdlc-e2e` IS two flow nodes, and
the three flows stop being three copies of the same prompts. Same for
release-cut's overlap with status-digest, and for every site that wants
"run the audit flow, then act on its verdict" today solved by hand-chaining.

**Shape (unchanged from the parity proposal, still correct).** A new executor
kind — the sanctioned 1.x extension point (`kind` is `str`, resolved against
the registry; `unknown-kind` is the named failure on an un-upgraded driver).
No new role, no `format_version` bump:

```jsonc
{ "id": "cut", "role": "work", "kind": "flow",
  "spec": { "flow": "flows/factory/release-cut.tg.json",
            "args": { "version": "{args.version}" } } }
```

`FlowExecutor` implements `protocols.Executor`. `plan()` loads the child flow
(named error if missing), renders `spec.args` (`{args.NAME}` and
`{steps...}` references — args to a child are DATA, not permissions, so the
write-scope restriction does not apply; every rendered value folds into the
hash). `execute()` runs a child engine in-process. The child's `final` node's
result is the node's result, validated against the parent node's contract.

---

## 1. Finding 24 answered: concurrency is a shared semaphore, not a pool

The defect the finding names: `_costs_tokens_hint` returns `False` for any
kind that is not `harness`/`fake`, so a `flow` node dispatches to the
hardcoded 8-worker `other_pool` — and every agent spawn inside the child would
escape `--max-workers` entirely, because the child engine's `run()` builds its
OWN `ThreadPoolExecutor(max_workers=...)`.

Making `_costs_tokens_hint` return `True` for flow nodes is the wrong fix
twice over: the flow node itself spawns nothing (it would burn a token slot on
bookkeeping), and it does nothing about the child's own pools.

**Mechanism: a `RunResources.worker_slots` semaphore, owned by the root
engine.** `threading.BoundedSemaphore(max_workers)`, created once per RUN (not
per engine). Every engine — root and child alike — acquires one slot around
each token-costing node execution (`token_pool` submission wraps
acquire/release; the pool itself becomes a thread supply, not the concurrency
authority). A child engine is constructed with the parent's `RunResources`, so
total live token work across the whole tree of engines is `--max-workers`,
exactly as the operator set it. The flow node stays on `other_pool`
(`_costs_tokens_hint` unchanged): it holds no slot while parked waiting on its
child, so a 3-deep composition does not eat 3 slots to run 1 spawn.

**One wallet, by construction.** `RunResources.spend_spawn` is the ROOT
engine's `_spend_spawn` bound to the root store: child engines call it instead
of their own counter, so `token_spawns`, `max_agent_spawns`, and the
wall-clock deadline are the root's, shared and thread-safe under the existing
`_budget_guard`. A child records its own local count too (its `status` should
be readable alone), but authority is the root wallet. A trip inside the child
raises `BudgetTripped` through `FlowExecutor.execute()`; **the engine's
node-execution wrapper learns that `BudgetTripped` escaping an executor is a
RUN-level stop (exit 4), never a node failure (exit 3)** — today that
exception only originates before execute, so the wrapper change is small and
testable in isolation.

## 2. Finding 25 answered: `RunResources` is the seam, bound at engine start

The defect: the parity sketch said child nodes "acquire from the same
registry", but `_locks` is per-`Engine` state built in `__init__` — a naive
sub-engine gets a fresh dict and the failure is not a deadlock but the silent
inverse: a child tree-writer running beside a parent-level `tree` holder. Two
writers, one tree.

**Mechanism.** `RunResources` (one dataclass, root-owned) carries everything
that must be RUN-scoped rather than engine-scoped:

- `locks` + `locks_guard` — the exclusive-token registry (`tree` included);
- `worker_slots` — §1's semaphore;
- `spend_spawn` — §1's wallet;
- `workspace` — the ONE shared `GitWorkspace` instance (same tree, same
  snapshot/restore machinery, same `_snapshot_guard`);
- `deadline` — the root's `max_run_minutes` clock;
- `abort` — a `threading.Event`; `cancel <parent_run> <flow-node>` sets it,
  the child engine's wave loop checks it (an in-process child has no process
  tree to kill, so cancel becomes a cooperative stop + parent node failure);
- `depth` — the composition depth counter (cap: 5, matching taskflow's).

The root engine builds one and keeps today's behaviour byte-identical when no
flow node exists (its own fields simply live one object away). It reaches the
`FlowExecutor` through a narrow, explicit protocol extension: an executor MAY
define `bind_run(resources)`, and the engine calls it on every registered
executor that does, once, at engine start. No global, no meta smuggling, and
the seed/replay wrappers forward it like they forward everything else.
`needs_check`, `_gate_outcomes`, heal snapshots and `target_gate` stay
engine-local — they are properties of one GRAPH, and each engine owns exactly
one.

**The token rule (finding 4, still right).** A flow node holds NO exclusive
tokens: `FlowExecutor.plan()` returns `exclusive=[]` unconditionally, and §6
gains `exclusive-on-flow` (error) so a flow author cannot reintroduce the
deadlock by hand. With shared locks, a parent-level writer and a child writer
serialize correctly against each other; the flow node itself is just a
spectator. `spec.writes` on a flow node is likewise a §6 error
(`write-scope-on-flow`) — scopes belong to the child's own nodes, which
declare and enforce them exactly as they do standalone.

## 3. Finding 26 answered: resume-mid-child IS child resume

The defect: "resume mid-child" was listed as a torture case with no decision
behind it — re-run the whole child (re-bills everything under one wallet) or
descend (needs a pointer and a story for the child's lineage head).

**Mechanism: the child run dir is deterministic and hash-named.**
`<parent_run>/children/<node_id>-<input_hash[:12]>/` — a real run dir with its
own `state.json`, journal, and flow copy, so `status`, `explain`, `steer`,
`verify-trace` and the cockpit descend with zero new plumbing (they take a run
dir; this is one).

- **Crash mid-child, parent resumed:** the parent flow node was `running` →
  stale → pending → `execute()` runs again → the child dir for the SAME
  parent `input_hash` already exists → the child engine attaches and RESUMES
  it. Completed child nodes hash-skip through the child's own revalidation.
  No parent-record pointer needed: the path is a function of what the parent
  record already stores.
- **Parent hash moved (flow edit, arg change, steering):** the name no longer
  matches → a FRESH child lineage starts beside the old one; the old dir
  remains as evidence and is collected with its parent (below). If the edit
  was one child prompt, `--seed <old child dir>` already exists and applies —
  composition adds no new mechanism because the child is a real run.
- **The child's lineage head vs a tree the parent moved:** child resume runs
  the ordinary M7 comparison; parent-sibling writes since the crash register
  as external edits and re-run the child's leaves and unconsumed nodes. That
  is the safe direction, at worst a cost — and it is the SAME answer a
  standalone run gives to an operator's out-of-band edit, which is the point:
  a child is not a special kind of run.

**Child outcome → parent meaning**, one table, frozen into tests:

| child engine exit | parent flow node |
|---|---|
| 0 | done; final node's result, contract-validated |
| 2 (gate block) | failed, error names the child gate + child run dir; parent resume → child resume → the blocked gate re-runs (r5 blocked-on-resume, inherited for free) |
| 3 (node failure) | failed, error names the child node + child run dir |
| 4 (budget) | never surfaces as an exit: the shared wallet raises `BudgetTripped` through execute (§1), run-level stop |
| 6 (approval rejected) | failed, error says so — but see §5: approvals inside a child are a lint in v1 |
| 7 (refusal/config) | failed with the child's message verbatim |

## 4. Hash, verification, and the readers

- **Parent `input_hash` parts:** `flow:<child_flow_hash>` (the file's sha256,
  M5), `args:<rendered spec.args, compact JSON>`, `config:<digest>` (a child's
  behaviour is its stanzas' behaviour). Editing the child flow re-bills the
  parent node and everything downstream — correct and SAID, in FLOW-AUTHORING,
  because it is surprising. Deliberately NOT a part: the child's recorded
  results (that would be circular) and the tree (the child's own nodes carry
  `spec.reads` if file content should invalidate them).
- **§6 additions**, all named: `flow-file-missing`, `flow-cycle` (a→b→a across
  files, walked at verify time), `flow-depth` (> 5), `exclusive-on-flow`,
  `write-scope-on-flow`, `flow-in-map` (a map whose body is an engine is
  deferred, not designed by accident), and child flows are RECURSIVELY
  verified so `verify` on the parent reports the child's errors with the
  child's path prefixed.
- **Finding 21, pinned not assumed:** `gc.plan_gc`, `estimate`, and `active`
  are single-level `runs_dir.iterdir()` scans. Child dirs live INSIDE the
  parent run dir, so they are invisible to all three BY CONSTRUCTION — never
  gc'd independently, never double-counted by estimate, never listed as
  orphan runs. One test per reader asserts it, so the accident the parity
  review found becomes a load-bearing choice.
- **`heal.targets` may not name a flow node** — already true (§6 requires
  harness-kind targets) and now deliberate: healing a subgraph means healing
  its nodes, from its own gates, inside the child.

## 5. Deliberately out of scope (v1 of this feature)

- **Approvals inside a child**: mechanically they work (the child prompts on
  the same TTY), but an approval buried two flows deep is an approval the
  operator did not see coming — `verify --lint` names it
  (`lint-approval-in-child`) and the cockpit story stays at depth 0 until the
  MISSION page learns to render children.
- **`flow-in-map`** (§4): fan-out of engines multiplies every question in this
  document; a map item that needs a subgraph is a sign the subgraph wants to
  be the flow.
- **Cross-run seeding of children beyond what exists**: `--seed` on the child
  dir works because the child is a run; a parent-level flag that threads seeds
  into children is sugar, deferred.
- **Workspace isolation per child** (worktrees): the enabler for racing
  writers, still its own project, still not this one.

## 6. Work, order, exit criteria

~3–4 weeks (the parity sketch said 2–3; findings 24–26 bought a semaphore, a
seam, and an attach story). Order: (1) `RunResources` extraction with today's
behaviour pinned byte-identical — this lands FIRST and alone, because it
touches `roles.py`'s hottest paths; (2) `FlowExecutor` plan/hash/§6; (3)
execute + outcome mapping; (4) resume/attach; (5) torture-suite cases (child
budget trip → exit 4; child gate block → parent failure naming the gate;
crash mid-child → resume without re-billing completed child nodes; two
engines racing one `tree` token; depth cap; cycle refusal). Exit criteria:
full pytest green, torture extended with all six cases, the replay fixture
untouched (nothing here may move hash composition for existing kinds),
`sdlc-e2e` reimplemented as two flow nodes WITHOUT deleting the original
until a release note says so, and DEVIATIONS entries for the executor
`bind_run` extension and the `BudgetTripped`-through-execute rule.

**Standing note.** This document was written by the author of the parity
plan's implementation, informed by its reviews but not yet reviewed itself.
The highest-value target for an independent reviewer is §1's claim that the
semaphore composes with the existing pool design without deadlock — a parked
flow node on `other_pool` (8 hardcoded workers) is itself a bounded resource,
and 9 concurrent flow nodes would starve it. If that ceiling is real, the
fix (flow nodes get their own unbounded dispatch, or `other_pool` sizes with
the graph) should be decided at adoption, not discovered live.
