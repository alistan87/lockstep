# Proposal: the domain-expert cockpit — cost tracking, gate-driven improvement, and unattended mode

**Status:** revision 5 — a **consolidation-only** merge of revision 2 with
the rev 3 amendment (second adversarial pass, narrator seam: B1, B2, M1,
M2, M3, m1) and the rev 4 amendment (pane-grammar pass L-B1..L-M2;
unattended-mode design + pass U-B1..U-B4, U-M1..U-M2) applied at their
anchors. No new normative content is introduced in this revision; the
cumulative review log at the end records every change by name. Targets
lockstep v0.3.x. Nothing here touches a frozen surface without an explicit
amendment note; every driver change is additive and lands behind config.

## Goal

Let a **non-programmer domain expert** (DE) run substantial engineering
work through lockstep by talking to an **orchestrator agent** in WezTerm —
the DE answers domain questions and approves outcomes; agent nodes do the
software engineering; the human never touches git, JSON, or flow files
beyond two scripted finger-moves (pressing Enter on a pre-typed command,
and typing a single letter at an approval prompt). Three repo features
make this sustainable:

1. **Per-node cost tracking** — every run reports what each node spent
   (spawns, tokens, wall time — dollars only where a harness actually
   reports them), visible to both the DE and the orchestrator.
2. **Gate-driven improvement** — the friction the system already records
   (gate blocks, heal rounds, corrective re-spawns) is aggregated across
   runs, turned into concrete human-approved improvements, and
   **measured**: every applied improvement is checked against
   before/after cohorts.
3. **Unattended mode (§D)** — a run may proceed past most *intermediate*
   human gates on system-rendered, evidence-citing judgment with deferred
   human review — under earned, per-class, excursion-revocable
   qualification, and never across the egress/spend/sensitivity floor.

## Non-goals

- No web dashboard, no server, no database. The observation surface stays
  plain files + CLI (SPEC §12); WezTerm panes are a *view*, never a
  dependency — and never a hazard to the run (see §A.3 reader rules).
- No driver-side chat or session features (§15 stays "build-loop tool");
  no mid-flight prompt injection (steering is checkpoint-consumed — a
  **permanent non-goal** per §16.2/r6, which this design respects rather
  than works around).
- No change to frozen surfaces: exit codes, `format_version` 1.x
  semantics, §7 fencing/footer, hash composition (M3). Where a feature
  touches the hash boundary (the `usage_fields` stanza-digest rule, §B)
  the amendment text is named as a prerequisite, not assumed.
- No autonomous self-modification: improvement proposals are applied only
  through the existing reviewed-and-approved SDLC flows, and unattended
  mode structurally excludes self-modifying flows (§D, U-M2).
- **The human channel is never forged** (§D, U-B1): interactive approvals
  are answered only by a human at a real TTY; no mode, extension, or
  automation ever types into an approval prompt.

## Personas and trust model

| Principal | Does | Never does |
|---|---|---|
| Domain expert (DE) | Talks to the orchestrator; answers domain questions; presses Enter on pre-typed commands; types `a`/`r` at approval prompts; says STOP | git, JSON, flow authoring, composing CLI commands, judgment calls about locks/resumes |
| Orchestrator (interactive pi session) | Authors/runs flows per `docs/DRIVING-LOCKSTEP.md`; runs them **detached** (§A); translates run state; relays domain questions; manages panes per §A.3; owns recovery; writes the cockpit journal (§A.2) | Approvals (structurally cannot — non-TTY auto-reject; in unattended mode, judgment is rendered by judge *gates*, never by the orchestrator); editing live lineages; spending without stated budgets |
| Agent nodes (headless spawns) | The engineering; in unattended mode, judge gates render triage verdicts citing mechanical evidence (§D) | Control flow beyond sanctioned gate blocks; asking questions directly (see §A.1) |
| Human engineer (occasional) | Reviews improvement batches and deferred-approval ledgers; owns `lockstep.toml`, personas, sidecar configs, and the r7 amendment | — |

Ground truth stays the run dir. The DE's *primary* trust anchor is not
citations they cannot read — it is (a) a mechanical, summary-free DE-tier
status rendering produced by the cockpit script, not by the orchestrator,
and (b) a standing retrospective check ("DE was told X, state said Y",
computed per §C from the cockpit journal) that audits narration drift
systematically. Orchestrator narrations must still map to a citable
run-dir artifact (event, status line, gate `result.json`, or the run's
`flow.tg.json` copy) so the engineer can audit any dispute.

## Approach

### A. The cockpit (UX)

**Execution model (load-bearing, stated explicitly):** the orchestrator
NEVER blocks on `lockstep run`, and every detached run MUST have non-TTY
stdin — a background job, or a pane with stdin redirected from NUL. This
is what makes the approval guarantee *structural*: a run hosted in a bare
pty pane would pass `isatty()`, sit silently at the approval `input()`
forever instead of auto-rejecting, and die if the pane closed.
Detached-with-null stdin, the orchestrator keeps conversing, polling
`status`/`events.jsonl` between chat turns. A mute orchestrator would
forfeit narration, question relay, and STOP; a blocked CHAT pane is a
broken cockpit. At the work machine (~90 s pi round-trips), the cockpit
leans on the MISSION/ACTIVITY panes for status and reserves CHAT for
decisions and domain questions.

**Layout — one WezTerm tab per deliverable lineage, fixed pane roles**
(title carries `segment k of n`; full grammar in §A.3):

```
┌─ tab: weekly-report · seg 1 of 2 ─────────────────────────────────┐
│ ┌──────────────────────────┬──────────────────────────────────┐  │
│ │ CHAT — orchestrator      │ ACTIVITY — tail of the running   │  │
│ │ (interactive pi; DE      │ node's progress.jsonl (r6        │  │
│ │ lives here)              │ checkpoints) + heartbeat line;   │  │
│ │                          │ raw stdout on request            │  │
│ ├──────────────────────────┴──────────────────────────────────┤  │
│ │ MISSION — DE-tier status (fixed glossary), spend line;      │  │
│ │ raw `lockstep status` table below it for engineers          │  │
│ └──────────────────────────────────────────────────────────────┘  │
│   (+ transient titled panes per §A.3: APPROVAL, request-tail,     │
│    raw-stdout)                                                    │
└────────────────────────────────────────────────────────────────────┘
```

- **ACTIVITY tails `progress.jsonl`**, not `stdout.log`: JSON-mode
  harnesses emit stdout only at the end, so a stdout tail looks hung for
  minutes and then dumps raw JSON — the opposite of reassurance. Progress
  checkpoints are one plain line each; a heartbeat ("working — 4 m
  elapsed") guarantees blank never means dead. "Show me the raw output"
  opens a stdout pane on request. All tails use the `Tail-RunFile`
  primitive (§A.3, L-B2).
- **MISSION is two-tier.** Top: a DE-tier rendering generated mechanically
  by the cockpit script from the run dir — `state.json` for statuses plus
  the run's `flow.tg.json` copy for the denominators ("of 2" heal rounds,
  "of 25" budget) — fixed glossary (`running / waiting / sent back for
  rework (1 of 2) / needs you / done`, plus the unattended glossary line
  per §D, U-M1), spend line (`agent tasks used 9 of 25` always;
  tokens/dollars when available), and for map nodes a collapsed line
  (`files checked: 12 of 40, 1 redone`). Bottom: the raw `lockstep
  status` table. The DE-tier text is summary-free by construction (field
  mapping, no model), which is what makes it a trust anchor.
- The orchestrator is the pane manager (`wezterm cli`
  split/kill/set-title) under the §A.3 rules: "show me the reviewer" → a
  titled request-tail pane. Map fan-outs get ONE newest-active-item pane,
  never N panes.

**Deliverables:**
- `contrib/start-cockpit.cmd` — the **double-clickable entry point**, and
  the only way a DE ever starts or restarts the system. It opens WezTerm,
  starts the orchestrator with a standing boot protocol, and launches the
  MISSION loop. The boot protocol IS the recovery path: scan `runs/` for
  unfinished lineages and check the lock-holder pid (the lockfile records
  pid + host). **Lock pid dead + stale `running` statuses ⇒ resume is
  safe** — the engine already auto-clears same-host dead-pid locks on a
  plain `resume`; `--force-unlock` is the documented fallback only, and
  the rule is mechanical (never a judgment call, least of all the DE's).
  **Lock pid alive ⇒ the detached run survived the orchestrator's
  death** — the normal case after a pi session-limit kill: reattach the
  view, narrate "still working," and do NOT unlock. After the lock/pid
  scan, the boot protocol **reads the cockpit journal tail (§A.2)** and
  resumes the conversation from it: consent already given is not re-asked
  (restated in one line: "you approved up to 25 tasks; 9 used"), answered
  clarifications are summarized not re-asked, standing `note` entries are
  re-loaded, and the last `handoff`/`stop` frames the "here's where we
  are" narration. Pane ids from before the restart are never trusted: the
  boot protocol re-lists via `wezterm cli list`, kills orphans, and
  respawns from lineage state (§A.3). Then: "last night's run stopped at
  step 6 of 9 — nothing is lost; say continue to resume." Cold start and
  the morning after are the same double-click.
- `contrib/cockpit.ps1 <run_dir>` — pwsh successor to `wezterm-watch.sh`
  implementing the layout, the DE-tier renderer, the `Tail-RunFile`
  primitive, and the reader rules (§A.3). Invoked by the
  orchestrator/launcher, never typed by the DE. Fallback without wezterm:
  plain status loop.
- Starter fragments in `flows/starter/`: the clarify-gate fragment
  (§A.1) and the **evidence-bearing approval fragment** (pattern: extract
  shell node → approval node whose prompt body includes the extract),
  plus "Clarification gates" and "Evidence rule" sections in
  `FLOW-AUTHORING.md` and relay/handoff etiquette in
  `DRIVING-LOCKSTEP.md`.

**The DE conversation loop:**

1. DE states intent in CHAT. The orchestrator picks/adapts a flow, then
   runs a **budget-consent beat**: it states the cap in honest units ("up
   to 25 agent tasks; on this machine I can count tasks, not dollars —
   last week's similar run was about $N on the bill"), states the mode
   (attended by default; unattended per §D requires its own consent
   terms), and waits for an explicit go. The consent is journaled (§A.2).
2. Orchestrator launches the run detached and narrates transitions from
   the run dir (sources: `events.jsonl` + `lockstep status` + gate
   `result.json` + the run's `flow.tg.json` copy — events alone don't
   carry findings counts or heal budgets, and a healing gate emits
   `heal-round` rather than `blocked`, so the narrator reads all four).
   A small glossary is kept in the DE docs ("sent back for rework: the
   checker rejected it; this can happen at most N times").
3. **Domain questions via clarification gates** (§A.1). The orchestrator
   relays questions in plain language — quoting the gate's finding
   verbatim alongside the relay — echoes the DE's answer back for
   confirmation BEFORE folding it in (answers are effectively permanent),
   journals the exchange as a `clarify` triple (§A.2), then steers +
   resumes.
4. **Approvals: the segmentation rule.** DE-facing flows place approval
   nodes at the END of their flow — precisely: **nothing non-trivial may
   run downstream of an approval**, because everything after it executes
   in the DE's resume process; a seconds-long shell node (summary print,
   deliverable copy) is fine, an implement half is not. The
   `plan-adversarial` → `implement-heal` chain is the flagship shape;
   monolithic `sdlc-e2e` is explicitly unsuitable for the cockpit until
   an r7 approval mechanic exists. The orchestrator's detached run
   reaches the approval and auto-rejects — exit 6 is the *designed
   handoff signal*, narrated as "ready for your decision."

   **The evidence rule (B1).** A DE-facing approval node MUST render its
   evidence into its own TTY output, so the APPROVAL pane shows the DE
   what they are approving — never a narrated summary of it. Evidence is
   a mechanical extract of the deliverable produced by the flow, not by
   the orchestrator: the plan's headings and per-section first lines, a
   diff stat, the gate's findings table, or the full document when it is
   short. The flow author chooses the extract; the extraction is
   deterministic (shell/compute node output rendered by the approval
   prompt), so the evidence channel has the same trust status as the
   MISSION DE tier. The orchestrator MAY gloss the evidence in CHAT but
   the DE is briefed: *decide from the pane, not the chat.* A flow whose
   approval shows no evidence is unsuitable for the cockpit — same
   register as the `sdlc-e2e` exclusion.

   **The quiescence check (B2, mandatory, mechanical).** Before spawning
   the APPROVAL pane the orchestrator MUST verify, from run-dir state
   alone, that the *only* runnable node is the approval: (a) no steering
   mail is unconsumed by a done or pending target; (b) no non-approval
   node is pending, failed-retryable, or invalidated. If the check fails,
   the orchestrator resumes **detached** first, lets the engine burn down
   the queue to the approval's auto-reject again, and only then hands
   over (pane choreography per §A.3's state machine). The check is a
   fixed procedure documented in `DRIVING-LOCKSTEP.md`
   ("quiescent-except-approval"), never a judgment call. Rule of thumb:
   **any steer after the last detached resume ⇒ not quiescent until a
   detached resume has consumed it.**

   The orchestrator then spawns a titled APPROVAL pane with `lockstep
   resume <run_dir>` **pre-typed** via `wezterm cli send-text`, under the
   §A.3 target-integrity rule (L-B1: only into a pane spawned and
   title-verified in the same action sequence) — the DE presses Enter,
   the blocked approval re-runs on a real TTY, and the DE answers the
   actual prompt, which is `[a]pprove / [r]eject / [e]dit` (documented
   verbatim; the DE is briefed "type a or r, then Enter — never e"; if
   anything unexpected appears, paste it into CHAT). The handoff is
   journaled (§A.2). Because the approval is terminal, the DE's process
   exits seconds later and the orchestrator launches the next segment. No
   typo surface (nothing is composed by hand), no run ownership transfer
   (the DE's process lives for seconds, not hours).
5. **STOP is a reserved word.** If the DE says STOP in CHAT, the
   orchestrator must: `lockstep cancel` the running node(s), not resume,
   report what was spent, and journal the `stop` (§A.2). (Physical red
   button: closing the APPROVAL pane or pressing Ctrl-C in a run pane
   kills that process; the boot protocol makes this recoverable, and the
   DE docs say so — "closing the laptop never loses paid work.") STOP
   semantics are mode-independent (§D).
6. Completion: the flow's final shell node copies the deliverable OUT of
   sensitive `runs/` into a designated `Deliverables/` folder — always
   behind a human approval (§D, U-B4: egress is never unattended); the
   orchestrator opens it (`Start-Process`) and appends the cost table.
   The run dir itself remains internal.
7. **Queued asks:** one deliverable per tab; a second ask while one runs
   is queued by default ("I'll start it when the current run finishes —
   say 'run it now' to open a second tab"), keeping spend serial unless
   the DE opts in.

### A.1 The clarification-gate pattern

The rev-1 mechanism ("answers as heal-text", "steer for running nodes")
was reviewed against the engine and does not exist: heal text is composed
by the engine exclusively from the gate's Verdict, and steering never
reaches an in-flight spawn (checkpoint consumption is r6's design and
mid-flight injection is a permanent non-goal). The pattern that works,
verified against the code, uses only shipped mechanics:

- The clarify gate is a normal gate with **`heal.max_rounds: 0`** whose
  contract output phrases underspecification as findings with
  `category: "question"`. A heal-enabled clarify gate would be a bug:
  heal fires immediately in-process, re-running the target with
  unanswered questions and burning rounds.
- On block (exit 2), the orchestrator reads the questions from
  `phases/<gate>/result.json`, relays them, and echo-confirms the DE's
  answers. **The echo MUST quote the gate's finding verbatim alongside
  the plain-language relay** (M3; findings are one readable line by the
  clarify-gate contract — if a finding is not readable as one line, that
  is a defect in the clarify gate's contract output, filed as such). The
  confirmed exchange is journaled as the `clarify` triple (§A.2).
- Answers travel via **`lockstep steer <run> <target> "<answers>"`** —
  the target, not the gate — then `lockstep resume`: the blocked gate
  re-runs, the done target re-runs because of unconsumed mail (r6 C2),
  and the steering block folds into its prompt AND hash.
- **Consumption is verifiable**: after the re-spawn, the orchestrator
  verifies the *steer text* — not just its existence — in
  `phases/<target>/prompt.txt` before telling the DE "your answer is in
  the instructions" (M3). (r7 nicety: a `steer-consumed` event, batched
  below.)
- **Answers are effectively permanent**: the mailbox renders in full into
  every later prompt and folds into the hash; a correction is appended
  beside the original, and true retraction means `--fresh` (re-bills the
  lineage). Hence the mandatory echo-confirm, and a warning before
  steering any node whose output the DE has already been shown
  (re-running it may change that output and invalidate descendants).

### A.2 The cockpit journal (M1)

`<run_dir>/cockpit-journal.jsonl` — append-only, orchestrator-written,
one JSON object per line. The orchestrator appends a line at each of
exactly five moments; nothing else writes it, and the engine never reads
it (it is cockpit convention, not driver schema — no r7 text needed).
Entry kinds:

- `consent` — budget-consent beat: cap stated, units, mode
  (`attended|unattended` with the §D terms when unattended), DE's go,
  timestamp.
- `clarify` — the full M3 triple: gate finding text (verbatim), the
  plain-language relay as shown to the DE, the DE's answer as spoken, and
  the steer text as sent. Plus target node and confirmation quote.
- `handoff` — APPROVAL pane spawned: run, node, quiescence-check result,
  evidence extract identifier (B1).
- `stop` — STOP invoked: what was cancelled, spend at cancellation.
- `note` — free-form DE preference or instruction the orchestrator
  intends to honor across the run ("always use the Q2 dataset").

**Writer/reader discipline:** append-only via open-append-write-close (no
held handles — same reader rules as the cockpit script; reads via
`Tail-RunFile`); the MISSION renderer and retro may read it; treated as
sensitive (lives in `runs/`, subject to the retro privacy projection —
`clarify` bodies are the one deliberate exception *within* the run dir,
and are still stripped by the cross-run retro projection like all other
bodies).

**Trust status, stated plainly:** the journal is evidence of *what was
said*, not truth about state — it is a narrated artifact by construction.
That is exactly what makes it auditable against the mechanical artifacts
(§C, told-vs-state) rather than redundant with them.

### A.3 Pane grammar (normative)

**Invariants:** **CHAT** left column, never moves, never shrinks (the
DE's home). **MISSION** bottom, full width, always present (the trust
anchor doesn't blink). **ACTIVITY** right column, 1–2 panes,
frontier-following (attention never scales with graph width). Transients
(APPROVAL, request-tail, raw-stdout) spawn in the ACTIVITY column only,
titled, at most one per role, killed on state-exit.

**Per-shape policy:** chains and gated chains need no layout events (the
frontier walks or alternates; MISSION counts `rework k of n`). Map
fan-outs get ONE newest-active-item pane plus the collapsed MISSION
counter (`12 of 40, 1 redone`). Parallel width 2 may split ACTIVITY;
width ≥ 3 collapses to newest-active + MISSION counters. Clarifications
get **no pane** — CHAT carries the §A.1 ritual; ACTIVITY shows the idle
placeholder. **Tabs:** one tab per deliverable lineage, title carries
`segment k of n`; a second deliverable is a second tab, queued by default.

**L-B1 — send-text target integrity.** `wezterm cli send-text` may target
ONLY a pane id returned by a `split-pane` the orchestrator issued **in
the same action sequence**, and only after a title round-trip (set-title →
list → match). Pre-typing into any preexisting pane is prohibited. Enter
is never sent by the orchestrator under any circumstances. A failed
round-trip aborts the handoff and falls back to CHAT narration + a
freshly spawned pane on the next attempt.

**L-B2 — one tail primitive.** `cockpit.ps1` ships a single
`Tail-RunFile` primitive — FileStream opened with `FileShare
ReadWrite|Delete`, poll ≥ 0.5 s, reopen-on-rename/truncation (length
regression ⇒ reopen), partial trailing line tolerated, every error
display-only. **All** pane access to run-dir files (ACTIVITY,
request-tails, raw-stdout, MISSION's inputs, the journal, the ledger)
goes through it; raw `tail -f` / `Get-Content -Wait` on run-dir paths is
prohibited in cockpit code and in the orchestrator's pane commands.
Rationale: the driver's `state.json` writes are atomic-replace with
retries precisely because this machine's AV already causes transient
`PermissionError`s; engine rotation renames per-attempt files, and a
held handle without delete sharing fails that rename on Windows — a
*view* must never take the run down. The cockpit script additionally
polls at ≥ 1 s for state, ignores `*.tmp`, and treats every pane error as
display-only.

**L-M1 — the pane state machine:**

| Lineage state | ACTIVITY | Transient |
|---|---|---|
| running | frontier (1–2, hysteresis per L-M2) | request-tail on demand |
| paused — clarification | idle placeholder: "waiting on your answer — nothing is spending" | none |
| exited 6, NOT quiescent | frontier again (detached burn-down resume) | none |
| exited 6, quiescent | killed | APPROVAL (pre-typed per L-B1) |
| segment done (exit 0) | idle: "segment k done" | none; next segment relaunches frontier |
| failed / cancelled | idle: "stopped — nothing spending" | stderr-tail on request |

CHAT and MISSION are present in every row (invariant). The idle
placeholders are rendered by the cockpit script from lineage state — the
heartbeat principle extended: **blank must never be ambiguous between
dead, thinking, and waiting-on-you**, and the disambiguating line is
mechanical, never narrated.

**L-M2 — frontier hysteresis.** An ACTIVITY pane is *bound* to its node
until the node reaches a terminal state for the current attempt cycle. A
newly running node claims an idle pane if one exists, else the pane whose
node terminated longest ago. A pane whose node is still running is never
re-pointed. (Map items are one logical node — the newest-active-item rule
is the one sanctioned exception to binding.)

### B. Per-node cost tracking (feature)

- **v0-prereq (step 0): probe the envelopes.** Five minutes per machine:
  run one node per stanza, inspect `stdout.log` — what usage fields does
  claude-code's envelope carry; does pi's `--mode json` envelope carry
  any; copilot-cli has no JSON mode at all. (Lead worth checking during
  the probe: pi's *internal* taskflow accounting reports per-step token
  cost, so the numbers may exist even where the headless envelope omits
  them.) The probe's results shape the field map AND the v1 amendment; it
  runs before any code is written.
- **v0 — summarizer, no driver change.** `contrib/cost_report.py
  <run_dir>...` (accepts MULTIPLE run dirs — with terminal approvals, a
  deliverable spans a chain of runs, and "what did this report cost" must
  roll up; the orchestrator tags related runs via a naming convention or
  label file). Walks `phases/*/` including rotated attempts and map items
  — retries cost money too (envelopes are preserved per attempt; the
  `json_field` unwrap is in-memory, so `stdout.log*` retains them;
  verified against the executor). Emits per-node/per-run/per-deliverable
  tables: spawns, attempts, heal rounds, tokens, wall time (from
  `events.jsonl` transition-pair timestamps — `state.json` spans mislead
  on re-runs; map items have no transition events, so item wall time is
  best-effort file mtimes). **Units policy:** spawns/tokens/wall-time are
  the primary columns; dollars appear only where the envelope reports
  them, labeled notional (both machines actually bill in quota/limits).
  Executors with no envelope print "no envelope", never a fake 0. Field
  maps live in an operator-owned sidecar config (not hardcoded — and not
  in `lockstep.toml` yet: the stanza model is `extra="forbid"`),
  pre-proving the v1 shape. Flows may append a final shell node running
  it (it derives the run dir from `LOCKSTEP_PHASE_DIR/../..` — no
  `{run_dir}` interpolation form exists — and excludes its own cost).
- **v1 — driver capture, additive, behind config — with the digest rule
  named.** Stanza key `usage_fields = {...}`; extraction after each
  attempt; a new `usage` event kind; per-node accumulation in
  `state.json`; a spend column in `status`. **Hash impact is NOT "none"**
  (rev-1 error): the harness fingerprint includes the whole-stanza digest
  (r5 B1), so adding the field to the model — even unset — would change
  every stanza's digest and silently re-bill every cached node on
  upgrade. The r7 amendment MUST therefore redefine the stanza digest to
  exclude `usage_fields` (defensible: it changes what the driver reads
  back, never what the spawn does) or explicitly adopt the one-time
  invalidation; the digest rule is amendment text on the hash-composition
  boundary, not an implementation detail. `doctor` extends to check
  configured `usage_fields` resolve against a real probe envelope.
- **v2 — token/cost budgets** (`budget.max_tokens`/`max_cost`, exit 4):
  deferred until v1 numbers prove out; needs §9.5 amendment text, which
  must state that enforcement is executor-dependent (only metered stanzas
  bind). Until then `max_agent_spawns` stays the lever — it already
  counts heal rounds and correctives.

### C. Gate-driven improvement (feature)

**Data sources, corrected:** the per-round truth lives in `events.jsonl`
(transitions, `heal-round`, timestamps) and the gates'
`phases/<gate>/result.json` + rotated `result-attempt<n>.json` (full
finding bodies per round); the run's `flow.tg.json` copy carries gate
budgets. `state.json` holds only a lossy latest verdict string and the
heal-round counter — it is NOT sufficient alone (rev-1 error). Corrective
re-spawns are currently **not evented** (rev-1 claimed they were): v0
counts them by matching the fixed corrective-preamble marker inside
`prompt*.txt` file bodies — current AND rotated (a corrective that
succeeded is often the last spawn, so its marker sits in `prompt.txt`,
never rotated) — marker match only, bodies never shipped.

- **v0 — friction report.** `contrib/retrospect.py <runs-root>`:
  aggregates across run dirs, **grouped by `(flow, flow_hash)` cohort** —
  every applied improvement changes `flow_hash`, so before/after cohorts
  come free. **Privacy projection specified:** node ids, gate ids,
  counts, rounds, severities, `Finding.category`, cost numbers, triage
  classes and qualification states (§D) — `claim`/`evidence`/`reason`/
  heal-text bodies are stripped or hard-truncated (they routinely quote
  code and prompts; "metadata only" must be enforced by projection, not
  assumed). The aggregator skips pi `--session-dir` transcript subtrees
  (A.3.4: transcripts are never node inputs). Output stays under `runs/`
  (it remains sensitive) or stdout.
- **Measurement is part of the feature, not an afterthought:** the
  report's trend section states, for each previously applied improvement
  (identified by the commit-trailer finding ID), whether its target
  metric moved across cohorts. The feature's success metric:
  heal-rounds-per-run and blocks-per-gate for a touched flow decline
  within N runs of an applied batch — and a regression is a first-class
  retro finding. For unattended cohorts (§D): overturn rate and
  time-in-class per (flow_hash, approval-class).
- **Consumption path, default-first:** the orchestrator reads the
  friction report and *discusses* improvements with the human; upheld
  ones ride `plan-adversarial` → `implement-heal` with approval, one
  batch per cycle, provenance trailers on every commit. The fancier
  `flows/starter/retrospect.tg.json` (shell aggregator → analyst →
  arbiter) is shipped but its adoption is gated on the discussion path
  having produced at least one applied-and-measured batch — no analysis
  ceremony before the loop has closed once by hand.
- **v2 — only on demonstrated need:** `lockstep report <run_dir>` as a
  CLI verb (§3 amendment; contrib scripts prove the shape first).
- **Standing retro category (M2): told-vs-state**, computed from
  `cockpit-journal.jsonl` against the mechanical artifacts of the same
  run — `consent` caps vs actual spend at each beat, `clarify` relays vs
  gate `result.json` finding text, `handoff` claims vs `state.json` at
  handoff time, `stop` spend vs the cost report. Both sides of every
  comparison are fenced run-dir artifacts; pi transcripts remain
  excluded (A.3.4 untouched). Drift findings are first-class retro
  findings with their own category. Additionally (M3): a
  **translation-fidelity check** over `clarify` triples —
  finding-vs-relay and answer-vs-steer token overlap flagged below
  threshold for human review (a heuristic tripwire, not a metric with a
  target; it exists to surface candidates, never to auto-judge).

**Addendum-A conformance** (verified in review): nothing here routes
control flow through extensions; retro reading past run dirs is cross-run
forensics as fenced data, which A.3.3 anticipates — not the intra-lineage
re-run contamination note 4 prohibits.

### D. Unattended mode (feature)

Unattended mode lets a run proceed past most *intermediate* human gates
using judgment rendered by the system itself, with deferred human review.
It is designed under three non-negotiables:

1. **The human channel is never forged (U-B1).** The structural
   guarantee — interactive approvals answered only by a human at a real
   TTY — stays intact. Unattended mode is implemented by *removing*
   interactive approval nodes from the flow, never by answering them.
   Sending keystrokes into an approval prompt (by the orchestrator, an
   extension, or any automation) is a prohibited pattern, same standing
   as Addendum-A's; §A.3's L-B1 spawn-only send rule makes it
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

## Step-by-step changes (resequenced: mechanics and pilot before polish)

0. **Envelope probe on both machines** (5 min each) — shapes B's field
   maps and the r7 draft. *(B)*
1. `contrib/cost_report.py` (+ sidecar field-map config) + offline
   tests: synthetic envelopes for both stanza shapes, rotated attempts,
   map items, multi-run rollup, "no envelope" rendering. *(B-v0)*
2. Clarification-gate pattern + approval rules + journal: starter
   fragments (clarify gate, evidence-bearing approval),
   `FLOW-AUTHORING.md`/`DRIVING-LOCKSTEP.md` sections (evidence rule,
   quiescence procedure, dead-pid force-unlock rule,
   exit-6-as-handoff narration, echo-confirm with verbatim finding),
   journal spec + boot-protocol journal replay. All doc/fragment work —
   pre-pilot, no driver change. *(A)*
3. **Pilot checkpoint with a named DE — attended-only** — a real person,
   success/abort criteria written before the session (see Test plan,
   incl. the planted-defect criterion). Cockpit polish and all driver
   work are gated on its findings. If no pilot user can be named,
   workstream A shrinks to the pattern docs (which serve the maintainer
   regardless). *(A)*
4. `contrib/start-cockpit.cmd` + `contrib/cockpit.ps1` (DE-tier
   renderer, `Tail-RunFile`, pane state machine + hysteresis, spawn-only
   send-text with title round-trip, boot/recovery protocol with journal
   replay and pane re-listing) + scripted manual test protocol
   (rotation-under-tail drill, stale-pane-id handoff drill,
   non-quiescent handoff drill). *(A)*
5. `contrib/retrospect.py` + offline tests (synthetic run dirs derived
   from a sanitized real run; blocks, heals, corrective markers,
   cohorts, told-vs-state comparator, clarify-triple fidelity tripwire).
   *(C-v0)*
6. `flows/starter/retrospect.tg.json` + README row (adoption gated per
   §C). *(C-v1)*
6b. `contrib/make_unattended.py` + ledger writer + sidecar qualification
   state + offline tests (transform determinism; egress nodes untouched;
   cost-at-risk computation; qualification state machine incl. excursion
   revert; refusal on self-modifying flows). **Gated on: the pilot
   passed AND the retro has produced at least one attended cohort** (the
   overturn comparator needs somewhere to live). *(D-v0)*
7. r7 amendment batch: `usage_fields` stanza key + **stanza-digest
   exclusion rule**, new event kinds reserved against §10.1 (`usage`,
   `corrective`, `steer-consumed`, `deferred`), state accumulation +
   status column, `approval.policy` (recorded, not implemented before
   D-v0 closes) — batched with the two r7 items DEVIATIONS already
   queues. Implement only after adoption. *(B-v1, D-r7)*
8. Defer: token budgets (B-v2), `report` verb (C-v2), monolithic-flow
   approval mechanic (r7 candidate).

## Test plan

- Contrib scripts: offline unit tests against synthetic fixtures (no
  tokens); existing suite stays green throughout; flow verification for
  the retro flow, the clarify fragment, the evidence-approval fragment,
  and unattended transforms.
- B-v1 (post-amendment): fake-executor envelopes with usage fields; a
  stanza-digest test pinning that adding/setting `usage_fields` does NOT
  change cached-node hashes; live smoke (`LOCKSTEP_LIVE`) reconciling v0
  totals against provider-reported envelope numbers — not merely
  asserting parse success.
- Cockpit: scripted manual protocol (spawn, refresh across waves, map
  collapse, reader-rule compliance under a running engine, fallback
  path, boot-recovery against a killed run with a held lock,
  **rotation-under-tail** — force a heal round while ACTIVITY tails the
  target's progress file; pass = rotation succeeds, pane re-points —
  **stale-pane-id handoff** — pass = round-trip failure aborts to CHAT +
  fresh pane — and **non-quiescent handoff** — steer an answered
  clarification, then reach the approval; pass = the orchestrator
  resumes detached first and the APPROVAL pane, when finally spawned,
  executes the approval node only, in seconds).
- **Pilot session (step 3, attended-only):** a named non-programmer runs
  the `plan-adversarial` → `implement-heal` chain end-to-end using only
  CHAT, the pre-typed APPROVAL pane, and STOP. Success bar: (a) zero
  moments where the DE composes a command or reads JSON; (b) N
  comprehension spot-checks — the DE states what is running / blocked /
  spent, compared against the state files, zero discrepancies; (c) the
  DE can recover a deliberately killed run via the double-click path
  alone; (d) one approval in the pilot chain is seeded with a **planted
  domain-visible defect** in the deliverable (wrong unit, wrong chamber
  set, inverted acceptance limit — chosen by the engineer with the
  pilot's domain in mind, written down before the session); success
  requires the DE rejects it based on the APPROVAL pane's evidence
  alone, with CHAT glossing disabled for that beat. A caught plant
  validates the evidence rule; a missed plant is a named blocker against
  B1's extract design, not against the pilot. Abort criteria written
  with the pilot's name.
- Unattended (step 6b): qualification state-machine tests incl.
  excursion revert; the judge planted-defect gate before any class first
  enters `proceed-flagged`.

## Risks and mitigations

- **pi envelope carries no usage fields** — cost on the work machine
  degrades to spawns/wall-time. Mitigated by design: spawns are always
  tracked, units policy never promises dollars the envelope can't back,
  and step 0 resolves the unknown before code is written.
- **Narration drift** — mitigated structurally: the DE-tier MISSION text
  is script-generated (not narrated), narrations must cite run-dir
  artifacts, approvals decide from pane-rendered evidence (B1), and the
  retro audits told-vs-state (journal vs mechanical artifacts) every
  cycle.
- **Cockpit reader destabilizing the run** — the §A.3 reader rules and
  the single `Tail-RunFile` primitive; treated as a hard requirement of
  `cockpit.ps1`, tested under a live engine (rotation-under-tail drill).
- **Steer permanence** — a wrong DE answer is effectively unretractable
  short of `--fresh`. Mitigated by echo-confirm etiquette (with verbatim
  finding), the journaled triple, and the done-node warning; accepted
  residual: a confirmed-but-wrong answer costs a fresh lineage.
- **Improvement-loop instability** — one batch per cycle,
  approval-gated, provenance trailers, cohort measurement that makes a
  regressing batch visible in the next report, and unattended mode
  structurally excluded from touching flows or judges (U-M2).
- **Judge evidence ceiling (unattended)** — the judge sees the B1
  extract, not the DE's domain knowledge; the clarification a DE would
  have volunteered at an attended approval does not exist in unattended
  mode. `escalate` and the planted-defect gate mitigate but cannot close
  this; the consent beat states it plainly. Accepted residual of the
  mode.
- **Qualification sample-size at small scale** — every applied
  improvement changes `flow_hash` and resets cohorts, so actively
  improved flows may never qualify for auto-accept. Arguably correct
  (a changed process re-qualifies), but it means unattended value
  depends on flow stabilization; see open question 6.
- **Hold-heavy unattended runs** — a poorly qualified flow in unattended
  mode is attended mode plus judge-spawn cost. The retro's time-in-class
  metric makes it visible; flows that stay hold-heavy should stay
  attended.
- **Two-machine maintenance of contrib scripts** — pwsh-first (both
  machines are Windows), field maps in config not code, and the pilot
  gate keeps the surface small until the loop is proven.
- **Session limits kill long runs** (known) — the boot protocol makes
  recovery a double-click (journal replay restores the conversation, not
  just the run); approval segmentation keeps DE-owned processes
  seconds-long; detached execution means an orchestrator death never
  takes the run with it (and vice versa).

## Open questions

1. ~~pi envelope fields~~ → promoted to step 0 (probe, don't wonder).
2. ~~steer vs heal for answers~~ → resolved: answers travel via steer +
   resume only; heal text is engine-owned (§A.1).
3. Deliverables handoff beyond `Deliverables/` + `Start-Process` — does
   the DE need email/share integration, and who owns that boundary?
   (Explicitly out of scope for v0.3; revisit at the pilot. Note: any
   such integration is egress and sits on the never-auto floor.)
4. Concurrent runs for one DE: queued-by-default policy is set (§A step
   7); whether MISSION ever needs a cross-run overview waits for real
   demand.
5. Approval mechanic for monolithic flows (mid-flow approvals without
   run ownership transfer) — r7 candidate; until then segmentation is
   the rule.
6. **Qualification survival across `flow_hash` changes** — should
   qualification persist when an applied improvement does not touch the
   approval's upstream subgraph? Needs a subgraph-hash mechanic that
   does not exist yet; the strict reset stands until one is specified.

## Review log (cumulative)

**rev 1 → rev 2** (three-lens adversarial review): approval mechanic
rebuilt around terminal approvals + pre-typed `resume` pane (was:
unworkable "paste into CHAT, y/n"); clarification answers re-mechanized
on steer+resume with `max_rounds: 0` gates (was: nonexistent heal-text
channel and §16.2 conflict); cold-start/recovery given an owner
(`start-cockpit.cmd` boot protocol, mechanical force-unlock rule);
detached execution model stated (was: implicitly mute orchestrator);
`usage_fields` stanza-digest re-billing acknowledged and made an
amendment prerequisite (was: "hash impact: none"); ACTIVITY re-pointed at
`progress.jsonl`; MISSION split into script-generated DE tier + raw
tier; STOP + budget-consent beat added; deliverable handoff defined;
retro data sources corrected (events + gate result rotations, not
state.json; correctives counted via prompt markers pending an event);
privacy made a projection, not a claim; measurement (flow_hash cohorts,
applied-batch trends) made part of feature C; sequencing reordered
loop-before-view with a named-pilot gate; cost units re-based on
spawns/tokens with notional dollars; multi-run deliverable rollup added.

**rev 2 → rev 3** (second adversarial pass, narrator seam): B1 approval
evidence rule — approvals render mechanical extracts of the deliverable
in their own TTY output; decide-from-the-pane briefing; starter
fragment. B2 mandatory mechanical quiescence-except-approval check
before spawning the APPROVAL pane; detached burn-down when not
quiescent; manual-protocol drill. M1 `cockpit-journal.jsonl`
(append-only, orchestrator-owned, five entry kinds); boot protocol
replays it — consent, answered questions, and standing notes survive
orchestrator death. M2 told-vs-state retro category re-based on
journal-vs-mechanical-artifact comparison; transcripts stay excluded.
M3 echo-confirm quotes the gate finding verbatim; steer text verified by
content in the re-spawned prompt; clarify triples journaled and
fidelity-tripwired in retro. m1 planted-defect approval in the pilot,
evidence-only, pre-registered; a miss files as a B1 blocker. Pattern
addressed: rev 2 made every mechanical channel drift-proof,
concentrating residual risk in the semantic channel while auditing it
with excluded data; the journal gives the semantic channel an in-idiom,
fenced, auditable artifact without touching engine schema or frozen
surfaces.

**rev 3 → rev 4** (pane-grammar pass + unattended-mode design/pass):
Layout — L-B1 send-text confined to just-spawned, title-verified panes,
pre-typing into preexisting panes prohibited; L-B2 single `Tail-RunFile`
primitive with delete-sharing for ALL run-dir reads,
rotation-under-tail drill; L-M1 pane state machine adopted, idle
placeholders made mechanical (blank never ambiguous between dead,
thinking, waiting-on-you); L-M2 pane–node binding with hysteresis.
Unattended — U-B1 human channel never forged: implemented by flow
transform (judge gates + ledger), keystroke injection into approval
prompts a named prohibited pattern; U-B2 auto/flagged legal only under a
downstream cost-at-risk cap, stated at consent; U-B3 evidence-citing
judges, skip-lot qualification per (flow_hash, approval-class), overturn
= excursion = immediate revert to hold, planted-defect gate before first
qualification; U-B4 never-auto floor — egress, spend increases,
sensitivity-labeled artifacts, and (U-M2) any self-modifying flow; U-M1
journaled per-run consent, mechanical MISSION rendering with
pending-review count, evidence-bearing async review, STOP unchanged.

**rev 4 → rev 5**: consolidation only — rev 3 and rev 4 amendment text
applied at their anchors; goal/non-goals/trust table updated to
reference §D; risks extended with the three stated residuals (judge
evidence ceiling, qualification sample-size, hold-heavy runs); open
question 6 added (qualification survival across `flow_hash`). No new
normative content.
