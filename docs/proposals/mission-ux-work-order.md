---
type: plan
title: "Work order: MISSION UX — the blocker card, one number per fact, and the peek panel"
description: Build-ready plan for three MISSION page improvements scoped 2026-09-11 — the stopped-run truth batch (blocker card, the failed-run AND dead-driver clocks, the blocker-path half of S2), deduplication (meter into tile, one cost disclosure), and detail-on-demand (peek panel + status-aware drawer threshold, closing S1.5). Three batches, each a commit, contrib-only, no frozen surface touched. Revised same day: an adversarial UX review returned seven findings, all adopted (§1b).
resource: docs/proposals/mission-ux-work-order.md
status: accepted 2026-09-12 — building
---
# Work order: MISSION UX

**Status: DRAFT.** Written from a scoping session on 2026-09-11 that read the
page source, rendered two real runs against it, and measured section byte
shares. Extends the MISSION page shipped by `PROPOSAL-sssf-adoptions.md` and
`PROPOSAL-cockpit-ux.md`; Batch 2 closes **S1.5** and Batch 0 builds the
blocker-path half of **S2**, both accepted in
`upstream-response-mission-scale.md`. Companion mockup:
`docs/proposals/mockups/mission-ux.html` (design target, not shipping code).

**Revised 2026-09-11** after an adversarial review focused on reader stories
(§1b). The review's diagnosis, kept here because it names the failure mode to
guard against while building: the original scoping worked from *rendering*
defects (what the page shows) rather than *reader stories* (what the person
does next) — which is how a dead driver rendering as a healthy run, an
unactionable blocker card, and a status-blind drawer threshold all slipped
past while the test scaffolding was airtight.

**Execute in order. Each batch is a commit; full pytest is green before the
next one starts. Guide edits ship in the same commit as the code they bind —
the guides are test-coupled (`test_the_l3_glossary_matches_the_domain_experts_guide`,
`test_the_retired_guide_clauses_are_gone`), so there is no "docs later."**

---

## 1. Why — eight findings, all verified against the shipped page

Measured on `runs/webapp-local-20260810T051339Z` (14 nodes; page 47,403 B, of
which CSS 14,370, JS 5,197, drawers 8,376, timeline+twin 10,454, cost card
2,144) and `runs/release-cut-20260908T115314Z` (failed at `tag`).

| # | Finding | Evidence |
|---|---|---|
| F1 | **A failed run's clock never stops.** The stop-the-clock branch fires only when every node settled (`if not (running or blocked or failed) and total and settled == total`, `contrib/mission_view.py:239`), so the failed release-cut run's headline reads `stopped with a problem  -  87 h 37 m` and the number grows forever. The comment above that line describes fixing exactly this for the done case; the failure branch kept it. | `mission_view.py:236-244`; live render of the release-cut run |
| F2 | **The failure case has no card.** An approval gets `_decision_card` (`mission_server.py:1350`) — evidence verbatim, above the fold. A failure gets four hero words; the failing node (`tag`) and the engine's own error (`exit code 128 (no result emitted)`) appear nowhere above the fold. The reader scrolls to the board, finds the ✗, opens a drawer. | `render_wrap` part order (`mission_server.py:1799-1857`); live render |
| F3 | **Detail is at the bottom of the page.** Every step name in both views is `href="#step-<id>"` (`mission_server.py:1161`, `:1220`), and the drawers card is the last card before the footer — clicking a timeline bar scrolls the reader past four cards and loses their place in the plot. | `render_wrap`; `_drawers` (`:1468`) |
| F4 | **Every drawer rides in every swap.** All node drawers are server-rendered into the page and into every `/api/state` refresh, opened or not: ~600 B/node measured, so a live 120-node run re-ships ~72 KB of drawer HTML per refresh to a reader looking at one node. This is the transfer-shaped sibling of the `drawers_unshared` cost S1 fenced off. | measured: 8,376 B / 14 nodes; `tests/test_mission_bench.py::test_shared_projection_keeps_drawers_free` |
| F5 | **One number, three renderings.** `agent tasks — 10` (tile, `_stat_tiles:1265`), `agent tasks used 10 of 40` (meter card, `_meter_card:1304`), and the spend block's ceiling context sit adjacent. The third was already half-deduped (`test_the_spend_card_does_not_repeat_the_meter`), which is the precedent this batch finishes. | live render |
| F6 | **Two near-identical cost disclosures.** `_cost_card` renders `history` and `head` as two sibling closed `<details>` (`mission_server.py:1428-1432`) whose bodies list the same nodes with one column of difference. Modest — they are collapsed — but both bodies ship in every swap and the two summary rows read as two facts when they are one fact with a mode. | `_cost_card:1412-1434` |
| F7 | **Two CSS tokens are used but never defined**, found by rendering the mockup and looking (the mockups-README check working as designed): `var(--raise)` (`mission_server.py:813-814` — the rail's hover and current-run highlight silently paint nothing) and `var(--accent)` (`:830`, `:836` — the critical-path label edge and its legend swatch are invisible, so the timeline's headline feature is marked only by a bold label beside an empty swatch). Invalid at computed-value time; no test sees pixels. | `grep -n -- --accent contrib/mission_server.py`; `:root` block at `:785-795` defines neither |
| F8 | **A dead driver renders as a healthy run.** Kill the driver mid-node — on this machine a session limit does exactly this to long runs — and `state.json` still says `running`: the hero reads `running`, the clock grows forever, no card renders, over a run where nothing is spending and nothing will ever finish. The page's own pinned rule ("nothing on screen is allowed to be ambiguous between fine and broken", DE guide) forbids this. `lockstep active`/`status` already know the answer — `inspect_lock` (`state.py:679`), extracted read-only precisely so callers like this could ask — and the page never asks. This is F1's *more common* sibling: the "I open the page the morning after" story. | grep: no lock/pid read anywhere in `mission_view.py`; `cli.py:686` (the STALE tag); DE guide :139 |

F1/F2/F8 are honesty defects and go first. F3/F4 are the interaction and
scale defects and are the largest work. F5/F6 are dilution.

### 1b. The adversarial UX pass (2026-09-11) — seven findings, all adopted

Reviewed against the code and the DE guide (which binds what the page may
say). Kept as a map so each finding's landing spot is auditable:

| # | Finding (one line) | Landed in |
|---|---|---|
| A1 | The stale-driver morning is the missing story; Batch 0 fixed the rarer of the two lying clocks | F8; D6; §4.1, §4.3 |
| A2 | The status-blind threshold collapses the loud minority, contradicting the board's own pinned collapse rule | D7; §6.2 |
| A3 | The blocker card's primary reader cannot act on it (unglossed machinery words, no next step, "attempt" is not DE vocabulary, "stopped" header over visibly moving bars) | §4.2 rewritten |
| A4 | `selecting()` is `wrap.contains(...)` and the panel is outside `.wrap` — the promised guard doesn't cover it; panel scroll/1 Hz churn unaddressed | §6.1 |
| A5 | The over-threshold no-JS reader is pointed at a door (the panel) that needs JavaScript | §6.2 |
| A6 | Batch 1 introduces DE-facing vocabulary and moves a guide-level promise, but listed no guide edits and left the consent sentence's home undecided | D5; §5.3 |
| A7 | Stalled-behind counts double-count shared dependents when two nodes fail | D8; §4.2 |

## 2. Decisions taken here, so they are not relitigated mid-build

**D1 — threshold laziness over panel-only (Batch 2).** Two candidate shapes
for F4: keep the inline drawer stack forever and let the peek panel be pure
UX, or cap the inline stack and serve detail on demand. **Threshold wins on
scalability**: panel-only leaves page weight linear in node count in every
refresh tick, and every roadmap pressure (flow composition, map fan-outs, the
downstream machine that filed S1) adds nodes. Below the threshold — every
domain-expert-scale run — nothing degrades at all. Above it, the no-JS reader
gets a named absence instead of inline bodies, on runs that were already
unreadable at that size without the collapse rules. Panel-only is recorded as
the rejected alternative and the fallback if review finds the two-path
`_drawers` unjustifiable.

**D2 — the board/timeline merge is CUT.** The scoping conversation proposed
folding the board into the timeline as "six renderings of one list." Wrong on
inspection: the board is `mission_rows`' *collapsed* projection — loud-first
ordering, the synthesized `N finished` counter, the three-waiting cap — and
that collapse is a promise the DE guide makes in words ("Finished steps
collapse into a count so the board stays short") and ~10 tests pin
(`test_l0_row_set_matches_mission_rows`,
`test_finished_work_collapses_but_the_loud_minority_never_does`, …). The
timeline is chronological-and-complete by design. Two projections answering
two questions is not duplication. Revisiting this means a proposal of its
own, not a batch.

**D3 — blocker logic lives in `mission_view`, rendering in the page.** Same
one-renderer rule as the agent block: the pane and the TUI can adopt the same
words later without a second derivation. Wiring them is *not* in this work
order.

**D4 — no new route.** `/api/node/<id>` exists, is pinned in the enumerated
route table, and already returns drawer lines + raw record + token
(`mission_server.py:1969-1977`). It grows one field. The route table does not
change.

**D5 — the consent sentence lives in the tile foot.** It glosses the
denominator ("of 40"), so it sits adjacent to the number it explains — and
the spend card is already pinned NOT to repeat meter facts
(`test_the_spend_card_does_not_repeat_the_meter`), which settles the "or the
spend card" branch the first draft left open. Decided here so Batch 1 does
not relitigate it (A6).

**D6 — stale detection asks the engine's one decider.** `mission_view` gains
`driver_presence(run_dir)`, a thin call to `lockstep.state.inspect_lock`
(`state.py:679` — read-only, free, extracted from `acquire_lock` precisely so
`status`/`wait`/`active` and callers like this could ask without taking the
lock). A second pid-liveness implementation in contrib would be the S1(a)
sin with a process table instead of a formatter. contrib already imports from
the package (`pistream`'s cost parsers), so this is not a new kind of edge.
The import is guarded like `cost_report`'s: a page copied without the package
renders a named absence ("driver liveness unknown from this copy"), never a
silently healthy board. Only `dead` — and `none` while nodes record
`running`, mirroring `active`'s STALE arm (`cli.py:686`) — renders the stale
story; `foreign` and `unknown` claim nothing, because from here nothing is
known (fleet rendering is §8 Q4). Pane/TUI wiring stays out of scope, same
seam rule as D3.

**D7 — the drawer threshold is status-aware.** Over `DRAWER_INLINE_MAX`,
only **settled** drawers degrade to the pointer sentence; failed, blocked,
and running drawers keep full inline bodies at any run size. This is not a
new rule — it is the board's own pinned collapse rule ("anything running,
anything that needs you, anything that went wrong … is always shown in full —
the quiet steps are the ones that get folded away", DE guide :67,
`test_finished_work_collapses_but_the_loud_minority_never_does`) applied to
L2. The loud minority is small by definition, so the byte win survives
intact; and a live run crossing the threshold mid-read (map fan-outs
materialize items at runtime) becomes a non-event for exactly the drawers a
reader has open. The 120-node reader's question is "which one broke" — the
one drawer a status-blind threshold would have emptied.

**D8 — stalled-behind is per-entry, never summed.** Each failed node's card
entry says "N steps are waiting behind this one"; two entries' counts may
overlap when failures share dependents, and no total is ever rendered — so
nothing invites the reader to add them. A failed descendant is excluded from
every count: it has its own entry, and appearing in both places would count
one problem twice.

## 3. What binds every change (the page's constitution — none of it is new)

- **GET only, reads only.** `test_no_route_writes_anything` drives every
  route against a write-raising harness; new response fields ride inside it.
- **The client renders no word, formats no time, computes no geometry.**
  `test_no_step_word_and_no_time_string_is_rendered_by_client_code`,
  `test_the_client_only_swaps_server_rendered_html`. Anything the panel shows
  is server-rendered HTML injected verbatim.
- **One renderer per fact across surfaces.** Panel and drawer both come from
  `node_drawer`/`node_agent_lines`; a fourth formatter is the S1(a) failure.
- **Vocabulary is pinned.** `mission_view.GLOSSARY` ↔ `cockpit.ps1`,
  `L3_GLOSSARY` ↔ the DE guide. New sentences enter through the guide in the
  same commit.
- **Colour is data and state; chrome is ink and surface.** No categorical hue
  as chrome (`test_no_categorical_hue_is_used_as_chrome`); status hues are the
  four reserved steps. The blocker card takes `--critical` — a status step,
  not a categorical. New chrome-only CSS does not re-run the palette
  validator; any change to a DATA colour or the surface does.
- **Absence is named, never blank.** The over-threshold drawer body, the
  no-baseline case, everything: a sentence, server-worded.
- **Costs are measured in bytes with `contrib/mission_bench.py`**, before and
  after. No wall-clock claims (AV-confounded on this machine).
- **Caches never change output.** Any new memo ships with the
  eviction-changes-timing-only test (S1 condition b).
- **No frozen surface is touched.** Contrib + tests + guides only; stdlib
  only; `pydantic` stays the only runtime dependency (and contrib does not
  even use that).

## 4. Batch 0 — truth first: the clocks, the blocker card, the dead driver

### 4.1 The clocks (F1, F8's clock half)

`contrib/mission_view.py:239` becomes:

```python
if total and not running and not blocked and (failed or settled == total):
```

so a failed run's elapsed freezes at its last `ended_at`, exactly as a done
run's does. **Known limitation, recorded in the new test's docstring, not
fixed here:** a failed run resumed days later snaps back to
wall-clock-since-start (`began` is segment-spanning). Cross-segment elapsed is
a separate question; freezing while failed is strictly more honest than today
either way.

`headline` also grows two optional keyword args, both defaulting to today's
behaviour so the pane and TUI are untouched (D3's seam rule):
`presence` (D6's dict, or `None` = don't know) and `last_event_at` (the
journal's newest timestamp — the page already holds the journal from
`render_wrap`'s single read, S1.2). When presence says stale, the run counts
as not-running for the stop-the-clock branch and the clock freezes at
`last_event_at` — a stale run's running nodes have no `ended_at`, so the
journal's last line is the only honest "when it stopped."

Tests: `test_a_failed_runs_clock_stops` and `test_a_stale_runs_clock_stops`
in `tests/test_cockpit_blockers.py`, beside
`test_a_finished_runs_clock_stops` (:427) and
`test_a_live_runs_clock_keeps_running` (:445), same fixture pattern.

### 4.1b The undefined tokens (F7)

Define both in `:root`: `--accent` as ink (`var(--ink)` or a near-white step —
the critical path is chrome, and chrome is ink; giving it a hue now would be a
new colour meaning, which is Batch 0 scope creep), `--raise` as a surface step
a hair above `--surface` (the mockup uses `#191b1e`). Add the mechanical test
that closes the class: every `var(--x)` referenced in `CSS` is defined in it
(a regex over the stylesheet — pixels stay untested, definitions don't).

### 4.2 The blocker card (F2) — the blocker-path half of S2

New in `contrib/mission_view.py`:

- `stalled_behind(flow, state) -> dict[str, int]` — for each failed node, the
  count of not-yet-settled transitive dependents, from the run's own
  `flow.tg.json` copy (the same dep walk `topo_layers` does). Per-entry
  semantics per D8: failed descendants excluded, overlap allowed, no total.
  `{}` without a flow copy — mirroring `steps_to_decision`'s refusal to guess.
- `blocker_summary(run_dir, state, flow) -> list[dict] | None` — one entry
  per failed node: human label, `rec["error"]` **verbatim**, attempt count,
  stalled-behind count, and whether anything else is still running.
  Mechanical fields only. `None` when nothing failed.

**Build-time verification, one test's worth (A3):** confirm which statuses
are terminal-failed versus rework-transient before wording — a node inside a
heal round must never render the card (`test_a_healing_node_shows_no_blocker_card`).
The DE guide already separates "sent back for rework" from "stopped with a
problem"; the card must not collapse that distinction.

New in `contrib/mission_server.py`: `_blocker_card`, rendered in `render_wrap`
directly after `_decision_card`'s slot. Precedence when both exist: decision
first (a waiting human outranks a stalled branch — and the decision card's
own instruction, resume from a terminal, is also the stale card's remedy),
blocker after, all render.

**The card is written for its reader (A3).** The DE guide's entry for this
state is "something went wrong; **ask the assistant**" (:82), and its rule
for machinery vocabulary is that it appears glossed, "each with one line
saying what it means" (:130). So, walking the shape:

```
✗ a step stopped — tag
  the machine's own words: exit code 128 (no result emitted)
  tried once · 3 steps are waiting behind this one
  Ask the assistant — nothing else can move this forward.
```

- **Header is liveness-aware.** "stopped with a problem" only when nothing
  is running; when siblings still run — `headline`'s `elif failed:` already
  outranks `running` (`mission_view.py:221`), so bars visibly move below a
  "stopped" hero today — the card (and ideally the hero, same commit) says
  a step failed while other work continues. Exact words enter through the
  guide.
- **The error is verbatim AND named as machinery.** `rec["error"]` untouched
  (S2's evidence rule), introduced as the machine's own words — the
  raw-record pattern, not a bare quote a DE must decode.
- **Counts in DE vocabulary.** "tried once", not "1 attempt" — "attempt" is
  not in the glossary. Stalled-behind per D8: "N steps are waiting behind
  this one" — a dependency fact, never a schedule estimate.
- **The last line is the guide's own next step.** A card that names a
  problem without naming whose move it is fails the same test the decision
  card passes — the decision card works because it asks for something its
  reader can do.

The node name links to `#step-<id>` (upgraded to the panel by Batch 2 for
free). Error text through `e()` like everything model-authored — the
injection test covers it if it uses the same helpers.

S2 honesty rules, adopted as written in `upstream-response-mission-scale.md`:
the downstream count is a **dependency fact**, never worded as a schedule
estimate; operational conditions acquire no invented severity — the severity
half of S2 remains the existing `ledger_summary` line, which is already
"built-ins + ledger only."

Guide: one paragraph in `COCKPIT-FOR-DOMAIN-EXPERTS.md` "The screen," same
commit — and it answers "what do I do," not just "what it means."

Tests (~12): `stalled_behind` on a chain, a diamond, no flow copy, two failed
nodes (overlapping counts, no sum anywhere in the render); card carries the
error verbatim; card absent on a healthy run; card absent during a heal
round; the failed-while-running header; decision-before-blocker ordering;
the card sits above the fold on a failed run (mirror
`test_the_offline_note_is_above_the_fold`).

### 4.3 The dead driver (F8) — the other half of "stopped"

The reader story Batch 0 exists for is "I open the page the morning after,"
and on this machine the morning-after run is more often stale than failed
(session limits kill long drivers; recorded machine quirk). Mechanism per
D6: `driver_presence` via `inspect_lock`, guarded import, `dead`/`none`+
`running` only.

- **Hero:** a new headline word — "stopped unexpectedly" (final words via
  the guide table, which gains one row, same commit; this is headline
  vocabulary, NOT `mission_view.GLOSSARY`, which stays six entries — the
  cross-surface pin is untouched).
- **Card:** the stale story renders in `_blocker_card`'s slot with the same
  anatomy as 4.2 — the lock's own line verbatim as evidence
  (`LockInfo.describe()`: "recorded pid 1234 … NOT alive"), a DE sentence
  ("the tool driving this run is gone; nothing is spending"), and the next
  step ("ask the assistant to restart it" — and `resume` IS the remedy, so
  the sentence is true).
- **Clock:** frozen per §4.1.
- **Named absence:** when the package is not importable, one server-worded
  line says liveness cannot be checked from this copy — never a healthy
  render by omission (the same rule as the missing cost_report).

Tests (~6): stale render (lock file with a dead pid — spawn-and-reap a
child for a real dead pid, no guessing), hero word, card evidence line
verbatim, clock frozen at last event, live run renders nothing new,
missing-package absence is named. Plus the guide-pin tests the new row
touches.

**Size: M (~250 LOC + tests; was S before A1/A3 adoption).**

## 5. Batch 1 — one number per fact (F5, F6)

### 5.1 The meter merges into the "agent tasks" tile (F5)

The tile's value becomes `10 of 40` with the `.track` bar inside the tile;
`_meter_card` is deleted. Four pinned behaviours move, not vanish — their
tests are rewritten to target the tile:

- no declared cap → count only, no denominator, no bar
  (`test_the_meter_shows_no_denominator_without_a_declared_cap`)
- cross-segment degradation to "of at least N"
  (`test_the_meter_degrades_to_of_at_least_across_segments`)
- no severity ramp on the fill (`test_the_meter_has_no_severity_ramp`)
- the consent sentence ("the number this flow declared — the one you agreed
  to before anything started") moves to the tile foot (D5 — decided, not
  deferred); it is a guide-level promise and may not vanish.

Check `test_the_fourth_stat_tile_is_chosen_mechanically` and
`test_a_large_standalone_number_is_not_tabular` still hold against the new
tile markup.

### 5.2 One cost disclosure (F6)

`_cost_card`'s two sibling `<details>` become one `<details id="cost-tree">`
with a `viewswitch` inside (`every attempt` ↔ `kept only`), the l0/l1 pattern:
both bodies server-rendered, JS hides one, JS-off shows both stacked inside
the single disclosure — the established honest fallback.
`mission_view.cost_lines` is untouched (the TUI's `c` panel shares it); the
JS `show()` machinery generalises to N switch groups (still zero words
rendered client-side).

**`test_nothing_the_old_page_showed_is_gone` will trip in this batch — that
is its job.** Nothing is removed (both modes stay reachable; every meter fact
survives in the tile), so the assertion update carries a docstring saying
exactly that.

### 5.3 Guide edits — in this batch's commit, not "later" (A6)

The first draft listed none, while introducing two DE-facing mode words and
moving a guide-level promise. Enumerated now:

- `COCKPIT-FOR-DOMAIN-EXPERTS.md`, the spend paragraph (:85): the two cost
  views get their words — **every attempt** (every try, including work that
  was replaced) and **kept only** (what the run kept) — final wording at
  build time, but the words on the switch and the words in the guide are the
  same words, entered in the same commit. If they land in `L3_GLOSSARY`,
  `test_the_l3_glossary_matches_the_domain_experts_guide` extends.
- The consent sentence's move to the tile foot: the guide sentence "That is
  the number you agreed to before anything started" (:86) must still be
  true of what the reader sees, and `test_the_retired_guide_clauses_are_gone`
  polices any clause the meter card's deletion orphans.

**Size: M-small (net-negative LOC; ~8 tests rewritten, ~4 new, plus the
guide pins).**

**Explicit non-goals:** the board/timeline merge (D2); converting the
spend/ACTIVITY `<pre>` cards to structured HTML (no information defect);
touching `mission_rows`/`step_rows` (shared with the pane and the TUI).

## 6. Batch 2 — the peek panel and the drawer threshold (F3, F4; closes S1.5)

### 6.1 The panel

- `/api/node/<id>` grows an `html` field: the server-rendered panel fragment —
  the same drawer `<pre>` body plus the glossed raw-record table. Client
  injects it verbatim (constitution §3).
- The panel is a non-modal `<aside>` **outside `.wrap`** — `refresh()` swaps
  `.wrap`'s innerHTML, and a panel inside it dies on every poll. Slide-over
  from the right; `role="complementary"`; close on Esc and a close button;
  focus moves in on open, returns to the invoking link on close.
- While open on a live run, the panel re-fetches its one node after each
  successful `refresh()`. **The guard is not free (A4):** `selecting()` is
  `wrap.contains(s.anchorNode)` (`mission_server.py:1057-1060`) and the panel
  is outside `.wrap` by construction — as shipped it would never protect a
  panel selection. It extends to "in `.wrap` OR in the panel," with a test.
- **The panel gets the same reader-restoration discipline `refresh()` already
  gives `.wrap`** (open ids, focus — `:1080-1089`), because drawer `<pre>`
  bodies are long and an innerHTML swap resets a reader to the top at 1 Hz:
  (1) **swap only when changed** — compare the fetched fragment to what is
  displayed and skip identical injections, which is also the byte-churn fix;
  (2) preserve scroll offset and focus-within across a real swap; (3) the
  extended `selecting()` guard above. Each pinned by a test.
- **No-JS stays whole:** name links keep `href="#step-<id>"`; JS intercepts
  with `preventDefault`. `test_a_row_in_either_view_opens_its_drawer_without_javascript`
  passes untouched.

### 6.2 The threshold (D1, as amended by D7)

`DRAWER_INLINE_MAX` in `_drawers`: at or below it, full inline bodies exactly
as today. Above it, **only settled drawers degrade** (D7) — failed, blocked,
and running drawers keep full inline bodies at any run size, the board's
loud-first collapse rule applied to L2. A degraded `<details>` renders its
summary line plus a server-worded sentence naming where the detail lives —
a named absence, never a blank.

**The sentence is worded for the weakest reader who will see it (A5).** Both
reader kinds do see it: the JS interception hooks the step-*name* links, so
a JS reader opening the drawer stack inline reads the sentence too — and for
a no-JS reader the panel does not exist, so a sentence naming only the panel
points at a door that needs JavaScript. It therefore names both paths with
their honest conditions — the panel via the step's name (works with
JavaScript), and the assistant (works always; the DE guide's universal next
step). It does not name a CLI command: `lockstep status` is not in the DE
guide's vocabulary, and the guide binds what the page may say. Final words
enter through the guide, same commit.

The number comes from `mission_bench.py` on the synthetic
retained-history fixture, not intuition (the ROADMAP lesson: the centre
intuition would have optimized was already free) — and it now counts
**settled** drawers, since loud ones never degrade. Print is unaffected
either way — closed `<details>` bodies do not print.

### 6.3 The S1 conditions, adopted as acceptance criteria

- **(a)** The panel fragment is `node_drawer`/`node_agent_lines` output —
  never a fourth formatter. The glossary tests extend to the fragment.
- **(b)** If a memo lands so the open panel's re-fetch is warm (e.g.
  `_collect` keyed on the journal fingerprint), it ships with the
  eviction-changes-timing-only test.
- **(c)** Torn-line tolerance is untouched — this batch does not modify
  `mission_cursor` or `_events_after`.
- The `drawers_unshared` guard rail
  (`test_shared_projection_keeps_drawers_free`) keeps passing: the per-click
  fetch is one node, and it must not quietly become N.
- **The loud minority never degrades** (D7):
  `test_a_failed_nodes_drawer_never_degrades` — a failed/blocked/running
  drawer keeps its full inline body over any threshold; a run crossing the
  threshold live changes no loud drawer.

### 6.4 File-level changes

| File | Change |
|---|---|
| `contrib/mission_server.py` | `html` on `/api/node`; the `<aside>` in `render_page`; ~40 lines of chrome-only CSS; ~60 lines of JS (intercept, fetch, inject-only-when-changed, scroll/focus restore, Esc/focus, extended `selecting()`, re-fetch-on-refresh) — the largest client change this page has had, and where the review effort goes; status-aware `DRAWER_INLINE_MAX` in `_drawers` |
| `contrib/mission_bench.py` | new centre: one `/api/node` request, cold and warm; drawers centres re-described for the threshold (settled drawers only) |
| `tests/test_trace_page.py` | ~15 new: panel fragment is server-rendered; panel survives a refresh; identical fragment is not re-injected; `selecting()` covers the panel; no stdout bodies in the fragment (extend `:959`); the fragment names its step in L0's words (extend `:941`); threshold boundary at N and N+1 over settled drawers; a loud drawer never degrades; the over-threshold sentence is present, named, and true without JavaScript |
| `tests/test_mission_bench.py` | extend the guard-rail pair; the eviction test if the memo lands |
| `docs/guides/COCKPIT-FOR-DOMAIN-EXPERTS.md`, `COCKPIT-THEORY-OF-OPERATIONS.md` | the panel, and the threshold behaviour, same commit |

**Size: L.** Everything server-side is assembly of existing parts; the JS
discipline is the risk.

## 7. Measurement and the screenshot check

- `contrib/mission_bench.py` before Batch 0 and after each batch, bytes per
  centre, on both the synthetic fixture and this machine's real `runs/`.
  Batch 2's acceptance number: `render_wrap` bytes on the over-threshold
  fixture drop by the drawer share; the sub-threshold render is byte-identical.
- **Render it and look at it** after each batch — the mockups README records
  five defects the first screenshot caught that no test could see, and the
  headless-Chrome recipe (with the stale-PNG warning) lives there. New pins
  come *from* looking. Batch 0's look includes all three stopped states side
  by side — failed, dead-driver, refused — because the whole point of F8 is
  that they must not be confusable, and confusability is exactly what a
  screenshot sees and a test does not.

## 8. Open questions

1. `DRAWER_INLINE_MAX`'s value — decided by bench numbers at Batch 2, not
   now, and counted over settled drawers (D7). Candidate: the size where
   settled-drawer HTML crosses ~25% of the swap.
2. Should the pane (`cockpit.ps1 -Role mission`) and the TUI adopt
   `blocker_summary` and `driver_presence` in the same release? D3/D6 say
   the seams are ready; wiring is deliberately out of scope here.
3. Cross-segment elapsed (the §4.1 limitation) — worth its own note if a
   resumed-after-parking run confuses a reader in practice.
4. Foreign locks (a fleet worktree's run viewed from here): D6 deliberately
   claims nothing — liveness is genuinely unknown across hosts/trees. If the
   fleet cockpit grows a page story, the honest rendering ("driven from
   another tree; liveness unknown from here") is its problem to word, with
   FLEET-OPERATIONS.
5. The rail refreshes per page load only (Batch 0 review, P3): it lives
   outside `.wrap`, and `/api/state` swaps only `.wrap` — so on a page left
   open, the board can flip to "stopped unexpectedly" while the rail row for
   the same run keeps its load-time word until a manual reload. Pre-existing
   page design (the rail has always been load-time-static); fixing it means
   swapping the nav in the poll, which is its own change with its own byte
   cost. Worth doing if a reader is observed confused by the split.
