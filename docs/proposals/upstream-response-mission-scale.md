---
type: plan
title: "Upstream response: MISSION scale, blocker intelligence, and execution provenance"
description: Point-by-point disposition of the downstream MISSION feature request filed against lockstep 0.13.0 — one accepted P0 (incremental projections, gated behind a measurement phase), two accepted with scope reductions, one counter-proposal (journal attempt events instead of a new per-attempt manifest artifact), and answers to all five upstream questions. Every source-level claim in the report was verified before classification; all five were accurate.
resource: docs/proposals/upstream-response-mission-scale.md
status: draft for downstream discussion
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

**Next action is the reporter's:** run `mission_bench.py` on the slow
machine and return the JSON. The cost centre it names decides what S1 builds
first.
