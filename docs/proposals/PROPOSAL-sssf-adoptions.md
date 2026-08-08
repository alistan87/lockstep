---
type: proposal
title: "Proposal: adoptions from Super Simple Software Factory — write-scope quarantine and the trace page"
description: Import what survived review from disler/super-simple-software-factory — a lock-correctness fix, an index-safe restore, and quarantine for out-of-scope writes (A), and a polled trace page replacing the meta-refresh MISSION page (B). The protected-path floor (A2) and agent profiles (C) are deferred with their findings recorded.
resource: docs/proposals/PROPOSAL-sssf-adoptions.md
status: draft
---
# Proposal: adoptions from Super Simple Software Factory

**Status: draft, rev 5, 2026-08-08.** Rev 4 changed one thing on the operator's
instruction: **the DE-tier promise is relaxed and the tier split abandoned.**
There is one page, with progressive disclosure, aimed at being a surface a
domain expert opens by choice — §4.6 is the design spec. Rev 5 is that spec
after its own adversarial round, which found nine defects in it: three retired
guide clauses rather than two, collapse not being expressible as a disclosure
level, a status map whose keys were not the glossary's, a spend denominator
sourced from a hand-authored journal, a tier grouping that reversed a recorded
decision, and a promise the guide already made that no non-PowerShell surface
keeps. No never-rule moves. Rev 1 was written after reading
`disler/super-simple-software-factory` (skill branch, MIT) end to end. It went
through two adversarial rounds — round 1: a spec audit against SPEC r3 +
r4/r5/r6 + ADDENDUM-A + DEVIATIONS, a cockpit-doctrine review of B, an engine
review of A and C; round 2: two reviews aimed only at what the rev-2 rewrite
introduced. **Twenty-five findings survived verification across both rounds**,
including three blockers in rev 2's own rewrite. §10 records what changed and
which review claims were checked and rejected. Nothing here has been
implemented.

Two results reshaped the proposal:

- **Rev 1's feature A was a data-loss bug** (the engine releases the `tree` lock
  before the write-scope check), and **rev 2's fix was incomplete** — it moved
  the *check* inside the lock but never said where the *mutation* goes, and it
  missed that shell nodes take no token at all.
- **The protected-path floor (A2) failed review twice** and is deferred to its
  own proposal (§5). It needs a `format_version` bump, exempts map nodes,
  cannot see gitignored paths — including `runs/`, where
  `approval-evidence.txt` lives — and its unlock rule fails open on the one
  flow that would use it. That is a programme, not a feature.

What remains is genuinely ready: **A0/A0b/A1/A3** (engine correctness plus
quarantine) and **B** (the trace page). Feature C does not ship (§6).

**Scope:** `src/lockstep/` (A0, A0b and A1 change behaviour on live paths; A3
and one `verify_trace` return change are additive), `contrib/`, `tests/`,
`docs/`. No `format_version` change. One `DEVIATIONS.md` entry is superseded
and one pinned test deliberately reversed; §7 accounts for both.

---

## 1. Why now

| # | Finding | Evidence |
|---|---|---|
| E0 | **The write-scope check runs with the `tree` lock released**, and **shell nodes never take the lock at all.** `_release(locks)` is in the `finally` at `roles.py:632-633`; the check at `:635` snapshots the tree afterwards. `shell.py:101` sets `exclusive=[]`, and shell nodes are dispatched to a separate 8-worker pool (`roles.py:534,556`) that `--max-workers` does not bound. A finishing node is therefore accused of a concurrent node's legitimate writes. | `roles.py:619-647`; `shell.py:101`; `workspace.py:144-151` |
| E1 | A node that writes outside `spec.writes` is detected, failed, and its files left on disk. A logged deviation, not an accident. | `roles.py:636-646`; `DEVIATIONS.md:104-107`; pinned by `tests/test_write_scope.py:67` |
| E2 | `restore()` writes the **real index**. `snapshot()` is scrupulous about it (temp `GIT_INDEX_FILE`, `workspace.py:137-142`); `restore` is not (`workspace.py:173`), and the baseline tree was built from the worktree, so index-only content was never captured. A staged-but-unwritten hunk is destroyed by any heal rollback today. | `workspace.py:166-179`; verified empirically in round 2 |
| E3 | The run has no visual surface. `mission_server.py` is a `<pre>` behind a 5-second `<meta http-equiv="refresh">`: whole-page reload, no cursor, no time axis. | `contrib/mission_server.py:38-71`; `state.py:112-113` |

**Correction to the first reading (C1).** lockstep's tree-diff detection already
catches the case SSSF built its changeset comparison for — an agent reverting an
operator's uncommitted work. The baseline is a real tree including untracked
files (`workspace.py:136-142`), so a reverted file differs between trees. The
gap was never detection.

**Correction to the first reading (C2).** SSSF cannot restore a file its agent
reverted and says so (`permissions.py:150`). Here the baseline carries the
pre-node content, so quarantine *can* restore it — via A0b, and only via A0b.

---

## 2. What binds every change

1. **`pydantic` remains the only runtime dependency.** B ships stdlib
   `http.server` and one hand-written HTML file. No build step.
2. **One store.** `events.jsonl` is hash-chained; `verify-trace` is the check
   that it was not rewritten. No second store (§8.1).
3. **The approval never moves**, and that guarantee must stay *checkable* — with
   the honest split between what is mechanised and what is coverage-bounded
   stated rather than blurred (§4.7).
4. **Frozen surfaces do not move without a `DEVIATIONS.md` entry.** §7.
5. **TDD per SPEC §14**, with one stated exception: B's browser JS cannot be
   unit-tested without a dependency, so no logic that can be wrong lives there
   (§4.4).

Rev 1 listed a sixth bind — ADDENDUM-A's "enforcement may never enable" — and
used it to carry A2's hash argument. Struck: ADDENDUM-A says of itself that it
is informative, amends nothing, and governs *pi extensions*
(`ADDENDUM-A-pi-hooks.md:68, 84-86`). It also says the opposite of what rev 1
wanted (§5).

---

## 3. Feature A — engine correctness and quarantine

### A0. The check and the mutation both go inside the lock

Today: acquire tokens → execute → `finally: release` → check scope. Rev 2 said
"compute `violations` inside the `try`". That is necessary and **not
sufficient**, because A1's `diff_patch` and `restore` are mutations and rev 2
never placed them. If they stay outside, rev 1's data-loss bug returns one step
later: `diff_patch` snapshots fresh, capturing a concurrent node's writes into
*this* node's evidence patch, and `restore` checks the baseline out over a
peer's live file.

**Rule: the entire violation sequence — detect, patch, restore, record — sits
inside the same `try` that holds the tokens.**

### A0c. Sound detection requires that every concurrent writer serializes

A0 fixes harness↔harness. It does not fix harness↔shell, because
`shell.py:101` takes no token: a shell node in the same wave writes freely while
a harness node is being measured against a whole-tree baseline. Under A1 that
becomes quarantine of a live file belonging to a *passing* node.

Two options, and the choice is a real trade:

- **(i) Shell nodes acquire `tree`.** Correct, one line, and consistent with
  SPEC §9.3's reasoning that a tree-mutating step is inherently serial. Cost:
  shell nodes stop overlapping with harness work; on flows with many small
  shell gates this is measurable and must be measured before the choice is made.
- **(ii) Gate the quarantine on serialization.** Track whether any token-less
  node was in flight during this node's window; if so, report the violation and
  **do not** mutate — today's behaviour, as the honest fallback.

**Recommendation: (i), with (ii) as the safety net for any future token-less
kind.** Detection that can misattribute must never be allowed to *act*.

Rev 2's §10 asserted the unchecked classes were "readonly harness and shell".
That was about which nodes *get checked*; it does not follow that checked nodes
are safe from unchecked ones. The `tree` token is a barrier among nodes that
voluntarily take it, not a barrier over the tree.

### A0b. Index-safe restore — one option, not two

Rev 2 offered `git cat-file blob <tree>:<path>` → worktree write, or
save/restore around `git checkout`. Round 2 tested the first and it is **wrong**:

- With `.gitattributes` `* text=auto eol=crlf`, cat-file restores LF where the
  baseline had CRLF, and `git status` reports **nothing** — the clean filter
  normalises it back to the same blob. `changed_paths()` is blind for the same
  reason, so the run reports `restored` for a file it corrupted.
- With a clean/smudge pair (the git-lfs shape), it writes the pointer text over
  the file's contents.
- Mode (`100755`) and symlinks (`120000`) are discarded: `cat-file blob` emits
  bytes and drops the tree entry's mode.

The correct fix is the idiom already in the file, thirty lines above:

```python
GIT_INDEX_FILE=<temp> git checkout <tree> -- <path>
```

It *is* checkout, so filters, eol, mode and symlinks are preserved; and it
touches no real index — exactly what `snapshot()` does at `workspace.py:137-142`.
Verified in round 2.

**One rule A0b does not by itself give A1.** If the agent *staged* its
out-of-scope write, an index-safe restore leaves the violating blob in the
index, where the next commit picks it up — and lockstep cannot see it, because
`snapshot()` reads the worktree. So A1 additionally resets the index entry **for
violating paths only**, and only where that entry is the agent's: a path the
operator had already staged before the node ran is named in the failure message
and left alone, on the same reasoning `permissions.py:138-160` uses.

*Correction to round 1:* it called `restore`'s index behaviour a SPEC §9.4
breach. §9.4 item 2's "Caller's index state is saved and restored around this"
governs **snapshot**, which the code honors. This is an unstated hazard, not a
logged breach — the fix is required regardless.

### A1. Quarantine instead of abandon

With A0, A0c and A0b in place: preserve the diff, restore the worktree, reset
the index entry, fail the node. Artifacts are **attempt-scoped**, as heal
already does (`roles.py:931-933`):

```
phases/<node>/out-of-scope-<attempt>/       # moved-aside creations
phases/<node>/out-of-scope-<attempt>.patch  # the blocked attempt, before restore
```

Rev 1 used fixed names; `shutil.move` (`workspace.py:179`) silently overwrites,
so a re-run would have destroyed attempt 1's evidence — in a feature whose whole
rationale is that a blocked attempt must leave some.

Also required:

- **Refresh the lineage-head fingerprint after restoring**, as heal does at
  `roles.py:940-944`, or a crash-then-resume misreads the rollback as external
  edits.
- **A rename that moves an in-scope file out of scope** leaves it in neither
  location: `--no-renames` (`workspace.py:150`) splits it, the in-scope delete
  is permitted, only the new path is quarantined. Recoverable from the discard
  dir; detect and say so in the message.
- **Partial-restore failure names both errors** and the paths already handled.
- Directory and deleted-path cases need nothing: `diff-tree -r --name-only`
  emits blobs only, and a delete is restored by checkout.
- **The corrective re-spawn is outside all of this.** `_validate_with_respawn`
  runs a real harness spawn after `_release` (`roles.py:788`), with no token and
  no scope check. Pre-existing; named here because a proposal about lock
  correctness should not leave it unsaid. Out of scope for this batch.

**This supersedes a logged deviation and reverses a pinned test.**
`DEVIATIONS.md:104-107` records both the leave-in-place behaviour *and* the
detection timing ("after the node finishes … only while the node holds the
`tree` token"); A0 changes the timing and A1 changes the outcome, so the whole
entry is rewritten, not just its last clause. `tests/test_write_scope.py:67`
(`test_the_offending_file_is_not_deleted`) becomes
`test_the_offending_file_is_moved_aside_not_deleted`.

*Rev-1 sentence withdrawn:* "A1 brings the code into line with §0.1 item 2." It
does not — leaving files in place also never deletes. The defect is an unsound
rationale in a message, not a nonconformance.

### A3. Touched-path evidence

On success with a baseline, write the full in-scope changed-path list to
`phases/<node>/touched-<attempt>.txt` and record only a count plus that path on
`PhaseRecord`.

**Attempt-scoped**, for the reason A1 is: `phase_dir` is never cleared and
survives resume and heal rounds (which is why `harness.execute` rotates its
artifacts, `harness.py:287-296`). Rev 2 fixed this in A1 and reintroduced it in
A3 in the same batch.

Rev 1 put up to 200 paths on the record, justified by a map-node scenario that
**cannot occur** (`spec.writes` on a map is the hard error `write-scope-on-map`,
`taskgraph.py:414-419`). The real cost is that `FileStore.record` rewrites the
entire `state.json` on every call (`store.py:25-28`), so a path list on any node
is re-serialised on every subsequent record — the wrong direction on a machine
whose documented quirk is transient `PermissionError` on file replaces. A file
plus a count is also better evidence at an approval on a 3 000-file codemod.

No compatibility problem: `PhaseRecord` sets no `model_config`, so pydantic v2
defaults to `extra="ignore"` (`state.py:105-125`). Open, and not answered here:
`RunState.schema_version` is `"1.0"` (`state.py:129`) and nothing in this repo
states when it moves.

*Rev-2 claim withdrawn:* "`export_fixture.py` needs a rule for `touched.txt`."
It does not — the scrubber is a strict allowlist (`KEEP_TOP`/`RESULTS`,
`export_fixture.py:30-31`), so the file is excluded by construction. The
proposed test asserted a tautology.

---

## 4. Feature B — the trace page

Replace the meta-refresh page with a polled one. Same process, same loopback
default, same `--host` warning, same absence of any writing route.

### B1. One run, one token, no enumeration

No `/api/runs`. The board's identity stays mechanical — pinned, or newest by
mtime, re-resolved per request as today (`mission_server.py:143-147`). A picker
would create N MISSIONs and strip the referent from "when two surfaces
disagree, MISSION is right".

**But a polled page breaks something the meta-refresh page cannot.** Every
route resolves `newest_run` independently, so when the DE's next segment starts,
the server begins answering for run B while the client's cursor still holds run
A's offset: `/api/events?after=400` against a 12-event run returns empty
forever. A full page reload resets that by construction; a poll does not.

**Fix: every response carries a run token; the client discards its cursor and
its rendered state when the token changes.** Rev 2 had no such token, and
Batch 2's "cursor advances and never replays" test passes on the bug.

| Route | Source |
|---|---|
| `GET /api/state` | `state.json` projection + the rendered word/label from `mission_view` (B4), + run token |
| `GET /api/events?after=<n>` | `read_events` (`state.py:266`), line-offset cursor, + run token |
| `GET /api/node/<id>` | `mission_view.node_detail` verbatim |
| `GET /api/evidence` | `approval-evidence.txt` / `rejection.txt` under B5's rules |

`after` is validated as a non-negative integer — it is the one user-controlled
input that reaches an indexing operation, and rev 2's claim that "the only
user-controlled segment left is a node id" was wrong.

Node ids need no sanitising: `_ID_RE` is `^[a-z0-9][a-z0-9-]*$`
(`taskgraph.py:23`) and `node_detail` already looks the id up in `state["nodes"]`
before touching a path (`mission_view.py:501-504`). The route allowlist is for a
clean 404, not for safety. Batch 2 keeps a traversal test anyway.

### B2. No stdout content

Rev 1 put a stdout tail in the drill-down. That **reverses a recorded decision**:
`mission_view.py:405-409` — "Size and mtime only, never CONTENT. Tailing
`stdout.log` was rejected for good reason … and nothing here reverses that."
`stdout.log` is the harness envelope, i.e. the model's full result text. The page
serves what `node_detail` serves: names, sizes, mtimes.

### B3. Trace integrity — the full rule, not half of it

Rev 2 said "treat an empty `head` as not verified, as `cli.py:426-430` already
does". That cites the second half of a two-part rule, and **inverts on the case
that matters**: a journal tampered at line 5 returns `(False, prev, 5, …)` where
`prev` is line 4's digest — non-empty (`state.py:247-256`) — so the page would
render a broken chain as verified, with a head.

The correct four-way rule:

| Condition | Render |
|---|---|
| `not ok` | **BROKEN**, with the failing line — never mind `head` |
| `ok`, no events at all | *nothing to verify* (a fresh run, or a single torn first line) |
| `ok`, events but none chained | *unchained* |
| otherwise | verified, with head |

`verify_trace` returns only a 4-tuple with prose in `detail`, so distinguishing
rows 2 and 3 needs `total`/`chained` exposed. That is a small `state.py` change
— so B is **not** "contrib only", and §8 says so.

### B4. The page renders text it is given

Rev 1 said the JS computes layout from raw statuses. That means the JS maps
`blocked → "needs you"` itself: a third glossary, in a language pytest cannot
execute, and the first cockpit surface whose copy is not derived from
`mission_view`. Corrected: **every word comes from `mission_view` over the API**;
the JS positions boxes.

Round 2 showed that is not yet achievable and named what is missing:

- **No DE-tier duration renderer exists.** `headline()` formats
  `"3 h 20 m"` inline (`mission_view.py:232-233`); `_elapsed_str` (`5s`,
  `3m30s`, `1h05m`, `:590-599`) is private and consumed only by `cost_lines`,
  and consumed only by `cost_lines`. A time axis needs both clock-time tick
  labels and "running for 4m", which are two different formatters — see B6.8,
  where rev 4's single-formatter answer is corrected. **Batch 2 adds both,
  public and server-side, and the page uses only those.**
- **Words are not the only thing that can drift; the row set can too.** MISSION
  promises the DE that finished steps collapse into a count
  (`COCKPIT-FOR-DOMAIN-EXPERTS.md:66-69`, implemented in `mission_rows`, exposed
  as `visible_nodes`). A waterfall shows every node by definition. Resolved in
  B6.1: collapse becomes the **default disclosure level** rather than a tier, so
  the landing view still honours `visible_nodes` and cannot contradict the pane,
  while opening the timeline is the DE's own act.

Rev 1 also mis-cited the pinning test: it is `tests/test_mission_render.py:419`,
pinning six `status → word` pairs against `cockpit.ps1` — not the headline
vocabulary, not the collapse rules. It could never have pinned a JS
reimplementation.

### B5. Evidence on the page — with a correct predicate and a freshness rule

Doctrine permits it: the TUI already binds `e` to `approval-evidence.txt`
(`mission_tui.py:328-331`). Three corrections to rev 2:

- **The predicate must be `quiescent.py`'s, not `needs_you`.** `needs_you` is
  *any* node `blocked` (`mission_view.py:550-553`), which fires on a clarify
  gate — the normal exit-2 shape, and not an approval. Rev 2 would have put
  approval evidence above the fold when there was no approval.
- **Freshness.** `approval-evidence.txt` is a fixed path holding a point-in-time
  snapshot — `impact()` counts `git status` at render time
  (`render_evidence.py:202`). A later segment overwrites it and a stale one
  survives. **Corrected in rev 5:** rev 3 compared its mtime against the
  *approval node's* `started_at`, which flags every legitimate case as stale —
  the evidence is written by a render node that runs **before** the approval
  (`COCKPIT-THEORY-OF-OPERATIONS.md:296-303`), and `started_at` is stamped when
  the approval goes running (`roles.py:216`), so a fresh file is *always* older.
  The comparison is against the **render node's** most recent run interval:
  stale iff the file predates the last time the node that writes it ran.
- **`rejection.txt` gets its own rule and its own sentence.** It is the human's
  verbatim words, the one artifact the doctrine singles out as theirs
  (`COCKPIT-THEORY-OF-OPERATIONS.md:232-236`). When both exist, the evidence for
  the *current* approval wins; the rejection is reachable but never automatic.
  And it is named in the `--host` warning, because putting a person's own words
  on an unauthenticated surface advertised for phone use deserves one line.

The decision banner stays in the words `mission_server.py` already uses.

### B6. The rich view — one page, one audience, progressive disclosure

**Decision (2026-08-08, rev 4): the DE-tier promise is relaxed, and the tier
split is abandoned.** There is one page. The domain expert reaches full detail
by choosing to, and the default view stays plain.

The reasoning is adoption, and it is a legitimate design goal rather than a
concession: a surface a DE opens *because they want to* gets looked at, and a
surface that is looked at is one where a bad run is caught early. Rev 3's
`--driver` flag failed on its own terms anyway — a CLI flag is a per-process
seam, so a DE and a driver at the same URL see the same page regardless.

**What this retires — three clauses, not two.** `COCKPIT-FOR-DOMAIN-EXPERTS.md`,
"What you never have to do", is amended to strike *"Know what a 'run directory'
is"*, *"Work out what a number means"*, and — found in review — **"Decide
whether something is safe to restart."** L3 shows `invalidated_by` and hash
parts, i.e. `lockstep explain`; the guide currently promises that judgment is
made mechanically and is *"never something you have to judge"*
(`COCKPIT-FOR-DOMAIN-EXPERTS.md:186-188`). Putting the re-bill record one click
away puts the reader in front of exactly that judgment, so the clause goes with
the other two rather than being quietly falsified.

Replacement promise: **nothing you need in order to decide is behind a word you
do not know; everything else is one click away, labelled in plain language, and
never something you are required to act on.** The remaining clauses — write or
read code, use git, type a command, remember which files matter — stand.

**L3 needs its own glossary, and it is pinned like the first one.** Review
caught the replacement promise contradicting L3 in the same paragraph: `node
id`, `input-hash parts`, `chain head` and `verify-trace` are not plain-language
labels. Every L3 term carries a one-line gloss, and those glosses are pinned by
test exactly as `GLOSSARY` is (`tests/test_mission_render.py:419`) — otherwise
the page acquires unpinned DE-facing vocabulary, which is the defect the first
glossary test exists to prevent.

CLAUDE.md makes that guide binding on what may be said to the human, so it is
edited in the same commit as the page, never after.

**What this does not relax.** All four never-rules stand unchanged, and none of
them was ever a tier question: no write verb and no form (§4.7); the decision
happens at a terminal; quiescence is `quiescent.py`'s answer and never the
page's; evidence is quoted, never narrated (§4.5). Also unchanged: no stdout
bodies (§4.2), no run picker without a token (§4.1), and the words on the page
come from `mission_view` (§4.4).

**Visual target:** `docs/proposals/mockups/trace-page.html` (open it; static fake
data, no server) and `trace-page.png` beside it. It is a mockup, not shipping
code — but it is the rendered check step, and rendering it caught four layout
defects that reading the spec did not: axis ticks in a different coordinate
space from the bars, a clipped status word, a tip overflowing the plot, and both
views drawing at once.

#### B6.1 Four surfaces — and L0→L1 is a *switch*, not an expansion

| Level | What it shows | Entry |
|---|---|---|
| **L0 — Board** *(default)* | Everything today's page shows, restyled: headline, stat row, the collapsed step list **exactly as `mission_rows` emits it**, the spend line, both cost blocks, ACTIVITY, the per-step `<details>`, and — when a decision waits — the evidence block. | on load |
| **L1 — Timeline** | Every step on a shared time axis, in place of the step list. The rest of L0 stays put. | "show every step" |
| **L2 — Step** | A drawer: what the step was for, its intervals, attempts, what it produced (names and sizes), which checks ran and what they found, what it cost. | click a row |
| **L3 — Raw** | Node id, input-hash parts, what moved since last time, chain head, `verify-trace` detail — each with its glossed one-liner. | "show the raw record" |

**Correction from review: collapse is not expressible as a disclosure level.**
`mission_rows` does four things a filter cannot (`mission_view.py:308-346`): it
synthesizes `+ N more waiting` and `N finished, M not needed` rows that have no
L1 counterpart; it injects a node's verbatim `mission.txt` first line as an
extra row (`:332-336`); it iterates nodes in **recorded order**, not
chronological; and it forces rows in on `has_note`/`is_map` while capping
pending at three in state order. So L0→L1 deletes rows and inserts different
ones.

Two consequences, both specified rather than papered over:

- **L1 is an alternative rendering of the step list, not an expansion of it.**
  The control says "show every step", and switching back is one action. Nothing
  claims the two agree row-for-row, because they do not.
- **L1 must not lose what L0 showed.** A node with a `mission.txt` note carries
  a note marker on its row, and the note text is in its tooltip and its L2
  drawer. Rev 4 would have dropped it silently.

Rev 4 also claimed "L0 honours `visible_nodes`, so the default view cannot
contradict the pane". **Withdrawn**: `visible_nodes` is derived from
`mission_rows` (`:482-489`), so that agreement is with `mission_view` — not
with `cockpit.ps1`'s independent PowerShell collapse (`cockpit.ps1:511-581`),
which is the shipped default pane. Nothing spans the two, and §4.4 already
concedes the glossary test does not pin collapse rules. The honest statement:
**L0 renders from `mission_rows`, so it cannot contradict the TUI or the
server; agreement with `cockpit.ps1` rests on the same unpinned convention it
rests on today, and this proposal does not improve that.**

Each level returns to the one above in one action. **L2 names its step in L0's
words** — which requires a change, since L0 truncates labels to 33 characters
plus an ellipsis (`:327-330`) while `node_detail` prints the full name plus
`(step id: …)` (`:508-510`). L2 shows the full label without the identifier;
the identifier lives at L3.

**Integrity escalates.** §4.3's four-way rule is worthless three levels down: a
journal that renders **BROKEN** appears at L0, not at L3. Rev 4 put
`verify-trace` only at L3 and would have hidden a tampered journal from the
landing view of the surface the DE is now expected to open.

#### B6.2 The waterfall

*Form.* The data's job is duration-and-state-over-time per step, so the form is
horizontal spans on one shared time axis. **No minimum step count** — rev 4 said
"not drawn below four steps", which review showed would disable the timeline for
the cockpit's canonical flows (`flows/starter/evidence-approval.tg.json` is four
nodes, `clarify-gate.tg.json` three). L1 is opt-in: if the reader asked for every
step, three bars is the honest answer to what they asked.

**Time semantics — segments, not one span.** `state.json`'s `started_at` is the
*first* start and `ended_at` the *last* end, kept across every attempt, heal
round and resume (`roles.py:216-218`), so a node blocked overnight would draw a
fourteen-hour bar of which minutes were work. The true intervals are in
`events.jsonl`, which `cost_report.wall_and_heals` already walks as
`running→terminal` pairs (`cost_report.py:322-340`) before summing them away.
**The bar draws one segment per interval**, with the idle time between them as
surface. The table twin's duration column is the sum of the same intervals, so
the picture and the number cannot disagree — which was rev 4's defect: it would
have drawn a merged span beside a summed duration and called them the same
thing.

| Element | Spec |
|---|---|
| Row | one per node, ordered by first `running` event; nodes that never ran (`pending`, `skipped` — `started_at` is `None`) sort last, in graph order, and draw an empty track. **No tier grouping** — see below |
| Bar | a **span**, not a magnitude — 4px rounded at both ends, ≤ 24px thick, never filling the row band; one segment per run interval |
| Separation | 2px gap in the surface colour between adjacent rows and between segments — never a stroke around a bar |
| Running node | its last segment draws to `now`, with a soft fade at the leading edge and the live dot the page already uses. **Never dashed** |
| Sub-tick node | minimum 3px segment so a fast step cannot vanish; the tooltip carries the real duration |
| Gridlines | solid 1px hairlines at clean time ticks, one step off the surface. Never dashed |
| Axis | absolute clock times in `tabular-nums` at clean intervals; the container is sized to **include** the axis band, so the card never grows a nested scrollbar |
| Labels | the step's word in the row gutter, in a text token — never in the bar's colour. A duration at the bar tip only for the running step and any failed step. **Never a number on every bar** |

**No tier grouping — rev 4 reversed a recorded decision.** `load_tiers` returns
*approval* tiers keyed by approval node id (`mission_view.py:109-118`), not a
per-node grouping, so it would have grouped one row and left the rest loose.
And `cockpit.ps1:302-308` records the decision not to read `tiers` on the board,
with its reason: "a tier belongs at the decision surface … MISSION is a status
board. An earlier cut stored it in a script variable nothing ever read, which is
worse than not reading it — it made a dead channel look wired." Rev 4 reversed
that in the same document that rejected rev 1's stdout tail for reversing a
recorded decision.

#### B6.3 Colour — assigned by job, and validated

Two palettes, two different jobs, no overlap.

**Step state → the reserved status palette.** These are states, so they take
status tokens and never categorical ones, and every one of them ships with its
icon **and** its `mission_view` word beside it. Colour never carries meaning
alone — which is the accessibility rule and the cockpit doctrine arriving at
the same place from opposite directions.

The map keys are the **six `GLOSSARY` entries** (`mission_view.py:36-43`), and
the icon column is `COST_ICON` (`:572-579`) — which already exists and covers
all six. Rev 4 listed six rows that were not the glossary's six, omitting
`skipped`, and required "its icon" without referencing the icon map that ships,
which would have invented a second icon set: B4's second-glossary defect in
another medium.

| Status → word | Icon | Encoding |
|---|---|---|
| `done` → `done` | `✓` | status good `#0ca30c` |
| `blocked` → `needs you` | `⊗` | status warning `#fab219` |
| `failed` → `stopped with a problem` | `✗` | status critical `#d03b3b` |
| `running` → `running` | `◐` | no status hue — primary ink outline + live cap. Not a severity; painting it as one would misstate it |
| `pending` → `waiting` | `○` | no fill — empty track, muted `#898781` |
| `skipped` → `not needed` | `⊘` | no fill — empty track, muted, at 60% row height so it reads as deliberately absent rather than not-yet |

**Decorations, not words.** `"sent back for rework (1 of 2)"` is *not* a
glossary entry — `node_word` synthesizes it whenever `heal_round > 0`, and it
appends map counters too (`"running - 3 of 8 checked, 1 redone"`,
`:262-280`). So rework is a **modifier on a base status**, drawn as status
serious `#ec835a` on the segment that was redone while the row keeps its base
status; and the map counter is text, never colour. Rev 4's proposed test —
"the status→colour map pinned against the glossary" — would have failed on
first run, because its key set was neither `GLOSSARY.values()` nor `node_word`'s
range. The test is re-specified as: keys are exactly `GLOSSARY`, plus a
documented rule for `node_word`'s decorated forms, and an unknown status
(`node_word` falls back to `"?"`) renders muted with the raw string, never a
guessed colour.

All four status steps clear 3:1 on the dark surface (5.19 / 9.49 / 6.60 / 3.62).

**Cost breakdown → categorical slots 1–4, in fixed order**, for the stacked bar
of input / output / cache-read / cache-write. Validated, not eyeballed:

```
$ node scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500" \
      --mode dark --surface "#1a1a19"
  [PASS] Lightness band · Chroma floor · Contrast vs surface
  [PASS] CVD separation      worst adjacent #c98500↔#199e70 ΔE 8.4 (protan)
  [PASS] Normal-vision floor worst adjacent #c98500↔#199e70 ΔE 19.8
  → ALL CHECKS PASS
```

A legend is present (four series), segments are separated by the same 2px
surface gap, and interior segments carry no inline label — the legend and the
table view carry them.

**Surfaces and ink.** Chart surface `#1a1a19`, page plane `#0d0d0d`, primary
ink `#ffffff`, secondary `#c3c2b7`, muted `#898781`, gridline `#2c2c2a`, axis
`#383835`. The terminal aesthetic survives intact — today's hand-picked `#111`
background sits within a hair of the validated plane — but it is now a
documented palette rather than four values someone liked.

One consequence worth naming: **text stops wearing the data colour.** Today's
spend line is `#dc4` and the needs-you line is `#f66` (`mission_server.py`
style block). Under this spec the numbers go to text tokens and a coloured
swatch beside them carries the state.

#### B6.4 Meters and tiles

- **Spend meter.** The denominator comes from `cost_report._budget_cap`, which
  reads **the run's own flow copy** and whose docstring is the sentence rev 4
  wanted: *"the denominator the DE was quoted at the consent beat, not a live
  config"* (`cost_report.py:629-631`). Rev 4 said "the consent record", meaning
  the cockpit journal — which **nothing in the repo writes**: it is authored by
  the orchestrator agent, has no schema (`retrospect.py:201` guards
  `isinstance(cap, int)` for that reason), and is by doctrine *"evidence of what
  was said, not truth about state"*. Putting it on screen would print a
  hand-written number on the one surface the DE was told *"no one writes by
  hand … it cannot flatter or round off"*, and would collapse the separation
  that makes `retrospect.told_vs_state` an audit at all.
  Two cases the existing code handles and a bare meter cannot: **no cap
  declared** (`_budget_cap` returns `None` → show the count with no
  denominator and no meter, as `plan_card.py:140` does), and **multi-segment**
  (caps sum, degrading to `of at least N` — the guard that fixed a real
  `used 38 of 25`).
  **No severity ramp.** Rev 4 coloured the fill accent→warning→critical as it
  approached the ceiling; nothing in the run dir says 80% of a ceiling is a
  warning, and inventing that threshold is the first editorial judgment on a
  module whose docstring says it is *"summary-free by construction … the domain
  expert's trust anchor, so it must never acquire a narrated branch"*
  (`mission_view.py:32-35`). The fill is one hue with the ceiling marked; the
  only colour change is **at or over** the ceiling, which is mechanical.
- **Stat row.** `step 3 of 8` · `running 14m` · `spend` · `a decision is 2 steps
  away`. Label in sentence case, value in the same sans, **proportional
  figures** — `tabular-nums` is for the table view's columns and the axis ticks,
  never for a large standalone number.
- **Exactly one hero.** The headline sentence is it. No second thing on the page
  competes for that role.

#### B6.5 Interaction

- Hover **and keyboard focus** show the same tooltip on every bar; the hit area
  includes the 2px gap and meets ~24px.
- **Refresh holds the previous render at reduced opacity — never a skeleton.**
  This matters more here than in most dashboards: the page polls every second,
  so a skeleton flash would be the dominant visual experience of a healthy run.
- **Row changes are data, and are animated in place.** Rev 4 also promised
  "never a layout jump", which contradicts the collapse rule: every completion
  removes a row and increments `N finished` (`mission_view.py:315-316, 342`), at
  1 Hz. Resolved by specifying the mechanism instead of the wish — the tail
  counters occupy a **stable slot** that is present from first render, and a row
  leaving collapses its height over one frame rather than vanishing. The
  prohibition that stands is on *chrome* jumping (skeletons, re-flowed cards),
  not on the data changing.
- `prefers-reduced-motion` disables the live pulse; the state is still legible
  from the word and the cap.
- No per-chart filters and no filter inside a card.

#### B6.6 The table view, which is also the fallback and the test surface

Every chart has a table twin, and here it does triple duty:

1. **Accessibility.** No value is reachable only by hovering.
2. **No-JS fallback.** The table is **server-rendered from `mission_view`**, so
   the authoritative content exists before a line of JavaScript runs.
3. **It is what the tests assert against.** This is what makes §2's TDD bind
   satisfiable: the waterfall is a progressive enhancement over a
   server-rendered table, so pytest can pin the content while the untestable
   layer stays limited to positioning boxes. B4's "no logic that can be wrong
   lives in the JS" stops being a discipline and becomes a structural fact.

Under `forced-colors` or print, the page falls to the table view; texture stays
opt-in and off by default.

#### B6.7 What the rich view must not show, and two things it must

No stdout bodies (§4.2). No control that writes anything (§4.7). No run picker
without a run token (§4.1). No evidence without the freshness check and the
`quiescent.py` predicate (§4.5). Retiring the tier clauses widened *who may see
detail*; it did not widen *what exists to be seen*.

Two additions review forced, both consequences of adoption succeeding:

- **The question card must be on the page.** The guide promises that at a
  clarification *"the exact words the system used will appear on screen in the
  ACTIVITY area"* (`COCKPIT-FOR-DOMAIN-EXPERTS.md:113-117`) — and only
  `cockpit.ps1:944` renders `question-card.txt`. Neither `mission_view`, the
  TUI, nor the page reads it. Make the browser the DE's preferred surface
  without fixing that and the guide's verbatim promise becomes silently false
  at exactly the moment it matters. Add `GET /api/question`, and the same
  content to the TUI, so no surface has the banner without the words.
- **The banner and the evidence block must stop using different predicates.**
  §4.5 moves evidence onto `quiescent.py`'s predicate, but §4.5's closing line
  keeps the banner as `mv.needs_you` — *any* blocked node
  (`mission_view.py:550-553`). On a clarify gate the page would say **"needs
  you"** while deliberately withholding evidence and having no question to show.
  The banner is re-specified to name *which kind* of attention: a question
  (question card) or a decision (evidence). Same source, two shapes.

**And the keyboard says so out loud.** B6.5 makes the page keyboard-navigable,
and the DE has been told `a` and `r` are the answers at an approval
(`COCKPIT-FOR-DOMAIN-EXPERTS.md:132-151`). Pressing them here must not be a
silent no-op: they render the same sentence the banner carries — the decision
happens at the terminal. The terminal enforces its guarantee loudly (non-TTY
auto-rejects, exit 6); the page must not enforce it by doing nothing visible.

#### B6.8 What the API owes the page

Rev 4's list was short by three, because `mission_view` returns **preformatted
terminal strings** — `f"{name:<34} {word}"` with a 33-char truncation
(`mission_view.py:327-330`) — and there is no accessor that returns the parts
separately. Shipping 72-column padding into HTML, or re-splitting it in JS,
would each defeat B4. The full list:

1. **A structured row accessor** — `(node_id, label, word, icon, note)` per
   node, with `mission_rows` reimplemented on top of it so the terminal
   rendering stays the one that formats.
2. **A run-interval accessor** — the `running→terminal` pairs
   `cost_report.wall_and_heals` walks (`:322-340`) exposed *before* they are
   summed, for B6.2's segments and the table twin's durations.
3. **An absolute-time tick formatter.** B6.2's axis carries clock times, not
   durations, so rev 4's "one duration formatter" cannot produce it — and §8's
   proposed "no *step word* is rendered by client code" would not have caught
   the JS formatting them. Both formatters are public and server-side.
4. One public **duration** formatter (`_elapsed_str` is private and
   cost-panel-only, `:590-599`).
5. Per-node **cost component rows** from `cost_report`.
6. The meter **ceiling** from `cost_report._budget_cap` (B6.4) — not the
   journal.
7. `GET /api/question` for the question card (B6.7).

No tier label: B6.2 drops the grouping.

### B7. What it does not get, and what the test actually proves

No archive button, no annotation, no re-run, no kill. SSSF permits exactly one
write (`POST .../archive`); rejected here (§8.2) because the guarantee this
cockpit sells is that the browser cannot change the run.

`tests/test_cockpit_ux.py:248-254` asserts `do_POST`/`do_PUT`/`do_DELETE`/
`do_PATCH` do not exist. That survives B untouched and is a real mechanism.
"No *handler* writes" is not checkable and never was — a `do_GET` branch could
write, and `contrib/question_card.py` is a cockpit module that writes and
deletes.

Rev 2 proposed "every handler is asserted to be a pure read" as a mechanism.
Round 2 is right that half of it is a wish. Split, honestly:

- **Mechanism:** the route table is enumerated and pinned; no `do_*` method
  other than `do_GET` exists.
- **Coverage-bounded harness:** patch `open` (write modes), `Path.write_text`,
  `Path.unlink`, `Path.mkdir`, `os.replace`, `shutil.move` to raise, then drive
  every route against a fixture run dir. This proves purity **for the inputs
  exercised** — AST inspection cannot do better, because one level of
  indirection defeats it and the transitive closure is where writes live.

Rev 2 called the whole thing "a mechanism, not a promise". It is one of each.

---

## 5. Deferred: the protected-path floor (rev 1's A2)

Rev 1 put `protected` in `lockstep.toml`; rev 2 moved it into the flow file.
Both failed. The findings, so the next attempt starts from them:

1. **It needs a `format_version` bump.** `TaskGraph` is `extra="forbid"`
   (`taskgraph.py:69`), so a top-level `protected` is a *first-class field*, and
   `SPEC.md:522` freezes 1.x against exactly that. The `spec.writes` precedent
   rev 2 cited says so explicitly — `DEVIATIONS.md:99-101` records that `writes`
   was put inside the per-kind `spec` dict *specifically to avoid the bump*.
   Rev 2 inverted its own citation. And rejection would not be clean either way:
   `load_flow` checks only the major version (`taskgraph.py:140-141`), so an
   older driver produces a pydantic dump, not §15's purpose-built message.
2. **Map nodes are exempt.** `_run_map` acquires tokens and executes
   (`roles.py:1074-1080`) but never takes a scope baseline. A map of one bypasses
   any floor. Rev 2 presented "readonly harness and shell" as the exhaustive
   list of unchecked classes; it was not.
3. **The unlock rule fails open on its only shipped consumer.**
   `codemod-apply` needs `flows/`, so it would declare `unlocks: ["flows/"]` —
   an exact match on the protected entry, nullifying it wholesale. And with
   overlapping entries (`protected: ["src/", "src/lockstep/gates/"]`,
   `unlocks: ["src/lockstep/"]`) the rule is ambiguous: "the entry that would
   otherwise block it" is singular where two apply, and the natural `any(...)`
   implementation unlocks the gate programs — E2's attack succeeding.
4. **Globs fail open silently.** Under a non-glob deny dialect,
   `protected: ["*.tg.json"]` matches nothing and yields zero protection, while
   `writes` in the same file *is* glob-capable and pinned
   (`test_write_scope.py:80-83`), so an author has every reason to expect
   otherwise. Verify would have to reject glob metacharacters in `protected`.
5. **It cannot see gitignored paths — including `runs/`.** `snapshot()` uses
   `git add -A`, which honours `.gitignore`. So `lockstep.toml` is unprotectable,
   and so is **`<run_dir>/approval-evidence.txt`** — the artifact the doctrine
   says every decision is made from. That is the sharpest finding in either
   round and it deserves its own treatment, not a subsection.
6. **The implementation is not the obvious one.** Dropping the `scope and` guard
   at `roles.py:625` fails *every* node, because `path_in_scope(p, [])` is
   `False` for all `p` (`workspace.py:34-40`). The floor needs its own violation
   function.
7. **Cost, unmeasured.** Two full `git add -A` + `write-tree` passes per
   non-readonly node in every flow that declares a floor.
8. **The shell bypass is undecided** (rev 2's O4) while the floor would ship.
   A deny-list with a documented bypass is a sequencing defect.

**The matcher decision stands and moves independently.** `path_in_scope` uses
`fnmatch`, so `*` crosses `/` — permissive, which is fail-open for an allow-list
and over-blocking for a deny-list. The blast radius of fixing it is **zero
today**: every `writes` entry in the repo is one of five literal filenames, no
globs. It should be fixed *before* A1 ships, because after A1 a matcher change
stops altering a message and starts reverting writes the previous release
accepted.

---

## 6. Feature C — agent profiles: do not build

The decisive finding: **C removes the only integrity mechanism currently
covering write scope.** `spec.writes` lives in the flow file, so an edit changes
`flow_hash` and starts a new lineage. Move it into `lockstep.toml` and it is
covered by neither `flow_hash` nor any fingerprint part — `writes` reaches only
`meta["writes"]` (`harness.py:266`). Widening a profile's scope between a run and
its resume changes what gets *moved out of the working tree* under A1, with the
node staying `done`, no `invalidated_by`, and nothing for `explain` to show.

Two further disqualifiers:

- **The engine reads the raw spec.** `roles.py:621` reads
  `node.spec.get("writes")` directly and `plan()`'s resolution never reaches it,
  so a profile-supplied `writes` would be enforced by nothing. Same at
  `taskgraph.py:358, 367, 394, 421, 461`; any miss is a silent enforcement hole.
- **`argv_extra` after `readonly_argv` defeats readonly with a correct hash.**
  `harness.py:240-244` orders argv → `persona_flag` → `readonly_argv`; a
  last-wins flag appended after cancels readonly while `argv:` hashes perfectly
  and `readonly-unenforced` still passes (it only asks whether
  `stanza.readonly_argv` is non-empty, `taskgraph.py:367`).

**Preconditions before reconsidering:** (1) `writes` and `cwd` become hashed
fingerprint parts, as an independent change with its own re-billing event;
(2) profile resolution happens once, before both engine and verifier read the
spec; (3) `argv_extra` is inserted before `readonly_argv`.

---

## 7. Frozen-surface accounting

| Surface | Touched? | Note |
|---|---|---|
| Exit codes `0/2/3/4/5/6/7/8` | No | |
| `format_version` 1.x | No | The one change that would have required a bump (`protected` as a top-level flow key) is deferred to §5, where the requirement is recorded |
| §7 fencing/footer contract | No | |
| Hash composition (M3) | No | A0–A3 add no fingerprint part. C, which would have changed hashed inputs, does not ship |
| **`DEVIATIONS.md:104-107`** | **Superseded in full** | Both clauses change: A0 moves detection to *during* the node, A1 replaces "leave the files in place". Rewritten in the same commit |
| **`tests/test_write_scope.py:67`** | **Reversed** | A deliberate reversal of pinned behaviour, renamed to say what it now pins |
| SPEC §9.4 restore semantics | No | Round 1's breach claim was wrong: item 2's index clause governs *snapshot*. A0b fixes a real hazard, not a breach |
| `Workspace.restore` behaviour | **Changed** | Index-safe (A0b). Affects the existing heal path — for the better, and Batch 0 tests it there first |
| Shell node concurrency | **Changed, if A0c(i)** | Shell nodes acquire `tree`. Measured before the choice is made |
| `verify_trace` return | **Widened** | `total`/`chained` exposed for B3. Additive; `cli.py` unaffected |
| Cockpit "never" rules (all four) | No | *Answer an approval:* no write verb (mechanised) plus a coverage-bounded purity harness — §4.7 states which is which. *Hand over without exit 0:* holds; `quiescent.py` reads only `state.json`, the mailbox and the flow copy, and no page input reaches it. *Re-derive quiescence:* the page shows no hand-over signal, and B5 now uses `quiescent.py`'s own predicate rather than the broader `needs_you`. *Narrate in place of evidence:* B5 puts the evidence file on the page, with a freshness rule |
| Default MISSION page content | **Restyled, nothing removed** | L0 shows what the page shows today (board, spend, cost, ACTIVITY, node details) under B6.3's palette. Rev 2 would have silently dropped the cost blocks; rev 4 keeps every one and adds levels above them |
| **`COCKPIT-FOR-DOMAIN-EXPERTS.md` — "What you never have to do"** | **Three clauses retired** | *"Know what a 'run directory' is"*, *"Work out what a number means"*, and — found in review — *"Decide whether something is safe to restart"*, which L3's `invalidated_by`/hash-part panel breaks. Replaced by the disclosure promise in B6, which now also covers "never something you are required to act on". CLAUDE.md makes this guide binding on what may be said to the human, so it is edited in the same commit as the page. The other four clauses stand |
| **`COCKPIT-FOR-DOMAIN-EXPERTS.md:113-117` — the question-card promise** | **Made true** | The guide promises a clarification's verbatim words appear on screen; only `cockpit.ps1:944` renders them. B6.7 adds `/api/question` and the same content to the TUI. Not a retirement — a promise the code did not keep, surfaced by making the browser the preferred surface |
| L3 vocabulary | **New, and pinned** | `node id`, hash parts, chain head, `verify-trace` each carry a glossed one-liner, pinned by test as `GLOSSARY` is — no unpinned DE-facing words |
| Cockpit tier model | **Abandoned** | One page, disclosure levels. No `--driver` flag; a per-process flag could never have separated two readers at one URL anyway |
| "Additive only" | **Withdrawn** | A0, A0b, A0c and A1 change behaviour on live paths. Only A3 and the `verify_trace` widening are additive |

---

## 8. Sequencing and tests

**Batch 0 — A0 + A0b + A0c.** Correctness only; merges even if everything below
is rejected. Tests: a node is not accused of a concurrent *harness* peer's
in-scope write; a node is not accused of a concurrent *shell* node's write; a
staged-only hunk survives a heal rollback; an eol/`.gitattributes` file survives
a restore byte-for-byte; an exec-bit file keeps its mode (POSIX-gated). Measure
the A0c(i) concurrency cost on the largest flow and record the number in the
commit message.

**Batch 1 — A1 + A3.** Tests: out-of-scope create moved aside and named;
out-of-scope modify restored to baseline; out-of-scope revert of an operator's
uncommitted file restored (the C2 case SSSF cannot do); a staged out-of-scope
write does not survive in the index; an operator's pre-existing staged path is
named and left alone; in-scope writes untouched; patch written before restore;
attempt 2 overwrites neither attempt 1's discard dir nor its patch nor its
`touched-1.txt`; partial restore failure names both errors; lineage-head
fingerprint refreshed so resume reports no external edits; in-scope→out-of-scope
rename reported. Plus the rewritten `test_write_scope.py:67`.

**Batch 2 — B.** `contrib/` plus the `verify_trace` widening. Tests: route
table enumerated and pinned, no `do_*` but `do_GET`; the write-patching purity
harness over every route; `after=abc`, `after=-1`, and a traversal attempt all
404; cursor advances and never replays; **run token changes across a segment
boundary and the client-visible contract says so**; a run tampered at line 5
renders BROKEN, a fresh run renders "nothing to verify", an unchained run
renders "unchained"; the default page still contains both cost blocks and the
`render_page` glossary assertion still passes; evidence route appears exactly
when `quiescent.py`'s predicate says an approval waits, and flags a stale
evidence file.

Plus, for B6: **L0 renders server-side with JavaScript disabled** and contains
the headline, the step words and the spend figure (§2.5's bind made structural,
so assert it directly); L0 renders from `mission_rows`, so it matches the TUI
row-for-row (the `cockpit.ps1` agreement is *not* claimed — §4.6.1); every
waterfall value is present in the server-rendered table twin, and a healed node's
bar segments sum to its table duration; the status→colour map's keys are exactly
`GLOSSARY` and its icons exactly `COST_ICON`, with `node_word`'s decorated forms
(`sent back for rework (1 of 2)`, map counters) matched by the documented rule
and an unknown status rendering muted; the four cost-stack hexes are pinned
against the validator's recorded output; no step word and no time string is
rendered by client code; a note row survives L0→L1; a BROKEN chain appears at
L0; the meter shows no denominator when `_budget_cap` is `None` and `of at
least N` across segments; `a`/`r` on the page produce the terminal sentence;
`/api/question` returns the card verbatim when one exists; and the evidence
freshness check passes for a just-rendered file (the rev-3 rule failed this).
The DE-guide edits — three retired clauses, the L3 glossary, the question-card
promise — ship in this batch, not after it.

**Deferred: the floor (§5) and profiles (§6).**

Docs: `COCKPIT-THEORY-OF-OPERATIONS.md` gains the trace page and its two tiers;
`CLAUDE.md` module map and cockpit block; `DEVIATIONS.md` rewrite. The
`runs/`-is-unprotectable finding (§5 item 5) goes in the ops notes now, ahead of
any floor work.

---

## 8b. Rejected from SSSF, with reasons

1. **A SQLite mirror of the trace.** Sound for their design. Ours against is
   stronger: our journal is hash-chained and `verify-trace` is a guarantee. A
   mirror is a second store that can disagree with the one thing a reader can
   check, and the failure is silent. `events.jsonl` + a line cursor buys the
   polling model without the second store.
2. **The one write (`POST /archive`).** §4.7.
3. **Workflows as imperative Python.** Caching, resume, replay and estimate all
   need a graph readable before it runs. The architectural fork.
4. **Re-prompting the same live session on contract failure.** Cheaper, but it
   makes a node's cost depend on a harness's session store — unhashable,
   unreplayable state.
5. **A tester agent and `quality.py` placeholders that exit 0.** Their own README
   lists this as the first thing that will lie to you.

---

## 9. Open questions

- **O4.** Should any future floor cover `shell` nodes? Undecided, and §5 makes
  it a precondition rather than an open question.
- **O6.** `runs/` is gitignored, so no git-derived mechanism can protect
  `approval-evidence.txt` from a node that rewrites it. Own proposal; the
  finding goes in the ops notes immediately.
- **O7.** `RunState.schema_version` is `"1.0"` and nothing states when it moves.
  A3 adds a `PhaseRecord` field without a view on that.

---

## 10. What the reviews changed

**Round 1 (three reviews, 16 findings).** A0 (lock released before the check —
rev 1's A1 would have caused data loss); A2's gitignore blindness and
machine-local placement; the ADDENDUM-A category error; unlock-by-prefix
defeating the floor with rev 1's own example; C's un-hashed `writes`; B's stdout
tail reversing a recorded decision; B4's second glossary; a superseded deviation
and a pinned test missing from §7.

**Round 2 (two reviews, 9 findings, three of them blockers in rev 2's own
rewrite).** A0 not closing E0 for token-less shell nodes; A1's mutation never
placed inside the lock; `git cat-file` restore corrupting eol/smudge/LFS content
invisibly; `protected` being a first-class field requiring a `format_version`
bump — inverting the very deviation rev 2 cited; map nodes as a third unchecked
class; the deny-matcher's overlapping-entry ambiguity and silent glob fail-open;
`touched.txt` reintroducing the overwrite hazard A1 had just fixed; the
`verify_trace` rule inverting on tampering; the polled cursor having no run
identity; `--driver` being a per-process seam over a false premise about
`mission_view`; `needs_you` firing on clarify gates.

**Round 3 (one review of rev 4's B6, 9 findings).** A third guide clause broken
by L3 and not accounted for; the replacement promise contradicting L3's own
vocabulary in the same paragraph; collapse not being expressible as a disclosure
level (`mission_rows` synthesizes and reorders, it does not filter); a status
map whose six keys were not the glossary's six — `skipped` missing, "sent back
for rework" not a glossary word at all — making the proposed pinning test
unpassable; a second icon set invented beside the `COST_ICON` that ships; the
spend denominator sourced from a hand-authored journal instead of
`cost_report._budget_cap`; tier grouping that reversed a decision recorded in
`cockpit.ps1:302-308`; a minimum-step threshold that would have disabled the
timeline for the cockpit's own canonical flows; a promise at
`COCKPIT-FOR-DOMAIN-EXPERTS.md:113-117` that no non-PowerShell surface keeps;
and a defect in rev 3's own evidence-freshness rule, which flagged every fresh
file as stale.

**Review claims checked and rejected.** `restore` does not breach SPEC §9.4
(item 2's index clause governs `snapshot`); the "silent on concurrent nodes"
framing was wrong in the *opposite* direction from rev 1's — every non-readonly
harness node holds `tree` unconditionally; the node-id allowlist is sufficient
and partly redundant (`_ID_RE` plus `node_detail`'s own lookup); B4's timezone
concern does not apply (`utcnow()` emits `Z`); doctrine does not forbid the
evidence file on a non-deciding surface (the TUI already binds `e` to it); and
rev 2's own `export_fixture` finding was wrong — the scrubber is an allowlist,
so the proposed test asserted a tautology.

**What survived from rev 1 unchanged:** the recommendation not to build C, and
the five rejections in §8b.
