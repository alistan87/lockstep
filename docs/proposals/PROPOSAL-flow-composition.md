---
type: proposal
title: "Proposal: flow composition — run a saved flow as one node (kind: \"flow\")"
description: The successor to PROPOSAL-taskflow-parity-tiers §2.2, written because its third-pass review (findings 24–26) found three engine questions the sketch never answered. This proposal answers them — a shared worker semaphore, a RunResources seam bound to executors at engine start, and resume-as-child-resume via a hash-named child run dir — then survived its own adversarial review (nine findings, three material: the wrapper-forwarding lie, rollback-x-composition excluded in v1, the impossible cancel story). Not adopted; adoption is a commit that says so.
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
each token-costing node execution. The acquire happens INSIDE the worker
thread, first thing, never in the dispatch loop — acquiring before submit
would serialize wave dispatch itself. The pools become thread supply; the
semaphore is the concurrency authority. A child engine is constructed with
the parent's `RunResources`, so total live token work across the whole tree
of engines is `--max-workers`, exactly as the operator set it. The flow node
holds no slot while parked waiting on its child, so a 3-deep composition does
not eat 3 slots to run 1 spawn.

**Flow nodes dispatch on their own thread, not `other_pool`** (review finding
4). The draft parked them on `other_pool`, whose 8 hardcoded workers also
serve every shell node: 8 flow nodes parked for an hour would queue a
5-second shell probe behind them. Not a deadlock — the wave barrier
guarantees parked flow nodes eventually finish, and children run in their own
engines' pools — but an hour-long starvation of the cheapest node kind is a
defect. A parked thread costs nothing worth rationing, so each flow node gets
a dedicated thread (`_costs_tokens_hint` stays `False`; the budget accounting
is untouched because the flow node spawns nothing).

**Deadlock argument, stated so it can be attacked:** a slot is held for
exactly one spawn's duration; no slot-holder ever acquires a second slot or
waits on another engine; the flow node holds no slot and no token while
parked; parent-engine gate outcomes (heal, rollback) process only after
`futures_wait`, so nothing engine-local runs concurrently with its own wave.
The one blocking edge is child-token-work → semaphore → parent-token-work,
and parent token work always terminates without waiting on any child.

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
- `abort` — an in-process `threading.Event`, but its SOURCE is the existing
  cross-process mechanism: `cancel` is another process and cannot set an
  Event here. Today `cmd_cancel` requires a recorded pid and FAILS on a node
  that never spawned one — which a flow node is — so cancel gains a
  marker-only path for flow nodes: write the `CANCELLED` marker, kill
  nothing. The `FlowExecutor` polls its own phase dir's marker (the file the
  engine already honours for every other kind) and sets `abort`; the child's
  wave loop checks it between waves. Cooperative stop, parent node
  failed(cancelled), no retries — r6 C3 semantics preserved (review
  finding 3);
- `depth` — the composition depth counter (cap: 5, matching taskflow's).

The root engine builds one and keeps today's behaviour byte-identical when no
flow node exists (its own fields simply live one object away). It reaches the
`FlowExecutor` through a narrow, explicit protocol extension: an executor MAY
define `bind_run(resources)`, and the engine calls it on every registered
executor that does, once, at engine start. No global, no meta smuggling.
**The seed/replay wrappers do NOT forward it for free** (review finding 1 —
the draft claimed they "forward everything", and they forward nothing: each
copies exactly four attributes at construction, `kind` / `cacheable` /
`supports_corrective_respawn` / `SpecModel`, with no `__getattr__`). Both
wrappers gain an explicit `bind_run` that delegates to `inner`, and a test
pins that a seeded run still binds — the failure this prevents is a
`--seed` run whose children silently revert to unshared locks: two writers,
one tree, found only under the flag nobody tests by hand. The `FlowExecutor`
in turn calls `bind_run` on its CHILD registry's executors with the same
root `RunResources` — binding recurses with the composition, it does not
stop at depth 1. `needs_check`, `_gate_outcomes`, heal snapshots and
`target_gate` stay engine-local — they are properties of one GRAPH, and each
engine owns exactly one.

**The token rule (finding 4, still right).** A flow node holds NO exclusive
tokens: `FlowExecutor.plan()` returns `exclusive=[]` unconditionally, and §6
gains `exclusive-on-flow` (error) so a flow author cannot reintroduce the
deadlock by hand. With shared locks, a parent-level writer and a child writer
serialize correctly against each other; the flow node itself is just a
spectator. `spec.writes` on a flow node is likewise a §6 error
(`write-scope-on-flow`) — scopes belong to the child's own nodes, which
declare and enforce them exactly as they do standalone.

**Rollback healing and composition do not mix in v1** (review finding 2, the
one hazard the draft missed entirely). Rollback restores EVERYTHING changed
since its baseline (§9.4.4) against the ONE shared tree, and composition
creates two windows the existing graph-local guards cannot see:

- *Child rolls back, parent loses work.* A child healing gate's restore scope
  brackets the whole child run — including a parent-level sibling's completed
  writes, which land inside the child's baseline-to-now diff and get restored
  or discarded. The webapp-local incident (`lint-concurrent-heal-rollback`),
  reproduced across engines, where no lint can see it.
- *Parent rolls back, child attaches to a lie.* A parent rollback whose
  cascade invalidates a flow node re-marks it pending; re-execution attaches
  to the hash-named child dir, whose records say `done` for tree work the
  rollback just removed. Child revalidation hash-matches (inputs unchanged);
  M7 re-runs only leaves and unconsumed nodes; interior child records stand
  as `done` for writes that no longer exist.

The scope-narrowing that would make either window safe is the recorded r7
candidate (ROADMAP-NOTES), not this proposal. Until it exists: two §6
ERRORS — `rollback-heal-in-child` (a composed child may not contain a
`rollback: true` healing gate) and `flow-in-rollback-cone` (a flow node may
not be a descendant of any rollback-healing gate's targets; the cone is
computed exactly as W5b computes it). `rollback: false` loops — Phase B's
pattern — remain legal everywhere, and they are the common case for the
factory flows composition exists to collapse.

## 3. Finding 26 answered: resume-mid-child IS child resume

The defect: "resume mid-child" was listed as a torture case with no decision
behind it — re-run the whole child (re-bills everything under one wallet) or
descend (needs a pointer and a story for the child's lineage head).

**Mechanism: the child run dir is deterministic and hash-named.**
`<parent_run>/children/<node_id>-<input_hash[:12]>/` — a real run dir with its
own `state.json`, journal, and flow copy, so `status`, `explain`, `steer`,
`verify-trace` and the cockpit descend with zero new plumbing (they take a run
dir; this is one). `execute()` does not receive the node, so `plan()` stashes
`role`/`kind`/`contract` in `work.meta` and `execute()` recomputes the hash
from its own `fingerprint_parts` — the exact pattern `ReplayExecutor.plan()`
already uses for the same reason (review finding 7).

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
- **`spec.flow` is a LITERAL path** — no interpolation of any kind (review
  finding 5). `flow-cycle` and `flow-depth` are verify-time walks over the
  flow FILES; a `{args...}` path would make both undecidable until run time,
  which is exactly when a cycle stops being a named error and becomes a hang.
  `spec.args` VALUES may interpolate freely — data, not structure.
- **`timeout_s` on a flow node is a §6 error** (`timeout-on-flow`, review
  finding 6). The engine enforces timeouts by killing spawned processes; an
  in-process child engine has none, so a declared timeout would be silently
  ignored — a limit that does not limit is worse than none. What actually
  bounds a child: its own `budget.max_run_minutes` and the root deadline in
  `RunResources`, whichever bites first.
- **§6 additions**, all named: `flow-file-missing`, `flow-cycle`, `flow-depth`
  (> 5), `exclusive-on-flow`, `write-scope-on-flow`, `rollback-heal-in-child`,
  `flow-in-rollback-cone`, `timeout-on-flow`, `flow-in-map` (a map whose body
  is an engine is deferred, not designed by accident), and child flows are
  RECURSIVELY verified so `verify` on the parent reports the child's errors
  with the child's path prefixed.
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
- **Rollback healing inside or around a composed child** (§2): excluded by two
  §6 errors until the r7 scope-narrowing work exists. The loop pattern
  (`rollback: false`) is unaffected and is what the factory flows use.
- **Progress bridging**: while a child runs, the parent's flow node shows
  `running` with no per-step detail; the child's own run dir has the full
  story (`status <child dir>`, the cockpit pointed at it). Mirroring child
  journal lines into the parent's `progress.jsonl` is advisory sugar,
  deferred with the cockpit depth story (review finding 9).
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

---

## Adversarial review of this proposal (2026-08-14)

Run against the draft above, claims checked against `roles.py`, `seed.py`,
`replay.py`, `cli.py` and `proc.py`. Findings that changed the document are
folded into their sections and marked; the rest are recorded here so the next
reader does not rediscover them.

1. **applied, material** — "the seed/replay wrappers forward it like they
   forward everything else" was FALSE: both wrappers copy exactly four
   attributes at construction and forward nothing dynamically (no
   `__getattr__`). Without an explicit `bind_run` delegation, every `--seed`
   and `--replay` run would silently hand children UNSHARED locks — two
   writers, one tree, under the flag nobody tests by hand. The same class of
   error (an asserted mechanism nobody opened the file for) that the parity
   third pass caught in the sketch this proposal replaces. §2 fixed; test
   required.
2. **applied, material** — rollback healing × composition, missed entirely by
   the draft: a child rollback restores parent-sibling writes inside its
   window, and a parent rollback's cascade re-attaches a child whose records
   say `done` for tree work the rollback removed (M7 catches only leaves and
   unconsumed nodes). Two §6 errors exclude both windows in v1; the r7
   scope-narrowing work is the unlock. `rollback: false` loops unaffected.
3. **applied, material** — the draft's cancel story ("cancel sets a
   `threading.Event`") was impossible: `cancel` runs in another process, and
   today it FAILS outright on a node with no recorded pid — which a flow node
   is. Cancel gains a marker-only path; the `FlowExecutor` polls the
   `CANCELLED` marker the engine already honours and translates it to the
   in-process abort.
4. **applied** — parking flow nodes on `other_pool` shared 8 hardcoded
   workers with every shell node: 8 hour-long children would queue a
   5-second probe behind them. Starvation, not deadlock (the wave barrier
   bounds it), but decided now rather than discovered live: flow nodes get a
   dedicated thread each. The draft's own standing note asked exactly this
   question; the answer is above.
5. **applied** — `spec.flow` restricted to a literal path: interpolation
   would make `flow-cycle`/`flow-depth` undecidable at verify time.
6. **applied** — `timeout_s` on a flow node would be silently ignored (no
   process to kill); now a §6 error naming what actually bounds a child.
7. **applied** — `execute()` cannot see the node, so the child-dir hash is
   recomputed from stashed meta — `ReplayExecutor.plan()`'s existing pattern,
   cited so the implementer copies rather than invents.
8. **verified, no change** — the deadlock argument in §1 (slot per spawn, no
   nested acquires, wave barrier, children in their own pools) survives; the
   same wave barrier is what makes same-engine rollback-vs-flow overlap
   impossible, which localized finding 2 to the cross-engine windows.
9. **applied, small** — progress visibility while a child runs named as
   deferred scope instead of implied.

**Standing note for the adopter.** This review and the document share an
author; the parity plan's history says the residual risk is shared blind
spots. The highest-value target for an independent reviewer is now §2's
claim that `RunResources` extraction can land with today's single-engine
behaviour byte-identical — it touches `_locks`, `_spend_spawn` and the
dispatch path of every run, composed or not, and it is ordered FIRST
precisely because getting it wrong breaks runs that never heard of
composition. Second target: finding 2's exclusions are stated as sufficient;
nobody has yet tried to construct a third rollback window they do not cover.
