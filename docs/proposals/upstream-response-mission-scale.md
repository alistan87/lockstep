---
type: plan
title: "Upstream response: MISSION scale, blocker intelligence, and execution provenance"
description: Point-by-point disposition of the downstream MISSION feature request filed against lockstep 0.13.0 — one accepted P0 (incremental projections, gated behind a measurement phase), two accepted with scope reductions, one counter-proposal (journal attempt events instead of a new per-attempt manifest artifact), and answers to all five upstream questions. Every source-level claim in the report was verified before classification; all five were accurate.
resource: docs/proposals/upstream-response-mission-scale.md
status: S1 (1-5) + S2 blocker-path view + S3 engine half BUILT through 0.16.0; the rest of the S2/S4 views open
upstream_baseline: "lockstep 0.13.0 (9bee0c4)"
---

# Upstream response: MISSION scale and observability

**Responding to:** *"Feature request: MISSION scale, blocker intelligence, and
execution provenance"* (Sol, work repo, 2026-09-10), filed against 0.13.0
(`9bee0c4`) — requests **S1–S4** plus a diagnostics-export follow-up.

Every source-level claim in the report was verified against the code before
anything below was classified. **All five were accurate** — an unusually
clean report, and the sanitization discipline made it cheap to act on. One
of them found a stale comment defending the hot path: `_events_after`'s own
docstring asserts *"The whole-file read is unavoidable"*
(`mission_server.py`), which S1's byte-offset cursor refutes. That sentence
is the reason the cost was never questioned upstream.

Independent corroboration worth recording: the same performance seam was
found upstream on 2026-09-10 from the maintainer side and logged in
ROADMAP-NOTES before this report arrived. Two independent observations of
the same three cost centres is why S1 is P0 and not a hypothesis.

## Disposition at a glance

| Request | Classification | Disposition | Target |
|---|---|---|---|
| **Phase 0** — benchmark fixture + read/byte counters | **accepted, mandatory first** | `contrib/mission_bench.py`: synthetic history, per-cost-centre bytes/files/wall, machine-readable | **built with this response** |
| **S1** — incremental MISSION projections | **accepted (P0)** | byte-offset cursor, one shared projection per snapshot, immutable attempt-log memo, two-layer rail cache, lazy drawers | next slice, gated on Phase 0 numbers |
| **S2** — run health and blocker path | accepted | built on S1's projection; severity counts from built-ins + ledger only; operational conditions kept strictly beside severity | after S1 |
| **S3** — attempt trajectory | accepted **with a counter-proposal on the mechanism** | journal `kind:"attempt"` events (hash-chained) instead of a new per-attempt manifest artifact | after S1 |
| **S4** — tool activity and instruction provenance | accepted, **scope reduced** | most of "configured execution" is already recorded in `hash_parts`; this is largely a rendering job | after S1 |
| diagnostics export | deferred, agreed | revisit after Phase 0 shows which measurements are worth exporting | — |

Nothing here changes hash composition, exit codes, or any frozen surface.
Nothing here gives the browser a write path.

---

## Verified claims

Each row of the report's behaviour table was checked against 0.13.0:

| Claim | Verdict |
|---|---|
| `_events_after()` reads and splits the whole journal every poll, parsing only past the line cursor | **confirmed** — and its docstring wrongly calls the whole-file read unavoidable |
| `/api/state` reconstructs the complete run body | **confirmed** |
| `run_list()` enumerates and sorts every run dir before taking twelve | **confirmed** |
| `_drawers()` renders every node drawer into the page | **confirmed** |
| `node_tokens()` re-reads every `stdout*.log` to rebuild usage | **confirmed** |

The report's sharpest operational catch, which upstream had not noticed:
**do not rely on the runs-root directory mtime to notice a running→done
transition.** Writing inside a child directory does not reliably bump the
parent's mtime, so the obvious rail-cache design silently serves a stale
word for the one transition the reader is watching for. That constraint is
adopted verbatim into S1's acceptance criteria.

---

## Phase 0 — measurement first (built with this response)

Accepted without reservation, and promoted from "suggested first phase" to
a **precondition**: the upstream rule for this class of work is evidence
before optimization (ROADMAP-NOTES 2026-09-10). Five plausible cost centres
have been named across two reports; optimizing the wrong one is placebo work
that still costs a review.

`contrib/mission_bench.py` ships with this response. It is read-only, spends
nothing, imports only the stdlib and the cockpit modules, and reports per
cost centre: **bytes read, files opened, and wall time** — bytes first,
because wall time on this hardware is confounded by AV and cannot be
compared across machines. It can generate a synthetic retained-history
fixture (many run dirs, long journals, many rotated attempt logs) or profile
a real `runs/` in place.

The number that decides S1's shape is the **quiet heartbeat**: bytes read by
`/api/events` when nothing has changed. If that scales with journal size on
the reporter's machine, the byte-offset cursor is first and everything else
waits.

**Requested of the reporter:** run

```
python contrib\mission_bench.py --runs-root <your runs dir> --json
```

on the slow machine and return the JSON. It contains counts, sizes and
timings only — no paths beyond the runs root, no run names, no prompts, no
findings, no file contents — so it is safe to send under the same
sanitization rules the report itself observed.

---

## S1 — incremental MISSION projections (accepted, P0)

All six mechanisms are accepted as described. Three conditions:

**(a) Lazy drawers must not become a fourth renderer.** The cockpit
deliberately keeps ONE implementation of the agent block called by three
surfaces (page, TUI, `cockpit.ps1 -Role why` via `mission_view.py --agent`),
and two tests pin vocabulary across surfaces. A server-rendered node-detail
page is a fourth path and is acceptable only if it calls the same
`node_drawer`/`node_agent_lines` and the glossary tests extend to cover it.
Fact parity is not a review promise here; it is a test.

**(b) The cache may never change output.** Stated in the report and restated
here as the acceptance rule with teeth: a test must show that evicting any
entry changes timing only. A derived cache that can change a rendered word
is a correctness surface wearing a performance costume.

**(c) Torn-line tolerance survives the cursor rewrite.** SPEC §10.3
tolerance for a torn trailing JSONL line is load-bearing (the driver appends
while the page reads). The byte-offset cursor must re-read a partial final
line on the next tick rather than consuming it — and the existing
mid-file-tear refusal (`_events_after` returns nothing rather than lying)
must be preserved, not optimized away.

## S2 — run health and blocker path (accepted)

Accepted as specified. The two decisions upstream most wants to endorse
explicitly, because both are the honesty rules this cockpit is built on:

- **Operational conditions never acquire an inferred severity.** A contract
  failure, a scope violation, a provider limit, and an approval wait are
  blocking conditions without a `Finding.severity`, and inventing one to
  make a tidy count is the confident-wrong-number failure the cost surfaces
  already refuse. Beside the counts, never inside them.
- **A malformed ledger reads `unreadable`, never zero.** Same rule as
  `not reported by this harness` and the absent-cache-fields line.

Downstream impact ("4 steps waiting behind the blocked node") is computable
from the run dir's own `flow.tg.json` copy — a dependency fact, and the
report is right to refuse to call it a schedule estimate.

## S3 — attempt trajectory (accepted; counter-proposal on mechanism)

**The gap is real.** `hash_parts` is recorded per NODE (the latest plan),
not per attempt, and rotated artifact filenames record *that* an attempt
happened, never *why*. Recovering "attempt 2 was a contract corrective,
attempt 3 was heal round 1" currently requires inferring from filenames and
correlating loosely against journal events. The report is right that this is
not sound.

**Counter-proposal: journal it, do not add an artifact.** Instead of a new
versioned per-attempt manifest file, the engine should append a
`kind:"attempt"` journal event at each attempt start carrying: node id,
attempt ordinal, scope (node or map item index), **engine-owned cause enum**
(`initial` | `retry` | `auto-retry` | `corrective` | `scope-corrective` |
`heal` | `resume` | `served`), heal round where applicable, and the digest
references already computed at plan time. No prompt text, no context
contents — same exclusion the report specifies.

Why this is the better shape:

- **It rides the hash chain.** `events.jsonl` is chained and checked by
  `verify-trace`. A manifest artifact would put engine-recorded *fact*
  outside trace integrity — the wrong side of the report's own line
  ("keep derived caches outside trace integrity"; an attempt record is not
  derived).
- **No second versioning surface.** The journal is already kind-tagged and
  forward-tolerant: an older reader ignores an unknown `kind`, which is
  precisely the compatibility property the report asks a new artifact to
  provide.
- **Legacy runs degrade exactly as specified** — no `attempt` events means
  `cause unknown`, displayed, never inferred.
- **The cockpit already reads the journal**, and S1 is making that read
  incremental regardless.

The journal already carries `heal-round`, `scope-corrective-respawn`,
`repair`, `seed` and `adoption` events, so this extends an established
pattern rather than starting one. If the reporter's use case needs something
the journal genuinely cannot express, upstream will revisit — but the
manifest should be the fallback, not the opening move.

Everything else in S3 is accepted: finding identity by normalized
(category, file, claim digest), the new/persisting/resolved/severity-changed
classification, `not comparable` when contract or shape is missing, harness
retries kept distinct from heal rounds, and the refusal to label a later
attempt "better."

## S4 — tool activity and instruction provenance (accepted, scope reduced)

**Most of "Configured execution" is already recorded and needs no engine
change.** `state.py`'s `hash_parts` carries labelled digests of exactly
these inputs — persona, argv, the resolved stanza digest (`config`),
`prompt.context:<rel>` per declared context file, `prompt.reads_manifest`,
reads parts, plus heal/steer/contract sub-parts — recorded-never-hashed,
specifically so `lockstep explain` can say which input moved. Rendering
"which persona and which context files were actually in this node's
execution packet, by digest" is a read of `state.json`.

Two genuine gaps remain, and only these need new work:

1. **Per-attempt granularity** — covered by the S3 counter-proposal above.
2. **Observed-vs-configured tool activity** — the drawer shows aggregate
   tool counts; `attempts_detail[*].tools` already exists in `cost_report`
   for pi streams and is simply not rendered per attempt.

The report's insistence that configured and observed never be conflated is
adopted. So is the refusal to display tool arguments, results, prompts, or
context contents — and upstream notes the report's own conclusion that no
ambient skill loader is needed, which matches the standing rule that an
unhashed instruction channel is the thing to avoid (the reason every pi
stanza here carries `--no-context-files --no-skills`).

---

## Answers to the five questions

**1. Process-local cache first, or engine-owned run index?**
Process-local. An engine-owned index makes the engine write an artifact that
serves a view concern, and retention already has an owner (`lockstep gc`).
Revisit only if Phase 0 shows cold-start scanning still dominates after the
in-process cache lands.

**2. Separate server-rendered node-detail GET for no-JavaScript users?**
Yes — under S1(a). `/api/node/<id>` already exists as a route; it needs an
HTML sibling that calls the same render functions, not a new renderer.

**3. Built-in contracts and the ledger only, or an adapter protocol?**
Built-ins and the ledger only, for v1. An adapter protocol is a plugin seam,
and the working agreement prefers deleting a feature over adding one.
Custom contracts degrade to `not reported`, exactly as the report specifies.
Revisit when a concrete custom contract needs it — with the contract in hand.

**4. Normalized tool telemetry: contrib adapter or executor artifact?**
Contrib adapter for now. 0.13.0 moved the pi-stream parsers into the driver
(`lockstep.pistream`) with contrib importing them and keeping a standalone
fallback, so the parsing is already shared. An executor artifact is engine
output that must be versioned forever; it earns that only if Phase 0 proves
stdout reparsing dominates after the immutable-log memo.

**5. Context-file display labels in `flow.labels.json`, or is the path enough?**
The path is enough for v1. The labels sidecar is the right home if labels
become necessary — display-only, unhashed, already loaded — but do not add
the indirection before real paths prove unreadable on a real flow.

---

## Delivery

The report's phase order is adopted unchanged. Phase 0 ships with this
response so the S1 design can be chosen from numbers rather than from five
plausible hypotheses. S2–S4 explicitly must not land on the current repeated
full-log scan — upstream agrees, and that is the reason S1 is the gate.

## Built 2026-09-10, and what the numbers said

Phase 0 answered its own question, so four slices landed without waiting.
Measured on a 40-run / 3 000-event fixture, cold -> warm:

| centre | before | after |
|---|---|---|
| quiet heartbeat | 274,500 B, x16 across a x16 sweep | **4,096 B, constant** |
| run rail | 19,584 B / 12 files | **0 B / 0 files** warm |
| usage walk | 1,853,825 B | **278,273 B** warm |
| full render | 3,538,276 B | **1,750,760 B** warm |

**S1.1** (byte cursor), **S1.3** (attempt-log memo), **S1.4** (two-layer
rail cache) and **S3's engine half** (`kind:"attempt"` events, as
counter-proposed) are built and pinned. `contrib/mission_bench.py --sweep
--axis depth` also settled a question this document said would need the
reporter's real-world numbers: across **254x more log bytes** (134 KB ->
34 MB of harness logs, i.e. a run resumed and retried for weeks) the warm
render and usage costs are **flat, x1.0**. The memo absorbs run depth
entirely, so S1.3 earns its complexity without further evidence.

Four adversarial rounds followed the build. Round 1 found 2 blockers, 7
majors, 9 minors in the original work; rounds 2 and 3 found defects
introduced by the previous round's FIXES (2 blockers, then none); round 4
found no blocker and no correctness defect in the engine. The reporter's
`parent-directory mtime` catch was adopted verbatim and then violated from
the other direction by the first fix — membership was filtered on
`state.json` at scan time, so a run started while the page was open stayed
invisible for the life of the process. It has a test now.

### S1.2, and what the reporter's own numbers changed (0.15.0)

The reporter adopted 0.14.0 and returned a real-history profile (132 run
dirs). It corrected an upstream claim: the byte cursor was described here as
"the largest single win", which was true of the upstream FIXTURE (a 277 KB
journal) and false of their machine (45 KB). Their biggest win was the rail
cache — 294,159 B → 0 B per paint — and their warm render was only 10%
better than cold, with `first_paint_warm` the heaviest centre. That is a
profile dominated by per-render re-reads, which is precisely S1.2, and it
is why their priority ordering was right and the upstream fixture's was not.

S1.2 shipped in 0.15.0. `events.jsonl` was read THREE times per render (the
feed, `collect_run`'s wall/heal pass, `_intervals` for the timeline) —
77% of everything a warm render still touched. It is now parsed once and
handed down; `_trace_status` is memoized on the journal's identity, since
one render asks for that whole-file re-chain twice independently.

| | 0.14.0 | 0.15.0 |
|---|---|---|
| warm render (upstream fixture) | 1,750,760 B | **378,260 B** (−78%) |

**Two defects the reporter found on adoption, fixed in 0.14.1**, both
invisible upstream and both worth recording as a process result rather than
a bug list: CPython moved `JSONDecodeError.pos` in 3.13, so the
deletion-only repair silently stopped repairing and every dangling comma
fell through to a billed corrective re-spawn; and a test branched on the
gitignored `cost-fields.toml`, passing on any machine that had one. Four
adversarial review rounds missed both, because every round ran on one
interpreter inside a working tree carrying untracked files. The reviews were
adversarial about the CODE and never about the ENVIRONMENT.
`contrib/portability_check.py` now runs the suite from a clean clone under a
named interpreter, and doing so is a pre-tag rule.

**S1.5 closed and the S2 blocker path built in 0.16.0** (the mission-ux
work order, `docs/proposals/mission-ux-work-order.md`): lazy detail landed
as the peek panel plus a status-aware inline threshold — settled drawers
degrade past `DRAWER_INLINE_MAX`, the loud minority never does, and the
`drawers_unshared` guard rail held (the per-click fetch is one node). The
blocker card carries the failing step, the error verbatim, and the
stalled-behind dependency count worded exactly as this response required —
a dependency fact, never a schedule estimate, no invented severity. The
same batch made a vanished driver render as "stopped unexpectedly" instead
of a healthy "running" over a corpse.

**Still open:** the rest of the S2 view (condition counts beside the
ledger line) and the S4 view over the S3 attempt events the engine already
journals.

**Still worth having from the reporter:** `mission_bench.py --runs-root
<runs> --json` from the slow machine. Not to choose what to build — the
byte counts settled that — but for the two questions a synthetic fixture
cannot answer: the COLD cost on a real retained history, and how many run
dirs have actually accumulated (Q1's cold-start question).
