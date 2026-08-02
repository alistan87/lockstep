# Proposal: unattended mode (deferred)

**Status:** split out of `PROPOSAL-domain-cockpit-rev6.md` §D at rev 7, on
the decision that it should not keep growing inside a document describing
work that is actually about to be built. The design below is unchanged
from rev 6 (itself the rev-4 unattended pass: U-B1..U-B4, U-M1..U-M2);
only the risks and open questions that belong to it were moved here with
it. **Nothing in this document is scheduled.** It is double-gated — it
requires a cockpit that has been used by a real domain expert AND a
retrospective that has produced at least one attended cohort to
concord against — and neither exists.

**Prerequisite reading:** the cockpit proposal, whose §A.1 (clarification
gates), §A.3 (pane grammar, L-B1 send-text integrity), §B (cost
tracking), and §C (gate-driven improvement) this design builds on
directly. Section references of the form §A/§B/§C point there; §D refers
to this document.

**Why it was kept rather than dropped:** the two non-negotiables it
works out — that the human approval channel is never forged, and that
autonomy is earned per class against recorded human decisions and
revoked on a single overturn — are the constraints any future attempt at
this would have to rediscover. The `make_unattended.py` transform and
the ledger also pre-prove the shape of a possible engine-native
`approval.policy` stanza, the same way `cost_report.py` pre-proved
`usage_fields`.

## The design

Unattended mode lets a run proceed past most *intermediate* human gates
using judgment rendered by the system itself, with deferred human review.
It is designed under three non-negotiables:

1. **The human channel is never forged (U-B1).** The structural
   guarantee — interactive approvals answered only by a human at a real
   TTY — stays intact. Unattended mode is implemented by *removing*
   interactive approval nodes from the flow, never by answering them.
   Sending keystrokes into an approval prompt (by the orchestrator, an
   extension, or any automation) is a prohibited pattern, same standing
   as Addendum-A's; the cockpit proposal's §A.3 L-B1 spawn-only send rule makes it
   mechanically checkable.
2. **Autonomy is earned per class, and revocable (U-B3).** No judge
   auto-accepts anything until it has demonstrated concordance with human
   decisions on that exact flow and approval class — and one overturn
   revokes it.
3. **Egress is never unattended (U-B4).** Nothing leaves `runs/` — no
   `Deliverables/` copy, no external side effect, no spend-cap increase,
   no artifact carrying a sensitivity label above the workspace's floor —
   without a human approval. Unattended mode removes *intermediate*
   approvals only.

**Mechanism (v0 — no engine change):**

- **`contrib/make_unattended.py <flow.tg.json>`** — a deterministic
  transform producing the unattended variant of a DE-facing flow: each
  intermediate approval node is replaced by a **judge gate** (a normal
  gate; model judgment gating control flow is already-sanctioned
  mechanics) followed by a shell node appending to the ledger. Terminal
  egress approvals are left untouched (U-B4). The transform is
  deterministic and diffable; the variant's `flow_hash` differs from the
  attended flow's, so retro cohorts separate attended and unattended for
  free.
- **Judge gates consume evidence (U-B3).** The judge's prompt includes
  the same B1 mechanical extract the human would have seen, and its
  contract requires the Verdict to cite it. A judge whose Verdict does
  not reference the evidence is a contract violation (block), not a
  pass.
- **Triage classes** (judge contract output, enum, closed):
  - `auto-accept` — proceed; permitted only for qualified classes.
  - `proceed-flagged` — proceed; ledger entry marked for priority
    review.
  - `hold` — gate blocks (exit 2); the orchestrator queues it for the
    human like any needs-you beat. This is the starting class for
    everything.
  - `escalate` — gate blocks AND the orchestrator raises it in CHAT
    immediately as urgent (judge saw something anomalous: contradictory
    evidence, scope drift, sensitivity ambiguity).
- **The ledger — `<run_dir>/deferred-approvals.jsonl`** (append-only,
  flow-written via the shell node, journal-style reader/writer
  discipline): approval id, class, judge triage + verdict summary,
  evidence extract reference, **downstream cost-at-risk** (the set of
  descendant nodes that will consume the artifact, with their historical
  cost from the cost report where available), qualification state at
  decision time.

**U-B2 — deferral vs. steer-permanence.** A post-hoc rejection of a
deferred item invalidates every descendant that already ran; with
segmentation gone (unattended chains segments automatically), one bad
early auto-accept cascades into the whole lineage — and steer-permanence
means true retraction is `--fresh`, re-billing everything. Rule:
`auto-accept` and `proceed-flagged` are legal only when the ledger's
computed downstream cost-at-risk is at or below a configured cap
(`unattended.max_cost_at_risk`, sidecar config beside the cost field
maps); above the cap, the judge's best available class is `hold`. The cap
is stated at the consent beat in the same honest units as the budget
("I'll pause for review whenever more than ~N tasks of work would ride on
an unreviewed decision").

**Qualification — skip-lot sampling for approvals (U-B3, operative
rule).** Per **(flow_hash, approval-class)** pair:

- Start at `hold` — 100 % inspection. The judge still runs and renders a
  proposed verdict; the human decides. Every human decision recorded
  against the judge's proposal is a **concordance sample** — the hold
  phase *is* the qualification data source (no chicken-and-egg).
- `N_flag` consecutive concordant samples ⇒ class may render
  `proceed-flagged`. `N_auto` further concordant reviews of flagged
  items ⇒ `auto-accept` permitted. (Defaults `N_flag = 5`,
  `N_auto = 5`; operator-owned in the sidecar config.)
- **Any overturn — a human reversing a judge's accepted/flagged decision
  at review — is an excursion: the class reverts to `hold`
  immediately**, and the overturn is a first-class retro finding with
  the judge's cited evidence attached.
- The retro computes overturn rate and time-in-class per cohort; a class
  that cannot hold `auto-accept` across two consecutive cohorts is
  flagged for judge-contract rework, not for threshold loosening.
- The **planted-defect discipline extends to judges (m1 extension):**
  before a class first enters `proceed-flagged`, one seeded
  domain-visible defect must pass through it and the judge must `hold`
  or `escalate` it. A missed plant blocks qualification for that class
  and files as a blocker against the judge's contract.

**U-M1 — consent, rendering, and STOP.** Unattended mode is entered only
per-run, at the budget-consent beat, and journaled: the `consent` entry
gains `mode: attended|unattended`, the cost-at-risk cap, and a
restatement of the never-auto floor. MISSION's DE tier renders unattended
decisions mechanically and distinctly: `auto-approved (review pending:
3)` with its own glossary line; the count comes from the ledger, never
from narration. STOP semantics are unchanged and mode-independent. The
review beat itself is a needs-you moment: the orchestrator presents
ledger items **with their evidence extracts** (never summaries alone —
B1 applies to async review too), the human answers `a`/`r` per item, and
an `r` triggers the rework conversation with its cost stated (steer vs
`--fresh`, per §A.1's permanence rules).

**U-M2 — the improvement loop stays human.** The gate-driven improvement
loop (§C) is outside unattended scope entirely: improvement batches
modify flows and judges themselves, and a system auto-approving changes
to its own judges is the textbook runaway. `make_unattended.py` refuses
to transform any flow whose nodes write to `flows/`, `contrib/`, or
`lockstep.toml`. (Consistent with the standing non-goal: no autonomous
self-modification.)

**r7 note (deferred, recorded).** If unattended mode proves out, the
engine-native form is an `approval.policy` stanza + a `deferred` event
kind, batched with the existing r7 items. The ledger and transform
pre-prove the shape, same pattern as cost tracking's v0 → v1. Not
implemented before the v0 loop has closed at least once with real
qualification data.

## Risks specific to this mode

- **Judge evidence ceiling** — the judge sees the B1 extract, not the
  domain expert's domain knowledge; the clarification a DE would have
  volunteered at an attended approval does not exist in unattended mode.
  `escalate` and the planted-defect gate mitigate but cannot close this;
  the consent beat states it plainly. Accepted residual of the mode.
- **Qualification sample-size at small scale** — every applied
  improvement changes `flow_hash` and resets cohorts, so actively
  improved flows may never qualify for auto-accept. Arguably correct (a
  changed process re-qualifies), but it means unattended value depends
  on flow stabilization; see the open question below.
- **Hold-heavy runs** — a poorly qualified flow in unattended mode is
  attended mode plus judge-spawn cost. The retro's time-in-class metric
  makes it visible; flows that stay hold-heavy should stay attended.

## Open question

**Qualification survival across `flow_hash` changes** — should
qualification persist when an applied improvement does not touch the
approval's upstream subgraph? Needs a subgraph-hash mechanic that does
not exist yet; the strict reset stands until one is specified.

## Entry conditions (what would have to be true to schedule this)

1. The cockpit is in real use by a domain expert on the work laptop —
   not demonstrated once, in routine use.
2. `retrospect.py` has produced at least one attended cohort with
   recorded human approval decisions, so the concordance samples that
   qualification depends on have somewhere to come from.
3. Someone wants it. Attended mode with terminal approvals is cheap for
   the domain expert (seconds of process ownership per segment); the
   case for this mode rests on approval *latency* across time zones or
   working hours, which has not yet been observed as a problem.
