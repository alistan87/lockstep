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
# author: adapt the closest template in flows/starter/ (see docs/FLOW-AUTHORING.md)
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

## Environment facts

- `lockstep doctor` after any harness upgrade and weekly — the only check
  that catches harness flag drift. Probes spend small model calls.
- Executors are stanzas in `lockstep.toml` (see `lockstep.toml.example`);
  authoring guidance: `docs/FLOW-AUTHORING.md`; worked examples + per-flow
  caveats: `flows/starter/README.md`.
- Every spawned node carries `LOCKSTEP_NODE_ID/_ROLE/_WORKSPACE_SCOPE/
  _VERDICT_FILE/_PHASE_DIR/_CONTRACT` in its environment; on pi with the
  project-local extension, out-of-scope writes are blocked in-session and
  recorded to `verdicts.jsonl` (read by verdict-file gates). Extensions only
  enforce — never route control flow through them
  (`docs/ADDENDUM-A-pi-hooks.md`).
