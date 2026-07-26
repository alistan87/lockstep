# lockstep

Harness-agnostic driver for headless coding agents: executes a taskgraph
(`*.tg.json`) DAG whose nodes are prompts to agent harnesses or plain
subprocesses. The driver never calls a model and never holds a credential.

**The spec is authoritative:** `docs/SPEC.md` (revision 3) as amended by
`docs/AMENDMENTS-r4.md`, `-r5.md`, and `-r6.md` (all adopted; the LATER
revision wins wherever documents disagree: r6 > r5 > r4 > SPEC).
Implementation-level departures are logged in `docs/DEVIATIONS.md` — check it
before reporting a spec mismatch. Adversarial audit reports live in
`docs/AUDIT-*.md`. `docs/ADDENDUM-A-pi-hooks.md` (informative) governs pi
extension use: extensions may only ENFORCE, never enable — deleting one must
not change what a correct agent can accomplish on any executor.

## Commands

```
.venv\Scripts\python.exe -m pytest                 # full suite; run after EVERY change
.venv\Scripts\lockstep.exe verify <flow.tg.json>   # static verification (exit 5 on error)
.venv\Scripts\lockstep.exe run <flow> --dry-run    # layered execution plan
.venv\Scripts\lockstep.exe status <run_dir>        # incl. latest per-node progress (r6)
.venv\Scripts\lockstep.exe steer <run_dir> <node> "msg"   # consumed at next checkpoint; folds into hash
.venv\Scripts\lockstep.exe cancel <run_dir> <node>        # kills the node's process tree; no retries
.venv\Scripts\lockstep.exe doctor                  # probes harness stanzas; spends small model calls
.venv\Scripts\lockstep.exe run flows\audit-spec.tg.json --max-workers 3   # self-audit; spends real tokens
```

Live smoke (spends tokens): `$env:LOCKSTEP_LIVE="1"; .venv\Scripts\python.exe -m pytest tests\live`

## Module map (src/lockstep/)

- `taskgraph.py` — format models + the §6 static verifier (named error codes)
- `interpolate.py` — reference forms, data fencing, spill-to-file, `when` eval
- `contracts.py` — built-in output contracts (Verdict, Finding, …) + resolver
- `protocols.py`, `registry.py`, `policy.py` — seams (Executor/Workspace/Store/Policy; AllowAllPolicy is the v1 no-op); kind → executor
- `executors/` — `harness.py` (headless agent subprocess), `shell.py`, `fake.py` (test double), `proc.py` (spawn + kill_tree)
- `workspace.py` — GitWorkspace (temp-index write-tree snapshots; restore never deletes), NullWorkspace
- `state.py`, `store.py` — records, hash composition, events.jsonl, lockfile, run dirs
- `roles.py` — the engine: waves, exclusive tokens, lineage-head resume, gates, heal cascade, map, approvals, budgets
- `cli.py`, `render.py`, `doctor.py` — frozen exit codes, Mermaid, executor probes

## Frozen surfaces — stop and ask before changing

Exit codes (`0/2/3/4/5/6/7/8`, see `__init__.py`), `format_version` 1.x
semantics, the §7 fencing/footer contract, hash composition (M3), and every
stated guarantee in the spec. Any deviation goes in `docs/DEVIATIONS.md`
(what, why, date) — silent drift is what the audit gate exists to catch.

## Working agreement

TDD per SPEC §14. `pydantic` is the ONLY runtime dependency — prefer deleting
a feature over adding a dependency. Full pytest after every change.

## Deliberate non-bugs (do not "fix")

- `interpolate.py`: the FULL pre-spill value is hashed while the prompt gets a
  stub; the stub's run-specific path is excluded from the hash (SPEC §7).
- Shell nodes always re-run (`cacheable=False`) — SPEC §0.1.7.
- A done map node always re-enters `_run_map`; per-ITEM hashes do the caching.
- `NullWorkspace` disables external-edit detection (AMENDMENTS M6).
- Readonly harness nodes get `FOOTER_READONLY` (stdout result channel) — the
  standard footer would order them to write files their `readonly_argv` forbids.
- Corrective re-spawns embed the original prompt + fenced invalid output —
  headless spawns are stateless; "output-only" constrains side effects, not context.

## Ops notes

- Run `lockstep doctor` after any harness upgrade and weekly — the only check
  that catches harness flag drift. Not a pre-commit hook (AMENDMENTS A1).
- This machine's AV causes transient `PermissionError` on file replaces and
  git object writes — retry once before investigating.
- `runs/` holds prompts, diffs, and model output: sensitive, gitignored,
  never committed. `lockstep.toml` is local (gitignored); the committed
  template is `lockstep.toml.example`.

Project skills: `/flow-authoring` (write a taskgraph), `/debug-run` (diagnose
a run dir). Subagents: `spec-auditor` (read-only spec-vs-code audit),
`run-diagnostician` (run-dir failure triage).
