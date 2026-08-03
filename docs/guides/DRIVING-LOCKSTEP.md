---
type: guide
title: Driving lockstep (orchestrator protocol)
resource: docs/guides/DRIVING-LOCKSTEP.md
---
# Driving lockstep (orchestrator protocol)

Paste this section into your repo's agent instructions (`AGENTS.md`,
`CLAUDE.md`, or equivalent), or reference it from there. It defines how an
agent with shell access drives lockstep: authoring flows, running them,
reading run dirs, and knowing when to hand control back to a human.

## Principle

Plans are data, not prose. When work needs fan-out, gates, retries, or an
audit trail, express it as a taskgraph and let lockstep own the control flow —
do NOT hand-roll loops of single agent calls in the shell around what a flow
expresses with caching, healing, and budgets built in.

## The drive loop

```
# author: adapt the closest template in flows/starter/ (see docs/guides/FLOW-AUTHORING.md)
lockstep verify flows/x.tg.json            # loop until "ok"; exit 5 lists ALL named errors
lockstep run flows/x.tg.json --dry-run --arg k=v    # inspect waves; costs nothing
lockstep run flows/x.tg.json --arg k=v     # only with budget.max_agent_spawns set
lockstep status runs/<run-dir>             # progress incl. latest per-node checkpoint
lockstep steer runs/<run-dir> <node> "…"   # mid-flight correction, consumed at next checkpoint
lockstep resume runs/<run-dir>             # continue after exit 2/3/4/8 once addressed
lockstep run flows/x.tg.json --fresh …     # new lineage; re-runs (and re-bills) everything
```

## Branch on exit codes, not log text (frozen)

| Exit | Meaning | Orchestrator action |
|---|---|---|
| 0 | success | read the final node's result; done |
| 2 | gate BLOCK | read the gate's verdict + findings in the run dir; fix or decide; `resume` |
| 3 | node failed after retries | diagnose the node's phase dir (below); fix; `resume` |
| 4 | budget/timeout tripped | decide whether to raise budget (edits flow ⇒ new lineage!) or `resume` within it |
| 5 | verification error | fix the flow per the named codes; re-verify |
| 6 | approval rejected | HAND TO HUMAN (see below) |
| 7 | executor/config error | run `lockstep doctor`; check `lockstep.toml`; not a flow bug |
| 8 | run-dir lock held | another process owns the run; do not force-unlock without diagnosing |

## Hard rules

1. **Approvals are not yours.** Under your shell, stdin is non-TTY, so any
   `approval` node auto-rejects (exit 6). Flows you run autonomously must not
   contain approval nodes; a flow WITH one is your signal to stop and tell
   the human to run it themselves in a terminal. Never restructure a flow to
   remove an approval you were not asked to remove.
2. **Never edit a flow that has a live lineage.** Edits change `flow_hash`:
   every completed node re-runs and re-bills. Use `steer` for mid-flight
   corrections; batch flow edits for the next fresh run.
3. **Always set `budget.max_agent_spawns`** in flows you author (heal rounds
   and corrective re-spawns count against it). Spawned nodes bill the same
   provider quota you run on.
4. **`runs/` is sensitive** (prompts, diffs, model output). Never commit it,
   never paste its contents into anything that leaves the machine.
5. **`--fresh` is a spend decision**, not a debugging reflex. Prefer `resume`;
   go fresh only when inputs changed in ways the hash cannot see (e.g. the
   pi extension was installed/fixed — see `flows/starter/pi-guard-smoke`).

## Diagnosing a run dir (distilled)

Everything lives under `runs/<run>/`:

- `state.json` — per-node status, attempts, verdicts, heal rounds;
  `events.jsonl` — the append-only audit trail. `lockstep status` summarizes.
- `phases/<node>/` — per node: `prompt.txt` (exact rendered prompt),
  `argv.json`, `stdout.log`/`stderr.log`, `result.json|txt` (validated
  result), `verdicts.jsonl` (in-session guard blocks, pi only). Prior
  attempts are rotated alongside as `*-attemptN.*` — compare attempts to see
  what a retry or corrective re-spawn changed.
- Map items: `phases/<node>/items/<i>/` (same layout per item).
- Healing gates: `attempt-<round>.patch` preserves each rolled-back attempt.
- Decode order for a failed harness node: `state.json` error → node's
  `stderr.log` (provider limits are named) → `result.*` vs contract →
  `prompt.txt` (did interpolation render what you expected?).

Provider limit named in the error ⇒ wait, then `resume` — do not go fresh.

## Driving for a non-programmer (the cockpit)

When the person you are talking to is a domain expert rather than an engineer,
four rules change. All four exist because everything the human's terminal runs,
the human owns — and they cannot read a stack trace.

**1. Never block on `lockstep run`.** Run detached, with non-TTY stdin. A run
hosted in a bare pty passes `isatty()`, sits silently at the approval prompt
forever instead of auto-rejecting, and dies with its pane. Detached, you keep
conversing and poll `status` / `events.jsonl` between turns. A mute orchestrator
forfeits narration, question relay, and STOP.

**2. Exit 6 is a handoff, not a failure.** A detached run reaching an approval
auto-rejects and exits 6. Narrate it as "ready for your decision."

**3. Check quiescence before handing over — with the tool, never by eye.**

```powershell
python contrib\quiescent.py <run_dir>     # 0 = only the approval is runnable
```

Exit 1 lists what would otherwise run inside the human's terminal. The fix is
always the same: `resume` **detached** first, let the engine burn the queue down
to the approval's auto-reject again, then re-check. **Any steer after the last
detached resume means not quiescent until a detached resume has consumed it.**
Do not re-derive this predicate by reading `state.json` yourself.

A trivial `shell` node downstream of the approval is fine — that is the
sanctioned shape (`approve` → copy the deliverable out). A harness node there is
not, and `quiescent.py` will say so: split the flow into two segments.

**4. Spawn the approval pane; never type Enter.**

```powershell
pwsh -File contrib\cockpit.ps1 -RunDir <run_dir> -Approve
```

This checks quiescence, then spawns a pane that **runs** `contrib\approve.ps1` —
evidence first, prompt second. Nothing is typed into the pane at all, which is
what makes "no automation answers an approval" true by construction: there is no
code path that types anywhere. The human reads the pane and types `a` or `r`.

**Why not pre-type the command?** That was the original design, and it failed on
the first real machine. A pane spawned as `pwsh` did not stay a shell — a
PowerShell profile auto-started an interactive agent in the project directory —
so the "command" was typed into a **chat composer**. Nothing caught it, because
the verification step only confirmed the pane id existed. Had the human pressed
Enter, they would have sent a shell command to a language model instead of
approving. Hence three rules now:

- every cockpit pane runs with **`-NoProfile`**, because a cockpit pane is
  infrastructure, not the operator's interactive shell;
- the pane's program **writes a handshake** naming its marker and its
  `WEZTERM_PANE`, and the cockpit requires both to match the pane it just
  created — a pane title cannot carry this, since a title follows the foreground
  process and becomes `python.exe` the moment `lockstep resume` starts;
- verification failure **kills the pane and aborts** to narration rather than
  leaving a decision surface nobody can vouch for.

Never `wezterm cli set-tab-title` on a pane in a shared tab: a tab is shared by
every pane in it, so that renames the human's own tab.

**Clarification questions** come from a gate with `heal.max_rounds: 0` whose
findings use `category: "question"` (`flows/starter/clarify-gate.tg.json`). On
block, read `phases/<gate>/result.json`, relay each question in plain language
**with the finding quoted verbatim**, echo-confirm the answer before sending it,
then steer and resume:

```powershell
lockstep steer <run_dir> <target-node> "<the answer>"
lockstep steer <run_dir> <the-gate>    "<the answer>"   # so it stops asking
lockstep resume <run_dir>                               # detached
```

Steer the **target**, not just the gate — and the gate too, or it re-asks a
question already answered and blocks forever. Answers are effectively permanent:
the mailbox renders in full into every later prompt and folds into the hash, so
a correction is appended beside the original and true retraction means `--fresh`
(which re-bills the lineage). Verify the steer text actually landed in
`phases/<target>/prompt.txt` before telling anyone their answer was applied.

**Recovery is a double-click**: `contrib\start-cockpit.cmd` scans for unfinished
runs and applies the mechanical rule — lock pid dead ⇒ plain `resume` is safe;
lock pid alive ⇒ the run outlived you, reattach and do **not** unlock.

**STOP is a reserved word.** If the human says it: `lockstep cancel` the running
nodes, do not resume, and report what was spent.

## Environment facts

- `lockstep doctor` after any harness upgrade and weekly — the only check
  that catches harness flag drift. Probes spend small model calls.
- Executors are stanzas in `lockstep.toml` (see `lockstep.toml.example`);
  authoring guidance: `docs/guides/FLOW-AUTHORING.md`; worked examples + per-flow
  caveats: `flows/starter/README.md`.
- Every spawned node carries `LOCKSTEP_NODE_ID/_ROLE/_WORKSPACE_SCOPE/
  _WRITE_SCOPE/_VERDICT_FILE/_PHASE_DIR/_CONTRACT` in its environment
  (`_WRITE_SCOPE` is the node's declared `spec.writes` as a JSON array, empty
  when it declares none); on pi with the
  project-local extension, out-of-scope writes are blocked in-session and
  recorded to `verdicts.jsonl` (read by verdict-file gates). Extensions only
  enforce — never route control flow through them
  (`docs/spec/ADDENDUM-A-pi-hooks.md`).
