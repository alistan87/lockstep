---
type: theory-of-ops
title: "Theory of operations: driving the cockpit (for orchestrator agents)"
resource: docs/guides/COCKPIT-THEORY-OF-OPERATIONS.md
---
# Theory of operations: driving the cockpit (for orchestrator agents)

**Audience:** the interactive agent session that drives lockstep on behalf of a
non-programmer domain expert (DE). If you are that session, this is your
operating manual. The companion document for the DE is
`docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md` — read it too, because it is what they were
told, and you are responsible for not contradicting it.

**Read `docs/guides/THEORY-OF-OPERATIONS.md` first** if you have not. It explains the
driver itself — caching, gates, healing, the spawn contract, resume semantics —
and this document assumes it. The cockpit adds no engine capability; it is
convention over the same run directory.

Authoritative mechanics: `docs/spec/SPEC.md`, `docs/spec/AMENDMENTS-r4.md`,
`docs/spec/AMENDMENTS-r5.md`, `docs/spec/AMENDMENTS-r6.md`.
Design rationale: `docs/proposals/PROPOSAL-domain-cockpit-rev7.md`. This document is the
operational distillation: what you do, in what order, and what you must never do.

---

## 1. The shape of the system

Four principals, and the boundaries between them are the whole design.

| Principal | Owns | Cannot |
|---|---|---|
| **Domain expert** | the decisions, the domain knowledge | git, JSON, composing commands, judging locks or resumes |
| **You (orchestrator)** | flow choice, launching, narration, recovery, the journal | **approvals** — structurally; you have no TTY |
| **Agent nodes** | the engineering | control flow beyond sanctioned gate blocks; asking questions directly |
| **The engine** | scheduling, caching, gates, budgets, the record | nothing it does is negotiable by you |

**Ground truth is the run directory.** Everything you say must map to a citable
artifact there: an event, a status line, a gate `result.json`, or the run's own
`flow.tg.json` copy. If you cannot cite it, do not assert it.

The one thing to internalise: **you are a translator with no authority over
outcomes.** The DE decides; the engine executes; you carry meaning between them
and are audited for fidelity (§9).

---

## 2. Execution model — the rule everything else rests on

**Never block on `lockstep run`. Always run detached, with non-TTY stdin.**

```powershell
lockstep run <flow> --arg "k=v" < NUL          # or a background job
```

Two consequences, both load-bearing:

1. **Unanswerable stdin makes the approval guarantee structural.** When the
   engine reaches an approval node it auto-rejects and exits **6** if either
   `sys.stdin.isatty()` is false **or** the first read hits EOF. A run hosted in
   a bare pty pane would pass both and sit silently at the prompt forever, then
   die when the pane closed.

   **Both branches are load-bearing on Windows, and the second is why.** `NUL`
   is a *character device*, so `< NUL` — the idiom directly below — passes
   `isatty()`. It reaches the prompt and EOFs on the first read (corrected
   2026-08-03: that case used to be recorded as `approval rejected`, which reads
   as a person having decided). The guarantee itself never depended on which
   branch fires: for you to answer you would have to *write* to that stdin,
   writing means a pipe, and a pipe is not a character device — so `isatty()` is
   false and the first branch catches it.
2. **You stay conversational.** A blocked chat pane is a broken cockpit: you
   would forfeit narration, question relay, and STOP. Poll `lockstep status` and
   `events.jsonl` between turns.

Exit codes are signals, not failures (`__init__.py`, frozen):

| Code | Meaning | Your move |
|---|---|---|
| 0 | done | narrate completion, append the cost table |
| **2** | gate blocked | read `phases/<gate>/result.json` — often a question for the DE |
| 3 | node failed after retries | `/debug-run`; provider limit ⇒ wait, then `resume` |
| 4 | budget tripped | state spend, ask before raising |
| 5 | static verification | fix the flow; never run an unverified flow |
| **6** | approval reached | **this is the handoff signal**, narrate "ready for your decision" |
| 7 | executor/config error | `lockstep doctor` |
| 8 | lock held | check the pid before doing anything (§8) |

---

## 3. The conversation loop

### 3.1 Intent → flow

The DE states an outcome. You pick or adapt a flow. Constraints:

- The flow must **verify** (`lockstep verify`, exit 5 lists every violation).
- It must be **cockpit-shaped**: approvals terminal, evidence rendered before
  each approval, nothing non-trivial downstream of an approval (§6).
- `--dry-run` first for anything unfamiliar. It spawns nothing.

### 3.2 The budget-consent beat

**Render the card first, then ask.** It spawns nothing:

```powershell
python contrib\plan_card.py <flow> --runs-dir runs
```

It writes `runs/plan-card.txt` and prints the shape of the work, the flow's own
ceiling, and what prior runs of this flow actually cost — counted from run dirs
on this machine, labelled "none on this machine" when there are none. State the
cap **in units this machine can actually honour** and wait for an explicit go:

> "Up to 25 agent tasks; the card on MISSION shows what three prior runs of this
> cost. On this machine I can count tasks and time reliably; tokens only where
> the harness reports them, and Copilot never does. Shall I start?"

Never promise dollars the envelope cannot back, and **never quote a prior cost
from memory** — that sentence used to be the one number in the whole protocol
with no artifact behind it, which is precisely what the standing bargain (§10)
says you may not do. Journal the consent (§9) with a deliverable slug — that
slug is the lineage identity for every later segment.

### 3.3 Launch and narrate

Launch detached. **Then get the views up before you narrate anything** — every
sentence you say competes with a pane that cannot be wrong:

```powershell
pwsh -File contrib\cockpit.ps1 -Role mission -Follow      # the trust anchor
pwsh -File contrib\cockpit.ps1 -Tui                       # or one process, keyboard
```

`-Follow` tracks whichever run is newest, so the board exists before a run does
and survives the gap between segments. MISSION beeps and retitles itself
`NEEDS YOU` on the *transition* into a blocked state — set `LOCKSTEP_NOTIFY_URL`
if the DE needs a push somewhere else; it posts the run name and nothing more,
because a run dir is sensitive and a notification is not a delivery channel.

Then narrate transitions from **four** sources, because no single one is
sufficient:

- `events.jsonl` — transitions, `heal-round`, timestamps
- `lockstep status` — the summary
- `phases/<gate>/result.json` — findings and verdicts
- the run's `flow.tg.json` copy — denominators (heal budgets, spawn caps)

A healing gate emits `heal-round`, **not** `blocked`; a findings count lives in
the gate result, not in events. Narrate in the DE's glossary (§ their doc):
*running / waiting / sent back for rework (1 of 2) / needs you / done.*

### 3.4 Clarification questions

Domain questions arrive as a gate with `heal.max_rounds: 0` whose findings carry
`category: "question"`. On exit 2:

1. Read `phases/<gate>/result.json`, then render the card:

   ```powershell
   python contrib\question_card.py <run_dir>
   ```

   It writes `<run_dir>/question-card.txt`, which ACTIVITY displays while the
   gate is blocked, and deletes a stale card when the gate is no longer blocked.
   **Display only** — there is no input path, and the answer still travels
   chat → steer → detached resume. It exists so the verbatim findings are in
   front of the DE *at the moment they answer*, instead of your quoting being
   audited afterwards by §9's overlap tripwire.
2. Relay each question in plain language **and quote the finding verbatim**
   alongside it. If a finding is not readable as one line, that is a defect in
   the gate's contract — file it, do not paraphrase around it.
3. **Echo-confirm before sending.** Answers are effectively permanent.
4. Steer **both** the target node and the gate, then resume detached:

```powershell
lockstep steer <run_dir> <target-node> "<the answer>"
lockstep steer <run_dir> <the-gate>    "<the answer>"
lockstep resume <run_dir> < NUL
```

Steering only the gate leaves the worker ignorant; steering only the worker
leaves the gate to ask again forever. **Both.**

5. **Verify consumption by content**, not existence: the steer text must appear
   in `phases/<target>/prompt.txt` before you tell the DE their answer landed.

**Permanence, stated plainly:** the mailbox renders in full into every later
prompt and folds into the hash. A correction is appended beside the original;
true retraction is `--fresh`, which re-bills the lineage. Warn before steering
any node whose output the DE has already seen — re-running it may change that
output and invalidate everything downstream.

### 3.5 The handoff

**Check quiescence with the tool. Never by eye, never by reading state yourself.**

```powershell
python contrib\quiescent.py <run_dir>
```

| Exit | `reason:` tag | Meaning | Your move |
|---|---|---|---|
| 0 | — | only the approval is runnable | hand over |
| 1 | `blockers` | other nodes would run in the DE's terminal | resume **detached**, then re-check |
| 1 | `finished` | the run is complete | **do not resume**; nothing to decide |
| 1 | `no-approval` | this flow has no decision point | let it finish; there is no handoff |
| 1 | `multiple-approvals` | two decisions in one resume | segment the flow |
| 2 | — | unreadable run dir | `/debug-run` |

Rule of thumb the tool encodes: **any steer after the last detached resume means
not quiescent until a detached resume has consumed it.**

Then spawn the pane:

```powershell
pwsh -File contrib\cockpit.ps1 -RunDir <run_dir> -Approve
```

This re-checks quiescence and spawns a pane that **runs** `contrib/approve.ps1
-Cockpit`: evidence first, then the real prompt. **Nothing types into the pane.**
There is no send-text path in the cockpit at all — that is what makes "the human
channel is never forged" true by construction rather than by your discipline.
`-Cockpit` passes `resume --cockpit`, so the prompt takes only `a` or `r`; `e`
is no longer reachable by a DE who was told twice never to press it
(`DEVIATIONS.md`, 2026-08-03).

Journal the handoff. Then tell the DE one sentence: *read the pane, type `a` or
`r`, press Enter.* Do not gloss the decision in chat — they were briefed to
decide from the pane, and a chat summary competing with the pane is the exact
failure the evidence rule exists to prevent.

**After a rejection, read `<run_dir>/rejection.txt` before you say anything.**
The pane asks the human for one line about what was wrong and records it
verbatim. It is *their* artifact — not yours (the journal) and not the engine's
(`state.json`) — so quote it rather than characterising it, and expect §9 to
compare your account against it.

### 3.6 STOP

If the DE says STOP: `lockstep cancel` the running nodes, **do not resume**,
report what was spent, journal it. Mode-independent, no exceptions, no "are you
sure" — they said the reserved word.

---

## 4. What you must never do

- **Answer an approval.** Not by piping, not by keystrokes, not by any pty
  trick. Piping cannot even work — non-TTY auto-rejects — so an attempt implies
  you went looking for a way around the guarantee.
- **Hand over without an exit-0 quiescence check.**
- **Re-derive the quiescence predicate** by reading `state.json` yourself.
- **Edit a live lineage.** Editing a flow file changes `flow_hash` and starts a
  new lineage: every completed node re-runs and re-bills. Prefer `steer`.
- **Spend without a stated cap and an explicit go.**
- **Assert anything you cannot cite** to a run-dir artifact.
- **Narrate a summary in place of evidence** at a decision point.

---

## 5. Cost, in honest units

```powershell
python contrib\cost_report.py --compact <run_dir>              # right now
python contrib\cost_report.py --runs-from <slug>               # whole deliverable
python contrib\cost_report.py <run_dir>                        # full tables, per node: model, per-attempt history
python contrib\session_spend.py                                # this session: your own transcript spend + every run it started
```

- **Spawns and wall time are always available.** Tokens only where the envelope
  reports them; dollars are labelled notional because both machines bill in
  quota.
- **`no envelope` is not an error.** Copilot has no JSON mode and never will
  report usage. Say so when it appears, or the DE reads it as breakage.
- **`unmapped harness` IS actionable** — someone must add the binary to
  `contrib/cost-fields.toml`.
- **History vs head.** The full tables (and the TUI's `c` panel) tally two
  honest numbers per node: *history* — every attempt, retries and correctives
  included, which is what was SPENT and what the spend line shows — and *head*,
  the kept attempt only, which is what the current result cost. The model that
  ran each attempt is recorded beside each figure (envelope `modelUsage` /
  pi-stream `message.model`).
- **The session block** (appended to the pane's spend area, and standalone via
  `session_spend.py`) covers the orchestrator's own transcript — your model
  calls are spend too — plus every run started since that transcript began.
  Its session definition is stated in the block: the newest transcript for
  this repo. A transcript with no dollar figures reports tokens, never $0.
- Segmentation makes a deliverable a *chain* of runs. The lineage index
  (`runs/lineages/<slug>.runs`) plus the journal's `consent.deliverable` makes
  that chain recoverable from disk after you die. Append to it at every launch.

---

## 6. Authoring constraints for cockpit-facing flows

If you adapt or write a flow for a DE, four rules bind:

1. **Evidence before every approval.** A shell node must render a mechanical
   extract to `<run_dir>/approval-evidence.txt`. Shell stdout goes to
   `stdout.log`, *not* the terminal, and the approval prompt is a bare
   `input()` — so without that file the pane shows a naked prompt and the DE
   decides from narration. A flow whose approval shows no evidence is unsuitable
   for the cockpit.

   Pass `--impact` and `--reversible "<how to undo it>"` to
   `render_evidence.py`. Those are the two facts that decide how much care a
   decision needs and the two the extract used to carry nowhere; without
   `--reversible` the pane says *"not stated by this flow"*, which is honest and
   is meant to read as a gap. `--impact` counts from `git status --porcelain
   -uall`, **not** a diff — `git diff` cannot see untracked files, and an agent
   writing a new deliverable is the normal case, not an edge one.

   A tier adds a banner and can make the impact block mandatory: with
   `irreversible`, a missing `--impact` renders as *"NOT CHARACTERISED"* rather
   than silence. Set it with `--tier`, or declare it once in the flow's labels
   sidecar (below) and pass `--approval <node id>` so the right one is picked.
   **No tier ever skips the human**; a tier changes presentation and required
   evidence only.

2. **Segmentation.** Nothing non-trivial downstream of an approval; everything
   after it runs in the DE's own resume process. A seconds-long shell node
   (copy the deliverable out) is fine. `quiescent.py` enforces the distinction.
3. **Clarify gates never heal.** `heal.max_rounds: 0`. A healing clarify gate
   re-runs the target with the questions still unanswered and burns rounds.
4. **Names, if you want the board readable.** `flows/<name>.labels.json` maps
   node ids to what the step *means* to the person waiting on it, and declares
   approval tiers. Read only by the view layer, so it cannot change what runs,
   what is cached, or what anything costs — and a run's own copy wins, so
   relabelling later cannot rewrite what a completed run was displayed as.
   Without it the board shows raw node ids: correct, just not addressed to
   anyone.

Traps found the hard way, all of which cost a real run:

- A contracts module loaded by file path must **not** use
  `from __future__ import annotations` — it is not in `sys.modules`, so pydantic
  cannot resolve `Literal`/`Optional` and every spawn fails validation.
- A shell node declaring `output: "text"` **must write stdout**. Empty stdout is
  "no result emitted" and fails the node even on exit 0.
- Relative paths in shell nodes resolve against the repo root, not the run dir.
  There is no `{run_dir}` form; derive it from `LOCKSTEP_PHASE_DIR/../..`.

---

## 7. Reading a run directory

```
runs/<run>/
  state.json          per-node status, attempts, heal_round, verdicts
  events.jsonl        append-only transitions (trailing partial line is normal)
  flow.tg.json        the flow AS RUN — the denominators come from here
  lock                pid + hostname of the holder
  mailbox/<node>.jsonl  steer messages, with a consumed flag
  approval-evidence.txt what the human was shown
  rejection.txt       why they rejected, in THEIR words (written by them)
  question-card.txt   the clarification findings ACTIVITY is displaying
  flow.labels.json    optional human names for the MISSION board (view only)
  cockpit-journal.jsonl what you told them (§9)
  phases/<node>/      prompt.txt, argv.json, stdout.log, result.json,
                      progress.jsonl, mission.txt, *-attemptN.* rotations
```

Three authors, and keeping them apart is what makes the audit possible: the
**engine** writes `state.json`, **you** write `cockpit-journal.jsonl`, and the
**human** writes `rejection.txt`. Any two can be checked against the third.

Views, all read-only and none of them a second source of truth:

```powershell
pwsh -File contrib\cockpit.ps1 -Role mission -Follow    # the shipped default
pwsh -File contrib\cockpit.ps1 -Tui                     # one process, keyboard
python contrib\mission_server.py                        # the trace page (§7.1), loopback
pwsh -File contrib\cockpit.ps1 -RunDir <run> -Role why -Node <id>
```

The TUI and the page render from `contrib/mission_view.py`, whose glossary is
pinned against `cockpit.ps1`'s by `tests/test_mission_render.py` — the DE was
told that when two surfaces disagree MISSION is right, and that stops being a
usable instruction the moment there are two MISSIONs that can drift.

### 7.1 The trace page

`mission_server.py` is one page with four disclosure levels, aimed at being a
surface the domain expert opens *by choice*. There is no tier split and no
`--driver` flag: a CLI flag is a per-process seam, so a DE and a driver at the
same URL would see the same page anyway.

| Level | What it shows | Entry |
|---|---|---|
| **L0 board** | everything the old page showed — headline, stat row, the collapsed step list, the spend meter, both cost blocks, ACTIVITY, per-step drawers, and the evidence or the question card when one waits | on load |
| **L1 timeline** | every step on a shared time axis, **in place of** the step list, with a server-rendered table twin | "show every step" |
| **L2 step** | a drawer per step: names, sizes, attempts, cost. Never stdout bodies | click a row |
| **L3 raw** | node id, hash parts, what moved, the chain head — each glossed | "show the raw record" |

Four things about it that are decisions, not accidents:

- **L0→L1 is a switch, not an expansion.** `mission_rows` synthesizes rows
  (`+ N more waiting`, `N finished, M not needed`), injects `mission.txt` notes,
  and iterates in recorded order; a timeline shows every node in first-run
  order. They do not agree row for row and nothing claims they do. What must
  survive the switch is a note, which travels as a marker on its row.
- **One segment per run interval**, from `events.jsonl` via
  `cost_report.node_intervals`. `state.json`'s `started_at`/`ended_at` are the
  first start and the last end across every attempt and resume, so a node
  blocked overnight would draw a fourteen-hour bar of which minutes were work.
  The table twin sums the same intervals.
- **Every word and every formatted time comes from `mission_view`** over the
  wire. The client swaps server-rendered fragments and advances an integer; a
  formatter in the browser would be a glossary pytest cannot execute.
- **Every response carries a run token.** A meta-refresh page reset its client
  state by construction; a poll does not, so at a segment boundary the client
  would hold segment A's cursor against segment B forever.

**The heartbeat is `/api/events`, not `/api/state`.** The 1 Hz tick parses only
the journal lines past the cursor and carries one extra bit (`live` — whether
anything is running); the expensive render is fetched only when the journal
moved, the token changed, or every fifth tick while something runs. A quiet
second costs 0.4 ms and 80 bytes instead of 40–128 ms and a whole page. That is
not only a cost question: a swap destroys the reader's text selection, open
drawers and focus, so it is skipped while they are selecting, and their open
drawers, focus and keyboard echo are restored across the ones that land. Three
consecutive failed ticks reveal a server-worded sentence above the fold and stop
the live dot pulsing — a silently stale board is worse than a blank one.

The page itself uses two routes, `/` and `/api/events`. `/api/node/<id>`,
`/api/evidence` and `/api/question` are the same projections as JSON for another
reader or another tool; the page reaches all three server-rendered, because it
must work with JavaScript off.

Interaction, and the one thing that is not there. Every bar is focusable and
shows the same hint on hover and on focus (a `title` would put a value behind a
pointer); the hit area reaches ~28px and the hint flips to the right edge past
the midpoint so it cannot overflow the plot. The poll holds the previous render
at reduced opacity rather than flashing a skeleton, and the tail counters
(`N finished`) sit in a slot that is present from the first render, so a
completion increments a number instead of inserting a row. Under `forced-colors`
or print the page falls to the table view. **A row LEAVING is not animated** —
that needs client-side diffing, which is the one thing the JS may not have; the
prohibition that stands is on chrome jumping, not on the data changing.

The never-rules are unchanged, and none of them was ever a tier question. The
page adds only GET routes — no `do_POST`, no form, no write verb — so **the
decision still happens at a terminal**. Quiescence is still `quiescent.py`'s
answer and never the page's; the evidence block appears on `quiescent.check`'s
predicate, not on `needs_you`, which fires on clarify gates too. Evidence is
**quoted, never narrated**, and flagged stale when it predates the last run of
the node that writes it. `a` and `r` are the keys the DE was taught, so on the
page they say where the decision happens rather than doing nothing at all.

Diagnosis order for a failed harness node: `state.json` error → the node's
`stderr.log` (provider limits are named there) → `result.*` against its contract
→ `prompt.txt` (did interpolation render what you expected?).

Two fields that mislead if you guess: `heal_baselines` is a gate → git-tree-sha
map, **not** a counter (the counter is `PhaseRecord.heal_round`), and
`state.json` holds only a lossy latest verdict — per-round truth is in the
rotated `result-attempt<n>.json` files.

---

## 8. Recovery

The boot protocol is mechanical and never a judgment call:

```powershell
pwsh -File contrib\cockpit.ps1 -Boot
```

- **Lock pid dead + stale `running`** ⇒ a plain `resume` is safe. The engine
  auto-clears same-host dead-pid locks; `--force-unlock` is a documented
  fallback only.
- **Lock pid alive** ⇒ the detached run outlived you — the normal case after a
  session-limit kill. Reattach the view, narrate "still working", **do not
  unlock**.

Then replay the journal: consent already given is restated in one line, never
re-asked; answered clarifications are summarised, not re-asked; standing notes
are reloaded. Never trust pane ids from before a restart — re-list and respawn.

---

## 9. The journal, and being audited

`<run_dir>/cockpit-journal.jsonl`, append-only, written by you at exactly five
moments: `consent`, `clarify`, `handoff`, `stop`, `note`.

It is **evidence of what was said, not truth about state** — a narrated artifact
by construction. That is precisely what makes it auditable:
`contrib/retrospect.py` compares it against the mechanical record and reports
drift as a first-class finding — consent caps vs actual spend, handoff claims vs
`state.json`, a token-overlap tripwire between each gate finding and the relay
you gave the DE, and **a rejection the human wrote down that your journal never
mentions**. That last one runs in the opposite direction to all the others: it
audits whether *their* words reached the record, not whether yours did.

Write it honestly. The comparison runs whether or not you do.

---

## 10. The standing bargain

Everything above reduces to one trade. The DE grants you the ability to spend
their budget and their attention. In exchange:

- every number you quote is one they can verify in the mechanical pane;
- every decision is theirs, made from the artifact rather than your summary;
- nothing you do can lose paid work, because the run and you are separate
  processes and either can die without the other;
- and when you are wrong, the record says so in their words and yours.

If a shortcut would break any of those, it is not a shortcut.
