---
type: plan
title: "Upstream response: OW-07 consumer feedback — convergent reviews, adoption, and truthful terminal state"
description: Point-by-point disposition of the two MIMIR consumer reports from the OW-07 campaign (Sol 2026-09-07, Gemini 2026-09-08) — one confirmed engine bug, four accepted features, one template programme built around a severity-adjudication node, and three declines with the shipped mechanism named in each. Every load-bearing claim was verified against the source at 0.10.0 before classification.
resource: docs/proposals/upstream-response-ow07-feedback.md
---

# Upstream response: OW-07 consumer feedback

**Responding to:** two proposals filed from the MIMIR consumer repo against
lockstep 0.10.0 (`9e353fc`):

- *"Feature request: convergent review loops, usable declared reads, and
  truthful terminal state"* (Sol, 2026-09-07) — requests **S1–S6**.
- *"Feature Request: Implementation Taskgraph Ergonomics, Scope Guarding, and
  Incremental Re-review"* (Gemini, 2026-09-08) — requests **G1–G5**.

Both reports were verified claim-by-claim against the source before anything
below was classified. They are accurate about the code with two exceptions
noted inline (S5 is behind what already ships; G4 asks for a mechanism that
already exists under another name), and the S6 bug is **worse** than reported
— see below. The reporters' own evidence discipline made this response cheap
to produce; that is worth saying to them.

Classification uses the taxonomy Sol requested: **engine bug** /
**accepted feature** / **doc-template improvement** /
**consumer-authoring responsibility**.

## Disposition at a glance

| Request | Classification | Disposition | Target |
|---|---|---|---|
| S6 — truthful preflight terminal state | **engine bug** | fix: persist a run-level terminal record; `wait`/`status`/`active`/MISSION prefer it; detach grace window | **0.10.1** |
| S5 — pause detached approvals | consumer-authoring + doc-template | the requested behaviour ships today as auto-reject → interactive resume; fix the *wording* in `wait`/`status`/MISSION, document the idiom | 0.10.1 (wording), docs now |
| G3a — budget override on resume | accepted feature | `resume --max-agent-spawns N`, journaled | 0.11.0 |
| S1 — declared-reads manifest | accepted feature (paths only) + doc | `spec.reads_manifest: "paths"`; decline `"contents"` | 0.11.0 |
| S2 + G5 — review convergence / finding lifecycle | doc-template (engine unchanged) | the **adjudicated-review programme**: reviewer → severity-adjudication node → gate, findings ledger with deterministic invariants, delta mode via existing `node_diff` | 0.11.0 |
| S4 — stable shell projection | split | consumer-authoring (deterministic gate output) now; `explain --graph` "conditionally fresh" label 0.11.0; `stable_output` projection deferred | 0.11.0 / 0.12.0 |
| S3 + G2 — adopt a human-remediated artifact | accepted feature (Sol's semantics, not Gemini's) | `lockstep adopt` — design note first, then build | design 0.11.x, ship 0.12.0 |
| G1a — advisory / secondary write scopes | **declined** | a scope you may violate is not a scope; the shipped answer is the `pi-guarded` scope guard (enforce in-session) or an honest declaration | — |
| G1b — re-spawn on scope violation | accepted feature | one corrective re-spawn mirroring the contract-violation shape | 0.12.0 |
| G3b — per-node spawn budgets | deferred | real design surface (heal rounds, maps, corrective re-spawns); revisit if starvation recurs | — |
| G4 — topology/config hash split | **declined** | this is `--seed`, which ships; weakening lineage identity solves a solved problem | docs now |

---

## S6 — detached preflight refusal reports exit 4 (engine bug, P0)

**Confirmed, with an aggravating interaction the report missed.**

The reported mechanics are exactly right: `cmd_run` writes `state.json` (all
nodes pending) before `engine.run()`; `_preflight_dirty_scope()` raises
`RunRefusal` at the top of `run()`; the handler returns exit 7
(`EXIT_CONFIG`) and releases the lock. Foreground callers see the right code
and message. But `_detach()` confirms the launch the moment the child takes
the lock — *before* the preflight runs — so the refusal goes only to the
detached log, and `cmd_wait`, reconstructing an exit purely from node
statuses, falls through to `EXIT_BUDGET` (4): *"stopped with runnable work
remaining — a plain resume continues."*

The part the report underplays: **that advice is live ammunition.** The
dirty-scope preflight is fresh-runs-only by design
(`check_dirty_scope=not resume` — E9; a resumed tree is expected dirty with
the run's own prior work). So the operator who trusts exit 4 and types
`lockstep resume` silently bypasses the exact protection that refused the
run — and if the writer node's hash missed (the OW-07 case: volatile
upstream shell output), it re-runs and legally overwrites the human-edited
file. The wrong exit code funnels the operator into the failure E9 exists to
prevent. This is why it is P0 here too, not just in the consumer's table.

**Fix plan (0.10.1):**

1. **Persist a run-level terminal record.** New optional `RunState` field
   (additive; `format_version` 1.x semantics unchanged):

   ```json
   "terminal": {
     "status": "refused",
     "exit_code": 7,
     "reason": "dirty_scope",
     "message": "uncommitted working-tree changes fall inside declared write scopes ..."
   }
   ```

   Written by the `RunRefusal` handler in `cmd_run`/`cmd_resume` before
   `release_lock`, and **cleared** by the next successful attach/resume/run
   of the same dir. Scope is refusals-post-lock only — normal terminal
   outcomes keep deriving from node records, so `cmd_wait`'s existing
   precedence (gate block > rejection > failed > done > budget) is untouched
   except for one new highest-precedence check.
2. **A refusal event** in `events.jsonl` (`{"kind": "refusal", ...}`), so the
   journal is not empty, `verify-trace` covers it, and the MISSION timeline
   has something true to render.
3. **Readers prefer the record.** `wait` exits with the recorded code (7,
   never 4, with all nodes pending); `status` and `active` print
   `refused: dirty scope` with the message; MISSION renders `refused`, not
   `waiting` — glossary entries added on all pinned surfaces
   (`mission_view.GLOSSARY` / `cockpit.ps1` / `L3_GLOSSARY` stay in step).
4. **Detach grace window.** After `await_start` confirms the lock, watch
   ~2s for an immediately-terminal child and echo the refusal into the
   launching terminal; return promptly once real node work begins.

Acceptance tests are the consumer's six, verbatim — the repro is
deterministic and model-free. Their test 6 (a child that begins work reports
as launched exactly as today) is the regression guard on the grace window.

## S5 — pause, don't reject, detached approvals (already ships; fix the wording)

The one place the report is behind the code. The requested end state —
detached run parks at the approval, a human resumes interactively and
answers exactly once — **is the current design**:

- The record already distinguishes *nobody was there* from *the human said
  no*: `"approval auto-rejected (non-TTY stdin)"` (and the Windows
  NUL-device EOF variant `"no answer available on stdin"` — the comment at
  the site reads "NOBODY WAS THERE — a different fact from 'the human said
  no'") versus `"approval rejected"`.
- Auto-reject writes no `rejection.txt` — that artifact is exclusively the
  human's verbatim words (`contrib/approve.ps1`), which is what makes it
  sticky and gc-protected.
- Approvals are never resume-skipped (SPEC §9.3): a resume from an
  interactive terminal asks for real, once. `cmd_wait`'s sticky-rejection
  handling exists precisely because rejected-then-resumed-and-approved is a
  supported, tested path.
- Exit 6 is documented as **the handoff signal**; MISSION shows `needs you`;
  `contrib/attention.ps1` fires on it.

What is genuinely improvable is presentation: an auto-reject currently
*reads* like a decision. **0.10.1, wording only:** when the blocking
approval's error says auto-rejected, `wait`/`status`/MISSION render
`awaiting a human decision — resume from a terminal to answer` instead of
leaving it visually adjacent to a real rejection. No new run status, no new
exit code (exit codes are a frozen surface, and the record already encodes
the distinction the new code would carry). A new FLOW-AUTHORING /
THEORY-OF-OPERATIONS passage documents the idiom: *detach the long work;
exit 6 with an auto-reject error is the machine asking for you; resume is
the answer path.* The consumer's split-flow workaround is unnecessary.

Declined within this item, per their own non-goals: any MISSION/API answer
path, any synthesized approval.

## G3a — `resume --max-agent-spawns N` (accepted, 0.11.0)

Confirmed: the cap lives in the flow (`budget.max_agent_spawns`),
`token_spawns` in state, and today the only way to raise it is editing the
flow → new `flow_hash` → new lineage (then `--seed` back — workable, but
three commands and a new run dir for what is one operator decision).

An explicit override at resume touches no hash and matches exit 4's
documented contract ("a plain resume continues"). Sketch:

```
lockstep resume <run_dir> --max-agent-spawns 16
```

- Engine uses the override for this drive only; the flow file is untouched.
- Journaled (`{"kind": "budget", "op": "override", "from": 8, "to": 16}`)
  and echoed by `status` — the ceiling is part of the consent story
  (`plan_card` reads the flow), so an override must leave an artifact, not
  a memory.
- Lowering the cap is allowed, including below `token_spawns` already
  spent — "stop spending" is a coherent operator intent; the engine warns
  and stops at the budget check as it always does.

**G3b (per-node spawn budgets) is deferred, not declined.** It interacts
with heal rounds, map fan-out, and corrective re-spawns; nothing in the
OW-07 evidence needed it once the global override exists. Revisit on a
second starvation report.

## S1 — declared-reads manifest (accepted: `"paths"`; declined: `"contents"`)

Confirmed: `apply_reads` feeds `fingerprint_parts`/`hash_detail` only, never
`prompt_parts`. `reads.py` says it plainly — "a precision feature, not a
correctness feature" — but the field name invites the misreading, and this
is now the second consumer surprised by it. The authoring error (a prompt
saying "read the files named in `spec.reads`") was theirs; the trap is ours.

**0.11.0:**

```json
"spec": {
  "reads": ["src/ontology/**/*.py", "docs/contract.md"],
  "reads_manifest": "paths"
}
```

- Default absent/`"none"` — current behaviour; a node without the key
  contributes nothing and hashes byte-identically (the same additivity
  discipline `reads` itself shipped under; M3 is not disturbed, pinned by
  the replay fixture passing without re-recording).
- `"paths"` appends a fenced, sorted, repo-relative resolved file list to
  the prompt. In the prompt ⇒ in the input hash, so a changed match set
  re-bills and names itself in `explain` with no extra machinery. A
  zero-match glob injects an explicit empty manifest (silence is how the
  consumer's reviewer came to block on `evidence-access`). `runs/` and
  `.git/` exclusions unchanged.
- `reads_manifest` without `reads` is a verify error — dead config must not
  hide a wrong belief about what it does (the `on_exhausted` posture).
- **`"contents"` is declined**: bounded generated context is `spec.context`'s
  job; a second channel with duplicate spill semantics is a seam we would
  service forever.

Docs get the consumer's sentence nearly verbatim, in FLOW-AUTHORING and the
`reads` section of the spec notes: *`spec.reads` is an input-hash
declaration only. It does not tell the harness which files exist and does
not grant or perform reads.*

Their five acceptance tests are adopted as written.

## S2 + G5 — review convergence: the adjudicated-review programme (templates + contracts; engine unchanged)

Both reports describe the same failure shape from different campaigns:
open-ended reviewers, each pass a fresh audit, every self-graded major
terminal at a `block_on_severity` gate — safe, expensive, non-convergent.
Sol's own evidence contains the fix: the hand-authored arbiter round
discarded unsupported findings and upheld two concrete majors, and the
campaign converged. What they ask us to build is that shape as a first-class
thing. We agree with the diagnosis and will ship it as **templates,
contracts, and a deterministic gate — not engine state**. A cross-run
findings ledger inside the engine is domain-runtime work; the build-loop
answer (per the §15 ruling) composes from parts that already exist.

**The design principle: discovery and gating must not share a mind.** A
reviewer's job is to find everything, and a mind graded on finding will
grade what it finds as major. The severity that a gate acts on must be
assigned by a node whose *only* job is adjudication.

### The severity-adjudication node

A harness node between the reviewers and the gate:

- **Input:** every reviewer's `Finding[]` verbatim; the prior-round ledger;
  the frozen scope statement (what this review is *of* — e.g. "a
  pre-implementation contract: absent implementation is not a defect");
  and the delta, from the existing **`node_diff`** probe (`tree_before`/
  `tree_after` of the writer node — the recorded pair a later phase cannot
  move; this is G5's "delta scope reviewing", already shipped).
- **Output:** the same `Finding` contract, re-graded — no new contract
  needed, and `block_on_severity` already consumes it:
  - `blocker` — violates the frozen scope's stated guarantees, or a
    regression introduced by the reviewed delta, with file-level evidence;
  - `major` — in-scope, concrete, evidenced defect;
  - `minor`/`nit` — style, philosophy, future work: recorded, never blocks;
  - unevidenced or out-of-frozen-scope findings: demoted to `nit` with the
    demotion reason in the finding body (rejected-as-unfounded, preserved,
    attributable).
- **Rubric asymmetry:** a *new* blocker is always allowed — the engine must
  never suppress discovery — but the adjudicator must state why it is
  in-scope or a regression, which is exactly the judgment Sol's arbiter
  performed and the raw gates could not.
- The node wears the existing `arbiter.md` persona (`readonly: true`,
  stdout result channel — its adjudicated `Finding[]` lands in
  `phases/adjudicate/result.json`); the gate becomes
  `block_on_severity --at major --node adjudicate` (or `--at blocker`),
  which resolves exactly that file via `LOCKSTEP_PHASE_DIR`. **Zero engine
  change**: contract, gate, persona, and probe all ship today; what was
  missing was the composition, stated.

### The findings ledger

Lifecycle across rounds needs a durable artifact, and `runs/` is gitignored
and sensitive — so the ledger is a **repo file the flow declares**
(e.g. `docs/reviews/<campaign>-ledger.json`). Entries carry a stable id
(normalized category + file + claim digest), state
(`new` / `persisting` / `resolved` / `accepted-risk` /
`rejected-as-unfounded`), round, and disposition evidence.

**The model never touches the ledger file.** The adjudicator is readonly
(stdout result channel), so it *cannot* write it — and it must not: Sol's
hardest acceptance test is that an `accepted-risk` disposition "cannot be
silently removed by a later model response", and no prompt can promise
that. Instead the deterministic gate body **`lockstep.gates.ledger_check`**
(the ledger inside its shell node's `spec.writes`) takes the prior ledger
plus the adjudicator's `result.json`, refuses any invariant violation — a
prior blocker vanishing without a disposition, an `accepted-risk` entry
dropped or reworded — and only then merges and writes the updated ledger.
The model proposes findings; the program owns the file. That is the house
pattern (gates are tested programs), and it makes the acceptance test true
by construction rather than by trust.

### Deliverables (0.11.0)

1. `flows/factory/adjudicated-review.tg.json` — reviewers (fan-out) →
   adjudicate → `ledger_check` → `block_on_severity --node adjudicate`,
   with `full` and `delta` variants (delta feeds `node_diff` output and the
   ledger; full omits them).
2. `lockstep.gates.ledger_check` + ledger schema in
   `flows/factory_contracts.py`.
3. Advisory lint (`verify --lint`): warn on the ping-pong shape — multiple
   Finding-producing harness nodes each gated directly by its own
   `block_on_severity` with no intervening adjudication node. Exact
   detection heuristic settled during implementation; advisory only, exit
   code unchanged.
4. FLOW-AUTHORING: "Convergent review" section — the two-mind principle,
   the frozen-scope statement, and why gating raw reviewers does not
   converge.
5. Cockpit follow-on (separate, after the ledger exists to render): MISSION
   shows `4 resolved, 1 persists, 2 new` from the ledger instead of an
   undifferentiated list.

Sol's acceptance tests map: A/B → B/C rounds (ledger states), attributable
accepted-risk (`ledger_check`), delta reviewer raising a labeled new
blocker (rubric), gate on adjudicated not raw severity (`--node
adjudicate`). All zero-token testable with the fake executor.

## S4 — stable shell projection (split: discipline now, label 0.11.0, projection deferred)

Diagnosis confirmed — `seed.py` never serves shell nodes by design
(§0.1.7), and their volatile output re-billed downstream harness nodes a
pre-run `explain --graph` had called fresh. But the proximate cause was **a
pytest duration inside a Verdict reason**, and that is a flow bug our own
house rule exists to prevent: gate bodies are tested programs in
`src/lockstep/gates/` precisely so their output is deterministic
(`pytest_verdict` emits a stable pass-path Verdict for this reason). The
volatile-reason gate was consumer-authored.

Three-part disposition:

1. **Consumer-authoring, now:** a shell node's output is a hash input for
   everything downstream that interpolates it — emit stable text; keep
   timings and logs on stderr or in evidence files. Goes in FLOW-AUTHORING
   as a named anti-pattern, and back to MIMIR in the passdown.
2. **Accepted, 0.11.0:** `explain --graph` labels a node downstream of an
   always-rerun shell node **`conditionally fresh`** with the shell
   dependency named. `explain` already documents the assumption it makes;
   saying it per-node is strictly more honest and costs one graph walk.
3. **Deferred to 0.12.0, tentatively accepted:** the `stable_output`
   JSON-pointer projection (`{steps.X.stable}` alongside `{steps.X.output}`,
   raw logs untouched as evidence). Clean and additive, but it adds a
   second interpolation/hash channel — it should ship only if the
   discipline in (1) proves insufficient in practice. The text-normalizer
   variant is declined outright (a normalizer command is a second program
   whose determinism nobody checks).

## S3 + G2 — adopt a human-remediated artifact (accepted; design note first)

The gap is real and the OW-07 evidence shows today's failure mode precisely:
after a gate block and a legitimate human edit, every road is bad —
`resume` re-runs the producer if its hash missed; `--seed` correctly
refuses the dirty scope; `--allow-dirty-scope` waives the one protection
standing between the producer and the human's edits (and in the observed
run, the producer did re-run and did overwrite); the safe workaround
(author a second re-review flow) works but severs lineage.

**We adopt Sol's semantics, not Gemini's.** The two asks look similar and
are not: Sol adopts an *artifact* into a settled run and reruns its
consumer cone — provenance recorded as `external-approved-remediation`,
never pretending the harness produced the edit. Gemini's `adopt-node` marks
a *failed node* `done` because the tree now looks right — which would let
"looks right" stand in for "the recorded result is what produced this", and
that is the provenance the trace chain exists to protect.

Sketch (target 0.12.0, short design note first):

```
lockstep adopt <run_dir> <writer-node> --reason-file owner-decision.md [--path <p> ...]
```

1. Refused while a driver is live (lock check, same as resume).
2. Paths default to the dirty paths inside the writer's declared
   `spec.writes`; anything outside is refused; unrelated dirty paths
   overlapping another writer's scope are refused (the preflight's logic,
   reused).
3. Journals an `adoption` event: per-path before/after blob hashes, the
   reason text, `source: external-approved-remediation`.
4. Marks transitive consumers pending **explicitly** — not by leaning on
   reads-hashes, which cannot see a consumer that reads the artifact
   without declaring it (`reads.py`: an undeclared read stays invisible);
   downstream reviews and gates run unweakened on resume.
5. **The design note's two hard questions**, which are why the note
   precedes the code:
   - *Result-text consumers.* Adoption only composes when consumers see
     the artifact through the tree (`spec.reads`, or reading the file). A
     consumer that interpolates `{steps.<writer>.output}` consumes the
     *recorded result text*, which adoption does not rewrite — the
     taskgraph's reference forms are statically parseable, so `adopt`
     refuses (or warns with `--force`) when the cone contains one.
   - *Pinning across a hash miss.* "Do not rerun the adopted writer" is
     not achievable by inaction: if the writer's own input hash missed —
     exactly the OW-07 case, volatile upstream shell output — a plain
     resume reruns it and overwrites the adopted artifact anyway. The
     adoption record must therefore settle the writer *even against a hash
     miss*, which is a real, deliberately-scoped departure from "nothing
     is trusted except the hash": trusted here is the journaled human
     adoption event, surfaced by `status`/`explain` as
     `settled-by-adoption` (never as a cache hit), and dissolved by
     `--fresh` or `--force-stale` like any other pin.

Acceptance tests: the consumer's four, plus the result-text-consumer
refusal.

## G1 — write scopes: advisory scopes declined; corrective re-spawn accepted

The factual claim is right: the scope check runs once, after
`_execute_with_retries`, so a violation never retries regardless of
`retry.max` — quarantine, `failed`, stop. Two asks, opposite fates:

**G1a (`secondary_writes` / advisory scopes) — declined.** A scope a node
may violate is not a scope; it converts the quarantine from a guarantee
into a suggestion, and the same report's summary calls that quarantine
"airtight" as praise. The shipped answers to lint-cleanup drift: declare
the scope honestly (if the linter makes the node touch `metagraph.py`, the
node writes `metagraph.py`), or on pi use the **`pi-guarded`** stanza —
the ADDENDUM-A scope guard makes the out-of-scope write fail *in-session as
a tool error*, where the agent self-corrects for free, instead of post-hoc
at the snapshot. Enforce, never enable; deleting the extension changes
nothing a correct agent can do. This goes in the passdown — the consumer's
Phase-1 loss was one `--extension` flag away from not happening.

**G1b (one corrective re-spawn on violation) — accepted, 0.12.0.** We
already do exactly this for contract violations, for the same reason:
headless spawns are stateless, so the correction must carry its own
context. On a scope violation, one re-spawn embedding the original prompt,
the quarantined patch as fenced evidence, and the declared scope
("revert X; keep only edits to declared files"), from the restored tree.
Bounded (one round, spends a spawn, journaled), symmetric with the
contract-violation shape, and it does not weaken the boundary — the
quarantine still happened; the retry starts clean.

## G4 — topology/config hash split — declined; the mechanism ships

`flow_hash` is the sha256 of the flow file's raw bytes, and it is *lineage
identity*, not an input hash. The report's cost claim — edit one budget
line, lose all completed upstream work — describes 0.8.x. The entire
documented purpose of `--seed` (`seed.py`'s docstring, nearly word-for-word
the report's complaint) is: **keep the refusal, remove the cost.** Bump the
budget, then

```
lockstep run <edited-flow> --seed <old_run_dir>
```

— every node whose input hash matches is served free into the new lineage;
only what the edit actually touched runs, plus two documented exceptions:
shell nodes (§0.1.7 — always re-run, but they spend no tokens) and **map
items** (never seeded — their per-item hash is composed after planning, a
deliberate limit `seed.py` records; a map-heavy flow re-bills its items
across lineages, and the passdown must say so rather than let `--seed`
oversell itself). Splitting `topology_hash`/`config_hash` would weaken
lineage identity — and put us in the business of adjudicating forever which
JSON keys are "non-structural" — to solve a solved problem. Where `--seed`
underperforms in practice, the cause in the OW-07 evidence was volatile
shell output, which is S4's item, not a hash-granularity problem.
(The narrow budget case gets the dedicated fix anyway: G3a's
`resume --max-agent-spawns` skips the new lineage entirely.)

Classification: consumer-authoring + documentation. The passdown and
FLOW-AUTHORING get an explicit "editing a flow mid-campaign" recipe:
budget-only → `resume --max-agent-spawns` (0.11.0); any other edit →
`run --seed`, with `explain --graph` first to see what will re-bill.

---

## What already ships that OW-07 needed (passdown items)

Four of the reported pain points have shipped answers the consumer did not
find. That is a discoverability finding against our docs, and it becomes a
"consumer playbook" page plus a passdown note to the work repo:

| Pain | Shipped answer |
|---|---|
| "editing the flow throws away completed work" (G4) | `run <flow> --seed <run_dir>`; `explain --graph` to preview |
| "reviews re-audit the whole cumulative history" (G5) | `node_diff --node <id>` — the recorded `tree_before`/`tree_after` pair; `lint-live-diff-per-phase` exists because of exactly this |
| "agents get quarantined for helpful lint cleanup" (G1) | `pi-guarded` stanza: in-session scope enforcement, self-correcting |
| "detached runs can't wait for approval" (S5) | auto-reject **is** the park; exit 6 is the handoff; interactive `resume` asks for real, once |

## Release plan

- **0.10.1 (bug-fix):** S6 terminal record + refusal event + reader
  precedence + detach grace window; S5 wording on `wait`/`status`/MISSION.
  Full pytest + the S6 acceptance six + re-record nothing (no hash change).
- **0.11.0:** G3a `resume --max-agent-spawns`; S1 `reads_manifest: "paths"`;
  the adjudicated-review programme (template, `ledger_check`, lint, docs);
  S4's `explain --graph` conditionally-fresh label; the consumer playbook
  page. Replay fixture must pass un-re-recorded (all additive to hashing).
- **0.12.0:** S3 `lockstep adopt` (design note first); G1b scope corrective
  re-spawn; S4 `stable_output` only if the determinism discipline proves
  insufficient by then.
- **Not scheduled:** G1a advisory scopes, G4 hash split, S1 `"contents"`,
  S5 new run-state/exit code, G3b per-node budgets (deferred).

Frozen surfaces audit: no exit-code changes (7 already means refusal; S6
makes detached runs *report* it), no hash-composition changes (S1 rides the
prompt; the ledger is an ordinary written artifact), `format_version`
untouched (one additive optional state field). Each item lands with tests
per the working agreement; nothing here requires a DEVIATIONS entry.
