---
name: debug-run
description: Diagnose a failed or stuck lockstep run directory — read status, decode exit codes, find the failing node's artifacts (rotated attempts, result channels, envelopes), and decide between resume and --fresh. Use when a `lockstep run` exits nonzero or a node behaves unexpectedly.
---

# Diagnosing a lockstep run

Start: `.venv\Scripts\lockstep.exe status <run_dir>` — statuses, attempts,
heal rounds, gate verdicts, token spawns.

## Exit codes (frozen, SPEC §3)

`0` ok · `2` gate BLOCK (read `verdicts` in status / `phases/<gate>/result.json`)
· `3` node failed after retries · `4` budget/wall-clock trip (state persisted,
resumable) · `5` static verification · `6` approval rejected (incl. non-TTY
auto-reject) · `7` executor/config error or run-time refusal · `8` lock held.

## Where the evidence lives: `<run_dir>/phases/<node>/`

- **Harness nodes**: `prompt.txt`, `argv.json`, `stdout.log`, `stderr.log` —
  LATEST attempt; prior attempts rotated to `*-attempt1.*`, `*-attempt2.*`, …
  Read the rotated ones: attempt 1 usually explains attempt 2. **Shell nodes**
  write only `stdout.log`/`stderr.log` (rotated on retry too, per r5 A4) —
  their missing `prompt.txt` is normal, not corruption.
- `result.json` / `result.txt` — the validated result the driver stored.
- `stdout.log` for a claude harness node is a JSON envelope: check
  `is_error`, `api_error_status`, and `result` (the model's final text).
- Gates: `attempt-<n>.patch` (the blocked attempt, preserved before rollback)
  and `discarded-<n>/` (files created by the blocked attempt — moved, never
  deleted). Map items: `items/<n>/` with the same shape.
- **Write-scope violations** leave the same shape, attempt-scoped:
  `out-of-scope-<attempt>.patch` (the whole blocked attempt, written BEFORE the
  tree was put back) and `out-of-scope-<attempt>/` (paths the node created —
  moved, never deleted). The node's `error` names every violating path and what
  happened to it: `restored to its state before this step`, `moved aside into
  out-of-scope-N/`, or `left staged as you had it` for a path the operator had
  already staged. `THE ROLLBACK DID NOT COMPLETE` means a partial restore — the
  message names the paths already handled and the tree is part-way back; the
  patch is the recovery path.
- `touched-<attempt>.txt` — on a node that declared `spec.writes` and SUCCEEDED,
  the in-scope paths it changed. Its absence on a failed node is deliberate: a
  failed spawn's changed paths are wreckage, not a record.
- `events.jsonl` — one line per transition; a trailing partial line after a
  crash is normal and tolerated.

## Failure signatures seen in practice

- `contract validation failed twice: result is not valid JSON … char 0` — the
  node produced an EMPTY result. Check the envelope: a 429 with "session
  limit" means wait for reset then `resume`; a prose `result` means the model
  narrated instead of emitting bare JSON (when no result file exists the
  driver falls back to the LAST balanced top-level JSON value in stdout,
  markdown fences stripped — it need not be final, but if there is none,
  tighten the task prompt).
- `provider limit/overload (429|529)` in the node error (the driver names it
  and prints a resume hint, AMENDMENTS-r5 B3) — a transient provider
  incident. Harness nodes already retry 2× with minute-scale backoff by
  default (r5 B2); if it still failed, the incident outlasted the backoff:
  wait, then `resume <run_dir>`. A node that set `retry: {"max": 0}`
  explicitly opted out of the default.
- Exit 7 "not git-managed" — a healing gate (`heal.max_rounds > 0`) with
  `rollback: true` on a non-git tree: init git or set `rollback: false`.
- Exit 8 — read `<run_dir>/lock` (pid/hostname). Same-host dead pid clears
  automatically; cross-host requires `resume <run_dir> --force-unlock`.
- "changed OUTSIDE lockstep" warning on resume — external edits detected via
  the lineage-head fingerprint; done nodes with un-consumed outputs re-run.
- Transient `PermissionError` on this machine is usually AV — retry once.

## Steering and cancel (r6)

`steer <run_dir> <node> "msg"` appends to `mailbox/<node>.jsonl`; consumed at
the next checkpoint (node spawn, heal round, map item at concurrency 1) and
rendered as a `--- steering ---` block that folds into the hash — steering a
`done` node re-marks it pending on the next resume. `cancel <run_dir> <node>`
kills the recorded process tree (`phases/<node>/pid.txt`, plus the Windows Job
Object named in `phases/<node>/job_name.txt`); the node fails as
`cancelled` with NO retries and restarts from a known input on resume. Latest
per-node progress (advisory `progress.jsonl`) shows in `status`.

On Windows, `phases/<node>/job-unavailable.txt` means that node got NO job
object and its teardown fell back to `taskkill /T /F` alone — the mechanism
that is unreliable against a `pi.cmd` → `cmd.exe` → `node.exe` chain when a
security product denies the termination call. If a node's descendants survived
a kill, look for that file first: it says the guarantee was never in force,
which is otherwise indistinguishable from it having failed. Its contents are
the `GetLastError` from whichever call was refused. See DEVIATIONS.md
(2026-08-10).

## Resume vs run vs run --fresh

`resume <run_dir>` re-runs failed/stale-running/pending/blocked nodes and
hash-skips unchanged done work (per-item for maps). It replays the flow copy
ARCHIVED in the run dir (`flow.tg.json`) — an edited flow file is neither
picked up nor rejected; the flow_hash refusal fires only if you pass
`--flow <edited-file>` explicitly. To adopt an edited flow, use
`run <flow>` — the new hash starts a new lineage. `run <flow> --fresh`
forces a new lineage for an UNCHANGED flow (`--fresh` does not exist on
`resume`). `token_spawns` persists across a lineage — check budget headroom
before resuming a many-attempt run.
