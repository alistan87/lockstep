---
type: plan
title: "Work order: concurrent lockstep — guardrails, tooling, and the lane contract"
description: Build-ready plan for running multiple lockstep drivers at once (one git worktree per writing run, delegated to subagents) without interference. Six batches — pinning what already holds (0), resume-root and attach guardrails (1), the lane launcher and lock hygiene in contrib (2), a lock-held preflight gate (3), the lane-runner agent and fleet skill (4), docs binding and roadmap seams (5). Written to be executed in a fresh session with no prior context. Rev 2 — reworked after adversarial review against the code (lane-record identification under concurrency, persisted lane records, attach falls through instead of refusing).
resource: docs/proposals/concurrent-orchestration-work-order.md
status: draft
---

# Work order: concurrent lockstep

**BUILT, 2026-08-16.** Batches 1–5 shipped in order, each green, then a
three-reviewer adversarial round (code, docs, spec-audit). What the build
changed from this document:

- **Batch 2's run-dir confirmation shipped STRONGER than specified.** The
  listing-diff + lock-pid cross-check described in step 4 was defeatable
  (both sides of the pid check come from newest-match lookups); the build
  identifies the run by its **recorded repo_root** — unforgeable, since only
  our child is told `--repo-root <worktree>` — and demotes `--detach`'s
  printed lines to diagnostics. The start-lock stays. Abort semantics also
  hardened: an unconfirmed launch KEEPS the worktree (a slow-starting driver
  must not find its root deleted) and nothing is ever killed unless
  identified as ours.
- **Batch 1 required a DEVIATIONS.md entry the plan never ordered** (found
  by the spec audit): the wrong-root resume refusal and the attach
  fall-through are §9.2 narrowings and are now logged there.
- **`lock_held` grew four honesty fixes from review:** the open is retried
  once (the AV transient would otherwise read as a holder), an open-refusal
  is its own category (`open-refused` — an ACL or read-only attribute is not
  a lock), the range is whole-file on POSIX and a fixed large range on
  Windows (the gate's docstring states the blind spot), and a FOREIGN holder
  file blocks. `pid_alive` lives in `gates/_common` — a gate never imports an
  engine private.
- **The decision channel is the cockpit pane, not a bare resume:**
  `approve.ps1` now passes the run's recorded root and the main repo's
  config itself, `rejection.txt` remains the pane's artifact (the
  lane-runner falls back to the approval record's error when a human
  answered outside it), and the harvest order is walkthrough → harvest →
  merge — the branch has no commit until harvest creates it.

**Execute in order. Each batch is a commit; full pytest is green before the
next one starts.** Design rationale lives in the conversation that produced
this order and in `copilot-work-order-mimir-db-concurrency.md` (the work-repo
half: it makes the *databases* safe; this order makes the *driver fleet*
safe). The review that produced rev 2 verified this document's factual claims
against the code; file:line citations below are from that pass and are load-
bearing — if the code has moved, re-verify before building on one.

## 0. Read this first

**What is being built.** Lockstep already tolerates multiple drivers — the
lockfile is per run dir, and `--repo-root` / `--runs-dir` / `--config`
(cli.py:962-967) are the whole isolation surface. What is missing is
everything around that fact: nothing records which tree a run must be resumed
from, nothing mechanizes the worktree-per-run recipe (so every agent would
improvise it), nothing gives a flow a fast named failure when an external
resource is lock-held, and nothing tells a lane agent what its report must
contain. Five small pieces, no engine redesign.

**The fleet model this order serves** (the contract, fixed by prior design
discussion — do not re-derive):

- One **worktree per writing run**, fresh, on its own branch, removed after
  harvest. Read-only flows may share the main checkout.
- Central `--runs-dir` (the main repo's `runs/`) so the cockpit, seeds,
  `session_spend`, and `plan_card` keep seeing everything. Fleet launches
  always pass `--fresh`.
- Drivers are **detached**; lane agents are disposable. A lane agent's job
  ends at a terminal event: run done, run failed, or exit 6. **Exit 6 is the
  decision signal** — the agent returns a decision packet (evidence verbatim,
  `quiescent.py` confirmed 0) and the human answers in their own terminal.
  All existing cockpit rules bind unchanged.
- Cross-run mutual exclusion (shared DBs, ports, external services) is the
  **orchestrator's** job via lane assignment — not the engine's. See §6.

**What is NOT being built, and must not be started:**

- **A cross-run exclusive-token registry in the engine.** Tokens stay
  per-driver. A machine-wide token namespace is an r7-shaped feature with
  spec surface (where does it live, what happens on driver death, how does
  `verify` see it) — it goes to ROADMAP-NOTES (Batch 5), not to code.
- **The protected-path floor.** Already designed and rejected
  (PROPOSAL-sssf-adoptions §5); concurrency does not change why.
- **Any DuckDB awareness in `src/lockstep/`.** pydantic stays the only
  runtime dependency. The gate in Batch 3 is a generic file-lock probe built
  on stdlib; MIMIR specifics live in the work repo.
- **A `--worktree` flag on `run`.** The recipe is contrib tooling (Batch 2),
  not CLI surface — same reasoning as the cockpit: tested programs outside
  the engine, engine unchanged.
- **A new exit code.** The frozen set is `0/2/3/4/5/6/7/8` (`__init__.py`);
  Batch 1's refusal reuses `EXIT_CONFIG` (7), the existing refusal precedent
  throughout cli.py.

**Frozen surfaces:** untouched. No exit-code changes, no hash-composition
changes, no format_version bump. Batch 1 adds a *recorded* (never hashed)
field; pydantic ignores unknown fields on load, so old state files load into
the new model (field defaulted) and — if it ever mattered — new state files
load into old code. If review during the build finds otherwise, stop and log
the resolution in DEVIATIONS before proceeding.

---

## Batch 1 — engine guardrails: the resume root, and attach under a live lock

The two real footguns multiple drivers add, resolved against the code:

1. **Resume-from-the-wrong-tree.** A run started with `--repo-root <worktree>`
   must be resumed against that worktree: snapshots, restores, and the M7
   fingerprint are all relative to it. **The repo root is not currently
   recorded anywhere durable** — `RunState` (state.py:152-190) has no such
   field and no run-start event carries it; do not hunt for an existing one.
   Build:
   - Record the resolved repo root in `RunState` at run creation (recorded,
     never hashed; defaulted so pre-field state files still load).
   - Comparisons use `resolve()` **then `os.path.normcase`** — Windows case
     differences must not produce false refusals.
   - `resume` with a mismatched root: **refusal, exit 7 (`EXIT_CONFIG`)**,
     printing both paths — a wrong-tree resume rolls back and snapshots
     someone else's work.
   - `run`-attach with a mismatched root: **fall through to a new lineage
     with a printed note**, exactly as `--fresh` would. Not a refusal: the
     newest lineage for a flow+args will often point at a harvested (deleted)
     worktree, and `find_attachable_run` returns only the single newest
     candidate (state.py:705) — a hard refusal here would permanently brick
     plain `lockstep run <flow>` from the main checkout after every fleet
     run.
   - `status` and `active` print the recorded root, so a fleet's runs are
     tellable apart without opening state files. A missing root (pre-field
     run) prints as unknown, never as a mismatch.
2. **Attach under a live lock — already safe; pin it.** Verified behaviour:
   `find_attachable_run` never inspects locks (state.py:685-705); `cmd_run`
   attaches, loads state read-only, and `acquire_lock` raises `LockHeld` →
   exit 8 before any write (cli.py:387-400; state writes are resume-gated at
   cli.py:401-409). A *stale same-host* lock is silently cleared and the run
   attached — desired, so the invariant is "never attach while the lock is
   **live**", and the test asserts the second invocation **never writes or
   locks** (it does read state.json — "never touches" would be wrong). Only
   the resume/cross-host case is pinned today (tests/test_lifecycle.py:572);
   the run-attach-under-live-lock test is new: driver A live (fake executor,
   slow node), `run` same flow+args, assert exit 8, A's run dir byte-stable.

Tests: wrong-root resume refusal (worktree fixture), run-attach root
mismatch falls through to a new lineage, pre-field state loads, the exit-8
pin above. TDD throughout.

## Batch 2 — contrib: the lane launcher and lock hygiene

Tested programs, not inline recipes — the cockpit precedent.

- **`contrib/lane.py`** — mechanizes the launch so no agent improvises it.
  `python contrib\lane.py start <flow> [--branch <name>] [--arg k=v ...]`:
  1. Creates the worktree (fresh, own branch). Known gotchas it owns so
     agents don't: gitignored `.venv`/`lockstep.toml`/`runs/` do not exist in
     a fresh worktree; AV `PermissionError` retry-once on worktree add and
     remove; **every path passed onward is absolute** (the detached driver
     re-runs the argv with `cwd=Path.cwd()` — cli.py:274,284 — so relative
     paths resolve against wherever lane.py ran). Also verifies the main
     repo has `gc.auto` disabled (§6.3) and warns when it is not.
  2. Runs `verify <flow> --repo-root <wt> --config <main>\lockstep.toml` —
     `--repo-root` included because verify resolves personas and child flows
     against it (taskgraph.py:414,614).
  3. Launches `<main>\.venv\Scripts\lockstep.exe run <flow> --repo-root <wt>
     --config <main>\lockstep.toml --runs-dir <main>\runs --fresh --detach`,
     forwarding each `--arg k=v` (the flow must declare the arg — undeclared
     args are rejected, cli.py:113-115).
  4. **Identifies the new run dir without trusting `--detach`'s stdout.**
     The detach parent locates the run by newest flow_hash+args match
     (cli.py:344-347 → `find_attachable_run`, `max(candidates)` at
     state.py:705), so two lanes starting the same flow+args
     near-simultaneously can each print the *other's* run dir. lane.py
     therefore (a) holds a start-lock (a lockfile under the main `runs/`)
     across steps 3-5 so identical launches serialize for the seconds a
     launch takes, and (b) confirms by diffing the runs-dir listing
     before/after and checking the new dir's lock holder pid matches the
     printed driver pid. Mismatch = abort loudly, kill the driver, remove
     the worktree.
  5. **Persists the lane record** — the artifact every later step keys on —
     to `<worktree>\.lockstep-lane.json`: `{worktree, branch, run_dir,
     driver_pid, flow, args, started}`. Also printed as one JSON line for
     the orchestrator. The file is excluded from the harvest commit.
  `lane.py harvest <worktree>` reads `.lockstep-lane.json`, refuses while
  `inspect_lock(run_dir)` reports a live driver, commits the branch (the
  repo's configured git identity; message names the flow and run dir; the
  lane file excluded) or exports a patch, and removes the worktree only when
  clean-after-commit. `lane.py abandon` is the explicit destructive sibling
  and says what it is deleting.
- **`contrib/who_holds.py <path>`** — pid-liveness check over the
  `<file>.holder.json` convention (shared with the work-repo runbook):
  prints `LIVE`/`STALE`/`NONE`, always exit 0. Reports, never decides.
- **`contrib/attention.ps1`** gains nothing; `lane.py start` *prints the
  command* to start it per run, keeping the human-notification path
  agent-independent.

Tests: lane record shape and persistence, start-lock serialization (two
starts, same flow+args, distinct run dirs each matching its own lock
holder), refusal to harvest with a live driver, worktree cleanup logic
(against a temp git repo), who_holds liveness matrix.

## Batch 3 — gates: `lock_held`

`src/lockstep/gates/lock_held.py` — follows the existing gate convention
exactly (`main(argv)` invoked as `python -m lockstep.gates.<name>`, findings
via `_common.emit()`, **always exit 0**, verdict values `"pass"`/`"block"` —
`gates/_common.py:47`; there is no "fail" verdict, do not invent one).
Stdlib only: a **non-blocking lock attempt** on `--path` (`msvcrt.locking`
with `LK_NBLCK` on Windows, `fcntl.lockf` elsewhere), released immediately.
Verdict `block` quotes `<path>.holder.json` when present (pid, purpose,
liveness) so the failure names its holder. Documented limits, in the
docstring: it is a *diagnostic*, not a mutex or reservation — the resource's
own lock is the lock; and a process that merely has the file open without an
OS lock is invisible to it (a held DuckDB write lock is visible; a plain
open handle is not). Tests in `tests/test_gates.py` style: held / free /
stale-holder-file, with `pytest.mark.skipif` per platform — `fcntl` does not
import on this Windows machine, and full pytest must stay green here.

## Batch 4 — the lane-runner agent and the fleet skill

- **`.claude/agents/lane-runner.md`** (frontmatter per
  `.claude/agents/run-diagnostician.md`; tools: Read, Grep, Glob, Bash — no
  write tools; the *run* writes, the agent watches). The contract, verbatim
  in the agent body: drive one lane record (`.lockstep-lane.json`);
  `wait --timeout` as heartbeat (timeout exits 1 — report progress and
  continue); turn-ending events are done / failed / exit 6. **Exit 6 covers
  two cases — distinguish by `<run_dir>/rejection.txt`:** absent = the
  detached auto-reject handoff → return the **decision packet** (run_dir,
  node, `approval-evidence.txt` verbatim through its impact/reversible
  lines, `question_card.py` output verbatim, `quiescent.py` exit status,
  token spawns from `status`); present = a human already rejected → quote
  `rejection.txt` verbatim and stop the lane, never characterise it. Money
  is never quoted from `status` (it reports token spawns, cli.py:736) or
  from memory — currency comes only from `plan_card.py`/`cost_report.py`
  artifacts. On failure: triage + exactly one recovery recommendation (the
  run-diagnostician's discipline). Never answer an approval; never spawn a
  second driver on its run dir; never resume except from the lane record's
  worktree.
- **`.claude/skills/fleet-ops/SKILL.md`** (frontmatter per
  `.claude/skills/debug-run/SKILL.md`) — the orchestrator side: when to
  give a run a worktree (it writes) vs the main checkout (readonly flows);
  launch via `lane.py`, one lane record per agent; DB/port/service lanes are
  assignment, with the librarian-style single writer lane for shared
  resources; relay decision packets with evidence verbatim + the existing
  handover rules (`quiescent.py` 0 before any handoff); `doctor` once before
  a fleet, never per-agent; total in-flight spawn cap across the fleet, not
  per run (default: eight, §6.1); recovery table (agent dead → new agent on
  same lane record; driver dead → `status`/STALE → resume from the recorded
  root); and the **harvest walkthrough** (§6.2): after a green run, present
  what was delivered to the domain expert in plain terms grounded in the
  lane's evidence (branch diff, run record), and park the branch until that
  discussion approves the merge — the merge itself is the expert's decision,
  never the orchestrator's.

Both documents cite COCKPIT-THEORY-OF-OPERATIONS rather than restating it —
one source for the approval rules.

## Batch 5 — docs binding and roadmap seams

- `docs/guides/FLEET-OPERATIONS.md`: the model from §0 in full — recipe,
  lane lifecycle, decision-packet protocol, the DB pattern summary (pointer
  versions, outboxes, single-writer lane) with a pointer to the work-repo
  runbook for the MIMIR half.
- CLAUDE.md: add `lane.py`, `who_holds.py`, the wrong-root resume refusal,
  and the attach fall-through to the commands/notes sections; one line under
  the cockpit rules: "a lane agent's decision packet is subject to the same
  evidence rules."
- ROADMAP-NOTES seams recorded: cross-run exclusive tokens (r7 candidate,
  with the driver-death question stated); runs-root as config rather than
  flag; `gc`/`explain --graph` awareness of runs whose worktrees are gone
  (their recorded roots dangle after harvest — Batch 1 makes that harmless
  for attach, but `gc` retention and `explain` could say so).
- THEORY-OF-OPERATIONS: one paragraph on multiple drivers — what the engine
  guarantees (per-run-dir locking, job containment per driver) and what it
  deliberately does not (cross-run exclusion).

## 6. Owner decisions (asked as open questions; answered by the owner, 2026-08-16)

1. **Fleet-wide spawn ceiling: eight.** At most 8 concurrent harness spawns
   across the whole fleet — the skill's default ceiling. As lane count
   grows, lower `--max-workers` per run so the fleet total stays under it.
2. **Harvest policy: always park, then walk it through.** Lane branches are
   never merged automatically. After a green run the orchestrator works
   with the domain expert directly: explain what was delivered in plain
   terms, grounded in the lane's evidence (the branch diff, the run
   record) — never narration in place of evidence, the standing cockpit
   rule — discuss, and merge only on the expert's approval. Batch 4's
   skill carries this duty.
3. **git auto-gc: disabled.** `git config gc.auto 0`, applied to the main
   repo 2026-08-16. It is local (per-clone) config, so each machine sets it
   once — FLEET-OPERATIONS and the getting-started path say so, and
   `lane.py start` verifies it and warns when unset (Batch 2).
