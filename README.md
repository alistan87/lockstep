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

Spec: `docs/SPEC.md` (revision 3) + `docs/AMENDMENTS-r4.md` (adopted delta).

## Quickstart

```console
$ pip install -e .            # Python >= 3.11; pydantic is the only dependency
$ lockstep init               # writes ./lockstep.toml — edit the argv templates
$ lockstep doctor             # probe each configured harness actually runs
$ lockstep verify flows/gated-build.tg.json
$ lockstep run flows/gated-build.tg.json --arg task="add a --version flag"
$ lockstep status runs/gated-build-<stamp>/
$ lockstep resume runs/gated-build-<stamp>/     # after a crash or budget trip
```

Observation is deliberately plain: `tail -f` any `runs/**/phases/<node>/stdout.log`,
`watch -n2 lockstep status <run_dir>`, or `src/lockstep/watch/wezterm-watch.sh <run_dir>`.

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
round-trip per commit.)

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

Build order and working agreement: SPEC §14. Deviations: `docs/DEVIATIONS.md`.
License: MIT.
