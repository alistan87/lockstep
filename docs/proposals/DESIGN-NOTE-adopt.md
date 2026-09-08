---
type: proposal
status: draft
title: "Design note: `lockstep adopt` — settling a human-remediated artifact into a run"
description: The design questions that must be answered before `lockstep adopt` is built (S3, scheduled 0.12.0). Establishes what adoption is, the mechanisms it composes from, and five decisions the owner must make first — two named in the OW-07 response, one found in `prepare_resume` while writing this note, and two that fall out of them. Recommends an answer to each; nothing here is adopted until a commit says so.
resource: docs/proposals/DESIGN-NOTE-adopt.md
---

# Design note: `lockstep adopt`

**Status: draft — decisions open.** This is the note that
`docs/proposals/upstream-response-ow07-feedback.md` scheduled ahead of the
code ("design note first, then build"). It exists because two of the
questions are load-bearing enough that answering them inside a build would
mean answering them by accident.

**Responding to:** S3 (Sol, 2026-09-07) and its near-twin G2 (Gemini,
2026-09-08). The disposition accepted **Sol's semantics and declined
Gemini's** — that distinction is §1, and it is the whole design.

Verified against 0.11.0 (`981b294`) before writing; every mechanism named
below was read in the source, not remembered.

## 1. What adoption is, and the one thing it is not

The gap is real, and the OW-07 campaign showed the failure precisely: a gate
blocks, a human legitimately edits the artifact by hand, and then **every
road is bad**.

- `resume` re-runs the producer if its hash missed — and in the observed
  run it did, and it overwrote the human's edits.
- `run --seed` correctly refuses the dirty scope.
- `--allow-dirty-scope` waives the one protection standing between the
  producer and those edits (`cli.py:479` —
  `check_dirty_scope=not resume and not ns.replay and not ns.allow_dirty_scope`).
- Authoring a second re-review flow works, and severs lineage.

`adopt` closes that gap by recording a fact the engine has no way to hold
today: **this artifact in the tree is settled, a human put it there, and its
consumers have not seen it yet.**

What it is not — and this is the line between the two consumer requests:

| | Sol (S3) — adopted | Gemini (G2) — declined |
|---|---|---|
| unit | an **artifact** (paths in the tree) | a **node** (`failed` → `done`) |
| claim recorded | "a human produced this, outside the harness" | "the tree looks right now" |
| provenance | `source: external-approved-remediation`, never a harness result | the record says the node succeeded |
| consumers | explicitly re-run | inherit a result nobody produced |

Gemini's `adopt-node` would let *looks right* stand in for *the recorded
result is what produced this*, which is the exact provenance the journal's
hash chain exists to protect. Declined stays declined. If the demand behind
it resurfaces, the honest shape is a reason-bearing **failure waiver** that
leaves the node `failed` and unblocks its dependents — a different feature,
with a different name, and not this one.

## 2. Mechanisms it composes from (all shipping today)

Nothing in the sketch below needs new engine machinery. It needs a new
*command* that assembles parts that already exist, plus one additive record
field (D2):

| need | shipped mechanism |
|---|---|
| what changed in the tree | `GitWorkspace.dirty_paths()` (`workspace.py:168`) |
| the writer's permitted scope | `Engine._writes_of` → `render_scope` (`roles.py:981`) — the ONE reader of a declared scope |
| is a path inside it | `workspace.path_in_scope` (`workspace.py:26`) |
| refuse while a driver is live | `inspect_lock` / `acquire_lock` (`store.py:634`, `:662`) — the check `resume` already makes |
| wrong-tree refusal | `_same_root` against `RunState.repo_root` (`cli.py:514`) |
| tamper-evident record | `append_event` chained into `events.jsonl`, covered by `verify-trace` |
| the consumer cone | `Engine._dependents` |
| human-artifact retention | `gc.py:97` — the `rejection.txt` protection pattern |

The preconditions in §3 are, deliberately, the dirty-scope preflight's own
logic (`_preflight_dirty_scope`, `roles.py:730`) read in the other
direction: the preflight refuses because a dirty in-scope path *would be
overwritten*; `adopt` accepts exactly those paths and records that the
overwrite must not happen.

## 3. Sketch

```
lockstep adopt <run_dir> <writer-node> --reason-file owner-decision.md [--path <p> ...]
```

1. **Refused while a driver is live** (lock check, same as `resume`), and
   refused from a tree other than `RunState.repo_root` (same reasoning: the
   paths are relative to the recorded tree).
2. **Paths default** to the dirty paths falling inside the writer's declared
   `spec.writes`. Anything outside is refused. A dirty path that overlaps
   *another* writer's scope is refused — the preflight's logic, reused,
   because adopting a path a second node may legally overwrite records a
   guarantee the engine cannot keep.
3. **A reason is mandatory** (`--reason-file`, no inline `--reason`): these
   are the human's own words, and the cockpit's evidence rule is that a
   decision is read from an artifact, never from narration. Copied into the
   run dir as `adoption-reason.txt` and gc-protected like `rejection.txt`.
4. **Journals an `adoption` event**: per-path before/after blob hashes, the
   reason text, `source: "external-approved-remediation"`, the writer node
   id. Chained, so `verify-trace` covers it.
5. **Marks transitive consumers pending, explicitly** — not by leaning on
   reads-hashes, which cannot see a consumer that reads the artifact without
   declaring it (`reads.py`: an undeclared read stays invisible). Downstream
   reviews and gates then run unweakened on the next resume.
6. **Pins the writer** (D2) and **refreshes the fingerprint for the adopted
   paths only** (D3).

`status` and `explain` render the writer as **`settled-by-adoption`** —
never as a cache hit, never as `done` unqualified.

## 4. The decisions

Five. D1 and D2 are the two the OW-07 response named as the reason this note
precedes the code. D3 was found in `prepare_resume` while writing it, and is
the one that would have shipped as a bug. D4 and D5 fall out of D2.

### D1 — Result-text consumers

**The problem.** Adoption composes only when consumers see the artifact
*through the tree* (`spec.reads`, or the node simply opening the file). A
consumer that interpolates `{steps.<writer>.output}` consumes the **recorded
result text**, which adoption does not rewrite — so re-running that consumer
feeds it the model's superseded output while the file on disk says something
else. Silent, and worse than not adopting at all.

**What the code allows.** The taskgraph's reference forms are statically
parseable (`interpolate.extract_refs`, `_REF_RE`), so this is decidable
before anything is written. But `Engine._body_referenced_deps`
(`roles.py:426`) is **not the right scanner to reuse**: it covers
`spec.task`, `spec.cmd[]` and `node.over`, and deliberately excludes
`node.when` (A2). There are **five** interpolation sites in 0.11.0:

| site | executor |
|---|---|
| `spec.task` | `harness.py:233`, `fake.py:97` |
| `spec.cmd[]` | `shell.py:122` |
| `spec.args{}` | `flow.py:99` (a child flow's args) |
| `node.over` | map source |
| `node.when` | `eval_when` |

A scanner that misses `spec.args` lets a composed child inherit stale text;
one that misses `when` lets a stale value decide whether a node runs at all.

**Recommendation: refuse, and scan all five.** If the writer's consumer cone
contains any `{steps.<writer>.output}`, `{steps.<writer>.json…}`, or a
`{previous.output}` that resolves to the writer, `adopt` refuses and names
the node and the site. `--force` downgrades it to a warning and records
`forced_result_text_consumers: [...]` in the adoption event — because "my
consumer quotes the output in a heading" is a real and harmless case, and a
refusal with no override becomes a reason to reach for
`--allow-dirty-scope` instead, which is strictly worse.

**Open:** whether `--force` should exist at all. Against it: the house rule
that a scope you may violate is not a scope (G1a). For it: this is not a
scope but a *coupling warning*, and the fallback if we refuse absolutely is
the exact unsafe command this feature exists to replace.

### D2 — Pinning across a hash miss

**The problem.** "Do not re-run the adopted writer" is **not achievable by
inaction.** If the writer's own input hash missed — exactly the OW-07 case,
volatile upstream shell output — a plain resume re-runs it and overwrites the
adopted artifact anyway. `_settle`'s revalidation branch re-plans,
recomposes, compares, and re-pends on any difference; it has no notion of a
node settled for a reason other than its hash.

So the adoption record must settle the writer **even against a hash miss.**
That is a real, deliberately-scoped departure from *nothing is trusted
except the hash*: what is trusted here is the journaled human adoption
event.

**Recommendation.** One additive optional field on `PhaseRecord`:

```python
adopted: AdoptionRecord | None = None   # paths, reason, ts, event chain head
```

— additive and optional exactly as `terminal` (S6) and `repo_root` (fleet)
were, so an older driver reading a newer `state.json` degrades rather than
fails, and `format_version` 1.x semantics are untouched. `_settle`'s
revalidation branch short-circuits on it *before* re-planning:

```python
if rec.adopted is not None:
    invalidate = False
    rec.invalidated_by = None          # it did not hash-match; it was settled
```

and logs `settled-by-adoption <node> — adopted <n> path(s) on <ts>` at the
decision site, for the same reason the hash-miss branch logs there: an
operator watching a resume must not have to reconstruct it afterwards.

**Hash honesty.** The alternative — rewriting `input_hash` to whatever the
re-plan yields — is rejected outright. It would make `explain` report a
clean cache hit for a node that missed, converting one bad resume into a
permanently misleading record. The pin is visible, or it is not a pin.

### D3 — The M7 lineage-head fingerprint (found in code; not in the response)

**This one would have shipped as a bug.** `prepare_resume`
(`roles.py:479–509`) compares `RunState.fingerprint_detail` (path → content
hash at the last completed node) against the current tree. An adoption is a
human edit to the working tree, so **by construction it registers as an
external edit.** The next resume then warns and re-pends every `done`
cacheable node whose dependents are not all `done`:

```python
and (not self._dependents[node.id]
     or any(st.nodes[d].status != "done" for d in self._dependents[node.id]))
```

Step 5 of the sketch has just marked the writer's consumers pending. **The
writer therefore matches that condition and is re-pended — the precise
overwrite the whole feature exists to prevent**, arriving by a second road
while D2's pin guards the first. (D2's short-circuit lives in `_settle`;
this sweep runs in `prepare_resume`, before it.)

**Recommendation: refresh `fingerprint_detail` for the adopted paths only.**
The field is a per-path map, so this is natural and narrow: `adopt` updates
the entries for the paths it adopted and leaves every other entry alone.
Unrelated external edits made in the same window stay detectable and still
warn. The alternative — recomputing the whole fingerprint — would launder
every unrelated edit through the adoption, and the M7 warning is the only
mechanism that names them.

Belt and braces: the external-edit sweep should also skip nodes with
`rec.adopted is not None`, so the two paths agree even if a later change
moves one of them.

### D4 — What dissolves a pin

The OW-07 response says the pin is "dissolved by `--fresh` or
`--force-stale` like any other pin". Read against the code, that is
incomplete: both are `run` flags that start or seed a **new lineage**, and
the adoption record lives in the *old* run's `state.json`. Within the run
dir that holds the pin, nothing today releases it.

**Recommendation:** `lockstep adopt <run_dir> <node> --release`, which clears
`rec.adopted`, journals `{"kind": "adoption", "op": "release"}` (never
deleting the original — the journal is append-only and the history is the
point), and lets the node revalidate normally on the next resume. Symmetric
with the budget override's discipline: an operator decision leaves an
artifact, not a memory.

**Open:** whether `--release` is needed in 0.12.0 at all, or whether "start a
new lineage" is a sufficient answer for the first version. The cost of
deferring is that an adoption is irreversible within its run.

### D5 — `--seed` from a run containing an adoption

A seed serves a node whose `input_hash` matches (`seed.py`; the decision is
made in `plan()`). An adopted writer's recorded `input_hash` is the **stale**
one — D2 deliberately does not rewrite it — and its recorded **result text is
the model's**, not the human's; the human's work is in the *tree*, which the
new lineage inherits by standing in the same repo.

Two failure shapes follow. If the new lineage re-plans to the same hash, the
seed serves the superseded result text — D1's problem, arriving across
lineages. If it does not match, the writer runs and overwrites the adopted
file, with no pin in the new run to stop it.

**Recommendation for 0.12.0: name it, do not solve it.** `run --seed` prints
a warning naming every adopted node in the source run and what it implies:
*"`<node>` was settled by adoption in the seed source; this lineage will not
inherit that pin — `--force-stale <node>`, or re-adopt after the run."* The
pin does not transfer: a new lineage is a new consent, and silently
inheriting a human's decision into a run they did not authorise is the mirror
of the mistake in §1.

## 5. What this does not touch

- **No exit-code change.** A refusal from `adopt` is a config refusal — exit
  7, the code that already means *the run was refused, and here is why*.
- **No hash-composition change.** `adopted` rides `state.json` only, like
  `repo_root` and `terminal` before it. The replay fixture must pass
  un-re-recorded; if it does not, something is wrong with the change, not
  with the fixture.
- **No `format_version` change** — one additive optional field.
- **No weakening of gates.** Consumers re-run unweakened; a gate that blocked
  before the human's edit gets a fresh look at the edited artifact, which is
  the entire point.
- **A DEVIATIONS entry is required on build.** D2 is a deliberate departure
  from "nothing is trusted except the hash", and that is exactly what the
  register is for.

## 6. Acceptance tests

The consumer's four, plus the ones this note's own findings demand. All
zero-token (fake executor + a git tmp tree).

1. Adopt an artifact, resume → the writer does **not** re-run; its consumers
   do.
2. **D2:** the same, with the writer's input hash forced to miss → the writer
   still does not re-run, and `status`/`explain` say `settled-by-adoption`,
   never a cache hit.
3. **D3:** adopt, then resume → the M7 external-edit sweep does not re-pend
   the writer, *and* an unrelated external edit made in the same window still
   warns and names its path.
4. **D1:** adopt where a consumer interpolates `{steps.<writer>.output}` →
   refused, node and site named; `--force` proceeds and records the override.
   One case per interpolation site, `spec.args` and `when` included.
5. A path outside the writer's `spec.writes` → refused. A path inside
   *another* writer's scope → refused.
6. Adopt under a live lock → refused, nothing written. Adopt from the wrong
   repo root → refused, both paths named.
7. `verify-trace` passes over a run containing an adoption event.
8. **D5:** `--seed` from a run containing an adoption → warning naming the
   node; the pin does not transfer.
9. A 0.11.0 driver loading a `state.json` containing `adopted` → ignores it
   and degrades to old behaviour (the `RunState` unknown-field property,
   pinned since 2e1fdba).

## 7. What the owner is being asked

| | question | recommendation |
|---|---|---|
| D1 | refuse on result-text consumers — and does `--force` exist? | refuse; yes, with the override journaled |
| D2 | pin across a hash miss via an additive `adopted` record | yes; never rewrite `input_hash` |
| D3 | refresh `fingerprint_detail` for adopted paths only | yes — otherwise the feature defeats itself on the first resume |
| D4 | ship `adopt --release` in 0.12.0, or defer | ship it; a pin that is irreversible within its lineage is a trap |
| D5 | seed inheritance of a pin | do not transfer; warn loudly |

D3 is the only one where a wrong answer produces silent data loss rather than
an argument.
