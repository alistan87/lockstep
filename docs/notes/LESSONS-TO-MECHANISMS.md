# Lessons → mechanisms: retiring the work-repo authoring skill into engine guarantees

**Provenance.** The work-repo mirror's `lockstep-authoring` skill accumulated 24
numbered "lessons from live runs" (plus 9 graph-design items) across several
adversarial self-reviews. Each lesson marks a place where the engine let an
author do the wrong thing and charged tokens/heal-rounds for it. This note maps
every lesson to the mechanism that retires it — verify error, lint, engine
change, CLI addition — or marks it irreducible (stays prose). Engine claims
were re-verified against **this** repo's source at `58d6233` (2026-08-11), not
taken from the skill's text; two of its central claims were wrong (noted below).

**Standing rule adopted with this note:** a corrected claim in any
skill/lesson doc gets **deleted, not annotated**. The work-repo doc preserves
its own errors as inline commentary ("an earlier version of this item
claimed…"), which puts the wrong claim and the right one in the same context
window with the wrong one stated first. A doc read by an LLM must not carry
its own changelog; git carries it.

---

## P0 — confirmed engine bug (lesson 19): resume dispatches downstream of a stale, unrevalidated cache

Verified by inspection, `roles.py`:

- On resume, every `done` node enters `needs_check` (`roles.py:366`) for hash
  revalidation.
- `_settle` revalidates a `done`+`needs_check` node only once **its** deps are
  settled (`roles.py:449-451`). If its upstream is `pending` (e.g. was
  invalidated), the revalidation is deferred — the node **stays `done` and
  stays in `needs_check`**.
- The wave condition (`roles.py:548-549`) and the pending branch of `_settle`
  (`roles.py:429`) test only `status in SETTLED` (`{"done","skipped"}`,
  `roles.py:53`). Membership in `needs_check` is invisible to both.

Trace matching the live observation: `checks` invalidated → pending;
`capture-diff` (dep: `checks`) done, in `needs_check`, revalidation deferred;
`independent-review` (deps: `capture-diff`, `viewport-evidence`) is pending
with all deps "done" → **dispatched in the same wave as `checks`**, consuming
the previous attempt's cached evidence. The work repo needed 3 resume cycles
before the reviewer ever saw fresh inputs.

**Fix:** a dep counts as settled for dispatch (and for `when` evaluation in
the pending branch — same staleness hazard) only if `status in SETTLED` **and**
`dep not in needs_check`. Deferral is transitive through the existing
fixed-point loop, so this is two condition changes plus a regression test
(resume with an invalidated root and a done-cached middle; assert the leaf
does not start before the middle revalidates). Candidate for
`contrib/torture_suite.py` coverage. Spec: arguably already promised by §9.2/
§9.3 resume semantics; if not, log in DEVIATIONS → propose in r7.

---

## Second inverted claim, for the record

The skill asserts `write-scope-unenforced` fires on a *bare* `lockstep verify`
when a mutating node lacks `spec.writes`. Inverted twice: (a) the verifier
**skips** nodes with no `writes` entirely (`taskgraph.py:410-412` — `if not
writes: continue`); (b) `write-scope-unenforced` (`taskgraph.py:436-445`)
fires only when writes are *declared* but the `tree` token isn't held — which
harness/shell kinds hold implicitly, so for them it effectively never fires.
**There is no check today, error or lint, for "mutating node, no declared
write scope."** That absent check is item V1 below and the single
highest-leverage authoring mechanism in this note. (`readonly-unenforced`,
`taskgraph.py:383-388`, is real and a hard error — that half of the skill is
correct.)

---

## The table

Verdict key — **bug**: confirmed engine defect. **engine**: behavior change
here. **error/lint**: new `verify_flow`/`lint_flow` code. **CLI**: new command
or flag. **design**: working as intended; mechanism is surfacing, not change.
**fixed-here**: already solved at home; work repo is running a stale mirror.
**irreducible**: stays prose (skill/guide). **work-repo**: MIMIR-specific, not
lockstep's to fix.

### Evidence-bearing review pattern (lessons 1–6)

| # | Lesson (compressed) | Verdict | Mechanism |
|---|---|---|---|
| 1 | Server lifecycle (start→probe→teardown) in ONE shell node's one process | design + fixed-here | Pattern belongs in `/flow-authoring` as a worked example. Orphan risk it hedges against is largely closed here by Job Object containment (`19e644b`): a node MAY background a process for later nodes; nothing outlives the RUN. |
| 2 | Never point a probe at the live data store; copy to throwaway first | work-repo / irreducible | Domain discipline. Generic residue for the guide: probes are the OBSERVE half and must be side-effect-free — one sentence in FLOW-AUTHORING. |
| 3 | Deterministic classifier between raw probe output and interpretive reviewers | design | This is exactly the `gates/` vs `probes/` split that already exists here. Mechanism: name the pattern in FLOW-AUTHORING ("probe → classifier → reviewer"); consider a starter flow demonstrating it. |
| 4 | Contract JSON shape must be spelled out in the prompt or reviewers invent field names / wrap in fences | **engine** (E1, E2) | E1: for `output:"json"` nodes, inject the resolved contract's field list + enum values into `FOOTER` (generated from `contracts.py`, cannot drift from the validator). E2: file-channel results (`harness.py:345-356`) bypass fence salvage that stdout gets (`extract_last_json`, `harness.py:374,389`) — apply the same salvage to `result.json` content before contract validation. Together these retire lesson 4 and graph-design item 2. |
| 5 | A contract-valid `pass` can rest on a narrower check than the stated scope | irreducible (+ optional contract seam) | No checker catches "checked less than claimed." Optional mechanization: add optional `untested: [str]` to `Verdict` 1.1 and prompt reviewers to fill it — makes the narrowness visible, not impossible. Keep the lesson, two sentences. |
| 6 | One adversarial-review run is not exhaustive; repeat runs find disjoint findings | irreducible | Budget/process guidance. One sentence in the guide. |

### Lockstep mechanics (lessons 7–15)

| # | Lesson | Verdict | Mechanism |
|---|---|---|---|
| 7 | `verify --lint` can't resolve stanzas from a shared `--config`; false `no-executor-stanza` | **CLI** (C1) | Confirmed: `verify` subparser has no `--config` (`cli.py:605-607`) while `run`/`resume`/`doctor` do. Add it, thread into `verify_flow`/`lint_flow` (which already accept `config`). Retires the lesson outright. |
| 8 | Editing a flow starts a new lineage; a one-word prompt fix re-bills every done node | design + **engine** (E7, larger) | Refusal is correct (hash integrity). The pain is real: propose `run --fresh --seed <old_run_dir>` — warm-start a NEW lineage by serving any node whose input_hash matches a recorded result in the seed run (replay machinery already proves result-serving; this is hash-keyed so sound). r7 candidate; log seam in ROADMAP-NOTES. |
| 9 | Heal rounds exhausted → terminal block; hand-patching named defects is legitimate closure if declared | design + irreducible | Engine behavior correct. The residue — "cite which finding motivated each hand correction; never present a hand-patched result as the graph passing" — is cockpit/skill prose. Two sentences. |
| 10 | A synthesis node with read tools explores beyond its cited evidence paths | irreducible today; seam noted | Prompt discipline now. Future seam: `spec.reads` enforced by a pi extension (read-scope guard) — ADDENDUM-A-compatible (pure enforcement). Don't build until it bites again. |
| 11 | `taskkill` access-denied on throwaway server; orphaned listener | **fixed-here** | Job objects + unconditional-first `taskkill /T /F` (`proc.py`, `19e644b`, DEVIATIONS 2026-08-10). Work-repo folklore around a solved problem → mechanism is V3 (version provenance) + mirror update. |
| 12 | Write-capable orphan node survives orchestrator kill, keeps editing for minutes | **fixed-here** | Same as 11 — the exact scenario Job Object containment closes ("nothing outlives the RUN"). Keep the forensic residue (git-checkout-mid-diagnostic leaves tracked files at old content; never `rm -rf` a shared dir) as three lines in `/debug-run`. |
| 13 | A blocking gate wired to an absolute target (`ruff check .`, named test file) blocks on pre-existing debt; each false block burns a 30–40 min heal round | **engine** (E4) + new built-in gate | E4: first-class **baseline gates** — `baseline: true` on a gate runs its body once before its targets, records the verdict, and blocks only on findings not present in the baseline. Plus port the work repo's scoped-diff idea as a tested built-in: `lockstep.gates.scoped_checks` (lint/test only paths in the run's own diff). Retires the most expensive lesson. |
| 14 | Heal retry prompt = original task + gate findings, nothing else; prior attempts' expensive evidence is re-derived from zero | **engine** (E3) | Confirmed (`roles.py:1117-1119`, `harness.py:216-221`). Add a carry channel: engine folds `<phase_dir>/attempt-notes.md` (append-only, node-writable, size-capped) into the next heal round's prompt, hash-tracked exactly like `heal_text` already is. Bounded, resume-stable. |
| 15 | Gate findings naming out-of-scope files read as authorization; node with 2-template scope edited 5 core modules chasing a pre-existing failure | **engine** (E5) + V1 | E5: annotate composed `heal_text` with the target's own declared scope — "You may modify ONLY: {spec.writes}; report anything else, do not fix it." Engine-composed, so it can't be forgotten per-flow. Backstop is V1 (required writes) + existing quarantine. The skill's hand-written defensive clause dies. |

### Post-incident mechanics (lessons 16–24)

| # | Lesson | Verdict | Mechanism |
|---|---|---|---|
| 16 | Prose scoping is a fallback; mechanical `spec.writes` is the primary control | **error** (V1) | The keystone. `missing-write-scope`: a `role: work` node of a write-capable kind with no `spec.writes` is a verify **error**. Escape hatch: `"writes": ["**"]` requires `spec.writes_rationale` — whole-tree access becomes a deliberate, greppable act. Frozen-surface note: this changes `verify` outcomes for existing flows → land as lint now, promote to error at `format_version` 1.1 (record in DEVIATIONS + ROADMAP-NOTES). Retires 16, most of 15, and graph-design item 3's 40 lines. |
| 17 | `spec.task` is one of five prompt channels (persona, context, heal_text, steer_text) | doc | Pure description of existing engine. Becomes a five-row reference table in `/flow-authoring`. Not a lesson. |
| 18a | Heal rollback restores EVERY path changed since the gate baseline — including an operator's unrelated out-of-band fix, silently, every round | design-but-sharp + spec seam (E8) | Confirmed (`roles.py:1092`, scope = `changed_paths(baseline)`; `lint-concurrent-heal-rollback` documents the gate-vs-gate variant). Interim mechanism: rollback already emits `restored`/`discarded` events — additionally WARN loudly (event + stderr + cockpit) for any restored path in NO target's `spec.writes`: "restored a path no target declared — out-of-band edit undone." Real fix once V1 makes writes universal: narrow rollback scope to changed ∩ (targets' declared writes), spec amendment (r7) since §9.4.4 currently says all-since-baseline. |
| 18b | Editing a file inside a done node's `spec.writes` mid-run re-bills that node on resume | design | Cache soundness working as intended. Mechanism: surfacing — `lockstep explain` already answers "why re-billed" after the fact; add tiny `lockstep scope <flow> <path>` (C3) answering "which nodes' scopes contain this path" BEFORE the edit. |
| 19 | Resume runs a downstream node concurrently with its upstream's re-verification; reviewer evaluates stale evidence | **BUG** (P0) | See top of this note. Dispatch + `when`-eval must treat `done ∧ in needs_check` as unsettled. |
| 20 | Gate body exceeding `timeout_s` emits no verdict → not a valid block → heal never fires → terminal block with budget remaining | **engine** (E6) | Correct reading of §9.4.3, wrong outcome: a timeout is infrastructure failure, not a quality verdict. Give gate timeouts their own retry lane (bounded re-runs, optionally widened window) before terminal-blocking; `status` shows elapsed-vs-`timeout_s` so the cause is visible. Separately: their 13→19→30→32 min growth across resumes implicates `snapshot()` (`git add -A` + write-tree) churn on long-lived runs — measure here (perf task P1-perf); relates to gc/retention. |
| 21 | Killing a stray process needs the same care as killing an orchestrator; identify by command line first | irreducible (+ partially fixed-here) | Job objects shrink the population of lockstep-caused strays; the "don't kill the operator's browser" judgment can't be mechanized. Three lines in `/debug-run`. |
| 22 | Lost working-tree content is recoverable from `snapshot()`'s dangling blobs via `git fsck` | CLI-optional (C4) | True and useful — snapshots leave content-addressable copies of every dirty file. Optional `contrib/recover_blob.py <run_dir> --grep <phrase>`; otherwise a paragraph in `/debug-run`. Low priority. |
| 23 | Monitor via blocking tail of `events.jsonl`, not sleep-loops; always launch runs detached (foreground tool-timeout kills the whole tree) | **CLI** (C2) + fixed-here | Add `lockstep wait <run_dir> [--timeout]` — blocks until a terminal transition, exits with the run's meaning; replaces the fragile `tail -F | grep -m1` incantation on every platform. The kill-consequence half is version drift: here, driver death reaps the tree cleanly and `resume` recovers — by design, not by luck. |
| 24 | `max_run_minutes` is per-invocation (`time.monotonic()` at each run/resume start); budget exit can strand a just-finished node's result unrecorded (attempts++ but pending) | design + test (T1) | Per-invocation window confirmed (`roles.py:532,242-243`) and is the intended semantics — document in SPEC/skill, don't change. The stranded-result race is unverified here: write the repro test (budget trips while a node's future completes); if real, order result-recording ahead of the budget flag. |

### Graph-design items with independent mechanisms (not double-covered above)

| Item | Content | Verdict | Mechanism |
|---|---|---|---|
| GD5 | A mutating producer with no downstream readonly reviewer + deterministic gate has no closure — "use a reviewer" alone can't block a bad result | **lint** (L1) | `lint-ungated-mutation`: a write-capable work node none of whose transitive dependents is a gate. Statically checkable; advisory (investigatory flows are legitimately gateless — the lint text says how to silence: state it in `description`). The semantic-independence residue (reviewer fed producer's narrative vs primary evidence) stays prose. |
| GD9 | Pre-run dirty files inside a node's `spec.writes` will be legally overwritten; nothing mechanical checks overlap | **engine** (E9) | Preflight at `run`/`resume`: working tree dirty ∧ dirty path ∈ some node's declared writes → refuse with the list (`--allow-dirty-scope` to override). Cheap: verify already has the scopes; workspace already snapshots. Closes the "preserve user-owned changes" gap with a mechanism instead of a checklist. |
| GD4 | `spec.context` is "informational" by convention only; an LLM can't distinguish informational from imperative | irreducible | Correct observation. Residue: "review context files with the same rigor as task text; context narrows what a node is TOLD, never what it may WRITE." Two sentences in `/flow-authoring`. |

---

## Consolidated work items

> **Status 2026-08-11: P0 + P1 + P2 implemented** (see DEVIATIONS 2026-08-11
> for the batch entry; tests live beside each feature). Notes below record
> where the implementation landed differently than sketched.

**P0**
- [x] **B1 (lesson 19):** dispatch/`when`/revalidation treat `done ∧ needs_check`
  as unsettled (`roles.py::_dep_settled`; regression in `test_resume.py`).

**P1 — small, high-leverage engine/CLI**
- [x] **E1:** `contracts.py::describe_contract`, injected before the footer for
  `output:"json"` harness nodes; folds into `input_hash` (`prompt.contract`).
- [x] **E2:** fence salvage on the file result channel.
- [x] **E5:** heal text is now per-target and restates the target's scope.
- [x] **C1:** `verify --config`.
- [x] **V1:** `lint-missing-write-scope` + `lint-unscoped-writes`; PLUS
  `spec.writes` became presence-keyed (`[]` = enforced "writes nothing" —
  stronger than sketched; DEVIATIONS 2026-08-11). All committed flows scoped.
- [x] **T1:** the stranded-result race does NOT exist in this codebase —
  `_spend_spawn` precedes execute, recording follows unconditionally; pinned by
  `test_budget_trip_never_strands_a_completed_result`. Lesson 24's per-invocation
  wall window confirmed as intended behavior.

**P2 — features**
- [x] **E3:** `attempt-notes.md` carry (tail-capped 4 000 chars, folded into the
  persisted heal text; advertised by one footer line).
- [x] **E4:** `baseline: true` gates (pre-run findings subtracted on
  `(file, claim)`; verify errors `baseline-not-gate`,
  `baseline-gate-references-steps`) + `lockstep.gates.scoped_checks`.
- [x] **E6:** narrowed from the sketch — gates already retry timeouts via
  `retry`/the M4 auto-retry; the real gap was the misleading terminal reason,
  which now names the timeout and the remedy.
- [x] **E9:** dirty-scope preflight (fresh runs; `--allow-dirty-scope`).
- [x] **L1:** `lint-ungated-mutation` — accepts a gate/approval on EITHER side
  (an upstream approval is the evidence-approval deliver pattern); silenced by
  "ungated" in the flow description.
- [x] **C2:** `lockstep wait <run_dir> [--timeout] [--poll]`.
- [x] **V3:** `driver_version` in `state.json`; `resume`/`status` name drift.
  (Source digest + doctor integration deferred — version alone covers the
  observed folklore.)
- [ ] **P1-perf:** measure `snapshot()` cost growth across many resumes
  (lesson 20's duration creep) — still open; see ROADMAP-NOTES 2026-08-11.

**P3 — spec-level (r7 candidates, record seams in ROADMAP-NOTES)**
- [ ] **E7:** cross-lineage warm start (`--seed <run_dir>`, hash-keyed result reuse).
- [ ] **E8:** rollback scope narrowed to targets' declared writes (after V1 universal); interim: loud warning on restoring an undeclared path.

**Irreducible residue (the whole surviving prose, ~15 lines total)**
lessons 2 (probes side-effect-free), 5 (pass narrower than scope), 6 (repeat
adversarial runs), 9 (declare hand-patches), 10 (evidence-path discipline),
21 (identify before killing), GD4 (context files reviewed like code),
GD5-residue (feed reviewers primary evidence, not producer narrative) — plus
the standing rule: corrected claims are deleted, not annotated.
