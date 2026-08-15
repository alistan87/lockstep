# lockstep

A harness-agnostic driver for headless coding agents. `lockstep` executes a
**taskgraph** (`*.tg.json`): a declarative DAG whose nodes are run by pluggable
executors — a prompt handed to a headless agent harness (Claude Code, pi,
Copilot CLI) or a plain subprocess (`pytest`, `ruff`, a script).

The driver owns everything else: static verification before anything spawns,
topological scheduling with resource-exclusion serialization, per-node state on
disk, input-hash resume, schema validation of results, gate adjudication,
snapshot/rollback on heal, human approvals, budgets, timeouts. **The driver
never calls a model, never holds an API key, and never makes a network
request** — model access is whatever credential the spawned harness carries.

Creed, in order: *plans are data, not prose* · *machine checks before model
judgment* · *the model authors content, never control flow* · *no session to
time out* · *harnesses are replaceable config, not dependencies*.

Spec: `docs/spec/SPEC.md` (revision 3) as amended by `docs/spec/AMENDMENTS-r4.md`,
`docs/spec/AMENDMENTS-r5.md`, and `docs/spec/AMENDMENTS-r6.md` (all adopted; the later
revision wins). Implementation
departures: `docs/spec/DEVIATIONS.md`. Pi extension hooks (informative, binding for
pi nodes in this repo): `docs/spec/ADDENDUM-A-pi-hooks.md`.

## Quickstart

```console
$ pip install -e .            # Python >= 3.11; pydantic is the only dependency
$ lockstep init               # writes ./lockstep.toml — edit the argv templates
$ lockstep doctor             # probe each configured harness actually runs
$ lockstep verify flows/gated-build.tg.json
$ lockstep run flows/gated-build.tg.json --arg task="add a --version flag"
$ lockstep run flows/gated-build.tg.json --detach --arg task="…"   # a driver that outlives this shell
$ lockstep status runs/gated-build-<stamp>/     # incl. latest per-node progress; STALE if its driver died
$ lockstep active runs/                         # runs something is (or should be) driving; --all adds idle ones
$ lockstep steer runs/<run>/ implement "prefer the streaming writer"   # next checkpoint
$ lockstep cancel runs/<run>/ implement         # kills the node's process tree; no retries
$ lockstep resume runs/gated-build-<stamp>/     # after a crash or budget trip
$ lockstep wait runs/<run>/ --timeout 600       # block until the driver exits; exits with the RUN's meaning
$ lockstep verify flows/x.tg.json --lint        # + advisory anti-pattern warnings
$ lockstep verify flows/x.tg.json --config c.toml     # the SAME config the run will use
$ lockstep explain runs/<run>/ implement        # which hash inputs moved; why it re-billed
$ lockstep explain runs/<run>/ --graph          # whole-graph staleness dry run vs the current tree; zero spawns
$ lockstep run flows/x.tg.json --replay runs/<run>/   # recorded results; no spawns, no tokens
$ lockstep run flows/x.tg.json --seed runs/<run>/     # edited flow, new lineage, inherit what did not change
$ lockstep run flows/x.tg.json --seed runs/<run>/ --force-stale judge   # ...except judge + descendants: recompute
$ lockstep verify-trace runs/<run>/             # recompute the journal's hash chain
$ lockstep gc                                   # runs/ retention plan; dry-run unless --apply
```

Observation is deliberately plain: `tail -f` any `runs/**/phases/<node>/stdout.log`
or `watch -n2 lockstep status <run_dir>`. Richer read-only views ship in
`contrib/` — see [the cockpit](#driving-it-for-someone-who-does-not-code-the-cockpit).

## The taskgraph, in one example

`flows/gated-build.tg.json`: harness implement → shell test wrapper (emits
`CheckResult` JSON) → **shell gate** emitting a `Verdict` (a fully
deterministic gate — the preferred form whenever the check is
machine-decidable) with

```jsonc
"heal": { "max_rounds": 1, "targets": ["implement"], "rollback": true }
```

On `block`, the driver preserves the failed attempt as a patch, restores a
proactively-taken git baseline (untracked files included; created files are
moved aside, never deleted), re-marks every completed descendant of the heal
targets pending, appends the gate's findings (fenced as data) to the targets'
prompts, and re-runs — naming the round ("This is repair round N of M").
Rounds exhausted ⇒ exit 2, unless the gate declares
`heal.on_exhausted: "pass"`: the LOOP pattern accepts the best-so-far, with
the recorded verdict saying exactly that ("accepted after N rounds without
resolving: …") — never a plain pass. With `rollback: false` each round builds
on the last instead of undoing it (`flows/starter/refine-loop.tg.json`).

A heal round is the expensive unit in this system — a rollback plus a full
re-run of whatever produced the work — so two mechanisms exist to stop gates
spending one on failures the run did not cause. `"baseline": true` on a gate
runs its body once against the pre-run tree and subtracts those findings at
evaluation, so a block whose findings all predate the run flips to pass; and
`python -m lockstep.gates.scoped_checks --run "ruff check {files}"` checks only
the files this run changed. A gate pointed at `ruff check .` otherwise owns the
whole repository's debt. Between rounds, a target may leave durable notes in
`attempt-notes.md` in its phase dir — the retry prompt includes them, so a
second attempt does not re-derive what the first established.

## Starter flows

`flows/starter/` ships fourteen portable, adversarially-reviewed templates —
see its README for the full table and per-flow caveats:

- **SDLC**: `plan-adversarial` (author → two attacking reviewers → healing
  arbiter gate → human approval), `implement-heal` (implementer → deterministic
  lint+pytest gate that HEALS on failure → adversarial diff review →
  block-on-major gate), `bugfix-heal` (diagnose → fix → healing repro gate →
  review), `two-phase-remediation` (prove it, then fix it — each phase reviewed
  on its OWN change via `node_diff`, so no resume can show phase 1's reviewer
  phase 2's work), and `sdlc-e2e` (the whole chain).
- **Audit**: `file-audit` (map fan-out, one readonly auditor per file with
  content-fingerprint caching), `proposal-gate` (completeness gates for a
  human-owned doc — deterministic section check, then reviewers; no heal).
- **Cockpit fragments**: `clarify-gate` (a non-healing gate that asks a human
  what only they can settle), `evidence-approval` (the evidence-bearing terminal
  approval, with its labels sidecar).
- **Patterns** (0.9.0): `tournament-judge` (three readonly rivals answer one
  brief in parallel, a judge crowns at most one — or blocks rather than crown
  the least-bad), `refine-loop` (a healing gate as a LOOP: `rollback: false`
  builds on each round, `on_exhausted: "pass"` accepts the best-so-far on the
  record), `draft-then-review` (COMPOSITION: two saved flows as two
  `kind: "flow"` nodes — each child a real run under `<run>/children/`, one
  wallet, one tree, one worker cap).
- **Ops**: `pi-guard-smoke` (live-verifies the pi extension on a new machine),
  `retrospect` (gate-driven improvement from the friction report).

They double as worked examples of every major feature: shell vs harness nodes,
contracts, healing vs terminal gates, map, approval, args, budgets. The
repo's own dogfood flow is `flows/audit-spec.tg.json` (spec-vs-code audit).
Portable references: `docs/guides/FLOW-AUTHORING.md` (writing flows) and
`docs/guides/DRIVING-LOCKSTEP.md` (the drive protocol for orchestrator agents —
paste-ready for an `AGENTS.md`).

## The factory flows

`flows/factory/` covers repeatable **production** shapes rather than
build-one-thing runs — see its README for the table
(`PROPOSAL-factory-programme.md` is the design record):

- **Software**: `release-cut` (collect → changelog draft → version-sync gate →
  wheel build/install/import smoke → evidence approval → tag),
  `codemod-propose`/`codemod-apply` (bulk transformation as approved
  ChangeOrders, with a staleness gate that hard-blocks if the tree moved after
  the human read the proposal), `triage-intake` (reproduction-attempted
  TriageRecords that feed `bugfix-heal`), and a generated `harness-bakeoff`
  (`contrib/bakeoff_gen.py` — doctor catches flag drift, this catches quality
  drift).
- **Reports**: `research-report` (fingerprinted sources → per-source
  extraction → outline gate → per-section drafts → a deterministic
  **citation-integrity gate** → adversarial claim check → editor → approval
  over the report itself) and `status-digest` (deterministic collectors → a
  narrative whose every numeral must appear in a collector's output — the
  **number-provenance gate**; schedule it weekly), plus `run-postmortem`
  (every claim must cite a run-dir artifact that exists).

The deterministic gate bodies are tested programs in `src/lockstep/gates/`
(`python -m lockstep.gates.<name>` — see FLOW-AUTHORING for the library and
the `verify --lint` table).

Recorded runs become zero-token regression fixtures: `contrib/export_fixture.py`
(a scrubbed allowlist — it also clears the two machine-local fields inside
`state.json`) feeds `contrib/replay_suite.py`, which replays each fixture with
**strict `input_hash` matching**, so a mismatch after an engine or flow change
is the regression being hunted. `flows/selftest-replay.tg.json` is the one
committed fixture's source and the one flow that must stay **shell-only**: a
harness node's hash includes the local executor-config digest, so only an
all-shell flow records hashes that match on another machine. It doubles as a
zero-token check that the documents everything else cites still carry the
sections it cites them for. With no fixtures the suite reports `0/0 — NOTHING
WAS CHECKED` on stderr rather than a quiet pass; `--require-fixtures` makes an
empty net a failure.

## Local models

A harness with **no file tools at all** is a first-class executor. `ollama run`
can only print, so a node on it answers on the §8.3 stdout channel and a shell
node writes the file — nothing about caching, gating, healing or resume changes:

```toml
[executors.local-coder]
argv = ["ollama", "run", "qwen2.5-coder:14b", "--nowordwrap", "{prompt}"]
prompt_via = "stdin"          # omit json_field: this harness speaks raw stdout
```

**The model is per NODE, not per flow.** One stanza per model, chosen with
`spec.executor`. pi lists ollama as a provider, so a flow can mix models *and*
harnesses at zero cost:

```toml
[executors.pi-coder]
argv = ["pi.cmd", "-p", "--no-session", "--provider", "ollama",
        "--model", "qwen2.5-coder:14b", "{prompt}"]
prompt_via = "stdin"
```

`flows/demo/sudoku-local.tg.json` is the worked example — a playable sudoku with
no network, no credential and no token cost, whose solver runs on a 35B through
ollama and whose CLI runs on a 14B through pi, because the second job is
smaller. It is also the honest demonstration of what a machine check is for:
`contrib/demo/sudoku_check.py` runs what the model produced (in a child process
with a clock on it, because model-written backtracking loops forever
surprisingly often) and holds it to properties a sudoku either has or does not —
including that a puzzle has **exactly one solution** and that successive puzzles
**vary**, both judged by the gate's own solver rather than the module's. A
BLOCK hands its findings back to the generator as the next prompt.

## Driving it for someone who does not code (the cockpit)

`contrib/` is a layer for running lockstep **on behalf of a domain expert** — a
colleague who owns the judgment calls but will never write code, use git, or
type a command. It adds no engine capability; it is convention over the same run
directory, plus a handful of read-only views.

The whole design reduces to one trade: the assistant may spend your budget and
your attention; in exchange every number it quotes is one you can verify
yourself, and every decision is made from an artifact rather than its summary.
Four guarantees hold that up, and each is **structural rather than a rule
somebody follows**:

- **The assistant cannot approve anything.** Not by policy — there is no code
  path. Runs launch detached with unanswerable stdin, so reaching an approval
  auto-rejects and exits 6; that exit *is* the handoff signal. There is no
  `send-text` anywhere in the cockpit.
- **A decision is made from evidence, never narration.** A shell node renders a
  mechanical extract to `<run_dir>/approval-evidence.txt` before every approval,
  ending in the two facts that set how much care it needs: **blast radius**
  (`4 files - 2 edited, 1 new, 1 DELETED`) and **reversibility** (or an explicit
  *"not stated by this flow"*). The pane prints that, then the real prompt.
- **The reason for a rejection is an artifact too.** After `r`, the pane asks
  for one line and writes `<run_dir>/rejection.txt` in the human's own words —
  and `contrib/retrospect.py` reports it as drift if the assistant's journal
  never mentions it. Symmetry: the argument that made evidence a file applies in
  both directions.
- **Nothing you do can lose paid work.** The run and the assistant are separate
  processes; either can die without harming the other.

```console
$ contrib\start-cockpit.cmd                        # the only entry point, ever
$ python contrib\plan_card.py <flow>               # consent, backed by prior runs
$ pwsh -File contrib\cockpit.ps1 -Role mission -Follow    # the status board
$ pwsh -File contrib\cockpit.ps1 -Tui               # or one process, keyboard-driven
$ python contrib\mission_server.py                  # or the MISSION page on loopback, GET only
$ python contrib\quiescent.py <run_dir>             # is this safe to hand over?
$ pwsh -File contrib\cockpit.ps1 -RunDir <run> -Approve   # spawn the decision pane
$ python contrib\cost_report.py --compact <run_dir> # spend, in honest units
$ pwsh -File contrib\attention.ps1 -RunDir <run>    # toast when the run needs a human
```

Every view is a projection of `state.json`, the run's own `flow.tg.json` copy,
and `phases/<node>/*` — a field mapping with a fixed glossary, no model, no
second source of truth. The TUI and the web page share `contrib/mission_view.py`
with `cockpit.ps1`, and a test pins their glossaries to each other. **Decisions
never leave the terminal**: the page has no form, no POST handler, and no route
that writes.

### The MISSION page

`contrib/mission_server.py` is one page with four levels of disclosure and a rail
of recent runs, aimed at being a surface a domain expert opens *by choice*:

| level | what it shows | entry |
|---|---|---|
| **board** | headline, stat row, the collapsed step list, the spend meter, both cost blocks, ACTIVITY, and the evidence or the question card, verbatim, when one waits | on load |
| **timeline** | every step on a shared time axis, **in place of** the step list, with a table twin beside it. Annotated: rollback markers where the tree was restored, replaced attempts faded, and the critical path — the chain everything else waited for — edged | "show every step" |
| **step** | a drawer per step: names, sizes, attempts, cost — never stdout bodies | click a row |
| **raw** | node id, hash parts, what moved, the chain head — each with a one-line gloss, pinned by test against the domain-expert guide | "show the raw record" |

A left rail lists recent runs (`?run=<name>`), so a failed run and the one that
fixed it can be compared without restarting anything. The name is matched
against the directory listing rather than joined onto a path, and an unknown
one is a 404 rather than a silent fallback to a different run.

A missing piece of the cockpit is a **named case, not an empty chart.**
`reader_note()` distinguishes `cost_report.py` being unimportable (a copy that
left it behind, or a Python below 3.11 with no `tomllib`) from a `state.json`
caught mid-replace, and prints a plain sentence where the timeline or the cost
would have been. Riding over both with the same `except` drew a blank chart and
a column of dashes — indistinguishable from a run that did nothing.

Four properties that are structural rather than stylistic:

- **It renders with JavaScript switched off.** Every level is server-rendered,
  and the table twin is the accessibility path, the no-JS fallback and the
  surface the tests read — which is what keeps "no logic that can be wrong lives
  in the JS" a fact rather than a discipline. The client swaps server-rendered
  fragments and advances an integer; it formats no word and no time.
- **The heartbeat is the cheap route.** `GET /api/events?after=<n>` parses only
  the lines past the cursor; the expensive render is fetched only when the
  journal moved, the run changed, or a node is running. A quiet second costs
  0.4 ms and 80 bytes.
- **A refresh never lands on a reader.** It is skipped while text is selected,
  and open drawers, focus and the keyboard echo are restored across it.
- **Every response carries a run token.** A poll has no natural reset, so at a
  segment boundary the client discards its cursor rather than asking for
  `after=400` of a twelve-event run.

Colour is validated, not chosen: the four cost-stack hexes and the reserved
status steps are run through the data-viz validator against the page's own
surface, and every status colour ships with its icon and its glossary word, so
colour never carries meaning alone.

Read `docs/guides/COCKPIT-THEORY-OF-OPERATIONS.md` if you are the session
driving it, and `docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md` — which is what the
human was told, so it binds what you may say.

## Tools are part of the stanza (pi, and cost on a metered plan)

A harness's tool set is **argv**, which means it is configuration the driver
records, hashes and can differ per node — not something you ask the model for
in a prompt. pi takes `--tools` (allowlist), `--exclude-tools`,
`--no-builtin-tools` and `--no-tools`; claude takes `--disallowedTools`.

That is the lever behind three separate things:

- **`spec.readonly` is real enforcement on pi.** `readonly_argv = ["--tools",
  "read,submit_result"]` satisfies SPEC §6.11's argv-visible
  requirement, so readonly nodes are legal on pi — and they drop the `tree`
  token, so reviewers fan out in parallel instead of queueing. Name the node's
  answer tool in the list: the allowlist covers extension tools too. Put them
  on a stanza **without `--mode json`** (`pi-review`): a readonly node answers
  on stdout, and `--mode json` fills stdout with pi's event stream, so the
  driver would read `{"type":"agent_settled"}` as the result.
- **Cost.** On a request-metered plan (Copilot and friends) the spend is round
  trips, not tokens. A node that cannot edit cannot spend a round trip trying,
  and a narrow tool list measurably shortens the loop. Pair it with
  `"retry": {"max": 0}` on subscription-backed stanzas — a 429 there usually
  means quota, which does not clear in a minute's backoff (FLOW-AUTHORING has
  the full argument).
- **Blast radius.** Tools the node does not need are damage it cannot do,
  enforced before the model is asked to behave.

Use the narrowest list a node can still do its job with; make every
judgement-producing node (review, triage, estimate, plan) readonly.

## Pi extension hooks (optional, pi executor only)

`contrib/pi-extension/lockstep-guard.ts` is an in-session enforcement layer
for pi: a `tool_call` scope guard that blocks-and-records deterministic
verdicts (`verdicts.jsonl`, read by a shell gate), and a contract-keyed
`submit_result` tool. Governing rule (`docs/spec/ADDENDUM-A-pi-hooks.md`):
extensions may only *enforce*, never *enable* — deleting the extension must
not change what a correct agent can accomplish on any executor.

Attach it **per stanza**, from argv — `--extension contrib/pi-extension/lockstep-guard.ts`
(see the `pi-guarded` stanza in `lockstep.toml.example`). Loading it that way
puts it in the recorded spawn and in the stanza digest, so attaching or
removing the guard re-bills exactly the nodes whose enforcement changed. It
blocks writes outside the node's declared `spec.writes`, resolved against
`LOCKSTEP_REPO_ROOT`; a node that declares no `writes` is not gated.

The scope block is verified against live pi 0.83.0 with a control run.
Re-verify after any pi upgrade or any edit to the extension:
`lockstep run flows/starter/pi-guard-smoke.tg.json --fresh`.

## Exit codes (frozen)

`0` success · `2` gate BLOCK · `3` node failed after retries · `4`
budget/timeout · `5` static verification error · `6` approval rejected · `7`
executor/config error · `8` run-dir lock held.

Note: wall clock may exceed `budget.max_run_minutes` by up to the largest
in-flight `timeout_s` — in-flight nodes finish rather than being killed.

## What resume promises

Cache correctness — a node re-runs when anything it depends on changed and is
skipped when nothing did — and an auditable record. **Not reproducibility**:
harness nodes are nondeterministic; re-running one legitimately yields
different output and correctly invalidates its dependents. Shell nodes always
re-run, deliberately. Map nodes resume per item.

Editing the flow file is a different act: it changes `flow_hash` and starts a
new lineage, so `resume` refuses it and every completed node would re-run.
That refusal is the cache's whole basis, but the bill it implied taught people
not to edit flows — so `run <flow> --seed <old_run_dir>` keeps the refusal and
drops the cost. Every node whose `input_hash` the edit did not move is served
from the old run; the edited node runs, and so does anything whose upstream
produced a *different* result. A re-run ancestor that lands on the same output
does not re-bill its readers: this is content-addressed, not lineage-addressed.
Nothing is trusted but the hash, so shell nodes, map items and failures are
never served, and `status` names every node it inherited and from where.

## Write scope, and what happens when an agent leaves it

Every mutating node declares `spec.writes` — the paths it is allowed to write.
A narrow prompt is advisory text a model can rationalize past under gate
pressure; the scope is the mechanical control, and `verify --lint` warns when a
write-capable node has none. There are three honest declarations: a list of
paths, globs or directory prefixes; `[]`, which means *this node writes
nothing* and is enforced as written; or `["**"]` with a required
`spec.writes_rationale`, for a target genuinely decided at run time. A scope
may interpolate `{args.NAME}` — but never `{steps...}`, which would let a
node's own upstream output decide what it may write.

The driver never sees tool calls, so it **detects** rather than prevents, by
diffing a baseline tree taken before the spawn. Detection runs *during* the
node, inside the same lock that holds its exclusive tokens: outside that lock
the diff measures whatever the next node has already written, and a node that
stayed in scope gets accused of its peer's work. Every write-capable kind takes
the `tree` token for the same reason, shell included. The declared scope also
reaches the spawn as `LOCKSTEP_WRITE_SCOPE`, so an in-harness extension can
prevent what the driver can only detect.

Three further things a declared scope buys. A heal re-run's prompt restates the
target's own scope, because gate findings naming out-of-scope files otherwise
read as authorization to go fix them. A fresh `run` refuses to start when
uncommitted working-tree changes sit inside any declared scope, since an
in-scope write would legally overwrite them (`--allow-dirty-scope` overrides).
And a heal rollback names any path it restored that no target declared — the
signal that an out-of-band edit made mid-run was just undone.

A violation is **quarantined, not abandoned**. The blocked attempt is preserved
as `phases/<node>/out-of-scope-<attempt>.patch` before anything is touched; each
violating path is then restored to its baseline content, or — if the node created
it — moved into `phases/<node>/out-of-scope-<attempt>/`. Rollback still never
deletes: the file is moved, and the failure message names every path and its
outcome. An index entry the node itself staged is reset; one that was already
staged before the node ran is named and left exactly as you left it. On success
the in-scope changed-path list goes to `phases/<node>/touched-<attempt>.txt`.

The run directory is excluded from all of this. Keep `runs/` gitignored — the
engine excludes it from the scope check and from heal rollback, but not from the
lineage fingerprint, so an un-ignored run dir makes every resume warn about
external edits to its own `state.json`.

## Process containment, and what outlives what

A spawn is a process tree, not a process: a harness stanza like `pi.cmd` is an
npm shim, so Windows interposes `cmd.exe`, which runs `node.exe`. Timeouts and
`lockstep cancel` have to reach all of it.

POSIX uses `start_new_session` + `killpg`. Windows uses `CREATE_NEW_PROCESS_GROUP`
plus a **Job Object**, with `taskkill /T /F` still running first and
unconditionally. The job is what makes containment reliable: `taskkill /T` walks
the live parent-pid table, and Windows does not reparent orphans — once a shim
exits, its children point at a dead and eventually recycled pid, so the walk
finds nothing or walks a stranger's tree. Job membership is recorded by the
kernel at assignment and survives the parent's death.

The rule is **nothing outlives the run** — not *nothing outlives its node*:

- A node's clean exit does **not** tear its job down. A process a node
  deliberately backgrounded for later nodes keeps running, exactly as it would
  on POSIX. Flows that share a long-lived resource across nodes — a database
  connection holder is the usual case — depend on this.
- When the driver exits, crashes, or is killed, the last job handle closes and
  the **kernel** reaps whatever is left. Nothing survives into the next run.
  That second half matters most when the survivor holds a lock: an unkillable
  orphan sitting on a database file is the failure this exists to prevent.

One narrow gap, stated rather than papered over: assignment is not atomic with
process creation (~17 µs), and a descendant born inside that window is in no job.
Since its parent dies with the job, the pid walk cannot reach it either.

If a node could not get a job at all — a nested job that forbids it, a policy
denial — `phases/<node>/job-unavailable.txt` records why, because a silent
fallback to the pid walk is otherwise indistinguishable from the guarantee.
Background: `docs/spec/DEVIATIONS.md` (2026-08-10).

## Executors are config, not code

```toml
# lockstep.toml
default = "claude-code"

[executors.claude-code]
argv = ["claude", "-p", "{prompt}", "--output-format", "json"]
json_field = "result"
readonly_argv = ["--disallowed-tools", "Edit,Write"]
```

`readonly_argv` is what lets read-only reviewers fan out in parallel: a
`spec.readonly: true` node drops the `tree` exclusion **and** gets the flags
appended — declared-but-unenforced readonly is a verification error.

**Run `lockstep doctor` after any harness upgrade and on a weekly cadence.** It
is the only check that catches harness flag drift; the offline suite
structurally cannot see it. (Not a pre-commit hook — that spends a model
round-trip per commit.) A clean probe leaves `runs/doctor-record.json`, and
`run` prints one advisory line when the record is missing, stale, or a stanza
changed since — the weekly habit is now a mechanism, not a memory.

## Security posture

argv lists only, never `shell=True`; no network from the driver; no credential
read, stored, or forwarded. `spec.context`/`spec.cwd` must resolve inside the
repo root. Interpolated content is fenced as data — mitigation, not a
guarantee. Running an untrusted taskgraph means running untrusted argv and
feeding untrusted prompts to a harness holding file and shell tools: the same
trust model as `make` or a CI config. Run dirs hold prompts, diffs, and model
output — sensitive, never committed.

## Development

```console
$ .venv\Scripts\python.exe -m pytest      # offline suite; fake executor, no tokens
$ python contrib/replay_suite.py          # zero-token FLOW regression from recorded fixtures
$ python contrib/torture_suite.py         # zero-token ENGINE regression: heal, rollback, quarantine, timeout
$ lockstep run flows/selftest-replay.tg.json    # zero-token doc self-check
$ python contrib/snapshot_bench.py        # what a tree snapshot costs here, and why
$ LOCKSTEP_LIVE=1 pytest tests/live       # live smoke (spends tokens)
```

The three zero-token suites cover different things and none replaces another:
the replay fixtures regress recorded flows, the torture flows regress the
engine paths a recording never takes (a heal cascade, a quarantine, a timeout),
and the self-check flow regresses the documentation this file is part of.

Every git tree operation the engine performs journals its duration as an
advisory `kind: "timing"` line in `events.jsonl` — a snapshot is
`git add -A` into a fresh temp index, so it costs O(tree bytes) on every call
and a run that slows down over its life can be read rather than guessed at.
`contrib/snapshot_bench.py` reproduces the numbers on any repo.

Build order and working agreement: SPEC §14. Deviations: `docs/spec/DEVIATIONS.md`.
License: MIT.
