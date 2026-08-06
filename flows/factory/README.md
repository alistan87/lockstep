# Factory taskgraphs

The **production** shapes: repeatable runs over many items or many weeks,
where `flows/starter/` builds one thing per run. Designed and adopted via
`docs/proposals/PROPOSAL-factory-programme.md`; every flow follows the house
grammar — deterministic checks before model judgment, adversarial review
before gates, approvals decided from rendered evidence — and every gate here
is a tested program from `src/lockstep/gates/` (see FLOW-AUTHORING's gate
library table), never an inline one-liner.

Custom contracts live in `flows/factory_contracts.py` (each flow's
`contracts_module`). The deterministic collectors these flows share are
`contrib/collectors.py` subcommands.

## The flows

| Flow | Spends tokens | What it does |
|---|---|---|
| `release-cut.tg.json` | up to ~6 | `--arg tag=v0.x.y`: git-log collect → readonly changelog draft to `CHANGELOG.draft.md` → **version-sync gate** (`__version__` vs pyproject vs changelog vs tag — the defect class r7 itself shipped; no heal, drift is a human decision) → wheel build + scratch-venv install + import + `--help` smoke (a Verdict gate) → evidence approval over the draft → `git tag`. Nothing is pushed; folding the draft into the real changelog is the human's. |
| `codemod-propose.tg.json` | 1/site + 1 | Segment 1 of 2. `--arg instruction= --arg pattern=`: content-grep discovery (fingerprinted per file) → one readonly **ChangeOrder** per site → orders published to `codemod-orders.json` (no-ops dropped) → `CODEMOD-PROPOSAL.md` for the human. Nothing is edited. |
| `codemod-apply.tg.json` | 1/order + review | Segment 2 of 2. Running it IS the approval act (the docs-okf pattern), so it opens with the **staleness gate**: every order's fingerprint recomputed; any drift since the human read the proposal hard-blocks. Then serialized appliers → healing pytest gate → adversarial diff review → block-on-major. Everything stays uncommitted; the commit is the human's second gate. |
| `triage-intake.tg.json` | 1/report + 1 | `--arg reports=<file.json>` (`{"reports": [...]}`): one triager per report ATTEMPTS reproduction and emits a TriageRecord — the exact repro command is the evidence — then records publish to `triage-records.json` and a readonly digest closes. Feeds `starter/bugfix-heal` one record at a time. |
| `research-report.tg.json` | up to ~40 | `--arg brief=` (+ `sources_dir`, default `sources/`): fingerprinted source manifest → per-source readonly extraction (cached per content fingerprint) → outline as JSON → arbiter gate (heals once) → per-section readonly drafts, every claim tagged `[S#]` → the deterministic **citation-integrity gate** (heals the draft map ONCE — heal re-bills all sections, so past that the findings go to a human) → adversarial claim check → arbiter → editor to `report.md` → approval over the report itself. |
| `status-digest.tg.json` | up to ~6 | `--arg days=` (default 7): three deterministic collectors → narrative to `digest.md` → the **number-provenance gate**: every numeral in the prose must appear in a collector's output (heals once) → approval. Schedule it weekly. |
| `run-postmortem.tg.json` | up to ~6 | `--arg run_dir=`: mechanical facts over a FOREIGN run dir (statuses, attempts, errors, invalidation reasons, the verify-trace outcome) → analyst writes `POSTMORTEM.md`, every claim citing `[artifact: <relpath>]` → the citation gate checks each path exists (heals once). Read-only against the target run. |
| *(generated)* `harness-bakeoff` | 1/(stanza×task) + 1 | `python contrib/bakeoff_gen.py` regenerates it from your `lockstep.toml`. `doctor` catches FLAG drift after a harness upgrade; this catches QUALITY drift — run both. |

## Notes

- **Fixtures**: after each flow's first successful live run, export it —
  `python contrib/export_fixture.py runs/<run> tests/fixtures/replay/<flow>` —
  and it joins `contrib/replay_suite.py`'s zero-token regression net. Review
  the kept files before committing; they are model output.
- **Approvals** follow the cockpit rules: evidence rendered by
  `contrib/render_evidence.py` from the real artifact, only seconds-long
  shell downstream of the approval, non-TTY auto-reject (exit 6) as the
  handoff signal. The codemod pair has NO in-flow approval by design.
- The flows write their drafts at the repo root (`CHANGELOG.draft.md`,
  `report.md`, `digest.md`, `POSTMORTEM.md`, `CODEMOD-PROPOSAL.md`,
  `codemod-orders.json`, `triage-records.json`) — deliverables and handoff
  artifacts, yours to commit or discard.
