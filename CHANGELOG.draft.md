## v0.11.0

The OW-07 response release: the point-by-point disposition of two consumer
reports (`docs/proposals/upstream-response-ow07-feedback.md`, c3e1c2c), built
out. 0.10.1-scope (truthful terminal state) and 0.11.0-scope ship together —
nothing went out between them (865dbf0).

### Breaking / changed behaviour — read this first

- **A post-lock refusal now exits 7, not 4.** A refusal after the lock is
  taken (dirty write scope, a failed heal precondition) used to leave every
  node pending and no journal, so `wait` reconstructed exit 4 — "a plain
  resume continues" — which was wrong advice, because `resume` is E9-exempt
  and would skip the very preflight that refused. The refusal handlers now
  persist a run-level terminal record plus a chained refusal event before
  releasing the lock; `wait` returns the recorded **7**. Anything keyed on
  exit 4 for this case must change. Budget stops still read 4. (bdce9c2)
- **A refused run says so everywhere a reader asks:** `status` prints the
  evidence verbatim, `active` tags **REFUSED** by default, MISSION and the
  cockpit headline say `refused: dirty scope` instead of `waiting`, and a
  `--detach` parent watches a 3s grace window so the refusal lands in the
  launching terminal rather than only in a log. The next drive clears the
  record. (bdce9c2)
- **An auto-rejected approval is worded as a parked question, not a
  decision.** `wait`/`status`/MISSION/cockpit now say "resume from a terminal
  to answer"; a human rejection stays a rejection. Exit 6 is unchanged —
  wording only. (bdce9c2)
- Old-driver compatibility holds: `RunState` ignores unknown fields, so a
  0.10.0 driver reading a newer `state.json` degrades to the old reporting
  rather than failing. (2e1fdba)

### The operator gets two decisions back

- **`lockstep resume <run_dir> --max-agent-spawns N`** — raise (or lower;
  "stop spending" is coherent, warned, never refused) the spawn cap for
  **this drive only**. The flow's ceiling stays the consent artifact, every
  override leaves a journaled budget event that `status` echoes, and the next
  plain resume is governed by the flow again. This replaces the three-command
  workaround (edit the flow, new lineage, `--seed` back). Budgets are not
  hash inputs, so an override re-bills nothing. (6380eea)
- **`spec.reads_manifest: "paths"`** — append the resolved `reads` list to the
  prompt. `spec.reads` alone is an input-hash declaration the harness never
  sees; a reviewer told to "read the files named in `spec.reads`" correctly
  reported no such list and blocked. The manifest is in the prompt, therefore
  in the input hash: a changed match set re-bills, and `explain` names
  `prompt.reads_manifest`. Zero matches say so out loud. It shares
  `apply_reads`' enumerator, so the list the prompt names and the files the
  hash covers cannot disagree. `reads_manifest` without `reads` is invalid at
  `verify`; absent or `"none"` is byte-identical to 0.10.0 (the replay fixture
  passes un-re-recorded). (6380eea)

### The adjudicated-review programme

For flows where review rounds never converge (four consecutive blocks
observed on OW-07 phase 3): discovery and gating must not share a mind.

- `flows/factory/adjudicated-review.tg.json` — the template. Reviewers
  discover under a frozen scope statement; ONE readonly adjudicator
  (`personas/adjudicator.md`) re-grades their raw findings — unevidenced or
  out-of-scope demotes to nit with the reason kept; a NEW blocker is allowed
  but must say why it is in scope. (60ded6f)
- **`lockstep.gates.ledger_check`** — cross-round memory as a repo file the
  model never writes. Merges each round mechanically (new / persisting /
  reported-resolved, nothing ever deleted, returns recorded), blocks only on
  ACTIVE entries at threshold, treats accepted-risk as a human state that
  must carry a disposition or it blocks, and re-runs idempotently on
  identical input so resume revalidation cannot inflate history. (60ded6f)
- New advisory lints (`verify --lint`; exit code unchanged):
  `lint-unadjudicated-reviews` names the ping-pong shape this replaces — two
  or more `Finding[]` producers each gated raw; one gated raw stays
  refine-loop's fine shape (60ded6f). `lint-ledger-rollback` catches the
  ledger gate colliding with heal's default rollback, which would erase the
  cross-round memory every round — the template and FLOW-AUTHORING say
  `"rollback": false`. (f7f7d3c)
- FLOW-AUTHORING gains "Convergent review": the why, delta mode via
  `node_diff`, and the frozen-scope lesson. (60ded6f)

### `explain --graph` stops overstating freshness

- A node whose freshness rests on an always-rerun shell reproducing its
  recorded output now reads **`conditionally fresh <id> — depends on
  always-rerun shell <sid>`** instead of plain "fresh". The OW-07 pre-run
  explain said three fresh; the shell printed a different duration string and
  two re-billed. Taint follows CONSUMPTION (`{steps.<shell>.}`) rather than
  mere ordering edges, and names the root shell through intermediaries;
  counts fold into fresh with the conditional split alongside. (fc87ea2)

### Docs

- **`docs/guides/CONSUMER-PLAYBOOK.md`** (new) — symptom first, mechanism
  second. Four OW-07 feature requests already had shipped answers nobody
  found (`--seed`, `node_diff`, `pi-guarded`, auto-reject-is-the-park), plus
  two new recipes (budget-only edits never fork a lineage; stable shell
  output is authoring discipline) and the 0.10.0-era all-pending/exit-4
  caveat for older drivers. (fc87ea2)
- FLOW-AUTHORING documents the detached-approval idiom (bdce9c2) and states
  the `reads` trap in one sentence — it is an input-hash declaration only
  (6380eea). COCKPIT-FOR-DOMAIN-EXPERTS learns the two words MISSION now
  shows (2e1fdba). CLAUDE.md's command list learns the budget override and
  the adjudicated-review template (f7f7d3c).

### Fixes

- `record_terminal` guards its whole body, not just the load — an AV
  transient on the state write or event append would have replaced the clean
  exit-7 refusal message with a traceback, the reporting mechanism failing
  exactly where it was built to work. (2e1fdba)
- `status`' budget echo derived from a second, unguarded `read_events` call:
  both a duplicate full-journal scan and a regression of the r6 fix that lets
  `status` render over a corrupt journal. It now derives from the guarded
  read. (f7f7d3c)
- `cmd_active`'s comment stops claiming an abandoned refusal cannot linger
  (it can, until `gc`), and `cmd_wait`'s docstring admits the recorded-exit
  path. (2e1fdba)
