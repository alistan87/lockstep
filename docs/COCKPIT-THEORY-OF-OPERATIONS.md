# Theory of operations: driving the cockpit (for orchestrator agents)

**Audience:** the interactive agent session that drives lockstep on behalf of a
non-programmer domain expert (DE). If you are that session, this is your
operating manual. The companion document for the DE is
`docs/COCKPIT-FOR-DOMAIN-EXPERTS.md` — read it too, because it is what they were
told, and you are responsible for not contradicting it.

Authoritative mechanics: `docs/SPEC.md` + `docs/AMENDMENTS-r4/r5/r6.md`.
Design rationale: `docs/PROPOSAL-domain-cockpit-rev7.md`. This document is the
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

1. **Non-TTY stdin makes the approval guarantee structural.** When the engine
   reaches an approval node and `sys.stdin.isatty()` is false, it auto-rejects
   and exits **6**. A run hosted in a bare pty pane would pass `isatty()`, sit
   silently at the prompt forever, and die when the pane closed.
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

Before spending anything, state the cap **in units this machine can actually
honour** and wait for an explicit go:

> "Up to 25 agent tasks. On this machine I can count tasks and time reliably;
> tokens only where the harness reports them, and Copilot never does. Last
> week's similar run was about $N on the bill. Shall I start?"

Never promise dollars the envelope cannot back. Journal the consent (§9) with a
deliverable slug — that slug is the lineage identity for every later segment.

### 3.3 Launch and narrate

Launch detached. Then narrate transitions from **four** sources, because no
single one is sufficient:

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

1. Read `phases/<gate>/result.json`.
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

This re-checks quiescence and spawns a pane that **runs** `contrib/approve.ps1`:
evidence first, then the real prompt. **Nothing types into the pane.** There is
no send-text path in the cockpit at all — that is what makes "the human channel
is never forged" true by construction rather than by your discipline.

Journal the handoff. Then tell the DE one sentence: *read the pane, type `a` or
`r`, press Enter.* Do not gloss the decision in chat — they were briefed to
decide from the pane, and a chat summary competing with the pane is the exact
failure the evidence rule exists to prevent.

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
```

- **Spawns and wall time are always available.** Tokens only where the envelope
  reports them; dollars are labelled notional because both machines bill in
  quota.
- **`no envelope` is not an error.** Copilot has no JSON mode and never will
  report usage. Say so when it appears, or the DE reads it as breakage.
- **`unmapped harness` IS actionable** — someone must add the binary to
  `contrib/cost-fields.toml`.
- Segmentation makes a deliverable a *chain* of runs. The lineage index
  (`runs/lineages/<slug>.runs`) plus the journal's `consent.deliverable` makes
  that chain recoverable from disk after you die. Append to it at every launch.

---

## 6. Authoring constraints for cockpit-facing flows

If you adapt or write a flow for a DE, three rules bind:

1. **Evidence before every approval.** A shell node must render a mechanical
   extract to `<run_dir>/approval-evidence.txt`. Shell stdout goes to
   `stdout.log`, *not* the terminal, and the approval prompt is a bare
   `input()` — so without that file the pane shows a naked prompt and the DE
   decides from narration. A flow whose approval shows no evidence is unsuitable
   for the cockpit.
2. **Segmentation.** Nothing non-trivial downstream of an approval; everything
   after it runs in the DE's own resume process. A seconds-long shell node
   (copy the deliverable out) is fine. `quiescent.py` enforces the distinction.
3. **Clarify gates never heal.** `heal.max_rounds: 0`. A healing clarify gate
   re-runs the target with the questions still unanswered and burns rounds.

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
  cockpit-journal.jsonl what you told them (§9)
  phases/<node>/      prompt.txt, argv.json, stdout.log, result.json,
                      progress.jsonl, *-attemptN.* rotations
```

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
`state.json`, and a token-overlap tripwire between each gate finding and the
relay you gave the DE.

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
