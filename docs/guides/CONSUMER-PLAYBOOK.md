---
title: "Consumer playbook: the mechanism you were about to ask for"
description: The shipped answers to the problems every serious consumer repo hits in its first real campaign — found because the OW-07 feedback asked for four features that already existed under other names. One page, symptom-first, so the next consumer searches here before authoring a workaround.
resource: docs/guides/CONSUMER-PLAYBOOK.md
---

# Consumer playbook: the mechanism you were about to ask for

The OW-07 consumer campaign (22 spawns, seven verified runs) produced two
thoughtful upstream feature requests — and four of their pain points had
shipped answers the authors never found. That is a discoverability failure,
and this page is its fix: **symptom first, mechanism second**, so the search
that starts from the pain lands here. The full disposition of that feedback
is `docs/proposals/upstream-response-ow07-feedback.md`.

## "I edited the flow and lost all my completed work"

You did not. Editing a flow changes `flow_hash`, which starts a new lineage
— that refusal is the cache's integrity basis and it stays. The cost does
not:

```
lockstep run <edited-flow> --seed <old_run_dir>
```

serves every node whose input hash still matches, free, into the new
lineage; only what your edit actually touched runs. Preview first with
`lockstep explain <old_run_dir> --graph`. Two documented exceptions re-run
regardless: **shell nodes** (§0.1.7 — they spend no tokens) and **map
items** (their per-item hash composes after planning; a map-heavy flow
re-bills its items across lineages — budget for that before seeding).

Special case, budget-only edit: don't edit the flow at all —
`lockstep resume <run_dir> --max-agent-spawns N` raises the cap for that
drive, journaled, no new lineage.

## "Every review pass re-audits the whole history and finds new complaints"

Two mechanisms, one shape:

- **Delta review**: `python -m lockstep.probes.node_diff --node <writer>`
  diffs the two trees the engine RECORDED for that node
  (`tree_before`/`tree_after`) — phase 1's review can never be re-run
  against phase 2's work, which is exactly what a live `git diff` gets
  wrong on resume (`lint-live-diff-per-phase` exists because of it).
- **Convergence**: the reviewer → adjudicator → ledger-gate shape,
  templated in `flows/factory/adjudicated-review.tg.json`. Discovery and
  gating must not share a mind; the ledger remembers rounds so nothing is
  re-litigated. FLOW-AUTHORING "Convergent review" is the why;
  `lint-unadjudicated-reviews` names the ping-pong shape you have now.

## "The agent got quarantined for helpful lint cleanup"

The write-scope quarantine is post-hoc; the **preventive** layer is the
`pi-guarded` stanza, which attaches the ADDENDUM-A scope guard via
`--extension`: an out-of-scope write fails *in-session as a tool error*, the
agent self-corrects for free, and the node finishes in scope instead of
dying at the snapshot. (Enforce, never enable — deleting the extension
changes nothing a correct agent can do.) The other honest fix: if satisfying
the linter makes the node touch `src/x.py`, then the node writes `src/x.py`
— declare it. A scope you hope won't be enforced is not a scope.

## "Detached runs can't wait for an approval"

They can — the auto-reject **is** the park. A detached run reaching an
approval records `approval auto-rejected (non-TTY stdin)` — a different
recorded fact from a human's "reject", with no `rejection.txt` — and exits
6, the documented handoff signal. `wait`/`status`/MISSION word it as
awaiting a human. `lockstep resume` from a real terminal asks once, for
real. Do not split the flow in two to avoid it (FLOW-AUTHORING, the
approval role).

## "explain --graph said fresh, but the run re-billed those nodes anyway"

A shell node always re-runs, and anything that interpolates its output is
fresh only if the re-run prints the same bytes — `explain --graph` now
labels those nodes `conditionally fresh` with the shell named. The fix is
authoring discipline: **a shell node's output is a hash input for
everything downstream that consumes it.** Emit stable text (verdicts,
counts, facts); keep timings, durations, and logs on stderr or in evidence
files. A pytest duration inside a Verdict reason cost the OW-07 campaign
two re-billed harness nodes on a seeded run. The shipped gate bodies in
`src/lockstep/gates/` are the pattern: tested programs with deterministic
pass-path output.

## "A run shows all nodes pending and wait says exit 4"

On drivers ≥ 0.10.1 that run says `refused` everywhere (`wait` exits 7 with
the reason; `status` prints the refusal verbatim; `active` tags it
REFUSED; MISSION stops saying "waiting"). If you see the all-pending +
exit-4 shape on an older driver, read the detached log before trusting the
exit code — and do NOT plain-resume a dirty-scope refusal: resume skips the
dirty-scope preflight by design, and a hash-missed writer will legally
overwrite the edits the refusal was protecting. Commit or stash first.

## "The reviewer says it can't see the files spec.reads names"

Correct — `spec.reads` is an input-hash declaration only; it does not tell
the harness which files exist and does not grant or perform reads. Either
name the files in the task text, or opt in to the manifest:
`"reads_manifest": "paths"` appends the resolved list to the prompt (and
the hash). A zero-match glob says so explicitly. FLOW-AUTHORING "Declared
reads" has the mechanics.
