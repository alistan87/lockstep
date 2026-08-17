---
name: fleet-ops
description: Orchestrate multiple concurrent lockstep runs (a fleet) — one worktree per writing run via contrib/lane.py, one lane-runner agent per lane, cross-run resources serialized by assignment, decisions relayed as verbatim evidence. Use when launching or supervising more than one run at a time, or when a lane parks on a decision, dies, or finishes ready to harvest.
---

# Running a fleet

The engine tolerates many drivers (per-run-dir locks); everything else is
YOUR job: tree isolation, resource lanes, the spawn ceiling, and the
decision protocol. The model is `docs/guides/FLEET-OPERATIONS.md`; the
approval rules are `docs/guides/COCKPIT-THEORY-OF-OPERATIONS.md` and bind a
fleet exactly as they bind one run. This skill is the operating order.

## Launch

- **A run that writes gets a worktree; a readonly flow shares the main
  checkout.** Never two writing runs on one tree.
- Launch ONLY via `python contrib\lane.py start <flow> [--arg k=v ...]` —
  it owns the recipe (fresh worktree + branch, verify, main repo's
  config/runs-dir/binary, `--fresh --detach`, run-dir confirmation under a
  start-lock) so you never improvise it. Its last stdout line is the lane
  record (also persisted at `<worktree>/.lockstep-lane.json`). File it: the
  record is the lane's identity, and every later action keys on it.
- One **lane-runner** agent per lane record, launched with the record as its
  input. One lane = one worktree = one run dir = one agent. No sharing.
- Before the first launch of a fleet: `lockstep doctor` ONCE (never
  per-lane — it spends model calls and writes a shared record), and start
  `pwsh -File contrib\attention.ps1 -RunDir <run>` per lane (lane.py prints
  the command) so the human hears about decisions even if the agent layer
  is wedged.

## Ceilings and lanes

- **Fleet-wide spawn ceiling: 8 concurrent harness spawns** (owner decision,
  work order §6.1) — the sum over every lane of that run's `--max-workers`-
  bounded harness concurrency stays ≤ 8. Lower per-lane `--max-workers` as
  lane count grows.
- **Shared mutable resources are lanes, not locks.** A DB, port, or external
  service has ONE writer lane; everything else reads versioned/immutable
  views. Lockstep's exclusive tokens are per-driver and cannot serialize
  across runs — assignment is the mechanism, `lockstep.gates.lock_held` at a
  writing flow's head is the fast named failure when assignment was wrong,
  and `contrib\who_holds.py` is the diagnostic.

## Decisions (exit 6)

The lane-runner returns a decision packet; your job is RELAY, not judgment:

- Present `approval-evidence.txt` and any `question_card.py` output
  **verbatim** — never narration in place of evidence, never a paraphrase of
  a rejection (`rejection.txt` is quoted, in the human's own words).
- Hand over only with `contrib\quiescent.py` at exit 0 (the packet states
  it; verify if stale). A **not-ready report** (`reason: blockers`) is yours
  to clear, not the lane-runner's: resume detached from the main repo —
  `.venv\Scripts\lockstep.exe resume <run_dir> --repo-root <worktree>
  --config lockstep.toml --detach` — then re-message the lane-runner to
  re-check.
- The human answers through the COCKPIT APPROVAL PANE
  (`cockpit.ps1 -RunDir <run> -Approve` → `approve.ps1`): evidence first,
  a/r prompt, and on `r` it writes `rejection.txt` — the pane reads the
  run's recorded root from state.json and passes `--repo-root` plus the
  main repo's `--config` itself. You never answer, and there is no channel
  by which you could. Manual fallback (a human working without the
  cockpit): the exact command is
  `<main>\.venv\Scripts\lockstep.exe resume <run_dir> --repo-root
  <worktree> --config <main>\lockstep.toml` — quote it in the relay; a bare
  `lockstep resume` in the worktree cannot work (no `.venv`, no
  `lockstep.toml` there), and a rejection this way leaves NO
  `rejection.txt` (the lane-runner falls back to the recorded error).
- After the human's answer settles, re-message the SAME lane-runner to
  continue `wait` — its context holds the lane.

## Recovery

| what died          | what to do                                                       |
|--------------------|------------------------------------------------------------------|
| lane-runner agent  | new agent on the SAME lane record; nothing was lost              |
| driver             | `lockstep status`/`active` says STALE → resume with the lane's root: `.venv\Scripts\lockstep.exe resume <run_dir> --repo-root <worktree> --config lockstep.toml` (wrong root = exit 7 refusal, by design) |
| launch (lane.py aborted) | verify/launch failures remove the worktree (no driver existed); an unconfirmed launch KEEPS worktree and driver and says so — check `lockstep active` and the detached log, then hand-write the lane record or `abandon` |
| provider limit     | wait it out, then resume as above (quote the envelope evidence)  |

## Harvest (owner decision §6.2: always park, then walk it through)

Order matters — **the branch has no commit until harvest creates it** (the
run's work sits uncommitted in the worktree), so merging before harvesting
merges nothing:

1. Lane reports done → run the **harvest walkthrough** with the domain
   expert: what was delivered, in plain terms, grounded in the lane's
   evidence — `git -C <worktree> status --porcelain` and
   `git -C <worktree> diff HEAD` (the uncommitted delivery), the run
   record, `node_diff` for a scoped node — never in your summary of it.
2. Expert approves → `python contrib\lane.py harvest <worktree>` (refuses
   under a live driver, COMMITS the branch with the lane record excluded,
   removes the worktree). The branch now carries the delivery, parked.
3. Merge the parked branch — the expert's call, made in step 2's
   discussion. Review form: `git diff main...<branch>` (three-dot: against
   the fork point, so other lanes' already-merged work does not appear as
   spurious reversals).

`lane.py abandon` is the discard path and says what it deletes.

## Never

- Never exceed the ceiling to "just finish something".
- Never harvest, merge, or abandon on your own judgment — parking is the
  default and the merge is the expert's.
- Never let a missing lane record pass silently: a worktree without
  `.lockstep-lane.json` is not a lane; find out what it is before touching it.
