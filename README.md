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
$ lockstep status runs/gated-build-<stamp>/     # incl. latest per-node progress
$ lockstep steer runs/<run>/ implement "prefer the streaming writer"   # next checkpoint
$ lockstep cancel runs/<run>/ implement         # kills the node's process tree
$ lockstep resume runs/gated-build-<stamp>/     # after a crash or budget trip
$ lockstep verify flows/x.tg.json --lint        # + advisory anti-pattern warnings
$ lockstep explain runs/<run>/ implement        # which hash inputs moved; why it re-billed
$ lockstep run flows/x.tg.json --replay runs/<run>/   # recorded results; no spawns, no tokens
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
prompts, and re-runs. Rounds exhausted ⇒ exit 2.

## Starter flows

`flows/starter/` ships ten portable, adversarially-reviewed templates — see
its README for the full table and per-flow caveats:

- **SDLC**: `plan-adversarial` (author → two attacking reviewers → healing
  arbiter gate → human approval), `implement-heal` (implementer → deterministic
  lint+pytest gate that HEALS on failure → adversarial diff review →
  block-on-major gate), `bugfix-heal` (diagnose → fix → healing repro gate →
  review), and `sdlc-e2e` (the whole chain).
- **Audit**: `file-audit` (map fan-out, one readonly auditor per file with
  content-fingerprint caching), `proposal-gate` (completeness gates for a
  human-owned doc — deterministic section check, then reviewers; no heal).
- **Cockpit fragments**: `clarify-gate` (a non-healing gate that asks a human
  what only they can settle), `evidence-approval` (the evidence-bearing terminal
  approval, with its labels sidecar).
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
$ python contrib\mission_server.py                  # or the trace page on loopback, GET only
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

### The trace page

`contrib/mission_server.py` is one page with four levels of disclosure, aimed at
being a surface a domain expert opens *by choice*:

| level | what it shows | entry |
|---|---|---|
| **board** | headline, stat row, the collapsed step list, the spend meter, both cost blocks, ACTIVITY, and the evidence or the question card, verbatim, when one waits | on load |
| **timeline** | every step on a shared time axis, **in place of** the step list, with a table twin beside it | "show every step" |
| **step** | a drawer per step: names, sizes, attempts, cost — never stdout bodies | click a row |
| **raw** | node id, hash parts, what moved, the chain head — each with a one-line gloss, pinned by test against the domain-expert guide | "show the raw record" |

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

## Pi extension hooks (optional, pi executor only)

`contrib/pi-extension/lockstep-guard.ts` is an in-session enforcement layer
for pi: a `tool_call` scope guard that blocks-and-records deterministic
verdicts (`verdicts.jsonl`, read by a shell gate), and a contract-keyed
`submit_result` tool. Governing rule (`docs/spec/ADDENDUM-A-pi-hooks.md`):
extensions may only *enforce*, never *enable* — deleting the extension must
not change what a correct agent can accomplish on any executor. Install
project-locally (`.pi/extensions/`), then live-verify with
`lockstep run flows/starter/pi-guard-smoke.tg.json` (re-verify with `--fresh`
after any change; it is UNTESTED against live pi until you do).

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

## Write scope, and what happens when an agent leaves it

A node may declare `spec.writes` — the paths it is allowed to write. The driver
never sees tool calls, so it **detects** rather than prevents, by diffing a
baseline tree taken before the spawn. Detection runs *during* the node, inside
the same lock that holds its exclusive tokens: outside that lock the diff
measures whatever the next node has already written, and a node that stayed in
scope gets accused of its peer's work. Every write-capable kind takes the `tree`
token for the same reason, shell included.

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
$ LOCKSTEP_LIVE=1 pytest tests/live       # live smoke (spends tokens)
```

Build order and working agreement: SPEC §14. Deviations: `docs/spec/DEVIATIONS.md`.
License: MIT.
