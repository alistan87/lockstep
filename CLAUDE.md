# lockstep

Harness-agnostic driver for headless coding agents: executes a taskgraph
(`*.tg.json`) DAG whose nodes are prompts to agent harnesses or plain
subprocesses. The driver never calls a model and never holds a credential.

**Orientation:** `docs/guides/THEORY-OF-OPERATIONS.md` explains why the driver behaves
as it does (caching, gates, healing, the spawn contract, resume) — read it
before changing engine behaviour or authoring a flow.

**The spec is authoritative:** `docs/spec/SPEC.md` (revision 3) as amended by
`docs/spec/AMENDMENTS-r4.md`, `docs/spec/AMENDMENTS-r5.md`, and `docs/spec/AMENDMENTS-r6.md`
(all adopted; the LATER
revision wins wherever documents disagree: r6 > r5 > r4 > SPEC).
Implementation-level departures are logged in `docs/spec/DEVIATIONS.md` — check it
before reporting a spec mismatch. Adversarial audit reports live in
`docs/audits/`. `docs/spec/ADDENDUM-A-pi-hooks.md` (informative) governs pi
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
.venv\Scripts\lockstep.exe run <flow> --estimate   # cost floor from prior runs; spends nothing
.venv\Scripts\lockstep.exe run <flow> --replay <run_dir>  # serve recorded results; no spawns, no tokens
.venv\Scripts\lockstep.exe verify-trace <run_dir>  # recompute the journal's hash chain (exit 5 if broken)
.venv\Scripts\lockstep.exe doctor                  # probes harness stanzas; spends small model calls; leaves runs\doctor-record.json
.venv\Scripts\lockstep.exe run flows\audit-spec.tg.json --max-workers 3   # self-audit; spends real tokens
.venv\Scripts\lockstep.exe verify <flow> --lint    # + advisory anti-pattern warnings; exit code unchanged
.venv\Scripts\lockstep.exe explain <run_dir> <node> [--against <run>]  # which hash inputs moved; why a node re-billed
.venv\Scripts\lockstep.exe gc [runs] [--apply]     # estimate-aware retention; dry-run by default
python contrib\replay_suite.py                     # zero-token flow regression over recorded fixtures
python contrib\export_fixture.py <run_dir> <dest>  # scrubbed replayable fixture (review before committing)
pwsh -File contrib\attention.ps1 -RunDir <run>     # toast/webhook on decision-waiting / failed / stopped
```

Deterministic gate bodies live in `src/lockstep/gates/` (invoked as
`python -m lockstep.gates.<name>` from shell gate nodes — tested programs, not
inline one-liners; see FLOW-AUTHORING). Factory flows (release-cut, codemod
propose/apply, triage-intake, research-report, status-digest, run-postmortem)
live in `flows/factory/` with custom contracts in `flows/factory_contracts.py`;
`contrib/bakeoff_gen.py` generates the harness-bakeoff flow from lockstep.toml.

Live smoke (spends tokens): `$env:LOCKSTEP_LIVE="1"; .venv\Scripts\python.exe -m pytest tests\live`

Cockpit tools (all read-only; none spends a token):

```
python contrib\plan_card.py <flow>            # consent card: shape, ceiling, prior runs
python contrib\question_card.py <run_dir>     # clarify findings, verbatim, for ACTIVITY
python contrib\quiescent.py <run_dir>         # exit 0 = only the approval is runnable
pwsh -File contrib\cockpit.ps1 -Role mission -Follow   # the status board
pwsh -File contrib\cockpit.ps1 -Tui                    # one process, keyboard drill-down
python contrib\mission_server.py                       # read-only page, loopback only
pwsh -File contrib\cockpit.ps1 -RunDir <run> -Role why -Node <id>   # why did that step do that
```

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
stated guarantee in the spec. Any deviation goes in `docs/spec/DEVIATIONS.md`
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
a run dir), `/getting-started` (first-run setup on a new machine). Subagents:
`spec-auditor` (read-only spec-vs-code audit), `run-diagnostician` (run-dir
failure triage).

## Driving for a non-programmer (the cockpit)

`contrib/` holds a layer for running lockstep on behalf of a domain expert:
detached runs, clarification gates, evidence-bearing terminal approvals, live
spend, and a friction retro. The view layer is `cockpit.ps1` (shipped default,
zero dependencies) plus `mission_view.py` — pure render functions shared by
`mission_tui.py` (one process, keyboard) and `mission_server.py` (read-only
localhost page). A test pins the two glossaries to each other; keep them in
step. If you are the session driving it, read
`docs/guides/COCKPIT-THEORY-OF-OPERATIONS.md` — it is the operating manual, and
`docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md` is what the human was told, so it binds
what you may say. Three rules that are enforced by code, not discretion:

- **Never answer an approval.** Non-TTY stdin auto-rejects (exit 6) by design;
  the cockpit has no `send-text` path at all. Exit 6 is a handoff signal.
- **Never hand over without `contrib/quiescent.py` exiting 0** — whatever is
  runnable at that moment runs in the human's own terminal.
- **Never narrate in place of evidence at a decision point.** Approvals are
  decided from `<run_dir>/approval-evidence.txt`, rendered by the flow, ending in
  blast radius (`--impact`) and reversibility (`--reversible`). The rule is
  symmetric: after a rejection, quote `<run_dir>/rejection.txt` — the human's
  own words — rather than characterising why they said no.
- **Never quote a cost from memory.** `contrib/plan_card.py` computes it from
  prior runs; that used to be the one number in the protocol with no artifact
  behind it.
