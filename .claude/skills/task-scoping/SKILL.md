---
name: task-scoping
description: Scope a software task into right-sized lockstep nodes BEFORE authoring the flow — the decomposition step flow-authoring assumes is already done. Use when a task feels too big for one node, when a flow's nodes keep retrying or burning correctives, or when deciding what should be a node at all. Companion to /flow-authoring, which covers the grammar; this covers the cut lines.
---

# Scoping a software task for lockstep

An oversized node is the most expensive authoring mistake in this system,
and no verifier catches it: the flow verifies clean, then bleeds at
runtime. This skill is the decomposition step you do BEFORE opening
`/flow-authoring`.

## Why size is a cost, mechanically

Every one of these is per-NODE, so one big node concentrates all of them:

- **A node fails, retries, and heals as a unit.** There is no partial
  credit: a 20-minute node that times out at minute 19, or emits output
  that fails its contract, re-runs from zero. A corrective re-spawn
  carries the original prompt PLUS the invalid output — roughly 2.5× the
  node's tokens, and on a request-metered harness (Copilot) **every
  retry, auto-retry, and corrective is another billed request**.
- **The cache is per-node input_hash.** A big node's hash moves when ANY
  of its inputs move; a resume re-bills the whole thing. Small nodes
  invalidate surgically — that is the entire point of the DAG.
- **Contract validation quality degrades with scope.** A model asked for
  one artifact emits it cleanly; a model asked for five things emits
  prose wrapping five things, and the driver's repair/corrective
  machinery exists for that failure, not instead of it. The deletion-only
  repair (0.13.0) can save a dangling comma; it cannot save a task that
  should have been three tasks.
- **Steering, heal targets, and approvals are per-node.** A human can
  only redirect at node boundaries. A monolith gives them one boundary:
  after everything.
- **Agent drift grows with loop length.** A headless spawn that runs 40
  tool calls wanders; write scopes quarantine what it wandered into, and
  the quarantine + scope-corrective is another billed round.

## The sizing rule

**One node = one artifact, one concern.** Concretely:

- The task sentence has no load-bearing "and". "Implement the parser
  **and** its tests **and** update the docs" is three nodes (or two plus
  a gate) — each with its own `writes` scope, its own contract, its own
  retry boundary.
- A node either WRITES or JUDGES, never both. Producing work and
  evaluating work in one prompt yields self-graded work. The judge is a
  separate `readonly: true` node — which also drops the `tree` token and
  runs in parallel, and (on pi with `envelope = "pi-stream"`) reports
  its own cost.
- The output should fit one code review. If a human could not review the
  node's diff in one sitting, the model could not hold it in one loop.
- **The floor: don't split below one meaningful unit of work.** On a
  request-metered harness every node spawn is a billed request, so ten
  trivial nodes cost ten requests where two right-sized ones cost two.
  Split by CONCERN, not by line count — and give anything deterministic
  to a `shell` node, which costs nothing: formatters, file moves, test
  runs, diffs (`worktree_diff`/`node_diff` probes), manifest generation.
  A model must never be spawned for what a script can do.

## Cut patterns (each maps to shipped machinery)

| Shape of the task | Cut | Machinery |
|---|---|---|
| build something non-trivial | plan (readonly) → implement (scoped writer) → verify (gate) | `flows/starter/implement-heal`, `sdlc-e2e` |
| same edit across N files/modules | manifest (shell or readonly) → `map` over items → one reduce/apply | per-ITEM hashes: one bad item retries ALONE; `flows/factory/codemod-propose` + `codemod-apply` |
| large change, risky writes | readonly nodes EMIT orders → one serialized, scoped applier applies | the factory propose/apply split; keeps N readonly nodes parallel and one writer accountable |
| judgement (review, triage, estimate) | fan out readonly judges → adjudicate | `adjudicated-review`, `audit-spec`; readonly = parallel = wall-clock free |
| multi-phase work | phase gates between phases, `node_diff --node` per phase | a phase-2 edit can never re-litigate phase 1's review |
| "fix whatever the gate found" | heal with SMALL targets | heal re-runs its `targets` list — a small target re-bills small |

## Warning signs a node is too big (check before verify)

- The task prose runs past a page, or enumerates deliverables.
- `timeout_s` had to be raised past ~600s for a writer to survive.
- `spec.writes` spans src AND tests AND docs (three concerns).
- The contract has fields that describe unrelated things.
- You raised `retry.max` to "make it pass" — retries are for transport
  failures; a node that needs shape retries needs a smaller shape.
- The corrective count in `lockstep status` / the cost panel climbs on
  the same node across runs: that is a prompt-craft signal, and the
  usual craft failure is scope.
- On the work machine specifically: watch the cost panel's per-node
  "usage msgs" count — a node whose count keeps growing is running a
  long agent loop, and long loops are the drift-and-retry zone.

## Worked example

Wrong (one node, seen in the wild — this is the flow that "caused
multiple retries"):

> "Implement the CSV import feature: parser in src/importer/, unit tests
> in tests/, update README and the user guide, run the test suite and
> fix anything that breaks, then summarize what changed as JSON."

Five concerns, one hash, one timeout, one retry boundary, self-graded.
Right:

```
plan       readonly harness   contract: StepPlan      (judgement, parallel-safe)
impl       harness            writes: ["src/importer/**"]
tests      harness            writes: ["tests/**"]        depends_on: [impl]
suite      shell gate         python -m lockstep.gates.pytest_verdict
docs       harness            writes: ["README.md", "docs/**"]  depends_on: [suite]
review     readonly harness   contract: Finding[]     depends_on: [suite]
```

`suite` is free. `plan` and later `review` are readonly and parallelize.
A failure in `tests` retries `tests` — not the parser, not the docs. A
resume after editing only the docs prompt re-bills only `docs`. That
asymmetry is what you are buying with the extra nodes, and on a
request-metered plan it is usually 2–3 extra CHEAP requests against
whole-flow re-runs that cost every node again.

## Budget the flow before running it

- `budget.max_agent_spawns`: count worst case — nodes × (1 + retries +
  1 corrective) + heal rounds × targets. If the honest ceiling alarms
  you, the flow is telling you where it is too big.
- `budget.max_spawns_per_node`: on a wide map or a broad node, cap each
  node/item so one runaway cannot starve the mandatory tail. Remember
  the automatic timeout/empty-result retry is extra even at `retry.max: 0`;
  the cap is the only thing that bounds it.
- `python contrib\plan_card.py <flow>` previews shape + ceiling;
  `lockstep run <flow> --estimate` prices it from prior runs, spending
  nothing.
- `verify --lint` catches the mechanical subset: `lint-missing-write-scope`
  (writer with no declared scope), `lint-ungated-mutation`,
  `lint-map-without-budget`, `lint-argv-prompt` (big prompts on argv).
  Lint cannot see an oversized task; that is this skill's job.
