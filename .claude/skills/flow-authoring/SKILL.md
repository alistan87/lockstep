---
name: flow-authoring
description: Author or modify a lockstep taskgraph (*.tg.json) — node model, roles×kinds, contracts, heal rules, interpolation forms, and the verification rules that will reject a malformed flow. Use when writing a new flow, adding nodes to an existing one, or fixing `lockstep verify` errors.
---

# Authoring a taskgraph

**Start from the closest template in `flows/starter/`, not from a blank file.**
The adversarially-reviewed flows there cover the shapes that recur — author→
review→approve, implement→heal→review, map fan-out (reduce = any consumer of
`{steps.<map>.json}`), tournament (rival candidates → judge), refine loop
(healing gate with `rollback: false` + `on_exhausted`), clarification
gate, evidence approval, diagnose→fix→verify. Copy the nearest one and edit prompts, checks and
budgets. Its README table says what each is for and carries the per-flow
caveats. Authoring from scratch reinvents decisions those flows already
survived a review over, and the failure it produces is not a verify error —
it is a flow that verifies clean and gates the wrong thing.

Full grammar: `docs/spec/SPEC.md` §4–§7 as amended by `docs/spec/AMENDMENTS-r4.md`,
`-r5.md`, and `-r6.md` (the latest adopted revision wins).
Always finish with `.venv\Scripts\lockstep.exe verify <flow>` (exit 5 = errors,
all reported at once with named codes) and `run <flow> --dry-run` to see waves.

## Minimal node

```jsonc
{ "id": "impl",                  // ^[a-z0-9][a-z0-9-]*$
  "role": "work",                // work | gate | approval | map
  "kind": "harness",             // harness | shell | flow (fake = test double)
  "spec": { "task": "…prompt…", "persona": "implementer", "readonly": false },
  "depends_on": [], "output": "text", "timeout_s": 900 }
```

- `kind: "shell"` spec is `{ "cmd": ["pytest", "-q"], "cwd": "." }` — argv list,
  never a shell string. Shell nodes ALWAYS re-run on resume (by design).
- `kind: "flow"` runs a saved flow as one node: `{ "flow":
  "flows/x.tg.json", "args": {"k": "{args.k}"} }` — literal path, child =
  a real run under `<run>/children/`, one wallet/tree/worker-cap, child's
  final result = the node's result. No token, no writes, no timeout on the
  node itself; rollback-healing gates cannot compose (all §6 errors). See
  FLOW-AUTHORING "Composition"; worked example
  `flows/starter/draft-then-review.tg.json`.
- Exactly one node should set `"final": true` (else the last node is assumed,
  with a warning).

## Rules verification WILL enforce (§6 — the common trip-wires)

- Every `{steps.X...}` reference requires `X` in that node's `depends_on`
  (`unlisted-step-ref`); every `{args.K}` must be declared AND every declared
  arg referenced (`undeclared-arg` / `unused-arg` — both errors).
- `output: "json"` requires a resolvable `contract` (built-ins:
  `CheckResult`, `StepResult`, `Finding`, `Verdict`, `PathManifest`,
  `ProgressEvent`, `SteerMessage` — the last two live since r6 adopted
  progress and steering; `"X[]"` = array of; `module:Name`; or a bare name
  resolved via the flow-level `contracts_module` field). **Do not hand-copy the
  contract's field names into the task text**: the engine states the resolved
  schema (fields, enum literals, optionality) in the prompt itself, generated
  from the same model the driver validates against, so prompt and validator
  cannot drift. Task text should say what the CONTENT must establish, not what
  the JSON looks like.
- **Gates**: `role: "gate"` requires `output: "json"` + a contract resolving to
  `Verdict`. Prefer `kind: "shell"` gates (deterministic) whenever the check is
  machine-decidable — see `flows/gated-build.tg.json`. Reach for the **gate
  library** before writing an inline `python -c`: `python -m
  lockstep.gates.<name>` — `pytest_verdict`, `block_on_severity`,
  `required_sections`, `version_sync`, `citation_check`, `numbers_check`,
  `coverage_delta`, `fingerprint_check`, `pi_guard_smoke`, `scoped_checks`,
  `tournament_pick`
  (FLOW-AUTHORING has the argv for each). An embedded one-liner is untested,
  unreadable in the run dir, and re-quoted wrong on the first edit.
- **A gate wired to an ABSOLUTE target owns the repository's debt.**
  `ruff check .` or a named test file blocks on pre-existing failures in files
  the run never touched, and each false block costs a full heal round
  (rollback + re-run of the implementer). Two mechanisms, pick one:
  `python -m lockstep.gates.scoped_checks --run "ruff check {files}"` checks
  only the worktree's own changed files; or set **`"baseline": true` in the
  gate's `spec`** — its body runs once against the pre-run tree, and at
  evaluation the engine subtracts baseline findings (exact `(file, claim)`
  match), flipping a block to pass when every finding predates the run (the
  stored result is the adjudicated verdict, so downstream references agree).
  A baseline gate's cmd may not reference `{steps...}`
  (`baseline-gate-references-steps`) — it measures the tree before any step
  exists.
- **A gate that times out cannot heal** (a timeout is not a valid block,
  §9.4.3) — the run terminal-blocks with "timed out after Ns" naming the
  remedy. Size `timeout_s` from a measured run and put `retry` on the gate if
  the command's duration varies.
- **A heal target can leave notes for its own retry.** The footer invites every
  harness node to append durable findings ("failure X pre-dates this change,
  confirmed") to `attempt-notes.md` in its phase dir; a heal re-run's prompt
  includes them, so the retry does not re-spend what the first attempt already
  established. For expensive verification work, SAY in the task text what is
  worth noting there.
- **Heal**: only on gates; `max_rounds > 0` requires explicit `targets` that
  are harness-kind ANCESTORS of the gate; a node may not be a target of two
  gates; a healing gate (`max_rounds > 0`) with `rollback: true` — the
  default — requires a git-managed workspace: `verify` only WARNS
  (`heal-rollback-nongit`), but the run refuses with exit 7. The heal prompt
  names its round ("This is repair round N of M", engine-composed). A heal
  gate with `rollback: false` is the LOOP pattern (each round builds on the
  last — `flows/starter/refine-loop.tg.json`), and `heal.on_exhausted:
  "pass"` accepts the best-so-far when rounds run out, recorded as
  "accepted after N rounds without resolving: ..." — forbidden with
  `rollback: true` (`on-exhausted-with-rollback`), dead without rounds
  (`on-exhausted-without-rounds`), lint-named (`lint-on-exhausted-pass`).
  A `worktree_diff` capture inside a loop body draws
  `lint-live-diff-per-phase` even alone — use `node_diff`.
- **Map**: `role: "map"` requires `over` shaped `{steps.X.json...}` resolving
  to a JSON array; `concurrency: 1` guarantees array-order sequential runs;
  items resume per-item.
- **Approval**: no `kind`, no `spec` — core-handled TTY prompt; auto-rejects
  (exit 6) on non-TTY stdin; never resume-skipped.
- **Readonly**: `spec.readonly: true` lets harness nodes fan out in parallel
  (drops the `tree` exclusion) but the executor stanza MUST declare
  `readonly_argv` or verification fails (`readonly-unenforced`) — §6.11 wants
  enforcement visible in argv, not in the prompt. Readonly nodes answer on
  stdout — they cannot write `result.json`. claude: `--disallowedTools`.
  **pi: `--tools read,submit_result`** — an allowlist, so name
  the node's answer tool or you remove its answer channel, on a stanza with
  **no `--mode json`** (`pi-review`): readonly answers on stdout, and
  `--mode json` fills stdout with pi's event stream. Make every
  judgement node (review, triage, estimate, plan) readonly: it fans out, it
  cannot corrupt the tree, and on a metered subscription it is the cheapest
  reliability lever there is.

  **`readonly_argv` must remove the SHELL too**, not just write/edit — bash is
  a write vector (`echo x > file`), and `readonly` is exactly what licenses the
  scheduler to drop the `tree` token and run these nodes concurrently. claude:
  `--disallowedTools Edit,Write,NotebookEdit,Bash`. pi's built-ins are `read,
  edit, write, bash, web_search, source_check, fetch_content,
  get_search_content, taskflow` — no `grep`/`find`/`ls`, so `read` really is
  the whole grant.

  **Consequence to design around: a readonly node cannot run `git diff` or
  search the repo.** Hand it the input as data from a shell node — see the
  probe library below. And a directory reference is a search even when it
  doesn't look like one: "every file under `gates/`" needs the same treatment,
  because `read` takes a named file and errors on a directory (`EISDIR` on
  pi 0.83.0) — have a shell node emit the listing and say "the files listed
  below" instead.

  **Give never-mutating personas `readonly: true` in their front-matter**
  (`personas/reviewer.md` and `arbiter.md` here carry it; the key is stripped
  before hashing, so adding it re-bills nothing). `spec.persona` and
  `spec.readonly` are independent fields, so a "you fix nothing" persona on a
  node that declares neither `spec.readonly` nor `spec.writes` silently keeps
  full write tools and the `tree` token — `verify --lint` names that
  (`lint-persona-not-readonly`). Any explicit statement satisfies it,
  including `writes: []` for a reviewer that must keep a shell.

## The probe library (`python -m lockstep.probes.<name>`)

A gate DECIDES (emits `Verdict`, driver branches); a probe OBSERVES (emits
text, a readonly node judges it). Always exits 0 — a failed command is an
observation, not a broken node.

- `worktree_diff [--base HEAD]` — status, diff, and the contents of CREATED
  files (untracked, so absent from any diff). The tree **as it is now**.
- `node_diff --node <id>` — what ONE STEP changed, from the two git trees the
  engine recorded for it. Same answer on every resume. Needs the target node to
  declare `spec.writes`, hold the `tree` token, and have succeeded.
- `command_output "<cmd>" [--label repro]` — run one command, report exit code
  and output, capped middle-out so a traceback keeps both ends.

**Use `node_diff` for any flow that reviews more than one phase.** Shell nodes
always re-run on resume (§0.1.7), so a second `worktree_diff` capture — and,
worse, the FIRST one re-running later — reports a tree that now contains the
next phase's work. The reviewer's prompt embeds it, its hash legitimately
moves, and a review that had PASSED re-bills and comes back with violations
that never existed (reported live: two full restarts).
`verify --lint` warns via `lint-live-diff-per-phase`.

Shape: `shell probe → readonly judge`. `flows/starter/implement-heal` (capture
the diff, then review it) and `bugfix-heal` (run the repro, then diagnose it)
are the worked examples; `two-phase-remediation` is the two-phase one, where
both reviewers read `node_diff`. Beyond enabling `readonly`, the observation becomes
deterministic, cached, and durable as evidence in the run dir.

## Interpolation (§7)

`{args.K}` · `{steps.ID.output}` (raw text) · `{steps.ID.json}` /
`{steps.ID.json.a.b}` (compact-serialized) · `{item}` / `{item.field}` in a map
body · `{previous.output}` (exactly one dep). `{{` escapes `{`. Values are
fenced as untrusted data in harness prompts and spill to a file above
`max_interp_chars` (default 20000 — raise it if a downstream readonly node
must consume large upstream JSON inline).

`when` grammar is exactly `{ref} ==|!= <JSON literal>` compared as compact-JSON
strings: `== true`, `== "foo"` (quotes required), `== null` (also matches a
skipped upstream — `when` is exempt from transitive skip, AMENDMENTS A2).
No numerics beyond exact serialization: `5` ≠ `5.0`.

## Large datasets and artifacts

The failure mode is not a crash — it is a flow that costs ten times what it
should, or re-bills a whole corpus because one byte moved.

- **Pass a PATH, not the payload.** A harness with tools opens files itself.
  Interpolating a big value puts it in the prompt, in the hash, and in the
  bill. Interpolate what you *want* pinned to the hash; nothing else.
- **`max_interp_chars` (20000) protects harness prompts only.** Over the cap a
  value spills to a file and the prompt gets a stub path. **Shell argv is
  neither capped nor spilled** — the raw string hits Windows' ~32k command-line
  limit and the spawn fails with exit 127. Have shell nodes read a file.
- **Fan out over a manifest, not a blob**: shell node emits `PathManifest` →
  `map` takes one item each. The only shape whose cost tracks the CHANGED part
  of a corpus, because each item caches separately.
- **Fingerprint the items.** Item strings are the cache keys, so `docs/a.md`
  never re-runs when `docs/a.md` changes — emit `path|content-fingerprint` and
  have the item prompt split on the last `|` (`lint-map-over-manifest`).
- **Wide fan-out ⇒ `optional: true` + an explicit budget.** Otherwise one bad
  item fails the node and discards every sibling you already paid for. With it,
  failed slots arrive as `{"status": "failed"}` — say so in the consumer's
  prompt, or it reads a failure as a clean result.
- **Large outputs go to files under `spec.writes`**, with a short summary as
  the result. The §8.3 channel is not a delivery mechanism.

Caching consequence: the FULL pre-spill value is hashed while the prompt gets a
stub (§7, deliberate). Honest, but it means one edit anywhere in a big
interpolated value re-runs that node and every descendant — interpolate a
fingerprint or a summary if the payload churns.

**`spec.reads`** (harness/fake only): declared file inputs as hash parts —
`"reads": ["src/**/*.py"]` re-bills the node when a matched file changes, and
`explain` names the file. PRECISION, not correctness: an undeclared read stays
invisible. pathlib globs (`**` crosses dirs — NOT writes' fnmatch);
`{args.NAME}` only; hashed at every plan incl. resume revalidation
(`lint-broad-reads` past 200 files; timing in the journal). Absent/empty =
no-op. See FLOW-AUTHORING "Declared reads".

## Retry

Harness nodes DEFAULT to `retry: { max: 2, backoff_ms: 60000 }` (AMENDMENTS-r5
B2) — transient provider errors (429/529) surface as nonzero exits and the
minute-scale backoff absorbs them. Setting `retry` explicitly in the flow file
(even `{"max": 0}`) overrides the default entirely; shell nodes stay at
`max: 0`. If you do need a custom retry, bake it in BEFORE the first run —
editing the flow later changes `flow_hash` and starts a new lineage,
re-running (and re-billing) every completed node.

**On a REQUEST-metered plan (Copilot and friends), set `"retry": {"max": 0}`.**
There a 429 usually means quota exhausted, not a blip: it does not clear in a
minute, and the two retries spend two more requests against the same wall
before the node fails anyway. The driver already names it (`provider
limit/overload` + a resume hint, r5 B3), so the cheaper posture is to fail fast
and `resume` when quota returns. Keep the default where billing is per token.

## The result channel decides the stanza

Where a node's answer comes back is not a detail — it constrains the argv:

- **Tools + the standard footer ⇒ FILE channel** (`result.json`/`result.txt`).
  A structured stdout mode is free here, because stdout is never read.
- **`readonly: true`, or a harness with no file tools ⇒ STDOUT channel**
  (`FOOTER_READONLY`). Then stdout must carry ONLY the answer. On pi that means
  the stanza must not set `--mode json`: it is an event stream whose last
  object is `{"type":"agent_settled"}`, which is exactly what the driver would
  validate against your contract. Use a separate readonly stanza.
- **A stanza declaring `json_field`** speaks envelopes, so even a text node is
  unwrapped from one; a stanza omitting it takes stdout verbatim.

Rule of thumb: one stanza per (model × result channel), picked per node with
`spec.executor`. That is cheaper than it sounds — stanzas are config, and
mixing them in one graph is the design, not a workaround.

## Write scope (`spec.writes`)

**Every mutating work node declares one.** A narrow prompt is advisory text a
model can rationalize past under gate pressure; `spec.writes` is the mechanical
control. `verify --lint` warns on a write-capable work node without one
(`lint-missing-write-scope`; a verify ERROR at format_version 1.1). The three
honest declarations:

- `"writes": ["docs/plan.md", "src/x/"]` — the paths/globs it may write.
- `"writes": []` — **this node writes nothing** (a probe, a collector, a
  printer). Presence-keyed and ENFORCED: any write quarantines. An absent key
  is the old unconstrained behavior; declared-empty is a real constraint.
- `"writes": ["**"]` + `"writes_rationale": "…"` — deliberate whole-tree
  access for genuinely run-time-parameterized targets (a generic implementer
  told which files to touch by `--arg task`). The rationale is required
  (`lint-unscoped-writes`) so a reviewer can see the omission of a real scope
  was a decision.

A scope MAY interpolate `{args.NAME}` (`"writes": ["docs/{args.name}"]`) —
args only. A `{steps...}` reference would let a node's upstream output decide
what that node may write, so it is the verify error `dynamic-write-scope`; the
rendered value is re-checked for `..` and absolute paths so an arg cannot
escape either. Reach for `["**"]` only when the target is not expressible even
with args.

It reaches the spawn as `LOCKSTEP_WRITE_SCOPE` so an in-harness extension can
prevent a stray write; the driver detects one by diffing a baseline tree,
inside the node's own lock.

A violation is **quarantined**, so a scope is a decision about what may be
reverted rather than only flagged: the blocked attempt is kept as
`phases/<node>/out-of-scope-<attempt>.patch`, each violating path is restored to
its baseline or moved into `out-of-scope-<attempt>/` (never deleted), and the
node fails naming every path and its outcome. On success the in-scope changed
paths land in `touched-<attempt>.txt`.

Two engine behaviours the scope feeds beyond quarantine: a heal re-run's prompt
RESTATES the target's own scope (gate findings naming out-of-scope files
otherwise read as authorization to edit them), and a fresh `run` refuses when
uncommitted working-tree changes sit inside any declared scope — an in-scope
write would legally overwrite the operator's edit (`--allow-dirty-scope`
overrides; resumes are exempt).

Verification: absolute or escaping entries are `bad-write-scope`; an entry
referencing anything but `{args.NAME}` is `dynamic-write-scope`; a map node
declaring one is the hard error `write-scope-on-map` (the items share one tree
and one diff); a `readonly` node gets the advisory `write-scope-unenforced`,
because it holds no `tree` token and the diff would be unsound. Every other
write-capable kind, shell included, takes the token. The matcher is `fnmatch`,
so `*` crosses `/`. A mutation with no gate or approval on either side of it
draws `lint-ungated-mutation` — nothing authorized it and nothing can block
it; if a human reads the output directly by design, say "ungated" in the flow
description.

## Gates: the ways one goes wrong

Learned from `flows/demo/sudoku-local.tg.json` (see FLOW-AUTHORING for the
worked version of each).

- **A property you do not check is a property you do not have.** Write the
  pass reason as the list of properties actually established; reading it back
  is how you notice the missing one.
- **Check the behaviour, not the convention.** A gate that rejects a working
  implementation over a calling convention spends the heal budget on nothing.
- **A gate that runs model-written code must not hang.** Run it in a CHILD
  process with its own clock. A hang becomes `no valid verdict emitted` —
  fail-closed and correct, and useless; a timeout you catch becomes a finding
  that names the function.
- **Judge with your own code.** A model's solver cannot certify its own
  generator.
- **`fix_hint` is the next prompt** — the heal round appends findings verbatim.
  Evidence goes in `evidence`, the instruction in `fix_hint`.
- **Normalise at the boundary.** A ``` fence is a shell node's job
  (`save_result.py --strip-fence`), not three heal rounds.
- **Run the gate against a known-bad and a known-good input before the flow.**
- **Never let two healing gates with `rollback: true` heal concurrently.** A
  rollback discards every path changed since its own baseline, not just its
  target's, so concurrent gates delete each other's work — three wasted rounds
  on `webapp-local` before it was spotted. `heal-target-overlap` misses it
  (disjoint targets, colliding baselines). Serialise the branches with a
  dependency edge.

## Tool-less harnesses, and one model per node

A harness that can only print (`ollama run`, any bare model CLI) is a
first-class executor: `output: "text"`, the §8.3 stdout channel, and a shell
node writes the file. Two rules —

- a text node on a stanza declaring `json_field` is unwrapped from the
  envelope; on one that omits it, stdout is taken verbatim;
- the model is per NODE (`spec.executor`, one stanza per model), and it
  decides whether a flow converges: on the sudoku flow a 14B never met the
  uniqueness requirement and a 35B met it first try. Harnesses mix in one
  graph as freely as models do.

**PIN THE MODEL IN ARGV, ALWAYS.** A stanza with no `--model` does not mean
"the default model" — it means *whatever that harness picks at spawn time*,
which is its own config and its own last selection. That choice is not in argv,
so it is not in `input_hash`: two runs can carry identical hashes and have been
answered by different models, and a stanza the harness resolves differently
tomorrow silently re-answers a node the cache thinks is settled. It also reads,
from inside a flow, exactly like lockstep having no per-node model selection at
all — the flow says nothing about a model because the stanza says nothing
either. Pin every stanza, including the one you think of as the default.

pi takes `--model provider/id` with no separate `--provider`, and a `:<level>`
suffix for reasoning effort, so **reasoning level is per stanza too** —
`--model claude-opus-5:high` and `--model claude-opus-5:low` are two stanzas
over one model, and a node picks between them like any other:

```toml
[executors.pi-deep]
argv = ["pi.cmd", "-p", "--mode", "json", "--no-session",
        "--no-context-files", "--no-skills",
        "--model", "anthropic/claude-opus-5:high", "{prompt}"]
prompt_via = "stdin"
```

Repointing a node at a different stanza re-bills it — `argv` and the resolved
stanza's digest are both fingerprint parts. The digest is PER STANZA (r5 B1),
so *adding* stanzas invalidates nothing that already ran; `lockstep explain
<run> <node>` names which part moved.

**When the harness HAS tools** (pi, Claude Code) three things change: the
result arrives on the FILE channel (the footer's `result.txt`, not stdout); the
phase dir becomes the agent's scratchpad and your step drawer lists what it
left there; and stdout may stay EMPTY for the whole node because the harness
buffers. So **ask for progress in the task text** on anything running more than
a couple of minutes — the footer's "optionally, you MAY" invitation to write
`progress.jsonl` is not enough — and size `timeout_s` from a measured call: a
35B took 14m55s through pi, and the 900s default would have killed it at 15:00.

## Budget & executors

`budget.max_agent_spawns` counts every token-costing spawn INCLUDING corrective
re-spawns and heal rounds — interrupted lineages keep their counter, so leave
headroom. Executors are stanzas in `lockstep.toml` (see
`lockstep.toml.example`); pick per node via `spec.executor`, per flow via
`executor_default`, else the config `default`. Multi-model review = one stanza
per model (see `flows/audit-spec.tg.json`).
