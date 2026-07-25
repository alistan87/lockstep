---
name: flow-authoring
description: Author or modify a lockstep taskgraph (*.tg.json) — node model, roles×kinds, contracts, heal rules, interpolation forms, and the verification rules that will reject a malformed flow. Use when writing a new flow, adding nodes to an existing one, or fixing `lockstep verify` errors.
---

# Authoring a taskgraph

Full grammar: `docs/SPEC.md` §4–§7 as amended by `docs/AMENDMENTS-r4.md`.
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
  `CheckResult`, `StepResult`, `Finding`, `Verdict`, `PathManifest`, plus
  `ProgressEvent`/`SteerMessage` — reserved for v2 but resolvable; `"X[]"` =
  array of; `module:Name`; or a bare name resolved via the flow-level
  `contracts_module` field).
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

## Retry — set it BEFORE the first run

Give harness nodes `"retry": { "max": 2, "backoff_ms": 60000 }`: transient API
errors (529 Overloaded, 5xx) surface as nonzero exits, and the built-in
automatic retry covers only timeouts and empty results. Bake this in up
front — editing the flow later changes `flow_hash` and starts a new lineage,
re-running (and re-billing) every completed node.

## Budget & executors

`budget.max_agent_spawns` counts every token-costing spawn INCLUDING corrective
re-spawns and heal rounds — interrupted lineages keep their counter, so leave
headroom. Executors are stanzas in `lockstep.toml` (see
`lockstep.toml.example`); pick per node via `spec.executor`, per flow via
`executor_default`, else the config `default`. Multi-model review = one stanza
per model (see `flows/audit-spec.tg.json`).
