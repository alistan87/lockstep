# Deviations log

What / why / date, per the SPEC §14 working agreement. Amendments in
`AMENDMENTS-r4.md` are adopted spec, not deviations; this file records
implementation-level departures below that bar.

- **2026-07-25 — `kind: "fake"` is treated as harness-like in verification and
  scheduling** (contributes `tree` unless readonly; allowed as a heal target;
  pools as token-costing when its spec says so). Why: the offline suite (§13.1)
  must exercise heal, exclusion, and corrective-respawn paths without a real
  harness; the fake must therefore be admitted where "harness-kind" is required.
- **2026-07-25 — the run dir carries a copy of the flow file (`flow.tg.json`)**.
  Why: SPEC §3 `resume <run_dir>` takes no flow argument, but the engine needs
  the definitions; a byte-identical copy preserves the flow_hash lineage check.
- **2026-07-25 — `resume` re-marks `blocked` nodes as pending** (spec lists only
  failed/stale-running/pending). Why: after a terminal gate block, a resume with
  nothing re-runnable would re-exit 2 forever; re-running the gate (and its
  blocked dependents) is what a human resuming plainly wants. `heal_round` is
  preserved, so exhausted heal budgets stay exhausted.
- **2026-07-25 — reserved commands (`steer`, `cancel`) exit 7** after printing
  "reserved for v2"; the spec assigns them no exit code.
- **2026-07-25 — approval "edit" reads from the interactive prompt loop**
  (input() until EOF), which on Windows means Ctrl-Z then Enter.
- **2026-07-25 — readonly harness nodes get a variant footer** (found by the
  first live audit-spec run). The §7 standard footer instructs writing
  `result.json`, but a readonly node's `readonly_argv` disables write tools —
  guaranteeing a denied tool call and an empty result. `FOOTER_READONLY` tells
  the node its final response IS the result (the §8.3 stdout fallback channel).
- **2026-07-25 — corrective re-spawns carry context** (same dogfood run). A
  headless harness spawn is stateless; the bare §9.3 wording ("emit the
  corrected JSON for your previous analysis") reaches a fresh session with
  nothing to correct. The corrective prompt now includes the original rendered
  prompt, the invalid output (fenced as data), and the validation error.
  "Output-only" constrains side effects, not context.
- **2026-07-25 — per-attempt artifacts are rotated**, not overwritten
  (`stdout-attempt1.log`, …): attempt 1's output was undiagnosable after the
  corrective attempt overwrote it. §10.1's `stdout.log`/`prompt.txt` names
  still hold the latest attempt.
