---
type: plan
title: "Upstream response: bounded retries, truthful shell results, and safe recovery"
description: Point-by-point disposition of the downstream request of 2026-09-24 (sections A, B, C) against lockstep 0.18.0 — A's first slice built and shipped in 0.19.0, B's capture question waiting on a redacted fixture, C's conflict gate still deferred with the consumer's evidence recorded. Every claim was checked against the source before classification.
resource: docs/proposals/upstream-response-bounded-retries.md
---

# Upstream response: bounded retries, truthful shell results, and safe recovery

**Responding to:** *"Upstream request: bounded retries, truthful shell
results, and safe recovery"* (Sol, 2026-09-24), filed from the consumer
repo against `v0.18.0` (`dc2da89`). The request was checked claim by claim
against the source. It is accurate about the code, and careful about what
its evidence does not show — which is why this response can be short.

| Section | Disposition | Shipped in |
|---|---|---|
| A — per-node spawn ceiling | **Built** (first slice) | 0.19.0 |
| B — shell result policy / capture | **Needs your fixture** for the capture half; the opt-in stays deferred | — |
| C — retry baseline and conflict-aware rollback | **Answered** for retries; the conflict gate stays deferred (OPEN-WORK item 13) | — |

## A. Bound every spawn cause at the node boundary — built

Confirmed first: `roles.py::_execute_with_retries` did take one additive
automatic retry on a timeout or empty result regardless of `retry.max`
(SPEC §9.3 / AMENDMENTS M4 — a stated guarantee, not an accident). Your
report is the second starvation report OPEN-WORK item 10 was waiting for.

**What 0.19.0 adds:** `budget.max_spawns_per_node` (optional, `>= 1`). It
bounds every token-costing spawn of ONE node — or one map item — across the
lineage: initial, `retry`, the M4 auto-retry, contract and scope
correctives, heal rounds, resumes, and a baseline gate's pre-run spawn.
Against your acceptance tests:

1. `retry.max: 0` + a timeout, cap reached → no auto-retry. The node fails
   with the cap AND the last attempt's own reason; an unrelated node keeps
   the wallet. The trip is not exit 4: a capped work node exits 3, a capped
   gate blocks terminally (exit 2).
2. Counters (`token_spawns` on node and item records) persist across
   resume, heal rounds, `adopt` and map item resets. `--seed`-served nodes
   and items spawn nothing and are never counted. `--replay` does count, as
   it already did against the wallet — a replay cannot trip a cap its
   recording did not.
3. Once a map's width is known the engine journals `{"kind": "budget",
   "op": "forecast"}` and warns when the MINIMUM still needed (one spawn per
   unfinished item plus mandatory downstream token-costing nodes) exceeds
   the wallet. Advisory; silent under `--seed`/`--replay`.
4. A trip is journaled `{"kind": "budget", "op": "node-cap", ...}`;
   `status` prints `spawn cap:` naming each capped node and item and the
   way out. **Your trade-off question, answered: the cap is not
   overrideable on an archived lineage.** Raising it is a flow edit and a
   new lineage (`run <flow> --seed <run_dir>` serves the finished work) —
   the one exclusion, stated in the error, is that a new lineage's counters
   start at zero.

Also: `verify --lint` warns (`lint-spawn-cap-below-heal`) when a cap cannot
cover a gate's heal rounds or baseline spawn. Not in this slice: a
whole-map total, a verify-time prediction (map width is runtime data), and
a parent flow's cap reaching into a `kind:"flow"` child (the child's own
flow governs its nodes). DEVIATIONS 2026-09-24 is the full record.

## B. Shell result policy and empty-output diagnosis — your fixture, please

**Capture.** Reading `executors/shell.py` and `executors/proc.py` at 0.18.0
found no capture defect: stdout is redirected straight to `stdout.log` and
read back after exit, and `PYTHONIOENCODING=utf-8` is set for every child
(the one known empty-log mechanism, and that one exits 1, not 0). Your run
predates several of those changes, so a redacted fixture would settle it:

- the node's `argv[0]` — only a bare `python`/`python3` is rewritten to the
  driver's interpreter; `python.exe`, `py`, or a path resolves through
  `PATH` in the spawned environment;
- the byte size of every `stdout*.log` / `stderr*.log` in the phase dir;
- the driver version recorded in `state.json`, and the attempt events.

**Auto-retry of a shell node.** Real, and the risky half of item 24: M4's
retry exists for a flaky harness, yet it also re-runs a deterministic shell
command that may already have made its side effect. Opting shell out (as
the `flow` executor already does) is a one-line change to a stated
guarantee's scope, so it waits for the owner's decision; today,
`budget.max_spawns_per_node` does not help here, because shell spawns cost
no tokens and are never counted.

**Silent-success opt-in.** Still deferred on item 24's trigger — a
second silent-success node whose body genuinely cannot print. Your
printing-command case, as you said yourself, is a capture question first.

## C. Evidence-preserving retries and conflict-aware rollback

**What attempt 2 sees.** An ordinary retry (including the M4 auto-retry)
does NOT restore the tree: attempt 2 starts from whatever attempt 1 left,
in-scope edits included. Rotated `stdout-attemptN.log` files and the
`kind:"attempt"` events record that each attempt happened and why; the
write-scope check and quarantine run once, after the last attempt, against
the node's pre-attempt baseline (`tree_before`). Your observation that
attempt-1 in-scope edits were absent from attempt 2's result is therefore
the agent's own rewrite, not an engine reset.

**Heal rollback already preserves evidence** before it restores:
`phases/<gate>/attempt-N.patch` holds the whole diff since the gate's
baseline and `discarded-N/` holds files the attempt created; the journal
labels each path `restored` or `discarded`, and `restored-undeclared` names
a restored path outside every node's declared scope.

**The conflict gate itself** — refuse the restore instead of warning after
it — is E8-full, OPEN-WORK item 13, and amends SPEC §9.4.4's stated
rollback scope. Its trigger is a real `restored-undeclared` event. Your
older-driver evidence (an out-of-scope edit restored on a blocked gate,
under the pre-rename `restored` label) is recorded against the item; it
does not fire the trigger, because it predates the check that would name
the path. If a 0.19.0 run journals one, send the event line.

## Also in 0.19.0, found while verifying this

- `run --detach` waited a fixed 3 s for a refusal; under load a
  dirty-scope preflight outlasted it and the parent reported a clean launch
  of a refusing run. The driver now journals `op: "preflight-passed"` and
  the parent waits for it.
- `contrib/lane.py start` now waits through a Windows `PermissionError` on
  its start lock instead of crashing the second of two concurrent starts.
