---
type: notes
title: Roadmap notes from dogfooding
resource: docs/notes/ROADMAP-NOTES.md
---
# Roadmap notes from dogfooding

> **Status: all four items below were adopted in `AMENDMENTS-r5.md`**
> (2026-07-25: B1 per-stanza digests, B2 default harness retry, A3 resume
> wording, A1 readonly footer) and are implemented. Kept for provenance.
> Remaining future work lives in SPEC §16 (v2 roadmap): structured progress
> (16.1) and steer/cancel (16.2) are the recommended next items.

**r7 candidates from ADDENDUM-A adoption (2026-07-26):** a dedicated
per-node write-scope field — *implemented 2026-08-02 as the optional
`spec.writes` key plus a new `LOCKSTEP_WRITE_SCOPE` env var, deliberately
NOT by repurposing `LOCKSTEP_WORKSPACE_SCOPE` (which stays the resolved
`spec.cwd`, since the reference extension prefix-matches a single directory
against it); see DEVIATIONS* — and reconciling §6.11 with extension-based readonly
enforcement (an argv-visible flag that loads the enforcing hook), which would
finally allow `spec.readonly` nodes on pi.

**New r7 candidate (2026-07-26) — re-run isolation.** A resumed audit's
re-spawned readonly reviewers found their own rotated prior-attempt output
(via the phase-dir pointer in the readonly footer) and RECYCLED their stale
findings — verbatim old code quotes against a fixed tree — instead of
re-deriving them. Mitigated in v0.2.x (readonly footer drops the phase-dir
pointer and asserts fresh execution; audit prompts add a fresh-review guard),
but the general principle deserves spec text: prior-attempt artifacts should
not be discoverable by a re-executing agent, or must be explicitly marked as
non-input. Related: run dirs under the repo root are visible to agents with
read tools; consider defaulting `--runs-dir` outside the audited tree.

**New r7 candidate (2026-07-28) — a corrective re-spawn can exceed the Windows
command-line limit, and the spawn error is then masked.** *Both defects fixed
2026-08-02 (see DEVIATIONS); an r7 amendment should still state the argv rule
in §8.5.* Observed live: an
extract node emitted an 11 KB result that failed contract validation; r5 A2's
corrective prompt (original prompt + invalid output + validation error) came to
59,028 chars; the stanza used `prompt_via = "argv"`; Windows `CreateProcess`
caps a command line at 32,767. The spawn never started. Two defects, and the
second is what made it expensive to diagnose:

1. **No guard.** Nothing checks the rendered argv length before spawning. r5 A2
   deliberately made corrective prompts several times larger than the original,
   so this is now reachable on any node whose invalid output is sizable — on the
   primary development platform.
2. **The diagnosis is discarded.** `harness.execute` correctly returns
   `RawResult(exit_code=127, error="spawn failed: …")`, but
   `_validate_with_respawn` ignores `raw2.error` and reports only the
   ContractError from `validate_result(raw2.result_text or "")` — so the operator
   sees `result is not valid JSON: Expecting value: line 1 column 1 (char 0)` for
   a process that never ran. The early return also means the stale `result.json`
   from the previous attempt is never read, which is why the symptom is an empty
   result rather than a repeat of the first schema error.

Candidate fix: check the assembled argv against a platform limit at plan or spawn
time and fail with a named error naming `prompt_via = "stdin"` as the remedy;
and surface `raw.error` in the "contract validation failed twice" message
whenever the re-spawn itself failed. Workaround today is config-only — set
`prompt_via = "stdin"` on any stanza whose nodes can produce large outputs.

**New r7 candidate (2026-07-27) — a JSON string interpolated into shell argv
carries its quotes.** *Fixed 2026-08-02 by explicit decision to touch §7 (see
DEVIATIONS); an r7 amendment should formalize it.* §7 defines `{steps.X.json.field}` as "parsed,
compact-re-serialized", so a STRING field renders as `"…"`. That is right for a
prompt (the value is data) and required for `when` comparison semantics, but a
shell node's argv element is already a discrete string, so the quotes become part
of the value — a path arrives as `".chronicle/resolved/ch0001.json"` and the
program opens a file whose name starts with a quote. `interpolate.py` already
makes the opposite choice one function away: `{item.field}` inserts strings raw,
commented "prompt-friendly". Candidate fix: render strings raw when `fence=False`
(shell argv), leaving prompts and `when` untouched. Not done unilaterally — §7 is
a frozen surface. Found while wiring a gate's contract pointer into a downstream
shell node; the failure surfaces as a confusing file-not-found far from the flow
file.

**New r7 candidate (2026-07-27) — heal text is not resume-stable.**
*Implemented the same day (see DEVIATIONS); an r7 amendment should formalize the
rule beside r6 C2's.* A gate's
heal text (the block reason plus the fenced findings) is appended to each
target's prompt and folds into `input_hash`, but it lives only in the Runner's
in-memory `heal_texts` dict (`roles.py`); `RunState` persists `heal_round` and
`heal_baselines`, not the text. A resume in a fresh process therefore re-plans a
healed node WITHOUT it, computes a different hash, and re-runs a node that had
already healed and passed. It is silent — the node looks like an ordinary cache
miss — and the cost scales with heal rate × already-completed targets, so it
grows with run length. r6 C2 solved exactly this shape for steering: it renders
the ENTIRE mailbox, consumed and unconsumed, precisely so a resume re-plans the
prompt the spawn actually saw. Heal wants the same treatment — persist per-node
heal text in `RunState` (latest round wins) and render it at plan time. Pinned
by `tests/test_heal.py::test_healed_node_hash_is_stable_across_resume`
(xfail-strict). Found while designing a long chain flow, where every resume
re-ran every heal-touched node in the completed prefix.

Observations from live multi-model runs on 2026-07-25 that suggested
spec-level refinements:

- **Per-stanza executor-config digests.** §8.2 hashes the WHOLE lockstep.toml
  into every harness node's fingerprint. During a sustained Haiku 529 outage
  this meant repointing the one broken stanza would have invalidated completed
  nodes that used *other* stanzas — the expensive Opus review would have been
  re-billed to fix the cheap Haiku one. Hashing only the stanza a node
  actually resolves (plus the `default` key when relied upon) preserves the
  §0.1.4 invalidation guarantee at stanza granularity.
- **Default retry for harness kinds.** Transient provider errors (429 session
  limits, 529 overload) surface as nonzero exits; the M4 automatic retry
  covers only timeouts and empty results, and `retry.max` defaults to 0. A
  kind-level default (e.g. harness ⇒ `max: 2`, minute-scale backoff) would
  match reality; today the burden is on flow authors (see /flow-authoring).
- **Resume-vs-archived-flow wording for §9.2.** Resume replays the run dir's
  archived `flow.tg.json` (DEVIATIONS entry); a future amendment should state
  this in the spec proper — the "editing a flow starts a new lineage" sentence
  reads as if `resume` compares against the edited working file.
- **Readonly result-channel formalization.** `FOOTER_READONLY` (DEVIATIONS
  entry) works, but the spec's §7 footer contract should acquire the readonly
  variant officially rather than by deviation.

---

## §15 answered (owner decision, 2026-08-10)

**Both — and the build-loop phase is the one we are in.** Lockstep is a
build-loop tool for driving coding agents *and*, later, the runtime executor
for domain work; the domain system does not yet have a working graph or a real
DAG to run, so the tool is being built first and the domain seams stay
deferred.

What that settles, and what it does not:

- `pyfunc` / `action` executor kinds and a graph-backed `Store` (§16.3) are
  **not cancelled** — they are waiting for a real second consumer. The rule of
  two still holds: do not design `Workspace` / `Store` / `Policy` against an
  imagined one. §15's own instruction, *revisit when the domain system has a
  working graph*, is unchanged.
- `Policy` stays the `AllowAllPolicy` no-op until domain work needs teeth.
- Nothing in the core (§8.1) moves, which is why the question could stay open
  through v1 in the first place.

The practical consequence for now is a tie-breaker rather than a roadmap item:
where a design choice trades build-loop ergonomics against domain-runtime
generality, **take the ergonomics** and record the seam.

**New r7 candidates (2026-08-11) — from the LESSONS-TO-MECHANISMS batch**
(docs/notes/LESSONS-TO-MECHANISMS.md; the P1/P2 items there are implemented,
see DEVIATIONS 2026-08-11; these are the deliberately deferred seams):

- ~~**E7 — cross-lineage warm start.**~~ SHIPPED 2026-08-12 as
  `run <flow> --seed <run_dir>` (DEVIATIONS 2026-08-12). Provenance landed as
  proposed — `seeded_from` on the record, a `kind: "seed"` journal line, a
  `seeded:` line in `status`. What is left for r7 is the SPEC text: §9.2's
  caching story now has a cross-lineage case, and the three limits (shell
  nodes, map items, failures) are stated in DEVIATIONS but not in the spec.
  The open sub-item is per-ITEM seeding for maps, which needs the engine to
  hand the executor its item index at plan time.
- **E8-full — narrow heal rollback to declared scopes.** §9.4.4 restores every
  path changed since the gate's baseline; with `spec.writes` now on every
  committed flow (V1), rollback could restore changed ∩ (targets' declared
  writes) and leave operator out-of-band edits standing. Needs an amendment
  (the current behavior is stated spec); the interim loud warning
  (`restored-undeclared` events) ships now. Watch for the warning firing in
  practice as the evidence for the amendment.
  **Decision 2026-08-12 (owner): still waiting, deliberately.** No run has
  produced a `restored-undeclared` event yet, and amending stated spec text on
  a hypothesis is the thing this note exists to prevent. The trigger is
  explicit: the FIRST real instance is the evidence. Whoever sees one, bring
  it here — do not re-open this on reasoning alone.
- ~~**Interpolated write scopes.**~~ SHIPPED 2026-08-12 (DEVIATIONS
  2026-08-12), the first of the two options: scopes render through the run's
  args at plan time. `{steps...}` is refused (`dynamic-write-scope`) — a scope
  the graph can widen by writing a different answer is not a permit.
  `evidence-approval` now scopes to `{args.deliverable}`; `implement-heal`
  keeps `["**"] + rationale`, correctly — a generic implementer told which
  files to touch by `--arg task` is not expressible even with args, which is
  what the escape hatch is for.
- **V1 promotion.** `lint-missing-write-scope` becomes a verify ERROR at
  format_version 1.1 (a mutating node must declare its scope; `[]` and
  `["**"]+rationale` are the honest outs). All committed flows already comply.
  **Decision 2026-08-12 (owner): hold at lint.** Every flow HERE complies, so
  the error changes nothing locally — its entire effect lands on flows
  elsewhere, and the work-repo mirror is imminent. Promoting a lint to an
  error in the same week a downstream copy is being synchronised turns one
  migration into two failures. Revisit once the mirror is on this version and
  its own flows are scoped.
- **Gate-duration drift under orchestration (lesson 20).** Measured
  2026-08-12; see LESSONS-TO-MECHANISMS P1-perf. `snapshot()` costs O(tree
  bytes) on EVERY call because the temp index is fresh and has no stat cache
  (1.37 s for a 47 MB tree here; a run's own output grows the tree it pays
  for). The engine now journals `kind: "timing"` lines so a slow run can be
  read rather than guessed at, and `contrib/snapshot_bench.py` reproduces the
  cost. Two open decisions came out of it:
  - **Seed the temp index from the repo's real one** — 12–27× faster, tree
    byte-identical in every measured round. Costs a soundness property: git's
    stat cache would stage a rewritten-but-same-size-and-mtime file at its old
    blob, and write-scope detection would miss that violation. §9.4.2 states
    the fresh-index mechanism, so this is an amendment-or-DEVIATIONS call, not
    an implementation detail. The honest framing: lockstep already trusts the
    stat cache in `dirty_paths()` (`git status --porcelain`), so the question
    is whether the ENFORCEMENT path deserves a stronger guarantee than the
    advisory one.
  - ~~**Stop taking two snapshots per rollback**~~ — DONE 2026-08-12. Both
    methods take an optional `current: SnapshotRef`; the rollback path
    snapshots once and passes it to both, halving a heal round's tree work.
    It also tightened §9.4.4: the preserved patch and the restore scope now
    describe the SAME tree, where two snapshots described two moments and a
    file written between them would have been restored without appearing in
    the evidence.

**New r7 candidate (2026-08-12, adversarial review of the batch) — map items
are the one unguardable mutator class.** A map node cannot declare
`spec.writes` (`write-scope-on-map`: the items share one tree and one diff),
gets no quarantine, and neither V1 nor L1 names it — the exact class V1 exists
to catch, and the work repo's codemod-apply map is a live instance. Per-item
scopes need per-item baselines (concurrency-1 maps could take them between
items); until then the honest guidance is the factory pattern: readonly items
that EMIT orders + one serialized, scoped applier. A lint was considered and
rejected — it would fire on the canonical codemod-apply flow (the W2 rule:
a warning that is wrong on the flow it teaches is one people learn to skip).

- **2026-08-15 (whole-implementation review): per-stanza `default_retry`.**
  Retry defaults are per-KIND (`HarnessExecutor.default_retry` = 2 x 60s),
  so every copilot node must carry `"retry": {"max": 0}` by hand forever -
  the request-metered posture cannot live in the stanza where it belongs.
  The trap in fixing it: `stanza_digest` hashes `model_dump()`, so ADDING a
  field to ExecutorStanza changes every stanza's digest and re-bills every
  cached harness node on upgrade. Doing this needs a digest-migration story
  (e.g. exclude-if-default serialization) decided FIRST - which is why it is
  a note and not a patch.

- **2026-08-15 (chronicle composition adoption): flow-node config digest is
  whole-file, not per-stanza.** `FlowExecutor.plan()` folds `ctx.config_digest`
  (sha256 of the raw bytes of the entire `lockstep.toml`) into every flow
  node's hash, where a harness node folds only its own stanza's digest
  (AMENDMENTS-r5 B1). A client that composes N children (chronicle: one per
  book chunk) therefore re-bills ALL of them when any byte of the config
  moves - a comment, an unrelated stanza - which is exactly the class of
  no-op edit B1 made free for flat flows. Chronicle guards it with a
  preflight tripwire (loud stop + explicit ack), but the seam is lockstep's:
  a flow node cannot know which stanzas its child uses without loading the
  child at plan time - which it already does. Narrowing the digest to the
  union of the child's referenced stanzas (+ default) would restore B1
  parity; the trap is the same digest-migration story as per-stanza
  `default_retry` above - decide that first.

- **2026-08-15 (pi-gemma chronicle run forensics): corrective re-spawns can
  feed the model fence-salvaged rubble instead of its own output.** When a
  spawn's outer JSON object fails raw_decode from one corrupted token (live
  instances: `"char_span": [4177, 418ff]`, a key mangled to `larance_span`),
  `extract_last_json` falls through to the last balanced INNER value - here
  `"schema_observations": []` - and the corrective prompt then fences `[]`
  as "previous invalid output" for a ~200-line delta that was 99% good. The
  model recovers by full re-derivation, not correction, and the operator
  reads "produced nothing" in the logs when it produced nearly everything.
  Candidate fix: when the salvaged value validates as NOTHING against the
  contract but raw stdout contains a longer near-object, embed the raw text
  (or the longest decode prefix) in the corrective fence instead. Needs care:
  the fence must stay within max_interp_chars and the §7 fencing contract.
