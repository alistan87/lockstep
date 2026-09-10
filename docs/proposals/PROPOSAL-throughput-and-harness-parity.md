---
type: proposal
title: "Proposal: throughput and harness parity — the digest unblock, request-thrift, and event-driven dispatch"
description: Six features from a harness-capability review (claude 2.1.228, pi 0.85.1), split by the two billing models lockstep actually runs under — token-metered claude at home, request-metered pi/Copilot at work. One structural unblock (stanza-digest canonicalization with a scheduling-field carve-out), two config-only adoptions, schema pass-through where the harness supports it and deletion-only JSON repair where it does not, and a pi-stream result channel. Readonly shell nodes were withdrawn to deferred by adversarial review; event-driven dispatch was deferred at adoption on structural evidence; six deferred items carry recorded triggers.
resource: docs/proposals/PROPOSAL-throughput-and-harness-parity.md
status: adopted (BUILT 2026-09-09; E deferred, A3 skipped)
---
# Proposal: throughput and harness parity

**Status: draft, rev 2, 2026-09-08.** Rev 1 was written after a
capability review of the installed harnesses (claude 2.1.228, pi 0.85.1,
aider 0.86.2; codex and copilot-cli not installed on this machine) against
the engine as of 0.12.0. Rev 2 is rev 1 after one adversarial round — two
independent reviews with full repo access, prompted to refute: one against
the spec, frozen surfaces, and recorded decisions; one against the
engine's concurrency, the billing claims, and the cockpit honesty rules.
**Seventeen findings survived verification** (three blockers). §11 records
each finding, what it changed, and the claims that were attacked and
held. The largest outcomes: **Feature F (readonly shell nodes) is
withdrawn to deferred** — both reviews broke it independently; **C2's
repair is deletion-only** — the drafted bracket-closure step could pass a
truncated review as a clean one; **A2's retry key leaves the digest
entirely** — hashing a scheduling-only knob is the spurious-invalidation
class r5 B1 exists to kill. Nothing was implemented at rev 2.

**Rev 3, 2026-09-09: adopted for direct build, with Feature E deferred.**
The deferral evidence is structural: wall clock only beats
Σ(slowest-per-layer) when some node in layer L does not depend on all of
layer L−1, and a check across all 40 shipped flows found that shape in
exactly two (`webapp-local`'s save nodes; `pi-guard-smoke`'s
`guard-gate`). The flagships are fan-out→join, where the barrier IS the
dependency edge — no dispatch strategy beats a join. With E deferred,
A2's retry tier is the only frozen-surface change left that could have
been an r7 amendment, so the amendment question collapses to DEVIATIONS
entries. A3 is skipped until a composing client exists locally. Build
order: A1+A2 → C2+C3 → G1/G2 → baseline run → B1/B2 → D → C1.
**Built 2026-09-09**, full suite + torture + replay green after every
step; the replay fixture passed un-re-recorded throughout (A1's
byte-identity and C1's additivity, proven on a real recording). Left with
the operator: port B1/B2 and `default_retry` to the live lockstep.toml
AFTER a baseline run records the `cache:` line; re-run `doctor` after
flipping; enable `schema_argv` per its example-file note when a
doctor-probed build warrants it.

**Scope:** `src/lockstep/` (registry, harness executor, flow executor,
roles, state), `contrib/cost_report.py`, `contrib/mission_view.py`,
`lockstep.toml.example`, `docs/`, `tests/`. No `format_version` change. No
new runtime dependency (pydantic stays the only one). Frozen surfaces
touched: hash composition (M3) in three places, r5 B2's retry resolution
order, and SPEC §9.1's stated scheduling mechanism — each accounted in §9.

---

## 1. Why now, and the fact that reshapes it

The review found the harnesses' genuinely adoptable capabilities cluster
in two failure modes lockstep pays for today: **the driver does not tell
the harness what it already knows** (the resolved contract → corrective
re-spawns), and **the driver waits when it does not have to** (the wave
barrier).

The reshaping fact: **the work repo's primary harness is pi over GitHub
Copilot, which is request-metered** — the scarce resource there is
premium requests, not tokens. The home machine runs claude under a
subscription, where the meaningful units are usage-limit headroom and
latency (dollars are notional under subscription billing, as every cost
surface already prints). One proposal, two economies:

| Feature | claude at home (subscription) | pi/Copilot at work (request-metered) |
|---|---|---|
| B1 cache-section flag | cache-hit rate → latency and usage-limit headroom | n/a (claude-only flag) |
| B2 fallback model | degrades instead of 3-min retry wait | n/a (pi has no such flag) |
| C1 schema pass-through | kills the ~2.5× corrective re-spawn | n/a — **pi 0.85.1 has no schema flag** (probed) |
| C2 deletion-only JSON repair | saves the re-spawn's tokens | **saves a full metered request** — the headline win at work |
| D pi-stream result channel | n/a | telemetry on every reviewer; structural result extraction |
| E event-driven dispatch | wall clock | wall clock, and worth more: pi RTT here is ~90 s/turn |
| G telemetry lines | cache-read % (proves B1 and the deferred reorder) | usage-message counts under their honest name (§8) |

Verification notes behind that table: pi 0.85.1's full flag list was
probed on 2026-09-08 — no `--output-schema`/`--json-schema`, no fallback
flag, no prompt-section flag; it does have `--thinking <level>`, which is
already reachable today as plain stanza argv and needs no feature (a
`pi-mechanical` stanza with `--thinking off` is config an operator can
write now). claude 2.1.228 has `--json-schema <schema>`,
`--exclude-dynamic-system-prompt-sections`, and `--fallback-model
<models>` (probed via `--help` by the author and independently by review;
runtime behaviour is doctor's job, per A1's rule that flags drift).

## 2. Feature A — stanza-digest canonicalization (the unblock)

Three recorded roadmap items and two features in this proposal dead-end
on the same sentence in ROADMAP-NOTES (2026-08-15, twice):
`stanza_digest` hashes `model_dump()` of an `extra="forbid"` model, so
**adding any field to `ExecutorStanza` changes every stanza's digest**
and re-bills every cached harness node on upgrade, whether or not the
stanza uses the field.

**A1 — frozen-field canonicalization with a scheduling carve-out.**
`stanza_digest` serializes:

- the **v1 field set** (`argv`, `prompt_via`, `json_field`,
  `persona_flag`, `readonly_argv`) **always**, defaults included —
  byte-identical to today's canonical JSON for every stanza that exists
  (verified by review: today's digest is `json.dumps(model_dump(),
  sort_keys=True, ensure_ascii=False)` over exactly those five,
  `harness.py:157`, `registry.py:19-26`);
- any later **behaviour-bearing** field only when its value differs from
  its default; and
- **scheduling-only fields never** — fields that change when/whether a
  spawn retries or waits, but not what the spawn is. Node-level `retry`
  is unhashed today, r5 B3 pins the posture for diagnosis ("never
  affects scheduling, hashing, budgets"), and the persona `readonly`
  key's strip-before-hashing is the recorded precedent. The DEVIATIONS
  entry states both rules: which class a future field belongs to must be
  decided when the field is added, and a behaviour-bearing field must
  have a default whose absence-semantics equal today's behaviour.

Pinned by a test asserting the digest of every stanza in
`lockstep.toml.example` is unchanged against recorded values.

**A2 — first consumer: per-stanza `default_retry` (free, not
re-billing).** The recorded 2026-08-15 item ships with A1 as its proof:
`default_retry = { max = 0 }` on the copilot-cli stanza replaces the
hand-written `"retry": {"max": 0}` on every copilot node forever.
Precedence: node `retry` > stanza `default_retry` > kind default. As a
scheduling-only field it is **excluded from the digest** — setting it
re-bills nothing, which review established is the only reading
consistent with r5 B1's purpose (§11 F-S2). Two costs stated honestly:
this **amends r5 B2's frozen resolution order** (a stanza tier did not
exist — §9 row; amendment candidate or DEVIATIONS), and it needs
plumbing — `_effective_retry` (roles.py) never sees the stanza today, so
the resolved stanza's retry travels via `work.meta` from plan time.

**A3 — narrow the flow-node config digest, transitively.** The
2026-08-15 chronicle note: `FlowExecutor.plan()` folds the whole-file
`config_digest` where a harness node folds only its stanza. Narrow it to
the union of referenced stanzas (+ the `default` key when relied on) —
computed **transitively over nested flow nodes** (flows compose to
`MAX_DEPTH = 5`; a non-transitive union would let a grandchild's stanza
edit serve the parent a stale result — review finding F-S4, a
correctness break in the rev 1 shape). The migration cost, stated at its
true size: a flow node's moved hash starts a **fresh child lineage**
(the child dir is keyed by the parent's `input_hash`), so **every
composed child re-runs from scratch once** and `--seed` cannot bridge
it. Pay it deliberately, in a quiet week, with `explain` naming the part
— or skip A3 until a composing client exists locally; A1/A2 do not
depend on it.

## 3. Feature B — config-only adoptions (no engine change)

Ship as edits to `lockstep.toml.example` plus a doctor probe note; the
operator ports them to the live gitignored `lockstep.toml` by hand, per
the existing rule that the example never updates the real file.

**B1 —** `--exclude-dynamic-system-prompt-sections` on the claude
stanza. Moves per-machine system-prompt sections (cwd, env, git status)
into the first user message so the cached prefix survives across spawns
— lockstep's exact access pattern, N stateless spawns against one repo.
Only applies with the default system prompt (true for these stanzas).
Re-bills claude nodes once (argv is hashed, by design). The claim G1
must prove is **cache-hit rate**, and the benefit is latency and
usage-limit headroom — not dollars, which are notional under
subscription billing (review finding F-E7).

**B2 —** `--fallback-model` on a **new, separately named** claude stanza
(`claude-code-resilient`), not the default one. The r5 B2 retry posture
stays the default; a node whose author prefers "a weaker answer now"
over "the right answer in three minutes" opts in per node via
`spec.executor`. Not on by default because a silent model downgrade
changes what the node is; the envelope's `modelUsage` is how cost views
keep it honest.

## 4. Feature C — schema pass-through where possible, repair where not

The corrective re-spawn (r5 A2) is the engine's most expensive recovery:
original prompt + fenced invalid output, roughly 2.5× the node's tokens
— and on Copilot, **a second metered request**. Three moves, in
preference order; each lower one fires only when the one above cannot.

**C1 — schema pass-through (claude; codex when configured).** New
optional stanza key `schema_argv` — an argv fragment template appended
when the node has `output: "json"` and a resolvable contract, with
`{schema}` filled from the resolved contract. Two corrections from
review:

- `{schema}` is the schema of the **contract**, not the model: for a
  `Name[]` contract it is `{"type": "array", "items":
  model_json_schema()}` — the rev 1 shape would have schema-constrained
  the harness to a single object while the validator demanded an array,
  *guaranteeing* the corrective re-spawn C1 exists to kill, on the
  flagship reviewer nodes (`Finding[]` throughout audit-spec and
  adjudicated-review). A test pins an array-contract node (F-S5).
- The filled schema text is its **own fingerprint part**
  (`schema:<compact-json>`). Neither of rev 1's cited mechanisms hashed
  it: `hash_detail` is recorded-never-hashed, and the stanza digest
  covers the template, not the filled value — while the hashed
  `describe_contract` prose is lossy (a `Field` constraint edit changes
  the argv without changing the prose). When `model_json_schema()`
  raises, skip the flag AND the part, symmetrically with the existing
  contract-description block (F-S6).

Nodes on stanzas without `schema_argv` are untouched. Claude shape:
`schema_argv = ["--json-schema", "{schema}"]`; codex's `--output-schema`
takes a file path, so the template also accepts `{schema_file}`, written
to the phase dir. `verify --lint` warns when schema-bearing argv rides
`prompt_via = "argv"` near the Windows 32k cap (the ArgvTooLong family).

**C2 — deletion-only JSON repair before the re-spawn (all harnesses).**
When contract validation fails on a harness node, before spending the
corrective re-spawn, run a deterministic in-house repair pass (no new
dependency) that may only **delete**: strip markdown fences (exists),
take the longest `raw_decode`-complete value, drop trailing garbage
after an already-complete value, remove a dangling comma before an
**existing** closer. It must **never synthesize closing tokens** — the
rev 1 draft's bracket-closure step would validate a stream truncated at
`[{f1},{f2},` as a two-finding review with the third finding (possibly
the blocker) silently dropped, and truncation just after `[` as a
*clean* review: shape-only validation accepts any-length lists, which is
the chronicle-forensics `[]`-salvage failure reintroduced with driver
blessing (F-E2, blocker). Truncated output falls through to C3, where
the model completes its own prefix.

If the repaired text validates, accept it under three honesty
obligations (F-E3, F-S7):

- **Rotate the invalid result file first.** On the file channel the raw
  bytes exist only in `result.json`, and recording repaired bytes over
  them destroys the evidence (rotation normally happens at the *next*
  execute, which repair's whole point is to avoid). The `kind:"repair"`
  journal event names the rotated path and what the pass deleted.
- **`repaired: true` on the PhaseRecord**, surfaced by `status` and the
  mission drawer — a journal-only marker is silent at every surface
  that quotes the result, and the human deciding from
  `approval-evidence.txt` must be able to see the bytes were touched.
- **Never for gate results.** A verdict is the one result whose
  consumers act without a human re-reading raw bytes; a gate's
  validation failure goes straight to C3/re-spawn. Shell nodes keep A4
  semantics (deterministic output, terminal on mismatch) — repair is
  for harness nodes only.

One DEVIATIONS entry: the repair step ahead of r5 A2's
validate→re-spawn two-step (review confirmed no spec sentence forbids
driver-side salvage — §8.3 already extracts and unwraps, and the E2
fence salvage is logged precedent).

**C3 — fix the corrective fence (the chronicle-forensics candidate,
2026-08-15, verbatim from ROADMAP-NOTES).** When the salvaged value
validates as nothing against the contract but raw stdout contains a
longer near-object, embed the longest decode prefix (capped at
`max_interp_chars`, §7 fencing respected) in the corrective fence
instead of the salvaged inner rubble — so the model corrects instead of
re-deriving. C2's machinery computes that prefix anyway; C3 is its reuse
on the path where repair failed or was refused.

## 5. Feature D — the pi-stream result channel

`lockstep.toml.example` states the trade today, verbatim: readonly pi
nodes cannot use `--mode json` (the stream would be read as the answer),
so every reviewer reports "no envelope" — *"until then, telemetry on
reviewers costs correctness, and correctness wins."* This feature is the
"until then."

New optional stanza key `envelope = "pi-stream"` (default absent =
current behaviour; mutually exclusive with `json_field` — verify error
otherwise). When set, the driver parses stdout as pi's JSONL event
stream. The parser exists on the cost path (`cost_report.py
pi_stream_usage`, probed against pi 0.83.0) and moves into the driver;
contrib imports it from there, keeping its existing try/except
standalone fallback so a cockpit copied without the driver degrades per
the missing-part honesty rule instead of crashing (the import direction
already runs that way: cost_report imports `extract_last_json` today).

Semantics, pinned by review (F-E6):

- `envelope = "pi-stream"` redefines **only the §8.3 stdout-fallback
  leg**. The file channel still wins: a writer's `result.json` is never
  shadowed by its stream chatter.
- The result text is the concatenation of **text-typed content blocks**
  of the last assistant `message_end` event — thinking blocks excluded,
  or a reviewer's chain of thought becomes its verdict.
- A stream that settles with tool activity and **no** assistant
  `message_end` (observed; documented in the cost parser) is a **named
  error** ("stream ended with no assistant text"), not a silent empty
  result — loud, like `readonly-unenforced`.
- `diagnose_provider_error` reads the stream's error events on the same
  path.

Payoff: `pi-review` gains `--mode json` back — readonly reviewers get
usage envelopes, tool counts, and model IDs in every cost surface,
ending the named honesty gap for the one harness where it was a
self-imposed trade; and result extraction becomes structural instead of
`extract_last_json`'s last-balanced-value scan. Doctor must probe the
stanza shape including the no-final-message edge; the stream shape is
pi's, not ours, so the re-probe-after-upgrade rule applies with teeth.
Blocked on A1 (new stanza field).

## 6. Feature E — event-driven dispatch: deferred at adoption

**Deferred at adoption (2026-09-09), before any code.** The design below
survived adversarial review and stands as written for when the trigger
fires; what it lacked was a payoff in this repo. A structural check
across the 40 shipped flows found exactly two whose graphs have any node
that could start before its wave's barrier
(`flows/demo/webapp-local.tg.json` — `save-ui`, `save-page`;
`flows/starter/pi-guard-smoke.tg.json` — `guard-gate`). The flagship
flows (audit-spec, adjudicated-review, release-cut) are fan-out→join,
and a join waits for everyone under any dispatch strategy. Meanwhile E
is the proposal's largest single change, costs a SPEC §9.1 accounting,
and must serialize around heal-armed gates anyway — 17 of 34 flows
carry heal. Trigger in §10; the design that fires then is this one.


`roles.py` dispatches in waves and `futures_wait(futures)` is a full
barrier: a 20-second gate whose deps finished early waits behind the
slowest node in its layer. Wall clock today is Σ(slowest-per-layer), not
the critical path — and at pi's ~90 s/turn the gaps are minutes.

**Spec accounting first:** §9.1 says "Topological layers." The layer is
the *stated mechanism*, so this is an amendment-or-DEVIATIONS call, not
a free patch — same class as the lesson-20 snapshot question. The
*observable* contract this proposal treats as frozen and preserves:
topological order, exclusive-token serialization, `--max-workers`
bounding, budget trip = no new spawns with in-flight work finishing,
approval/steer checkpoint semantics, and heal-cascade correctness.

**Design.** Replace the barrier with a completion loop:

- Dispatch everything currently ready (today's wave computation, reused
  as-is — review confirmed the ready-set predicate is state-pure), then
  `wait(..., FIRST_COMPLETED)`.
- On each completion: check wall/budget/abort flags (identical
  semantics, now per-event), re-run `_settle()`, dispatch the newly
  ready set, loop.
- **Heal-armed suspension** (reshaped by F-E1, blocker). Rev 1's drain
  flag — stop dispatching once a block verdict is seen — was **not**
  equivalent to the barrier: between a heal-armed gate's dispatch and
  its outcome processing, newly ready nodes would launch into a tree
  the cascade is about to roll back. Concretely: a writer dispatched in
  that window finishes `done`, the rollback reverts its writes but the
  invalidation set covers only heal-target descendants, so its outputs
  are silently orphaned — the §9.4.5 failure, whose exposure today is
  deliberately bounded to same-wave diamond siblings; and a
  target-descendant dispatched in the window spends real spawn budget
  on work the cascade immediately re-pends, so a "pure wall-clock win"
  could convert a gate block into extra billed spawns. The honest rule:
  **while any heal-capable gate is in flight, no new dispatch** —
  in-flight work finishes, the gate's outcome processes at quiescence
  exactly as the barrier provided, and dispatch resumes. Flows without
  heal-armed gates never suspend; flows with them serialize only around
  the gates, which is where today's engine was at a barrier anyway.
- Non-heal gate outcomes (pass verdicts; terminal blocks with no
  budget) need no suspension and process per-completion — review
  verified pass gates settle only after the adjudicated result is
  written, so no dependent can read a pre-adjudication verdict.

**A documentation duty E creates (F-E8):** per-completion `_settle()`
can run cache-revalidation `plan()` — which reads `spec.context` from
the live tree — while writers are in flight, on a *resumed* run whose
`needs_check` set is still draining. A correct flow is protected by its
dep edges (revalidation respects `_dep_settled`); a flow with a missing
dep edge loses the accidental protection the barrier gave it. E narrows
the engine's tolerance of undeclared dependencies; THEORY-OF-OPERATIONS
says so in one paragraph.

Composed engines (`kind: "flow"`) inherit this per engine; the shared
`RunResources` semaphore already bounds the tree and is unaffected.
Tests: the torture suite paths under the new loop, a pinned test that a
fast independent node completes while a slow sibling runs (the actual
claim), a pinned test that nothing dispatches while a heal-armed gate
is in flight, and the existing budget/abort/resume suites unchanged.

## 7. Feature F — readonly shell nodes: withdrawn to deferred

Rev 1 proposed `ShellSpec.readonly: true` ⇒ drop the `tree` token, so
probes and read-only gate bodies (pytest) fan out beside writers. Both
reviews broke it independently, and the payoff did not survive contact:

- **It reintroduces the token-less concurrent mutator class that a
  recorded, adopted decision rules out handling with detection that
  acts** (F-S1, blocker). PROPOSAL-sssf-adoptions A0c: "Detection that
  can misattribute must never be allowed to *act*," with a named
  runtime safety net — track token-less in-flight overlap; on overlap,
  report-only, no quarantine, no corrective. Rev 1 shipped an advisory
  lint where the recorded decision prescribes a runtime net: a lying
  readonly node would have its writes quarantined *out of an innocent
  scoped writer*, complete with a G1b corrective re-spawn billed to it.
- **The flagship payoff case is load-bearing serialization, not waste**
  (F-E4). `worktree_diff` and a pytest gate body read the LIVE tree; run
  beside a mid-flight writer they capture a half-written tree — a torn
  diff feeding a reviewer prompt as data the reviewer cannot know is
  torn, or phantom test failures triggering a heal rollback that
  reverts an innocent sibling. The only probe safe by construction is
  `node_diff`, which reads recorded trees — and is already fast.
- The engine change rev 1 described was partly a no-op (F-S9): nodes F
  applies to never had a scope baseline to skip.

Deferred with a two-part trigger, matching the discipline this proposal
applies to its other deferrals: (1) `kind:"timing"` evidence from a real
run showing read-only shell serialization material to wall clock, and
(2) a design that ships the A0c(ii) runtime net and restricts
concurrency to nodes whose inputs are recorded rather than live. Until
then, +0.15 s per parallel shell wave (the sssf measurement) is the
price of attribution that stays true.

## 8. Feature G — the telemetry lines that prove the rest

- **G1 — cache-efficiency line**: `cache: 84% read (12.3M read / 2.4M
  written)` in `mission_view.cost_lines` and `cost_report.py`, from the
  `cache_read_tokens` / `cache_write_tokens` fields the cost path
  already parses. This is how B1 (and the deferred prompt reorder)
  become measurable claims. What it measures is stated as cache-hit
  rate — latency and usage-limit headroom under subscription, real
  dollars only where billing is metered (F-E7). Honesty rule as
  everywhere: absent fields print as absent, never as 0%.
- **G2 — usage-message counts under their honest name** (reshaped by
  F-E5/F-S8, which caught both a wrong source and a conflated unit).
  The pi count is `pi_stream_usage`'s per-node count of usage-bearing
  assistant messages; the claude count is the envelope's `num_turns`
  (`envelope_turns`). **Neither is Copilot's billed premium-request
  unit** — an agentic session bills per user prompt with a model
  multiplier, and the cost path's own doctrine refuses confident wrong
  numbers. So the line is labelled what it is ("assistant messages
  reporting usage"), documented as the *correlate* the operator lines
  up against the Copilot dashboard, never as "requests." A per-node
  count that MOVES with retries and agent-loop length is still the
  right early-warning signal for request-metered work; it just does
  not get to claim the meter.

## 9. Frozen-surface accounting (complete list)

| Change | Surface | Instrument |
|---|---|---|
| A1 canonicalization + scheduling carve-out | M3 via r5 B1 stanza digest | DEVIATIONS entry (frozen-field + field-class rules); recorded-digest test proves no re-bill |
| A2 stanza `default_retry` | r5 B2's frozen resolution order (adds a stanza tier) | DEVIATIONS entry (rev 3 adopted call — no r7); plumbing via `work.meta` |
| A3 flow digest narrowing (transitive) | M3 (flow-node hash part) | DEVIATIONS entry; cost stated as full one-time re-run of composed children |
| B1/B2/C1 argv edits | stanza digest (by design) | none needed — argv is *supposed* to re-bill |
| C1 schema fingerprint part | M3 (additive part, absent = today's bytes) | DEVIATIONS entry; additivity pinned like `reads:` |
| C2 repair step | r5 A2's validate→re-spawn two-step | DEVIATIONS entry; rotation + `repaired` record + journal event |
| E dispatch (**deferred at adoption**) | SPEC §9.1 "Topological layers" | none until the §10 trigger fires; then amendment-or-DEVIATIONS, observable contract pinned by tests |
| D envelope key | §8.2/§8.3 result channels (stdout leg only) | additive stanza key; doc note; doctor probe incl. the no-final-message edge |

Not touched: exit codes, `format_version`, §7 fencing/footer, seed/replay
limits, adoption semantics, A0c's shell-token decision (F's withdrawal
leaves it standing).

## 10. Deferred, with triggers

- **Event-driven dispatch (Feature E)** — §6, design intact and
  review-hardened. Trigger: `kind:"timing"` evidence from a real run
  showing a material layer-boundary gap AND a flow whose graph has the
  early-ready shape (a node in layer L that does not depend on all of
  layer L−1) on its critical path. The instrument exists today (the
  engine's `kind:"timing"` journal lines), so the trigger is reachable
  without new code.
- **Readonly shell nodes** — §7. Trigger: timing evidence AND an
  A0c(ii)-compliant design.
- **Scoped-writer concurrency** (disjoint `spec.writes` ⇒ per-scope
  tokens instead of `tree`). Needs §9.4.2 baseline redesign, a static
  glob-disjointness check in verify, quarantine narrowing — and it
  inherits F's torn-read lesson: readers of the live tree must be
  ordered even when writers are disjoint. Trigger: a real flow whose
  wall clock is dominated by provably disjoint writers.
- **Prompt part reorder** (static context before volatile task, for
  cache prefix reuse across heal rounds). M3 re-bill of everything
  once; worth it only where G1 shows a low read-% on flows with large
  `spec.context` and heal. Trigger: G1 data, not intuition.
- **Snapshot short-circuit** (stat-fingerprint of dirty paths
  before/after; reuse the baseline ref when nothing moved). Same
  stat-cache trust `dirty_paths()` already extends; §9.4.2 states the
  fresh-index mechanism, so amendment-or-DEVIATIONS. Trigger:
  `kind:"timing"` lines showing snapshot time material in a real run.
- **Context slices** (`spec.context` entry addressing a heading/line
  range, hashing the slice). Additive grammar, but it changes what §7
  fences and invites staleness-by-line-drift; needs its own design
  note. Trigger: a flow whose prompts are dominated by whole-file
  context the task uses a fraction of — audit-spec is the live
  candidate.

## 11. Adversarial review, round 1 (2026-09-08)

Two independent reviews with full repo access, prompted to refute rev 1.
Findings are numbered by lens (F-S* spec/frozen-surfaces/recorded
decisions, F-E* engine/billing/honesty); severity as the reviewer
assigned it; every finding below survived the reviewer's own
verification with file:line evidence, and each names what it changed.

**Blockers — all three changed the proposal's shape:**

- **F-E1** — rev 1's drain flag was not barrier-equivalent: nodes
  dispatched between a heal-armed gate's block and the cascade would be
  orphaned by the rollback (the §9.4.5 failure, today deliberately
  bounded to same-wave diamonds) or spend real budget on
  immediately-re-pended work. → §6's heal-armed suspension; the
  "preserve exactly" claim deleted.
- **F-E2** — the bracket-closure repair step validates truncated output
  against every list-shaped contract (a review truncated at two of
  three findings repairs to a *valid* two-finding review; truncation
  after `[` repairs to a clean one). → C2 is deletion-only; synthesis
  falls to C3.
- **F-S1** — F reintroduced the token-less mutator class against the
  adopted A0c decision ("detection that can misattribute must never be
  allowed to act"), shipping an advisory lint where the recorded
  decision prescribes a runtime report-only net; misattribution
  triggers quarantine + a billed G1b corrective against an innocent
  writer. → F withdrawn to deferred (with F-E4: the pytest payoff case
  is load-bearing serialization — torn reads feeding reviewer prompts —
  and F-S9: part of the described engine change was a no-op).

**Majors:**

- **F-S2** — hashing `default_retry` re-bills on a knob that never
  reaches the spawn: the exact spurious-invalidation class r5 B1
  eliminates (node `retry` is unhashed today; the persona-`readonly`
  strip is precedent). → A1's scheduling-field carve-out; A2 is free.
- **F-S4** — a non-transitive stanza union lets a grandchild's stanza
  edit serve the parent stale results; and the migration restarts child
  lineages wholesale (child dirs are keyed by parent hash), not "one
  re-bill." → A3 transitive + honest cost + explicitly optional.
- **F-S5** — `{schema}` from `model_json_schema()` alone is wrong for
  every `[]` contract — it would *guarantee* corrective re-spawns on
  the flagship reviewer nodes. → the array wrapper + pinned test.
- **F-S6** — rev 1's schema hashing story cited two mechanisms that
  do not hash the filled schema (`hash_detail` is recorded-never-hashed;
  the stanza digest covers the template); the lossy prose proxy would
  serve cached results across a constraint change. → the `schema:`
  fingerprint part with symmetric skip.
- **F-S7 / F-E3** — C2's evidence and honesty story failed twice: on
  the file channel the raw bytes live in `result.json` and recording
  repaired bytes clobbers them (no second execute = no rotation); and a
  journal-only marker is silent at every surface that quotes the result,
  including `approval-evidence.txt`. → rotation-first, `repaired` on
  the PhaseRecord surfaced in status/mission, and no repair for gates.
- **F-E4** — see F-S1 above (folded into F's withdrawal).
- **F-E5 / F-S8** — G2 named a function that does not hold the number
  (`num_turns` is the *claude* envelope's; `pi_stream_tools` counts tool
  events) and conflated assistant turns with Copilot's billed unit —
  the confident-wrong-number failure the cost path exists to avoid. →
  §8's honest-name reshape.
- **F-E6** — D was underspecified on three verified edges: streams that
  settle with no assistant `message_end` (observed), thinking-block
  interleaving, and §8.3 file-channel precedence for writers. → §5's
  pinned semantics.

**Minors:**

- **F-S3** — A2 amends r5 B2's frozen resolution order and rev 1's
  "complete" §9 table had no row for it. → row added; plumbing named.
- **F-E7** — "saves cache writes" is a dollar claim in an economy the
  repo prints as notional. → B1/G1 restated as cache-hit rate →
  latency/headroom.
- **F-E8** — per-completion revalidation on a resumed run can read the
  live tree beside in-flight writers; correct flows are dep-edge
  protected, but E narrows tolerance of undeclared deps. → §6's
  documentation duty.
- **F-S9** — folded into F's withdrawal.

**Attacked and held (both reviewers, deduplicated):** A1's
byte-identity claim (today's digest reproduced exactly; §12's "provably
not fixture-affecting" stands); E's remaining barrier dependencies (pass
gates settle post-adjudication; blocking gates hold `running` so
dependents cannot dispatch early; steer is per-spawn; baselines precede
the loop; budget/abort strictly more frequent; map items inside one
future; store/journal writes lock-guarded; no renderer assumes runtime
wave boundaries); C2's legality (§8.3 already extracts/unwraps; the E2
fence salvage is logged precedent; repaired-validated text is coherent
downstream through interpolation, replay/seed, and the kind-agnostic
journal chain); C1's premise that every resolvable contract is a
pydantic model (`resolve_contract` rejects anything else — including all
of `flows/factory_contracts.py`); A3's premise that the flow executor
loads the child at plan time, and that `estimate` matching survives
hash moves; D's import direction (contrib already imports from the
driver, with a standalone fallback worth keeping); the billing table's
flag facts (re-probed independently); and the proposal's
recorded-decision hygiene elsewhere (E8-full, V1 hold-at-lint, seed
limits untouched; C3 verbatim from ROADMAP-NOTES).

## 12. Sequencing

Adopted order (rev 3). It differs from rev 2's in two ways: B1 moves
BEHIND its instrument — B1's whole claim is a cache-hit-rate delta, so
G1 must land and a baseline run exist before the flag flips, or there is
nothing to compare against — and the request-metered wins (C2, D) move
ahead of the claude-only ones.

1. **A1 + A2** (the unblock and its free first consumer) — everything
   with a stanza key waits on this.
2. **C2 + C3** (deletion-only repair + the fence fix; the
   request-thrift win at work) — engine-only, no stanza key,
   independent of A.
3. **G1 + G2** (the instruments), then a **baseline run**, then **B1**
   (and B2's opt-in stanza) — measure before flipping the flag.
4. **D** (pi-stream envelope) — consumes A1; the other request-metered
   win, and it closes the example file's named honesty gap.
5. **C1** (schema pass-through) — consumes A1; claude-only.
6. **A3** (flow digest) — skipped until a composing client exists
   locally; if taken later, schedule the child-lineage re-run
   deliberately. **E** — deferred, §10.

Full pytest after every step; re-record the replay fixture only if a
deliberate hash-composition change lands (A3 and C1's `schema:` part
qualify only for flows that use them — additivity keeps everything else
byte-identical; A1 is provably not one).
