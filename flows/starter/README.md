# Starter taskgraphs

Portable, generic flows for driving an end-to-end software development life
cycle with lockstep, plus a smoke flow that live-verifies the pi extension on
a new machine. Copy this directory (and `personas/`) into any git repo where
lockstep is installed; everything here uses the config-default executor unless
noted.

All ten flows pass `lockstep verify` and are meant as **templates** — edit
prompts, check commands, and budgets to fit the target repo, then re-verify.
Authoring grammar: `docs/guides/FLOW-AUTHORING.md`. Drive protocol for agents (exit
codes, run-dir diagnosis, approval rules): `docs/guides/DRIVING-LOCKSTEP.md`.

## The flows

| Flow | Spends tokens | What it does |
|---|---|---|
| `pi-guard-smoke.tg.json` | ~2 spawns | Live-verifies the pi scope guard (ADDENDUM-A): env identity reaches the session, the `tool_call` guard blocks a write outside the node's declared `spec.writes` and records a verdict, `LOCKSTEP_CONTRACT` drives structured output. Run it with `--executor-default pi-guarded`. PASS = guard live. BLOCK = extension not loaded or the hook API drifted (lockstep still safe — you only lose the fast-fail layer). |
| `plan-adversarial.tg.json` | up to ~12 | Author writes `PLAN.md` → two adversarial reviewers (feasibility/risk, completeness/scope) → arbiter gate **heals** the plan (≤2 rounds, findings folded into the author's re-prompt) → **human approval**. |
| `implement-heal.tg.json` | up to ~15 | Implementer works the task (follows `PLAN.md` if present) → deterministic **lint+pytest shell gate heals** the implementer (≤2 rounds, failure output in the re-prompt) → a shell probe captures the worktree change → **readonly** adversarial diff review → deterministic gate blocks on blocker/major findings (no auto-heal: a human fixes/decides, then `resume`). |
| `sdlc-e2e.tg.json` | up to ~30 | The full chain: plan → adversarial plan review → healing plan gate → **approval** → implement → healing lint+pytest gate → adversarial diff review → block-on-major gate → closing report. |
| `file-audit.tg.json` | 1/file + arbiter | **Map fan-out**: lists files matching `--arg glob=` as `path\|content-fingerprint` entries (per-item caching invalidates on content change, not just path); one readonly auditor per file (`concurrency: 4`); arbiter gate blocks on upheld blockers. `--arg focus=` steers the lens; `--arg max_files=` caps fan-out (default 40, truncation recorded in the manifest notes). |
| `proposal-gate.tg.json` | up to ~12 | Review gates for a **human-owned** proposal/design doc (`--arg file=`): deterministic required-sections shell gate (`--arg sections=`, default `Goal, Approach, Risks, Test plan`) → completeness + ambiguity/testability reviewers → arbiter Verdict. No heal by design: a block returns findings to the author, who revises and `resume`s. |
| `clarify-gate.tg.json` | up to ~8 | **FRAGMENT** — the clarification-gate pattern: a drafter works from an under-specified brief, and a NON-healing gate (`heal.max_rounds: 0`) reports what only a human can decide as findings with `category: "question"`. Answers arrive by `steer` + `resume`, never by heal text. Copy the gate into your own flow. |
| `evidence-approval.tg.json` | up to ~6 | **FRAGMENT** — the evidence-bearing terminal approval: a shell node renders a mechanical extract to `<run_dir>/approval-evidence.txt`, which `contrib/approve.ps1` prints before the prompt. Shows the segmentation rule (only a trivial shell node after the approval). |
| `retrospect.tg.json` | up to ~8 | Gate-driven improvement: `contrib/retrospect.py` emits the friction report (metadata only), an analyst proposes flow/prompt edits **each citing the number that motivates it**, an arbiter blocks anything the numbers do not support. Adoption is gated — run the report by hand first. |
| `bugfix-heal.tg.json` | up to ~15 | Observe → diagnose → fix → verify: a shell probe RUNS `--arg repro=` and captures the failure, a **readonly** diagnostician pins root cause from it plus `--arg bug=`; fixer implements with the diagnosis in-prompt; deterministic repro gate re-runs the command and **heals** the fixer (≤2 rounds); probe captures the change → **readonly** diff review → block-on-major gate. |

## Running them

```powershell
lockstep verify flows\starter\sdlc-e2e.tg.json          # exit 5 on schema errors
lockstep run    flows\starter\sdlc-e2e.tg.json --dry-run --arg "task=..."   # show waves, spawn nothing

lockstep run flows\starter\pi-guard-smoke.tg.json --executor-default pi-guarded
lockstep run flows\starter\plan-adversarial.tg.json --arg "task=Add CSV export to the report module"
lockstep run flows\starter\implement-heal.tg.json  --arg "task=Add CSV export to the report module"
lockstep run flows\starter\sdlc-e2e.tg.json        --arg "task=Add CSV export to the report module"
lockstep run flows\starter\file-audit.tg.json      --arg "glob=docs/*.md" --arg "focus=stale claims vs the code"
lockstep run flows\starter\proposal-gate.tg.json   --arg "file=proposals/q3-pipeline.md"
lockstep run flows\starter\bugfix-heal.tg.json     --arg "bug=export drops the last row" --arg "repro=python -m pytest tests/test_export.py -q"

lockstep status runs\<run-dir>            # live per-node progress
lockstep steer  runs\<run-dir> impl "prefer the streaming writer"   # consumed at next checkpoint
lockstep resume runs\<run-dir>            # continue after a block / crash / provider limit
```

Typical `implement-heal` outcomes: exit `0` (all gates passed) or exit `2`
(`review-gate` blocked — read the gate's verdict in the run dir, fix or decide,
then `resume`). Exit `6` (approval rejected) can only come from the flows with
an approval node: `plan-adversarial` and `sdlc-e2e`.

## Prerequisites

- **A git repo.** The healing gates use `rollback` (the default), which
  requires a git-managed workspace — `verify` only warns, but `run` refuses
  with exit 7 otherwise.
- **`lockstep.toml`** with a working executor stanza (`lockstep doctor` green).
- **Personas**: `planner`, `implementer`, `reviewer`, `arbiter` from
  `personas/` — copy that directory alongside `flows/`.
- **The deterministic gates are library calls**: `checks` runs
  `python -m lockstep.gates.pytest_verdict` and the review gates run
  `lockstep.gates.block_on_severity` — tested programs shipped in the package
  (see FLOW-AUTHORING's gate-library table), not inline one-liners. A bare
  `python` argv[0] resolves to the interpreter running lockstep
  (DEVIATIONS 2026-08-05), so no venv activation is needed. `ruff` is
  optional (linted only if found; pytest always runs). Swap the gate `cmd`
  for your repo's real checks — it just has to print a `Verdict` JSON object
  and exit 0.

## Portability notes (work-laptop / pi specifics)

- **Every judgement node is `readonly`, so YOUR DEFAULT STANZA MUST DECLARE
  `readonly_argv`.** Reviewers, arbiters, the diagnostician and the closing
  report all run readonly since 2026-08-09; without `readonly_argv` these flows
  fail `verify` with `readonly-unenforced` — free, before anything spawns, but
  it will be the first thing you hit on a fresh machine.

  - claude: `readonly_argv = ["--disallowedTools", "Edit,Write,NotebookEdit,Bash"]`
  - pi: `readonly_argv = ["--tools", "read,submit_result"]`, on a stanza with
    **no `--mode json`** (`pi-review` in `lockstep.toml.example`) — a readonly
    node answers on stdout, and `--mode json` fills stdout with pi's event
    stream.

  **`readonly_argv` must remove the shell too**, not just write/edit: bash is a
  write vector, and `readonly` is exactly what licenses the scheduler to drop
  the `tree` token and run these nodes at the same time. A stanza that keeps
  bash passes `verify` and breaks that guarantee.

  What it buys: `plan-adversarial`'s and `proposal-gate`'s reviewer pairs now
  genuinely fan out (the `exclusive-collision` warning is gone, not suppressed),
  nothing that judges can corrupt what it is judging, and on a request-metered
  plan a node that cannot edit cannot spend a round trip trying to.

- **The probe pattern: shell observes, readonly judges.** A readonly node
  cannot run `git diff` or the repro — so it does not. `implement-heal`,
  `bugfix-heal` and `sdlc-e2e` put
  `python -m lockstep.probes.worktree_diff` in a shell node and hand the
  reviewer the captured change as data; `bugfix-heal` does the same with
  `python -m lockstep.probes.command_output` before the diagnosis. Beyond
  enabling readonly, the observation becomes deterministic, cached, and durable
  in the run dir as evidence. Those three flows raise `max_interp_chars` to
  60000 and tell the consumer to read the spill file if the capture is bigger.
- **`pi-guard-smoke` assumptions**: **run it with the guarded stanza** —
  `--executor-default pi-guarded`. The flow deliberately pins no executor, so
  that it verifies on a machine that has no such stanza; the cost is that a
  plain run probes whatever your default is and reports the guard missing.
  `scope-probe` declares `spec.writes: ["flows/pi-guard-*.tmp"]` and tries to
  write `../pi-guard-escape.tmp` from `cwd: flows`. **The declared scope is
  what makes the probe meaningful** — the guard does not gate a node that
  declares no `writes`, so dropping that field silently turns the whole smoke
  test into a no-op that reports failure. The `guard-gate`
  (`python -m lockstep.gates.pi_guard_smoke`) removes the escape file after
  checking, so a failed smoke never poisons later runs.

  If the extension is absent entirely the write lands, and the **driver's** own
  scope check quarantines it and fails `scope-probe` before the gate runs: the
  message reads `write scope violated`, which is the same diagnosis from the
  engine instead of the gate. **Re-probe with `run --fresh`** after *editing*
  the extension: its path is in argv and so in the stanza digest, but its
  contents are not, so a plain re-run attaches to the old lineage and skips. Session-capture (`--session-dir {phase_dir}`) is a separate,
  stanza-level opt-in — verify fresh-session-per-spawn behavior first
  (ADDENDUM-A preamble note 1); this flow does not test it.
- **Approvals need a TTY.** `approve-plan` auto-rejects (exit 6) on non-TTY
  stdin — run `plan-adversarial` and `sdlc-e2e` interactively (WezTerm is
  fine; it is the parent terminal, not part of the loop), or delete the
  approval node for unattended runs.

## Things to know before the first real run

- **Set budgets and retries before the first run.** Editing a flow file later
  changes `flow_hash` and starts a new lineage — every completed node re-runs
  (and re-bills). Harness nodes here inherit the default
  `retry: {max: 2, backoff_ms: 60000}`, which absorbs provider 429/529s.
- **`budget.max_agent_spawns` counts heal rounds and corrective re-spawns** —
  the numbers above leave headroom; tighten them once you know your repo.
- **Everything is inspectable.** Each node's phase dir under `runs/<run>/`
  holds the rendered prompt, argv, stdout/stderr, result, per-attempt
  rotations, and (on pi with the extension) `verdicts.jsonl`; HEALING gates
  additionally preserve each rolled-back attempt as `attempt-N.patch`
  (terminal blocks leave no patch — nothing was rolled back). `runs/` is
  sensitive — keep it gitignored.
- **Chaining**: `plan-adversarial` then `implement-heal` with the same
  `--arg task=` is the two-stage version of `sdlc-e2e` — useful when you want
  a long gap (or a different day) between plan approval and implementation.
- **`file-audit` and readonly**: unlike the other flows, its map node sets
  `readonly: true` (parallel fan-out is the point). It therefore requires an
  executor stanza with `readonly_argv`; on bare pi, remove `"readonly": true`
  from the `audit` and `arbiter` nodes and accept serialized audits.
- **`bugfix-heal` repro quoting**: the repro command is split with
  `shlex.split` (Windows-aware: backslash paths survive, surrounding quotes
  are stripped) — simple commands (`python -m pytest tests\test_x.py -q`)
  work as-is; avoid shell operators (`&&`, `|`, redirects), which it will not
  interpret. An unrunnable command (typo, missing binary) FAILS the gate as a
  config error instead of burning heal rounds on the fixer; a hang is treated
  as a genuine block after 600 s.
- **`file-audit` scope**: only git-TRACKED files are listed (`git ls-files`) —
  commit or `git add -N` new files first; and `fnmatch` patterns let `*`
  cross `/` (`docs/*.md` also matches `docs/sub/x.md`), unlike shell globs.
  Inserting or deleting a file shifts later items' indices and re-runs them
  (per-item hashes couple to array position) — cost only, results stay
  correct.
- **`checks` gates assume a green baseline**: they run the FULL suite, so
  pre-existing red tests will heal the innocent implementer and then block —
  start from a passing suite, and prefer pinning `cmd` to your venv's
  interpreter/linters (the script already uses its own interpreter for
  pytest, but `ruff` resolves from PATH).
- **Large-interpolation flows on Windows argv stanzas**: `plan-adversarial`,
  `proposal-gate`, `sdlc-e2e`, and especially `file-audit` raise
  `max_interp_chars`, and Windows caps a command line at ~32k chars — with a
  `prompt_via = "argv"` stanza, aggregate findings between ~32k and the cap
  fail to spawn (exit 127). If you hit that, switch the stanza to
  `prompt_via = "stdin"` (config-only) or lower `max_interp_chars` so big
  values spill to files instead.

## The production shapes live next door

Most of the templates this section used to wish for now exist in
`flows/factory/` (see its README): **release-cut** (the release-readiness
checklist, with a version-sync gate and a build/install/import smoke),
**codemod-propose/apply** (the migration sweep, as approved ChangeOrders
behind a staleness gate), **triage-intake**, **research-report**,
**status-digest**, **run-postmortem**, and a generated **harness-bakeoff**.
Still worth building on top of `file-audit`: a scheduled context-freshness
audit over a docs corpus, and a coverage-gap audit feeding `implement-heal`
(`lockstep.gates.coverage_delta` already ships for its gate).
