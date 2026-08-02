# Proposal: the domain-expert cockpit — cost tracking, gate-driven improvement, and shippable setup

**Status:** revision 7 — revision 6 (the readiness pass: R-B1 pilot fork,
R-B2 lineage identity, R-B3 mechanized checks) with the six outstanding
owner decisions resolved and their consequences applied. **Targets
lockstep v0.3.x, packaged as a wheel and installed on the work laptop —
this revision is the first to treat the work laptop as the deployment
target rather than a second dev machine.**

Decisions resolved (detail in the review log):

1. **The domain expert is a persona, not a named pilot:** *Lam Product
   Engineer* — a non-programmer colleague who will receive this repo.
   Design proceeds against the persona (branch A); **validation moves to
   first contact** with a real colleague, after packaging (§ step 3).
2. **Deployment target: the work laptop** — WezTerm hosting pwsh, pi.dev,
   GitHub Copilot enterprise, credentials, and an existing repo with
   access to proprietary data. The dev machine mirrors most of that
   (verified: wezterm 20240203, pi 0.83.0), so the cockpit CAN be
   rehearsed here; what it cannot exercise is the **Copilot enterprise
   stanza** and **anything domain-specific**. Hence a new
   **setup-verification deliverable** (§ step 2b): assumed-similar
   becomes checked-similar, on the machine the author never sees.
3. **No driver-side cost capture.** `cost_report.py` is sufficient — on
   the condition that spend is **visible in a pane while the run is
   executing**, not only after it finishes (§B v0.5). This retires the
   `usage_fields` half of the r7 batch.
4. **Moot** — it existed only if (3) had gone the other way. The
   stanza-digest exclusion rule is no longer a prerequisite of anything,
   and the hash boundary goes untouched.
5. **Unattended mode split out** to `docs/PROPOSAL-unattended-mode.md`.
6. **Planted-defect design deferred to first contact**, since it depends
   on the colleague's domain and on the proprietary-data repo that only
   exists on the work laptop.

Nothing here touches a frozen surface at all — with (3) and (4) resolved,
the plan no longer requires an r7 amendment to proceed. Every deliverable
is a contrib script, a doc, a flow fragment, or a skill.

**Implementation state at rev 7:** step 0 (envelope probe) and step 1
(`contrib/cost_report.py` + sidecar field maps + offline tests) are
LANDED (`af8b950`, `c1367fd` — the latter adds pi's `--mode json` stream
parser, probed against pi 0.83.0). Everything else below is unbuilt.

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
   reports them), **visible in a pane while the run is executing**, not
   only in a post-mortem (rev-7 decision 3).
2. **Gate-driven improvement** — the friction the system already records
   (gate blocks, heal rounds, corrective re-spawns) is aggregated across
   runs, turned into concrete human-approved improvements, and
   **measured**: every applied improvement is checked against
   before/after cohorts.
3. **A setup that verifies itself** — the DE receives this repo as a
   wheel on a machine the author cannot inspect, so "is this installed
   correctly" must be answerable by the DE alone, mechanically, before
   any flow is run (rev-7 decision 2; § step 2b).

Unattended mode, previously goal 3, is now a separate deferred document
(`docs/PROPOSAL-unattended-mode.md`).

## Non-goals

- No web dashboard, no server, no database. The observation surface stays
  plain files + CLI (SPEC §12); WezTerm panes are a *view*, never a
  dependency — and never a hazard to the run (see §A.3 reader rules).
- No driver-side chat or session features (§15 stays "build-loop tool");
  no mid-flight prompt injection (steering is checkpoint-consumed — a
  **permanent non-goal** per §16.2/r6, which this design respects rather
  than works around).
- No change to frozen surfaces: exit codes, `format_version` 1.x
  semantics, §7 fencing/footer, hash composition (M3). **At rev 7 this
  is absolute, not conditional**: decision 3 retired the `usage_fields`
  stanza key, so nothing in this plan approaches the hash boundary and
  no r7 amendment is a prerequisite of any step.
- No autonomous self-modification: improvement proposals are applied
  only through the existing reviewed-and-approved SDLC flows.
- **The human channel is never forged:** interactive approvals are
  answered only by a human at a real TTY; no mode, extension, or
  automation ever types into an approval prompt. This is enforced by
  construction (§A.3 L-B1: the approval pane RUNS the approval script; the
  cockpit contains no `send-text` code path at all), and it is the
  guarantee the deferred unattended design is required to preserve.
- **Nothing this repo ships reads proprietary data.** The tool is
  generic; the data lives in the DE's own repo on the work laptop.
  Fixtures, tests, and anything committed here stay synthetic (§ step
  2b, § step 3).

## Personas and trust model

| Principal | Does | Never does |
|---|---|---|
| **Lam Product Engineer** (the DE persona) | Talks to the orchestrator; answers domain questions; presses Enter on pre-typed commands; types `a`/`r` at approval prompts; says STOP; runs the setup check after install | git, JSON, flow authoring, composing CLI commands, judgment calls about locks/resumes |
| Orchestrator (interactive pi session) | Authors/runs flows per `docs/DRIVING-LOCKSTEP.md`; runs them **detached** (§A); translates run state; relays domain questions; manages panes per §A.3; owns recovery; writes the cockpit journal (§A.2) | Approvals (structurally cannot — non-TTY auto-reject); editing live lineages; spending without stated budgets |
| Agent nodes (headless spawns) | The engineering | Control flow beyond sanctioned gate blocks; asking questions directly (see §A.1) |
| Human engineer (the author, remote) | Ships the wheel; reviews improvement batches; owns `lockstep.toml.example`, personas, sidecar configs, and flow authoring | Sit at the work laptop, see the proprietary repo, or reproduce a DE failure directly — hence every diagnosis must be possible from a run dir and a setup-check report alone |

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
  rework (1 of 2) / needs you / done`), **the live spend line** (`agent
  tasks used 9 of 25` always; tokens and wall time where the envelope
  carries them, `no envelope` where it does not — rendered by
  `cost_report.py --watch` against the *in-flight* run dir, rev-7
  decision 3, so "what has this cost so far" is answerable during the
  run and not only in a post-mortem), and for map nodes a collapsed line
  (`files checked: 12 of 40, 1 redone`). Bottom: the raw `lockstep
  status` table. The DE-tier text is summary-free by construction (field
  mapping, no model), which is what makes it a trust anchor — and the
  spend line inherits that status, since it too is arithmetic over run
  files with no model in the path.
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
  the lineage index (R-B2), and resumes the conversation from them:
  consent already given is not re-asked (restated in one line: "you
  approved up to 25 tasks; 9 used" — the *used* figure from
  `cost_report.py --runs-from <slug>` across the whole lineage, so it
  survives the segment boundaries the orchestrator's memory does not,
  and the deliverable's run set is read off disk rather than
  reconstructed), answered
  clarifications are summarized not re-asked, standing `note` entries are
  re-loaded, and the last `handoff`/`stop` frames the "here's where we
  are" narration. Pane ids from before the restart are never trusted: the
  boot protocol re-lists via `wezterm cli list`, kills orphans, and
  respawns from lineage state (§A.3). Then: "last night's run stopped at
  step 6 of 9 — nothing is lost; say continue to resume." Cold start and
  the morning after are the same double-click.
- `contrib/quiescent.py <run_dir>` — the B2 predicate as an exit code
  (§A step 4, R-B3). Ships with the step-2 doc/fragment batch, NOT with
  the cockpit: it is needed in the attended pilot and must not inherit
  the pilot gate.
- `contrib/cockpit.ps1 <run_dir>` — pwsh successor to
  `src/lockstep/watch/wezterm-watch.sh` (port it; don't start blank)
  implementing the layout, the DE-tier renderer, the `Tail-RunFile`
  primitive, the verified approval-pane launcher (L-B1), and the reader
  rules (§A.3).
  Invoked by the orchestrator/launcher, never typed by the DE. Fallback
  without wezterm: plain status loop.
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
   (attended — the only mode this document defines), and waits for an
   explicit go. The consent is journaled (§A.2)
   under a deliverable slug the orchestrator derives from the intent
   (the tab title's lineage name); that slug opens the lineage index
   (R-B2) that every later segment appends to.
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

   **The evidence rule (B1).** A DE-facing approval MUST put its evidence
   in front of the DE in the APPROVAL pane — never a narrated summary of
   it. Evidence is
   a mechanical extract of the deliverable produced by the flow, not by
   the orchestrator: the plan's headings and per-section first lines, a
   diff stat, the gate's findings table, or the full document when it is
   short. The flow author chooses the extract; the extraction is
   deterministic, so the evidence channel has the same trust status as
   the MISSION DE tier. The orchestrator MAY gloss the evidence in CHAT
   but the DE is briefed: *decide from the pane, not the chat.* A flow
   whose approval shows no evidence is unsuitable for the cockpit — same
   register as the `sdlc-e2e` exclusion.

   **ERRATUM (found in implementation, 2026-08-02).** Every revision
   through rev 7 said the approval node renders evidence "into its own
   TTY output". **It cannot.** A shell node's stdout goes to
   `phases/<node>/stdout.log`, not to the terminal, and the engine's
   approval prompt is a bare one-line `input()` (`roles.py:1096`). With
   no other mechanism the pane shows a naked `[approval:x] [a]pprove /
   [r]eject / [e]dit:` and the DE decides from chat narration — the exact
   outcome this rule exists to forbid. As built: the evidence node writes
   `<run_dir>/approval-evidence.txt`, and the pane runs
   **`contrib/approve.ps1`**, which prints that file, prints the briefing
   line, and only then calls `lockstep resume`. One pre-typed command,
   evidence first, prompt second. `cockpit.ps1 -Approve` warns loudly
   when the file is absent, and `approve.ps1` tells the DE that a missing
   evidence file is a flow defect whose safe answer is `r`.

   **The quiescence check (B2, mandatory, mechanical — R-B3).** Before
   spawning the APPROVAL pane it MUST be verified, from run-dir state
   alone, that the *only* runnable node is the approval: (a) no steering
   mail is unconsumed by a done or pending target; (b) no non-approval
   node is pending, failed-retryable, or invalidated. If the check fails,
   the orchestrator resumes **detached** first, lets the engine burn down
   the queue to the approval's auto-reject again, and only then hands
   over (pane choreography per §A.3's state machine).

   **This check is code, not prose.** It is computable from `state.json`
   alone, so it ships as `contrib/quiescent.py <run_dir>` — exit **0**
   quiescent-except-approval (prints the approval node id), **1** not
   quiescent (prints the blocking node ids and why), **2** unreadable run
   dir. The orchestrator CALLS it and acts on the exit code; it never
   reimplements the predicate by reading state itself, and
   `DRIVING-LOCKSTEP.md` documents the call, not the procedure. Rationale
   (R-B3): a guarantee whose only enforcement is an agent following a
   markdown paragraph is not mechanical in the sense this document uses
   everywhere else — and unlike the rest of the cockpit, this predicate
   is pure state-file arithmetic, so it is cheap to make real and cheap
   to test offline. Rule of thumb the tool encodes: **any steer after the
   last detached resume ⇒ not quiescent until a detached resume has
   consumed it.**

   `quiescent.py` is deliberately NOT part of `cockpit.ps1` (step 4): it
   is needed from step 2 onward, including in the attended pilot, and
   must not inherit the pilot gate. It is Python for the same reason the
   other contrib scripts are — offline unit tests against synthetic run
   dirs.

   The orchestrator then spawns a titled APPROVAL pane with `lockstep
   resume <run_dir>` running as the pane's own program (see the L-B1
   erratum: pre-typing was replaced because a spawned pane cannot be
   assumed to be a shell) — the DE presses Enter,
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
   semantics are unconditional.
6. Completion: the flow's final shell node copies the deliverable OUT of
   sensitive `runs/` into a designated `Deliverables/` folder — always
   behind a human approval — egress is never automated, the one floor
   the deferred unattended design also inherits; the
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
  (`attended`; the deferred unattended design adds its own terms here),
  DE's go,
  timestamp, plus (R-B2) `deliverable: <slug>` and `segment: k of n` —
  the back-reference that lets an orphaned run dir name its own lineage.
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

**L-B1 — send-text target integrity (mechanized, R-B3).** `wezterm cli
send-text` may target ONLY a pane id returned by a `split-pane` issued
**in the same action sequence**, and only after a title round-trip
(set-title → list → match). Pre-typing into any preexisting pane is
prohibited. Enter is never sent under any circumstances. A failed
round-trip aborts the handoff and falls back to CHAT narration + a
freshly spawned pane on the next attempt.

**ERRATUM (found in implementation, 2026-08-02): pre-typing was the wrong
mechanism, and the rule is stronger without it.**

As specified, the cockpit spawned a shell pane and pre-typed the resume
command into it. On the first real machine the spawned pane *did not stay a
shell*: a PowerShell profile auto-starts an interactive agent inside a project
workspace, so `pwsh -NoExit` became that agent and the "command" was typed into
a **chat composer**. The specified verification (set a title, confirm the pane
lists) caught nothing — it authenticated the pane's *identity*, never its
*program* — and because a WezTerm tab is shared by every pane in it, setting a
tab title also renamed the operator's own tab. Had the human pressed Enter they
would have sent a shell command to a language model instead of approving.

As built, `cockpit.ps1` spawns a pane that **runs `contrib/approve.ps1` as its
program**, and there is no `send-text` anywhere in the cockpit. That is
strictly stronger than the rule it replaces: L-B1 asked that no automation type
into an approval prompt, and the way to guarantee that is to have no code path
that types at all. The human's only input remains `a`/`r` at the genuine
prompt, so "the human channel is never forged" now holds by construction rather
than by discipline. Three supporting rules, each earned by a failure observed
during the build:

- **`-NoProfile` on every cockpit pane.** A cockpit pane is infrastructure, not
  the operator's interactive shell, and must not inherit startup customisation
  that can substitute its program.
- **Handshake verification, not title verification.** The pane's program writes
  a file naming its per-handoff marker and its `WEZTERM_PANE` (which WezTerm
  sets inside every pane it spawns); the cockpit requires both to match the
  pane it just created. A pane *title* cannot carry this signal — a title
  follows the foreground process, so it becomes `python.exe` the moment
  `lockstep resume` starts, and a correct handoff would look like a failure.
- **Verification failure kills the pane and aborts** to CHAT narration rather
  than leaving a decision surface nobody can vouch for.

`lockstep doctor --setup` reports whether shell profiles are present, stating
plainly that cockpit panes ignore them and that a terminal opened BY HAND may
not be a plain shell. It deliberately does not *probe* this: the substitution
occurs only in an interactive console, so a probe with piped stdio returns
control normally and would report "ok" on exactly the machines that have the
hazard — false assurance being worse than a stated fact.

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
  roll up; run-set identity is pinned by R-B2 below, not left to the
  orchestrator's memory). Walks `phases/*/` including rotated attempts and map items
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
  in `lockstep.toml`: the stanza model is `extra="forbid"`). Flows may
  append a final shell node running it (it derives the run dir from
  `LOCKSTEP_PHASE_DIR/../..` — no `{run_dir}` interpolation form
  exists — and excludes its own cost).

- **v0.5 — live pane mode (rev-7 decision 3, the condition on which
  driver capture was declined).** `cost_report.py --watch` renders a
  compact spend block against a run dir that is **still executing**, on
  the MISSION poll interval. Three requirements follow, and they are the
  whole of the work:
  - **Mid-flight tolerance.** Running nodes have no end timestamp, the
    current attempt's `stdout.log` may be partially written or absent,
    and `state.json` may be mid-replace (atomic-replace + AV retries,
    per the ops notes). Every one of these renders as `in progress` or
    the last good value — never a crash, never a fake 0, never a
    number that jumps backwards. Reads go through the same
    reader-rule discipline as §A.3 L-B2.
  - **Compact rendering.** A pane block of a few lines (`agent tasks
    used 9 of 25 · 2 rework rounds · 41 m elapsed · tokens: 1.2 M (pi)
    / no envelope (copilot)`), not the full per-node table. `--watch`
    implies the compact form; the full table stays the default.
  - **Cheap to re-run.** It is invoked every poll, so it walks the run
    dir without holding handles and without re-parsing envelopes it
    has already seen (mtime/size short-circuit).

  This is why no `usage` event, no `usage_fields` stanza key, and no
  driver change are needed: the numbers the DE needs are already on
  disk, and the only thing missing was reading them *early* rather than
  *late*.

- **R-B2 — deliverable identity (the run-set mechanic).** Segmentation
  (§A step 4) makes the multi-run case the NORMAL case, not the
  exception, so "which runs are this deliverable" must be recoverable
  from disk after the orchestrator dies — the boot protocol's whole
  premise. It is pinned two ways, redundantly and in both directions:

  - **Forward index:** `runs/lineages/<slug>.runs` — append-only, one
    run-dir path per line, written by the orchestrator when it launches
    each segment (same open-append-write-close discipline as the
    journal; lives under `runs/`, hence sensitive). `<slug>` is the
    deliverable slug chosen at the budget-consent beat.
  - **Back-reference:** the journal's `consent` entry (§A.2) carries
    `deliverable: <slug>` and `segment: k of n`, so a run dir found
    alone still names its lineage and the index can be rebuilt by
    scanning journals.

  `cost_report.py` gains **`--runs-from <slug-or-file>`** (resolving a
  bare slug under `runs/lineages/`) alongside its positional run dirs;
  the boot protocol reads the index to answer "what has this deliverable
  cost so far" without the DE or the orchestrator remembering a run-dir
  list. The index is cockpit convention — the engine neither writes nor
  reads it, so no r7 text is needed. Retro (§C) keys deliverable-level
  rollups off the same slug.
- **v1 — driver capture: DECLINED at rev 7 (decision 3), not deferred.**
  The design was: stanza key `usage_fields = {...}`, extraction after
  each attempt, a `usage` event kind, per-node accumulation in
  `state.json`, a spend column in `status`. It is dropped because v0.5
  delivers the only thing it was wanted for — spend visible during the
  run — without touching the driver, and because its cost was
  disproportionate: the harness fingerprint includes the **whole-stanza
  digest** (r5 B1; verified at rev 6 against `harness.py:115-119`, where
  `stanza_digest()` is a `model_dump()` of the entire stanza), so adding
  the field to the model *even unset* would change every stanza's digest
  and silently re-bill every cached node on upgrade. Avoiding that
  required amendment text on the hash-composition boundary. Trading a
  frozen-surface amendment and a re-bill for a number already derivable
  from `stdout.log` was the wrong trade.

  **Revisit only if** a future harness reports usage *only* in a channel
  the driver consumes and discards (i.e. the number stops being
  recoverable from the phase dir after the fact). That is the sole
  condition that changes the arithmetic; absent it, v0/v0.5 is the
  permanent answer.
- **v2 — token/cost budgets** (`budget.max_tokens`/`max_cost`, exit 4):
  now unreachable — it depended on v1's in-driver numbers, and enforcing
  a cap on numbers a contrib script reads after the fact is not a thing.
  `max_agent_spawns` remains the lever, and it is the honest one on both
  machines anyway: it already counts heal rounds and correctives, and
  both harnesses bill in quota rather than dollars.

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
  approval outcomes — `claim`/`evidence`/`reason`/
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
  retro finding.
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

### D. Unattended mode — split out (rev 7)

Moved to **`docs/PROPOSAL-unattended-mode.md`**, unchanged, with its own
risks and open question. It was ~40 % of this document, is double-gated
on things that do not yet exist (a cockpit in real use, plus an attended
retro cohort to concord against), and was crowding out work that is
about to be built. Its two structural guarantees still bind here and are
restated in place: **the human channel is never forged** (non-goals) and
**egress is always human-approved** (§A step 6).

## Step-by-step changes (rev 7: build for the persona, validate at first contact)

0. ~~**Envelope probe on both machines**~~ — **DONE** (`af8b950`);
   pi `--mode json` stream shape probed against 0.83.0 (`c1367fd`). *(B)*
1. ~~`contrib/cost_report.py` (+ sidecar field-map config) + offline
   tests~~ — **DONE** (`af8b950`, `c1367fd`): synthetic envelopes for
   both stanza shapes, rotated attempts, map items, multi-run rollup,
   "no envelope" rendering. *(B-v0)*
1b. **`--runs-from` + the lineage index** (R-B2): `runs/lineages/<slug>.runs`
   writer convention, `cost_report.py --runs-from <slug-or-file>`, journal
   `consent.deliverable`/`segment` fields, and an index-rebuild-from-journals
   path; offline tests for slug resolution, a missing index, and a run dir
   listed twice. *(B-v0)*
1c. **`--watch` live pane mode** (§B v0.5, decision 3): compact rendering,
   mid-flight tolerance (running nodes, partial `stdout.log`, `state.json`
   mid-replace), and re-run cheapness. This is now a **prerequisite of the
   cockpit**, not a nicety: MISSION's spend line is its only source, and
   declining driver-side capture is only defensible if this exists. *(B-v0.5)*
2. Clarification-gate pattern + approval rules + journal + **the B2
   predicate as code** (R-B3): starter fragments (clarify gate,
   evidence-bearing approval), `contrib/quiescent.py` + offline tests
   (quiescent, unconsumed steer, pending non-approval node,
   failed-retryable, invalidated descendant, unreadable dir),
   `FLOW-AUTHORING.md`/`DRIVING-LOCKSTEP.md` sections (evidence rule,
   **the quiescence CALL** — not a procedure to follow by hand — dead-pid
   force-unlock rule, exit-6-as-handoff narration, echo-confirm with
   verbatim finding), journal spec + boot-protocol journal replay. No
   driver change. *(A)*
2b. **Packaging + setup verification — new at rev 7 (decision 2).** The
   DE receives a wheel on a machine the author cannot inspect, so
   "assume we have a similar setup" has to become a check the DE can run
   alone and a report the author can read remotely.

   - **Wheel build + install path**: `lockstep` installed from a wheel
     into a venv on the work laptop, with `lockstep.toml` derived from
     `lockstep.toml.example` for that machine's stanzas (pi.dev, GitHub
     Copilot enterprise). Note copilot-cli has no JSON mode — its nodes
     render `no envelope` in every cost view, by design, and the DE
     must never read that as an error.
   - **`lockstep doctor` extension**: today it probes harness stanzas
     (the only check that catches flag drift). Extend it to the cockpit
     prerequisites — WezTerm present and `wezterm cli` responsive, pwsh
     version, the configured `Deliverables/` target writable, the
     personas dir and cost-fields sidecar resolvable, `runs/` present
     and gitignored. Each check emits a one-line pass/fail the DE can
     read and the author can be pasted.
   - **`/getting-started` skill** (repo skill, alongside
     `/flow-authoring` and `/debug-run`): walks the DE's orchestrator
     through first-run setup, invokes `doctor`, explains failures in DE
     register, and stops at the first hard failure rather than
     proceeding into a run that will fail later and less legibly.
   - **Setup-check report**: the artifact the DE sends back when
     something is wrong. It must be safe to send — machine facts and
     check results only, never repo contents, never paths inside the
     proprietary repo beyond their existence.

   This step is what makes remote support possible at all: per the
   personas table, the author can never reproduce a DE failure directly.
   *(A, new)*
3. **First contact — validation, not a pilot gate (decision 1).** The
   DE is a **persona** — *Lam Product Engineer* — not a named individual,
   so rev 6's "name a person or strike the cockpit" fork resolves to
   **branch A with the validation moved after packaging**: build steps
   1c/2/2b/4 against the persona, ship the wheel, and treat the **first
   real colleague session** as the pilot.

   What this costs, stated plainly: `cockpit.ps1` gets built before any
   real user has touched the design, so its UX claims are unvalidated
   at build time. That is a genuine risk rev 6 was written to avoid, and
   it is accepted here for a reason rev 6 did not have — the tool is
   being *distributed* to several colleagues rather than demonstrated to
   one, so there is no single session to gate on, and the persona is the
   only thing that can be designed against until the first colleague
   actually sits down.

   What does NOT change: the success criteria still get written **before**
   the first session, not after — (a) zero moments where the DE composes
   a command or reads JSON; (b) comprehension spot-checks (the DE states
   what is running / blocked / spent, compared against state files, zero
   discrepancies); (c) recovery of a deliberately killed run via the
   double-click path alone; (d) the planted-defect criterion, whose
   design is deferred to that session per decision 6 (it needs the
   colleague's domain and the proprietary repo, neither of which exists
   on the dev machine). A missed plant is a named blocker against B1's
   extract design.

   Mitigation for building unvalidated: keep step 4 minimal — the pane
   grammar (§A.3) is already specified tightly enough to implement
   without UX guesswork, and anything beyond it waits for first contact.
   *(A)*
4. `contrib/start-cockpit.cmd` + `contrib/cockpit.ps1` (port
   `src/lockstep/watch/wezterm-watch.sh`): DE-tier renderer **including
   the live spend line from `cost_report.py --watch`**, `Tail-RunFile`,
   pane state machine + hysteresis, **the handshake-verified approval
   pane** (L-B1 by construction — the pane runs the script, nothing is
   typed, R-B3), boot/recovery protocol
   with journal replay, lineage-index read, and pane re-listing; +
   scripted manual test protocol (rotation-under-tail, stale-pane-id
   handoff, non-quiescent handoff). **The drills run on the dev machine
   first** — it has wezterm and pi, so the pane grammar, the reader
   rules, and the handoff choreography are all genuinely testable here
   against fake/shell nodes at zero token cost — **and are re-run on the
   work laptop** before first contact, where the Copilot stanza and the
   real repo enter the picture. *(A)*
5. `contrib/retrospect.py` + offline tests (synthetic run dirs derived
   from a sanitized real run; blocks, heals, corrective markers,
   cohorts, told-vs-state comparator, clarify-triple fidelity tripwire).
   *(C-v0)*
6. `flows/starter/retrospect.tg.json` + README row (adoption gated per
   §C). *(C-v1)*
7. **r7 amendment batch — shrunk to nothing load-bearing.** Decision 3
   removed `usage_fields`, the stanza-digest exclusion rule, the `usage`
   event, state accumulation, and the status spend column; decision 5
   moved `approval.policy` to the unattended document. What remains is
   optional bookkeeping that blocks no step: reserving `corrective` and
   `steer-consumed` event kinds against §10.1, batched with the two r7
   items `DEVIATIONS.md` already queues. Do it when something else needs
   an amendment; do not open one for this alone. *(bookkeeping)*
8. Struck rather than deferred: token budgets (B-v2, unreachable without
   v1), `report` verb (C-v2, contrib scripts remain the shape).
   Still open: the monolithic-flow approval mechanic (r7 candidate;
   segmentation is the rule until then).

## Test plan

- Contrib scripts: offline unit tests against synthetic fixtures (no
  tokens); existing suite stays green throughout; flow verification for
  the retro flow, the clarify fragment, and the evidence-approval
  fragment.
- **`quiescent.py` (R-B3):** offline cases pinning each exit code —
  quiescent-except-approval (0); unconsumed steer mail against a done
  target, a pending non-approval node, a failed-retryable node, an
  invalidated descendant (1, each naming the blocker); missing or
  unparseable `state.json` (2). The predicate is the one place a wrong
  answer hands a live queue to the DE's process, so it is tested as a
  unit, not exercised only through the manual pane drill.
- **Lineage index (R-B2):** `--runs-from` resolves a bare slug and a
  path; a lineage whose index is missing rebuilds from journal
  `consent.deliverable` entries and produces the same rollup; a run dir
  appended twice is counted once; a segment whose journal is absent is
  reported as unknown, never silently dropped from the total.
- **`--watch` live mode (§B v0.5):** fixtures for a run dir captured
  *mid-flight* — a node with no end timestamp, a truncated `stdout.log`,
  a `state.json` replaced between two polls, a `*.tmp` sibling present.
  Pass = renders every time, never crashes, never emits a fake 0, and
  never reports a total lower than the previous poll. Plus a live smoke
  (`LOCKSTEP_LIVE`) on the work laptop reconciling the watched total
  against the post-run report.
- Cockpit: scripted manual protocol, **rehearsed on the dev machine
  (wezterm + pi are present; use fake/shell nodes so it costs nothing)
  and re-run on the work laptop before first contact** (spawn, refresh across
  waves, map collapse, reader-rule compliance under a running engine,
  fallback path, boot-recovery against a killed run with a held lock,
  **rotation-under-tail** — force a heal round while ACTIVITY tails the
  target's progress file; pass = rotation succeeds, pane re-points —
  **substituted-pane-program handoff** — spawn into an environment whose
  profile replaces the shell; pass = the handshake never matches, the pane
  is killed, and the handoff aborts to CHAT — and
  **non-quiescent handoff** — steer an answered clarification, then
  reach the approval; pass = `quiescent.py` exits 1 naming the steered
  target, the orchestrator resumes detached first, and the APPROVAL
  pane, when finally spawned, executes the approval node only, in
  seconds). Note the standing hazard (unfixed, accepted): the DE-tier
  renderer re-derives status semantics from `state.json` outside `src/`,
  so an engine state-shape change drifts it silently and no test pins
  that — the manual protocol is the only detector.
- **Setup verification (step 2b):** `doctor`'s new checks tested against
  a deliberately broken config — missing stanza, wezterm absent,
  unwritable `Deliverables/`, unresolvable personas dir — each producing
  a one-line failure a non-programmer can act on. Plus one end-to-end
  install rehearsal: fresh venv, wheel install, `lockstep.toml` from the
  example, `/getting-started`, first flow. Rehearse on the dev machine
  for mechanics; the real check is the work laptop.
- **First contact (step 3):** the first colleague runs the
  `plan-adversarial` → `implement-heal` chain end-to-end using only
  CHAT, the pre-typed APPROVAL pane, and STOP. Success bar: (a) zero
  moments where the DE composes a command or reads JSON; (b) N
  comprehension spot-checks — the DE states what is running / blocked /
  spent, compared against the state files, zero discrepancies; (c) the
  DE recovers a deliberately killed run via the double-click path alone;
  (d) one approval is seeded with a **planted domain-visible defect** in
  the deliverable (wrong unit, wrong chamber set, inverted acceptance
  limit — designed at that point per decision 6, since it needs the
  colleague's domain and the proprietary repo, and written down before
  the session); success requires the DE rejects it from the APPROVAL
  pane's evidence alone, with CHAT glossing disabled for that beat. A
  caught plant validates the evidence rule; a missed plant is a named
  blocker against B1's extract design. **Criteria are written before the
  session even though the session is no longer a gate** — otherwise
  "first contact" degrades into a demo, which validates nothing.

## Risks and mitigations

- **Copilot nodes report no usage at all** — copilot-cli has no JSON
  mode, so its nodes render `no envelope` permanently while pi nodes
  report tokens. A DE reading a half-populated cost pane may take it for
  breakage. Mitigated by the units policy (spawns and wall time are the
  primary columns and always present) and by `/getting-started`
  explicitly teaching that `no envelope` is a property of the harness,
  not a fault.
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
  approval-gated, provenance trailers, and cohort measurement that makes
  a regressing batch visible in the next report.
- **Building the cockpit before any real user touches it (new at rev 7,
  decision 1)** — the persona replaces the named pilot, so `cockpit.ps1`
  ships unvalidated. Mitigated by keeping step 4 to exactly what §A.3
  already specifies (the pane grammar is tight enough to implement
  without UX guesswork), by writing first-contact criteria in advance,
  by the fact that distribution to several colleagues makes a single
  gating session impossible anyway, and — materially — by the dev
  machine having wezterm and pi, so every *mechanical* claim (pane
  choreography, reader rules under rotation, handoff sequencing) is
  rehearsable before shipping. Accepted residual is narrower than it
  first appears: what goes untested is whether a non-programmer finds
  the result legible, not whether it works.
- **The author cannot reproduce a DE failure (new at rev 7, decision
  2)** — narrower than the whole stack, since the dev machine has
  wezterm and pi: the irreducible gaps are the **Copilot enterprise
  stanza**, the colleague's credentials, and a proprietary repo the
  author must not see. Everything diagnosable must
  therefore be diagnosable from a run dir plus a setup-check report,
  which is the real requirement behind step 2b and the reason
  `/debug-run` and the setup report must both produce sendable,
  non-sensitive output. Residual: a failure that reproduces only against
  proprietary data can be triaged but not fixed remotely.
- **Two-machine maintenance of contrib scripts** — pwsh-first for panes
  (both machines are Windows), Python for anything testable offline
  (`cost_report.py`, `quiescent.py`, `retrospect.py`), field maps in
  config not code. The asymmetry is narrower than rev 7 first assumed:
  the dev machine runs the tests AND the cockpit; only the Copilot
  stanza and the domain work are work-laptop-only.
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
   (Out of scope for v0.3; revisit at first contact. Any such
   integration is egress and stays human-approved.)
4. Concurrent runs for one DE: queued-by-default policy is set (§A step
   7); whether MISSION ever needs a cross-run overview waits for real
   demand.
5. Approval mechanic for monolithic flows (mid-flow approvals without
   run ownership transfer) — r7 candidate; until then segmentation is
   the rule.
6. ~~Qualification survival across `flow_hash` changes~~ → moved to
   `docs/PROPOSAL-unattended-mode.md` with the rest of §D.
7. **New at rev 7 — how do several colleagues share one repo?** Decision
   1 says the wheel goes to *colleagues*, plural. Each has their own
   work laptop, their own credentials, and their own proprietary-data
   repo, so `runs/`, `lockstep.toml`, and `runs/lineages/` are all
   per-person and none of it is shared — but the *flows*, personas, and
   fragments are, and they are the thing worth improving centrally
   (§C). Whether that means a git remote each colleague pulls, a wheel
   re-issued per improvement batch, or something looser is unanswered,
   and it decides where the gate-driven improvement loop's output
   actually lands. Answer it before the second colleague, not the first.
8. **New at rev 7 — does the retro ever leave the work laptop?** §C's
   friction report is aggregate metadata by projection (node ids,
   counts, categories — bodies stripped), which is exactly the shape
   that *could* travel back to the author. Whether it may, given it
   summarizes work done against proprietary data, is the colleague's
   call and their employer's, not a design decision. Until answered,
   assume it stays local and the improvement discussion happens on the
   work laptop.

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

**rev 5 → rev 6** (readiness pass against `src/lockstep/`). Every
load-bearing engine claim was re-verified against the implementation and
**all hold** — non-TTY approvals auto-reject to exit 6
(`roles.py:1092`, `EXIT_APPROVAL_REJECTED = 6`) and the prompt string is
verbatim as documented (`roles.py:1096`); `progress.jsonl` exists per
phase dir and per map item (`roles.py:62-100`); the lockfile records pid
+ hostname and same-host dead-pid locks are auto-cleared
(`state.py:257-274`), so the boot protocol's "mechanical, never a
judgment call" rule is real; `stanza_digest()` is a `model_dump()` of
the whole stanza (`harness.py:115-119`), confirming rev 2's correction
that adding `usage_fields` re-bills every cached node and that the
digest-exclusion amendment is a genuine prerequisite; the corrective
preamble is a fixed literal and `prompt.txt` rotates per attempt
(`harness.py:271`), so §C's marker-counting works; `heal.max_rounds: 0`
is legal and its targets are still validated
(`taskgraph.py:445-448`). Three defects fixed: **R-B1** — the step-3
pilot gate named no owner and no default branch, leaving steps 4 and 6b
indefinitely unschedulable; it is now an explicit fork with an owner
(the human engineer), a decision point (completion of step 2), and a
named default (branch B: workstream A terminates at the pattern docs,
steps 4 and 6b struck rather than deferred). **R-B2** — segmentation
makes multi-run deliverables the normal case, but run-set identity was
left to "a naming convention or label file" and the journal is per-run,
so a deliverable's run set was unrecoverable after an orchestrator
death — the exact failure the boot protocol exists to survive; pinned
now as `runs/lineages/<slug>.runs` plus a `consent.deliverable`
back-reference, with `cost_report.py --runs-from` and an
index-rebuild-from-journals path (new step 1b). **R-B3** — B2
quiescence and L-B1 send-text integrity were called "mechanical" while
living in prose an agent was trusted to follow, making the document's
two strongest safety claims its two weakest enforcements; quiescence is
now `contrib/quiescent.py` (exit 0/1/2, shipped in step 2 so it does
not inherit the pilot gate) and L-B1 is now a launcher that runs the
approval script AS the pane's program, so no code path types anywhere
(see the L-B1 erratum for the failure that forced this). Also recorded: steps 0 and 1 are landed; the DE-tier renderer's
`state.json` coupling is a named unfixed hazard with the manual protocol
as its only detector.

**rev 6 → rev 7** (owner decisions applied). Six decisions rev 6 left to
the owner were answered, and this revision is their consequences rather
than a new review pass.

*Decision 1 — persona, not named pilot.* The DE is *Lam Product
Engineer*, a stand-in for non-programmer colleagues who will receive
this repo. This does NOT satisfy rev 6's R-B1 gate on its own terms — a
persona cannot sit at a keyboard, catch a planted defect, or fail a
comprehension spot-check — so rather than pretend the gate is met, rev 7
**moves validation instead of removing it**: build against the persona,
ship, and treat first contact with a real colleague as the pilot, with
criteria written beforehand. Rev 6's branch B is withdrawn (distribution
to colleagues is a real commitment, not an aspiration), and the cost is
recorded as a first-class risk: `cockpit.ps1` now ships unvalidated, and
step 4 is deliberately confined to what §A.3 already specifies so there
is minimal UX guesswork to be wrong about.

*Decision 2 — the work laptop is the deployment target.* WezTerm/pwsh,
pi.dev, Copilot enterprise, credentials, and a proprietary-data repo all
live on a machine the author never touches; the personal machine builds
and tests but cannot run the system. New **step 2b** (packaging + setup
verification: wheel install path, `doctor` extended to cockpit
prerequisites, a `/getting-started` skill, and a sendable non-sensitive
setup report), new non-goal (nothing this repo ships reads proprietary
data; fixtures stay synthetic), rewritten personas row (the author can
never reproduce a DE failure — everything must be diagnosable from a run
dir plus a setup report), and the cockpit drills move to the work laptop
since a green run on the dev machine proves only that the script parses.

*Decision 3 — no driver-side cost capture, conditional on live
visibility.* §B v1 moves from "deferred" to **declined**, with the
revisit condition named (a harness that reports usage only in a channel
the driver consumes and discards). In exchange, **§B v0.5** is now a
prerequisite of the cockpit rather than a nicety: `cost_report.py
--watch` renders a compact spend block against an in-flight run dir with
mid-flight tolerance, and MISSION's spend line has no other source. Goal
1 was rewritten around during-run visibility, and v2 token budgets are
struck as unreachable without v1.

*Decision 4 — moot.* With v1 declined, the stanza-digest exclusion rule
is no longer a prerequisite of anything; the "no frozen surfaces"
non-goal becomes unconditional and step 7 shrinks to optional event-kind
reservations that block nothing.

*Decision 5 — §D split out* to `docs/PROPOSAL-unattended-mode.md`,
unchanged, with its three residual risks and its open question moved
with it, plus explicit entry conditions. Its two guarantees are restated
here in place (human channel never forged; egress always human-approved)
since they bind this document regardless.

*Decision 6 — planted-defect design deferred* to first contact, since it
requires the colleague's domain and the proprietary repo. The criterion
itself stays mandatory and pre-registered.

Two new open questions fall out of distributing to *several* colleagues
rather than one: how flows and personas are shared and re-issued across
per-person installs (7), and whether the retro's metadata projection may
travel back to the author at all (8) — the latter being the colleague's
call and their employer's, not a design decision.
