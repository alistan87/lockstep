# Changelog

The spec and its amendments are the authority on behaviour
(`docs/spec/SPEC.md` + `AMENDMENTS-r4/r5/r6/r7`, later wins);
`docs/spec/DEVIATIONS.md` records implementation-level departures. This file
is the release-facing summary. Versions before 0.9.0 predate it — their
record is the git history and the proposals under `docs/proposals/`.

## Unreleased

**Per-node spawn ceiling** (OPEN-WORK item 10, first slice; DEVIATIONS
2026-09-24). New optional `budget.max_spawns_per_node`: a second ceiling
under `max_agent_spawns`, per node and per map item, counting every
token-costing spawn in the lineage — including the automatic
timeout/empty-result retry that `retry.max: 0` never disabled. A trip fails
that node or item with the cap and the last attempt's own reason, spends
nothing, and leaves the wallet to the rest of the graph; `status` prints a
`spawn cap:` line and the way out (revise the flow, `run --seed`). Once a
map's width is known the engine also warns (and journals `op: "forecast"`)
when the minimum spawns still required exceed what the wallet has left.
`verify --lint` warns (`lint-spawn-cap-below-heal`) when a cap cannot
cover a gate's heal rounds or baseline spawn. Absent the key, nothing
changes.

**`--detach` no longer guesses how long start checks take.** The parent
waited a fixed 3 s for a refusal; under load a dirty-scope preflight took
longer and the parent reported a clean launch of a refusing run. The driver
now journals `op: "preflight-passed"` and the parent waits for it (30 s
safety net, which says so rather than claiming success). **`lane.py start`**
waits through a Windows `PermissionError` on its start lock instead of
crashing the second of two concurrent starts.

## 0.18.0 — 2026-09-20

**The instrument release: two small pieces of `docs/notes/OPEN-WORK.md`,
built and adversarially reviewed** (two passes, engine and record lenses;
19 findings, no blockers, one high-severity defect in the first cut caught
and reproduced before commit). Item 8: `gc` and
`explain --graph` name a run whose recorded repo root no longer exists (a
harvested fleet lane) — `gc` prints `root gone:` per candidate, and for kept
runs a summary count followed by one line each, without changing what it
keeps; the graph dry
run says so ahead of its per-node verdicts so "every node moved" reads as
the tree difference it is. Item 14's named first step: the engine journals
`op: "dispatch-wait"` per dispatched node — how long it sat ready behind its
wave's barrier and which dependency made it ready — and `status` sums it
into one `dispatch wait:` line. That is the evidence the deferred
event-driven dispatch trigger asks for and could not previously see; the
deferral itself is unchanged. A heal round's re-pend is itself the
readiness event (the pre-commit review caught the first cut reporting a
target's whole first attempt as a barrier wait), and a dependency with no
settle time in this process is unmeasured, never zero.

## 0.17.1 — 2026-09-20

**Cut as 0.17.0, shipped as 0.17.1.** Tagging the release found a defect in
the flow that does the tagging, so the 0.17.0 tag was withdrawn and this
entry carries both. Everything below shipped together; there is no 0.17.0 to
upgrade from.

**The open-work release.** `docs/notes/OPEN-WORK.md` ranked every
actionable item recorded across the roadmap notes, the lessons file, the
proposals index, and the deferral sections of the adopted proposals — 23
items in three tiers, plus a second list of recorded seams that are
deliberately not ranked. Six of the seven ranked as worth doing were then
built in rank order across three commits and four adversarial review
passes (39 findings, two blockers, every confirmed one folded in); the
ledger itself took two more passes before it was trusted to rank anything
(22 findings). Item 6, persona composition, stays parked on its trigger,
and the tiers say in plain words why every deferred item is deferred.

Two consequences beyond the item list. The upstream MISSION request is
**closed**: S1 through S4 are built, and the response document's "still
open" section is now empty. And the claude-code stanza no longer carries
an unhashed instruction channel, which was the last harness where
`explain`, `--seed` and `--replay` could be quietly wrong about what a node
was told.

The work mirror's integration steps are
`docs/proposals/passdown-0.17.0-work-repo.md` — the changed surface, the one
machine-local edit (`--safe-mode`, and the one-time re-bill it buys), the
`seeded:` line's new shape, and a verification checklist.

The top three of `docs/notes/OPEN-WORK.md`, built in order, then two
adversarial review passes before commit (engine and spec lenses; 23
findings, no blockers, every confirmed one folded in). Two of them were
engine fixes worth naming: a heal round used to reset a map item's
attempt counter, so round 1's quarantine evidence overwrote round 0's
(the counter now survives the reset, as a work node's always did); and a
cancelled attempt that left out-of-scope writes was billed a corrective
re-spawn that r6 C3 says it must not consume — the quarantine still runs,
the corrective no longer does, on the single-node path and the item path
alike.

**The claude-code stanza closes its unhashed instruction channel.** Five
live controls from this repo root (claude 2.1.270): a baseline headless
spawn loaded this repo's `CLAUDE.md` and all five of its `.claude/skills`;
`--safe-mode` reduced that to none of either, with subscription auth intact
and a writing spawn under `acceptEdits` still saving its file.
`--disable-slash-commands` dropped the skills but left `CLAUDE.md` loaded;
`--bare` stays rejected (it restricts auth). `lockstep.toml.example` carries
the flag and the controls; DEVIATIONS 2026-09-19 records it. Your live
`lockstep.toml` is yours to flip — it re-bills claude nodes once, by design.

**AMENDMENTS-r7.** A delta amendment, adopted, that restates into spec text
what DEVIATIONS had been carrying as "an r7 amendment should state this":
`spec.writes` and the quarantine, persisted heal and steer text, raw string
leaves in shell argv, the argv-length guard, the fingerprint part list as
composed, cross-lineage seeding, the recorded run root, the journal hash
chain, the readonly footer's fresh-execution rule, `heal.on_exhausted`,
shell nodes on the `tree` token, and the stanza's duty to close harness
auto-discovery. Every section names the deviation it restates; none
changes behaviour. Authority order is now r7 > r6 > r5 > r4 > SPEC.

**Maps can declare a write scope.** `write-scope-on-map` is gone: every
write-capable item already serialized on the `tree` token, so the engine
takes a baseline per ITEM inside it and runs the single-node sequence on
each — diff, quarantine, one corrective re-spawn, touched evidence — with
the item's own attempt counter, evidence under `phases/<map>/items/<i>/`,
`item` on the journal lines, and the four evidence fields on `ItemRecord`.
The scope is the map's (`{args.NAME}` only; never `{item…}`).
`lint-missing-write-scope` now covers maps; `codemod-apply` declares
`["**"]` with its rationale; `triage-intake`, `map-summarize` and the
repo-hygiene demo declare `[]`. Closes
ROADMAP 2026-08-12 ("the one unguardable mutator class").

Two adversarial passes before the fix-up commit (engine/config and cockpit
lenses; 16 findings, two blockers, both in the conditions line). The
first cut counted every step blocked BEHIND a failure as its own
condition, multiplying one problem by its fan-out, and matched `timed
out` where the engine writes `timeout` on a node; the line now follows the
engine's dependency texts and gate texts exactly, and an optional map's
tolerated item no longer reads as blocking. The drawer's attempt labels
refuse to guess when an attempt left no result; per-attempt tool counts
are numbered within each map item; the guide names every new word.

**Map items are seeded per item.** The E7 limit is withdrawn: the engine
hands each item's composed hash to the seed after planning and before any
spawn, so an unchanged item is served at zero budget and a changed one
runs. Provenance is per item (`seeded_from` on the item record, `item` on
the journal's seed line, `m[0], m[1]` on the `status` line); a partly
served map never reads as seeded itself.

**`[driver] runs_dir`.** One rule for where runs live — flag, then the
config key (relative to the config file), then `./runs` — asked by `run`,
`doctor`, `gc`, `active`, every cockpit tool, and the pane through
`mission_view.py --runs-root`. Hash-neutral; the default did not move. The
example config recommends pointing it outside the audited tree and says
why.

**The last accepted MISSION slices (S2, S3, S4).** A `blocking conditions`
line under the headline counts what the engine itself said went wrong, in
its own categories, beside the review-findings line and never inside it.
The step drawer gains `findings across attempts` — consecutive recorded
results compared by finding identity into new, persisting, resolved and
severity changed, attempts labelled by the journal's recorded cause, `not
comparable` when a side is not a findings shape — and the agent block
gains a `per attempt` tool-activity line that says `not reported` rather
than `0`. Vocabulary added to the domain-expert guide.

**The release-cut tag node says what it did.** The first cut of this release
left that node `failed` with the tag correctly created: bare `git tag` is
silent on success, a silent success leaves no result channel, and the engine
fails a resultless attempt whatever its exit code — so it auto-retried into
"tag already exists". `contrib/git_tag.py` prints one line naming the commit,
tag and kind, and is idempotent on the same commit so a retry cannot
manufacture the failure; a tag pointing elsewhere is still a hard refusal. The
engine half — whether exit 0 with empty stdout should pass for a work node —
is OPEN-WORK item 24, recorded and not taken, because it touches the §7/§8.3
result-channel contract.

The tag it makes is **annotated** unless you ask for `--lightweight`, since
v0.9.0 and v0.16.0 are and the withdrawn 0.17.0 tag only was not because the
node said nothing; `release-cut` takes an optional `message` arg for a real
annotation and falls back to the tag name. Its output is ASCII because that
stdout is the node's result channel: a piped CPython on Windows encodes with
the locale codec, so an em dash arrived as invalid UTF-8 and was recorded as a
replacement character.

## 0.16.0 — 2026-09-12

The MISSION UX work order (`docs/proposals/mission-ux-work-order.md`),
built end to end: three batches, three adversarial review passes (26
findings, all confirmed ones adopted), and the §7 render-and-look step —
which caught a defect no unit test could see. Closes **S1.5** and builds
the **blocker-path half of S2** from `upstream-response-mission-scale.md`.
Contrib + tests + guides only; no frozen surface touched.

**The stopped run tells the truth (Batch 0).** A failed run's clock
freezes at its last `ended_at` (it grew forever — the live release-cut run
read "stopped with a problem - 87 h 37 m" and counting), and the pane's
mirrored clock freezes with it. A failed run gets a **blocker card** above
the fold: the failing step, the engine's error verbatim and named as the
machine's words, "tried once" (or "1 of 3 items stopped" for a map — a map
parent's attempt counter is never incremented), the stalled-behind
dependency count (per entry, never summed), and the guide's own next step
as the last line. And the page now asks the engine's one liveness decider
(`lockstep.state.inspect_lock`): a dead lock over an unfinished run renders
**"stopped unexpectedly"** — hero, card with the lock's words verbatim,
chip, rail, and every time surface frozen at the journal's last line —
instead of a healthy "running" over a corpse, which is what a session-limit
kill used to look like. A copy that cannot import the package names that
absence rather than rendering healthy.

**One number per fact (Batch 1).** The spend meter merged into the
"agent tasks used" tile (bar, ceiling mark, and the consent sentence in the
tile foot); the two cost disclosures became one, with an
"every attempt / kept only" switch — both bodies still server-rendered and
stacked with JavaScript off. The l0/l1 switch machinery generalised to N
groups, membership derived from the server-rendered buttons.

**Detail on demand (Batch 2, closes S1.5).** Clicking a step's name opens a
**peek panel** — `/api/node/<id>` grows one `html` field, the same
`node_drawer` renderers plus the glossed raw-record table, injected
verbatim into an `<aside>` outside the swapped wrap. Open panels follow a
live run (swap-only-when-changed, scroll and selection preserved, a
generation counter against races, transient network failures tolerated);
no-JS keeps the fragment jump. Above `DRAWER_INLINE_MAX` (30) **settled**
drawers, the settled ones degrade to a named absence — failed, blocked and
running drawers keep full bodies at any size, the board's loud-first
collapse rule applied to L2. Measured: 64,160 → 21,180 B of drawer HTML
per swap at 120 nodes; sub-threshold renders are byte-identical.

**For contrib consumers:** `cost_report.collect_run` gains an optional
`now` (default keeps live behaviour) — the seam that lets a surface freeze
node time for a vanished run. `mission_view` gains `driver_presence`,
`driver_vanished`, `blocker_summary`, `stalled_behind`.
`mission_bench` gains the `node_fetch` cold/warm centre;
`docs/proposals/mockups/make-look-samples.py` renders the three stopped
states plus an over-threshold run for the look check.

## 0.15.0 — 2026-09-11

S1.2 (one projection per render) and the environment gate — the two
follow-ups the downstream adoption of 0.14.0 argued for.

**S1.2: the journal is parsed once.** `render_wrap` read `events.jsonl`
three times per render — for the feed, inside `collect_run`'s wall/heal
pass, and again inside `_intervals` for the timeline — which profiling put
at 77% of everything a warm render still touched after the caches landed.
It is now parsed at the top and handed down; `_intervals`, `_heal_marks`
and `collect_run` all take an optional pre-read journal and keep their
standalone behaviour for every other caller. `_trace_status` (a whole-file
re-chain, asked for twice per render by the chain chip and the feed's raw
record) is memoized on the journal's identity.

Warm render on a 40-run / 3,000-event fixture: **1,750,760 B → 378,260 B**,
a 78% cut. Cold: 3,326,312 B → 2,228,312 B.

**The environment gate.** `contrib/portability_check.py` runs the suite the
way a consumer gets it: `git clone` HEAD into a temp dir (tracked files
only — no `lockstep.toml`, no `cost-fields.toml`, no `runs/`), build a venv
under any named interpreter, install, run. Both 0.14.1 defects were
invisible here because every adversarial round ran on one interpreter
inside a working tree carrying gitignored files. Running it is now a
pre-tag rule in CLAUDE.md.

It taught something on its first run: `git archive` gives tracked files but
not a *repository*, and lockstep needs one — so it reported a failure no
consumer would ever see. A clone is the consumer's actual condition.

Priority for the remaining slices is the consumer's: S1.5, then S2/S4.

## 0.14.1 — 2026-09-11

Two defects found by the downstream consumer adopting 0.14.0, both of them
mine, and both invisible on this machine — which is the point worth
recording.

- **`JSONDecodeError.pos` moved in CPython 3.13.** Through 3.12 it reports
  the CLOSER (`{"a": 1,}` → `}`); on 3.13 it reports the COMMA. The
  deletion-only repair trusted the 3.12 shape, so on 3.13 every dangling
  comma went unrepaired and fell through to a corrective re-spawn — a
  billed request on a metered harness, silently. The comma is now located
  from BOTH directions and yields the same index either way; `[1,,2]` stays
  refused on every version. Pinned by tests that simulate the 3.13 decoder,
  so the fix is proved without needing that interpreter.
- **A test depended on a gitignored file.** `cost_absence` branches on
  whether `contrib/cost-fields.toml` exists; the tag ships only the
  example. The test passed on any machine that happened to have one and
  failed on a clean checkout. It now injects its own condition — a test
  whose outcome depends on an ambient untracked file is testing the machine.

Neither could have been caught here: this repo runs 3.11 and carries a
local `cost-fields.toml`. Four adversarial rounds did not find them because
every round ran on the same interpreter with the same untracked files.

## 0.14.0 — 2026-09-11

The measure-first release. MISSION scale (`upstream-response-mission-scale.md`, S1 + S3's engine half),
built after 0.13.0 was cut. A downstream consumer reported the page slowing
down after weeks of use; the same seam had been found upstream the same day
and logged in ROADMAP-NOTES. Every source-level claim in their report was
verified before anything was classified — all five were accurate.

**Phase 0 first, deliberately.** `contrib/mission_bench.py` measures each
cost centre in BYTES (wall time on a Windows box with AV is not comparable
across machines) and reports cold/warm pairs. Five plausible cost centres
had been named across two reports; optimizing the wrong one is placebo work
that still costs a review. It immediately corrected one of the maintainer's
own assumptions — drawers cost zero as the page calls them.

Measured on a 40-run / 3 000-event fixture, cold → warm:

| centre | before | after |
|---|---|---|
| quiet heartbeat | 274,500 B, ×16 across a ×16 sweep | **4,096 B, constant** |
| run rail | 19,584 B / 12 files | **0 B / 0 files** warm |
| usage walk | 1,853,825 B | **278,273 B** warm |
| full render | 3,538,276 B | **1,750,760 B** warm |

- **The journal cursor reads what was appended, not the file.** An opaque
  `<gen>.<offset>.<ordinal>`; `gen` digests the first line so a rotated
  journal resets explicitly. Byte offsets make SPEC §10.3's torn-trailing-line
  tolerance structural rather than a special case. The route's own docstring
  had called the whole-file read unavoidable, which is why nobody questioned
  the cost.
- **The attempt-log memo** keyed on `(path, size, mtime_ns)` — logs are
  append-then-rotate, so that is a sound identity. A `--sweep --axis depth`
  across **254× more log bytes** (a run resumed and retried for weeks) leaves
  the warm render **flat at ×1.0**.
- **The two-layer rail cache.** Membership keys on the runs-root mtime;
  status keys on each run's OWN `state.json`, because writing inside a child
  does not reliably bump the parent — the downstream reviewer's catch, and
  the reason a single-layer design never sees running→done.
- **`kind:"attempt"` journal events (S3, engine).** The request asked for a
  per-attempt manifest artifact; upstream counter-proposed journal events,
  because the journal is already hash-chained, kind-tagged and
  forward-tolerant, and an attempt record is engine FACT rather than derived
  data. The cause enum (`initial`/`resume`/`retry`/`auto-retry`/
  `corrective`/`scope-corrective`/`heal`/`baseline`/`served`) is the
  engine's and is never inferred; the heal round rides the persisted
  `RunState.heal_pending` so a budget trip between a cascade and its re-run
  cannot lose it. Additive: `input_hash` composition does not move.
- **The feed speaks the reader's words.** Eight engine status tokens
  (`heal-exhausted-pass`, `scope-corrective-respawn`, …) had been rendering
  raw in the one pane meant to be in a domain expert's language.

Four adversarial rounds followed. Round 1 found 2 blockers, 7 majors and 9
minors in the original work; rounds 2 and 3 found defects introduced by the
previous round's FIXES (2 blockers, then none); round 4 found no blocker and
no correctness defect in the engine — its findings were in the regression
net. Five tests written to prove a fix were found to pass for the wrong
reason, each a guard that silently excused its assertion.

Still deliberately unbuilt: S1.2 (one shared projection per snapshot — the
remaining ~1.75 MB of a warm render), S1.5 (lazy drawers), and the S2/S4
views, which the request's own sequencing puts behind S1.

## 0.13.0 — 2026-09-10

The harness-parity release: PROPOSAL-throughput-and-harness-parity, adopted
at rev 3 and built in one day under three adversarial review rounds — each
round attacking the previous round's fixes, every fix landing test-first.
Feature E (event-driven dispatch) was deferred at adoption on structural
evidence (2 of 40 shipped flows have any node that could start before its
wave's barrier; the flagships are fan-out→join, which no dispatch strategy
beats); A3 (flow-digest narrowing) waits for a composing client. Both carry
recorded triggers in the proposal's §10.

### The digest unblock (A1/A2)

`stanza_digest` is canonicalized over a frozen field set: the v1 fields
always (byte-identical to every earlier digest, pinned against recorded
values of the shipped example), later behaviour-bearing fields only when
set, scheduling-only fields never. Adding a field to `ExecutorStanza` no
longer re-bills every cached harness node on upgrade — the dead-end that
blocked three roadmap items. First consumer: per-stanza `default_retry`
(node `retry` > stanza > kind default), shipped live on the example's
copilot-cli stanza, whose digest did not move — the carve-out proving
itself. DEVIATIONS 2026-09-09.

### Request-thrift: deletion-only repair (C2/C3)

Before spending the corrective re-spawn — on a request-metered harness, a
second billed request — the driver tries a deterministic repair that may
only DELETE: fence lines, dangling commas before existing closers, garbage
around the value. It never synthesizes a closing token (a synthesized `]`
would pass a truncated review as a clean one), and on the file channel it
obeys a single-value rule at BOTH the salvage and repair layers: the §7
footer says the file contains only the JSON, so a file holding a second
value-shaped span, a failed span, or trailing bytes goes to the corrective
— where the model, not the driver, disambiguates. Acceptance rotates the
raw bytes first, journals a `kind:"repair"` event naming every deletion,
and sets `repaired` on the record — surfaced by `status` and the mission
drawer, reset on every new execution, and carried across `--seed`/
`--replay` so no lineage presents repaired bytes unmarked. The corrective
fence now embeds the longest near-object from the raw channel instead of
salvaged inner rubble (the chronicle-forensics fix).

### The pi-stream result channel (D)

`envelope = "pi-stream"` on a stanza has the driver parse pi's `--mode
json` JSONL stream structurally on the §8.3 stdout leg: the result is the
last assistant message's text blocks (thinking excluded), the file channel
still wins, and a stream with no assistant text is a named error. The
shipped `pi-review` stanza regains `--mode json` — readonly reviewers now
carry usage envelopes, tool counts, and model IDs in every cost surface,
ending the "telemetry on reviewers costs correctness" trade the example
file used to state. The parsers moved into the driver
(`lockstep.pistream`); contrib imports them with its standalone fallback.

### Schema pass-through (C1)

`schema_argv` on a stanza appends a schema-bearing argv fragment when the
node has `output: "json"` and a resolvable contract — `{schema}` fills
with the CONTRACT's schema (a `Name[]` contract becomes an array schema,
or the flag would guarantee the corrective it exists to kill), and the
filled schema is its own fingerprint part. Placeholder expansion is
single-pass on the template, so interpolated data and schema bytes can
never rewrite each other or the command line. `verify --lint` gains
`lint-schema-argv`.

### The instruments (G1/G2) and config adoptions (B1/B2)

Cost surfaces gain the `cache:` hit-rate line (absent prints as absent,
never 0%; a half-reported side names the gap instead of inventing 100%)
and per-node counts of usage-bearing assistant messages under exactly
that name — a correlate for request-metered dashboards, never the billed
premium-request unit. The example claude stanza adopts
`--exclude-dynamic-system-prompt-sections` (flip your live toml only
after a baseline run records the `cache:` line), and
`claude-code-resilient` ships `--fallback-model` as a per-node opt-in.

Three adversarial rounds (two agent lenses plus inline) found and fixed
seven blockers/majors across the build — salvage-starved repair, two argv
placeholder-injection directions, a stale schema file, decoy adoption at
two layers, a lying `repaired` flag, dropped provenance — each now a
pinned test. DEVIATIONS 2026-09-09 records all four departures and their
round-2/3 refinements.

## 0.12.0 — 2026-09-08

The trust-the-human release: the two remaining OW-07 acceptances, built and
adversarially reviewed as they landed. `lockstep adopt` gives the engine a way
to hold a journaled human decision alongside the hash (DESIGN-NOTE-adopt,
adopted with all five decisions as recommended; the one deliberate departure
from "nothing is trusted except the hash" is registered in DEVIATIONS
2026-09-08). The write-scope quarantine gains the corrective re-spawn its
contract twin always had (G1b), and the adjudicated-review programme's ledger
reaches every cockpit surface. `stable_output` (S4) remains deliberately
unbuilt — it ships only if the stable-shell-output authoring discipline proves
insufficient in practice.

### `lockstep adopt` — settle a human-remediated artifact (S3)

The OW-07 gap, closed: after a gate block and a legitimate human edit, every
road destroyed the edit or the lineage — `resume` re-ran the producer on a
hash miss and legally overwrote it, `--allow-dirty-scope` waived the E9
preflight wholesale, a second flow severed lineage. `lockstep adopt
<run_dir> <writer> --reason-file <f>` records the fact the engine had no way
to hold: this artifact is settled, a human put it there, and its consumers
have not seen it yet.

Semantics are Sol's, not Gemini's (DESIGN-NOTE-adopt §1, adopted+built
2026-09-08): the unit is an ARTIFACT adopted into a `done` writer whose
consumers re-run unweakened — never a failed node marked done because the
tree looks right.

- **The pin holds even against a hash miss** — the one deliberate departure
  from "nothing is trusted except the hash" (DEVIATIONS 2026-09-08); what is
  trusted instead is the chained `adoption` event (per-path before/after
  content hashes, the reason verbatim, `source:
  external-approved-remediation`). `input_hash` is never rewritten:
  `status`/`explain`/`explain --graph` say `settled-by-adoption`, never a
  cache hit.
- **Consumers re-pend explicitly** (reads-hashes cannot see an undeclared
  read); map consumers clear their item records, the same rule heal
  invalidation applies.
- **The M7 fingerprint refreshes for the adopted paths only** — the adoption
  is an external edit by construction, and without this the next resume's
  sweep re-pended the writer over its own adoption (D3, found in code while
  writing the design note). Unrelated edits still warn by name.
- **The pin dissolves** on `adopt --release` (journaled, the original event
  never deleted), and automatically when a heal round or a steering message
  re-spawns the writer — a re-spawn's output is model output and must not
  inherit the label.
- **Refusals, all exit 7**: live lock; wrong repo root; non-`done` writer;
  paths outside the writer's declared `spec.writes`; paths a second writer's
  scope also covers; consumers interpolating the writer's RECORDED result
  text ({steps.<id>.output}/.json/{previous.output}) — `--force` overrides
  with the override journaled in the adoption record. The site scanner is
  `_node_templates`, the same enumerator §6 verification uses — one list to
  keep honest, `when` and flow-child args included.
- **A seed names the adoption and never transfers the pin** (a new lineage
  is a new consent); `--force-stale <writer>` or re-adopt after the run.
- `adoption-reason.txt` is gc-protected like `rejection.txt` — human-authored
  artifacts are not the engine's to expire.

Pinned by tests/test_adopt.py (the design note's nine acceptance tests plus
the two dissolve paths); replay fixture passes un-re-recorded.

### One corrective re-spawn after a write-scope quarantine (G1b)

The scope twin of the contract corrective, accepted in the OW-07 response and
built: after a CLEAN quarantine, a harness-kind node is re-spawned once from
the restored tree — original task, the reverted patch fenced as evidence
(`scope.violation.patch`, capped; the full patch stays on disk), the declared
scope restated, and unlike the contract corrective it is NOT output-only (the
out-of-scope work is gone; the re-spawn may redo it inside the scope).
Attempt 1's in-scope writes survive. Bounded structurally, spends a spawn (a
budget trip stops cleanly with the quarantine standing), journals
`scope-corrective-respawn`; a second violation quarantines again and fails
terminally with both attempts' evidence intact. Shell nodes get none —
identical argv would just re-offend. The boundary is not weakened: the
quarantine happens on every violation. (DEVIATIONS 2026-09-08;
tests/test_write_scope.py::TestScopeCorrective; FakeSpec gains
`write_files_by_attempt` so the offline suite can model recovery.)

### The findings ledger reaches the cockpit

The adjudicated-review programme's deferred deliverable (upstream response,
S2+G5 item 5), now that the ledger exists to render: MISSION shows
`review findings: 2 new, 1 persisting, 4 resolved, 1 accepted risk (round 3)`
under the headline instead of an undifferentiated list. Mechanical like every
line on that board — a count over the ledger file's own `state` fields, found
via the flow arg the template already wires (`--arg ledger=`). All three
surfaces render it (`mission_view.ledger_summary`: the page and the TUI
in-process, `cockpit.ps1` via its `Get-LedgerLine` twin), the state order and
phrases are pinned across implementations by test exactly like the glossary,
and an EXISTING ledger that fails to parse is named out loud
(`ledger unreadable`) — an unreadable memory must not look like no memory. A
run without the arg, or whose gate has not written round 1 yet, renders
nothing. COCKPIT-FOR-DOMAIN-EXPERTS defines the four words, since the guide
binds what the surfaces may say.

## 0.11.0 — 2026-09-08

The OW-07 response release: the point-by-point disposition of two consumer
reports (`docs/proposals/upstream-response-ow07-feedback.md`, c3e1c2c), built
out. One confirmed engine bug, four accepted features, one new programme, and
three declines that each name the shipped mechanism they re-invent.
0.10.1-scope (truthful terminal state, awaiting-a-human wording) and
0.11.0-scope ship together — nothing went out between them (865dbf0).

### Breaking / changed behaviour — read this first

- **A post-lock refusal now exits 7, not 4.** A refusal after the lock is
  taken (dirty write scope, a failed heal precondition) used to leave every
  node pending and no journal, so `wait` reconstructed exit 4 — "a plain
  resume continues" — which was wrong advice, because `resume` is E9-exempt
  and would skip the very preflight that refused. The refusal handlers now
  persist a run-level terminal record plus a chained refusal event before
  releasing the lock; `wait` returns the recorded **7**. Anything keyed on
  exit 4 for this case must change. Budget stops still read 4. (bdce9c2)
- **Your local `lockstep.toml` needs `--permission-mode` on the claude-code
  stanza.** A headless `claude -p` spawn auto-denies its own tool permission
  prompts, so a WRITING node drafts its output and is then denied the Write
  that saves it — observed live, on this release's own changelog node. The
  committed `lockstep.toml.example` now carries
  `--permission-mode acceptEdits` (Edit/Write only; the driver's write-scope
  quarantine remains the enforcement layer). `lockstep.toml` is gitignored, so
  the example will not update it for you. A node that must RUN commands is
  still denied Bash — escalating that stanza to `bypassPermissions` is a
  deliberate, named decision, never the default. (85388b0)
- **An auto-rejected approval is worded as a parked question, not a
  decision.** `wait`/`status`/MISSION/cockpit now say "resume from a terminal
  to answer"; a human rejection stays a rejection. Exit 6 is unchanged —
  wording only. (bdce9c2)
- A refused run says so **everywhere a reader asks**: `status` prints the
  evidence verbatim, `active` tags **REFUSED** by default, MISSION and the
  cockpit headline say `refused: dirty scope` instead of `waiting`, and a
  `--detach` parent watches a 3s grace window so the refusal lands in the
  launching terminal rather than only in a log nobody was told to read. The
  next drive clears the record. (bdce9c2)
- Old-driver compatibility holds: `RunState` ignores unknown fields, so a
  0.10.0 driver reading a newer `state.json` degrades to the old reporting
  rather than failing. (2e1fdba)

### The operator gets two decisions back

- **`lockstep resume <run_dir> --max-agent-spawns N`** — raise (or lower;
  "stop spending" is coherent, warned, never refused) the spawn cap for
  **this drive only**. The flow's ceiling stays the consent artifact, every
  override leaves a journaled budget event that `status` echoes, and the next
  plain resume is governed by the flow again. This replaces the three-command
  workaround the report described: edit the flow, new lineage, `--seed` back.
  (6380eea)
- **`spec.reads_manifest: "paths"`** — append the resolved `reads` list to the
  prompt. `spec.reads` alone is an input-hash declaration the harness never
  sees; a reviewer told to "read the files named in `spec.reads`" correctly
  reported no such list and blocked on evidence access. The manifest is in the
  prompt, therefore in the input hash: a changed match set re-bills, and
  `explain` names `prompt.reads_manifest`. Zero matches say so out loud. It
  shares `apply_reads`' enumerator, so the list the prompt names and the files
  the hash covers cannot disagree. `reads_manifest` without `reads` is invalid
  at `verify`; absent or `"none"` is byte-identical to 0.10.0 (the replay
  fixture passes un-re-recorded). (6380eea)

### The adjudicated-review programme

For flows where review rounds never converge: discovery and gating must not
share a mind.

- `flows/factory/adjudicated-review.tg.json` — the template. Reviewers
  discover under a frozen scope statement; ONE readonly adjudicator
  (`personas/adjudicator.md`) re-grades their raw findings — unevidenced or
  out-of-scope demotes to nit with the reason kept; a NEW blocker is allowed
  but must say why it is in scope. (60ded6f)
- **`lockstep.gates.ledger_check`** — cross-round memory as a repo file the
  model never writes. Merges each round mechanically (new / persisting /
  reported-resolved, nothing ever deleted, returns recorded), blocks only on
  ACTIVE entries at threshold, treats accepted-risk as a human state that must
  carry a disposition or the gate blocks, and re-runs idempotently on
  identical input so resume revalidation cannot inflate history. (60ded6f)
- New advisory lints (`verify --lint`; exit code unchanged):
  `lint-unadjudicated-reviews` names the ping-pong shape this replaces — two
  or more `Finding[]` producers each gated raw; one gated raw stays
  refine-loop's fine shape (60ded6f). `lint-ledger-rollback` catches the
  ledger gate colliding with heal's default rollback, which restores every
  path since its baseline and would erase the cross-round memory each round —
  the exact failure the ledger exists to end. The template and FLOW-AUTHORING
  say `"rollback": false`. (f7f7d3c)
- FLOW-AUTHORING gains "Convergent review": the why, delta mode via
  `node_diff`, and the frozen-scope lesson. (60ded6f)

### `explain --graph` stops overstating freshness

- A node whose freshness rests on an always-rerun shell reproducing its
  recorded output now reads **`conditionally fresh <id> — depends on
  always-rerun shell <sid>`** instead of plain "fresh". The pre-run explain
  said three fresh; the shell printed a different duration string and two
  re-billed. Taint follows CONSUMPTION (`{steps.<shell>.}`) rather than mere
  ordering edges, and names the root shell through intermediaries; counts fold
  into fresh with the conditional split alongside. (fc87ea2)

### Docs

- **`docs/guides/CONSUMER-PLAYBOOK.md`** (new) — symptom first, mechanism
  second. Four feature requests already had shipped answers nobody found
  (`--seed`, `node_diff`, `pi-guarded`, auto-reject-is-the-park), plus two new
  recipes (budget-only edits never fork a lineage; stable shell output is
  authoring discipline) and the 0.10.0-era all-pending/exit-4 caveat for older
  drivers. (fc87ea2)
- FLOW-AUTHORING documents the detached-approval idiom (bdce9c2) and states
  the `reads` trap in one sentence — it is an input-hash declaration only
  (6380eea). COCKPIT-FOR-DOMAIN-EXPERTS learns the two words MISSION now shows
  a domain expert, since the guide binds what the surfaces may say (2e1fdba).
  CLAUDE.md's command list learns the budget override and the
  adjudicated-review template (f7f7d3c).

### Fixes

- `lockstep.gates.version_sync` accepts a **v-prefixed changelog heading**.
  Its delimiter lookbehind rejected the `## v0.11.0` form that release-cut's
  own prompt instructs (`## {args.tag}`), while the same gate blesses the `v`
  prefix on `--tag` — the flow and its gate contradicted each other. The token
  now allows one optional leading `v`; the 0.4.0.1-substring protection is
  re-pinned alongside the new case. (85388b0)
- `record_terminal` guards its whole body, not just the load — an AV transient
  on the state write or event append would have replaced the clean exit-7
  refusal message with a traceback, the reporting mechanism failing exactly
  where it was built to work. (2e1fdba)
- `status`' budget echo derived from a second, unguarded `read_events` call:
  both a duplicate full-journal scan and a regression of the r6 fix that lets
  `status` render over a corrupt journal. It now derives from the guarded
  read. (f7f7d3c)
- `cmd_active`'s comment stops claiming an abandoned refusal cannot linger (it
  can, until `gc`), `cmd_wait`'s docstring admits the recorded-exit path
  (2e1fdba), and `explain_graph`'s docstring admits the per-node conditional
  label (f7f7d3c).

## 0.10.0 — 2026-08-16

The fleet release: multiple concurrent drivers, made safe and operable
(concurrent-orchestration work order, adopted with the build; a
three-reviewer adversarial round plus a verification round before the cut —
the work order's header annotation is the record of what review changed).
Work-repo integration: `docs/proposals/passdown-0.10.0-work-repo.md`.

### The engine learns which tree a run belongs to
- `RunState.repo_root` — the resolved `--repo-root` a run was created
  against; recorded, never hashed; empty on older runs = unknown, never a
  mismatch. Child (`kind:"flow"`) runs record their parent's.
- `resume` against any other tree: **refusal, exit 7**, both paths named — a
  wrong-tree resume would snapshot and roll back someone else's work.
- An identical `run` whose newest lineage lives in another (often harvested,
  deleted) worktree **falls through to a new lineage** with a printed note —
  plain `run` keeps working after every fleet. Both narrowings logged in
  DEVIATIONS (2026-08-16).
- `status` prints `repo root:`, `active` prints `root:` (unknown stated,
  never omitted). Attach under a live lock pinned by test: exit 8, nothing
  written.

### The lane tooling (contrib)
- `lane.py start` — the worktree-per-run launch, mechanized: fresh worktree
  on its own branch, `verify` against it, detached run with the MAIN repo's
  config/runs-dir/binary, `--fresh` always. The run is identified by its
  own recorded root — unforgeable, only our child is told the worktree —
  with `--detach`'s printed lines demoted to diagnostics; launches
  serialize on a start-lock. Aborts never delete a tree that may host a
  live driver and never kill a pid not provably ours.
- `lane.py harvest` — refuses under a live driver, commits the branch (lane
  record excluded), restores the record if worktree removal fails;
  `abandon` is the explicit destructive sibling. The lane record
  (`<worktree>/.lockstep-lane.json`) is the durable identity every later
  step keys on.
- `who_holds.py` — LIVE/STALE/NONE (also FOREIGN/UNKNOWN) over the
  `<file>.holder.json` convention shared with the MIMIR DB runbook.

### Gate
- `lockstep.gates.lock_held` — preflight for flows that will write a shared
  file: non-blocking exclusive lock attempt (whole-file on POSIX, fixed
  large range on Windows), one open retry (the AV transient is not a
  holder), `open-refused` distinct from `lock-held`, FOREIGN holder files
  block, verdicts quote the holder by name. A diagnostic, not a mutex — the
  docstring says exactly what it cannot see.

### Roles, delegation, docs
- `docs/guides/FLEET-OPERATIONS.md` — the operating model, including "The
  roles": cockpit / main conversation / **dispatcher** / lane-runner, each a
  contract rather than a harness feature. Delegation moves information,
  never authority; evidence survives every hop verbatim; every tier above
  the run dir is disposable.
- `.claude/agents/fleet-dispatcher.md` + `lane-runner.md`, `/fleet-ops`
  skill (inline vs delegated). `approve.ps1` passes the run's recorded root
  and the main config, so the cockpit pane answers lane approvals.
- THEORY-OF-OPERATIONS §11: the multiple-drivers paragraph. Owner decisions
  recorded: ceiling 8 fleet-wide spawns; harvest always parks behind a
  walkthrough; `gc.auto 0` per clone (getting-started carries it).
- Two adversarially-reviewed work orders shipped with the release: the
  MIMIR DuckDB concurrency runbook (for the work repo) and the fleet order
  (built, annotated).

## 0.9.0 — 2026-08-15

The parity programme (PROPOSAL-taskflow-parity-tiers, adopted 2026-08-13) and
its successor (PROPOSAL-flow-composition, adopted 2026-08-15), every phase
adversarially reviewed before the next began.

### Patterns named and shipped
- FLOW-AUTHORING "Patterns you already have": **reduce**, **parallel**,
  **tournament**, **loop** — the keywords that deliberately do not exist,
  and the flows that ARE them.
- New starters: `tournament-judge` (rival candidates → judge, with the
  `tournament_pick` gate and the starter set's first custom contract),
  `refine-loop` (the loop pattern), `draft-then-review` (composition).
  New personas: `candidate`, `judge`.

### The loop (heal without rollback)
- Heal prompts name their round ("This is repair round N of M").
- `heal.on_exhausted: "block" | "pass"` — `"pass"` accepts the best-so-far
  when rounds run out, recorded as `accepted after N rounds without
  resolving: …` in the stored verdict, `status`, and a `heal-exhausted-pass`
  journal event. Never a plain pass; a gate that never decided (timeout,
  malformed verdict) never exhausts to pass. Guards:
  `on-exhausted-with-rollback` / `on-exhausted-without-rounds` (§6 errors),
  `lint-on-exhausted-pass`, and `lint-live-diff-per-phase` now fires on even
  one live capture inside a loop body.

### Declared staleness
- `spec.reads` — declared file inputs as hash parts: editing a declared file
  re-bills exactly the declarers, and `explain` names the file. Additive (M3:
  absent/empty contributes nothing — byte-identical parts, replay fixture
  unchanged). Stat-keyed per-process memo, `reads-hash` timing lines,
  `lint-broad-reads`, `snapshot_bench --reads`.
- `explain <run_dir> --graph` — the whole-graph staleness dry run: plans every
  node against the current tree into a throwaway dir, prints directly /
  transitively stale with the moved part named. Zero spawns; a gc'd result
  reports stale, never "unchanged".
- `run --seed <run> --force-stale <node>` — recompute: seed everything except
  the named frontier plus its downstream cone; forced is never confusable
  with hash-missed (record, journal, `status`, launch banner).

### Composition (`kind: "flow"`)
- A saved flow as one node. The child is a REAL run in
  `<run>/children/<node>-<hash12>/` — every run-dir tool descends unchanged;
  resume-mid-child is child resume (completed child work never re-bills);
  a moved parent hash starts a fresh child lineage beside the old evidence.
- One wallet, one tree, one worker cap across the whole tree of engines
  (`RunResources`: shared exclusive tokens, worker semaphore, root spawn
  budget). A child budget trip stops the RUN (exit 4), never "node failed";
  a child gate block fails the parent node naming the gate and the child dir.
- Boundaries as named errors: `rollback-heal-in-child`,
  `flow-in-rollback-cone` (rollback cannot cross the composition line until
  scope-narrowing exists), `exclusive-on-flow`, `write-scope-on-flow`,
  `timeout-on-flow`, `dynamic-flow-path`, `flow-cycle`, `flow-depth`,
  `flow-in-map`, `flow-args-missing`, recursive child verification,
  `lint-approval-in-child`. Steering a flow node is refused (steer the
  child's own nodes). Zero-token end-to-end smoke:
  `flows/demo/compose-smoke.tg.json`.

### Engine and harness fixes surfaced by the reviews
- AMENDMENTS M4's free retry-on-empty-result is now executor-opt-out
  (`auto_retry = False`); it was converting a child gate block into a
  retried success.
- The §6 arg scanner learned `spec.args` values and `spec.reads` entries as
  real references.
- `RenderCtx.runs_root`: the reads exclusion no longer mis-derives from a
  throwaway planning dir.
- Provider-limit diagnosis (`wait, then resume`) now works without a JSON
  envelope and reads stderr — reachable for copilot-cli, whose 429 usually
  means quota. The copilot stanza in `lockstep.toml.example` carries the
  four-point verification checklist (tool approval in -p mode, no pinned
  readonly shape, retry {max: 0} posture, the unhashed instruction-file
  channel).

### Docs
- THEORY-OF-OPERATIONS §8b (composition), the loop and heal-text round in §7;
  DRIVING-LOCKSTEP's exit table folds composed-run meanings; FLOW-AUTHORING
  gains "Patterns you already have", "Declared reads", and "Composition";
  debug-run and run-diagnostician descend into `children/`; DEVIATIONS
  entries for `on_exhausted`, `reads`, and the composition protocol
  extensions (`bind_run`, BudgetTripped-through-execute, `auto_retry`).

## 0.8.0 and earlier

Pre-changelog. See the git history and `docs/proposals/` — each adopted
proposal records what shipped and why.
