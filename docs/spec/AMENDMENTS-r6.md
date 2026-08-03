---
type: amendment
title: Lockstep spec — Revision 6 amendments
resource: docs/spec/AMENDMENTS-r6.md
---
# Lockstep spec — Revision 6 amendments

**Status: adopted.** Delta from Revision 5; authority order r6 > r5 > r4 >
SPEC.md (r3). This revision ships the first two §16 roadmap items —
**structured progress (§16.1)** and **checkpoint steering / cancel
(§16.2)** — whose layouts v1 already reserved. No flow-format change:
`format_version` stays 1.x; the driver becomes v0.2.0. The §16.2 permanent
non-goals (mid-flight injection into a running node, harness RPC) remain
non-goals.

---

## C1. Structured progress (§16.1 adopted)

Convention over machinery, exactly as reserved: a node MAY append
`ProgressEvent` JSON lines to `progress.jsonl` in its phase directory (map
items: `items/<n>/progress.jsonl`); the standard footer already offers this.

Adopted behavior:

1. A driver-owned **tailer thread** (1s cadence) follows every phase
   directory's `progress.jsonl` by byte offset and appends each complete,
   parseable line to `events.jsonl` as `{"kind": "progress", "node": …,
   "item": …?, "step": …, "pct": …?, "note": …}`. Unparseable or partial
   lines are skipped silently — progress is advisory. A final sweep at run
   end drains anything the cadence missed.
2. `lockstep status` renders the LATEST progress entry per node (step, pct)
   from `events.jsonl`.
3. **Hard rule, restated from §16.1:** progress never influences scheduling,
   hashing, gating, budgets, or retries. A node reporting 100% and then
   failing is simply failed.

## C2. Checkpoint steering (§16.2 adopted)

`lockstep steer <run_dir> <node_id> "message"` appends a `SteerMessage`
(`author: "local-user"`, `consumed: false`) as one JSON line to
`mailbox/<node_id>.jsonl`. Exit 0; exit 7 for an unknown node or missing
run dir.

**Consumption — defined checkpoints only** (unchanged from §16.2): before a
node spawns; between heal rounds (a heal re-spawn is a new spawn); between
map items at `concurrency: 1` (the mailbox is re-read before each item).

**Rendering and hashing.** At each checkpoint the ENTIRE mailbox — consumed
and unconsumed messages alike, in file order — is rendered into the prompt
inside a steering block:

```
--- steering ---
<ts> <author>: <message>
--- end steering ---
```

and into the hash text, so steering folds into `input_hash`. Rendering all
messages (not only new ones) is what makes the hash REPRODUCIBLE: a resume
re-plans the same prompt the spawn actually saw; a new message grows the
block and correctly invalidates. The `consumed` flag is bookkeeping recording
when a message first entered a spawn — it is marked (file rewritten in place)
at spawn time, and it drives resume: **a `done` node with unconsumed mailbox
messages is re-marked pending on resume** (§16.2 "steering a done node marks
it for re-run"). Steering text is operator instruction, deliberately NOT
data-fenced — unlike interpolated content, it is meant to be followed.

## C3. Cancel (§16.2 adopted)

Executors record the spawned process id in `<phase_dir>/pid.txt`.
`lockstep cancel <run_dir> <node_id>`:

1. writes a `<phase_dir>/CANCELLED` marker,
2. kills the recorded process TREE (same platform mechanics as §8.5
   `kill_tree`, by pid),
3. prints the matching steer/resume commands.

The running driver sees the death as a failed spawn, finds the marker, and
marks the node `failed` with error `cancelled` — consuming **no** retries
(neither the M4 automatic retry nor RetrySpec/B2 defaults fire on a
cancelled node) and no corrective re-spawn. The node restarts from a known
input on the next resume rather than mutating mid-thought. Exit 0 on kill;
exit 7 if the node has no recorded live pid.

## C4. Housekeeping

- `steer` and `cancel` leave "reserved" status; the §3 reserved-command note
  is superseded. Their own exit codes: 0 success / 7 error, as above.
- Driver version 0.2.0. Exit codes and `format_version` 1.x remain frozen.

---

## Test-list deltas

- C1: progress lines written by a node appear in `events.jsonl` as
  `kind: "progress"`; `status` renders them; a node reporting 100% then
  emitting garbage is simply failed (advisory rule).
- C2: a steered node's prompt and hash contain the steering block; messages
  are marked consumed at spawn; an unchanged mailbox leaves the hash stable
  across resume (no spurious re-run); a NEW message invalidates; steering a
  `done` node re-marks it pending on resume.
- C3: cancelling a running node kills its process tree, marks it
  `failed(cancelled)` with exactly one attempt (no retries), and the run
  exits 3; `resume` re-runs it.

*End of Revision 6 amendments.*
