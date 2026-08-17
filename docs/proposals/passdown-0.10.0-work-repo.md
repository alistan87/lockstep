---
type: notes
title: "Passdown: 0.9.0 → 0.10.0 (the fleet release) — work-repo integration"
description: Everything the work repo needs to integrate 0.10.0 — what changed by surface, the two behavior changes that will surprise existing scripts, machine-local steps no file carries, the MIMIR-runbook gating, and the verification checklist. Written to be executed by whoever drives the work mirror (Copilot/pi assisted), with no context beyond this file.
resource: docs/proposals/passdown-0.10.0-work-repo.md
---

# Passdown: 0.9.0 → 0.10.0 (the fleet release)

0.10.0 makes **multiple concurrent lockstep runs** safe and operable: the
engine learns which tree a run belongs to, `contrib/lane.py` mechanizes
worktree-per-run launches, a `lock_held` gate fronts held-lock failures with
names, and the delegation roles (dispatcher / lane-runner) are contracts in
`docs/guides/FLEET-OPERATIONS.md`. Everything was adversarially reviewed
(three reviewers + a verification round); the build record is
`docs/proposals/concurrent-orchestration-work-order.md`'s header annotation.

## 1. Carrying it over

Preferred: the bundle. At home:

```
.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
.venv\Scripts\python.exe contrib\build_bundle.py --version 0.10.0
```

copy `dist\lockstep-cockpit-0.10.0.zip` across, unzip over the work mirror,
and `pip install --force-reinstall lockstep-0.10.0-py3-none-any.whl` into
the work venv (`contrib/INSTALL-WORK-MACHINE.md` is the full first-install
guide). If you mirror manually instead, the changed surfaces are: `src/`
(via the wheel), `contrib/` (lane.py, who_holds.py NEW; approve.ps1
CHANGED), `.claude/agents` + `.claude/skills` (fleet-dispatcher, lane-runner
NEW; fleet-ops NEW; getting-started CHANGED), `docs/` (FLEET-OPERATIONS.md
NEW; THEORY-OF-OPERATIONS, DEVIATIONS, ROADMAP-NOTES, proposals CHANGED),
`CLAUDE.md`, `CHANGELOG.md`, `README.md`, `tests/` (test_fleet.py,
test_lane.py NEW; test_gates.py, test_lifecycle.py, test_detach.py
CHANGED — the work repo runs full pytest, so carry tests too).

**approve.ps1 caution:** the mirror is a manual copy — if the work copy has
local edits, DIFF before overwriting. The 0.10.0 change is additive (it
reads `state.json`'s `repo_root` and passes `--repo-root` + the main repo's
`--config` to `resume`); losing it means the cockpit pane cannot answer a
lane run's approval.

## 2. The two behavior changes that will surprise existing scripts

1. **`resume` from the wrong tree now refuses, exit 7.** Every run records
   the resolved `--repo-root` it was created against; a resume invoked
   against any other tree stops with both paths printed. Any work-side
   script or habit that resumes from "wherever I happen to be" now fails
   loudly. That is the guardrail working: fix the invocation (resume from
   the recorded root — `status` prints it as `repo root:`), don't work
   around it. Runs created before 0.10.0 have no recorded root and are
   never refused.
2. **An identical `run` whose newest lineage lives in another tree starts a
   NEW lineage** (with a printed `note:` line) instead of attaching. You
   will see this after any worktree-based run: plain `lockstep run <flow>`
   from the main checkout keeps working, at the cost of a fresh lineage —
   `--seed <old_run>` if you want the warm start. Logged in
   `docs/spec/DEVIATIONS.md` (2026-08-16) as two §9.2 narrowings.

Smaller surface changes: `status` gains a `repo root:` line and `active` a
`root:` line (unknown for pre-0.10.0 runs — anything parsing that output
should tolerate both); the run banner may print the fall-through note.

## 3. Machine-local steps (no file carries these)

- `git config gc.auto 0` in the work clone — per-clone config, so the
  mirror copy does NOT bring it. Owner decision §6.3: recorded snapshot
  trees are unreferenced loose objects and auto-gc can prune them.
  `lane.py start` warns when unset.
- Reinstall the wheel into the work venv (above), then `lockstep doctor` —
  version bump + the weekly rule anyway.

## 4. Fleet use at work: what is gated on what

- **Read-only fleets** (reviews, audits, digests — no `spec.writes`, no DB
  writes) can start immediately: they share the main checkout, no worktrees
  needed, ceiling 8 harness spawns fleet-wide (owner decision — pi's ~90s
  RTT punishes over-commitment).
- **Writing fleets** need worktrees (`lane.py start`) and, if they touch the
  MIMIR DuckDB stores, are **gated on the DB runbook**
  (`docs/proposals/copilot-work-order-mimir-db-concurrency.md`, phases 1–3
  minimum: read-only hygiene, path parameterization, holder files +
  preflight). This release lands the pieces Phase 3 builds on:
  `contrib/who_holds.py` is the who-holds tool the runbook specifies (copy
  or import it rather than rewriting), and `lockstep.gates.lock_held` is
  the preflight gate for writing flows.
- **The roles**: at work the main conversation may be Copilot and the
  dispatcher whoever holds the contract — a pi session, a Copilot custom
  agent, or the main agent running `/fleet-ops` inline. The contract is
  FLEET-OPERATIONS "The roles"; the `.claude/` files are Claude bindings of
  it, carried in the bundle for reference even where Claude Code isn't the
  driver. The rules that do not bend: nobody but the human answers an
  approval (exit 6 is the handoff), evidence passes every relay hop
  verbatim, one dispatcher per fleet, and lane branches always park —
  merge only after the walkthrough.

## 5. Verification checklist (in order; zero-token except doctor)

```
.venv\Scripts\python.exe -m pytest                     # full suite green
.venv\Scripts\lockstep.exe --help                      # imports as 0.10.0
.venv\Scripts\lockstep.exe run flows\selftest-replay.tg.json
python contrib\replay_suite.py                         # recorded fixtures still match:
                                                       # 0.10.0 does NOT touch hash
                                                       # composition, so a mismatch =
                                                       # mirror drift, investigate first
python contrib\torture_suite.py
.venv\Scripts\lockstep.exe run flows\demo\compose-smoke.tg.json
.venv\Scripts\lockstep.exe doctor                      # spends small model calls
```

Then one fleet smoke, cheap and self-cleaning (uses the fake executor —
zero tokens; needs a flow with only `kind:"fake"`/shell nodes):

```
python contrib\lane.py start <some-zero-token-flow.tg.json>
# expect: one JSON lane record; a worktree beside the repo; run under runs\
.venv\Scripts\lockstep.exe wait <run_dir printed above>
python contrib\lane.py abandon <worktree printed above>
```

Expect the `gc.auto` warning on the first `start` until step 3's config is
applied — that warning disappearing is itself a check.

## 6. Known machine notes that shaped this release

Session limits kill long-lived agents → drivers are detached and every
watcher tier is disposable (the fleet is readable from disk: lane records +
runs dir + `lockstep active`). AV transient `PermissionError` → lane.py and
the gate retry once where it bites. pi ~90s RTT → the ceiling is 8, not
"what fits".
