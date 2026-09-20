---
type: amendment
title: Lockstep spec — Revision 7 amendments
resource: docs/spec/AMENDMENTS-r7.md
---
# Lockstep spec — Revision 7 amendments

**Status: adopted (2026-09-19).** Delta from Revision 6; authority order
r7 > r6 > r5 > r4 > SPEC.md (r3). **This revision changes no behaviour.**
Every section restates, as spec text, a departure that `DEVIATIONS.md`
had been carrying with the note "an r7 amendment should state this", and
names the entry it restates. The deviations register keeps its entries:
it is the what-why-when trail, and this file is the contract. Where a
section here and its DEVIATIONS entry disagree, the entry is the
evidence and this file has a bug — report it. No `format_version`
change: the format stays 1.x. Every key below is an optional per-kind
`spec.*` key or engine behaviour — the 1.x extension route (DEVIATIONS
2026-08-02/08) — except H's `heal.on_exhausted`, the one first-class
`HealSpec` field, whose entry (DEVIATIONS 2026-08-14) records why it is the
exception. One section, D4, restates a change that lands in the same
commit as this file (DEVIATIONS 2026-09-19, maps); it is a restatement
because that entry, not this file, carries the change.

The r4–r6 pattern is kept: sections are lettered by theme, each names
the SPEC section it amends, and the test-list delta is at the end. The
amendment is deliberately a delta file, not a consolidation:
`flows/audit-spec.tg.json` audits against the layered form and
`flows/selftest-replay.tg.json` checks `SPEC.md` by section heading, so a
consolidation would be a behaviour change in the audit gate.

---

## D. Write scopes and the quarantine (§6, §9.1, §9.3, §9.4, §10.2)

*Restates DEVIATIONS 2026-08-02 (rewritten 2026-08-08), 2026-08-11 (the
LESSONS batch: presence-keying, lints, dirty-scope preflight,
`restored-undeclared`), 2026-08-12 (`{args.NAME}` in scopes), 2026-09-08
(G1b, the scope corrective) and 2026-09-19 (maps).*

### D1. `spec.writes` — the declared write scope

A write-capable node (`harness`, `shell`, and the test double `fake`) MAY
declare `spec.writes`, a list of repo-root-relative paths or `fnmatch`
globs (`*` crosses `/`). The key is **presence-keyed**: absent means
unconstrained (the v1 behaviour); present — including `[]` — is enforced,
and `[]` means "this node writes nothing". `["**"]` is whole-tree access
and SHOULD be accompanied by `spec.writes_rationale`; the advisory
`lint-unscoped-writes` names the omission, and nothing refuses it. A scope entry may interpolate
`{args.NAME}` and nothing else (§6 error `dynamic-write-scope`): a scope
the graph could widen by writing a different answer is not a permit. §6
rejects absolute or escaping entries (`bad-write-scope`).

The declaration reaches the spawn as `LOCKSTEP_WRITE_SCOPE` (JSON; `"[]"`
for a declared-empty scope; unset when undeclared) so an in-harness
extension can PREVENT a violation (ADDENDUM-A: enforce, never enable). The
driver DETECTS after the fact; it never sees tool calls.

### D2. Detection runs inside the `tree` token

Every write-capable executor takes the exclusive token `tree` unless the
node is `readonly` — **shell nodes included** (amending §9.1, which gave
the token to harness nodes only; measured cost +0.3% on the one shipped
flow with a parallel shell wave). Write-scope detection compares against a whole-tree baseline,
so it is sound only while the measured node is the only writer: the
baseline is taken, the node runs, the after-snapshot is taken and diffed,
and any quarantine and corrective happen **before the token is released**.
A declared scope on a node that holds no `tree` token draws the advisory
`write-scope-unenforced`; detection is off for it.

### D3. Quarantine, evidence, and one corrective

A changed path outside the scope is a violation. The engine (1) writes the
blocked attempt as `phases/<node>/out-of-scope-<attempt>.patch` BEFORE any
restore, (2) restores each violating path to the baseline one path at a
time — a creation is MOVED into `out-of-scope-<attempt>/`, never deleted
(§0.1 item 2) — (3) unstages what the agent staged while leaving the
operator's pre-existing staged paths and index alone, (4) journals every
path with its outcome as a `quarantined` transition, (5) refreshes the
lineage head so a crash-then-resume does not read the rollback as external
edits, and (6) fails the node with a message naming every path and where it
went. A restore that does not complete says so and names the unhandled
paths. In-scope writes are left exactly as they are.

After a CLEAN rollback, a harness-kind node gets **exactly one corrective
re-spawn** from the restored tree (`scope-corrective-respawn` journal line;
`attempt` cause `scope-corrective`): the original prompt, the reverted
patch fenced as data, and the scope restated. It spends a spawn, and a
second violation quarantines again with no third chance. Shell nodes get
no corrective (r4 A4's reasoning). The round-2 baseline is the original
one, so `tree_before`/`tree_after` bracket the node's total surviving
change.

On success, the in-scope changed paths are written to
`phases/<node>/touched-<attempt>.txt` and the record carries
`touched_count`, `touched_path`, `tree_before` and `tree_after` — a count
and pointers, never the list (§10.2: `state.json` is rewritten whole on
every record). The recorded tree pair is what `lockstep.probes.node_diff
--node <id>` diffs, so a phase-1 review cannot be re-run against phase-2
work.

### D4. Maps: the scope is the map's, the check is per item

A `role: "map"` node MAY declare `spec.writes` (the §6 error
`write-scope-on-map` is withdrawn). Every write-capable item holds the
`tree` token, so the engine takes a baseline for THAT item inside it and
runs D2–D3 on each item alone: item *k* is never accused of item *j*'s
write, a violation fails exactly the item that made it (the map then fails
through §9.3's `item N failed`), evidence lands under
`phases/<map>/items/<i>/`, the item's own attempt counter drives the
artifact names and the journal ordinal, journal lines carry `item`, and
`ItemRecord` carries the four evidence fields of D3. The scope never reads
`{item…}` (D1). A `readonly` map draws `write-scope-unenforced` like a
readonly work node. Advisory `lint-missing-write-scope` covers write-capable
maps exactly as it covers work nodes.

### D5. Two guards a declared scope buys

- **Dirty-scope preflight.** A fresh `run` refuses when uncommitted
  working-tree paths fall inside any declared scope (they would be legally
  overwritten); `--allow-dirty-scope` overrides; resumes and replays are
  exempt. The run's own directory is never counted.
- **Undeclared restores are named.** When every heal target of a gate
  declares a scope, a §9.4.4 rollback that restores a path outside the
  union of EVERY declared scope in the flow journals `restored-undeclared`
  and says so on the engine log — the signal that an out-of-band mid-run
  edit was reverted. The union is flow-wide, not the targets', so a
  non-target sibling's in-scope path is deliberately not named (the
  2026-08-11 entry says "their union"; the code, and this text, say the
  flow's). The rollback scope itself is unchanged (ROADMAP E8-full waits
  for its first real instance).

## E. Prompt composition and the prompt channels (§7, §9.2, §9.4.6)

### E1. Heal text and steering are run state, rendered at plan time

*Restates DEVIATIONS 2026-07-27 (heal text persisted) and r6 C2 (the whole
mailbox), stated together as that entry asked.* The engine-composed heal
text of §9.4.6 and the steering block of r6 C2 are both persisted in
`RunState` (`heal_texts`, latest round wins, never cleared on pass; the
mailbox, consumed and unconsumed) and both are rendered into the prompt at
plan time, where they fold into `input_hash`. A resume in a fresh process
therefore re-plans the prompt the spawn actually saw, and a healed or
steered node that passed is skipped as §9.2 promises.

### E2. What the heal text carries

*Restates DEVIATIONS 2026-08-11 (E3, E5) and 2026-08-14.* Beyond §9.4.6's
sentence and the fenced findings, the heal text RESTATES the target's own
declared scope — that the scope is unchanged by the findings, that the
node may modify only the declared paths, and that a finding naming a file
outside them is to be reported as out of scope, not edited — folds in the
target's `phases/<node>/attempt-notes.md` (tail-capped at 4000 characters)
so a retry inherits what the previous attempt established, and states the
round ("This is repair round N of M"). All of it is prompt text and hashes
as such.

### E3. The readonly footer asserts a fresh execution

*Restates DEVIATIONS 2026-07-25 (readonly footer, r5 A1) as extended by the
v0.2.x re-run isolation change (ROADMAP 2026-07-26).* `FOOTER_READONLY`
names the stdout channel as the result, omits the phase-directory pointer,
and tells the node the execution is fresh and that artifacts of earlier
attempts are not its input. This is the readonly footer only: the standard
footer still hands a writing node its phase directory, where rotated
prior-attempt artifacts sit. The general principle — prior-attempt
artifacts should not be discoverable by a re-executing node, or must be
marked non-input — remains a ROADMAP candidate (2026-07-26), not adopted
here.

### E4. A JSON string leaf renders raw into shell argv

*Restates DEVIATIONS 2026-08-02, adopted then by explicit decision on a §7
frozen surface.* §7's "parsed, compact-re-serialized" rendering of
`{steps.X.json.field}` applies to prompts (`fence=True`) and to `when`. In a
shell node's argv (`fence=False`) a STRING leaf renders raw — the element
is already a discrete string, so the JSON quotes would become part of the
value. Non-string values still compact-serialize. Shell nodes are
`cacheable=False`, so the change invalidated no cache.

### E5. The stanza closes what the harness would load on its own

*Restates DEVIATIONS 2026-08-14 (pi) and 2026-09-19 (claude).* The hash
covers what the driver sends. A harness that auto-discovers instruction
files or skills from the working tree (pi: `AGENTS.md`/`CLAUDE.md` and
skills; Claude Code: `CLAUDE.md` and `.claude/skills`) adds prompt text
`input_hash` cannot see, which defeats `run`, `--replay`, `--seed` and
`explain` at once. An executor stanza SHOULD close that channel in argv
where the harness documents a switch (`--no-context-files --no-skills`,
verified on pi 0.83.0; `--safe-mode`, verified on claude 2.1.270; each by
a live control recorded in the register), and SHOULD state the residue
where it cannot (a harness's own bundled skills are its behaviour, like
its version). Nothing verifies this mechanically — `doctor` probes the
stanza, not the channel — and a harness upgrade re-opens the question,
which is why the ops notes re-run the controls after one. Node rules
belong in `personas/` and `spec.context`, which are hashed.

## F. Hashing, lineage and seeding (§9.2, §3)

### F1. The fingerprint part list, as composed

*Restates DEVIATIONS 2026-07-26 (item index), 2026-07-31 (argv template),
2026-08-13 (`reads`), 2026-09-09 (stanza-digest canonicalization,
`schema`), as that 2026-07-31 entry asked.* A harness node's
`fingerprint_parts` are: the rendered prompt (full pre-spill values, with
the persona body prepended when the stanza has no `persona_flag`, every
`spec.context` file fenced, the heal text, the steering block, the
contract description for `output: "json"`, the reads manifest when
`reads_manifest: "paths"` is set, and the footer), the persona body, the
argv **template** (`{prompt}`, `{phase_dir}`, `{schema}` and
`{schema_file}` left intact — the prompt is hashed once and the others are
run-specific, §7's spill-stub rule generalized; the persona path,
`readonly_argv` and `schema_argv` are appended as configured), the
resolved stanza's digest (r5 B1; canonicalized over the stanza's frozen
field set, with scheduling-only fields such as `default_retry` excluded,
DEVIATIONS 2026-09-09), ONE `reads:` part carrying every file matched by
`spec.reads` as `path|content-sha256` (per-file digests are recorded in
`hash_parts` for `explain`, never hashed separately), and the filled
`schema:` part when `schema_argv` applies. A map ITEM appends `index:i` after the executor plans. A shell
node contributes its rendered argv only. Every part is labelled in
`hash_parts` (recorded, never hashed) so `lockstep explain` can name what
moved; M3's join rule is unchanged.

### F2. Cross-lineage warm start: `run --seed <run_dir>`

*Restates DEVIATIONS 2026-08-12 (E7) and 2026-08-13 (`--force-stale`).*
Editing a flow starts a new lineage (unchanged). `--seed` removes the cost:
every node is planned normally, and a SUCCESSFUL recording in the seed run
under the identical `input_hash` is served instead of spawned. The decision
is made at plan time, so a served node sets `costs_tokens = False` and
spends no §9.5 budget. Provenance: `PhaseRecord.seeded_from`, a `kind:
"seed"` journal line per hit, a `seeded:` line in `status`.
`--force-stale <node>` declines the seed for that node and its descendants
and records `forced` — distinguishable from a hash miss. Three limits:
shell nodes are never seeded (§0.1.7), map items are never seeded (their
hash is composed after planning), failures are never served. `--seed` and
`--replay` are refused together.

### F3. A run records its root

*Restates DEVIATIONS 2026-08-16 (fleet guardrail).* `RunState.repo_root`
is the resolved `--repo-root` a run was created against — recorded, never
hashed; a child (`kind: "flow"`) run records its parent's. `resume` from a
different root refuses with exit 7, naming both paths (the M7 fingerprint
and a heal rollback would otherwise be applied to a tree the run never ran
in). Run-attach requires the recorded root to match as well as
`(flow_hash, args)`; on a mismatch `run` starts a new lineage rather than
refusing, because a fleet's newest lineage often lives in a harvested
worktree that no longer exists.

## G. Journal and process guards (§8.5, §10.3)

### G1. The journal is hash-chained

*Restates DEVIATIONS 2026-07-31.* Each `events.jsonl` line carries `h =
sha256(prev_h + "\n" + line_bytes_without_h)`. `lockstep verify-trace`
recomputes the chain and exits 5 if it is broken; lines that predate the
rule verify as UNCHAINED, not broken. The head digest is printed at run end
and `--head` pins it. This is tamper EVIDENCE, not tamper proofing: whoever
can rewrite the file can re-chain it. Readers that ignore `h` are
unaffected; the §10.3 rule that every reader tolerates one trailing partial
line stands.

### G2. The assembled command line is checked before every spawn

*Restates DEVIATIONS 2026-08-02 (`ArgvTooLong`), as ROADMAP 2026-07-28
asked.* `proc.spawn` measures the assembled argv against the platform's
limit (Windows `CreateProcess`: 32,767 characters) and refuses with
`ArgvTooLong` — an `OSError`, so it rides the executors' failed-spawn path
(exit 127) rather than crashing the run — whose message names `prompt_via =
"stdin"` as the remedy. A corrective re-spawn's prompt is several times the
original (r5 A2), so any argv-passed stanza can reach the cap; `verify
--lint` warns once per `prompt_via = "argv"` stanza that carries harness
nodes (`lint-argv-prompt`), regardless of prompt size — the cap is reached
by the corrective, not the original. A failed corrective spawn reports the SPAWN error, not the contract
error.

## H. Heal exhaustion (§9.4.7)

*Restates DEVIATIONS 2026-08-14 (`heal.on_exhausted`).* `HealSpec` gains
the optional first-class field `on_exhausted: "block" | "pass"` (default
`"block"`, §9.4.7 unchanged for every existing flow). `"pass"` accepts the
best-so-far when rounds run out: the STORED verdict is rewritten to
`accepted after N rounds without resolving: <reason>` with the unresolved
findings kept, so downstream references, `when`, `status` and the cockpit
read the truth, and the journal gains `heal-exhausted-pass`. A timeout or
malformed verdict never exhausts to pass (§9.4.3: the gate never decided).
§6 errors: `on-exhausted-with-rollback` (a gate that rolls back and then
passes accepts a tree the work is no longer in) and
`on-exhausted-without-rounds`; advisory `lint-on-exhausted-pass` names every
user. `heal_round` remains a lineage budget that never resets.

---

## Test-list deltas

None new. Every section above is pinned by the tests its DEVIATIONS entry
names (`tests/test_write_scope.py` including `TestMapScope`,
`tests/test_heal.py::test_healed_node_hash_is_stable_across_resume`,
`tests/test_r7_fixes.py`, `tests/test_interpolate.py`, `tests/test_seed.py`,
`tests/test_fleet.py`, `tests/test_trace_chain.py`, `tests/test_stanza_digest.py`),
and the shell-only replay fixture (`tests/fixtures/replay/`) is unaffected
because no hash composition moved.
