---
type: guide
title: "Fleet operations: many concurrent lockstep runs that don't step on each other"
description: The operating model for running multiple lockstep drivers at once — worktree-per-run, the lane record, the decision protocol, resource lanes, and recovery. Built by the concurrent-orchestration work order; the engine half is THEORY-OF-OPERATIONS §11.
resource: docs/guides/FLEET-OPERATIONS.md
---

# Fleet operations

One lockstep run is a driver, a run dir, and a working tree. A **fleet** is
several at once, and the engine deliberately arbitrates nothing between them
(THEORY-OF-OPERATIONS §11, "Multiple drivers"). This guide is the layer that
does: what isolates the trees, what serializes shared resources, how
decisions reach the human, and how work comes back. The approval rules of
`COCKPIT-THEORY-OF-OPERATIONS.md` bind every lane exactly as they bind a
single run.

## The shape

- **One worktree per writing run.** Fresh, on its own branch, removed after
  harvest. Readonly flows (reviews, audits, digests) may share the main
  checkout — only mutation needs isolation.
- **One central runs dir** — the main repo's `runs/`. The cockpit rail,
  `session_spend`, `plan_card`, seeds and `gc` keep seeing everything. Fleet
  launches always pass `--fresh` (lane.py does).
- **Detached drivers, disposable watchers.** The run dir is the durable
  state. A lane-runner agent (or a human with `wait`) watches; if the
  watcher dies, nothing is lost.
- **The lane record is the identity**: `<worktree>/.lockstep-lane.json`,
  written by `lane.py start` — `{worktree, branch, run_dir, driver_pid,
  flow, args, started}`. Harvest, recovery, and every agent action key on
  it. A worktree without one is not a lane.

## Launch: contrib/lane.py, nothing else

```
python contrib\lane.py start <flow.tg.json> [--arg k=v ...] [--branch NAME]
python contrib\lane.py harvest <worktree>          # after the run settles
python contrib\lane.py abandon <worktree> [--force]  # the discard path
```

`start` owns the recipe so nobody improvises it: fresh worktree + branch
(default `lane/<flow>-<stamp>`), `verify` against the worktree with the MAIN
repo's config, then `run --repo-root <wt> --config <main>\lockstep.toml
--runs-dir <main>\runs --fresh --detach` — absolute paths throughout,
because gitignored files (`.venv`, `lockstep.toml`, `runs/`) do not exist in
a fresh worktree and the detached driver's cwd is wherever lane.py ran.

It also owns the one subtle failure: `--detach` locates its run by newest
(flow_hash, args) match, so two simultaneous same-flow launches could each
print the other's run dir. `start` serializes launches with a start-lock
under the runs dir, diffs the runs-dir listing before/after, and
cross-checks the run's lock-holder pid against the printed driver pid — on
any mismatch it aborts loudly, kills the driver, and removes the worktree
rather than filing a record that points at someone else's run.

## The engine guardrails under this (Batch 1)

- Every run records the resolved root it was created against; `status` and
  `active` print it.
- `resume` against a different tree: **refusal, exit 7**, both paths named.
  Resume from the lane's worktree, always.
- `run` (no `--fresh`) whose newest lineage was created in a vanished
  worktree: **falls through to a new lineage** with a printed note — plain
  `lockstep run` from the main checkout keeps working after every fleet.
- Attach under a live lock: exit 8, nothing written (pinned by test).

## Ceilings and resource lanes

- **Fleet-wide harness-spawn ceiling: 8** (owner decision, work order §6.1).
  Budget it across lanes via `--max-workers`; lower per-lane as lanes grow.
  pi's ~90s round trips make over-commitment expensive quickly.
- **A shared mutable resource gets ONE writer lane.** DuckDB databases,
  ports, external services: one lane may write; everyone else reads
  immutable/versioned views (the MIMIR side of this is the work-repo runbook
  `copilot-work-order-mimir-db-concurrency.md`: outboxes, versioned
  build-and-swap, the librarian). Exclusive tokens are per-driver and cannot
  serialize across runs — **assignment is the mechanism**. The fast named
  failure when assignment was wrong is `lockstep.gates.lock_held` at the
  writing flow's head; the diagnostic is `contrib\who_holds.py` over the
  `<file>.holder.json` convention.
- `git config gc.auto 0` in the main repo (owner decision §6.3, applied
  2026-08-16; per-clone, so set it once per machine): recorded snapshot
  trees are unreferenced loose objects, and an auto-gc mid-fleet can prune
  what `node_diff` and replay later need. `lane.py start` warns when unset.

## Decisions: exit 6 is the signal

A detached driver that reaches an approval auto-rejects (non-TTY stdin) and
exits 6 — by design, that IS the notification. The protocol:

1. The lane-runner returns a **decision packet**: run dir + parked node,
   `approval-evidence.txt` verbatim through its impact/reversible lines,
   `question_card.py` output verbatim, `quiescent.py` exit 0 confirmed,
   token spawns. (Exit 6 with `rejection.txt` present is the OTHER case: the
   human already said no — quote their words, stop the lane.)
2. The orchestrator relays it — evidence verbatim, never narration — and the
   human answers in their own terminal: `lockstep resume <run_dir>` **from
   the lane's worktree**.
3. `attention.ps1` runs per lane from launch, so the human hears about
   decisions even if every agent is wedged.
4. Money is never quoted from memory or from `status` (which counts token
   spawns): `plan_card.py` / `cost_report.py` artifacts only.

## Harvest: park, then walk it through (owner decision §6.2)

Lane branches never merge automatically. When a lane is done, the
orchestrator walks the domain expert through what was delivered — plain
terms, grounded in the lane's evidence (`git -C <wt> diff main`, the run
record, `node_diff` for scoped nodes), never a summary standing in for it —
and merges only on the expert's approval. Then `lane.py harvest <worktree>`:
refuses under a live driver, commits the branch (lane record excluded),
removes the worktree only when clean. The run dir stays; `gc` owns run
retention.

## Recovery

| what died | recovery |
|---|---|
| watcher agent | new agent, same lane record |
| driver | `active` shows STALE → resume from the lane's worktree |
| launch | lane.py already killed the driver and removed the worktree; re-run `start` |
| provider limit | wait it out (envelope evidence), resume from the worktree |
| worktree gone, lineage wanted | `lockstep run <flow>` from the main checkout — the root mismatch falls through to a new lineage; `--seed <old_run>` warm-starts it |

## What the fleet layer refuses to build

Cross-run exclusive tokens in the engine (r7-shaped spec surface — recorded
as a seam in ROADMAP-NOTES), the protected-path floor (rejected,
PROPOSAL-sssf-adoptions §5), and any DuckDB awareness in `src/lockstep/`
(pydantic stays the only dependency; MIMIR specifics live in the work repo).
