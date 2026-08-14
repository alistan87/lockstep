---
type: proposal
title: "Proposal: parity tiers 1–3 — patterns, loop, composition, and declared staleness"
description: What it takes to close the feature gap with pi-taskflow 0.2.6 for tiers 1–3, after checking each item against this repo's source. Phases A–C (patterns docs, loop, declared staleness) adopted 2026-08-13 and BUILT 2026-08-14, five commits each adversarially reviewed; composition (§2.2) split out to PROPOSAL-flow-composition.md; race not built. Includes the adversarial reviews that reshaped it.
resource: docs/proposals/PROPOSAL-taskflow-parity-tiers.md
status: stable
---
# Proposal: parity tiers 1–3

**ADOPTED IN PART, 2026-08-13. A–C BUILT, 2026-08-14.** All three adopted
phases shipped in five working commits, each followed by an adversarial review
whose blockers were resolved before the next began: **A** `0c894cf`/`676a126`
(patterns docs, tournament starter + `tournament_pick` gate + `TournamentPick`
contract; review moved the candidates' answers off the publish argv), **B**
`9e23fbb`/`ec454c1` (heal-text round number, `heal.on_exhausted`, loop-body
lint, `refine-loop` starter; review caught the theory doc lagging the engine
and named the lifetime-rounds edge), **C** `ed15c4c`/`a3910b4`/`894a803`/
`14785c2` (`spec.reads`, `explain --graph`, `--force-stale`; the memo is
stat-keyed rather than the adopted text's once-per-process — recorded in
DEVIATIONS — and `RenderCtx.runs_root` exists because the derived exclusion
excluded the world for throwaway planners). Exit criteria all met: full pytest
green throughout, torture suite 6/6, replay fixture unchanged across both
hash-adjacent phases, DEVIATIONS entries for `reads` and `on_exhausted`, every
new surface in FLOW-AUTHORING first. The authority is the commits; this
document stays as the reasoning.

**Phase D (composition, §2.2) was NOT adopted** — an independent third-pass
review (below) found three unanswered engine questions (findings 24–26: pool
dispatch for a `flow` kind, the per-engine lock registry, resume-mid-child
semantics), each a design decision rather than an implementation detail.
**D's successor proposal now exists:**
`PROPOSAL-flow-composition.md` answers findings 24–26 (plus 4 and 21) and is
where any adoption of composition happens. **Race (§1.4) is not built**, per
the recommendation. §4's sequencing tension (finding 22) resolved by default:
C-before-D became the schedule, not a trade.

**Standing caveat, stated once.** The recommendation on the record is *not* to
chase parity: pi-taskflow is a maintained package with a larger surface, and
lockstep's durable advantages (no credential, the protected working tree, the
auditable run dir, the cockpit) are things it does not compete on. This plan
exists because the work was asked for anyway. It is scoped so that every item
is worth having on its own merits even if parity is never reached — nothing
here is built *only* to match a checkbox.

**Correction to an earlier estimate.** A previous answer put tiers 1–3 at 3–6
months. Reading the source changed that: `reduce`, `parallel` and `tournament`
are already expressible, and `loop` is mostly machinery that exists. The
revised estimate is **7–10 weeks**, with the largest single item (composition)
being 2–3 of those.

---

## 0. The constraint that shapes everything

`Role = Literal["work", "gate", "approval", "map"]` is frozen for
`format_version` 1.x (SPEC §4). **A new role costs a format-version bump**,
which is a spec decision, not an implementation one.

`kind` is **not** frozen: `taskgraph.py:45` declares `kind: str = "harness"
# executor registry key`, and verification resolves it against the registry
(`unknown-kind` fires only when no executor claims it). A new kind is the
*sanctioned* extension point — implement `protocols.Executor`, register it, done.

So the design rule for this whole plan:

> **No new roles. New kinds are fair game.** Everything rides on the four
> existing roles, plus additive `spec` keys, plus — where the behaviour is
> genuinely a new way to execute a node — a new executor kind.

That distinction turns most of "tier 1" into documentation and removes the only
place this plan would otherwise have needed a 1.1 conversation (composition,
§2.2). It does not rescue `race`, whose problems are elsewhere.

---

## 1. Tier 1 — three of four are already expressible

### 1.1 `reduce` — exists, unnamed. Ship docs only.

`_run_map` writes the node's result as a JSON array of every item's result
(`roles.py`, `result_text = json.dumps(slots)`). A downstream node consuming
`{steps.<map>.json}` **is** a reduce; `flows/starter/file-audit.tg.json`'s
arbiter is the worked example. A `reduce` role would add a name and a format
bump and nothing else.

**Work:** one section in FLOW-AUTHORING naming the pattern, cross-referenced
from the starter README. **~half a day.**

### 1.2 `parallel` — exists as wave scheduling. Ship docs only.

Nodes with satisfied dependencies dispatch together, bounded by
`--max-workers` and the `tree` token. What taskflow calls a `parallel` phase is
lockstep's default. The one thing worth documenting is the *inverse*: why
tree-writing nodes serialize, and that `readonly` is what buys fan-out.

**Work:** docs, folded into 1.1. **~half a day.**

### 1.3 `tournament` — expressible; ship a starter flow.

N candidate nodes + a judge is `plan-adversarial`'s shape with the reviewers
replaced by competing authors. Nothing in the engine is missing.

**Work:** one starter flow (`tournament-judge.tg.json`), its invariants in
`test_starter_flows.py`, a README row. **~1 day.**

### 1.4 `race` — recommended AGAINST. Decision gate, not a schedule.

Race means: dispatch N, take the first success, cancel the rest. Three problems
— the fourth one a first draft listed, the wave barrier, is **not** a problem,
and the correction matters because a reader who spots the error would discard
the rest of the section with it: `run()`'s `futures_wait` barrier is only an
obstacle if racing is scheduled by the *engine*. As a `kind: "race"` executor
whose `execute()` spawns N candidates and returns the first, the barrier never
enters into it. What remains:

1. **Cancelled losers still bill.** §9.5 counts spawns, and a killed spawn was
   still paid for. A "race for latency" feature that costs N× tokens must say
   so in the flow author's face, not in a design doc. Worse as an executor:
   `_spend_spawn` is called once per `execute()`, so an N-way race would bill
   **one** spawn unless the executor is given a way to charge N — a silent
   under-count in the one number the budget exists to bound.
2. **Racers cannot write the tree.** Two writers need the `tree` token, which
   serializes them — a serialized race is just a slow sequence. So racers must
   be `readonly`, which reduces the feature to "ask N models a question, take
   the fastest answer". That is real, but it is much smaller than it sounds.
3. **No workspace isolation.** taskflow's racing story is `cwd: "worktree"` —
   each candidate mutates its own tree and a judge picks a diff. lockstep has
   one shared tree by design. Racing *writers* requires per-node worktrees,
   which is a larger project than everything else in this plan combined.

**Recommendation: do not build.** If it is wanted anyway, the shape is a
`spec.race` key on a `work` node (no new role), restricted to `readonly: true`
candidates, with a verify error otherwise and the token cost stated in
`--dry-run`. **~1 week if approved, and it buys the least of anything here.**

**Tier 1 total: ~2 days of documentation and one starter flow.**

---

## 2. Tier 2 — one small feature, one real project

### 2.1 `loop` — a non-rollback heal plus two small additions

A heal gate already is a loop: it re-marks its targets pending, folds its
findings into their prompts as feedback, and re-runs up to `max_rounds`.
`rollback: false` is already legal, which is exactly the difference between
"undo the bad attempt" (heal) and "build on the last one" (loop).

Three gaps, all small:

| Gap | Fix |
|---|---|
| The body cannot tell which round it is in | Append the round to the **engine-composed heal text**, beside `_heal_scope_line`. NOT a new `{round}` interpolation form: reference forms are a §7 surface, and `heal_texts[node]` already folds into both the prompt and the hash. Same result, none of the risk. |
| Exhausting rounds always blocks (exit 2) | `heal.on_exhausted: "block" \| "pass"` — `"pass"` accepts the best-so-far, `"block"` stays the default |
| The pattern is undocumented | A `refine-loop` starter flow + FLOW-AUTHORING section |

`on_exhausted: "pass"` must not record a plain `pass`. The verdict's reason is
rewritten to name what happened (`accepted after N rounds without resolving:
<original reason>`) and the journal gets its own event, or `status` and
`verify-trace` would report a gate that blocked as a gate that was satisfied —
which is precisely the kind of quiet untruth the trace exists to prevent.

`on_exhausted: "pass"` is a foot-gun by construction: it converts a blocking
gate into a passing one, and gates exist to stop bad work. Guards:
**forbidden together with `rollback: true`** (a gate that rolls back and then
passes has thrown away the work it just accepted), and a `verify --lint`
warning naming every gate that uses it.

**Work:** round number appended to the engine-composed heal text in `roles.py`
(beside `_heal_scope_line` — NOT a `{round}` form in `interpolate.py`; this
line originally contradicted finding 17 and was corrected at adoption, finding
23) + `on_exhausted` in the gate outcome path + verify rule + lint + starter
flow + tests. **~1 week.**

### 2.2 `flow` composition — run a saved flow as one node

The highest-value item in tiers 1–2, and the only one that shrinks the repo:
seven factory flows exist partly because there is no way to call one flow from
another.

**Shape:** a new executor kind, which §0 establishes is an extension point
rather than a format change — `role` stays `work`, and a `FlowExecutor`
implements `protocols.Executor` like `harness` and `shell` do:

```jsonc
{ "id": "cut", "role": "work", "kind": "flow",
  "spec": { "flow": "flows/factory/release-cut.tg.json",
            "args": { "version": "{args.version}" } } }
```

No `format_version` bump, no spec conversation, and `verify` already rejects
the kind on any machine whose registry lacks it (`unknown-kind`) — which is
the correct failure for a driver that has not been upgraded.

The executor seam does not, however, make the *runtime* question go away: a
`FlowExecutor.execute()` still has to run a whole graph from inside one node.
That is where the difficulty actually lives, below.

**Engine decisions, each with a wrong default:**

- **In-process sub-engine, not a child process.** A child process cannot share
  the parent's in-memory `tree` token: parent holds it, child blocks on it,
  deadlock. In-process, the child's nodes acquire from the same registry —
  which means the *parent node itself must hold no token* while its child runs,
  or the same deadlock reappears one level up. This is the single most likely
  place for this feature to ship broken.
- **One wallet.** The child shares the parent's `token_spawns` counter and
  wall-clock deadline. A child that trips the budget raises `BudgetTripped`
  inside the parent's node thread, which must propagate as a run-level stop
  (exit 4), not as a node failure (exit 3).
- **Hash.** The child's `flow_hash` and the rendered args fold into the parent
  node's `input_hash`. Editing the child flow re-bills the parent node and
  everything downstream — correct, and it must be *said*, because it is
  surprising.
- **Run dir.** `<parent_run>/children/<node_id>/` with its own `state.json` and
  journal, so `status`, `explain` and the cockpit can descend. **Check the
  readers first:** `gc.plan_gc` and `estimate` both do a single-level
  `runs_dir.iterdir()`, so a nested `state.json` is invisible to them today —
  which is the safe direction (a child is never gc'd independently of its
  parent) but must be asserted by test rather than assumed, and `active`'s
  single-level scan must stay single-level for the same reason.
- **Cycles and depth.** Verify-time cycle detection across flow *files*
  (a → b → a) plus a depth cap (5, matching taskflow's).
- **Result.** The child's `final` node's result becomes the node's result and is
  validated against the parent node's contract.

**Work:** ~2–3 weeks including the torture-suite cases (child budget trip,
child gate block, child failure, resume mid-child).

---

## 3. Tier 3 — declared staleness

**Scope statement, up front.** taskflow's provenance plane is *observed*: its
runtime records which upstream outputs each phase actually read. lockstep
cannot observe reads. Its agents are opaque subprocesses; a pi extension could
report them, `claude -p` cannot, and a feature that only works on one harness
violates ADDENDUM-A's rule that extensions may enforce but never enable.

So lockstep's version is **declared-only**, and this is *not* parity. What it
buys is precision, not correctness — an undeclared read stays invisible. That
must be stated everywhere the feature is documented, because `spec.writes`
already taught this repo what happens when a guardrail is believed to cover
more than it does.

The good news: for *step-to-step* edges lockstep's declared plane is already
exact. Interpolation is the only channel by which an upstream result reaches a
node's prompt, and the prompt is hashed. The only missing plane is **files**.

### 3.1 `spec.reads` — declared file inputs as hash parts

```jsonc
{ "id": "audit", "spec": { "reads": ["src/**", "pyproject.toml"], "task": "…" } }
```

Each matched path contributes `path|content-sha256` to `fingerprint_parts`, so
editing a read file invalidates exactly the nodes that declared it.

**Hash composition is a frozen surface (M3).** The change must be *additive*:
a node with no `reads` key produces byte-identical parts to today. Pinned by a
test asserting that, and by the replay fixture continuing to pass **without
re-recording** — if the fixture needs re-recording, the change was not additive
and is wrong.

**Cost.** Hashing globs runs at every plan, and `_settle` re-plans every `done`
node on resume. That is the shape that made a gate creep 13 → 32 minutes across
resumes (lesson 20). Mitigations, all required: a **per-process** memo (each
path hashed once — this helps several nodes with overlapping globs inside one
run, and deliberately does NOT survive a resume, because a resume must re-read
the tree or the whole feature is a lie), a `kind: "timing"` journal line
(`op: "reads-hash"`), a measured ceiling in `contrib/snapshot_bench.py`, and a
`verify --lint` warning when a `reads` glob matches more than N files.

**Work:** ~1 week including the perf work.

### 3.2 `why-stale` — the whole-graph dry run

Plan every node against the current tree and config, compare each `input_hash`
to a prior run's record, and print two sets: **directly stale** (its own hash
moved, with the moved part named) and **transitively stale** (an upstream is
stale, so its hash cannot be computed yet). Zero spawns, zero tokens.

This is `explain` generalised from one node to the graph, reusing
`diff_labels`, `executor.plan()` and `compose_hash`.

**Two constraints that are easy to get wrong:**

- Planning renders prompts, and rendering spills oversized values to files.
  This must plan into a **throwaway directory** and never write into the run it
  is reading — a read-only command that mutates the artifact it inspects is
  worse than no command.
- Rendering a node's prompt needs its upstreams' **recorded results**, which
  `gc` may have deleted. A missing result is not an error: it means the node
  cannot be proven fresh, so it reports as stale. Fail toward re-running, never
  toward a false "unchanged".

**Surface decision.** This is the fourth verb in the neighbourhood of
`explain` / `--dry-run` / `--estimate`, on a CLI that is already 16 verbs deep.
Ship it as **`lockstep explain <run_dir> --graph`**, not a new verb.

**Work:** ~1 week.

### 3.3 `recompute` — seed with a forced-stale frontier

`run <flow> --seed <prior_run> --force-stale <node>` — serve every hash-matched
result as `--seed` already does, except the named node and everything
downstream of it, which run for real. That is `recompute` with `--apply`;
`explain --graph` is its dry run.

`seed.py` already declines to serve a node whose hash moved; this adds a
"declines to serve this one regardless" set, plus provenance: `status` and the
journal must distinguish *forced* from *hash-missed*, or a reader cannot tell
why a node re-billed.

**Work:** ~2 days on top of 3.2.

---

## 4. Sequence, and the tension in it

| Phase | Items | Weeks |
|---|---|---|
| A | 1.1–1.3 docs + tournament starter | 0.5 |
| B | 2.1 loop (`{round}`, `on_exhausted`, lint, starter) | 1 |
| C | 3.1 `reads` → 3.2 `explain --graph` → 3.3 `--force-stale` | 2.5 |
| D | 2.2 composition (incl. the 1.1 format decision) | 2–3 |
| — | 1.4 race | not scheduled |

**The tension, stated rather than hidden** (and see review finding 22 — the
second pass moved this argument, it is not settled): C-before-D matches the
pain that was actually reported (work re-billing after a resume), but
D-before-C would shrink the surface C has to support — composition is what could collapse seven
factory flows into fewer, and every flow that exists when C lands is a flow C's
`reads` declarations must be retrofitted into. The order above prefers reported
pain over speculative consolidation. Reversing it is defensible; doing C and D
concurrently is not, because D changes what a node *is*.

**Resolved at adoption (2026-08-13):** C before D, by default rather than by
argument — D is not adopted and returns as its own proposal (findings 24–26),
so C is simply the schedule. Finding 22 is closed.

**Exit criteria for the adopted scope (A–C; the composition items move with D
to its own proposal):** full pytest green, replay fixture unchanged as a smoke
check with the additive-parts assertion as the proof (finding 27),
`DEVIATIONS.md` entries for `reads` and `on_exhausted`, and every new surface
in `docs/guides/FLOW-AUTHORING.md` before it is announced anywhere else.

---

## 5. What this plan deliberately does not do

- **No `expand` / LLM-authored sub-graphs.** The creed is *the model authors
  content, never control flow*. Implementing `expand` means deciding that creed
  was wrong; that is a separate conversation, not a line item.
- **No shared-context blackboard.** It needs in-harness tools, which is
  enable-not-enforce and therefore forbidden by ADDENDUM-A for any capability a
  flow depends on.
- **No TypeScript DSL.** `pydantic`-only is load-bearing; a Node dependency for
  authoring is a bigger cost than the ergonomics are worth.
- **No workspace isolation keywords.** `temp` / `dedicated` / `worktree` per
  node is the correct enabler for racing writers and competing experiments, and
  it is a project of its own. If it is ever built, it should be built for its
  own sake, not to unlock `race`.

---

## 6. Adversarial review of this plan

Five lenses, run against the draft above. Findings that changed the plan are
marked **applied**; the rest are recorded so the next reader does not have to
rediscover them.

**Cost and hash correctness**

1. **applied** — 3.1 changes hash composition, a frozen surface. Made additive,
   with "the replay fixture must pass without re-recording" as the falsifiable
   test of that claim.
2. **applied** — 3.1 re-hashes globs on every resume revalidation, the exact
   shape of lesson 20's 13→32 minute creep. Memo, timing line, bench and lint
   are now required, not optional.
3. **applied** — `why-stale` plans nodes, and planning spills files. Now
   required to plan into a throwaway directory.

**Concurrency and safety**

4. **applied** — composition deadlocks by default: a parent node holding the
   `tree` token while its child's nodes queue for the same token. Called out as
   the most likely way this ships broken.
5. **applied** — child budget semantics were unstated. One wallet; a child trip
   propagates as a run-level stop (4), not a node failure (3).
6. **open, accepted** — `--force-stale` on a node whose downstream set is large
   can re-bill most of a graph with no ceiling. `explain --graph` is the dry run
   that shows it; no hard limit is proposed, on the grounds that `--seed`
   already has none and one wallet already bounds it.

**Spec and contract**

7. **applied** — the first draft added roles for `reduce`, `loop` and `race`.
   Every one of those is a `format_version` bump. The no-new-roles rule in §0
   is the result, and it is what shrank tier 1 to documentation.
8. **applied** — `on_exhausted: "pass"` silently converts a blocking gate into
   a passing one. Now forbidden with `rollback: true` and lint-visible.
9. **applied** — a forced re-run and a hash miss would have been
   indistinguishable in `status`. Provenance is now part of 3.3's scope.

**Value and strategy**

10. **applied** — the plan risked describing `reads` as a correctness feature.
    It is a *precision* feature: an undeclared read is silently stale-blind,
    the same failure `spec.writes` had before V1. §3's scope statement now says
    so before describing the mechanism.
11. **open, unresolved** — C-before-D vs D-before-C. Recorded in §4 as a real
    trade rather than settled by assertion.
12. **applied** — the draft added a `why-stale` verb to a 16-verb CLI,
    overlapping `explain` and `--dry-run`. Folded into `explain --graph`.

**Does it survive the 2026-08-13 consumer report?**

13. **applied to scope** — a `loop` body re-enters nodes that are already
    `done`, which is structurally the same shape that produced the phase-1
    contamination: anything in a loop body that captures the live tree is wrong
    from round 2 onward. `lint-live-diff-per-phase` must be extended to fire
    inside a loop body, and the `refine-loop` starter must use `node_diff`.
    Added to 2.1's scope.
14. **open, small** — `explain --graph` reads a run dir that may be mid-write by
    a live driver. It should print the same `STALE`/`lock:` line `status` does,
    so a reader knows whether the state it is reasoning about is settled.

**What the first review did not find.** No finding argues that any of A, B or C
is unsound — only that three of them were mis-scoped and one (race) is not
worth its cost. D is sound but is the item most likely to ship subtly broken,
and it should not be attempted by anyone who has not read `roles.py`'s token
acquisition path end to end.

### Second pass — against the plan's factual premises

The first review argued with the plan's judgement. This one checked its claims
against the source, which is where the real damage was.

15. **applied, material** — the plan's organizing constraint was half wrong.
    `role` is a frozen `Literal`; **`kind` is `str`, resolved against the
    executor registry** (`taskgraph.py:45`, `:393`). Adding a kind is the
    sanctioned extension point, not a format change. This deleted §2.2's
    `kind: "shell"` lie, removed the only proposed `format_version` bump from
    the plan, and made composition materially cheaper and less risky than
    drafted.
16. **applied** — §1.4's lead objection to `race` (the wave barrier) was wrong
    for the same reason: as an executor kind, racing never touches the
    scheduler. Corrected in place, because a reader who catches a wrong
    argument discards the correct ones next to it. The recommendation does not
    change; a *new* and sharper objection replaced it — `_spend_spawn` charges
    once per `execute()`, so an N-way race would under-bill the budget by N-1
    unless the executor is given a way to charge for what it really spawned.
17. **applied** — 2.1 proposed a new `{round}` interpolation form. Reference
    forms are a §7 surface; the engine already composes `heal_texts[node]` into
    both prompt and hash. Appending the round there is the same feature with
    none of the risk.
18. **applied** — `on_exhausted: "pass"` would have recorded a plain `pass` for
    a gate that blocked, making `status` and `verify-trace` misreport. The
    verdict reason and a distinct journal event are now in scope.
19. **applied** — 3.1's "per-run memo" was imprecise in a way that mattered: a
    memo cannot span resumes, and *must not*, or a resumed run would trust a
    hash of a tree it never read.
20. **applied** — 3.2 assumed upstream results are always readable. `gc` can
    delete them. Missing result ⇒ stale, never "unchanged".
21. **applied** — nested child run dirs are invisible to `gc.plan_gc` and
    `estimate`, both single-level `iterdir()`. That is the safe direction, but
    it is currently an accident and needs a test.
22. **open, affects sequencing** — finding 15 makes D (composition) cheaper and
    less risky than §4 assumed when it put D last. The C-before-D argument is
    now weaker than when it was written; §4's stated tension should be
    re-decided by whoever adopts this, not inherited from the draft.

**Standing note for a third reader.** Both passes above were run by the same
author as the plan. The failure mode that leaves — shared blind spots — is
exactly what an independent reviewer is for. The highest-value target for one
is §2.2: everything else in this plan is small enough to fix after the fact.

### Third pass — independent review at adoption (2026-08-13)

Run by a different reader than the author of the plan and both passes above,
against the source, at adoption time. It confirmed the plan's checked premises
(the role/kind split, `rollback: false` legality, the `heal_texts` fold at
`roles.py:328` and `:1496`, the single-level `iterdir()` in `gc`/`estimate`/
`active`, and resume putting every `done` node through `needs_check` →
`_settle` re-planning — so 3.1's perf worry is real). It found five things the
first two passes missed — three of them exactly the shared-blind-spot kind the
standing note predicted, all three in §2.2:

23. **applied** — §2.1's Work line still said "`{round}` in `interpolate.py`",
    the very thing finding 17 rejected; the gap table above it said the
    opposite. A stale-after-revision estimate line is how the rejected design
    gets built. Corrected in place.
24. **material, blocks D** — `_costs_tokens_hint` (`roles.py:272`) returns
    `False` for every kind that is not `harness` or `fake`, so a
    `kind: "flow"` node dispatches to the hardcoded 8-worker `other_pool`
    (`roles.py:578`, `:604`), not the `--max-workers`-bounded `token_pool` —
    every agent spawn inside a composed child escapes the operator's
    concurrency cap. The same seam finding 16 caught for `race`
    (`_spend_spawn` under-billing), unchecked for `flow`.
25. **material, blocks D** — the "same registry" §2.2's deadlock fix relies on
    does not exist as a seam: `_locks` is per-`Engine` instance state built in
    `__init__` (`roles.py:188`), with nothing injecting it. A naive sub-engine
    gets a fresh lock dict, and the failure is not finding 4's deadlock — it
    is the silent inverse: the child's tree-writing node runs concurrently
    with a parent-level sibling holding `tree`. Two writers, one shared tree —
    the exact invariant the exclusive-token design exists to hold.
    `_budget_guard`, `_snapshot_guard`, `store`, `workspace` and `needs_check`
    are in the same position; sharing them is real constructor work absent
    from the 2–3 week estimate.
26. **blocks D** — "resume mid-child" is listed as a torture case but is an
    undecided design question. Resume works from the parent's per-node
    `input_hash` and one record: does resuming re-run the whole child
    (correct but re-bills, under one wallet, everything the child already
    paid for) or descend into the child's own `state.json` (the parent record
    must carry a child-run pointer, and the child's lineage head must be
    reconciled against a tree the parent's other nodes have since moved)?
    A torture case cannot be written until this is answered.
27. **applied, small** — 3.1's headline falsifiable test ("the replay fixture
    passes without re-recording") is nearly vacuous: the portable fixture is
    shell-only by design, and shell `fingerprint_parts` is `argv:` alone
    (`shell.py:134`), so no fixture node could ever carry `reads`. The direct
    assertion test (no-`reads` node ⇒ byte-identical parts) is the real
    evidence; keep the fixture check, but as a smoke test, not the proof.

**Outcome.** A, B and C adopted as written (with 23 applied). D withdrawn to
its own proposal, which must answer 24–26 before any code — plus finding 4's
token-holding rule and finding 21's gc/estimate test, which remain right. Race
stays unbuilt. The standing note's request is discharged: §2.2 was the target,
and §2.2 is where all three material findings landed.
