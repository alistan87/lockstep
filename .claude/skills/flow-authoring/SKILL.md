---
name: flow-authoring
description: Author or modify a lockstep taskgraph (*.tg.json) — node model, roles×kinds, contracts, heal rules, interpolation forms, and the verification rules that will reject a malformed flow. Use when writing a new flow, adding nodes to an existing one, or fixing `lockstep verify` errors.
---

# Authoring a taskgraph

Full grammar: `docs/spec/SPEC.md` §4–§7 as amended by `docs/spec/AMENDMENTS-r4.md`,
`-r5.md`, and `-r6.md` (the latest adopted revision wins).
Always finish with `.venv\Scripts\lockstep.exe verify <flow>` (exit 5 = errors,
all reported at once with named codes) and `run <flow> --dry-run` to see waves.

## Minimal node

```jsonc
{ "id": "impl",                  // ^[a-z0-9][a-z0-9-]*$
  "role": "work",                // work | gate | approval | map
  "kind": "harness",             // harness | shell (fake = test double)
  "spec": { "task": "…prompt…", "persona": "implementer", "readonly": false },
  "depends_on": [], "output": "text", "timeout_s": 900 }
```

- `kind: "shell"` spec is `{ "cmd": ["pytest", "-q"], "cwd": "." }` — argv list,
  never a shell string. Shell nodes ALWAYS re-run on resume (by design).
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
  resolved via the flow-level `contracts_module` field).
- **Gates**: `role: "gate"` requires `output: "json"` + a contract resolving to
  `Verdict`. Prefer `kind: "shell"` gates (deterministic) whenever the check is
  machine-decidable — see `flows/gated-build.tg.json`.
- **Heal**: only on gates; `max_rounds > 0` requires explicit `targets` that
  are harness-kind ANCESTORS of the gate; a node may not be a target of two
  gates; a healing gate (`max_rounds > 0`) with `rollback: true` — the
  default — requires a git-managed workspace: `verify` only WARNS
  (`heal-rollback-nongit`), but the run refuses with exit 7.
- **Map**: `role: "map"` requires `over` shaped `{steps.X.json...}` resolving
  to a JSON array; `concurrency: 1` guarantees array-order sequential runs;
  items resume per-item.
- **Approval**: no `kind`, no `spec` — core-handled TTY prompt; auto-rejects
  (exit 6) on non-TTY stdin; never resume-skipped.
- **Readonly**: `spec.readonly: true` lets harness nodes fan out in parallel
  (drops the `tree` exclusion) but the executor stanza MUST declare
  `readonly_argv` or verification fails (`readonly-unenforced`). Readonly
  nodes answer on stdout — they cannot write `result.json`.

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

## Retry

Harness nodes DEFAULT to `retry: { max: 2, backoff_ms: 60000 }` (AMENDMENTS-r5
B2) — transient provider errors (429/529) surface as nonzero exits and the
minute-scale backoff absorbs them. Setting `retry` explicitly in the flow file
(even `{"max": 0}`) overrides the default entirely; shell nodes stay at
`max: 0`. If you do need a custom retry, bake it in BEFORE the first run —
editing the flow later changes `flow_hash` and starts a new lineage,
re-running (and re-billing) every completed node.

## Write scope (`spec.writes`)

`"writes": ["CHANGELOG.draft.md"]` declares the repo-root-relative paths a node
may write. It reaches the spawn as `LOCKSTEP_WRITE_SCOPE` so an in-harness
extension can prevent a stray write; the driver detects one by diffing a
baseline tree, inside the node's own lock.

A violation is **quarantined**, so a scope is a decision about what may be
reverted rather than only flagged: the blocked attempt is kept as
`phases/<node>/out-of-scope-<attempt>.patch`, each violating path is restored to
its baseline or moved into `out-of-scope-<attempt>/` (never deleted), and the
node fails naming every path and its outcome. On success the in-scope changed
paths land in `touched-<attempt>.txt`.

Verification: absolute or escaping entries are `bad-write-scope`; a map node
declaring one is the hard error `write-scope-on-map` (the items share one tree
and one diff); a `readonly` node gets the advisory `write-scope-unenforced`,
because it holds no `tree` token and the diff would be unsound. Every other
write-capable kind, shell included, takes the token. The matcher is `fnmatch`,
so `*` crosses `/`.

## Budget & executors

`budget.max_agent_spawns` counts every token-costing spawn INCLUDING corrective
re-spawns and heal rounds — interrupted lineages keep their counter, so leave
headroom. Executors are stanzas in `lockstep.toml` (see
`lockstep.toml.example`); pick per node via `spec.executor`, per flow via
`executor_default`, else the config `default`. Multi-model review = one stanza
per model (see `flows/audit-spec.tg.json`).
