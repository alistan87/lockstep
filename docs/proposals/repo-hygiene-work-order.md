---
type: plan
title: "Work order: `repo-hygiene` — audit, weekly maintenance, and consolidation flows for the work repo"
resource: docs/proposals/repo-hygiene-work-order.md
status: stable
---
# Work order: `repo-hygiene` — audit, weekly maintenance, and consolidation flows for the work repo

**Status:** handoff plan for a coding agent (Claude Code) building against
the frozen lockstep spec + cockpit proposal rev 5. JSON below is
**schema-approximate** — node stanza fields conform to FLOW-AUTHORING.md
and the frozen SPEC wherever they disagree (named assumptions, §10).
Flows live at `flows/repo-hygiene/`. Everything here follows three
standing principles:

1. **Cost escalation:** deterministic catalog → rule engine → model
   judgment only for the ambiguous residue. Thousands of files, but only
   hundreds ever reach a spawn.
2. **The model authors the manifest; deterministic code executes it.**
   No agent ever moves, renames, merges, or edits a repo file. Agents
   emit typed manifest entries; a Python apply engine performs them on a
   git branch.
3. **Git is the second gate.** Every apply lands on a branch
   (`hygiene/<run-id>`, `consolidate/<run-id>`); the human merge review
   is the final approval, for free.

Scope boundary (agreed): **no knowledge extraction here.** This
work order decides where files live and what they are; Chronicle decides
what the graph learns from them. The catalog this builds becomes
Chronicle's corpus manifest; the `mimir:` frontmatter extension carries
provenance *pointers* (node IDs written back by a governed Chronicle
pass), never assertions.

---

## 1. Shared substrate

### 1.1 The catalog — `tools/hygiene/catalog.py` → `catalog.duckdb`

Deterministic, zero model cost, rebuildable from the repo at any time.
Tables (DuckDB; Pydantic models mirror rows):

- `files(path PK, sha256, size, mtime, ext, kind, frontmatter_json,
  okf_type, okf_valid, rule_id, disposition, catalog_run_id)`
  - `kind`: markdown | html | python | json | duckdb | binary | other
  - `disposition`: `conforming` | `auto_fix` | `ambiguous` | `unknown`
    | `excluded` (binaries, `.duckdb`, generated artifacts — resolved
    by deterministic rules ONLY; their content is never model input)
- `links(src_path, raw_target, link_kind, resolved_path, dangling)`
  - `link_kind`: `wikilink` (`[[target]]`, `[[target|alias]]`) |
    `mdlink` (`[text](target)`) — parse both; OKF uses mdlinks, the
    repo history uses wikilinks (assumption A5: confirm dialect).
- `rules(rule_id, pattern, target_dir, okf_type_hint, source_doc,
  source_line)` — parsed FROM the existing skills/rules docs, so the
  docs stay the source of truth and drift is detectable.
- `metrics(week, rule_id, n_conforming, n_auto_fix, n_ambiguous,
  n_unknown)` — the weekly drift control chart (§4).
- `clusters(cluster_id, member_path, similarity, method)` — §5 only.

### 1.2 OKF conformance — vendored, pinned

- Vendor the OKF spec (v0.2, from GoogleCloudPlatform/knowledge-catalog
  `okf/SPEC.md`) into `tools/hygiene/okf/SPEC-v0.2.md` and pin it: the
  spec is young and moving; conformance means "against the vendored
  version," bumped deliberately. (Assumption A2.)
- `tools/hygiene/okf/models.py` — Pydantic: required `type`; optional
  `title, description, resource, tags, timestamp`; **extra fields
  permitted** per spec.
- The Mimir extension rides in a namespaced block:

```yaml
type: memory-atom            # OKF required field
title: CX-09 seasoning baseline exclusion
tags: [spc, chamber-matching]
timestamp: 2026-08-02
mimir:
  concept_id: <content-derived, stable across moves>
  lens_hints: [spc-runtypes]
  provenance_for: []         # graph node IDs — written ONLY by a
                             # governed Chronicle pass, never by
                             # hygiene flows. Hygiene creates the key,
                             # leaves the list empty.
```

- `okf_type` taxonomy for this repo (rule-doc-owned, seed set):
  `memory-atom | proposal | plan | wiki | theory-of-ops | report-html |
  extracted-artifact | script | script-oneoff | template | data`.
- Bundle boundaries = lens/domain boundaries; each bundle directory
  gets the OKF manifest file per spec.

### 1.3 The manifest contract — what model spawns emit

JSONL, one entry per file, Pydantic-validated:

```json
{"path": "notes/tmp/chamber_notes3.md",
 "action": "move",              // keep | move | rename | inject_okf
                                 // | move+inject_okf | flag
 "target_path": "atoms/spc/chamber-notes-cx09.md",
 "okf_frontmatter": { "type": "memory-atom", "...": "..." },
 "confidence": "high",          // high | low — low is exhaustively
                                 // listed in approval evidence
 "rule_ref": "R-014",
 "why": "one line, <=120 chars"}
```

`flag` = "a human should look" (rule conflict, can't classify) — never
guessed around.

### 1.4 The apply engine — `tools/hygiene/apply.py` (deterministic)

Input: validated manifest + catalog. Actions, in order, on a fresh
branch: `git mv` per move/rename → rewrite ALL inbound links (both
dialects, from `links`) → inject/merge frontmatter **preserving the body
byte-exact** → write OKF bundle manifests → commit with trailers
(`Hygiene-Run:`, `Flow-Hash:`, `Manifest-Sha:`). Then `verify.py`:
re-inventory, assert **zero dangling links**, zero disposition
regressions, manifest fully applied. Verify failure = hard fail, branch
left for autopsy, nothing merged.

---

## 2. Flow 1 — `hygiene-audit-propose.tg.json` (segment 1 of 2)

| # | node | kind | class | contract / notes |
|---|------|------|-------|------------------|
| 1 | `build-catalog` | shell | — | `catalog.py --full`; emits mission counters (§6) |
| 2 | `run-rules` | shell | — | rule engine assigns dispositions; conflict report to `rules-conflicts.txt` |
| 3 | `clarify-rules` | gate (heal 0) | — | ONLY if conflicts exist: one-line `category:"question"` findings ("R-014 and R-031 both claim `reports/*.html` — which wins?"). Answers steer into `run-rules`'s successor per §A.1; journaled. |
| 4 | `classify-batches` | harness **map** | `bulk` | fan-out over `ambiguous ∪ unknown` in batches of 25. Input per spawn: rules doc + batch file heads (first ~80 lines each; `excluded` kinds never read). Output: manifest JSONL for the batch. MUST emit a progress checkpoint every ~5 files (cockpit heartbeat, §6). |
| 5 | `validate-manifest` | gate (heal 2) | machine | deterministic: schema, path collisions, targets legal per rules, link-rewrite feasibility, no writes outside repo, OKF frontmatter validates, dedupe, deterministic ordering (idempotency). |
| 6 | `review-sample` | gate (heal 2) | `strong` | adversarial sample review: every `low` confidence entry + random 5% of `high`; blocks on misclassification patterns, names the rule gap if the *rules* are wrong (that finding routes to the rules docs, not the manifest). |
| 7 | `render-evidence` | shell | — | B1 extract (§6.3) |
| 8 | `approve-manifest` | approval (terminal) | human | decide from pane |

Budget stanza: `max_agent_spawns: 90` (est. 33–60 map spawns + gates +
heals at ~5k files / ~20% ambiguous).

## 3. Flow 1 — `hygiene-audit-apply.tg.json` (segment 2 of 2)

`preflight` (compute: recompute `Manifest-Sha`, hard-block on
post-approval edit — same tamper check as the ontology flow) →
`apply` (shell: apply engine on branch) → `verify` (shell) →
`report` (shell: summary + cost table via `cost_report.py`). No agent
spawns; runs in minutes. **The merge of `hygiene/<run-id>` is the human
second gate** — the flow never merges.

## 4. Flow 2 — `hygiene-weekly.tg.json`

Same skeleton, delta-only: `build-catalog --diff` (files whose sha256
changed or are new since last catalog run) → rules → *maybe* 1–3
classify spawns → validate → (review gate only if any `low` confidence)
→ evidence → approval → apply segment. Typical week: tens of files,
**< 5 spawns, pennies, minutes**. Additionally appends to `metrics` —
the drift control chart: a rising `ambiguous`-per-week trend for a rule
means the rules docs are drifting from practice again, and the retro
(rev 5 §C) picks it up as a finding *before* the next mass cleanup.
Budget: `max_agent_spawns: 10`.

**Unattended note:** the weekly *propose* segment is the natural first
qualification candidate under rev 5 §D (intermediate gates only, tiny
blast radius, weekly cadence generates concordance samples fast). The
apply segment's branch commit is a write outside `runs/` = egress =
always human (U-B4). Do not wire this until §D's step 6b exists;
recorded here so the flow is authored unattended-compatible (no clarify
gate in the weekly path — rule conflicts `flag` instead of asking).

## 5. Flow 3 — `consolidate-campaign.tg.json` (separate; gated)

**Gate to run at all:** first audit merged + two clean weekly cycles.
Consolidation is destructive-adjacent and runs as an occasional
campaign, never weekly.

| # | node | kind | class | notes |
|---|------|------|-------|-------|
| 1 | `cluster` | shell | — | deterministic: minhash over shingles + (optional) VSS embeddings already in stack; emit clusters ≥ threshold; singletons never proposed |
| 2 | `propose-per-cluster` | harness map | `strong` | per cluster, propose ONE of: `merge` (true near-dupes / superseded drafts — draft the merged doc, list tombstones), `hub` (related-but-distinct — draft an OKF hub/index concept linking members; members untouched), `leave` (with why). Atomicity bias stated in the contract: memory atoms default to `hub`, never `merge`, unless near-duplicate. |
| 3 | `validate-consolidation` | gate (heal 2) | machine | every tombstone is a redirect stub (frontmatter `type: redirect`, `mimir.concept_id` preserved, body links to successor); ALL inbound links resolve post-apply (via `links`); no content loss (merged doc must contain every member's non-duplicate sections — checked by anchor coverage) |
| 4 | `review-consolidation` | gate (heal 2) | `strong` | adversarial: provenance survival, over-merge (distinct concepts collapsed), hub quality |
| 5 | `render-evidence` → `approve` | | human | evidence: per-cluster verdicts, full member lists, every `merge` shown with its tombstones; `hub` shown as the hub doc itself |

Apply segment: same engine + branch (`consolidate/<run-id>`), merge is
gate two. Budget: `max_agent_spawns: 40`.

---

## 6. Cockpit view for these taskgraphs (rev 5 §A.3 applied)

### 6.1 Tabs and MISSION

- Tab: `repo-hygiene · seg 1 of 2` → `· seg 2 of 2` (one deliverable
  lineage per rev 5); weekly runs headless by default — cockpit
  optional, attach on demand via `start-cockpit.cmd`.
- MISSION DE-tier gains **flow-provided counter lines** via a small,
  additive cockpit extension: any node may write
  `phases/<node>/mission.txt` (one line, plain text); the DE-tier
  renderer includes the newest per node verbatim. This stays mechanical
  — file copy, no model — same trust status as the rest of the tier.
  `build-catalog` and the map node use it:

```
repo-hygiene · seg 1 of 2
catalog ........ done — 5,214 files, 18,402 links, 4,102 resolved by rules
classify ....... running — batches 12 of 33, 1 redone
needs judgment . 812 files
agent tasks used 14 of 90
```

### 6.2 ACTIVITY

Map fan-out rule applies: ONE newest-active-batch pane, tailing that
batch spawn's `progress.jsonl`. **Contract requirement on
`classify-batches` (restated):** a checkpoint every ~5 files
(`"batch 12: 15/25 classified"`) — without it the pane is blank for a
minute-plus per batch and blank-never-means-dead is violated. Heal
rounds on gates 5/6 show as the frontier alternating per §A.3; MISSION
carries `rework (1 of 2)`.

### 6.3 APPROVAL evidence (B1 extract design — the hard one here)

The manifest is hundreds of entries; the pane cannot show it all and a
stats-only summary hides exactly what needs eyes. The extract is
therefore stratified, in this order:

1. Counts per action per okf_type (one table, ~10 lines).
2. **Every folder-level move/rename, exhaustively** (structural changes
   are few and high-consequence).
3. **Every `low` confidence entry and every `flag`, exhaustively** —
   these are the decision; if this list is long, that is a finding
   against the rules, and the right DE action is `r`.
4. Random sample of 10 `high` entries (spot-check honesty).
5. Link-rewrite count + "verify will assert zero dangling."
6. Pointer: full manifest at `<path>`; the git branch diff is the
   second look.

Briefing line in the pane: *decide from this pane; the merge review is
your second chance, not your first.*

### 6.4 Clarify + journal

`clarify-rules` findings are one-line rule conflicts — relayed with the
verbatim finding per §A.1, journaled as `clarify` triples; the answer
("R-031 wins for reports") is *also* a rules-doc edit candidate, which
the orchestrator notes for the next improvement batch rather than
editing live. `handoff`/`consent`/`stop` journal entries standard.

---

## 7. Executor classes (Addendum B — names, not models)

`lockstep.toml` maps; flows carry only classes:
- `bulk` — high-volume classification. Current intended mapping:
  GPT-5.6 Luna via github-copilot in pi (verify the enterprise admin
  has enabled the GPT-5.6 policy — off by default; assumption A3).
  Contract discipline: tight JSONL schema (Luna is verbose; use
  grammar-constrained output where the harness supports it).
- `strong` — review gates, consolidation proposals, rules
  reconciliation. Operator's strong-class mapping.

## 8. Cost & budget summary (units policy: spawns primary, $ notional)

| Flow | Spawns (est.) | Notional $ | Wall time |
|---|---|---|---|
| Audit (first run, ~5k files) | 40–80 | ~$5–15 (bulk share <$2) | 1–3 h |
| Weekly | 1–5 | <$1 | minutes |
| Consolidation campaign | 15–40 | ~$5–20 (strong-heavy) | ~1 h |

## 9. Test plan

- **Fixture:** `tests/fixtures/mini-repo/` — ~60 files covering every
  `kind`, both link dialects, a rule conflict, a near-duplicate pair, a
  superseded draft chain, a binary, a `.duckdb`.
- Determinism: same repo state ⇒ byte-identical catalog rows (sorted)
  and identical manifest given identical spawn outputs; manifest sha
  stable.
- Link integrity property test: for any manifest the validator passes,
  apply+verify yields zero dangling links (run over randomized
  fixture manifests).
- Idempotency: re-running audit on an applied repo proposes zero
  actions.
- Byte-exactness: frontmatter injection never touches the body
  (sha of body region pinned).
- Tamper: post-approval manifest edit ⇒ preflight hard-block.
- Planted defect (pilot criterion d, hygiene edition): one seeded
  misclassification (`script-oneoff` routed into `atoms/`) at `high`
  confidence must be caught by `review-sample`'s random stratum — a
  miss files against the sampling design.
- Consolidation: anchor-coverage check catches a dropped section;
  tombstone resolution test; atom-merge attempted ⇒ contract steers to
  `hub`.
- OKF: validator agrees with vendored SPEC-v0.2 on the sample bundles
  from the reference repo.

## 10. Named assumptions (resolve before/while building)

- **A1** — node stanza field names conform to the frozen SPEC +
  FLOW-AUTHORING.md; `mission.txt` is a new additive cockpit extension
  (display-only) — if any part requires engine change, stop and raise.
- **A2** — OKF v0.2 vendored + pinned; spec is a young draft — bumps
  are deliberate, diffed, and re-run the OKF validator suite.
- **A3** — `bulk`→gpt-5.6-luna mapping requires the Copilot enterprise
  GPT-5.6 policy enabled (off by default); fallback mapping = the
  cheapest currently enabled class; flows are mapping-agnostic.
- **A4** — wikilink dialect: confirm Obsidian-style `[[page|alias]]`
  incl. heading/block refs (`[[page#h]]`) before writing the parser.
- **A5** — repo is a git repo with clean-enough status to branch;
  apply refuses to run on a dirty working tree.
- **A6** — rules docs are parseable into `rules` rows; where prose
  resists parsing, the parser emits a `flag` rule and the clarify gate
  asks — the docs get *more* structured over time, never bypassed.

## 11. Build order

1. Vendor OKF spec + `okf/models.py` + validator (tests).
2. `catalog.py` (walk, hash, frontmatter, both link parsers) + tests.
3. Rule-doc parser + rule engine + dispositions + conflict report.
4. Manifest Pydantic schema + `validate-manifest` checker.
5. Apply engine + verify + trailers (fixture round-trip green).
6. Flow JSONs: audit propose/apply; flow verification passes.
7. `mission.txt` cockpit extension (display-only) + counter lines.
8. Weekly diff mode + `metrics` chart + weekly flow.
9. Consolidation: clustering + flows (gated; build last).
10. Live smoke on a repo *copy*, never in place, before the first real
    run — first real run is attended, in the cockpit, with the plant.
