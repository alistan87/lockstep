# Changelog

The spec and its amendments are the authority on behaviour
(`docs/spec/SPEC.md` + `AMENDMENTS-r4/r5/r6`, later wins);
`docs/spec/DEVIATIONS.md` records implementation-level departures. This file
is the release-facing summary. Versions before 0.9.0 predate it — their
record is the git history and the proposals under `docs/proposals/`.

## 0.11.0 — 2026-09-08

The OW-07 response release: the point-by-point disposition of two consumer
reports (`docs/proposals/upstream-response-ow07-feedback.md`, c3e1c2c), built
out. One confirmed engine bug, four accepted features, one new programme, and
three declines that each name the shipped mechanism they re-invent.
0.10.1-scope (truthful terminal state, awaiting-a-human wording) and
0.11.0-scope ship together — nothing went out between them (865dbf0).

### Breaking / changed behaviour — read this first

- **A post-lock refusal now exits 7, not 4.** A refusal after the lock is
  taken (dirty write scope, a failed heal precondition) used to leave every
  node pending and no journal, so `wait` reconstructed exit 4 — "a plain
  resume continues" — which was wrong advice, because `resume` is E9-exempt
  and would skip the very preflight that refused. The refusal handlers now
  persist a run-level terminal record plus a chained refusal event before
  releasing the lock; `wait` returns the recorded **7**. Anything keyed on
  exit 4 for this case must change. Budget stops still read 4. (bdce9c2)
- **Your local `lockstep.toml` needs `--permission-mode` on the claude-code
  stanza.** A headless `claude -p` spawn auto-denies its own tool permission
  prompts, so a WRITING node drafts its output and is then denied the Write
  that saves it — observed live, on this release's own changelog node. The
  committed `lockstep.toml.example` now carries
  `--permission-mode acceptEdits` (Edit/Write only; the driver's write-scope
  quarantine remains the enforcement layer). `lockstep.toml` is gitignored, so
  the example will not update it for you. A node that must RUN commands is
  still denied Bash — escalating that stanza to `bypassPermissions` is a
  deliberate, named decision, never the default. (85388b0)
- **An auto-rejected approval is worded as a parked question, not a
  decision.** `wait`/`status`/MISSION/cockpit now say "resume from a terminal
  to answer"; a human rejection stays a rejection. Exit 6 is unchanged —
  wording only. (bdce9c2)
- A refused run says so **everywhere a reader asks**: `status` prints the
  evidence verbatim, `active` tags **REFUSED** by default, MISSION and the
  cockpit headline say `refused: dirty scope` instead of `waiting`, and a
  `--detach` parent watches a 3s grace window so the refusal lands in the
  launching terminal rather than only in a log nobody was told to read. The
  next drive clears the record. (bdce9c2)
- Old-driver compatibility holds: `RunState` ignores unknown fields, so a
  0.10.0 driver reading a newer `state.json` degrades to the old reporting
  rather than failing. (2e1fdba)

### The operator gets two decisions back

- **`lockstep resume <run_dir> --max-agent-spawns N`** — raise (or lower;
  "stop spending" is coherent, warned, never refused) the spawn cap for
  **this drive only**. The flow's ceiling stays the consent artifact, every
  override leaves a journaled budget event that `status` echoes, and the next
  plain resume is governed by the flow again. This replaces the three-command
  workaround the report described: edit the flow, new lineage, `--seed` back.
  (6380eea)
- **`spec.reads_manifest: "paths"`** — append the resolved `reads` list to the
  prompt. `spec.reads` alone is an input-hash declaration the harness never
  sees; a reviewer told to "read the files named in `spec.reads`" correctly
  reported no such list and blocked on evidence access. The manifest is in the
  prompt, therefore in the input hash: a changed match set re-bills, and
  `explain` names `prompt.reads_manifest`. Zero matches say so out loud. It
  shares `apply_reads`' enumerator, so the list the prompt names and the files
  the hash covers cannot disagree. `reads_manifest` without `reads` is invalid
  at `verify`; absent or `"none"` is byte-identical to 0.10.0 (the replay
  fixture passes un-re-recorded). (6380eea)

### The adjudicated-review programme

For flows where review rounds never converge: discovery and gating must not
share a mind.

- `flows/factory/adjudicated-review.tg.json` — the template. Reviewers
  discover under a frozen scope statement; ONE readonly adjudicator
  (`personas/adjudicator.md`) re-grades their raw findings — unevidenced or
  out-of-scope demotes to nit with the reason kept; a NEW blocker is allowed
  but must say why it is in scope. (60ded6f)
- **`lockstep.gates.ledger_check`** — cross-round memory as a repo file the
  model never writes. Merges each round mechanically (new / persisting /
  reported-resolved, nothing ever deleted, returns recorded), blocks only on
  ACTIVE entries at threshold, treats accepted-risk as a human state that must
  carry a disposition or the gate blocks, and re-runs idempotently on
  identical input so resume revalidation cannot inflate history. (60ded6f)
- New advisory lints (`verify --lint`; exit code unchanged):
  `lint-unadjudicated-reviews` names the ping-pong shape this replaces — two
  or more `Finding[]` producers each gated raw; one gated raw stays
  refine-loop's fine shape (60ded6f). `lint-ledger-rollback` catches the
  ledger gate colliding with heal's default rollback, which restores every
  path since its baseline and would erase the cross-round memory each round —
  the exact failure the ledger exists to end. The template and FLOW-AUTHORING
  say `"rollback": false`. (f7f7d3c)
- FLOW-AUTHORING gains "Convergent review": the why, delta mode via
  `node_diff`, and the frozen-scope lesson. (60ded6f)

### `explain --graph` stops overstating freshness

- A node whose freshness rests on an always-rerun shell reproducing its
  recorded output now reads **`conditionally fresh <id> — depends on
  always-rerun shell <sid>`** instead of plain "fresh". The pre-run explain
  said three fresh; the shell printed a different duration string and two
  re-billed. Taint follows CONSUMPTION (`{steps.<shell>.}`) rather than mere
  ordering edges, and names the root shell through intermediaries; counts fold
  into fresh with the conditional split alongside. (fc87ea2)

### Docs

- **`docs/guides/CONSUMER-PLAYBOOK.md`** (new) — symptom first, mechanism
  second. Four feature requests already had shipped answers nobody found
  (`--seed`, `node_diff`, `pi-guarded`, auto-reject-is-the-park), plus two new
  recipes (budget-only edits never fork a lineage; stable shell output is
  authoring discipline) and the 0.10.0-era all-pending/exit-4 caveat for older
  drivers. (fc87ea2)
- FLOW-AUTHORING documents the detached-approval idiom (bdce9c2) and states
  the `reads` trap in one sentence — it is an input-hash declaration only
  (6380eea). COCKPIT-FOR-DOMAIN-EXPERTS learns the two words MISSION now shows
  a domain expert, since the guide binds what the surfaces may say (2e1fdba).
  CLAUDE.md's command list learns the budget override and the
  adjudicated-review template (f7f7d3c).

### Fixes

- `lockstep.gates.version_sync` accepts a **v-prefixed changelog heading**.
  Its delimiter lookbehind rejected the `## v0.11.0` form that release-cut's
  own prompt instructs (`## {args.tag}`), while the same gate blesses the `v`
  prefix on `--tag` — the flow and its gate contradicted each other. The token
  now allows one optional leading `v`; the 0.4.0.1-substring protection is
  re-pinned alongside the new case. (85388b0)
- `record_terminal` guards its whole body, not just the load — an AV transient
  on the state write or event append would have replaced the clean exit-7
  refusal message with a traceback, the reporting mechanism failing exactly
  where it was built to work. (2e1fdba)
- `status`' budget echo derived from a second, unguarded `read_events` call:
  both a duplicate full-journal scan and a regression of the r6 fix that lets
  `status` render over a corrupt journal. It now derives from the guarded
  read. (f7f7d3c)
- `cmd_active`'s comment stops claiming an abandoned refusal cannot linger (it
  can, until `gc`), `cmd_wait`'s docstring admits the recorded-exit path
  (2e1fdba), and `explain_graph`'s docstring admits the per-node conditional
  label (f7f7d3c).

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
