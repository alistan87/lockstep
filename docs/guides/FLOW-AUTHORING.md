---
type: guide
title: Authoring a lockstep taskgraph (portable reference)
resource: docs/guides/FLOW-AUTHORING.md
---
# Authoring a lockstep taskgraph (portable reference)

For any agent or human writing `*.tg.json` flows. Harness-agnostic: nothing
here assumes which coding agent authored the flow or which executor runs it.
Authoritative grammar: `docs/spec/SPEC.md` §4–§7 as amended by
`docs/spec/AMENDMENTS-r4.md`, `docs/spec/AMENDMENTS-r5.md`, `docs/spec/AMENDMENTS-r6.md`
(later revision wins). This file is the distilled
working subset.

**The method: imitate, then compile.** Start from the closest template in
`flows/starter/` (ten verified flows covering every construct below), adapt
it, then loop on `lockstep verify` until it prints `ok` — exit 5 reports ALL
violations at once with named error codes. Do not run a flow that has not
verified. Finish with `lockstep run <flow> --dry-run` to inspect the wave plan
before spending tokens.

If the run will use a shared config (`run --config <toml>`), pass the SAME
`--config` to `verify` — stanzas live there, and without it every stanza the
flow names resolves to nothing and `no-executor-stanza` fires on a flow that
is fine.

## Minimal node

```jsonc
{ "id": "impl",                  // ^[a-z0-9][a-z0-9-]*$
  "role": "work",                // work | gate | approval | map
  "kind": "harness",             // harness | shell
  "spec": { "task": "…prompt…", "persona": "implementer", "readonly": false },
  "depends_on": [], "output": "text", "timeout_s": 900 }
```

- `kind: "shell"` spec is `{ "cmd": ["pytest", "-q"], "cwd": "." }` — an argv
  LIST, never a shell string. Shell nodes always re-run on resume (by design:
  cheap, and it kills the silent-skip footgun).
- Exactly one node should set `"final": true`.
- Flow header: `format_version` ("1.0"), `name`, optional `description`,
  `args`, `budget`, `max_interp_chars`, `executor_default`.

## Verification trip-wires (named §6 error codes you will actually hit)

- Every `{steps.X...}` reference requires `X` in that node's `depends_on`
  (`unlisted-step-ref`).
- Every `{args.K}` must be declared in the flow-level `args` map AND every
  declared arg must be referenced (`undeclared-arg` / `unused-arg` — both
  errors). Declaration: `"args": {"task": null}` (null = required at run
  time via `--arg task=...`; a string = the default).
- `output: "json"` requires a resolvable `contract` (`json-without-contract`,
  `contract-unresolvable`). Built-ins: `CheckResult`, `StepResult`, `Finding`,
  `Verdict`, `PathManifest`, `ProgressEvent`, `SteerMessage`; `"X[]"` = array
  of X; `module:Name` or flow-level `contracts_module` for customs.
- **Gates** (`role: "gate"`) require `output: "json"` + a contract resolving
  to `Verdict` (`gate-contract`). Prefer `kind: "shell"` gates whenever the
  check is machine-decidable — deterministic, token-free, and the spec's
  preferred form.
- **Heal** (gates only, `heal-on-nongate`): `"heal": {"max_rounds": N,
  "targets": ["impl"]}` — targets must be harness-kind ANCESTORS of the gate
  (`heal-target-kind`, `heal-target-not-ancestor`); a node may not be a heal
  target of two gates (`heal-target-overlap`); healing with `rollback: true`
  (the default) needs a git-managed workspace (verify warns; run refuses,
  exit 7). On block: attempt preserved as a patch, tree restored, findings
  folded into the target's re-prompt (the engine names the round: "This is
  repair round N of M"), descendants invalidated, re-run.
  `heal.on_exhausted: "pass"` accepts the best-so-far when rounds run out
  (see the loop pattern below) — forbidden with `rollback: true`
  (`on-exhausted-with-rollback`: accepting work you rolled back accepts a
  tree the work is no longer in) and dead without rounds
  (`on-exhausted-without-rounds`).
- **Map** (`role: "map"`): `over` must be shaped `{steps.X.json...}` and
  resolve to a JSON array at run time; `item_var` (default `item`),
  `concurrency`. Items cache and resume per item; item hashes couple to array
  index, so inserting an item re-runs the shifted tail.
- **Approval** (`role: "approval"`): NO `kind`, NO `spec`
  (`approval-with-kind`) — core-handled TTY prompt. Non-TTY stdin ⇒
  auto-reject, exit 6. Never resume-skipped.
- **Readonly**: `spec.readonly: true` lets harness nodes fan out in parallel
  (drops the `tree` exclusion) but the executor stanza MUST declare
  `readonly_argv` (`readonly-unenforced`) — §6.11 wants the enforcement
  *visible in argv*, so an honour-system prompt does not qualify. Readonly
  nodes answer on stdout — they cannot write result files.

  What `readonly_argv` looks like per harness: claude takes
  `--disallowedTools`; **pi takes `--tools`, an allowlist** —
  `readonly_argv = ["--tools", "read,submit_result"]`. Name
  `submit_result` (or whatever tool the node answers with) explicitly: pi's
  allowlist covers extension and custom tools too, so omitting it removes the
  node's own answer channel. Naming a tool the harness does not have is
  harmless. Use the *narrowest* list that still lets the node do its job —
  this is the single cheapest reliability lever on a metered subscription,
  because a reviewer that cannot edit cannot burn a round trip trying.

  **Know what the list grants.** pi 0.83.0's built-ins are exactly `read, edit,
  write, bash, web_search, source_check, fetch_content, get_search_content,
  taskflow` — there is no `grep`, `find` or `ls`, because bash covers them. So
  `--tools read,submit_result` grants read and nothing else, which is the
  intended strength: `readonly` is what licenses the scheduler to drop the
  `tree` token, and **bash is a write vector**. A stanza that keeps bash while
  excluding write/edit passes verification and breaks that invariant — do not
  build one. For the same reason claude's list must exclude `Bash`, not just
  `Edit,Write`.

  **So a readonly node cannot run `git diff` or search the repo.** Give it what
  it needs as data from a shell node — that is what the probe library below is
  for.

  **The stanza must also leave stdout usable**, because that is where a
  readonly node's answer comes back. On pi that means the readonly stanza must
  NOT carry `--mode json`: measured against 0.83.0 it is an event STREAM, and
  the last object in it is `{"type":"agent_settled"}` — which is exactly what
  the driver hands to contract validation. Keep two stanzas (`pi` and
  `pi-review` in `lockstep.toml.example`); writers keep `--mode json` and its
  usage telemetry because they answer in a file, and readonly nodes trade that
  telemetry for a working result channel.

  Readonly is not only for reviewers. Any node whose product is a *judgement*
  — triage, estimation, planning, a verdict — should be readonly: it fans out,
  it cannot corrupt the tree, and it cannot be sent back for rework over a
  stray file.

## Advisory lints (`verify --lint`)

Opt-in warnings, never a changed exit code, and NOT part of §6. The admission
standard is part of the design: every lint names a recorded incident or a
shipped rule, and a lint with neither does not get added
(`PROPOSAL-factory-programme.md` §A2). Lints marked *(config)* need
lockstep.toml stanzas; when they cannot run, `--lint` says so rather than
reading as clean.

| code | fires when | anchor |
|---|---|---|
| `lint-work-after-approval` | a harness/fake node is reachable strictly downstream of an approval | evidence-approval's rule: post-approval work runs in the human's own resume — seconds-long shell only (fine for a deliberately attended flow like sdlc-e2e) |
| `lint-map-over-manifest` | a map fans out over a `PathManifest` node | file-audit's `path\|fingerprint` convention: item strings are the cache keys; bare paths never invalidate on content edits |
| `lint-map-without-budget` | a flow has a map node but no explicit `budget` | fan-out width is decided by runtime data; the spawn budget is the only ceiling |
| `lint-argv-prompt` *(config)* | a reachable stanza uses `prompt_via = "argv"` | the 59,028-char corrective prompt vs Windows' 32,767 cap; `ArgvTooLong` fails cleanly, stdin removes the ceiling |
| `lint-serialized-map` | a map with `concurrency > 1` whose items are not readonly | items hold the `tree` token and serialize anyway; the fan-out buys nothing and reads as a hang |
| `lint-concurrent-heal-rollback` | two healing gates with `rollback: true` whose windows can overlap | a rollback discards every path changed since ITS baseline, not just its target's, so the gates undo each other; recorded twice on `webapp-local`, the second time exiting 0 with half the deliverable deleted |
| `lint-missing-write-scope` | a write-capable work node declares no `spec.writes` | prose scoping is advisory text a model rationalizes past under gate pressure; becomes a verify ERROR at `format_version` 1.1 |
| `lint-unscoped-writes` | `"writes": ["**"]` without `spec.writes_rationale` | whole-tree access is sometimes right (a run-time-parameterized target) but it should be a written decision, not an omission |
| `lint-ungated-mutation` | a write-capable work node with no gate or approval on either side of it | nothing authorized the change and nothing can block it; silenced by saying "ungated" in the flow `description` when a human reads the output directly by design |
| `lint-live-diff-per-phase` | more than one node captures the live tree (`worktree_diff`), or even ONE does so inside a healing gate's loop body | shell nodes re-run on resume, so phase 1's capture re-runs against phase 2's tree and a passed review re-bills contaminated; a loop body re-runs every round, so its capture is wrong from round 2 even alone; use `node_diff --node` (consumer report 2026-08-13; parity 2.1 finding 13) |
| `lint-on-exhausted-pass` | a gate declares `heal.on_exhausted: "pass"` | it converts a blocking gate into a passing one after N failed repairs — legal for a refinement loop, but every gate that can wave work through gets named so a flow review sees the full list (PROPOSAL-taskflow-parity-tiers 2.1, finding 8) |
| `lint-tools-drops-result-channel` *(config)* | a stanza attaches an `--extension` but its `--tools` list omits `submit_result` | the allowlist covers extension tools, so the guard's structured-output channel silently disappears and the envelope stops being enforced (consumer report 2026-08-13) |
| `lint-persona-not-readonly` *(repo)* | a node names a persona whose frontmatter declares `readonly: true` but sets neither `spec.readonly` nor `spec.writes` | `spec.persona` and `spec.readonly` are independent fields, so "you fix nothing" personas silently keep full write tools and the `tree` token; `readonly: true` in the persona file is the self-documenting signal (consumer report 2026-08-14) |

## The gate library (`python -m lockstep.gates.<name>`)

Deterministic gate bodies as tested programs instead of embedded `python -c`
one-liners. Each prints exactly one Verdict object to stdout and exits 0 (a
blocking verdict is a result, not a failure); unreadable inputs become blocker
findings, not crashes; thresholds and paths stay in the flow file as argv —
the modules are tools, not policy. Shipped in the package (not `contrib/`) so
starter flows work against any target repo where lockstep is importable.

- `pytest_verdict` — ruff (if present) + pytest; blocker per failed check.
- `block_on_severity --at major --node review` — Finding[] → Verdict at a
  threshold; `--node` resolves a sibling's result via `LOCKSTEP_PHASE_DIR`.
- `required_sections <doc> "Goal, Risks"` — markdown headings present.
- `version_sync [--changelog F] [--tag vX.Y.Z]` — `__version__` vs pyproject
  vs changelog heading vs intended tag (the r7 0.2.0-vs-0.3.1 class).
- `citation_check <doc> --sources M | --paths ROOT [--per-section]` — `[S#]`
  ids resolve / `[artifact: path]` files exist; a citation-free document is a
  blocker. `--doc-node` / `--sources-node` read sibling results and flatten a
  map's aggregated JSON back to prose.
- `numbers_check <doc> --from collector.json | --from-node ID` — every
  numeral in the prose appears in a collector's output (dates, times, dotted
  versions, and integers ≤ 12 or year-like are allowed; `--allow REGEX` masks
  more).
- `coverage_delta --baseline F` — `totals.percent_covered` non-regression.
- `fingerprint_check <orders.json>` — every entry's file still matches the
  fingerprint it was approved against (the codemod-apply staleness gate).
- `pi_guard_smoke [--probe-node ID] [--escape-path P]` — did the pi extension
  actually block the probe's out-of-scope write and record a verdict
  (ADDENDUM-A A.3.3: it reads the guard's `verdicts.jsonl`, never the model's
  claim about being blocked). Removes the escape file it finds, so a failed
  smoke does not poison the next run.
- `tournament_pick --candidates a,b,c "{steps.judge.json}"` — a tournament
  judge's pick → Verdict. Blocks when `winner` is null (no candidate met the
  bar — crowning the least-bad is the quiet untruth the gate exists to stop)
  and when the winner is an id outside `--candidates` (a model-output defect
  that would otherwise surface one node later as a publish-step failure). The
  judge's rationale rides in the verdict reason either way. Worked example:
  `flows/starter/tournament-judge.tg.json`; see the tournament pattern below.
- `scoped_checks --run "ruff check {files}" [--run …] [--suffix .py]` — runs
  each check over ONLY the files this run changed (`git status --porcelain`,
  `{files}` expanding to one argument per file, the command skipped as a pass
  when nothing survives `--suffix`). A nonzero exit becomes one blocker
  carrying the output tail. See the debt rule below.

## The probe library (`python -m lockstep.probes.<name>`)

The sibling of the gate library, and the distinction is the point: a **gate
decides** (emits a `Verdict`, the driver branches on it); a **probe observes**
(emits text, a readonly node judges it). Every probe prints to stdout, **always
exits 0** — a command that failed is an observation, not a broken node — and
never writes to the workspace.

- `worktree_diff [--base HEAD] [--max-lines N]` — `git status --short
  --untracked-files=all`, the diff against the base, and the full contents of
  CREATED files (untracked, so absent from any diff). Truncation says it
  truncated. Reports the tree **as it is now**; see the warning below before
  using it more than once in a flow.
- `node_diff --node <id> [--run-dir D] [--max-lines N]` — what ONE STEP
  changed, read back from the two git tree objects the engine recorded for it.
  Deterministic: the same answer on every resume, in every later phase. The run
  dir is derived from `LOCKSTEP_PHASE_DIR` (exported to every spawn), so a flow
  node needs no path. Requires the target node to declare `spec.writes`, hold
  the `tree` token, and have succeeded — those are exactly the conditions under
  which the engine already computes both trees, so recording them costs
  nothing. A node that changed nothing, an unknown node and a pruned tree
  object each report themselves and still exit 0.

> **One live capture per flow.** `worktree_diff` describes the tree at the
> moment it runs, and shell nodes ALWAYS re-run on resume (SPEC §0.1.7). In a
> single-phase flow — implement, capture, review — that is exactly right. In a
> multi-phase one (capture, review, gate, remediate, capture, review, gate) the
> first capture re-runs on the first resume after phase 2 has written anything,
> now reporting phase 2's tree; the reviewer's prompt embeds that text, so its
> input hash legitimately moves, and a review that had PASSED re-bills and
> comes back with violations that never existed. Reported live at a cost of two
> full restarts. Use `node_diff --node <the step that made the change>` for any
> flow that reviews more than one phase; `verify --lint` warns
> (`lint-live-diff-per-phase`) when a flow captures the live tree twice.
> Worked example: `flows/starter/two-phase-remediation.tg.json` (prove it, then
> fix it — each phase reviewed on its own change).
- `command_output "<cmd>" [--label repro] [--timeout S]` — run one command,
  report exit code and output. The command arrives as one string (it comes from
  `--arg`) and is `shlex`-split with POSIX rules off on Windows. Output is
  capped middle-out, keeping both ends: a traceback's cause is at the top and
  its assertion at the bottom.

**Why they exist.** `readonly` has to remove every write vector to be worth
anything, and shell execution is one — so a readonly reviewer cannot run
`git diff`, and a readonly diagnostician cannot run the repro. Moving that one
command into a shell node hands the judgement node its input as DATA, and buys
three things on the way: the observation is deterministic, it is hashed and
cached like any other node, and it survives in the run directory as evidence.
The alternative — leaving the node write-capable so it can run commands — costs
the `tree` token, and with it the parallelism and the guarantee.

## Writing a gate that earns its place

Everything below was learned by running `flows/demo/sudoku-local.tg.json`
against a local model and watching the gate be wrong in a different way each
time. A gate is a program you are asking to refuse work; these are the ways
that goes wrong.

**A property you do not check is a property you do not have.** The first
sudoku gate checked that a generated puzzle was legal and solvable, and passed
a generator that returned the *same puzzle's solution* every time — it built
one canonical board and cut different holes in it. Nothing was broken; nothing
had been asked. Write the `pass_reason` as a list of the properties actually
established (`"a legal 25-clue puzzle with exactly one solution (3 distinct
puzzles, 3 distinct solutions)"`), because reading it back is how you notice
what is missing.

**Check the behaviour, never the convention.** That same gate demanded
`solve(grid)` *return* a completed grid. The model kept writing
`solve(grid) -> bool`, mutating in place, which is what every sudoku tutorial
writes; three attempts and two heal rounds could not talk it out of it. A gate
that rejects a working implementation over a calling convention burns the heal
budget on nothing. Accept either shape and check the thing you care about.

**A gate wired to an ABSOLUTE target owns the repository's debt.** `ruff check .`
or a named test file blocks on pre-existing failures in files the run never
touched, and a false block is not free: it is a full heal round — rollback,
plus a re-run of the implementer whose work was just discarded. Observed at
40 minutes a round. Two mechanisms, and the choice is per check:

- **Scope the check** to the run's own changes:
  `python -m lockstep.gates.scoped_checks --run "ruff check {files}"`. Right
  when the check is per-file.
- **Baseline the gate** — `"baseline": true` in the gate's `spec` (gate role
  only). The body runs once against the PRE-RUN tree; at evaluation the
  engine subtracts those findings on an exact `(file, claim)` match, so a
  block whose findings ALL predate the run flips to pass. Right when the
  check is whole-tree and cannot be scoped per-file (a build, a type check).
  The stored result is the ADJUDICATED verdict, so `{steps.<gate>.json…}` and
  `when` conditions downstream read what `state.verdicts` recorded. A
  baseline gate's `cmd` may not reference `{steps…}`
  (`baseline-gate-references-steps`) — it measures a tree in which no step has
  run yet. The baseline body spends from the same budget as any other spawn.

**A gate that runs model-written code must not be able to hang.** Model-written
backtracking loops forever surprisingly often. When it does, the driver kills
the gate at `timeout_s`, retries, gets no verdict either time, and terminal-blocks:
a timeout is not a valid verdict, so it cannot heal (§9.4.3). The reason names
the timeout and the remedy, but the run is over with budget still on the table.
Size `timeout_s` from a measured run, put `retry` on the gate when the
command's duration varies, and run untrusted code in a **child process with its
own clock** so the gate can turn the hang into a finding that names the
function instead of dying with it.

**Judge with your own code, not the code under test.** Asking a model's solver
to certify its own generator's output checks nothing. The uniqueness check
counts solutions with the gate's own backtracking; that independence is the
whole reason the count means anything.

**`fix_hint` is the next prompt.** A heal round appends the gate's findings to
the target's prompt verbatim, so a finding is an instruction, not a diagnosis.
`"the puzzle has two solutions"` teaches nothing; `"after removing a cell,
count the solutions; if there is more than one, put that cell back"` is a fix.
Put the *evidence* in `evidence` (`"r3c7 can be 4 or 5"`) and the *instruction*
in `fix_hint`.

**Normalise at the boundary; do not heal a formatting detail.** Small models
wrap source in a ``` fence however plainly the prompt forbids it. That is a
shell node's job (`contrib/save_result.py --strip-fence`), not three heal
rounds of a correctness gate. Reserve the heal budget for things that are
actually wrong.

**Test the gate before you run the flow.** It is an ordinary program: run it
against a known-bad input and a known-good one first. Every round trip through
a model to discover that your gate has a bug is a round trip wasted.

**Two healing gates with `rollback: true` must not heal concurrently.** A
rollback's scope is every path changed since ITS baseline (SPEC §9.4.4) — not
just the paths its own target wrote. Two gates that snapshot the same tree and
then heal in parallel therefore discard each other's output. Seen on
`webapp-local`: the backend gate's restore removed the frontend's module three
rounds running, and the frontend gate then blocked on a file the other branch
had deleted — three wasted heal rounds and a diagnosis that pointed at the
wrong node. `heal-target-overlap` does NOT catch this, because the targets are
disjoint; it is the baselines that collide. The fix is a dependency edge that
serialises the branches, even where no data flows along it.

## Harnesses with no file tools, and choosing a model per node

A harness that can only print — `ollama run`, or any bare model CLI — is a
first-class executor. The node answers on the §8.3 **stdout channel** and a
shell node writes the file:

```jsonc
{ "id": "core", "kind": "harness", "output": "text",
  "spec": { "executor": "local-coder", "task": "…" } },
{ "id": "save", "kind": "shell", "depends_on": ["core"],
  "spec": { "cmd": ["python", "contrib/save_result.py",
                    "--node", "core", "--out", "out.py", "--strip-fence"] } }
```

Two things decide whether that works:

- **`output: "text"` and the stanza's `json_field` interact.** A text node on a
  stanza that declares `json_field` is unwrapped out of the harness envelope; a
  text node on one that omits it ("omit for raw") takes stdout verbatim. Get it
  wrong on a raw harness and the driver used to hand you whichever JSON-looking
  literal appeared last in the source — a correct module arriving on disk as
  `[]` (fixed, and recorded in DEVIATIONS).
- **The model is per NODE.** One stanza per model, chosen with `spec.executor`.
  This is load-bearing, not decoration: on the sudoku flow a 14B failed the
  uniqueness requirement in four attempts and three heal rounds, and a 35B
  passed it on the first. Put the hard node on the bigger model and leave the
  cheap ones cheap. Harnesses mix in one graph as freely as models do.

### When the harness has tools

`ollama run` cannot write; pi can, and does. Run the same flow on pi and the
node behaves differently in three ways that matter to the author:

- **The result arrives on the FILE channel.** pi obeys the footer and writes
  `result.txt` into the phase directory, so the stdout fallback never runs.
  That is the better channel and it needs nothing from you.
- **The phase directory becomes the agent's scratchpad.** On the sudoku run pi
  left `test_debug.py` … `test_debug5.py`, `test_final.py` and a `__pycache__`
  beside its result: it wrote tests, ran them, and debugged itself. That is
  good behaviour and it is also what your step drawer will list as artifacts.
  Nothing is wrong; expect it, and do not treat the phase dir as yours.
- **Its stdout may be empty for the whole node.** pi buffers. On a fifteen
  minute node both logs sat at zero bytes, so the only liveness signal was the
  scratch files appearing (ACTIVITY reads those now). **If a node will run more
  than a couple of minutes, ask for progress in the task text.** The footer
  invites `progress.jsonl` with "optionally, you MAY", and an agent takes that
  at face value — the invitation is not enough for a node whose silence the
  reader has to interpret.

And size `timeout_s` from a measured call, not a guess. The 35B took 14m55s on
its first attempt through pi; the 900s default would have killed it at 15:00
and charged the run for the attempt.

## Interpolation (§7)

`{args.K}` · `{steps.ID.output}` (raw text) · `{steps.ID.json}` /
`{steps.ID.json.a.b}` (compact-serialized) · `{item}` / `{item.field}` in a
map body · `{previous.output}` (exactly one dep). `{{` escapes `{`.

- In HARNESS prompts, interpolated values are fenced as untrusted data and
  spill to a file above `max_interp_chars` (default 20000; the node gets the
  path). In SHELL argv they arrive raw — and `.json` leaves arrive
  JSON-encoded (quoted); `json.loads` the argv if you need the bare value.
- `when` grammar is exactly `{ref} ==|!= <JSON literal>` compared as
  compact-JSON strings: `== true`, `== "foo"` (quotes required), `== null`
  (also matches a skipped upstream).

## Large datasets and artifacts

The default failure here is not a crash — it is a flow that quietly costs ten
times what it should, or one that re-bills its whole corpus because a single
byte moved. Six rules, in the order they bite.

**1. Pass a PATH, not the payload.** A harness with tools can open files. If a
node needs a 4 MB CSV, give it the path in the prompt and let it read; do not
interpolate the contents. Interpolation puts the bytes in the prompt, in the
input hash, and in your bill. `spec.context` and `{steps...}` are for values
small enough that you *want* them pinned into the hash.

**2. Know what `max_interp_chars` does and does not protect.** Above the cap
(default 20000) a value in a HARNESS prompt spills to a file and the prompt
gets a stub path — the node reads it if it needs to. **Shell argv is not
capped and does not spill**: the raw string goes straight into the command
line, where Windows stops it near 32k and the spawn fails with exit 127. So
aggregate large data in a shell node by having it read a FILE, not by
interpolating a big `{steps.X.json}` into its argv.

**3. Fan out over a manifest, never over a blob.** The shape is: a shell node
emits a `PathManifest`, a `map` node takes one item each. This is the only
construct whose cost scales with the *changed* part of the corpus rather than
its size, because each item caches on its own hash. `flows/starter/file-audit`
is the worked example.

**4. Fingerprint the items or the cache is a lie.** Item strings ARE the
per-item cache keys. `docs/a.md` never invalidates when `docs/a.md` changes;
`docs/a.md|9f2c1b…` does. Emit `path|content-fingerprint` and tell the item
prompt to split on the last `|`. `lint-map-over-manifest` exists for this.

**5. Wide fan-outs need `optional: true` and a real budget.** Without
`optional`, one unreadable file fails the map node and discards every sibling
that already succeeded — and you have paid for them. With it, the failed slot
arrives as `{"status": "failed"}` in the results array, so **say so in the
consumer's prompt** or it reads a failure as a clean file. Set
`budget.max_agent_spawns` from the widest realistic fan-out plus heal rounds
and corrective re-spawns; `lint-map-without-budget` fires if you forget.

**6. Big outputs belong in files, and in `spec.writes`.** A node producing a
large artifact should write it and return a short summary — the §8.3 result
channel is not a delivery mechanism. Declare `spec.writes` so a stray write is
quarantined instead of merged into the run, and so the approval pane can show
`touched-<attempt>.txt` instead of a diff nobody reads.

One caching consequence worth internalising: the FULL pre-spill value is
hashed even though the prompt only carries a stub (SPEC §7, deliberate). That
is what makes the cache honest — but it also means a one-character edit
anywhere in a large interpolated value re-runs that node and every descendant.
If the payload changes often and the node does not truly depend on all of it,
interpolate a fingerprint or a summary instead of the thing.

## Patterns you already have (reduce, parallel, tournament, loop)

Names other DAG runners give to features that are already expressible here —
written down so nobody goes hunting for a keyword that deliberately does not
exist (adopted from PROPOSAL-taskflow-parity-tiers §1–§2, 2026-08-13: no new
roles for any of these).

**Reduce.** A map node's own result IS the collected array: the engine writes
one JSON array holding every item's result in manifest order, with a failed
`optional` slot arriving as `{"status": "failed"}`. Any downstream node that
consumes `{steps.<map>.json}` is therefore a reduce — no role, no keyword,
nothing to enable. `flows/starter/file-audit.tg.json`'s `arbiter` is the
worked example: it flattens, deduplicates, and adjudicates the per-file
Finding arrays into one Verdict. Two things to carry over from it: tell the
reducer what a failed slot looks like (or it reads one as clean data), and
raise `max_interp_chars` — a collected array is exactly the kind of value
that spills.

**Parallel.** Wave scheduling is the default: every node whose dependencies
are settled dispatches in the same wave, bounded by `--max-workers` (and a
map node's `concurrency` for its items). What actually needs authoring is the
INVERSE — why nodes serialize when you expected fan-out: any two nodes that
can write the tree share the `tree` exclusive token, so write-capable nodes
run one at a time by design, and a "parallel phase" of writers is a slow
sequence wearing a parallel name. `readonly: true` is what buys fan-out — it
is the declaration that licenses the scheduler to drop the `tree` token, and
the stanza's `readonly_argv` is what makes it true rather than polite. That
is why every judgement node in the starter flows is readonly and why
`plan-adversarial`'s reviewer pair genuinely runs concurrently. If a phase is
serializing, the fix is almost never more workers; it is making the nodes
that only judge stop being able to write.

**Tournament.** N competing candidates + a judge is `plan-adversarial`'s
shape with the reviewers replaced by rival authors: same fan-out, same
fan-in. `flows/starter/tournament-judge.tg.json` is the template — three
readonly candidates answer one brief from deliberately different angles in a
single wave, a readonly judge ranks them against stated criteria (a custom
`TournamentPick` contract via `contracts_module` — the starter set's worked
example of that field), `lockstep.gates.tournament_pick` blocks a null or
invented winner, and a shell node republishes the winning answer verbatim as
the flow's result. Candidates are readonly for the same two reasons stacked:
it is what lets them share a wave, and N writers to one shared tree is not a
tournament, it is a fight — which is also why the deliverable is the winning
ANSWER, not a change to the repo (racing writers needs per-node workspace
isolation, deliberately not built; see the proposal). What keeps a tournament
honest is the judge's prompt: it must pick on the stated criteria and quote
the decisive passages, or the flow has paid for N answers and gotten a coin
flip.

**Loop.** A healing gate already is a loop: it re-marks its targets pending,
folds its findings into their prompts as feedback, and re-runs up to
`max_rounds` — with the round number engine-composed into the heal text
("This is repair round N of M"), so the body always knows where it stands.
`rollback: false` is the whole difference between *heal* ("undo the bad
attempt") and *loop* ("build on the last one") — and the target's prompt must
say so, or the drafter treats its own round-1 work as someone else's mess.
`heal.on_exhausted: "pass"` closes the loop's other end: rounds run out and
the best-so-far is accepted — never silently. The stored verdict is rewritten
to `accepted after N rounds without resolving: <reason>` (downstream
`{steps...}` references and `when` conditions read the adjudicated pass, the
unresolved findings stay in it as the record of what was accepted), `status`
shows the same reason, and the journal carries a `heal-exhausted-pass` event.
A gate that times out or emits garbage never "exhausts to pass" — that is a
gate that never decided, and it blocks whatever `on_exhausted` says. Guards:
`on_exhausted: "pass"` is a §6 ERROR with `rollback: true`
(`on-exhausted-with-rollback`) and without rounds
(`on-exhausted-without-rounds`), and `verify --lint` names every gate that
uses it (`lint-on-exhausted-pass`) — on the starter it fires by design; a
refinement loop is exactly a gate that is not a quality bar, and the lint
makes each one legible in review. One trap, from the 2026-08-13 consumer
report: a live `worktree_diff` capture inside the loop body draws
`lint-live-diff-per-phase` even when it is the flow's only capture — the body
re-runs every round, so from round 2 the capture describes the cumulative
tree, never the round it evidences. `flows/starter/refine-loop.tg.json` is
the worked example: `node_diff --node draft` per round, deterministic
severity gate, and a final node that prints the gate's reason so an exhausted
acceptance is the flow's last words rather than a quiet exit 0.

## Retry, budget, caching

- Harness nodes DEFAULT to `retry: {"max": 2, "backoff_ms": 60000}` (absorbs
  provider 429/529s). An explicit `retry` overrides entirely; shell nodes
  default to `max: 0`.
- `budget.max_agent_spawns` counts EVERY token-costing spawn including heal
  rounds and corrective re-spawns — always set it; leave headroom.
  `budget.max_run_minutes` may be exceeded by one in-flight `timeout_s`.
- **Editing a flow file changes `flow_hash` and starts a new lineage** — every
  completed node re-runs (and re-bills). Finalize budgets/retries BEFORE the
  first run; prefer `lockstep steer` over editing mid-lineage. When you do have
  to edit, `run <flow> --seed <old_run_dir>` serves every node whose
  `input_hash` still matches a successful result in the old run and runs the
  rest — so a one-word prompt fix costs that node and its readers, not the
  graph. It is hash-keyed, so nothing is trusted: shell nodes and map items are
  never seeded, and `status` names what was inherited.
  **A seed trusts a prior RESULT, not a prior TREE.** It is not a "start over
  cleanly" mechanism: a node whose recorded output described a tree that has
  since moved is served again if its own inputs still hash the same. The one
  case that would hurt most is already closed — a probe that captures live tree
  state is a shell node, and shell nodes always re-run — but a harness node
  that *reasoned* about the tree can still be inherited. When the tree state is
  what went wrong, `--fresh` is the honest answer.
- **A heal target can carry notes into its own retry.** The footer invites
  every write-capable harness node to append durable findings to
  `attempt-notes.md` in its phase dir ("the auth test failure pre-dates this
  change, confirmed against HEAD~1"); the next heal round's prompt includes
  them (tail-capped), so the retry does not re-spend what the first attempt
  already established. When a node's expensive product is *verification*, say
  in the task text what is worth writing there.
- Cache correctness, not reproducibility: a node re-runs iff its inputs
  changed. To make caching honest for file-content work, put a content
  fingerprint IN the item/prompt (see `flows/starter/file-audit.tg.json`).

## Write scope (`spec.writes`)

**Every mutating work node declares one.** A narrow prompt is advisory text a
model can rationalize past under gate pressure — a node scoped in prose to two
templates edited five core modules chasing a pre-existing failure the gate had
named. `spec.writes` is the mechanical control; `verify --lint` warns when a
write-capable work node has none (`lint-missing-write-scope`, a verify error
at `format_version` 1.1).

It reaches the spawn as `LOCKSTEP_WRITE_SCOPE` (a JSON array), so an in-harness
extension can *prevent* a stray write; the driver itself never sees tool calls,
so it **detects** — by diffing a baseline tree taken before the spawn, while the
node still holds its exclusive tokens.

```json
"spec": { "task": "…", "writes": ["CHANGELOG.draft.md"] }
```

The three honest declarations:

- `"writes": ["docs/plan.md", "src/x/"]` — the paths, directory prefixes, or
  globs it may write.
- `"writes": []` — **this node writes nothing** (a probe, a collector, a
  printer). The key is **presence-keyed**: a declared-empty scope is a real,
  enforced constraint and any change quarantines. An ABSENT key is the old
  unconstrained behaviour. (Before 0.7.0 `[]` was read as unconstrained, so
  the tightest possible declaration disabled the check.)
- `"writes": ["**"]` + `"writes_rationale": "…"` — deliberate whole-tree
  access, for a target that is genuinely decided at run time (a generic
  implementer told which files to touch by `--arg task`). The rationale is
  required (`lint-unscoped-writes`) so a reviewer can see the missing scope
  was a decision.

**A scope may interpolate `{args.NAME}`** — `"writes": ["docs/{args.name}"]`
— so a parameterized flow scopes to the file it was told to write instead of
falling back to `["**"]`. Args only, and the reason is the whole point of a
permit: args are fixed before the run starts and chosen by whoever started it,
while a `{steps...}` reference would let a node's own upstream output decide
what that node may write — a permission the graph could widen by writing a
different answer. `verify` rejects those (`dynamic-write-scope`), and the
renderer refuses them again, because a scope is the wrong place to assume an
earlier check ran. The rendered result faces the same absolute-path and `..`
rules the written entry does, so `--arg dir=../../etc` cannot escape either.

What to know before you declare one:

- **A violation is quarantined, not just reported.** The blocked attempt is kept
  as `phases/<node>/out-of-scope-<attempt>.patch`, each violating path is
  restored to its baseline or moved into `out-of-scope-<attempt>/`, and the node
  fails with a message naming every path and its outcome. Declaring a scope is
  therefore a decision about what may be *reverted*, not only about what gets
  flagged. Rollback still never deletes.
- **Entries are repo-root-relative and match a path, a directory prefix, or a
  glob** — `verify` rejects absolute or escaping entries (`bad-write-scope`)
  and entries referencing anything but an arg (`dynamic-write-scope`).
  Note that the matcher is `fnmatch`, so `*` crosses `/`.
- **`verify` warns `write-scope-unenforced`** when the node holds no `tree`
  token — today that means a `readonly` node. Every other write-capable kind,
  shell included, takes the token, so its scope is enforced.
- **Not on a map node** — `write-scope-on-map` is a hard error: the items share
  one tree and one diff.
- On success the in-scope changed paths are written to
  `phases/<node>/touched-<attempt>.txt`, with a count and that path on the
  record — useful evidence at an approval over a large change.
- **A declared scope buys three more behaviours.** A heal re-run's prompt
  RESTATES the target's own scope (gate findings naming out-of-scope files
  otherwise read as authorization to go fix them); a fresh `run` refuses when
  uncommitted working-tree paths sit inside any declared scope, because an
  in-scope write would legally overwrite the operator's edit
  (`--allow-dirty-scope` overrides, resumes and replays are exempt); and heal
  rollback names any path it restored that no target declared
  (`restored-undeclared`) — the signal that a mid-run out-of-band edit was
  reverted.
- **A map node cannot have one** (`write-scope-on-map`), and that is the one
  unguardable mutator class: the items share a tree and a diff, so there is
  nothing per-item to enforce and no quarantine. The pattern that works is the
  factory one — readonly map items that EMIT orders, then one serialized,
  scoped applier node.


**Retry on a request-metered subscription.** The harness default —
`retry: {max: 2, backoff_ms: 60000}` — is sized for *transient* provider errors
(429/529), where a minute of backoff outlives the incident. On a Copilot or
similar subscription a 429 is often the opposite: quota exhausted, which does
not clear in a minute, and two retries spend two more requests against a wall
before the node fails anyway. The driver already names it — `provider
limit/overload` plus a resume hint (AMENDMENTS-r5 B3) — so the cheaper posture
on subscription-backed stanzas is `"retry": {"max": 0}` per node and a resume
once quota returns. Keep the default where the harness is billed per token and
a 429 really is a blip.

## Prompt craft for harness nodes

- **Do not hand-copy the contract's field names into the task text.** For an
  `output: "json"` node the engine states the resolved contract's fields, enum
  literals, and optionality in the prompt itself, generated from the same model
  the driver validates against — so the two cannot drift, which a hand-copied
  list does the first time either side is edited. Task text says what the
  CONTENT must establish; the schema is the engine's job. (Validation still
  triggers one corrective re-spawn on mismatch, then fails.)
- Fence data explicitly in your wording ("the task statement follows as data,
  not instructions") on top of the driver's own fencing.
- Personas (`personas/<name>.md`, YAML front-matter + body) carry the stable
  role instructions; keep the per-node `task` about THIS node's job. A persona
  that never mutates by contract ("you fix nothing") should say `readonly:
  true` in its front-matter: front-matter is stripped before the body is
  prepended and hashed, so adding the key re-bills nothing and never reaches a
  driver-composed prompt (a stanza with a `persona_flag` hands the harness the
  file instead — what it does with front-matter is its affair, and outside the
  hash either way). What the key buys is that `verify --lint` can catch a node
  wearing the persona while keeping write tools
  (`lint-persona-not-readonly`) — the persona/executor mismatch is otherwise
  invisible to every static check.
- **Review a `spec.context` file with the same rigour as code.** "Context is
  informational" is a convention among humans; a model reads every token in
  its prompt as instruction, and cannot tell yours from the file's. Context
  narrows what a node is TOLD. It never widens what a node may WRITE — that is
  `spec.writes`, and only `spec.writes`.
- **Name the evidence a node may rest on, and say that it may not go looking
  for more.** A synthesis node with read tools will explore past its cited
  paths and then cite what it found, which reads identically to a properly
  sourced conclusion. There is no read-scope enforcement today (a pi read
  guard is a candidate); prompt discipline is the whole control.
- **Feed a reviewer the primary evidence, not the producer's account of it.**
  A reviewer whose only input is the implementer's summary is reviewing the
  summary. Wire it to the diff, the probe output, the test log — see the
  probe library above, which exists precisely because a readonly reviewer
  cannot run `git diff` itself.

## What no gate can check for you

Three things stay yours, and each one has cost a real run:

- **A contract-valid `pass` can rest on a narrower check than the stated
  scope.** Nothing catches "checked less than claimed" — reading the
  `pass_reason` back as a list of properties actually established is how you
  notice.
- **One adversarial review is not exhaustive.** Repeat runs of the same
  reviewer over the same artifact return largely disjoint findings. Budget for
  more than one pass on anything that matters, rather than reading a single
  clean verdict as coverage.
- **A hand-patched defect is legitimate closure — if you declare it.** When
  heal rounds are exhausted, fixing the named defects by hand and re-running
  is a fine way to finish. Cite which finding motivated each correction, and
  never present a hand-patched result as the graph having passed.

## Authoring for a human decision (cockpit-facing flows)

A flow a non-programmer will drive has **four** extra obligations. Templates:
`flows/starter/clarify-gate.tg.json`, `flows/starter/evidence-approval.tg.json`
(with its `.labels.json` sidecar), and the full shape in
`flows/demo/repo-hygiene-demo.tg.json`.

**Clarification gates.** Domain questions travel through a gate with
`"heal": {"max_rounds": 0}` whose findings carry `category: "question"` and
whose `claim` is one line a non-programmer can answer without reading code — it
is read to them verbatim. A healing clarify gate is a bug: heal fires
in-process and re-runs the target with the questions still unanswered. Say in
the gate's prompt that a `--- steering ---` block containing a human's ruling is
authoritative and ends the question, or the gate re-asks forever.

**The evidence rule.** Every approval must be preceded by a shell node that
renders a **mechanical extract of the deliverable** — the thing itself, not a
summary of it. Shell stdout goes to `stdout.log`, not the terminal, and the
approval prompt is one bare line, so the extract must be written to
`<run_dir>/approval-evidence.txt`; `contrib/approve.ps1` prints it before the
prompt. `contrib/render_evidence.py` does headings/diffstat/full-text; a
domain-specific extract is better (see `contrib/demo/hygiene_evidence.py`, which
stratifies: counts, then every structural change, then everything the system was
unsure about, then a deterministic sample of what it was sure about). Sampling
must be seeded by the content: the same manifest has to produce the same pane
twice, or approval means nothing. **A flow whose approval shows no evidence is
unsuitable for a non-programmer.**

**The decision packet.** Evidence answers *what*; a human also needs *how much*
and *can it be undone*. Pass both to `render_evidence.py`:

```jsonc
"cmd": ["python", "contrib/render_evidence.py",
        "--headings", "{args.deliverable}",
        "--diffstat", "--impact",
        "--approval", "approve",                    // which approval this is for
        "--reversible", "delete DRAFT.md; nothing else was touched",
        "--title", "{args.task}"]
```

`--impact` counts from `git status --porcelain -uall` — **not** a diff, because
`git diff` cannot see untracked files and a new deliverable is the normal case;
the count always equals the number of changes, with unnamed status codes
reported as `other` rather than dropped. Omitting `--reversible` renders *"not
stated by this flow"*, which is honest and reads as the gap it is. Deletions and
conflicts get their own line.

**Names and tiers, in a sidecar.** `flows/<name>.labels.json` (read only by the
cockpit's view layer, never by the engine, so it cannot change what runs, what
is cached, or what anything costs):

```jsonc
{ "nodes": { "preflight": "checking the plan is safe to apply" },
  "tiers": { "approve": "irreversible" } }
```

Node ids are engineering identifiers; MISSION shows the label instead when there
is one. Keep labels under 34 characters. A tier changes the evidence banner and
what evidence is *required* — `irreversible` makes the impact block mandatory,
rendering a missing one as `NOT CHARACTERISED` rather than as silence. **No tier
ever skips the human**: a tier that could quiet an approval would let a flow
author remove them by declaring one.

**The segmentation rule.** Nothing non-trivial may run downstream of an
approval, because everything after it executes in the human's own resume
process. A seconds-long shell node (copy the deliverable out, print a summary)
is fine; an implement phase is not — split it into two flows. `monolithic
sdlc-e2e` is unsuitable for the cockpit for exactly this reason.
`contrib/quiescent.py` enforces the distinction mechanically.

Also worth knowing: a node may write one plain line to
`phases/<node>/mission.txt`, which the cockpit's MISSION pane renders verbatim
(a file copy — no model, so it keeps the pane's trust status). Use it for
counters a human actually cares about: `catalog: 5,214 files, 4,102 by rule,
812 need judgment`.

**Custom contracts load by file path** (`path/to/module.py:Name`) — do NOT put
`from __future__ import annotations` in that module. The module is not
registered in `sys.modules`, so postponed annotations leave pydantic unable to
resolve `Literal`/`Optional` and every spawn fails validation. Use eager
annotations and `Optional[str]` rather than `str | None`.

## Sharing a long-lived process across nodes

A node may start a process that outlives it — a database connection holder, a
local service later nodes call. The lifetime is defined, and it is the same on
both platforms:

- **It survives its own node's clean exit.** The driver does not tear down what
  a node backgrounded just because the node returned.
- **It does not survive the run.** When the driver exits, crashes, or is killed,
  the process is reaped (on Windows by the kernel, via the node's Job Object).

Author to both halves. The first means you may share a resource across nodes;
the second means you may not treat one run's leftovers as another run's setup —
a later run must be able to start the holder itself, or the flow is not
resumable. This matters most when the shared resource is **locked**: a
single-writer database whose holder survives into the next run leaves a lock
nothing can take, which is worse than no holder at all.

Two things to get right when you do this:

- **Declare every path the holder writes**, not just the obvious one. A database
  usually has a sidecar — a WAL, a journal, a lock file. If `spec.writes` names
  only `data/x.db`, the sidecar reads as an out-of-scope write and is quarantined:
  moved aside, never deleted, but separated from the file it belongs to. Declare
  the sidecar explicitly or scope to the directory.
- **The holder is not a node, so no token governs it.** Write-capable nodes are
  serialised against each other by the exclusive `tree` token, but a backgrounded
  process runs alongside every later node by construction — including readonly
  ones, which take no token at all. If the store is single-writer, that
  concurrency is yours to design around; the scheduler cannot see it.

## Operational caveats

Windows argv limits with large interpolations, encoding rules for embedded
gate scripts, start-green requirements for test gates, and per-flow notes
live in `flows/starter/README.md` — read it before adapting a starter flow.
