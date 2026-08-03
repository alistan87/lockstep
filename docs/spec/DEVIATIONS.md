---
type: register
title: Deviations log
resource: docs/spec/DEVIATIONS.md
---
# Deviations log

What / why / date, per the SPEC §14 working agreement. Amendments in
`AMENDMENTS-r4.md` / `AMENDMENTS-r5.md` are adopted spec, not deviations; this
file records implementation-level departures below that bar.

> **Revision 5 note (2026-07-25):** the entries below for the flow-file copy,
> blocked-on-resume, reserved-command exit code, fake-as-harness-kind, readonly
> footer, corrective-respawn context, and per-attempt rotation were formalized
> into spec text by `AMENDMENTS-r5.md` (A1–A5). They remain here as the
> historical record of when and why each departure happened.

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
  "reserved for v2"; the spec assigns them no exit code. *(Superseded by
  AMENDMENTS-r6 C4: both commands are implemented as of v0.2.0.)*
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
- **2026-07-26 — map item_hash includes the array index** as an extra
  fingerprint part beyond AMENDMENTS-r4 A3.1's list. Why: results are
  positional (collected in array order); two identical items at different
  indices must not share a cache slot. Inserting an item therefore re-runs
  the shifted tail — correct, since slots moved.
- **2026-07-27 — heal text is persisted in `RunState.heal_texts`**, not held in
  the Runner process. Why: the heal text (block reason + fenced findings) folds
  into a target's `input_hash` (§9.4.6, §9.2), but a resumed process re-planned
  the node WITHOUT it, so its hash differed and every healed node re-ran on the
  next resume — silently, indistinguishable from an ordinary cache miss, at a
  cost growing with run length. The spec does not say where heal text lives;
  putting it in run state is what makes §9.2's "skipped when nothing changed"
  true for healed nodes. Deliberately NOT cleared when the gate passes (unlike
  `heal_baselines`): clearing it would change the hash of a result it helped
  produce. Latest round wins. Same reasoning r6 C2 applied to whole-mailbox
  steering; an r7 amendment should state both together. Pinned by
  `tests/test_heal.py::test_healed_node_hash_is_stable_across_resume`.
- **2026-07-25 — per-attempt artifacts are rotated**, not overwritten
  (`stdout-attempt1.log`, …): attempt 1's output was undiagnosable after the
  corrective attempt overwrote it. §10.1's `stdout.log`/`prompt.txt` names
  still hold the latest attempt. *(2026-07-31: `verdicts.jsonl` joined the
  rotation set — unlike the logs it is a GATE INPUT (ADDENDUM-A §A.3.3), so a
  stale block record from a superseded attempt would fail a node that
  succeeded on retry.)* *(2026-08-01: `result.json`/`result.txt` joined too —
  the driver persists the validated result to the SAME path the executors read
  as the §8.3 file-first channel, so without rotation every re-execution
  (retry, heal round, resume of a blocked gate) returned its own previous
  answer: a blocked shell gate could never pass again. Found by the
  starter-flow adversarial review; pinned by `tests/test_result_rotation.py`.)*
- **2026-08-02 — `events.jsonl` lines carry a chain digest `h`**, and
  `lockstep verify-trace <run_dir>` recomputes it. Why: the cockpit's whole
  case rests on evidence a domain expert can rely on, and the journal had no
  integrity check at all — an approval that registered, an evidence copy that
  failed silently, and a journal missing both is a real observed failure. Each
  line's `h` is `sha256(prev_digest + "\n" + line_bytes)` with `h` appended
  last so a verifier can pop it and re-serialize the remaining keys to
  reproduce the exact bytes. Additive: readers that ignore `h` are unaffected,
  and lines predating the change verify as UNCHAINED rather than as broken.
  This is tamper EVIDENCE, not tamper proofing — whoever can rewrite the file
  can re-chain it, which is why the head digest is printed at run end and
  `--head` pins it. §10.3's telemetry text should acquire the rule at r7.
- **2026-08-02 — `emit_span` optionally writes OTLP/JSON**, instead of being a
  pure no-op (SPEC §10.3, §16.3's deferred "OTel exporter"). Why: the GenAI
  semantic conventions stabilized for client spans in early 2026, so there is
  now a real attribute vocabulary to target; writing the envelope by hand
  keeps `pydantic` the only runtime dependency. Off unless `--otel-file` is
  passed. Spans are ADVISORY on the same terms as structured progress (§16.1):
  never an input to scheduling, hashing, gating, budgets, or retries. GenAI
  attributes are attached only to `harness`/`fake` nodes — labelling a shell
  subprocess a model call would corrupt any downstream cost view.
- **2026-08-02 — nodes may declare `spec.writes`**, a repo-root-relative write
  scope, as an optional key inside the per-kind spec model rather than a new
  first-class node field. Why: this is the r7 "dedicated per-node write-scope
  field" candidate, and a first-class field would bump `format_version` 1.0 →
  1.1 (§15) for a feature that per-kind specs already accommodate. Empty means
  unconstrained, so every existing flow is unaffected. The driver DETECTS
  violations after the node finishes (it never sees tool calls) and only while
  the node holds the `tree` token — otherwise a concurrent node's writes would
  be misattributed, and `verify` emits `write-scope-unenforced` rather than
  guessing. Violations fail the node and leave the files in place: rollback
  never deletes (§0.1 item 2). A new `LOCKSTEP_WRITE_SCOPE` env var carries the
  scope as a JSON array; `LOCKSTEP_WORKSPACE_SCOPE` is deliberately UNCHANGED
  because ADDENDUM-A preamble note 2 documents it as a single directory and
  `lockstep-guard.ts` prefix-matches against it.
- **2026-08-02 — `lockstep run --replay <run_dir>`** serves recorded results
  instead of spawning. Why: every node is already content-addressed by
  `input_hash` with its result persisted, so replay is a lookup rather than a
  simulation — it gives zero-token flow regression tests, and it lets a
  failure be reproduced from a run dir someone sent you, which rev 7 lists as
  an irreducible support gap. Implemented as a run FLAG wrapping the existing
  executors, not a new `kind`, so `format_version` does not move. Strict by
  default: a recording whose `input_hash` no longer matches is refused, since
  serving it would turn a regression test green for the wrong reason;
  `--replay-any` relaxes that and logs every stale hit.
- **2026-08-02 — `lockstep run --estimate`** prints a cost floor from prior
  runs before spending anything. Why: the consent beat states a budget in
  "agent tasks", and until now that number came from nowhere. Estimates only in
  units the driver actually owns — token-costing spawns and wall time — and
  says so; harness-reported tokens and dollars stay in `contrib/cost_report.py`
  with the envelope field maps. Reports a FLOOR, never a forecast: nodes with
  no history contribute nothing, and a flow matched only by name (its
  definition has changed) is labelled as such.
- **2026-08-02 — a JSON STRING leaf renders raw into shell argv** (`fence=False`
  in `interpolate.py`), instead of §7's "parsed, compact-re-serialized". Why: an
  argv element is already a discrete string, so the compact-JSON quotes became
  part of the value — a path arrived as `"docs/x.json"` and the program opened a
  file whose name started with a quote, surfacing as a file-not-found far from
  the flow file. Non-string values (numbers, booleans, arrays, objects) still
  compact-serialize, and BOTH prompts (`fence=True`, the §7 footer contract) and
  `when` (`eval_when`, a separate code path) are untouched — so comparison
  semantics and prompt fencing do not move. §7 is a frozen surface: this was
  adopted by explicit decision rather than unilaterally, and an r7 amendment
  should state the rule. Shell nodes are `cacheable=False`, so the changed
  rendered argv invalidates no cache. Pinned by `tests/test_r7_fixes.py` and
  `tests/test_interpolate.py::TestForms::test_steps_json_and_path`.
- **2026-08-02 — the assembled command line is checked before every spawn**
  (`proc.argv_overflow` / `ArgvTooLong`, raised from `proc.spawn`). Why: Windows
  `CreateProcess` caps a command line at 32,767 chars, and r5 A2 deliberately
  makes a corrective prompt several times larger than the original, so any
  argv-passed stanza can reach the cap on a re-spawn; observed live at 59,028
  chars. `ArgvTooLong` subclasses `OSError` so it rides the executors' existing
  failed-spawn path (exit 127) rather than crashing the run, and the message
  names `prompt_via = "stdin"` as the remedy instead of leaving the operator
  with CreateProcess's generic parameter error.
- **2026-08-02 — a failed corrective re-spawn reports the SPAWN error, not the
  contract error.** Why: `_validate_with_respawn` (and the map path) discarded
  `raw2.error` and reported only `validate_result("")`, so a process that never
  started was diagnosed as `result is not valid JSON: Expecting value: line 1
  column 1` — the second defect of the 2026-07-28 roadmap note, and the one that
  made the first expensive to find. The ordinary case (re-spawn ran, output
  still invalid) is unchanged and still reports "failed twice".
- **2026-07-31 — the harness fingerprint hashes the argv TEMPLATE, not §9.2's
  "rendered argv"**: `{prompt}` stays intact (the prompt is hashed separately;
  expansion would double-embed it) and `{phase_dir}` stays intact (run-specific
  path — §7's spill-stub exclusion rule, generalized). §0.1.4 invalidation
  still holds: template/flag edits change the hashed template, and r5 B1's
  stanza digest covers the rest. Long-standing behavior; recorded here (rather
  than only in code comments and ADDENDUM-A's preamble) after the 2026-07-31
  addendum audit. An r7 amendment should restate §9.2's fingerprint part list.
