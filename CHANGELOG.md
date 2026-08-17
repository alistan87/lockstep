# Changelog

The spec and its amendments are the authority on behaviour
(`docs/spec/SPEC.md` + `AMENDMENTS-r4/r5/r6`, later wins);
`docs/spec/DEVIATIONS.md` records implementation-level departures. This file
is the release-facing summary. Versions before 0.9.0 predate it — their
record is the git history and the proposals under `docs/proposals/`.

## 0.10.0 — 2026-08-16

The fleet release: multiple concurrent drivers, made safe and operable
(concurrent-orchestration work order, adopted with the build; a
three-reviewer adversarial round plus a verification round before the cut —
the work order's header annotation is the record of what review changed).
Work-repo integration: `docs/proposals/passdown-0.10.0-work-repo.md`.

### The engine learns which tree a run belongs to
- `RunState.repo_root` — the resolved `--repo-root` a run was created
  against; recorded, never hashed; empty on older runs = unknown, never a
  mismatch. Child (`kind:"flow"`) runs record their parent's.
- `resume` against any other tree: **refusal, exit 7**, both paths named — a
  wrong-tree resume would snapshot and roll back someone else's work.
- An identical `run` whose newest lineage lives in another (often harvested,
  deleted) worktree **falls through to a new lineage** with a printed note —
  plain `run` keeps working after every fleet. Both narrowings logged in
  DEVIATIONS (2026-08-16).
- `status` prints `repo root:`, `active` prints `root:` (unknown stated,
  never omitted). Attach under a live lock pinned by test: exit 8, nothing
  written.

### The lane tooling (contrib)
- `lane.py start` — the worktree-per-run launch, mechanized: fresh worktree
  on its own branch, `verify` against it, detached run with the MAIN repo's
  config/runs-dir/binary, `--fresh` always. The run is identified by its
  own recorded root — unforgeable, only our child is told the worktree —
  with `--detach`'s printed lines demoted to diagnostics; launches
  serialize on a start-lock. Aborts never delete a tree that may host a
  live driver and never kill a pid not provably ours.
- `lane.py harvest` — refuses under a live driver, commits the branch (lane
  record excluded), restores the record if worktree removal fails;
  `abandon` is the explicit destructive sibling. The lane record
  (`<worktree>/.lockstep-lane.json`) is the durable identity every later
  step keys on.
- `who_holds.py` — LIVE/STALE/NONE (also FOREIGN/UNKNOWN) over the
  `<file>.holder.json` convention shared with the MIMIR DB runbook.

### Gate
- `lockstep.gates.lock_held` — preflight for flows that will write a shared
  file: non-blocking exclusive lock attempt (whole-file on POSIX, fixed
  large range on Windows), one open retry (the AV transient is not a
  holder), `open-refused` distinct from `lock-held`, FOREIGN holder files
  block, verdicts quote the holder by name. A diagnostic, not a mutex — the
  docstring says exactly what it cannot see.

### Roles, delegation, docs
- `docs/guides/FLEET-OPERATIONS.md` — the operating model, including "The
  roles": cockpit / main conversation / **dispatcher** / lane-runner, each a
  contract rather than a harness feature. Delegation moves information,
  never authority; evidence survives every hop verbatim; every tier above
  the run dir is disposable.
- `.claude/agents/fleet-dispatcher.md` + `lane-runner.md`, `/fleet-ops`
  skill (inline vs delegated). `approve.ps1` passes the run's recorded root
  and the main config, so the cockpit pane answers lane approvals.
- THEORY-OF-OPERATIONS §11: the multiple-drivers paragraph. Owner decisions
  recorded: ceiling 8 fleet-wide spawns; harvest always parks behind a
  walkthrough; `gc.auto 0` per clone (getting-started carries it).
- Two adversarially-reviewed work orders shipped with the release: the
  MIMIR DuckDB concurrency runbook (for the work repo) and the fleet order
  (built, annotated).

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
