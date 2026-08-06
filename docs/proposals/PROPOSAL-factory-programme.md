---
type: proposal
title: "Proposal: the factory programme — explainable caching, mechanised discipline, and seven production flows"
description: Post-r7 work order in four parts — engine explainability and hygiene (A), a tested gate library and authoring aids (B), operator attention signals (C), and seven generic production flows for a software/report factory (D).
resource: docs/proposals/PROPOSAL-factory-programme.md
status: stable
---
# Proposal: the factory programme

**Status: adopted and implemented, 2026-08-05.** Proposed 2026-08-04;
implemented in four batches (§8's order held), then adversarially reviewed —
a spec audit against SPEC r3 + r4/r5/r6 + DEVIATIONS, and an adversarial
review of the full diff — with every surviving finding fixed and pinned by a
test. Departures from the plan as written are recorded in §10. Extends nothing
and supersedes nothing: r7 (commit `44ab071`) and the cockpit UX programme
(`PROPOSAL-cockpit-ux.md`) stand as shipped.

**Scope:** `src/lockstep/` (additive only), `contrib/`, `flows/`, `tests/`.
No `format_version` change. No new runtime dependency. No frozen surface
moves — §7 accounts for every near-miss. No spec amendment is authored here;
the r7 amendment text that `ROADMAP-NOTES.md` still owes (§8.5 argv rule, §7
raw-string rendering, heal-text persistence) is separate work and stays owed.

---

## 1. Why now

Every item below traces to something that happened, not something imagined.
The evidence, first, in the order the parts answer it:

| # | Finding | Evidence |
|---|---|---|
| E1 | A wrongly re-billed node is *silent* — it looks like an ordinary cache miss. The heal-text defect re-ran every heal-touched node in a completed prefix on every resume, and was found only because a long chain made the waste unmissable. | `ROADMAP-NOTES.md` (2026-07-27 entry: "It is silent — the node looks like an ordinary cache miss"); `compose_hash` discards its labelled parts at `src/lockstep/state.py:34` |
| E2 | The rules that prevent known flow-authoring bugs live in prose. A healing clarify gate "would be a bug" (a flow description says so); nothing non-trivial may follow an approval (a FRAGMENT description says so); map items need content fingerprints or caching goes stale (a NOTE in a description says so). Descriptions are read by people who already know. | `flows/starter/clarify-gate.tg.json`, `flows/starter/evidence-approval.tg.json`, `flows/starter/file-audit.tg.json` (descriptions) |
| E3 | The deterministic gates are ~20-line Python programs embedded in single JSON strings, near-duplicated across flows. They are the least reviewable, least testable artifacts in the repo, and a typo ships silently until a live run pays for it. | `flows/starter/sdlc-e2e.tg.json:99` (the `checks` gate), block-on-severity variants in `sdlc-e2e`, `implement-heal`, `bugfix-heal` |
| E4 | `--replay` exists but nothing sweeps it. "All 18 flows still verify" is a static claim; nothing re-executes a flow against recorded results after an engine or flow change, so interpolation and contract drift is caught live, at token cost. | r7 commit `44ab071` (replay shipped as a flag); no caller in `tests/` or `contrib/` |
| E5 | "Run doctor weekly" is discipline, not mechanism — the exact failure mode the cockpit programme spent three tiers eliminating elsewhere. Doctor records nothing, so staleness is undetectable even in principle. | `CLAUDE.md` ops note; `src/lockstep/doctor.py` (no persisted record) |
| E6 | `runs/` holds prompts, diffs, and model output — sensitive by the repo's own designation — and grows without bound. Deleting by hand silently degrades `--estimate`, which mines those same directories. | `CLAUDE.md` ops note; `estimate_flow` at `src/lockstep/estimate.py:101` |
| E7 | The prior-run cost band is computed twice. `estimate.py` computes a floor; `contrib/plan_card.py` independently computes the range it shows the DE. Two implementations of the one number the protocol says must never be quoted from memory. | `src/lockstep/estimate.py:146`; `PROPOSAL-cockpit-ux.md` §T1.9 |
| E8 | The cockpit is poll-and-watch. A domain expert who steps away learns that the run needs them — approval runnable, clarification blocked, run failed — only by looking. Detached runs were the point; unattended *waiting* was never addressed. | `contrib/cockpit.ps1` (all surfaces are pull); `PROPOSAL-unattended-mode.md` deliberately deferred the harder problem |
| E9 | The starter flows build one thing well. Nothing covers the *factory* shapes — release cutting, bulk transformation, intake triage, recurring reporting — even though the repo has already run two of them by hand (`docs-okf-propose`/`apply`, the v0.3.1 bundle build) and shipped a version-sync defect (`__version__` 0.2.0 vs pyproject 0.3.1) that a two-line gate would have caught. | `flows/repo-hygiene/`, r7 commit message ("`__version__` was 0.2.0 against pyproject's 0.3.1") |

Two of these are classes, not bugs. **E1 is an explainability gap:** the engine
makes a caching decision worth real money and keeps no record a human can
interrogate. **E2/E3 are the cockpit's founding argument applied to flow
authors:** discipline encoded as prose is not a mechanism, and the people who
wrote the prose are the only ones it protects.

---

## 2. What binds every change

Inherited and non-negotiable; each item in §§3–6 is to be read against them:

1. **`pydantic` remains the only runtime dependency.** Everything below is
   stdlib, PowerShell built-ins, or existing seams.
2. **Frozen surfaces do not move.** Exit codes (`__init__.py`, SPEC §3),
   `format_version` 1.x, §7 fencing/footer, M3 hash composition, every stated
   spec guarantee. §7 of this document walks each near-miss.
3. **The mechanical tier stays mechanical.** New operator-facing surfaces
   (§5, §6's approval evidence) are field mappings and file reads, never
   narration. A view never takes the run down: L-B2 reader discipline applies
   to every new reader without exception.
4. **Advisory means advisory.** Nothing in this proposal converts a warning
   into a block or adds an exit code. A lint that blocks is a §6 verifier
   change, which is spec work, which this is not.
5. **TDD per SPEC §14; full pytest after every change; every flow passes
   `lockstep verify`** before and after.

---

## 3. Part A — engine and CLI: explainability and hygiene

### A1. `lockstep explain` — the cache-miss explainer (E1)

**The change.** At plan time the runner already assembles the labelled
fingerprint parts it feeds to `compose_hash`. Persist them: `PhaseRecord`
gains an optional `hash_parts: dict[str, str] | None` — **label →
sha256(part)**, never the raw part, because parts contain full prompts and
upstream results, and `state.json` should not become a second copy of
sensitive text. Labels name the part at the site where the runner appends it
(prompt render, upstream result per edge, stanza digest, steer mailbox, heal
text, workspace fingerprint, …). Map items record per-item parts on
`ItemRecord` the same way.

A new subcommand:

```
lockstep explain <run_dir> <node> [--against <other_run_dir>]
```

re-plans the node from the run dir's archived flow (the same source `resume`
uses, §9.2) and prints one line per label: `match`, `DIFFERS`, or `only in
<side>`. Without `--against` it compares the current plan against the lineage
head's recorded parts — the exact question "why is this node about to
re-bill". With `--against` it compares two recorded runs — the exact question
"why did yesterday's resume re-run the prefix".

**What it would have caught.** The heal-text defect (E1) on first occurrence:
`explain` would have printed `heal-text: only in <old run>` instead of a week
of silent re-billing. The per-stanza-digest motivation (r5 B1, the Haiku 529
story) is the same question one revision earlier.

**Guard rails.** M3 composition is untouched: the recorder is a spectator on
the inputs to the *existing* function. A test pins that the recorded part
digests, recomposed through `compose_hash`, reproduce the node's recorded
`input_hash` exactly — recorder drift then fails loudly instead of lying
quietly. Runs predating the field render as `unrecorded (pre-A1 run)`,
following the `UNCHAINED` precedent from trace chaining. `hash_parts` is
optional-with-default, so old `state.json` files load unchanged. Exit
behaviour: `0` for any successful report (including "everything differs");
missing run dir or unknown node reuse the codes `status` already produces for
the same situations. No new exit code.

### A2. `lockstep verify --lint` — anti-patterns out of prose, into the tool (E2)

**The change.** A `--lint` flag on `verify` that emits **advisory warnings**
(`W`-codes) after the §6 errors. Warnings never change the exit code: a flow
with lint findings and no §6 errors still exits 0. Flow-only lints run
always; lints needing executor config run only when `--config <lockstep.toml>`
is passed, and `--lint` says which lints were skipped when it is not — a lint
silently not run reads as "clean", which is the F-series lesson.

The opening set, each anchored to a recorded incident or a shipped rule:

| code | warning | anchor |
|---|---|---|
| W1 | a harness node is reachable strictly downstream of an approval | evidence-approval's rule: everything after the approval runs in the human's own resume process, so only seconds-long shell nodes may follow |
| W2 | a map's `over` references a `PathManifest` node — reminder that per-item cache keys are the item *strings*; bare paths do not invalidate on content change | file-audit's `path|fingerprint` convention, currently a NOTE in a description |
| W3 | a flow contains a map node but no `budget.max_agent_spawns` | map width is data-dependent at runtime; the budget is the only ceiling |
| W4 | *(config)* a stanza reached by this flow uses `prompt_via = "argv"` | the 59,028-char corrective-prompt incident; `ArgvTooLong` now fails cleanly, but stdin removes the ceiling entirely |
| W5 | *(config)* a readonly map node with `concurrency > 1` resolves to a stanza with no `readonly_argv` | file-audit's NOTE: audits then serialize or worse |

The set is deliberately small and deliberately versioned in one table in
`FLOW-AUTHORING.md`; a lint nobody can cite is a lint that gets argued with.
New lints require the same standard as these five: a recorded incident or a
shipped rule, named in the table.

### A3. Replay regression suite and scrubbed fixtures (E4)

**The change.** Two contrib tools, no engine change:

- `contrib/export_fixture.py <run_dir> <dest>` — copies the **allowlist
  only**: `flow.tg.json`, `state.json`, and each node's result channel
  (`result.json` / `result.txt`). Prompts, diffs, journal, rotated attempts,
  workspace snapshots — everything else — are dropped. The tool prints every
  file it kept and refuses binaries. What it keeps is still model output:
  the tool ends by saying so, and review-before-commit stays a human act.
- `contrib/replay_suite.py [--flows <dir>] [--fixtures <dir>]` — for every
  flow with a pinned fixture, runs `lockstep run <flow> --replay <fixture>`
  and reports pass/fail. Zero tokens by construction. Strict `input_hash`
  matching is the point — a hash mismatch after an engine change is exactly
  the regression being hunted — with `--replay-any` available per fixture
  only as an explicit, named exception.

Pinned fixtures for the starter flows go under `tests/fixtures/replay/`
(scrubbed, reviewed) and `replay_suite` joins the test suite as a marker-gated
job. "All flows verify" becomes "all flows execute against recorded reality"
without a token spent.

### A4. Doctor staleness — the weekly habit gets a record (E5)

**The change.** A successful `doctor` writes `<runs_root>/doctor-record.json`:
timestamp, per-stanza config digest (the r5 B1 digests, reused), probe
outcome. At `run` start the driver prints **one advisory line** when the
record is missing, older than `[doctor] max_age_days` (default 7,
`lockstep.toml`, local), or when any stanza's current digest differs from the
recorded one — the harness-upgrade case the ops note exists for. Never blocks,
never spawns a probe (doctor spends model calls; a run start must not).
A1 of the amendments stands: still not a pre-commit hook.

### A5. `lockstep gc` — estimate-aware retention for `runs/` (E6)

**The change.** `lockstep gc [<runs_dir>]` lists deletion candidates and
exits; only `--apply` deletes. Retention, all conditions conjunctive to
*keep*:

- the newest run of every `flow_hash`, always;
- the newest `N` runs per `flow_hash` (default 5) — precisely the history
  `estimate_flow` mines, so `--estimate` and `plan_card` never silently
  degrade;
- anything younger than `M` days (default 14);
- any run dir holding a live lockfile, an unanswered approval, or a
  `rejection.txt` (human-authored artifacts are not the engine's to expire).

Defaults live in `lockstep.toml` `[gc]`. The dry-run prints, per candidate,
which rule failed to protect it — a deletion the operator cannot explain is a
deletion that should not happen. Estimate degradation stays visible rather
than silent regardless: `estimate` already names nodes with no history.

### A6. One cost band, one implementation (E7)

**The change.** `estimate.py` grows the band it already half-computes:
`Estimate` carries min/median/max and `n` per node and per flow;
`render_estimate` shows "11–19 agent tasks over 3 runs (median 14)" instead
of a bare floor. `contrib/plan_card.py` then **consumes** the estimate output
instead of re-mining run dirs itself. The DE-facing number and the operator
`--estimate` become the same computation — the "never quote a cost from
memory" rule now has exactly one artifact behind it, produced by exactly one
implementation.

---

## 4. Part B — authoring: the gate library and composition

### B1. `contrib/gates/` — tested programs instead of embedded one-liners (E3)

**The change.** The deterministic gates move out of JSON strings into real,
unit-tested files, called by path. The r7 raw-string argv fix is what makes
this clean: interpolated paths and values now arrive unquoted in shell argv,
so a gate invocation is an ordinary command line.

The opening library — each emits a built-in contract on stdout, documents its
argv, and carries tests in `tests/test_gates.py`:

| gate | emits | replaces / enables |
|---|---|---|
| `pytest_verdict.py` | `Verdict` | the inline lint+pytest program in `sdlc-e2e` `checks` and `implement-heal` |
| `block_on_severity.py --at major <findings-path>` | `Verdict` | the thrice-duplicated Finding[]-to-block gate |
| `required_sections.py <doc> <section>...` | `Verdict` | `proposal-gate`'s section check |
| `version_sync.py` | `Verdict` | the r7 version-drift class (E9), for D1 |
| `citation_check.py <doc> --sources <manifest>` / `--paths <root>` | `Verdict` | for D5 and D7: every cited id resolves, every section cites |
| `numbers_check.py <doc> --from <collector.json>...` | `Verdict` | for D6: every numeral in the prose exists in a collector's output |
| `coverage_delta.py --baseline <json>` | `Verdict` | future coverage-hardening work |

The three starter flows that embed duplicates are rewritten to call the
library. Flows are templates, so the new-lineage consequence of editing them
(§9.2) costs nothing standing. One implementation, diffable, testable — and
the flows become readable enough to review.

**Boundary.** Gate *scripts* are deterministic tools, not policy: severity
thresholds and section lists stay in the flow file where the author and the
reviewer can see them, passed as argv.

### B2. `contrib/compose_flow.py` — fragments become includes (deliberately last)

`clarify-gate` and `evidence-approval` are shipped as FRAGMENT flows meant to
be copied by hand. A small composer — splice a fragment's nodes into a host
flow under an id prefix, rewire the named edges, emit a plain
`format_version: 1.0` file that must then pass `verify` — makes them actual
includes with no engine change and no new format. Scoped to exactly that:
id-prefix, edge-splice, verify. If it wants to grow templating features, the
answer is no; the taskgraph format staying dumb is a feature this repo has
already paid for. Sequenced last in §8 and droppable without harming the rest.

---

## 5. Part C — operating: the attention watcher (E8)

**The change.** `contrib/attention.ps1 -RunDir <run> [-WebhookUrl <url>]` — a
poll loop (interval ≥ 2 s, L-B2 discipline: shared-read opens, length
regression ⇒ reopen, partial trailing line tolerated, every error
display-only) that fires a notification on **transitions only**:

- `quiescent.py` flips to exit 0 — the approval is runnable, and *only* it;
- a gate blocks with `question`-category findings — `question_card.py` has
  content for the DE;
- the run process exits, any code — success, block, and failure all end the
  waiting;
- state reports a node failed with retries exhausted.

Notification is a Windows toast via the WinRT API PowerShell already exposes
(no module, no dependency), with a console-bell-plus-line fallback, and
optionally a POST to `[notify] webhook_url` from `lockstep.toml` (local,
gitignored, like every other machine-local setting). The payload is
mechanical: run dir, node id, transition name. **No summary field exists** —
a notification that describes the decision is a competing narration of it,
and §2.3 forbids the code path, not just the habit.

The watcher is a *reader*. It cannot answer an approval, cannot steer, cannot
resume; there is nothing to misuse because the paths do not exist — the same
guarantee shape as no-send-text.

---

## 6. Part D — the seven production flows (E9)

The starter set builds one thing per run. A factory runs the same shape over
many items or many weeks. Seven flows, each following the shipped grammar —
deterministic checks before model judgment, adversarial review before gates,
approvals decided from rendered evidence, heal only where re-prompting can
actually fix the target — and each carrying at least one gate anchored to a
recorded incident.

Custom contracts (`ChangeOrder`, `SourceNote`, `TriageRecord`, `ScoreCard`)
ship in `flows/factory_contracts.py` via the existing `contracts_module` seam
(`taskgraph.py:74`). No engine change.

### D1. `flows/factory/release-cut.tg.json`

```
collect (shell: git log since last tag, tags, versions → json)
→ changelog (harness, readonly: draft CHANGELOG entry from collect only)
→ version-gate (shell: gates/version_sync.py — __version__ vs pyproject vs
   intended tag vs changelog heading; Verdict; NO heal — version drift is a
   human decision, block hands it back)
→ build-smoke (shell: build wheel, install into scratch venv, `lockstep
   verify` a starter flow, `--help` smoke → CheckResult)
→ smoke-gate (shell: CheckResult → Verdict)
→ approval (evidence: the changelog entry + artifact sha256 digests,
   --impact "publishes a tag", --reversible "tag deletable until pushed")
→ tag (shell: git tag; seconds-long, W1-clean)
```

The version gate is E9's defect class, mechanised. The repo already builds
bundles by script; this puts gates where its own history says defects live.

### D2. `flows/factory/codemod-propose.tg.json` + `codemod-apply.tg.json`

The `docs-okf-propose`/`apply` pair, generalised from docs to code — the
repo's best-validated shape promoted to a template.

**Propose:** discover (shell: grep manifest, `path|fingerprint` per W2) →
map: per-site readonly analysis emitting a `ChangeOrder` (file, anchor,
before/after intent, risk note) → conflict-gate (shell: overlapping
files/hunks across orders → Verdict) → proposal document (harness), final.

**Apply:** staleness-check (shell: proposal fingerprints still match the tree
— the tree has not moved since the human read the proposal; Verdict, no heal)
→ approval (evidence: the proposal document itself) → map: apply each order
under `spec.writes` scoped to the order's files (r7) → per-item check (shell)
→ suite gate (gates/pytest_verdict.py, heal → apply, 2 rounds) → adversarial
diff review (Finding[]) → block-on-severity gate → report.

The staleness gate is the pair's load-bearing addition: propose/approve/apply
across sessions is exactly where the tree drifts under the approval.

### D3. `flows/factory/triage-intake.tg.json`

load (shell: `args.reports` file → JSON array) → map: per-report reproduction
attempt (harness; emits `TriageRecord`: reproduced verdict, repro command as
evidence, severity, component; `concurrency: 1` — repro executes code, the
tree token serialises honestly) → aggregate (shell: dedupe, sort by severity)
→ digest (harness, readonly), final. The intake side of the factory; its
output feeds `bugfix-heal` one record at a time.

### D4. `flows/factory/harness-bakeoff.tg.json` (generated)

Executor binding is per-node, so a stanza-per-item map is not expressible —
this flow is **generated** from `lockstep.toml` by `contrib/bakeoff_gen.py`:
one work node per (stanza × canned task), then judges (readonly map,
cross-grading, Finding[]), then a deterministic scorecard gate (`ScoreCard`,
advisory verdict), then a report. `doctor` catches *flag* drift after a
harness upgrade; nothing today catches *quality* drift. Run beside doctor
after upgrades. Spends real tokens and says so in its description, like
`audit-spec` does.

### D5. `flows/factory/research-report.tg.json` — the report factory

```
sources (shell: manifest, fingerprinted per W2)
→ map: extract (harness, readonly: per-source SourceNote — claims, quotes,
   ids; cached per source fingerprint, so re-runs re-read only changed sources)
→ outline (harness) → outline-gate (gates/required_sections.py + adversarial
   completeness reviewer + arbiter, proposal-gate's shape)
→ map: draft (one section per outline entry; every claim tagged [S#];
   spec.writes scoped to report/sections/)
→ citation-gate (shell: gates/citation_check.py — every [S#] resolves, every
   section cites at least once; Verdict; heal → draft, 1 round)
→ claim-check (harness, readonly: adversarial — claims the cited note does
   not support; Finding[])
→ arbiter-gate → editor (harness: merge to report.md)
→ approval (evidence: report.md itself), final
```

The citation gate is the cockpit's founding rule — never narrate in place of
evidence — promoted into the flow layer, which is why this flow belongs in
this repo specifically. **Caching note, stated in the flow description:**
heal text folds into the draft map's prompt, so a heal round re-bills every
section, not just the offenders. One round is the ceiling; past it, the
findings go to a human, who steers or fixes sections directly and resumes.

### D6. `flows/factory/status-digest.tg.json` — the recurring report

collectors (parallel shell: git log window, `cost_report.py --compact` per
run dir, run inventory, pytest summary — each emitting JSON) → narrative
(harness, readonly: prose from collector output only) → numbers-gate (shell:
`gates/numbers_check.py` — every numeral in the prose appears in some
collector's output, dates/versions allowlisted; Verdict; heal → narrative, 1
round) → approval/publish. Scheduled weekly. This mechanises "never quote a
cost from memory" for an entire document class: the numbers are mechanical,
the prose is judged, and the gate is what joins them.

### D7. `flows/factory/run-postmortem.tg.json`

facts (shell: extract from `args.run_dir` — statuses, exit codes, attempts,
errors, rotated-attempt inventory, and the `verify-trace` outcome, so the
journal's integrity is part of the record) → analyst (harness, readonly:
post-mortem markdown; every claim must cite an artifact path) →
citation-gate (`gates/citation_check.py --paths <run_dir>`: every cited path
exists) → report, final. The `run-diagnostician` subagent, productised: each
expensive failure becomes a durable, evidence-checked document instead of a
chat transcript. Runs read-only against a *foreign* run dir; L-B2 applies to
the facts collector.

---

## 7. Frozen surfaces — the near-misses, accounted

| surface | status in this proposal |
|---|---|
| Exit codes (SPEC §3, `__init__.py`) | Untouched. `--lint` warnings leave `verify` at 0; `explain` and `gc` reuse existing codes for existing situations; no new code is minted. |
| M3 hash composition | Untouched. A1 records the *inputs* to `compose_hash`; a pinned test recomposes recorded parts to the recorded `input_hash`, so the recorder can never disagree with the composer silently. |
| `format_version` 1.x | Untouched. New flows use existing roles/kinds/keys; custom contracts ride the existing `contracts_module` seam; everything else is CLI flags, contrib, or optional `RunState` fields (additive, default-valued, old runs load). |
| §7 fencing/footer | Untouched. B1 *relies on* the r7 raw-string argv decision already recorded in `DEVIATIONS.md`; it does not extend it. |
| Stated spec guarantees | The A4 advisory line and A2 warnings are additive output; `gc` refuses locked and human-artifact-bearing runs, so restore/resume guarantees are unreachable by it. |

Anything discovered mid-implementation that *would* move one of these stops
the work and comes back as a question, per the working agreement.

---

## 8. Delivery order

Four batches, each independently shippable, full suite green and all flows
verifying at each boundary. Later batches consume earlier ones — the gate
library (B1) is deliberately early because five of the seven flows call it.

| batch | contents | rationale |
|---|---|---|
| 1 | A1 explain, A2 lint, B1 gate library + starter-flow rewrites | Pays off on every subsequent run and every subsequent flow; smallest engine surface first. |
| 2 | A3 replay suite + fixtures, A4 doctor record, A6 one cost band | The regression net goes up before the flow work starts landing on it. |
| 3 | D1 release-cut, D5 research-report, D6 status-digest, C1 attention watcher | The two archetype flows (software factory, report factory) plus the recurring one; the watcher lands with the first flows a DE would actually leave unattended. |
| 4 | D2 codemod pair, D3 triage-intake, D7 run-postmortem, D4 bakeoff, A5 gc, B2 composer | The remaining factory shapes; gc once there is a fixture/estimate story to protect; the composer last and droppable. |

Acceptance, uniformly: TDD per SPEC §14 — the pinning test exists before the
behaviour; `python -m pytest` green after every change; `lockstep verify` (and
`--lint`, once it exists) clean on every shipped flow; new flows come with a
replay fixture from their first successful run (A3) so they join the
zero-token regression net immediately.

---

## 9. What this proposal explicitly does not do

- **No unattended mode.** C1 notifies; it never answers. The deferral in
  `PROPOSAL-unattended-mode.md` stands.
- **No send-text, no new decision surfaces.** Approvals are decided where
  they always were, from `approval-evidence.txt`, in the human's terminal.
- **No new runtime dependency, no new daemon.** The watcher is a script the
  operator runs; the mission server remains the only long-lived contrib
  process, unchanged.
- **No spec amendments.** The owed r7 amendment text (§1) remains owed and
  remains separate; this proposal neither writes it nor blocks on it.
- **No new lint without an incident.** The W-table's admission standard is
  part of the design, not a launch-day artifact.

---

## 10. Departures from this plan

Recorded at adoption, in the manner of `PROPOSAL-cockpit-ux.md` §12. Each
entry says what changed against the text above and why; review-driven fixes
are marked *(review)* — they came out of the adversarial pass this document's
own delivery order demanded.

1. **The gate library lives in `src/lockstep/gates/`, not `contrib/gates/`**
   (§4 B1 said contrib). Starter flows run against ARBITRARY target repos;
   `python -m lockstep.gates.x` resolves wherever lockstep is importable,
   while a contrib path exists only in this checkout. Consequence, found by
   the review as a major: a bare `python` on the spawned PATH often CANNOT
   import lockstep — fixed by resolving a bare `"python"`/`"python3"` argv[0]
   to `sys.executable` at execute time only (the planned argv and every
   `input_hash` keep the portable form). Logged in `DEVIATIONS.md`
   (2026-08-05) and pinned by
   `test_gates.py::test_shell_resolves_bare_python_to_the_driver_interpreter`.
   *(review)*
2. **`explain` does not re-plan** (§3 A1 promised a no-`--against` mode that
   re-plans and compares). Planning outside the engine would duplicate the
   runner's context assembly; instead the DECISION SITE annotates itself —
   `_settle` records the label diff (`invalidated_by`) at the moment it
   invalidates, and the steer/external-edit re-run paths record their reasons
   too, cleared again when a later revalidation matches. `explain` reads
   records only; `--against` diffs two runs as proposed. Strictly more
   accurate than a recomputation after the fact, at the price of answering
   "why DID it re-run" rather than "why is it ABOUT to".
3. **The recompose pin operates on raw parts at record time** — §3 A1's "the
   recorded part digests, recomposed through compose_hash, reproduce the
   input_hash" was unimplementable as written (digests are one-way). The pin
   that exists: recorded digests are sha256 of exactly the list the adjacent
   `compose_hash` call consumed
   (`test_explain.py::test_recompose_pin_recorded_parts_agree_with_the_hash`).
4. **The lint table shifted** (§3 A2): the proposed W5 (readonly map on a
   stanza without `readonly_argv`) was already §6's `readonly-unenforced`
   ERROR; it became `lint-serialized-map` (a parallel map whose non-readonly
   items serialize on the tree token). `lint-map-without-budget` was added;
   `lint-argv-prompt` is the only config-dependent lint. The authoritative
   table is in `FLOW-AUTHORING.md`; the §A2 table above is the proposal-time
   sketch.
5. **E7 was half-stale**: `plan_card.py` already consumed `estimate_flow`
   (fixed at some point after the cockpit-ux snapshot this document quoted).
   A6 therefore became: add the per-run band to the estimator, render it in
   both surfaces, and *(review)* compute the band from the RECORDED states
   (renamed nodes' spend counts for name-matched definitions) with the median
   taken over run totals, not summed per-node medians.
6. **gc ranks lineages by (flow_hash, args), and the newest run of every
   lineage is unconditional** — the proposal (§3 A5, §7) ranked by flow_hash
   and claimed resume guarantees were "unreachable by gc"; the review showed
   both wrong (attachment is keyed per (flow_hash, args), so gc could delete
   a lineage head and silently fork it, and `--keep-per-flow 0` voided
   "newest always"). Also: retention knobs are CLI flags, not a `[gc]` toml
   table. *(review)*
7. **The codemod pair has NO in-flow approval and no conflict gate** (§6 D2
   sketched an approval inside apply). It follows the proven docs-okf shape
   exactly: consent is the human LAUNCHING apply with CODEMOD-PROPOSAL.md in
   hand, and the staleness gate (`gates/fingerprint_check.py`) hard-blocks if
   the tree moved since they read it. The conflict gate died because a
   per-file map cannot produce overlapping orders.
8. **D1/D5/D6 write real files** (CHANGELOG.draft.md, report.md, digest.md)
   rather than passing documents as node results — approval evidence then
   renders mechanically from the artifact itself via `render_evidence.py`,
   and the number-provenance gate reads the same file the human approves.
9. **D4 is a generated flow with one judge node** (not a judges map): executor
   binding is per-node, so `contrib/bakeoff_gen.py` generates
   stanza × task work nodes (ids slug-sanitized and deduped *(review)*) plus
   a single ScoreCard[] judge.
10. **A4's knob fails open**: a garbage `[doctor] max_age_days` coerces to 7
    with a stderr warning instead of hard-blocking every run through config
    validation, and the advisory is skipped for `--dry-run`/`--estimate`/
    `--replay` — zero-token operations are not nagged. *(review)*
11. **No starter-flow fixtures were pinned** (§3 A3 wanted them at delivery):
    producing one requires a live token-spending run of each flow.
    `replay_suite` reports an empty net honestly; the obligation stands —
    every flow's first successful live run should be exported and pinned.
12. **One spec-audit finding was refuted by evidence**: the auditor read the
    broad `except (SkippedReference, InterpolationError, Exception)` in
    `_settle` as smuggled-in scope; `git diff` shows the tuple pre-existing —
    this change added only `as e` and the recording. No DEVIATIONS entry is
    owed there. Recorded because a refuted finding is still part of the
    review's record.
