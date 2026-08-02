# Proposal: the domain-expert cockpit — cost tracking and gate-driven improvement

**Status:** revision 2, after a three-lens adversarial review (non-programmer
persona walkthrough, spec/code feasibility, product scope) — 7 blockers and
12 majors from revision 1 are addressed in place; the review log at the end
records what changed. Targets lockstep v0.3.x. Nothing here touches a frozen
surface without an explicit amendment note; every driver change is additive
and lands behind config.

## Goal

Let a **non-programmer domain expert** (DE) run substantial engineering work
through lockstep by talking to an **orchestrator agent** in WezTerm — the DE
answers domain questions and approves outcomes; agent nodes do the software
engineering; the human never touches git, JSON, or flow files beyond two
scripted finger-moves (pressing Enter on a pre-typed command, and typing a
single letter at an approval prompt). Two repo features make this
sustainable:

1. **Per-node cost tracking** — every run reports what each node spent
   (spawns, tokens, wall time — dollars only where a harness actually
   reports them), visible to both the DE and the orchestrator.
2. **Gate-driven improvement** — the friction the system already records
   (gate blocks, heal rounds, corrective re-spawns) is aggregated across
   runs, turned into concrete human-approved improvements, and **measured**:
   every applied improvement is checked against before/after cohorts.

## Non-goals

- No web dashboard, no server, no database. The observation surface stays
  plain files + CLI (SPEC §12); WezTerm panes are a *view*, never a
  dependency — and never a hazard to the run (see cockpit reader rules).
- No driver-side chat or session features (§15 stays "build-loop tool"); no
  mid-flight prompt injection (steering is checkpoint-consumed — a
  **permanent non-goal** per §16.2/r6, which this design respects rather
  than works around).
- No change to frozen surfaces: exit codes, `format_version` 1.x semantics,
  §7 fencing/footer, hash composition (M3). Where a feature touches the
  hash boundary (the `usage_fields` stanza-digest rule, below) the amendment
  text is named as a prerequisite, not assumed.
- No autonomous self-modification: improvement proposals are applied only
  through the existing reviewed-and-approved SDLC flows.

## Personas and trust model

| Principal | Does | Never does |
|---|---|---|
| Domain expert (DE) | Talks to the orchestrator; answers domain questions; presses Enter on pre-typed commands; types `a`/`r` at approval prompts; says STOP | git, JSON, flow authoring, composing CLI commands, judgment calls about locks/resumes |
| Orchestrator (interactive pi session) | Authors/runs flows per `docs/DRIVING-LOCKSTEP.md`; runs them **detached** (below); translates run state; relays domain questions; manages panes; owns recovery | Approvals (structurally cannot — non-TTY auto-reject); editing live lineages; spending without stated budgets |
| Agent nodes (headless spawns) | The engineering | Control flow; asking questions directly (see clarification gates) |
| Human engineer (occasional) | Reviews improvement batches; owns `lockstep.toml`, personas, and the r7 amendment | — |

Ground truth stays the run dir. The DE's *primary* trust anchor is not
citations they cannot read — it is (a) a mechanical, summary-free DE-tier
status rendering produced by the cockpit script, not by the orchestrator,
and (b) a standing retrospective check ("DE was told X, state said Y") that
audits narration drift systematically. Orchestrator narrations must still
map to a citable run-dir artifact (event, status line, gate `result.json`,
or the run's `flow.tg.json` copy) so the engineer can audit any dispute.

## Approach

### A. The cockpit (UX proposal)

**Execution model (load-bearing, stated explicitly):** the orchestrator
NEVER blocks on `lockstep run`, and every detached run MUST have non-TTY
stdin — a background job, or a pane with stdin redirected from NUL. This is
what makes the approval guarantee *structural*: a run hosted in a bare pty
pane would pass `isatty()`, sit silently at the approval `input()` forever
instead of auto-rejecting, and die if the pane closed. Detached-with-null
stdin, the orchestrator keeps conversing, polling `status`/`events.jsonl`
between chat turns. A mute
orchestrator would forfeit narration, question relay, and STOP; a blocked
CHAT pane is a broken cockpit. At the work machine (~90 s pi round-trips),
the cockpit leans on the MISSION/ACTIVITY panes for status and reserves CHAT
for decisions and domain questions.

**Layout — one WezTerm tab per run, fixed pane roles:**

```
┌─ tab: weekly-report (run 2026-08-01) ─────────────────────────────┐
│ ┌──────────────────────────┬──────────────────────────────────┐  │
│ │ CHAT — orchestrator      │ ACTIVITY — tail of the running   │  │
│ │ (interactive pi; DE      │ node's progress.jsonl (r6        │  │
│ │ lives here)              │ checkpoints) + heartbeat line;   │  │
│ │                          │ raw stdout on request            │  │
│ ├──────────────────────────┴──────────────────────────────────┤  │
│ │ MISSION — DE-tier status (fixed glossary), spend line;      │  │
│ │ raw `lockstep status` table below it for engineers          │  │
│ └──────────────────────────────────────────────────────────────┘  │
│   (+ transient titled panes the orchestrator spawns on demand:    │
│    APPROVAL, or a tail of a specific node)                        │
└────────────────────────────────────────────────────────────────────┘
```

- **ACTIVITY tails `progress.jsonl`**, not `stdout.log`: JSON-mode harnesses
  emit stdout only at the end, so a stdout tail looks hung for minutes and
  then dumps raw JSON — the opposite of reassurance. Progress checkpoints
  are one plain line each; a heartbeat ("working — 4 m elapsed") guarantees
  blank never means dead. "Show me the raw output" opens a stdout pane on
  request.
- **MISSION is two-tier.** Top: a DE-tier rendering generated mechanically
  by the cockpit script from the run dir — `state.json` for statuses plus
  the run's `flow.tg.json` copy for the denominators ("of 2" heal rounds,
  "of 25" budget) — fixed glossary
  (`running / waiting / sent back for rework (1 of 2) / needs you / done`),
  spend line (`agent tasks used 9 of 25` always; tokens/dollars when
  available), and for map nodes a collapsed line (`files checked: 12 of 40,
  1 redone`). Bottom: the raw `lockstep status` table. The DE-tier text is
  summary-free by construction (field mapping, no model), which is what
  makes it a trust anchor.
- The orchestrator is the pane manager (`wezterm cli` split/kill/set-title):
  "show me the reviewer" → a titled tail pane. Map fan-outs get ONE
  newest-active-item pane, never N panes.

**Cockpit reader rules (a view must never hurt the run):** the driver's
`state.json` writes are atomic-replace with retries precisely because this
machine's AV already causes transient `PermissionError`s — a naive poller
holding the file open can make `os.replace` fail and take the run down with
it. The cockpit script therefore opens `state.json` with
`FileShare ReadWrite|Delete` (or copies then parses), never holds the
handle, polls at ≥1 s, ignores `*.tmp`, tolerates a partial trailing
`events.jsonl` line, and treats every pane error as display-only.

**Deliverables:**
- `contrib/start-cockpit.cmd` — the **double-clickable entry point**, and the
  only way a DE ever starts or restarts the system. It opens WezTerm, starts
  the orchestrator with a standing boot protocol, and launches the MISSION
  loop. The boot protocol IS the recovery path: scan `runs/` for unfinished
  lineages and check the lock-holder pid (the lockfile records pid + host).
  **Lock pid dead + stale `running` statuses ⇒ resume is safe** — the
  engine already auto-clears same-host dead-pid locks on a plain `resume`;
  `--force-unlock` is the documented fallback only, and the rule is
  mechanical (never a judgment call, least of all the DE's). **Lock pid
  alive ⇒ the detached run survived the orchestrator's death** — the normal
  case after a pi session-limit kill: reattach the view, narrate "still
  working," and do NOT unlock. Then: "last night's run stopped at step 6
  of 9 — nothing is lost; say continue to resume." Cold start and the
  morning after are the same double-click.
- `contrib/cockpit.ps1 <run_dir>` — pwsh successor to `wezterm-watch.sh`
  implementing the layout, the DE-tier renderer, and the reader rules.
  Invoked by the orchestrator/launcher, never typed by the DE. Fallback
  without wezterm: plain status loop.

**The DE conversation loop:**

1. DE states intent in CHAT. The orchestrator picks/adapts a flow, then runs
   a **budget-consent beat**: it states the cap in honest units ("up to 25
   agent tasks; on this machine I can count tasks, not dollars — last week's
   similar run was about $N on the bill") and waits for an explicit go.
2. Orchestrator launches the run detached and narrates transitions from the
   run dir (sources: `events.jsonl` + `lockstep status` + gate
   `result.json` + the run's `flow.tg.json` copy — events alone don't carry
   findings counts or heal budgets, and a healing gate emits `heal-round`
   rather than `blocked`, so the narrator reads all four). A small glossary
   is kept in the DE docs ("sent back for rework: the checker rejected it;
   this can happen at most N times").
3. **Domain questions via clarification gates** (pattern below). The
   orchestrator relays questions in plain language, echoes the DE's answer
   back for confirmation BEFORE folding it in (answers are effectively
   permanent — see the pattern), then steers + resumes.
4. **Approvals: the segmentation rule.** DE-facing flows place approval
   nodes at the END of their flow — precisely: **nothing non-trivial may
   run downstream of an approval**, because everything after it executes in
   the DE's resume process; a seconds-long shell node (summary print,
   deliverable copy) is fine, an implement half is not. The
   `plan-adversarial` → `implement-heal` chain is the flagship shape;
   monolithic `sdlc-e2e` is explicitly unsuitable for the cockpit until an
   r7 approval mechanic exists. The
   orchestrator's detached run reaches the approval and auto-rejects — exit
   6 is the *designed handoff signal*, narrated as "ready for your
   decision." The orchestrator then spawns a titled APPROVAL pane with
   `lockstep resume <run_dir>` **pre-typed** via `wezterm cli send-text` —
   the DE presses Enter, the blocked approval re-runs on a real TTY, and
   the DE answers the actual prompt, which is `[a]pprove / [r]eject /
   [e]dit` (documented verbatim; the DE is briefed "type a or r, then
   Enter — never e"; if anything unexpected appears, paste it into CHAT).
   Because the approval is terminal, the DE's process exits seconds later
   and the orchestrator launches the next segment. No typo surface (nothing
   is composed by hand), no run ownership transfer (the DE's process lives
   for seconds, not hours).
5. **STOP is a reserved word.** If the DE says STOP in CHAT, the
   orchestrator must: `lockstep cancel` the running node(s), not resume,
   and report what was spent. (Physical red button: closing the APPROVAL
   pane or pressing Ctrl-C in a run pane kills that process; the boot
   protocol makes this recoverable, and the DE docs say so — "closing the
   laptop never loses paid work.")
6. Completion: the flow's final shell node copies the deliverable OUT of
   sensitive `runs/` into a designated `Deliverables/` folder; the
   orchestrator opens it (`Start-Process`) and appends the cost table. The
   run dir itself remains internal.
7. **Queued asks:** one run per tab; a second ask while one runs is queued
   by default ("I'll start it when the current run finishes — say 'run it
   now' to open a second tab"), keeping spend serial unless the DE opts in.

### A.1 The clarification-gate pattern (corrected mechanism)

The rev-1 mechanism ("answers as heal-text", "steer for running nodes") was
reviewed against the engine and does not exist: heal text is composed by the
engine exclusively from the gate's Verdict, and steering never reaches an
in-flight spawn (checkpoint consumption is r6's design and mid-flight
injection is a permanent non-goal). The pattern that works, verified against
the code, uses only shipped mechanics:

- The clarify gate is a normal gate with **`heal.max_rounds: 0`** whose
  contract output phrases underspecification as findings with
  `category: "question"`. A heal-enabled clarify gate would be a bug: heal
  fires immediately in-process, re-running the target with unanswered
  questions and burning rounds.
- On block (exit 2), the orchestrator reads the questions from
  `phases/<gate>/result.json`, relays them, and echo-confirms the DE's
  answers.
- Answers travel via **`lockstep steer <run> <target> "<answers>"`** — the
  target, not the gate — then `lockstep resume`: the blocked gate re-runs,
  the done target re-runs because of unconsumed mail (r6 C2), and the
  steering block folds into its prompt AND hash.
- **Consumption is verifiable**: after the re-spawn, the steering block is
  visible in `phases/<target>/prompt.txt` — the orchestrator confirms it
  there ("your answer is in the new instructions") rather than asserting it.
  (r7 nicety: a `steer-consumed` event, batched below.)
- **Answers are effectively permanent**: the mailbox renders in full into
  every later prompt and folds into the hash; a correction is appended
  beside the original, and true retraction means `--fresh` (re-bills the
  lineage). Hence the mandatory echo-confirm, and a warning before steering
  any node whose output the DE has already been shown (re-running it may
  change that output and invalidate descendants).

Deliverables: a clarify-gate fragment in `flows/starter/`, plus
"Clarification gates" sections in `FLOW-AUTHORING.md` (gate shape) and
`DRIVING-LOCKSTEP.md` (relay etiquette, echo-confirm, consumption check,
done-node warning).

### B. Per-node cost tracking (feature)

- **v0-prereq (step 0): probe the envelopes.** Five minutes per machine:
  run one node per stanza, inspect `stdout.log` — what usage fields does
  claude-code's envelope carry; does pi's `--mode json` envelope carry any;
  copilot-cli has no JSON mode at all. The probe's results shape the field
  map AND the v1 amendment; it runs before any code is written.
- **v0 — summarizer, no driver change.** `contrib/cost_report.py
  <run_dir>...` (accepts MULTIPLE run dirs — with terminal approvals, a
  deliverable spans a chain of runs, and "what did this report cost" must
  roll up; the orchestrator tags related runs via a naming convention or
  label file). Walks `phases/*/` including rotated attempts and map items
  — retries cost money too (envelopes are preserved per attempt; the
  `json_field` unwrap is in-memory, so `stdout.log*` retains them; verified
  against the executor). Emits per-node/per-run/per-deliverable tables:
  spawns, attempts, heal rounds, tokens, wall time (from `events.jsonl`
  transition-pair timestamps — `state.json` spans mislead on re-runs; map
  items have no transition events, so item wall time is best-effort file
  mtimes). **Units policy:** spawns/tokens/wall-time are the primary
  columns; dollars appear only where the envelope reports them, labeled
  notional (both machines actually bill in quota/limits). Executors with no
  envelope print "no envelope", never a fake 0. Field maps live in an
  operator-owned sidecar config (not hardcoded — and not in `lockstep.toml`
  yet: the stanza model is `extra="forbid"`), pre-proving the v1 shape.
  Flows may append a final shell node running it (it derives the run dir
  from `LOCKSTEP_PHASE_DIR/../..` — no `{run_dir}` interpolation form
  exists — and excludes its own cost).
- **v1 — driver capture, additive, behind config — with the digest rule
  named.** Stanza key `usage_fields = {...}`; extraction after each
  attempt; a new `usage` event kind; per-node accumulation in `state.json`;
  a spend column in `status`. **Hash impact is NOT "none"** (rev-1 error):
  the harness fingerprint includes the whole-stanza digest (r5 B1), so
  adding the field to the model — even unset — would change every stanza's
  digest and silently re-bill every cached node on upgrade. The r7
  amendment MUST therefore redefine the stanza digest to exclude
  `usage_fields` (defensible: it changes what the driver reads back, never
  what the spawn does) or explicitly adopt the one-time invalidation; the
  digest rule is amendment text on the hash-composition boundary, not an
  implementation detail. `doctor` extends to check configured
  `usage_fields` resolve against a real probe envelope.
- **v2 — token/cost budgets** (`budget.max_tokens`/`max_cost`, exit 4):
  deferred until v1 numbers prove out; needs §9.5 amendment text, which
  must state that enforcement is executor-dependent (only metered stanzas
  bind). Until then `max_agent_spawns` stays the lever — it already counts
  heal rounds and correctives.

### C. Gate-driven improvement (feature)

**Data sources, corrected:** the per-round truth lives in `events.jsonl`
(transitions, `heal-round`, timestamps) and the gates'
`phases/<gate>/result.json` + rotated `result-attempt<n>.json` (full finding
bodies per round); the run's `flow.tg.json` copy carries gate budgets.
`state.json` holds only a lossy latest verdict string and the heal-round
counter — it is NOT sufficient alone (rev-1 error). Corrective re-spawns are
currently **not evented** (rev-1 claimed they were): v0 counts them by
matching the fixed corrective-preamble marker inside `prompt*.txt` file
bodies — current AND rotated (a corrective that succeeded is often the last
spawn, so its marker sits in `prompt.txt`, never rotated) — marker match
only, bodies never shipped.

- **v0 — friction report.** `contrib/retrospect.py <runs-root>`: aggregates
  across run dirs, **grouped by `(flow, flow_hash)` cohort** — every applied
  improvement changes `flow_hash`, so before/after cohorts come free.
  **Privacy projection specified:** node ids, gate ids, counts, rounds,
  severities, `Finding.category`, cost numbers — `claim`/`evidence`/
  `reason`/heal-text bodies are stripped or hard-truncated (they routinely
  quote code and prompts; "metadata only" must be enforced by projection,
  not assumed). The aggregator skips pi `--session-dir` transcript subtrees
  (A.3.4: transcripts are never node inputs). Output stays under `runs/`
  (it remains sensitive) or stdout.
- **Measurement is part of the feature, not an afterthought:** the report's
  trend section states, for each previously applied improvement (identified
  by the commit-trailer finding ID), whether its target metric moved across
  cohorts. The feature's success metric: heal-rounds-per-run and
  blocks-per-gate for a touched flow decline within N runs of an applied
  batch — and a regression is a first-class retro finding.
- **Consumption path, default-first:** the orchestrator reads the friction
  report and *discusses* improvements with the human; upheld ones ride
  `plan-adversarial` → `implement-heal` with approval, one batch per cycle,
  provenance trailers on every commit. The fancier
  `flows/starter/retrospect.tg.json` (shell aggregator → analyst →
  arbiter) is shipped but its adoption is gated on the discussion path
  having produced at least one applied-and-measured batch — no analysis
  ceremony before the loop has closed once by hand.
- **v2 — only on demonstrated need:** `lockstep report <run_dir>` as a CLI
  verb (§3 amendment; contrib scripts prove the shape first).
- **Standing retro category:** "DE was told X, state said Y" — narration
  drift is audited by the retrospective every cycle, not only when someone
  notices.

**Addendum-A conformance** (verified in review): nothing here routes control
flow through extensions; retro reading past run dirs is cross-run forensics
as fenced data, which A.3.3 anticipates — not the intra-lineage re-run
contamination note 4 prohibits.

## Step-by-step changes (resequenced: mechanics and pilot before polish)

0. **Envelope probe on both machines** (5 min each) — shapes B's field maps
   and the r7 draft. *(B)*
1. `contrib/cost_report.py` (+ sidecar field-map config) + offline tests:
   synthetic envelopes for both stanza shapes, rotated attempts, map items,
   multi-run rollup, "no envelope" rendering. *(B-v0)*
2. Clarification-gate pattern + approval-segmentation rule: starter
   fragment, `FLOW-AUTHORING.md`/`DRIVING-LOCKSTEP.md` sections (incl. the
   dead-pid force-unlock rule and exit-6-as-handoff narration). *(A)*
3. **Pilot checkpoint with a named DE** — a real person, success/abort
   criteria written before the session (below). Cockpit polish and all
   driver work are gated on its findings. If no pilot user can be named,
   workstream A shrinks to the pattern docs (which serve the maintainer
   regardless). *(A)*
4. `contrib/start-cockpit.cmd` + `contrib/cockpit.ps1` (DE-tier renderer,
   reader rules, boot/recovery protocol) + scripted manual test protocol.
   *(A)*
5. `contrib/retrospect.py` + offline tests (synthetic run dirs derived from
   a sanitized real run; blocks, heals, corrective markers, cohorts).
   *(C-v0)*
6. `flows/starter/retrospect.tg.json` + README row (adoption gated per
   §C). *(C-v1)*
7. r7 amendment batch: `usage_fields` stanza key + **stanza-digest
   exclusion rule**, new event kinds reserved against §10.1 (`usage`,
   `corrective`, `steer-consumed`), state accumulation + status column —
   batched with the two r7 items DEVIATIONS already queues. Implement only
   after adoption. *(B-v1)*
8. Defer: token budgets (B-v2), `report` verb (C-v2), monolithic-flow
   approval mechanic (r7 candidate).

## Test plan

- Contrib scripts: offline unit tests against synthetic fixtures (no
  tokens); existing suite stays green throughout; flow verification for the
  retro flow and clarify fragment.
- B-v1 (post-amendment): fake-executor envelopes with usage fields; a
  stanza-digest test pinning that adding/setting `usage_fields` does NOT
  change cached-node hashes; live smoke (`LOCKSTEP_LIVE`) reconciling v0
  totals against provider-reported envelope numbers — not merely asserting
  parse success.
- Cockpit: scripted manual protocol (spawn, refresh across waves, map
  collapse, reader-rule compliance under a running engine, fallback path,
  boot-recovery against a killed run with a held lock).
- **Pilot session (step 3):** a named non-programmer runs the
  `plan-adversarial` → `implement-heal` chain end-to-end using only CHAT,
  the pre-typed APPROVAL pane, and STOP. Success bar: (a) zero moments
  where the DE composes a command or reads JSON; (b) N comprehension
  spot-checks — the DE states what is running / blocked / spent, compared
  against the state files, zero discrepancies; (c) the DE can recover a
  deliberately killed run via the double-click path alone. Abort criteria
  written with the pilot's name.

## Risks and mitigations

- **pi envelope carries no usage fields** — cost on the work machine
  degrades to spawns/wall-time. Mitigated by design: spawns are always
  tracked, units policy never promises dollars the envelope can't back, and
  step 0 resolves the unknown before code is written.
- **Narration drift** — mitigated structurally: the DE-tier MISSION text is
  script-generated (not narrated), narrations must cite run-dir artifacts,
  and the retro audits told-vs-state every cycle.
- **Cockpit reader destabilizing the run** — the reader rules above;
  treated as a hard requirement of `cockpit.ps1`, tested under a live
  engine in the manual protocol.
- **Steer permanence** — a wrong DE answer is effectively unretractable
  short of `--fresh`. Mitigated by echo-confirm etiquette and the
  done-node warning; accepted residual: a confirmed-but-wrong answer costs
  a fresh lineage.
- **Improvement-loop instability** — one batch per cycle, approval-gated,
  provenance trailers, and cohort measurement that makes a regressing
  batch visible in the next report.
- **Two-machine maintenance of contrib scripts** — pwsh-first (both
  machines are Windows), field maps in config not code, and the pilot gate
  keeps the surface small until the loop is proven.
- **Session limits kill long runs** (known) — the boot protocol makes
  recovery a double-click; approval segmentation keeps DE-owned processes
  seconds-long; detached execution means an orchestrator death never takes
  the run with it (and vice versa).

## Open questions

1. ~~pi envelope fields~~ → promoted to step 0 (probe, don't wonder).
2. ~~steer vs heal for answers~~ → resolved: answers travel via steer +
   resume only; heal text is engine-owned (§A.1).
3. Deliverables handoff beyond `Deliverables/` + `Start-Process` — does the
   DE need email/share integration, and who owns that boundary? (Explicitly
   out of scope for v0.3; revisit at the pilot.)
4. Concurrent runs for one DE: queued-by-default policy is set (§A step 7);
   whether MISSION ever needs a cross-run overview waits for real demand.
5. Approval mechanic for monolithic flows (mid-flow approvals without run
   ownership transfer) — r7 candidate; until then segmentation is the rule.

## Review log (rev 1 → rev 2)

Adversarial findings addressed: approval mechanic rebuilt around terminal
approvals + pre-typed `resume` pane (was: unworkable "paste into CHAT,
y/n"); clarification answers re-mechanized on steer+resume with
`max_rounds: 0` gates (was: nonexistent heal-text channel and §16.2
conflict); cold-start/recovery given an owner (`start-cockpit.cmd` boot
protocol, mechanical force-unlock rule); detached execution model stated
(was: implicitly mute orchestrator); `usage_fields` stanza-digest re-billing
acknowledged and made an amendment prerequisite (was: "hash impact: none");
ACTIVITY re-pointed at `progress.jsonl`; MISSION split into script-generated
DE tier + raw tier; STOP + budget-consent beat added; deliverable handoff
defined; retro data sources corrected (events + gate result rotations, not
state.json; correctives counted via rotated-prompt markers pending an
event); privacy made a projection, not a claim; measurement (flow_hash
cohorts, applied-batch trends) made part of feature C; sequencing reordered
loop-before-view with a named-pilot gate; cost units re-based on
spawns/tokens with notional dollars; multi-run deliverable rollup added.
