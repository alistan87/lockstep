---
type: notes
title: "Passdown: 0.16.0 → 0.17.0 (the open-work release) — work-repo integration"
description: Everything the work mirror needs to integrate 0.17.0 — the release that ranked every recorded seam and built the top of the list. The changed surface by directory, the two output shapes that will surprise existing scripts, the one machine-local edit no file can make for you (the claude-code --safe-mode flip and its one-time re-bill), the four new capabilities worth adopting deliberately, and the verification checklist. Written to be executed by whoever drives the work mirror, with no context beyond this file.
resource: docs/proposals/passdown-0.17.0-work-repo.md
---

# Passdown: 0.16.0 → 0.17.0 (the open-work release)

**Nothing here breaks a flow that verifies today.** No exit code moved, no
frozen surface changed, `format_version` is still 1.x, and hash composition is
untouched. Two things re-bill once, both by design and both under your control,
and one printed line changed shape. That is the whole risk surface; the rest is
capability you opt into.

**If the bundle you were handed says 0.17.1**, this file still applies in
full: 0.17.1 is a patch for one defect found by cutting 0.17.0 — the
`release-cut` flow could not report a successful tag, because bare `git tag`
is silent on success and the engine fails a resultless attempt whatever its
exit code. The node goes through `contrib/git_tag.py` now, which prints, is
idempotent on the same commit, and makes an **annotated** tag by default
(`--arg message=...` for a real annotation). Nothing else changed and no
frozen surface moved; if the mirror does not cut its own releases, 0.17.0 and
0.17.1 behave identically. `contrib/git_tag.py` and `tests/test_git_tag.py`
are the only new files beyond the §1 list.

**If the bundle says 0.18.0**, add two additive things and no risk: (1) the
engine journals `kind: "timing", op: "dispatch-wait"` per dispatched node
(the wait behind its wave barrier, with the dependency or the heal-round
re-pend that made it ready named in `after`), and `status` sums those into
one `dispatch wait:` line — advisory, no reader branches on it, nothing
re-bills; (2) `gc` and `explain --graph` name a run whose recorded
`repo_root` no longer exists (`root gone:`) — the mirror's harvested lanes
will start reading that way, and `gc` keeps them exactly as before. New file:
`tests/test_dispatch_wait.py`; `state.root_present` is the one new helper.
CHANGELOG 0.18.0 and DEVIATIONS 2026-09-20 are the record.

This covers **0.16.0 → 0.17.0 only** (`1fd43e3..91e6510`, 51 files). If the
mirror is on an older baseline, read the intervening CHANGELOG entries first —
the passdown practice lapsed between 0.11.0 and this file, so there is no chain
to follow back.

**What the release is.** `docs/notes/OPEN-WORK.md` — new this release, and
probably the most useful file in it — ranked every actionable item recorded
across the roadmap notes, the lessons file, the proposals index, and the
deferral sections of the adopted proposals: 23 items in three tiers, plus a
second list of recorded seams left deliberately unranked so the omission is
visible rather than silent. Six of the seven ranked worth doing were then built.
Read that file before asking upstream for anything; if the request is already in
it, the row says what it costs and what would have to be true to move it.

## 1. Carrying it over

Preferred: the bundle. At home:

```
.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
.venv\Scripts\python.exe contrib\build_bundle.py --version 0.17.0
```

copy `dist\lockstep-cockpit-0.17.0.zip` across, unzip over the work mirror, and
`pip install --force-reinstall lockstep-0.17.0-py3-none-any.whl` into the work
venv (`contrib/INSTALL-WORK-MACHINE.md` is the full first-install guide).

**The bundle still carries no `tests/`.** `build_bundle.py`'s `DIRS` is
`contrib`, `flows`, `personas`, `docs`, `.claude/skills`, `.claude/agents`. If
the work repo runs full pytest — it should, and §5 depends on it — copy the test
files by hand. **New:** `tests/test_runs_dir.py`. **Changed:** `test_lint.py`,
`test_mission_render.py`, `test_seed.py`, `test_stanza_digest.py`,
`test_verify.py`, `test_write_scope.py`.

If you mirror manually instead, the changed surface is:

- `src/` **(via the wheel)** — `cli.py`, `registry.py`, `roles.py`, `seed.py`,
  `state.py`, `taskgraph.py`, `__init__.py`.
- `contrib/` — `cockpit.ps1`, `mission_view.py`, `mission_server.py`,
  `mission_tui.py`, `collectors.py`, `cost_report.py`, `lane.py`,
  `mission_bench.py`, `plan_card.py`, `retrospect.py`, `session_spend.py`. Most
  of these changed for one reason: they ask `registry.resolve_runs_dir` now
  instead of hard-coding `runs`.
- `flows/` — `audit-docs`, `audit-spec`, `demo/repo-hygiene-demo`,
  `factory/codemod-apply`, `factory/triage-intake`, `map-summarize` (all six for
  declared map write scopes; see §3.3).
- `docs/` — **NEW** `notes/OPEN-WORK.md`, **NEW** `spec/AMENDMENTS-r7.md`;
  changed `spec/DEVIATIONS.md`, `guides/FLOW-AUTHORING.md`,
  `guides/THEORY-OF-OPERATIONS.md`, `guides/COCKPIT-FOR-DOMAIN-EXPERTS.md`,
  `guides/COCKPIT-THEORY-OF-OPERATIONS.md`, `guides/FLEET-OPERATIONS.md`,
  `notes/ROADMAP-NOTES.md`, `proposals/upstream-response-mission-scale.md`,
  every index.
- `.claude/` — `skills/flow-authoring/SKILL.md`, `agents/spec-auditor.md`.
- `lockstep.toml.example` (see §2.1 — **this one matters**), `CLAUDE.md`,
  `README.md`, `CHANGELOG.md`, `pyproject.toml`.

**`docs/spec/AMENDMENTS-r7.md` is adopted; authority order is now
r7 > r6 > r5 > r4 > SPEC.** It is a *restatement*: every section names the
DEVIATIONS entry it lifts into spec text, and none of it changes behaviour. If
the mirror runs `flows/audit-spec.tg.json`, that flow hard-codes the layered
form and needs r7 in its list — otherwise the audit grades the code against a
spec missing eleven things the code deliberately does, and reports them all as
findings.

## 2. What will surprise existing scripts

### 2.1 `--safe-mode` on the claude-code stanzas — a one-time re-bill

`lockstep.toml` is gitignored on both machines, so **nothing copies this for
you**; `lockstep.toml.example` carries the flag and the controls behind it.

A headless `claude -p` spawn auto-loads `CLAUDE.md` from cwd and its parents AND
every `.claude/skills/*`, none of it in `input_hash` — an unhashed instruction
channel, the same class as pi's `AGENTS.md` discovery that the pi stanzas have
closed since August. Five live controls against claude 2.1.270 from the repo
root: the baseline spawn saw this repo's `CLAUDE.md` and all five of its skills;
`--safe-mode` reduced that to none of either, with subscription auth intact and
a writing spawn under `acceptEdits` still saving its file. `--bare` also
restricts auth to `ANTHROPIC_API_KEY` and is a non-starter for a subscription
stanza; `--disable-slash-commands` drops the skills but leaves `CLAUDE.md`
loaded.

**Adding the flag changes argv, argv is hashed, so every claude node re-bills
once.** That is the correct price and it is paid once per lineage — but decide
deliberately whether to flip mid-campaign or at a boundary. What it does NOT
remove: the skills bundled with the harness itself, which are the harness's own
behaviour, like its version — recorded, not hashable.

After flipping, keep node rules in `personas/` and `spec.context`. Those are now
the only instruction channels into a claude node, and both are hashed — which is
what makes `explain`, `--seed` and `--replay` truthful about what a node was
told.

### 2.2 The `seeded:` status line names map items

A `--seed` run that served map items prints them per item and counts them
separately:

```
seeded: 2 node(s) and 5 map item(s) served from <run> — build, lint, chunk[0], chunk[1], …
```

Two consequences for anything parsing it: the `N node(s)` clause is **absent**
when only items were served (it used to be the only clause and always present),
and names can now carry a `[i]` suffix. A partly served map is deliberately
neither "seeded" nor "new" — it is its items.

## 3. What is new, and worth adopting deliberately

### 3.1 `[driver] runs_dir` — where runs live is config now

One precedence rule, asked by `run`, `doctor`, `gc`, `active`, every cockpit
tool, and the pane: **`--runs-dir` flag > `[driver] runs_dir` in `lockstep.toml`
> `./runs`**. A relative value resolves against the config file's own directory,
so `"../lockstep-runs"` means a sibling of the repo. The default did not move;
doing nothing keeps `./runs`.

Hash-neutral — run dirs are excluded from every fingerprint — with one
exception: the config file's whole-file digest folds into `kind:"flow"` nodes,
so adding the key re-bills composed children once.

**Worth doing on the work mirror**, for the reason the fleet already does it by
hand: a runs root outside the audited tree retires the M7 warning class (an
un-ignored run dir makes every resume warn about external edits to its own
`state.json`), and read-tool agents stop browsing prior runs' prompts and diffs
by accident. A bad value degrades rather than blocks — a non-string or empty
`runs_dir` prints one line to stderr and falls back to the default.

### 3.2 `--seed` serves map items

Previously `--seed` skipped a map entirely, because an item's hash appends
`index:i` *after* the executor plans and a plan-time decision could not see it.
The engine now hands the composed per-item hash to `SeedExecutor.serve_item` —
still before any spawn, so a served item costs no spawn budget. On a chunked map
that is most of the bill. Provenance is per item: `seeded_from` on the item
record, `item` on the journal's seed line.

### 3.3 Maps can declare a write scope — `write-scope-on-map` is gone

A map node may now carry `spec.writes`, and the engine runs the full single-node
sequence per ITEM inside the `tree` token each write-capable item already held:
baseline, diff, quarantine, one corrective re-spawn, touched evidence, the
item's own attempt counter, evidence under `phases/<map>/items/<i>/`. The scope
is the map's and takes `{args.NAME}` only — never `{item…}`.

This closes the one mutator class quarantine could not guard. **Check your own
map nodes**: `lint-missing-write-scope` now covers maps, so `verify --lint` will
start advising on any mutating map that declares nothing. Upstream declared
`["**"]` on `codemod-apply` with the rationale written down, and `[]` on
`triage-intake`, `map-summarize` and the repo-hygiene demo. A codemod-apply map
in the work repo is the live unscoped instance this was built for.

### 3.4 The cockpit's last three views

The board gains a `blocking conditions` line — what the engine itself said went
wrong, in the engine's own categories, beside the review-findings line and never
inside it. A step blocked *behind* a failure is not counted as its own
condition, and an optional map's tolerated item does not read as blocking. The
step drawer gains `findings across attempts` (new, persisting, resolved,
severity changed; `not comparable` when a side is not a findings shape, attempts
labelled by the journal's recorded cause and never by inference) and a
per-attempt tool-activity line that says `not reported` rather than `0`.

Vocabulary is pinned across the page, the TUI and `cockpit.ps1` by two tests —
so if the mirror carries local edits to `cockpit.ps1`, diff before overwriting.

## 4. The one thing to send back

`contrib/mission_bench.py --runs-root <runs> --json` from the work machine.
Still outstanding from the 0.15.0 response, and the byte counts already settled
what to build — this is for the two questions a synthetic fixture cannot answer:
the cold cost on a real retained history, and how many run dirs have actually
accumulated. The `--json` output is sanitized and sendable.

## 5. Verification checklist

Run in order; stop at the first failure.

```
.venv\Scripts\python.exe -m pytest                             # full suite
python contrib\torture_suite.py                                # engine paths: expect 6/6
python contrib\replay_suite.py                                 # expect 1/1
.venv\Scripts\lockstep.exe run flows\selftest-replay.tg.json   # zero-token doc self-check
.venv\Scripts\lockstep.exe doctor                              # after the --safe-mode flip
python contrib\portability_check.py --python 3.13              # checks HEAD; commit first
```

`doctor` is the one that matters after §2.1: it probes the stanza as argv, which
is exactly what `--safe-mode` changed. Expect a one-time "stanza(s) … changed
since the last successful probe" advisory on the first run after the flip — that
is the digest noticing, not a fault.

Then, on a flow the mirror actually runs:

```
.venv\Scripts\lockstep.exe verify <flow> --lint     # new advice on mutating maps (§3.3)
.venv\Scripts\lockstep.exe run <flow> --estimate    # spends nothing
```
