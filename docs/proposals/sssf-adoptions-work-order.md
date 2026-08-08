---
type: plan
title: "Work order: SSSF adoptions — write-scope quarantine and the trace page"
description: Build-ready implementation plan for PROPOSAL-sssf-adoptions.md. Five batches — engine lock correctness and index-safe restore (0), quarantine and touched-path evidence (1), the mission_view accessors the page needs (2), the trace page itself (3), and the doc edits that bind it (4). Written to be executed in a fresh session with no prior context.
resource: docs/proposals/sssf-adoptions-work-order.md
status: stable
---
# Work order: SSSF adoptions

**ADOPTED AND BUILT, 2026-08-08.** Batches 0–4 shipped in order, each green:
`engine: the write-scope check runs inside the lock, and restore is index-safe`,
`engine: quarantine out-of-scope writes, and record what a node touched`,
`cockpit: the accessors the trace page needs`, and
`cockpit: the trace page, and the docs that bind it` (Batch 4 folded into
Batch 3's commit, as §7 requires). What the build changed from this document:

- **§9 was answered (i)** — shell nodes acquire `tree`. Measured first: worst
  case is exactly linear (4 × 1.0s shell nodes in one wave, 1.17s → 4.17s), but
  only 2 of 25 shipped flows have a wave with parallel shell work at all, and on
  the one that matters — `status-digest` wave 0 — it is 53.69s → 53.84s, +0.3%.
  `verify`'s `write-scope-unenforced` now fires only for readonly nodes.
- **Two engine bugs, neither in this document**, both found by running a
  deliberate violation end to end rather than by a test: the run directory was
  itself being quarantined where `runs/` is not gitignored (the engine moved its
  own `stdout.log` aside and rolled `state.json` back mid-run — excluded from the
  scope check and from heal rollback), and `touched-<attempt>.txt` was written
  for a *failed* spawn.
- **§9's measurement could not be run as written** — there is no
  `tests/fixtures/replay` on this machine, so the replay-based comparison was
  replaced by a static wave analysis over all 25 shipped flows plus a timed
  engine run of the one wave that has parallel shell work.

Nothing in §8 was started.

---

**Execute this in order. Each batch is a commit; full pytest is green before the
next one starts.**

Design rationale, the evidence for every claim, and the record of four
adversarial review rounds live in
[`PROPOSAL-sssf-adoptions.md`](PROPOSAL-sssf-adoptions.md). This document is the
build instruction and does not repeat the argument. Where the two disagree, the
proposal is the reasoning and this is the sequence — fix both.

---

## 0. Read this first

**What is being built.** Two things, unrelated except that both came from
reading `disler/super-simple-software-factory`:

- **A — engine.** Fix a live lock bug, make `Workspace.restore` index-safe, then
  quarantine out-of-scope writes instead of leaving them on disk, and record
  what a node actually touched.
- **B — cockpit.** Replace the meta-refresh MISSION page with a polled trace
  page: a board that opens into a waterfall, a step drawer, and a raw record.

**What is NOT being built, and must not be started.** Two features were designed
and then rejected in review. Do not revive either without reading why:

- The **protected-path floor** (a repo-wide deny-list). Deferred — needs a
  `format_version` bump, exempts map nodes, and cannot see gitignored paths
  including `runs/`. Proposal §5.
- **Agent profiles** (named identities in `lockstep.toml`). Rejected — it moves
  `spec.writes` out from under `flow_hash`, which is the only integrity
  mechanism currently covering write scope. Proposal §6.

**Do not re-derive these.** They were established by review against the code and
each cost a round to find:

| Fact | Where |
|---|---|
| The scope check runs **after** `_release(locks)` | `roles.py:632-635` |
| Shell nodes take **no** token — `exclusive=[]` | `shell.py:101` |
| Every non-readonly harness/fake node takes `tree` regardless of `--max-workers` | `harness.py:260`, `fake.py:97` |
| `restore()` writes the **real index**; `snapshot()` does not | `workspace.py:173` vs `:137-142` |
| `git cat-file blob` → worktree corrupts eol/smudge/LFS content and drops modes | verified empirically; use a temp-index checkout |
| Map nodes never take a scope baseline | `roles.py:1074-1080` |
| `mission_rows` synthesizes and reorders — it does not filter | `mission_view.py:308-346` |
| `GLOSSARY` has six entries incl. `skipped → "not needed"`; "sent back for rework" is **not** one — `node_word` synthesizes it with a counter | `mission_view.py:36-43`, `:262-280` |
| `COST_ICON` already maps all six statuses to glyphs | `mission_view.py:572-579` |
| The spend denominator is `cost_report._budget_cap` (the run's flow copy), **never** the cockpit journal, which is agent-authored | `cost_report.py:629-631` |
| `verify_trace` returns non-empty `head` on a tamper, and `(True, "", …)` on a healthy empty journal | `state.py:247-262` |

---

## 1. Ground rules

1. **TDD per SPEC §14.** Write the failing test, then the code.
2. **`.venv\Scripts\python.exe -m pytest` after every change.** Not at the end of
   a batch — after every change.
3. **`pydantic` stays the only runtime dependency.** Batch 3 ships stdlib
   `http.server` and one hand-written HTML file. No framework, no build step, no
   CDN fetch.
4. **No `format_version` change.** If you find yourself needing one, you have
   drifted into the deferred floor (§0) — stop.
5. **Frozen surfaces:** exit codes, `format_version` 1.x, the §7 fencing/footer
   contract, hash composition. This work order moves none of them. It does
   supersede one `DEVIATIONS.md` entry and deliberately reverse one pinned test
   — both in Batch 1, both explicit.
6. **This machine's AV** causes transient `PermissionError` on file replaces and
   git object writes. Retry once before investigating.

---

## 2. Traps

Every one of these was a defect in a draft of the proposal, caught by review.
If you find yourself doing one, stop and re-read the cited section.

| Trap | Why it is wrong |
|---|---|
| Moving only the *check* inside the lock, leaving `restore` outside | The mutation is the dangerous half: it reverts a concurrent node's live file while that node goes on to record `done`. Both go inside. §3 |
| `git cat-file blob <tree>:<path> > file` to restore without touching the index | Silently corrupts eol/smudge/LFS content and drops file mode and symlinks — and `git status` reports nothing, so the run says "restored" for a file it destroyed. Use `GIT_INDEX_FILE=<temp> git checkout`. §3 |
| A fixed `out-of-scope/` dir or `touched.txt` name | `phase_dir` survives resume and heal rounds; `shutil.move` overwrites silently. Attempt-scope every artifact, as heal already does. §4 |
| Recording the touched-path list on `PhaseRecord` | `FileStore.record` rewrites all of `state.json` on every call. Write the list to a file, record a count and a path. §4 |
| Changing `verify_trace`'s return arity | `cli.py:421` unpacks four. Add a new richer function; leave the tuple alone. §5 |
| Rendering `ok` from `verify_trace` as a green tick | A tamper returns `ok=False` **with a non-empty head**; a healthy empty journal returns `ok=True` with an empty one. Four-way rule, §6.3 |
| Letting the page's JS produce any word or time string | It becomes a glossary pytest cannot execute. Words and formatted times come from `mission_view`. §5, §6 |
| Grouping waterfall rows by `load_tiers` | It returns *approval* tiers keyed by approval node id, and `cockpit.ps1:302-308` records the decision not to read tiers on the board. §6 |
| A severity ramp on the spend meter | No artifact defines "80% is a warning". `mission_view` is summary-free by construction. §6 |
| Drawing one span per node from `state.json` | `started_at`/`ended_at` are first-start and last-end across every attempt and resume. Use per-interval events. §5, §6 |
| Adding a run picker, an archive button, or any non-GET route | The guarantee is that the browser cannot change the run. §6 |
| Putting `stdout.log` content on the page | Reverses a decision recorded at `mission_view.py:405-409`. §6 |

---

## 3. Batch 0 — engine correctness

Ships alone and is worth merging even if everything after it is rejected. No
behaviour depends on Batch 1.

### T0.1 — `Workspace.restore` becomes index-safe

`src/lockstep/workspace.py`, `GitWorkspace.restore`.

The checkout at `:173` writes the caller's real index. Run it against a
throwaway index instead, the way `snapshot()` already does at `:137-142`:

```python
with tempfile.TemporaryDirectory() as td:
    env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
    ...
    self._git("checkout", ref.ref, "--", rel, env=env)
```

`_git` already takes `env`. The move-aside branch for created paths is
unchanged. This fixes the existing heal-rollback path first — that is where the
tests go.

**Tests** (`tests/test_workspace.py` or the heal test module):
- a staged-but-not-written hunk survives a heal rollback (stage content A, edit
  worktree to B, snapshot, agent writes C, rollback → worktree B, index A);
- a file under `.gitattributes` `* text=auto eol=crlf` is restored byte-for-byte;
- an exec-bit file keeps mode `100755` after restore (`pytest.mark.skipif` on
  Windows — `core.filemode` is false here);
- a symlink is restored as a symlink (same POSIX gate).

### T0.2 — the scope check moves inside the lock

`src/lockstep/roles.py`, `_run_node`, currently `:617-647`.

Today: `try: … execute … finally: _release(locks)` and *then* the scope check.
Move the whole violation sequence — `_scope_violations`, and in Batch 1 the
patch/restore/record — inside the `try`, before the `finally` fires. Failure
handling (`_set_status(... "failed")` and return) may stay outside; only the
**tree-reading and tree-writing** steps must be under the token.

**Test:** two nodes in one wave, both non-readonly harness (so both take
`tree`); the first declares `writes: ["src"]` and writes only in scope, the
second writes `docs/x.md`. The first must not be accused. This fails today.

### T0.3 — token-less writers must serialize *(needs the operator decision, §9)*

`_scope_violations` compares against a whole-tree baseline, so a writer that
holds no token invalidates the comparison. `shell.py:101` sets `exclusive=[]`.

Implement **whichever option §9 returns**:

- **(i)** `shell.py` emits `exclusive=["tree"]`. One line. Measure the
  concurrency cost first (§9) and put the number in the commit message.
- **(ii)** The engine tracks whether any token-less node was in flight during
  this node's window; if so, report the violation and **do not** mutate in
  Batch 1. Today's behaviour as the fallback.

**Test:** a harness node with a scope, running concurrently with a shell node
that writes outside it — under (i) the shell node serializes and no
misattribution occurs; under (ii) the violation is reported and no quarantine
happens.

### Batch 0 acceptance

- Full pytest green.
- `lockstep verify flows/starter/*.tg.json` unchanged.
- `python contrib\replay_suite.py` green (zero tokens).

---

## 4. Batch 1 — quarantine and touched-path evidence

### T1.1 — quarantine the violation

`roles.py`, inside the `try` established by T0.2, when `violations` is non-empty:

1. `patch = self.workspace.diff_patch(scope_ref)` → write to
   `phase_dir / f"out-of-scope-{rec.attempts}.patch"` **before** any restore.
2. `self.workspace.restore(scope_ref, violations, phase_dir / f"out-of-scope-{rec.attempts}")`.
3. Reset the index entry for each violating path (T1.2).
4. Refresh the lineage-head fingerprint, exactly as the heal path does at
   `roles.py:940-944` — otherwise a crash-then-resume reads the rollback as
   external edits.
5. Fail the node with a message naming each path and its outcome
   (`restored` / `moved to out-of-scope-N/`), and **stop citing SPEC §0.1
   item 2** as the justification — that citation is a non-sequitur (proposal §1
   E1).

In-scope writes are left exactly as they are.

### T1.2 — the index rule

An index-safe restore leaves an agent's *staged* out-of-scope write in the
index, where the next commit picks it up and `snapshot()` (which reads the
worktree) cannot see it. So: reset the index entry **for violating paths only**,
and only where that entry is the agent's. A path the operator had already staged
before the node ran is **named in the failure message and left alone** — the
same rule SSSF uses at `permissions.py:138-160`, and the same reasoning as
`_roll_back`'s "was already modified" branch.

### T1.3 — the rename case

`--no-renames` (`workspace.py:150`) splits a rename into delete-old +
create-new. If a node renames an in-scope file to an out-of-scope path, the
in-scope delete is permitted and only the new path is quarantined — leaving the
file in neither location. Detect that shape (a permitted delete plus a
quarantined creation) and say so in the message; the content is recoverable from
the discard dir.

### T1.4 — partial-restore failure

If `restore` raises `WorkspaceError` part-way, fail with **both** the original
violation and the restore error, naming the paths already handled. A half
rollback that reads as a clean one is the failure mode this feature exists to
prevent.

### T1.5 — touched-path evidence

On **success**, when `scope_ref is not None`: write the full in-scope changed
path list to `phase_dir / f"touched-{rec.attempts}.txt"`, and record on
`PhaseRecord` only a count and that relative path. Do not put the list on the
record.

`PhaseRecord` sets no `model_config`, so pydantic v2 defaults to
`extra="ignore"` — old `state.json` files load fine and no migration is needed.
`export_fixture.py` is a strict allowlist (`KEEP_TOP`/`RESULTS`), so the new
file is excluded automatically; **no scrubber change is required** and no test
should assert one.

### T1.6 — the deviation and the pinned test

- **Reverse** `tests/test_write_scope.py:67`
  (`test_the_offending_file_is_not_deleted`) →
  `test_the_offending_file_is_moved_aside_not_deleted`: assert absence at the
  original path and presence under `out-of-scope-<n>/`.
- **Rewrite** the `2026-08-02 spec.writes` entry in `docs/spec/DEVIATIONS.md`
  (`:97-107`) in full. Both of its clauses change: detection now happens
  *during* the node, not after it, and violations are quarantined rather than
  left in place.

### Batch 1 tests

Out-of-scope create moved aside and named · out-of-scope modify of a committed
file restored to baseline · out-of-scope revert of an operator's uncommitted
file restored (the case SSSF cannot do) · a staged out-of-scope write does not
survive in the index · an operator's pre-existing staged path is named and left
alone · in-scope writes untouched · patch written before restore · attempt 2
overwrites neither attempt 1's discard dir, patch, nor `touched-1.txt` ·
partial restore names both errors · lineage-head refreshed so resume reports no
external edits · in-scope→out-of-scope rename reported.

---

## 5. Batch 2 — the accessors the page needs

Pure additions to `contrib/` and one to `src/lockstep/state.py`. No page yet.
This batch exists so that Batch 3's JavaScript can be layout-only.

### T2.1 — a richer trace-status function

`src/lockstep/state.py`. **Do not change `verify_trace`'s 4-tuple** — `cli.py:421`
unpacks it. Add `trace_status(run_dir) -> dict` returning
`{ok, head, first_bad_line, detail, total, chained}`, and refactor
`verify_trace` to call it. Batch 3 needs `total`/`chained` to tell "healthy but
empty" from "unchained" from "broken".

### T2.2 — a structured row accessor

`contrib/mission_view.py`. `mission_rows` returns preformatted terminal strings
(`f"{name:<34} {word}"`, 33-char truncation, `:327-330`). Add
`step_rows(run_dir, …) -> list[dict]` yielding
`{node_id, label, word, status, icon, note, kind}` and **reimplement
`mission_rows` on top of it** so the terminal rendering stays the thing that
formats. Nothing about the terminal output may change — the existing render
tests are the guard.

`icon` comes from the existing `COST_ICON` (`:572-579`). Do not invent glyphs.

### T2.3 — per-node run intervals

`contrib/cost_report.py`. `wall_and_heals` (`:322-340`) walks
`running → terminal` pairs and sums them. Split it: add
`node_intervals(events) -> dict[str, list[tuple[str, str | None]]]` (open
interval → `None` end), and have `wall_and_heals` sum from that. The waterfall
draws one segment per interval; the table twin sums the same intervals, so the
picture and the number cannot disagree.

### T2.4 — two public formatters

`contrib/mission_view.py`. `_elapsed_str` is private and cost-panel-only
(`:590-599`). Add **`format_duration(seconds)`** and **`format_clock(iso)`** as
public functions. The axis carries absolute clock times, not durations — one
formatter is not enough, and the JS must format nothing.

### T2.5 — the question card

`contrib/mission_view.py` gains an accessor for `<run_dir>/question-card.txt`.
The DE guide promises a clarification's verbatim words appear on screen
(`COCKPIT-FOR-DOMAIN-EXPERTS.md:113-117`) and **only `cockpit.ps1:944` renders
them today** — this is a pre-existing broken promise, not new work created by
the page. Wire it into `mission_tui.py` in this batch too, so no surface carries
the banner without the words.

### T2.6 — the evidence freshness predicate

The evidence file is written by a render node that runs **before** the approval,
so comparing its mtime against the approval's `started_at` flags every fresh
file as stale. Compare against **the render node's most recent run interval**
(T2.3 gives you that): stale iff the file predates the last time the node that
writes it ran.

The "is a decision waiting" predicate is `quiescent.py`'s, never
`mission_view.needs_you` (which fires on clarify gates too).

---

## 6. Batch 3 — the trace page

`contrib/mission_server.py` and its one HTML file. Visual target:
`docs/proposals/mockups/trace-page.html` (+ `.png`) — a static mockup, already
rendered and layout-checked.

### T3.1 — routes and the cursor

Keep: loopback default, the `--host` warning, run identity resolved
mechanically per request (pinned, or newest by mtime, `:143-147`). **No run
enumeration and no picker** — that would create N MISSIONs and strip the
referent from "when two surfaces disagree, MISSION is right".

| Route | Source |
|---|---|
| `GET /api/state` | `state.json` projection + `step_rows` output + run token |
| `GET /api/events?after=<n>` | `read_events`, line-offset cursor, + run token |
| `GET /api/node/<id>` | `mission_view.node_detail`, id allowlisted from `state.json` |
| `GET /api/evidence` | evidence / rejection under T2.6's rules |
| `GET /api/question` | the question card (T2.5) |

**Every response carries a run token.** When the DE's next segment starts, the
server begins answering for a different run while the client still holds the old
cursor — the client must discard its cursor and rendered state when the token
changes. A meta-refresh page resets by construction; a poll does not.

Validate `after` as a non-negative integer. Node ids are already
`^[a-z0-9][a-z0-9-]*$` (`taskgraph.py:23`) and `node_detail` self-guards
(`mission_view.py:501-504`), so the allowlist is for a clean 404, not for safety
— keep a traversal test anyway.

### T3.2 — L0, server-rendered

The landing view renders **server-side, with JavaScript disabled**: headline,
stat row, the collapsed step list from `mission_rows`, spend meter, both cost
blocks, ACTIVITY, per-step `<details>`, and the evidence or question block when
one is waiting. Nothing today's page shows is removed.

A **BROKEN** chain (T2.1) appears here, not three levels down.

### T3.3 — L1 waterfall and its table twin

Opened by "show every step". It **replaces the step list**; the rest of L0 stays.

L0→L1 is a *switch*, not an expansion: `mission_rows` synthesizes rows
(`+ N more waiting`, `N finished, M not needed`), injects `mission.txt` note
lines, and iterates in recorded order. Do not claim the two agree row-for-row.
**A node with a note must carry a note marker into L1** or the switch loses
content.

Geometry, colour and the row spec are in proposal §4.6.2–§4.6.3 and demonstrated
in the mockup. The load-bearing points: one segment per interval; spans rounded
both ends; 2px surface gaps; solid hairline gridlines; **the axis and gridlines
share the track column's coordinate space, not the card's** (this was a real
defect caught by rendering); labels selective; the container sized to include
the axis band.

The **table twin is server-rendered** and is the accessibility path, the no-JS
fallback, and the test surface.

### T3.4 — L2 and L3

L2 is a step drawer from `node_detail` (names and sizes — **never** stdout
bodies). It names its step in L0's words, without L0's 33-char truncation and
without the `(step id: …)` suffix; the identifier lives at L3.

L3 carries node id, hash parts, `invalidated_by`, chain head, `verify-trace`
detail — **each with a one-line plain-language gloss**, pinned by test as
`GLOSSARY` is (`tests/test_mission_render.py:419`).

### T3.5 — the keyboard, and what it must say

`a` and `r` are the keys the DE was taught. On this page they must render the
same sentence the banner carries — the decision happens at the terminal. A
silent no-op at a decision moment is the worst available behaviour.

### Batch 3 tests

Route table enumerated and pinned; no `do_*` method but `do_GET` (strengthens
`tests/test_cockpit_ux.py:248-254`) · a **write-patching purity harness** —
patch `open` (write modes), `Path.write_text`, `Path.unlink`, `Path.mkdir`,
`os.replace`, `shutil.move` to raise, then drive every route against a fixture
run dir. State in the test's docstring that this is **coverage-bounded**, not
structural: AST inspection cannot do better, and the transitive closure is where
writes live · `after=abc` / `after=-1` / traversal all 404 · cursor advances and
never replays · **run token changes across a segment boundary** · tampered
journal renders BROKEN, empty renders "nothing to verify", unchained renders
"unchained" · L0 renders with JS disabled and contains the headline, the step
words and the spend figure · L0 row set matches `mission_rows` · every waterfall
value present in the table twin, and a healed node's segments sum to its table
duration · status→colour keys are exactly `GLOSSARY`, icons exactly `COST_ICON`,
with a documented rule for `node_word`'s decorated forms and an unknown status
rendering muted · the four cost-stack hexes pinned against the validator output
· no step word and no time string rendered by client code · a note row survives
L0→L1 · meter shows no denominator when `_budget_cap` is `None`, and
`of at least N` across segments · `a`/`r` produce the terminal sentence ·
`/api/question` returns the card verbatim · evidence freshness passes for a
just-rendered file · `render_page`'s existing glossary assertion still passes.

---

## 7. Batch 4 — the docs that bind

**Ships with Batch 3, not after it.** CLAUDE.md makes
`COCKPIT-FOR-DOMAIN-EXPERTS.md` binding on what may be said to the human, so the
page and the guide cannot land apart.

- **`COCKPIT-FOR-DOMAIN-EXPERTS.md`, "What you never have to do":** strike
  *"Know what a 'run directory' is"*, *"Work out what a number means"*, and
  *"Decide whether something is safe to restart"* (L3's `invalidated_by` panel
  breaks the third). Replace with: **nothing you need in order to decide is
  behind a word you do not know; everything else is one click away, labelled in
  plain language, and never something you are required to act on.** The other
  four clauses stand. Add the page to the screen model, and the L3 glossary.
- **`COCKPIT-THEORY-OF-OPERATIONS.md`:** the trace page, its levels, and the
  restated never-rules (the page adds only GET routes; the decision does not
  move).
- **`CLAUDE.md`:** module map (`trace_status`, `step_rows`, `node_intervals`,
  the formatters) and the cockpit command block.
- **`docs/spec/DEVIATIONS.md`:** the Batch 1 rewrite, if not already committed
  there.
- **Ops note now, ahead of any floor work:** `runs/` is gitignored, so no
  git-derived mechanism can protect `<run_dir>/approval-evidence.txt` from a
  node that rewrites it. This is the sharpest finding of the review rounds and
  it has no fix in this work order.

---

## 8. Out of scope — do not start

- The protected-path floor. Proposal §5 lists its eight blocking findings.
- Agent profiles. Proposal §6.
- The corrective re-spawn's escape from the token and the scope check
  (`roles.py:788`) — real, pre-existing, named in the proposal, and not this
  work order's problem.
- Fixing `path_in_scope`'s `fnmatch` (`*` crosses `/`). Blast radius is zero
  today (every `writes` entry in the repo is one of five literal filenames), but
  once Batch 1 lands, a matcher change stops altering a message and starts
  reverting writes the previous release accepted. **If it is going to be done,
  it must go before Batch 1** — raise it rather than sliding it in.

---

## 9. The one decision needed before Batch 0 closes

**Do shell nodes acquire the `tree` token?**

Option (i) is correct and one line, but serializes shell nodes against harness
work. Option (ii) keeps concurrency and gives up quarantining whenever a
token-less node overlapped.

**Measure before choosing.** Run the largest fixture flow both ways under
`--max-workers 3` and compare wall time:

```
.venv\Scripts\lockstep.exe run <flow> --replay <recorded-run> --max-workers 3
```

Take the number to the operator with a recommendation. Put it in the commit
message either way.

---

## 10. Definition of done

Per batch:

```
.venv\Scripts\python.exe -m pytest                    # green, every time
.venv\Scripts\lockstep.exe verify flows\**\*.tg.json  # unchanged
python contrib\replay_suite.py                        # green, zero tokens
```

Whole work order:

- Batches 0–4 committed in order, each green.
- A run with a deliberate scope violation leaves `out-of-scope-1/`,
  `out-of-scope-1.patch`, a restored tree, and a failure message naming every
  path — and a resume of that run reports **no** external edits.
- `python contrib\mission_server.py` serves a page that renders with JavaScript
  disabled, opens into a waterfall, and has no route that writes.
- `docs/spec/DEVIATIONS.md` and `COCKPIT-FOR-DOMAIN-EXPERTS.md` both describe
  what the code now does.
