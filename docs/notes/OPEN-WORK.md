---
type: notes
title: Open work, evaluated and ranked
resource: docs/notes/OPEN-WORK.md
status: current
---
# Open work, evaluated and ranked (2026-09-19)

> **Progress:** items 1, 2 and 4, then 3, 5 and 7, were built the same day
> and shipped in **0.17.0**; their rows are marked BUILT and kept for the
> record. Item 6 (persona composition) stays parked on its trigger, so the
> ranked work still open starts at item 9.
>
> **Item 8 and item 14's named first step were built 2026-09-20** (DEVIATIONS
> 2026-09-20): `gc` and `explain --graph` say "root gone", and the engine
> journals `op: "dispatch-wait"` so the event-driven dispatch trigger can be
> read from a real run. Item 14 itself stays deferred; its trigger is now
> observable, which it was not.
>
> **Item 24 was added 2026-09-20, after 0.17.0 was tagged** — found by cutting it.
> The ledger was 23 items at the release, which is what the CHANGELOG says; it is
> 24 now. New items append rather than renumber, so a row's number stays citable.

**What this is.** The actionable open items recorded across
`ROADMAP-NOTES.md`, `LESSONS-TO-MECHANISMS.md`, the proposals index, and
the deferral sections of the adopted proposals, plus the findings of the
2026-09-19 review of the principles. Each row says what the change buys in
plain words, what it costs, and why it sits where it sits. A second
section lists the recorded seams that are NOT ranked, so the omission is
visible. Nothing here binds; the owner decisions recorded in
`ROADMAP-NOTES.md` (E8-full waits for evidence, V1 stays a lint) are
restated, not overturned.

**How the ranking was made.** Four questions, in order: is there evidence
in hand or only reasoning; does it cut tokens, heal rounds, or wall clock
on runs that actually happen; does it touch a frozen surface; how much
work is it. The bar applies to the review's own new items exactly as it
applies to the recorded deferrals: a new idea with no measurement behind
it goes in the tier for reasoning-only items with a named trigger, not at
the top because it is new.

**Review record.** Two adversarial passes ran against the first draft
(2026-09-19): a fact-check with file:line evidence and a judgment attack
using the repo's own recorded principles. Twenty-two findings; every
confirmed one is folded in below. The largest changes: the seeded temp
index and the prompt reorder dropped from "do next" to the deferred tail,
because the timing and cache instruments already have data and it points
the other way; the instruction channel became a persona-composition item
with a trigger, because the reliability claim had no local evidence; the
claude-code item was rescoped from "run a control" to "close the channel
in argv" after `claude --help` on this machine showed flags for it; the
r7 item was re-priced as a delta amendment, not a consolidation; and a
dozen recorded seams the first draft missed are listed in the second
section.

## Tier 1: evidence in hand, or already accepted

| # | Item | Benefit, in one sentence | Cost and risk | Verdict |
|---|---|---|---|---|
| 1 | **BUILT 2026-09-19 — Close the claude-code unhashed instruction channel in argv** (this review; DEVIATIONS 2026-09-19, r7 E5: `--safe-mode`, five live controls, doctor-probed) | The stanza's own comment concedes that every claude node carries a repo-root `CLAUDE.md`, and this repo's five `.claude/skills` are exposed the same way; closing it in argv makes `explain`, `--seed`, and `--replay` truthful for claude nodes the way the pi flags already do. | `claude --help` on this machine lists `--safe-mode` (all customizations off, including CLAUDE.md and skills), `--disable-slash-commands` (skills only), and `--setting-sources`; `--bare` is the known non-starter because it restricts auth. Needs a live control per flag, as the pi flags got (DEVIATIONS 2026-08-14), then the example stanza, DEVIATIONS, and a one-time re-bill of claude nodes through the stanza digest. Risk: a flag that also drops something the node needs, which the control is for. | **Do first.** The recorded rule for this class is "restriction goes in argv", not a study; the first draft's "no code unless the answer is yes" was the wrong framing. CLAUDE.md is the larger and already-confirmed channel; skills settle as a side effect of the same control. |
| 2 | **BUILT 2026-09-19 — The r7 delta amendment** (`docs/spec/AMENDMENTS-r7.md`, adopted; sections D–H, each naming the entry it restates) | DEVIATIONS mentions r7 nine times and "amendment" fourteen; ROADMAP names three more; every future change stops re-deriving which stated text the code departs from. | An additive file like r4, r5, r6, never a consolidation: `flows/audit-spec.tg.json` hard-codes the layered form, `flows/selftest-replay.tg.json` checks SPEC.md by section heading (a heading move fails the shell-only fixture and forces a re-record), and DEVIATIONS is the audit gate's what-why-when trail, not a tax to fold away. A day or two for the delta. Stated guarantees are a stop-and-ask surface, so the amendment restates, never rewrites. | **Do soon.** Candidates already named in the record: the argv-length rule, raw strings in shell argv, heal text and steering stated together, the readonly footer, §9.2's fingerprint part list and attach/resume `repo_root` text, the `--seed` limits and cross-lineage caching, §10.3's telemetry rule, resume-vs-archived-flow wording. |
| 3 | **BUILT 2026-09-19 — The remaining S2, S3, and S4 views** (DEVIATIONS 2026-09-19: conditions line, finding trajectory, per-attempt tools) | The board shows blocking conditions beside the ledger counts without inventing a severity; the drawer shows finding identity across attempts (S3's view half) and per-attempt tool activity. | Contrib and tests only. The attempt journal carries cause and ordinal, never tool activity; per-attempt tools come from `cost_report`'s `attempts_detail` (pi streams only) and are simply not rendered. Medium effort in the render layer. | **Do soon.** Accepted work ranks above unaccepted ideas by the proposals index's own rule that authority comes from adoption. Nothing is wrong without it, which is why it is not first. |
| 4 | **BUILT 2026-09-19 — Per-item baselines and write scopes for maps** (ROADMAP 2026-08-12; DEVIATIONS 2026-09-19, r7 D4; `TestMapScope`) | Closes the one mutator class quarantine cannot guard; the work repo's codemod-apply map is a live unscoped instance. | Write-capable items already serialize on the `tree` token, so a baseline per item fits inside the existing lock in `_run_map`; the first draft's "serialized maps only" restricts nothing. Undercounted in the first draft: the G1b scope corrective lives only in the single-node path, `_writes_of` needs an item context, and `write-scope-on-map` flips from error to legal. No local run has ever journaled a `quarantined` event, so the gap is structural, not observed. | **Worth doing.** Reliability, not speed. Structural but real. |
| 5 | **BUILT 2026-09-19 — The runs root as config** (DEVIATIONS 2026-09-19: `[driver] runs_dir`, one rule for driver and cockpit; the default stayed `./runs`, the example recommends the outside-tree shape) | Fleet commands stop repeating `--runs-dir`; a run dir outside the tree retires the M7 warning class the ops note describes (an un-ignored run dir makes every resume warn about its own `state.json`), and read-tool agents stop browsing prior runs' prompts and diffs. Lanes already run this shape, so the engine path is exercised. | Not small: the `runs` default is hard-coded in at least twelve files across `cli.py`, `contrib/`, and the PowerShell panes, and a `[driver] runs_dir` read by contrib needs `tomllib`, whose absence is already a documented degraded case. One deliberate decision on flag-vs-config precedence. Overstated in the first draft: a writing node is handed its phase dir path in the footer and in `LOCKSTEP_PHASE_DIR`, so only browse-by-accident discovery closes. | **Worth doing** as one change, with the cost stated honestly. |

## Tier 2: reasoning-only, with the trigger named

| # | Item | Benefit, in one sentence | Cost and risk | Verdict and trigger |
|---|---|---|---|---|
| 6 | **Persona composition: `spec.persona` takes a list** (this review, rescoped) | A node can wear a role persona plus one or more domain-method files, hashed and outside the data fence, so domain skills have a sanctioned instruction channel without a third mechanism. | Widen `str` to `str \| list[str]`; each body its own hash part so `explain` names the file. Never interpolated: an argument landing outside the fence breaks the §7 boundary, and persona files are read raw today, which is the property to keep. Precedent for an additive spec key is `spec.writes` and `spec.reads`, not `heal.on_exhausted` (a first-class engine field, recorded as the exception). Cost per spawn: corrective prompts embed the original, so every instruction file counts twice against the argv cap on `prompt_via = "argv"` stanzas. | **Wait for the trigger.** The authoring guide's §6 already records the split: method in the persona, reference in `spec.context`, and warns that one shared instructions file in every spawn is "more expensive and less accurate". The first draft's claim that correctives and heal rounds would drop has no local evidence: no run has journaled a corrective, and every recorded heal round blocked on correctness, not on house rules. Trigger: a flow that needs two personas on one node, or a heal or corrective whose findings cite a rule the prompt did not carry. |
| 7 | **BUILT 2026-09-19 — Per-item seeding for maps** (DEVIATIONS 2026-09-19: `serve_item`, item-level provenance) | `--seed` serves unchanged map items across lineages, which on a chunked map is most of the bill. | The engine must hand the executor its item index at plan time; the seed decision is plan-time by design. | **Trigger arguably met.** `webapp-local` ran nine times in August, before `--seed` shipped. Build when a map-heavy flow is re-run locally on a version that has `--seed`. |
| 8 | **BUILT 2026-09-20 — `gc` and `explain --graph` for vanished worktrees** (ROADMAP 2026-08-16; DEVIATIONS 2026-09-20) | Harvested lanes stop reading as "every node moved"; `gc` says which kept runs nothing can attach to or resume. | Small: both say "root gone". `gc` names, it does not reweight — a retention rule keyed on a path check would delete on an unmounted drive, and the lineage head is still `--estimate` history. | **Built.** Said, not acted on, in both places. |
| 9 | **`stable_output` projection** (OW-07 S4, tentatively accepted, deliberately unbuilt) | `{steps.X.stable}` beside `{steps.X.output}` so a shell node's volatile stdout stops re-billing consumers. | A second interpolation and hash channel. | **Ships only if** the stable-shell-output authoring discipline proves insufficient in practice (CHANGELOG 0.12.0). No report yet. |
| 10 | **G3b per-node spawn budgets** (OW-07, deferred not declined) | A runaway node cannot starve the rest of the run. | Interacts with heal rounds, map fan-out, and correctives. | **Revisit on a second starvation report.** The global cap override covered the first. |
| 24 | **A silent shell success is scored as a resultless failure** (observed live 2026-09-20 cutting 0.17.0; `runs/release-cut-20260920T171840Z`) | A shell work node whose command succeeds without printing would stop being failed, then auto-retried into a real failure when the command is not idempotent. | `executors/shell.py` sets `result_text` only when `stdout.strip()` is truthy, and `roles.py::_finish` fails any attempt with `result_text is None` whatever the exit code. Narrowing that to "exit 0 and no result is a PASS for a work node" touches the SPEC §7/§8.3 result-channel contract and what `output: "text"` promises a consumer — a stated-guarantee surface, so stop-and-ask, not a quiet patch. It also weakens a real signal: a harness node that exits 0 having written nothing IS a failure, and the same code path carries both. | **Recorded, not scheduled.** The flow-level fix shipped instead and is the cheap one: `contrib/git_tag.py` prints, so the channel is never empty, and it is idempotent on the same commit so a retry cannot manufacture the failure. Every other shell node in the repo already prints. Trigger: a second silent-success node in a flow whose body genuinely cannot print — at which point the question is per-`kind` result-channel semantics, not a special case. |

## Tier 3: deferred by decision or by an unfired trigger

| # | Item | Benefit, in one sentence | Why it stays deferred |
|---|---|---|---|
| 11 | **Seeded temp index for snapshots** (ROADMAP 2026-08-12; throughput §10 "snapshot short-circuit") | Snapshot, rollback, and scope checks 12 to 27 times faster on two synthetic trees, byte-identical in every bench round. | The recorded trigger is `kind:"timing"` lines showing snapshot time material in a REAL run, and the instrument has data against it: across every local run the tree ops sum to seconds, and on the release-cut run the changelog node's three tree ops were 173 ms of a 375 s node. The first draft also mis-stated two things: seeding turns O(tree) into O(changed plus untracked), so a run's own untracked output is still re-hashed every time; and the soundness cost lands on the ENFORCEMENT path (quarantine and §9.4.4 rollback, the only scope enforcement on claude-code), where `dirty_paths()` trusting the stat cache is an advisory preflight. A false negative there is a stated guarantee silently missed, which is a stop-and-ask surface, not a DEVIATIONS entry. |
| 12 | **Prompt part reorder** (throughput §10) | Static context before the volatile task, for cache prefix reuse across heal rounds. | Persona is already first; the reorder is context-before-task. The trigger is a LOW cache-read share on the cost line, and the two instrumented runs read 91 and 92 percent. Evidence against, not merely absent. |
| 13 | **E8-full: narrow heal rollback to declared writes** (owner decision 2026-08-12) | An operator's out-of-band edit survives a heal round. | Amends stated §9.4.4 text. The trigger is the first real `restored-undeclared` event; it appears only in code, docs, and one test's enum. |
| 14 | **Event-driven dispatch (E)** (throughput §6, deferred at adoption) | Nodes ready before their wave finishes start earlier. | Trigger unfired. The trigger as written had a defect: it named `kind:"timing"` lines as the instrument, but those recorded tree ops only (`scope-*`, `heal-*`, `reads-hash`), so a layer-boundary gap could not be seen through them. **Instrument fixed 2026-09-20** (DEVIATIONS 2026-09-20): `op: "dispatch-wait"` per dispatched node, the dependency that made it ready named, and `status` sums it into one line. The deferral stands until a real run shows a material wait on a critical path with the early-ready shape. |
| 15 | **Scoped-writer concurrency** (throughput §10) | Provably disjoint writers run in parallel. | Baseline redesign, static glob-disjointness check, quarantine narrowing, and F's torn-read lesson. No local flow is wall-clock bound on disjoint writers. |
| 16 | **Context slices** (throughput §10) | Prompts stop carrying whole files the task uses a fraction of. | Changes what §7 fences; invites staleness by line drift. Needs a design note. Audit-spec is the named candidate. |
| 17 | **A3: narrow the flow-node config digest** (throughput, skipped) | A composed child stops re-billing when an unrelated stanza changes. | Moving the hash restarts every composed child lineage and `--seed` cannot bridge it. Waits for a composing client locally. |
| 18 | **Cross-run exclusive tokens** (ROADMAP 2026-08-16) | Fleet lanes share a resource by engine token instead of lane assignment. | A precondition, not a trigger: spec surface first (registry location, release on driver death, verify's view of cross-flow collisions). Lane assignment plus `gates.lock_held` works and is documented. |
| 19 | **V1 promotion to error** (owner decision 2026-08-12) | A mutating node without a declared scope fails verify. | Every effect lands on flows elsewhere; the mirror-migration argument holds. Revisit once the mirror is on this version. |
| 20 | **`spec.reads` ENFORCEMENT via a pi extension** (lesson 10) | A synthesis node cannot read beyond its cited evidence. | `spec.reads` itself ships as a hashed declaration; the guard extension handles writes only. Recorded: don't build until it bites again. |
| 21 | **Readonly shell nodes (F)** (withdrawn to deferred) | Probes and test gates fan out beside writers. | Both reviews broke it: token-less mutators and torn reads of the live tree. The measured price of serializing is +0.3 percent on the one shipped flow with a parallel shell wave. |
| 22 | **Unattended mode** (double-gated) | Runs proceed past intermediate human gates with deferred review. | Requires a real domain-expert cohort and an attended retrospective; neither exists. |
| 23 | **`contrib/recover_blob.py`** (lesson 22) | Lost working-tree content recovered from snapshot blobs. | A `/debug-run` paragraph covers it. Lowest. |

## Recorded seams, not ranked

These are open in the record and were missing from the first draft. They
are listed so the omission is a decision, not an oversight. None has a
trigger that has fired, and most wait on a consumer that does not exist.

- **§15 seams** (ROADMAP, owner 2026-08-10): `pyfunc` and `action` executor
  kinds, a graph-backed `Store`, `Policy` beyond the no-op. Waiting for a
  real second consumer; the rule of two applies.
- **SPEC §16.3** items ROADMAP still points at: an OTel exporter to a
  backend (`emit_span` writes files only), `--attach <run_dir>`,
  finding-scoped map heal (all items re-run today), per-node worktree
  isolation, `parallel` and `reduce` roles.
- **Re-run isolation as a spec principle** (ROADMAP 2026-07-26): prior
  attempt artifacts non-discoverable or marked non-input. Item 5 covers
  the runs-dir half; the principle belongs in item 2's amendment.
- **`Verdict.untested`** (lesson 5, a 1.1 contract field). Not in
  `contracts.py`.
- **V3 source digest and doctor integration** (LESSONS): version alone
  covers provenance today.
- **Real-time concurrent-edit detection** (LESSONS): needs an OS watcher
  and a measured budget.
- **Diagnostics export** (mission-scale): deferred, agreed; revisit after
  the bench shows which measurements are worth exporting.
- **SSSF §5 protected-path floor and §6 agent profiles**, and open
  questions O4, O6, O7. O6 (`approval-evidence.txt` is unprotectable while
  `runs/` is gitignored) is in CLAUDE.md's ops notes with "no fix today".
- **Parity tiers §1.4 race**: not built.
- **Cockpit rev7**: the monolithic-flow approval mechanic (r7 candidate),
  reserving `corrective` and `steer-consumed` event kinds against §10.1,
  the planted-defect design (deferred to first contact).
- **Mission-UX Q5** (the load-time-static rail, a recorded non-bug) and
  **Q2** (pane and TUI wiring of `blocker_summary` and `driver_presence`,
  deliberately out of scope).

## What was checked and found already built

Four notes read as open and are not. The corrective fence rubble (ROADMAP
2026-08-15) shipped as throughput-parity C2/C3 (DEVIATIONS 2026-09-09,
"longest near-object"); the ROADMAP entry now carries the marker. The
mission-page O(everything) note (2026-09-10) is marked resolved through
0.15.0. Per-stanza `default_retry` (2026-08-15) shipped 2026-09-09. Lesson
24's stranded-result race has its repro test
(`tests/test_lifecycle.py`, `test_budget_trip_never_strands_a_completed_result`),
which pins the race as nonexistent. Nothing else on this list was found to
be built under another name.
