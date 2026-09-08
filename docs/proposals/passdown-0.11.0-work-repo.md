---
type: notes
title: "Passdown: 0.10.0 → 0.11.0 (the OW-07 response release) — work-repo integration"
description: Everything the work repo needs to integrate 0.11.0 — the release that is its own OW-07 feedback, built. What changed by surface, the one breaking exit code and the two output changes that will surprise existing scripts, the four mechanisms the reports asked for that already shipped, machine-local steps no file carries, and the verification checklist. Written to be executed by whoever drives the work mirror, with no context beyond this file.
resource: docs/proposals/passdown-0.11.0-work-repo.md
---

# Passdown: 0.10.0 → 0.11.0 (the OW-07 response release)

**This release is your two feature requests, answered and built.** The OW-07
campaign filed two reports against 0.10.0 — Sol's (2026-09-07, S1–S6) and
Gemini's (2026-09-08, G1–G5). The point-by-point disposition is
`docs/proposals/upstream-response-ow07-feedback.md`; 0.11.0 ships everything
scheduled for 0.10.1 and 0.11.0 in it. Both reports were verified
claim-by-claim against the source before classification, and the evidence
discipline in them is what made the response cheap to produce. That is worth
saying back.

One confirmed engine bug (P0), four accepted features, one new programme, three
declines that each name the shipped mechanism they re-invent, and four pain
points that already had answers nobody found (§4 — read that section even if
you skip the rest).

## 1. Carrying it over

Preferred: the bundle. At home:

```
.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
.venv\Scripts\python.exe contrib\build_bundle.py --version 0.11.0
```

copy `dist\lockstep-cockpit-0.11.0.zip` across, unzip over the work mirror, and
`pip install --force-reinstall lockstep-0.11.0-py3-none-any.whl` into the work
venv (`contrib/INSTALL-WORK-MACHINE.md` is the full first-install guide).

**The bundle carries no `tests/`.** `build_bundle.py`'s `DIRS` list is
`contrib`, `flows`, `personas`, `docs`, `.claude/skills`, `.claude/agents` — so
if the work repo runs full pytest (it does, and §6 depends on it), copy the
test files by hand. New this release: `tests/test_budget_override.py`,
`test_ledger_check.py`, `test_reads_manifest.py`, `test_terminal_record.py`.
Changed: `test_detach.py`, `test_explain_graph.py`, `test_gates.py`,
`test_lint.py`, `test_mission_render.py`.

If you mirror manually instead, the full changed surface since 0.10.0
(`9e353fc..981b294`, 40 files) is:

- `src/` **(via the wheel)** — `cli.py`, `roles.py`, `state.py`, `taskgraph.py`,
  `explain.py`, `reads.py`, `executors/harness.py`, `executors/fake.py`,
  `gates/version_sync.py`; **NEW** `gates/ledger_check.py`.
- `contrib/` — `cockpit.ps1`, `mission_server.py`, `mission_view.py` (all three
  learn the refused/parked vocabulary; the two vocabulary tests pin them).
- `flows/` — **NEW** `factory/adjudicated-review.tg.json`; `factory/README.md`.
- `personas/` — **NEW** `adjudicator.md`.
- `docs/` — **NEW** `guides/CONSUMER-PLAYBOOK.md`, **NEW**
  `proposals/upstream-response-ow07-feedback.md`; changed `FLOW-AUTHORING.md`,
  `COCKPIT-FOR-DOMAIN-EXPERTS.md`, `DRIVING-LOCKSTEP.md`, both indexes.
- `.claude/skills/` — `debug-run`, `flow-authoring`, `getting-started`.
- `lockstep.toml.example` (see §2.2 — **this one matters**), `CLAUDE.md`,
  `CHANGELOG.md`, `pyproject.toml`.

`contrib/approve.ps1` and `lane.py` are unchanged this release, so a work copy
with local edits needs no diff-before-overwrite this time.

## 2. What will surprise existing scripts

### 2.1 A post-lock refusal now exits 7, not 4 — breaking

This is S6, the confirmed P0, and it is worse than the report said. A refusal
taken *after* the lock (dirty write scope, a failed heal precondition) used to
leave every node `pending` and an empty journal, so `cmd_wait` reconstructed
exit **4** — "stopped with runnable work remaining; a plain resume continues."

That advice was live ammunition. The dirty-scope preflight is fresh-runs-only
by design (`check_dirty_scope=not resume`), so an operator who trusted exit 4
and typed `lockstep resume` **silently bypassed the exact protection that
refused the run** — and if the writer's hash had missed, it re-ran and legally
overwrote the human-edited file. The wrong exit code funnelled the operator
into the failure the refusal existed to prevent.

Now: the refusal handlers persist a run-level `terminal` record plus a chained
`refusal` event before releasing the lock, and `wait` returns the recorded
**7**. **Anything on the work side keyed on exit 4 for this case must change.**
Budget stops still read 4.

### 2.2 Your local `lockstep.toml` needs `--permission-mode` on the claude-code stanza

Machine-local, gitignored, and **the example will not update it for you.** A
headless `claude -p` spawn auto-denies its own tool permission prompts, so a
*writing* node drafts its output and is then denied the Write that saves it.
Observed live, on this release's own changelog node. `lockstep.toml.example`
now carries `--permission-mode acceptEdits` (Edit/Write only; the driver's
write-scope quarantine remains the enforcement layer). A node that must RUN
commands is still denied Bash — escalating to `bypassPermissions` is a
deliberate, named decision, never a default.

Skip this if the work mirror drives pi/Copilot only.

### 2.3 Output vocabulary moved on three surfaces

- An **auto-rejected approval** is now worded as a parked question, not a
  decision: `wait`/`status`/MISSION/cockpit say *"resume from a terminal to
  answer"*. A human rejection stays a rejection. Exit 6 is unchanged — wording
  only. (This is S5, and the requested behaviour already shipped; see §4.)
- A **refused run says so everywhere a reader asks**: `status` prints the
  evidence verbatim, `active` tags **REFUSED** by default, MISSION and the
  cockpit headline say `refused: dirty scope` instead of `waiting`. A
  `--detach` parent now watches a 3s grace window so the refusal lands in the
  launching terminal instead of only in a log nobody was told to read. The next
  drive clears the record.
- Old-driver compatibility holds in the other direction: `RunState` ignores
  unknown fields, so a 0.10.0 driver reading a newer `state.json` degrades to
  the old reporting rather than failing. Useful if the mirror lags.

## 3. What you asked for, and now have

**`lockstep resume <run_dir> --max-agent-spawns N`** (G3a) — raise, or lower,
the spawn cap for **this drive only**. Lowering below what is already spent is
allowed; "stop spending" is a coherent operator intent, warned and never
refused. The flow's ceiling stays the consent artifact, every override leaves a
journaled budget event that `status` echoes, and the next plain resume is
governed by the flow again. This replaces the three-command workaround the
report described (edit the flow → new lineage → `--seed` back).

**`spec.reads_manifest: "paths"`** (S1) — append the resolved `reads` list to
the prompt.

> `spec.reads` is an input-hash declaration only. It does not tell the harness
> which files exist, and does not grant or perform reads.

That sentence is now in FLOW-AUTHORING because the field name invited the
opposite reading and a reviewer told to "read the files named in `spec.reads`"
correctly reported no such list and blocked on evidence access. The manifest is
in the prompt, therefore in the input hash: a changed match set re-bills, and
`explain` names `prompt.reads_manifest`. It shares `apply_reads`' enumerator,
so the list the prompt names and the files the hash covers cannot disagree.
Zero matches inject an explicit empty manifest — silence is how the blocked
reviewer got there. `reads_manifest` without `reads` is a `verify` error;
absent or `"none"` is byte-identical to 0.10.0. **`"contents"` was declined** —
bounded generated context is `spec.context`'s job.

**The adjudicated-review programme** (S2 + G5) — for review rounds that never
converge. The principle: **discovery and gating must not share a mind.** A
reviewer's job is to find everything, and a mind graded on finding will grade
what it finds as major.

```
.venv\Scripts\lockstep.exe run flows\factory\adjudicated-review.tg.json ^
  --arg subject=<what is under review> ^
  --arg scope=<the frozen scope statement> ^
  --arg ledger=docs\reviews\<campaign>-ledger.json
```

Reviewers fan out and discover under a frozen scope statement; ONE readonly
adjudicator (`personas/adjudicator.md`) re-grades their raw findings —
unevidenced or out-of-scope demotes to `nit` **with the reason kept**, and a
NEW blocker is always allowed but must say why it is in scope. The gate then
reads the *adjudicated* severity, not the raw one. Zero engine change: contract,
gate, persona and the `node_diff` probe all shipped already; what was missing
was the composition, stated.

**`lockstep.gates.ledger_check`** is the cross-round memory, and the design rule
is the important part: **the model never writes the ledger.** The adjudicator is
readonly, so it *cannot* — and must not, because "an `accepted-risk` disposition
cannot be silently removed by a later model response" is not a promise any
prompt can keep. The deterministic gate merges each round mechanically (new /
persisting / reported-resolved, nothing ever deleted, returns recorded), blocks
only on ACTIVE entries at threshold, treats `accepted-risk` as a human state
that must carry a disposition, and re-runs idempotently so resume revalidation
cannot inflate history. The model proposes; the program owns the file.

The ledger lives **in the repo** (`docs/reviews/<campaign>-ledger.json`), not
under `runs/` — `runs/` is gitignored and sensitive, and the memory has to cross
lineages.

Two new advisory lints (`verify --lint`, exit code unchanged):
`lint-unadjudicated-reviews` names the ping-pong shape this replaces (two or
more `Finding[]` producers each gated raw; *one* gated raw stays refine-loop's
perfectly good shape), and `lint-ledger-rollback` catches the ledger gate
colliding with heal's default rollback — which restores every path since its
baseline and would erase the cross-round memory each round, the exact failure
the ledger exists to end. **Use `"rollback": false` on that gate.**

For delta mode, feed the reviewers a `node_diff` probe's output and say so in
the frozen scope (§4).

**`explain --graph` stops overstating freshness** (S4) — a node whose freshness
rests on an always-rerun shell reproducing its recorded output now reads
`conditionally fresh <id> — depends on always-rerun shell <sid>` instead of
plain "fresh". This is the OW-07 case where a pre-run explain said three fresh,
the shell printed a different duration string, and two re-billed. Taint follows
*consumption* (`{steps.<shell>.}`), not mere ordering edges.

**`docs/guides/CONSUMER-PLAYBOOK.md`** — symptom first, mechanism second. Read
it before authoring the next workaround; §4 is why it exists.

## 4. Four things you already had

Four reported pain points had shipped answers nobody found. That is a
discoverability failure against our docs, not an authoring failure on your side
— but these are usable today, on 0.10.0 as well as 0.11.0.

| Pain | Shipped answer |
|---|---|
| "editing the flow throws away completed work" (G4) | `lockstep run <edited-flow> --seed <old_run_dir>` — every hash-matched node served free into the new lineage. Preview with `explain <old_run_dir> --graph` first. |
| "reviews re-audit the whole cumulative history" (G5) | `python -m lockstep.probes.node_diff --node <id>` — diffs the two git trees the engine RECORDED for a scoped node (`tree_before`/`tree_after`), so a phase-1 review cannot be re-run against phase-2 work. `lint-live-diff-per-phase` exists because of exactly this. |
| "agents get quarantined for helpful lint cleanup" (G1) | the **`pi-guarded`** stanza — `--extension contrib/pi-extension/lockstep-guard.ts` makes the out-of-scope write fail *in-session as a tool error*, where the agent self-corrects for free, instead of post-hoc at the snapshot. **Your Phase-1 loss was one `--extension` flag away from not happening.** |
| "detached runs can't wait for approval" (S5) | the auto-reject **is** the park. The record already distinguishes *nobody was there* from *the human said no*; auto-reject writes no `rejection.txt` (that artifact is exclusively the human's verbatim words); approvals are never resume-skipped (§9.3), so an interactive `resume` asks for real, once. Exit 6 is the handoff signal. The split-flow workaround is unnecessary. |

Two limits on `--seed`, stated here so it does not oversell itself: **shell
nodes** always re-run (§0.1.7 — they spend no tokens) and **map items** are
never seeded (their per-item hash composes *after* planning, so a plan-time
decision cannot see it). **A map-heavy flow re-bills its items across
lineages — budget for that before seeding.**

## 5. Two pieces of authoring discipline this release asks for

1. **A shell node's output is a hash input for everything downstream that
   interpolates it — emit stable text.** The S4 diagnosis was confirmed, but the
   proximate cause was *a pytest duration inside a Verdict reason*, and that is
   a flow bug the house rule exists to prevent: gate bodies live in
   `src/lockstep/gates/` as tested programs precisely so their output is
   deterministic (`pytest_verdict` emits a stable pass-path Verdict for this
   reason). Keep timings and logs on stderr or in evidence files. Now a named
   anti-pattern in FLOW-AUTHORING.
2. **Editing a flow mid-campaign, the recipe:** budget-only change →
   `resume --max-agent-spawns` (no new lineage at all). Any other edit →
   `run --seed`, with `explain --graph` first to see what will re-bill.

**And one request back:** `lint-missing-write-scope` is held at *lint* rather
than promoted to a `verify` error at format_version 1.1 **because of this
mirror** — every flow here already complies, so the promotion's entire effect
lands on flows elsewhere, and promoting it in the same week a downstream copy is
being synchronised turns one migration into two failures. When the work repo's
own flows declare `spec.writes`, say so and the hold comes off.

## 6. Verification checklist (in order; zero-token except `doctor`)

```
.venv\Scripts\python.exe -m pytest                     # full suite green (copy tests first — §1)
.venv\Scripts\lockstep.exe --help                      # imports as 0.11.0
.venv\Scripts\lockstep.exe run flows\selftest-replay.tg.json
python contrib\replay_suite.py                         # 0.11.0 is ADDITIVE to hashing:
                                                       # reads_manifest absent/"none" is
                                                       # byte-identical, so the fixture must
                                                       # pass UN-RE-RECORDED. A mismatch here
                                                       # is mirror drift — investigate, do not
                                                       # re-record.
python contrib\torture_suite.py
.venv\Scripts\lockstep.exe run flows\demo\compose-smoke.tg.json
.venv\Scripts\lockstep.exe verify flows\factory\adjudicated-review.tg.json --lint
.venv\Scripts\lockstep.exe doctor                      # spends small model calls
```

Then two checks specific to this release:

```
.venv\Scripts\lockstep.exe verify <your-review-flow> --lint   # expect lint-unadjudicated-reviews
                                                              # on any gate-per-reviewer shape
.venv\Scripts\lockstep.exe explain <a-recent-run> --graph      # expect "conditionally fresh" where
                                                               # a shell node feeds a harness node
```

The S6 repro is deterministic and model-free if you want to see it: dirty a path
inside a declared `spec.writes`, `run --detach`, then `wait` — expect **7** and
a refusal message, where 0.10.0 gave you 4 and bad advice.

## 7. What is coming, and what is not

Do not re-file these.

**0.12.0:** `lockstep adopt` (S3) is **already built** on main — adopt a
human-remediated artifact into a settled run: the writer is pinned even
against a hash miss, its consumers re-run unweakened, and the pin dissolves
on `--release`, a heal round, or a steer (design note
`docs/proposals/DESIGN-NOTE-adopt.md`, adopted with all five decisions;
DEVIATIONS 2026-09-08; the playbook's "I fixed the artifact by hand" section
has the recipe). Still to come in 0.12.0: G1b, one corrective re-spawn on a
write-scope violation, mirroring the contract-violation shape; S4's
`stable_output` projection **only if** the discipline in §5.1 proves
insufficient by then.

**Declined, with the shipped mechanism named:** G1a advisory/secondary write
scopes (a scope you may violate is not a scope — the answer is `pi-guarded`, or
an honest declaration); G4 topology/config hash split (that is `--seed`;
weakening lineage identity solves a solved problem); S1 `"contents"`; S5's new
run-state and exit code (exit codes are a frozen surface, and the record already
encodes the distinction).

**Deferred, not declined:** G3b per-node spawn budgets — it interacts with heal
rounds, map fan-out and corrective re-spawns, and nothing in the OW-07 evidence
needed it once the global override existed. **Revisit on a second starvation
report**, so if it bites again, file it.

Still open from earlier and waiting on *you* for evidence: heal rollback
narrowing to declared scopes (E8-full) triggers on the **first** real
`restored-undeclared` event anywhere. None has fired yet. Whoever sees one,
bring it upstream — that item is deliberately not being re-opened on reasoning
alone.

## 8. Machine-local steps (no file carries these)

- `--permission-mode acceptEdits` into the work `lockstep.toml`'s claude-code
  stanza if the mirror uses it (§2.2). `lockstep.toml` is gitignored.
- Reinstall the wheel into the work venv, then `lockstep doctor` — version bump
  plus the weekly rule anyway.
- `git config gc.auto 0` in the work clone if it was never applied (per-clone
  config; the mirror copy does not bring it, and `lane.py start` warns while it
  is unset).
