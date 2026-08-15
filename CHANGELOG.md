# Changelog

The spec and its amendments are the authority on behaviour
(`docs/spec/SPEC.md` + `AMENDMENTS-r4/r5/r6`, later wins);
`docs/spec/DEVIATIONS.md` records implementation-level departures. This file
is the release-facing summary. Versions before 0.9.0 predate it — their
record is the git history and the proposals under `docs/proposals/`.

## 0.9.0 — 2026-08-15

The parity programme (PROPOSAL-taskflow-parity-tiers, adopted 2026-08-13) and
its successor (PROPOSAL-flow-composition, adopted 2026-08-15), every phase
adversarially reviewed before the next began.

### Patterns named and shipped
- FLOW-AUTHORING "Patterns you already have": **reduce**, **parallel**,
  **tournament**, **loop** — the keywords that deliberately do not exist,
  and the flows that ARE them.
- New starters: `tournament-judge` (rival candidates → judge, with the
  `tournament_pick` gate and the starter set's first custom contract),
  `refine-loop` (the loop pattern), `draft-then-review` (composition).
  New personas: `candidate`, `judge`.

### The loop (heal without rollback)
- Heal prompts name their round ("This is repair round N of M").
- `heal.on_exhausted: "block" | "pass"` — `"pass"` accepts the best-so-far
  when rounds run out, recorded as `accepted after N rounds without
  resolving: …` in the stored verdict, `status`, and a `heal-exhausted-pass`
  journal event. Never a plain pass; a gate that never decided (timeout,
  malformed verdict) never exhausts to pass. Guards:
  `on-exhausted-with-rollback` / `on-exhausted-without-rounds` (§6 errors),
  `lint-on-exhausted-pass`, and `lint-live-diff-per-phase` now fires on even
  one live capture inside a loop body.

### Declared staleness
- `spec.reads` — declared file inputs as hash parts: editing a declared file
  re-bills exactly the declarers, and `explain` names the file. Additive (M3:
  absent/empty contributes nothing — byte-identical parts, replay fixture
  unchanged). Stat-keyed per-process memo, `reads-hash` timing lines,
  `lint-broad-reads`, `snapshot_bench --reads`.
- `explain <run_dir> --graph` — the whole-graph staleness dry run: plans every
  node against the current tree into a throwaway dir, prints directly /
  transitively stale with the moved part named. Zero spawns; a gc'd result
  reports stale, never "unchanged".
- `run --seed <run> --force-stale <node>` — recompute: seed everything except
  the named frontier plus its downstream cone; forced is never confusable
  with hash-missed (record, journal, `status`, launch banner).

### Composition (`kind: "flow"`)
- A saved flow as one node. The child is a REAL run in
  `<run>/children/<node>-<hash12>/` — every run-dir tool descends unchanged;
  resume-mid-child is child resume (completed child work never re-bills);
  a moved parent hash starts a fresh child lineage beside the old evidence.
- One wallet, one tree, one worker cap across the whole tree of engines
  (`RunResources`: shared exclusive tokens, worker semaphore, root spawn
  budget). A child budget trip stops the RUN (exit 4), never "node failed";
  a child gate block fails the parent node naming the gate and the child dir.
- Boundaries as named errors: `rollback-heal-in-child`,
  `flow-in-rollback-cone` (rollback cannot cross the composition line until
  scope-narrowing exists), `exclusive-on-flow`, `write-scope-on-flow`,
  `timeout-on-flow`, `dynamic-flow-path`, `flow-cycle`, `flow-depth`,
  `flow-in-map`, `flow-args-missing`, recursive child verification,
  `lint-approval-in-child`. Steering a flow node is refused (steer the
  child's own nodes). Zero-token end-to-end smoke:
  `flows/demo/compose-smoke.tg.json`.

### Engine and harness fixes surfaced by the reviews
- AMENDMENTS M4's free retry-on-empty-result is now executor-opt-out
  (`auto_retry = False`); it was converting a child gate block into a
  retried success.
- The §6 arg scanner learned `spec.args` values and `spec.reads` entries as
  real references.
- `RenderCtx.runs_root`: the reads exclusion no longer mis-derives from a
  throwaway planning dir.
- Provider-limit diagnosis (`wait, then resume`) now works without a JSON
  envelope and reads stderr — reachable for copilot-cli, whose 429 usually
  means quota. The copilot stanza in `lockstep.toml.example` carries the
  four-point verification checklist (tool approval in -p mode, no pinned
  readonly shape, retry {max: 0} posture, the unhashed instruction-file
  channel).

### Docs
- THEORY-OF-OPERATIONS §8b (composition), the loop and heal-text round in §7;
  DRIVING-LOCKSTEP's exit table folds composed-run meanings; FLOW-AUTHORING
  gains "Patterns you already have", "Declared reads", and "Composition";
  debug-run and run-diagnostician descend into `children/`; DEVIATIONS
  entries for `on_exhausted`, `reads`, and the composition protocol
  extensions (`bind_run`, BudgetTripped-through-execute, `auto_retry`).

## 0.8.0 and earlier

Pre-changelog. See the git history and `docs/proposals/` — each adopted
proposal records what shipped and why.
