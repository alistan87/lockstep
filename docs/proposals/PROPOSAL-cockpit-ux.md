---
type: proposal
title: "Proposal: cockpit UX — the three tiers (mechanical repairs, new surfaces, one process)"
description: Extends rev 7 §A with nine recorded defects and a three-tier UX programme, ending in a dependency-free TUI, a read-only web MISSION, and risk-tiered approvals.
resource: docs/proposals/PROPOSAL-cockpit-ux.md
status: draft
---
# Proposal: cockpit UX — the three tiers

**Status:** draft, 2026-08-03. **Extends** `PROPOSAL-domain-cockpit-rev7.md`
§A (the cockpit UX) and does **not** supersede it: rev 7 §B (cost tracking),
§C (gate-driven improvement) and its adopted invariants stand unchanged. Where
this document revises a rev-7 decision it says so explicitly and gives the
reason (§T2.1 is the only such case).

**Scope:** `contrib/` and the three CLI seams that `contrib/` cannot reach from
outside the engine. No `format_version` change. No new runtime dependency. One
frozen-surface touch, declared in §9 and destined for `DEVIATIONS.md`.

---

## 1. Why now

The cockpit shipped and was driven against real runs. Nine things were found by
reading the shipped code and the artifacts it produced — not by speculation.
They are recorded here first because the tiers below are answers to them, and a
proposal whose motivation is a list of aspirations tends to produce features
nobody needed.

| # | Finding | Evidence |
|---|---|---|
| F1 | `Wait-PaneProgram` is called but defined nowhere in the repo. | `contrib/cockpit.ps1:766`; `grep` across the tree returns exactly one hit — the call site. |
| F2 | A rejection carries no reason. The engine records `error="approval rejected"` and nothing else; `approve.ps1` tells the DE to "say so in the chat". | `src/lockstep/roles.py:1166`; `contrib/approve.ps1:103` |
| F3 | `e` (edit) is a live hazard guarded only by prose. It writes free text as the approval's *result*, and terminating it needs Ctrl-Z then Enter. | `roles.py:1168-1177`; the warning appears in `COCKPIT-FOR-DOMAIN-EXPERTS.md` and again in the pane banner. |
| F4 | MISSION spawns `python contrib/cost_report.py` **once per second**, and each call re-walks every phase directory. | `cockpit.ps1` `Show-Mission` loop, `$Interval` default `1.0`. |
| F5 | MISSION full-screen-clears every tick, destroying scrollback and flickering. | `Show-Mission` calls `Clear-Pane` unconditionally per iteration. `cost_report.py:_watch` already demonstrates the repaint-on-change fix. |
| F6 | ACTIVITY discards the structured half of a progress record. The protocol emits `{step, pct, note}`; the pane prints `note` only. | `executors/harness.py:55` (the instruction), `cli.py:355` (status *does* render pct), `cockpit.ps1:441`. |
| F7 | The heartbeat leaves the cursor mid-line, so the next progress line is appended onto it. | `cockpit.ps1:447` writes `\r  working - N m elapsed` with `-NoNewline`; line 442 then writes at that column. |
| F8 | Rendered evidence can be unreadable at the decision point. The shipped demo's approval evidence contains a single unbroken ~350-word paragraph. | `runs/repo-hygiene-demo-20260802T191307Z/approval-evidence.txt`, the `deterministic gate: pass` block. |
| F9 | MISSION addresses the DE in engineering identifiers (`preflight`, `apply`). `Node` is `extra="forbid"`, so there is nowhere to put a human name. | `taskgraph.py` `Node`; `cockpit.ps1:306`. |

Two of these are worth naming as *classes* rather than bugs, because the tiers
are shaped around them.

**F2 is an asymmetry the rest of the design forbids.** Evidence travels
human-ward mechanically, on the stated grounds that a narrated summary at a
decision point is untrustworthy. The human's *reason for rejecting* — the single
most decision-relevant thing they produce all session — travels back through the
orchestrator's narration. The argument that justified `approval-evidence.txt`
applies unchanged in the other direction.

**F3 and F8 are the same defect at two altitudes:** a hazard the design mitigates
with a sentence in a document. Everywhere else the cockpit refuses this — non-TTY
auto-reject, no `send-text` path, a predicate with an exit code — precisely
because discipline is not a mechanism. These two are the residue.

---

## 2. What binds every change below

Inherited, non-negotiable, and each proposal in §§4-6 is to be read against them:

1. **The mechanical tier stays mechanical.** MISSION, ACTIVITY, and
   `approval-evidence.txt` are field mappings and file copies. A change that
   introduces a narrated branch into any of them destroys the DE's only trust
   anchor and is rejected regardless of merit.
2. **No `send-text`, ever.** Not into an approval pane, not into any pane. The
   guarantee is the *absence of the code path*.
3. **A view never takes the run down.** L-B2 reader discipline (`FileShare
   ReadWrite|Delete`, poll ≥ 0.5 s, length-regression ⇒ reopen, partial
   trailing line tolerated, every error display-only) applies to every new
   reader in this proposal without exception.
4. **The DE decides from the artifact.** No new surface may become a competing
   summary of a decision.
5. **`pydantic` remains the only runtime dependency.** This is what makes §T3.1
   resolve the way it does.

---

## 3. The three tiers, in one sentence each

- **Tier 1 — repair the mechanical layer.** Nine defects, no new surfaces, no
  new concepts for the DE to learn.
- **Tier 2 — add surfaces that make existing obligations verifiable at the
  moment rather than in the retro.**
- **Tier 3 — collapse the polling panes into one process, put MISSION on a
  read-only local page, and stop treating every approval as equally weighty.**

---

## 4. Tier 1 — mechanical repairs

### T1.1 Define the missing verification (F1)

`Wait-PaneProgram` is replaced by the mechanism that already works:
`New-VerifiedPane`, a single helper that spawns a pane and requires the
handshake file (marker + `$env:WEZTERM_PANE`) before returning the pane id.
`New-ApprovalPane` and the ACTIVITY spawn both go through it. This removes the
undefined call *and* closes the gap it was hiding — ACTIVITY has never actually
been verified, only the approval pane has.

The asymmetry in *consequence* is kept, and made explicit in code: a failed
handshake on an approval pane **kills the pane and aborts**; on a view pane it
**downgrades and reports**. A view is not a decision surface.

### T1.2 Capture the rejection reason (F2)

After `lockstep resume` returns 6, `approve.ps1` prompts once:

```
Rejected. In one line - what was wrong? (Enter to skip)
```

and writes `<run_dir>/rejection.txt`: the verbatim line, the approval node id,
and an ISO timestamp. Nothing is typed for the human; nothing is inferred if
they skip.

Three consequences, all intended:

- The orchestrator has an artifact to cite instead of a memory to relay.
- `retrospect.py` gains a second tripwire, symmetric with the gate-finding one:
  compare the DE's own words against the orchestrator's account of why the work
  was sent back.
- A rejection that the orchestrator never mentions is now *visible* as drift.

`rejection.txt` is a new artifact class — **written by the human, not by the
orchestrator and not by the engine.** It is therefore evidence in the same sense
`approval-evidence.txt` is, and it is explicitly not part of the journal, which
is the orchestrator's own record.

### T1.3 `--cockpit` on `resume`: `a` or `r`, nothing else (F3)

A new flag, default off, spec behaviour unchanged without it. With it, the
approval prompt accepts only `a`/`approve` and `r`/`reject`; `e` is refused with
one line telling the human to type `r` and explain in the chat.

This is the one frozen-surface touch (§9). It is worth it: `e` exists to let an
*operator* substitute an approval's result text, which is a coherent thing for
an operator to want and an incoherent thing to offer a non-programmer who has
been told never to use it. The flag makes the DE-facing surface match the
DE-facing documentation, by construction rather than by warning.

`approve.ps1` passes `--cockpit`. Nobody else does.

### T1.4 Throttle spend; repaint on change (F4, F5)

Two independent cadences in `Show-Mission`:

- status redraw on change, at `$Interval` (default 1 s);
- spend recomputed at most every 10 s (`-SpendInterval`), cached in between.

Repaint is gated on a rendered-text comparison, exactly as `cost_report.py
_watch` does it. A pane that repaints its whole surface once a second destroys a
human's ability to tell new information from old — the same reasoning that
already forced ACTIVITY to read incrementally (L-B2's rationale note).

The monotonic guard on spend totals is preserved: the cached block is only
replaced by a newer one that is not smaller.

### T1.5 Render `step` and `pct` (F6)

ACTIVITY renders a progress record as:

```
  [####------]  40%  step 2 of 5 - reading the manifest
```

`pct` absent ⇒ no bar, no invented denominator. `step` absent ⇒ note only.
Nothing is estimated; every element comes from the record.

### T1.6 Fix the heartbeat collision (F7)

The heartbeat becomes a discrete line that is erased before any content is
written (`\r` + clear-to-EOL, then the content, then re-emit the heartbeat).

### T1.7 The MISSION headline

One line above the node list, computed from `state.json` and the run's own flow
copy:

```
step 3 of 8  -  running  -  14 m  -  1 rework round  -  a decision is 2 steps away
```

Nodes in `done` collapse to the count; `running`, `blocked`, and anything with a
heal round are always listed in full. Rev 7 §A.3 set out to stop attention
scaling with graph width, and then MISSION listed every node — this finishes the
job it started. "A decision is N steps away" is a graph distance to the nearest
approval, not a prediction.

### T1.8 Human labels via a sidecar (F9)

`flows/<name>.labels.json`: `{"nodes": {"preflight": "checking the plan is safe
to apply"}}`, read only by the cockpit's view layer, falling back to the node id
when absent or unreadable. Still mechanical — a file lookup — so the trust tier
is unaffected, and the taskgraph format is untouched.

### T1.9 Consent backed by an artifact

`lockstep run <flow> --estimate` already computes a cost floor from prior runs
and spends nothing; `--dry-run` already produces the layered plan. Neither
reaches the DE. `contrib/plan_card.py` renders both into one block:

```
  5 steps - 2 automatic checks - 1 decision from you - up to 2 rework rounds
  budget ceiling: 25 agent tasks
  prior runs of this flow: 3 (11-19 agent tasks, 6-14 m)
```

written to `<runs_root>/plan-card.txt` and displayed by MISSION before launch.

This closes the last place where the DE is asked to trust an unbacked number:
the consent beat currently has the orchestrator quoting *its own memory* ("last
week's similar run was about $N"). Under the standing bargain — every number you
quote is one they can verify — that sentence should never have been in the
protocol.

---

## 5. Tier 2 — new surfaces, same guarantees

### T2.1 A question card (revises a rev-7 decision)

Rev 7 §A.3 gives clarifications **no pane**: CHAT carries the §A.1 ritual.
**This proposal revises that**, and states the reason.

The obligation at a clarification is that the DE receives the finding
*verbatim* alongside the plain-language relay. Today that obligation is
discharged by orchestrator discipline and checked *after the fact* by
`retrospect.py`'s token-overlap tripwire. That is precisely the arrangement the
evidence rule rejected for approvals: a narrated relay at a decision point,
audited later.

`contrib/question_card.py` renders `phases/<gate>/result.json` findings with
`category: "question"` verbatim into `<run_dir>/question-card.txt`; ACTIVITY
displays it while the gate is blocked. **Display only.** The answer still travels
chat → `steer` → detached resume; there is no input path, so nothing about the
human channel changes. What changes is that the DE can see the original words
at the moment they answer, instead of a reviewer seeing them a week later.

The rev-7 rationale for no pane was that a clarification is a *conversation* and
a pane would fragment it. That holds for the conversation; it does not hold for
the source text, which is what a card carries.

### T2.2 Notification on "needs you"

The largest practical gap: exit 6 and gate blocks are invisible unless the DE is
looking at the screen. `cockpit.ps1 -Role mission` fires once per *transition*
into a needs-you state (never on a poll that merely observes it):

- terminal bell, and the pane/window title becomes `NEEDS YOU - <run>`;
- if `$env:LOCKSTEP_NOTIFY_URL` is set, one POST with `<run> - needs you` — no
  run content, no findings, no evidence.

Dependency-free, defaults to local-only, and deliberately carries no payload:
the run dir is sensitive and a notification is not a delivery channel.

### T2.3 Drill-down from MISSION

MISSION becomes reachable by keyboard in the Tier-3 TUI (§T3.1) and, before
that, by a `-Role why -Node <id>` invocation: dump that node's last verdict,
heal reason, error, and result path. Display-only, read through the L-B2
primitive, no state written.

### T2.4 Blast radius and reversibility in the evidence (F8)

`render_evidence.py` gains two mechanical lines and one formatting rule:

- **`--impact`**: counts derived from the diff or a manifest — files added /
  modified / deleted / renamed, and whether anything is deleted.
- **`--reversible <text>`**: a flow-author-supplied, literal statement of how to
  undo it. Absent ⇒ the line reads `reversibility: not stated by this flow`,
  which is itself information.
- Verdict prose is **hard-wrapped at 78 columns and truncated at
  `--max-verdict-lines` (default 12)** with a pointer to the full text. F8 is
  what an untruncated verdict looks like in a pane.

This is the highest-value item in the proposal per line of code. The recurring
HITL failure mode is not that humans decide wrongly; it is that the packet they
decide from is unreadable, so the gate becomes theatre. The literature's
recommended packet is intent, affected resources, confidence, **blast radius**,
and **reversibility** — the evidence file carries the first three and neither of
the last two.

---

## 6. Tier 3 — one process, one page, and proportion

### T3.1 One TUI process — and why not Textual

**Evaluated: Textual.** It would give scrollback, collapsible sections, a live
Gantt, keybinds, and 60 fps repaint for very little code. **Rejected**, on this
repo's own working agreement: `pydantic` is the only runtime dependency and the
rule is to prefer deleting a feature over adding one. A cockpit that is
`pip install`-fragile on a work laptop nobody here administers is worse than a
plainer cockpit that always starts — and the cockpit's whole promise to the DE
is *"double-click `start-cockpit.cmd`; it is the same every time."*

**Adopted instead: `contrib/mission_tui.py`, standard library only.** Alternate
screen buffer (so scrollback survives), a line-diffing renderer (no full
repaints, killing F5 structurally), one process instead of two polling
PowerShell loops plus a per-second Python subprocess (killing F4 structurally),
and keybinds via `msvcrt.kbhit` on Windows / `termios` elsewhere:

| key | effect |
|---|---|
| `1`-`9` | drill down into that node (T2.3) |
| `e` | show the current `approval-evidence.txt` |
| `q` | quit the view (never the run) |

It renders MISSION and ACTIVITY in one frame, which also removes the two panes'
independent ideas of which node is the frontier.

**It is additive.** `cockpit.ps1` keeps working, unchanged in behaviour and
still the default for `-Role mission|activity`; the TUI is opt-in via
`start-cockpit.cmd` detection and a `-Tui` switch. The rule stands: correctness
lives in the PowerShell path, because that is the one that ships to machines
whose terminal nobody here controls.

The pure rendering functions are ordinary Python and therefore testable, which
the PowerShell panes never were. That is the second reason to prefer this over
Textual: it moves the view layer into the part of the repo that has a test
suite.

### T3.2 Read-only web MISSION

`contrib/mission_server.py`, `http.server` from the standard library, bound to
`127.0.0.1` by default, serving one auto-refreshing page rendered from the same
functions the TUI uses. It removes the WezTerm dependency for *observation* and
works from a phone on the same machine.

**The approval never moves.** A browser button is exactly the forgeable channel
this design exists to prevent, and the page carries no form, no POST handler,
and no route that writes. The page states in its own header that decisions
happen in the terminal.

Binding to anything other than a loopback address requires an explicit
`--host` and prints a warning naming what is being exposed: `runs/` holds
prompts, diffs, and model output.

### T3.3 Risk-tiered approvals

Every approval is currently equally weighty, which is the standard road to
rubber-stamping. A flow may declare a tier per approval in the labels sidecar
(so, again, no format change):

| tier | evidence header | behaviour |
|---|---|---|
| `routine` | `ROUTINE - reversible, N files` | unchanged prompt, quieter framing |
| `standard` | current behaviour | default when undeclared |
| `irreversible` | `IRREVERSIBLE - this cannot be undone by this flow` | banner, and the impact block is mandatory: a missing `--impact` renders as a refusal to characterise, not as "no impact" |

**No approval is ever skipped by a tier.** Tiering changes *presentation and the
required evidence*, never whether a human is asked. Auto-passing on a
self-declared tier would let a flow author quietly remove the human, which is
the one thing the trust model does not permit. Proportion is achieved by making
the serious ones louder, not the routine ones absent.

---

## 7. Comparative review — what is borrowed, and from where

| Source | Mechanism | Taken |
|---|---|---|
| HumanLayer | approvals as an API; Slack/email/SMS routing; persistent approval record | The out-of-band **signal** (T2.2). Not the channel: deciding in Slack is a forgeable surface. |
| LangGraph `interrupt()` | approve / **edit** / **reject-with-feedback** / respond as first-class resume types | Reject-with-feedback (T1.2). Confirms F2 is a missing normal shape, not an extension. |
| Temporal | signal-based approval, durable timers, escalation on no response | The **clock** (§10, deferred): a blocked run has no notion of "you have been waiting 3 days". |
| Argo Workflows | `suspend` gate in a DAG | Nothing — the approval role is the same primitive, better guarded. |
| Dagster | per-op Gantt above a filterable event/log pane | The Gantt (T3.1). `events.jsonl` timestamps and r7 spans already contain it; "where did the 40 minutes go" is currently unanswerable. |
| Prefect | per-task isolated logs and state | Already present as `phases/<node>/`; what was missing is navigation (T2.3). |
| Vibe Kanban / Conductor / Crystal | worktree-per-agent boards | The card metaphor for a collapsed view (T1.7). Noted as a caution: all are *programmer* tools that surface diffs, branches and worktrees — this cockpit's user must never see git. |
| HITL literature (approval/oversight fatigue) | risk-tiered pauses; a decision packet of intent, resources, confidence, **blast radius**, **reversibility** | T2.4 and T3.3. |

---

## 8. Implementation order

Sequenced so the suite stays green at every step and each stage is independently
revertible.

1. **T1.1** — pane verification (no behaviour change on a healthy machine).
2. **T1.4-T1.7** — MISSION/ACTIVITY rendering repairs.
3. **T1.8** — labels sidecar (view layer only).
4. **T1.2** — `rejection.txt` + `retrospect.py` tripwire. *Tests.*
5. **T1.3** — `resume --cockpit`. *Tests. DEVIATIONS entry.*
6. **T2.4** — evidence impact/reversibility/wrapping. *Tests.*
7. **T1.9 / T2.1** — plan card, question card. *Tests.*
8. **T2.2 / T2.3** — notification, drill-down.
9. **T3.1** — `mission_tui.py`, pure render functions first. *Tests.*
10. **T3.2** — `mission_server.py` over the same render functions. *Tests.*
11. **T3.3** — tiering in the labels sidecar + evidence. *Tests.*
12. Docs: both cockpit guides, `CLAUDE.md` ops notes, `DEVIATIONS.md`.

## 9. Frozen surfaces touched

**One.** `resume --cockpit` (T1.3) changes the accepted answers at the SPEC §9.3
approval prompt when — and only when — the flag is passed. Default behaviour is
byte-identical. Exit codes, `format_version`, the §7 fencing/footer contract and
hash composition are untouched. A `DEVIATIONS.md` entry lands with the commit:
what, why, date.

`Node` is **not** extended (T1.8, T3.3 both route through the sidecar), so no
flow file written today stops verifying.

## 10. Risks, and what is deliberately not done

- **New surfaces dilute the trust anchor.** Mitigation: every surface in this
  proposal is a projection of the same `state.json` + flow copy + `result.json`
  through the same L-B2 primitive. None is a second source of truth. The
  question card and the drill-down are display-only by construction.
- **The TUI becomes the real cockpit and the PowerShell path rots.** Mitigation:
  `cockpit.ps1` stays the default and the TUI is opt-in. If that inverts later,
  it should be a decision with its own commit, not a drift.
- **The web page leaks.** Mitigation: loopback default, no write routes, an
  explicit `--host` with a warning. `runs/` remains gitignored and sensitive.
- **A rejection prompt trains the DE to explain themselves.** They were told `r`
  costs nothing and needs no justification. Mitigation: Enter-to-skip, and the
  prompt says so.
- **Deferred: the waiting clock.** Temporal's durable timer + escalation is the
  right answer to "the run has been blocked for three days", but it needs an
  owner for the escalation target and belongs with the unattended-mode work,
  not here.
- **Deferred: behavioural evidence.** For code deliverables a diffstat is not
  something a non-programmer can judge; what they can judge is "these five things
  now work". Generating that mechanically is a real research problem and is not
  attempted.

## 11. Test plan

- `tests/test_cockpit_ux.py` — plan card, question card, impact/reversibility,
  verdict wrapping, headline computation, label resolution and fallback,
  risk-tier rendering.
- `tests/test_approval_cockpit.py` — `--cockpit` accepts `a`/`r`, refuses `e`
  and re-prompts, is byte-identical to today without the flag, and still
  auto-rejects on non-TTY stdin.
- `tests/test_rejection_reason.py` — `rejection.txt` shape; skip writes no file;
  `retrospect.py` reports an unrelayed rejection as drift.
- `tests/test_mission_render.py` — pure render functions: monotonic spend,
  repaint-on-change, collapse rules, `pct`/`step` rendering with fields absent.
- `tests/test_mission_server.py` — loopback default, no write routes, renders
  from the same functions.
- PowerShell paths remain untested by pytest, as today. T3.1 exists partly to
  move the view layer into the tested half of the repo.
