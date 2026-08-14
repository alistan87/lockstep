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
- **2026-08-09 — the §8.3 stdout fallback is JSON extraction only where JSON
  is expected.** §8.3 states the fallback as "the last balanced top-level JSON
  value in stdout, after `json_field` unwrapping". Applied literally to a node
  whose `output` is `"text"`, that is destructive: source code is full of
  balanced brackets, so a model asked for a Python module had its answer
  replaced by whichever list literal came last. Found by running
  `flows/demo/sudoku-local.tg.json` against a local model — the model wrote a
  correct module and the file on disk was `[]`. The rule now: a text node on a
  stanza that declares **no** `json_field` (documented in
  `lockstep.toml.example` as "omit for raw") takes stdout verbatim; a text node
  on a stanza that DOES declare one still gets unwrapped out of the envelope,
  because that stanza's harness speaks envelopes. JSON nodes are unchanged.
  The implementation already carried a text branch — it was ordered after the
  extraction, so it only fired when stdout contained no JSON anywhere.
- **2026-08-02, rewritten 2026-08-08 — nodes may declare `spec.writes`**, a
  repo-root-relative write scope, as an optional key inside the per-kind spec
  model rather than a new first-class node field. Why: this is the r7
  "dedicated per-node write-scope field" candidate, and a first-class field
  would bump `format_version` 1.0 → 1.1 (§15) for a feature that per-kind specs
  already accommodate. Empty means unconstrained, so every existing flow is
  unaffected. The driver DETECTS violations rather than preventing them (it
  never sees tool calls), by diffing a baseline tree taken before the spawn.

  **Detection happens DURING the node, not after it** — inside the same `try`
  that holds the node's exclusive tokens. Outside that token the diff measures
  whatever the next node has already written, so a node that stayed in scope is
  accused of its peer's work. For the same reason every write-capable kind now
  takes the `tree` token, shell included (`shell.py`); `verify` emits
  `write-scope-unenforced` only for the classes that still hold none (readonly
  nodes), rather than guessing.

  **Violations are QUARANTINED, not left in place.** The blocked attempt is
  preserved as `phases/<node>/out-of-scope-<attempt>.patch` *before* anything is
  touched; each violating path is then restored to its baseline content, or —
  if the node created it — moved into `phases/<node>/out-of-scope-<attempt>/`.
  Rollback still never deletes (§0.1 item 2): the file is moved, and the failure
  message names every path and its outcome. Artifacts are attempt-scoped because
  `phase_dir` survives resume and heal rounds. The index entry is reset for
  violating paths the node itself staged, and **left alone, named in the
  message, for any path the operator had already staged** before the node ran.
  A rename out of scope splits into a permitted in-scope delete plus a
  quarantined creation, leaving the file in neither place; that shape is
  detected and said out loud. A part-way restore failure reports both the
  violation and the restore error, and names the paths already handled.

  **The run directory is excluded** from both the scope check and heal
  rollback. `runs/` is gitignored in this repository so `git add -A` never sees
  it, but that is a convention rather than a property of the design: where the
  run dir sits inside an un-ignored work tree, every prompt, log and
  `state.json` write reads as a change the node made, and the engine would move
  its own `stdout.log` aside and roll `state.json` back mid-run. The M7 lineage
  fingerprint is deliberately NOT changed to match — that would move recorded
  fingerprints — so an un-ignored run dir still makes resume warn about
  external edits to its own files.

  On success, the in-scope changed-path list is written to
  `phases/<node>/touched-<attempt>.txt` and the record carries only a count and
  that path — `FileStore.record` rewrites all of `state.json` on every call.
  On failure it is not written: a failed spawn's changed paths are wreckage,
  not a record.

  A `LOCKSTEP_WRITE_SCOPE` env var carries the scope as a JSON array;
  `LOCKSTEP_WORKSPACE_SCOPE` is deliberately UNCHANGED because ADDENDUM-A
  preamble note 2 documents it as a single directory and `lockstep-guard.ts`
  prefix-matches against it.
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
- **2026-08-03 — `resume --cockpit` narrows the SPEC §9.3 approval prompt to
  `a`/`r`.** Why: `e` (edit) exists so an *operator* can substitute an
  approval's result text. That is a coherent operator affordance and an
  incoherent thing to offer a non-programmer whose only escape from it on Windows
  is Ctrl-Z then Enter — the cockpit was guarding a live hazard with a sentence
  in a document, which is the one thing that design otherwise refuses. (The DE
  guide used to carry a "never type `e`" warning too; the same commit that added
  this flag deleted that sentence, because a rule the program enforces should not
  also be a rule the reader has to remember. `contrib/approve.ps1` still prints
  it on the non-cockpit path, where `e` really is reachable.) **Default OFF and behaviour is byte-identical without the
  flag**, including `e`; only `contrib/approve.ps1` passes it. The non-TTY
  auto-reject fires first and is untouched, so the structural guarantee that an
  orchestrator cannot approve is unchanged. Nothing a run can accomplish
  changes: a cockpit human who wants to say something types `r` and says it,
  and their words are now captured mechanically in `<run_dir>/rejection.txt`
  (proposal T1.2) rather than relayed. Pinned by
  `tests/test_approval_cockpit.py`; proposed in
  `docs/proposals/PROPOSAL-cockpit-ux.md` §T1.3.
- **2026-08-03 — EOF at the approval prompt is recorded as an auto-reject, not
  as a decision.** The approval prompt loop mapped `EOFError` to `answer = "r"`
  (implementation-only — SPEC §9.3 specifies nothing about EOF at the prompt),
  so the run recorded `error="approval rejected"` — indistinguishable from a
  person having typed `r`. Why it matters in practice: **on Windows `NUL` is a
  character device**, so `sys.stdin.isatty()` returns True for the cockpit's own
  documented detached-launch idiom (`lockstep run <flow> < NUL`,
  `COCKPIT-THEORY-OF-OPERATIONS.md` §2). The isatty guard therefore does not
  fire for it; execution reaches the prompt and EOFs on the first read, and
  every detached run's approval was being filed as a human decision. Found by an
  end-to-end smoke of the cockpit UX work, not by reading. **The outcome is
  unchanged** — reject, exit 6 — and the guarantee that an orchestrator cannot
  approve never depended on which branch fires: answering requires *writing* to
  that stdin, writing means a pipe, and a pipe is not a character device, so the
  isatty guard catches that case. What changes is only the recorded reason:
  `approval auto-rejected (no answer available on stdin)`. A human pressing
  Ctrl-Z/Ctrl-D at a real prompt lands here too, and "nobody answered" describes
  that accurately. Also load-bearing for `contrib/approve.ps1`, which must not
  ask an absent human why they rejected. Pinned by
  `tests/test_approval_cockpit.py::test_eof_is_recorded_as_auto_rejected_not_as_a_decision`.

- **2026-08-05 — a bare `"python"`/`"python3"` argv[0] in a shell node resolves
  to `sys.executable` at EXECUTE time** (`executors/shell.py`). Why: the gate
  library ships as `python -m lockstep.gates.*` and the contrib collectors
  import lockstep, but the driver is documented to run as
  `.venv\Scripts\lockstep.exe` with no venv activated, so the PATH `python` is
  routinely an interpreter that cannot import lockstep — every rewritten
  starter flow and every factory flow would fail at its first gate (found by
  the factory-programme adversarial review). Execute-time only, deliberately:
  the PLANNED argv — and therefore `input_hash` and the `argv:` fingerprint
  part (§9.2) — keeps the portable `"python"`, never a machine-specific venv
  path, so recorded runs replay across machines. A pathy or versioned
  interpreter (`./py`, `python3.11`, `C:\Python\python.exe`) is spawned
  exactly as written. Pinned by
  `tests/test_gates.py::test_shell_resolves_bare_python_to_the_driver_interpreter`.

- **2026-08-10 — the Windows kill path adds a Job Object alongside
  `taskkill /T /F`.** Two documents name the mechanism, which is why this is
  logged rather than done silently: SPEC §8.5 ("Windows
  `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`") and — later, so it wins —
  AMENDMENTS-r6 C3, which says `cancel` kills the recorded tree "same platform
  mechanics as §8.5 `kill_tree`, **by pid**". Neither is withdrawn: taskkill now
  runs *unconditionally and first* on both paths, so every environment still
  gets exactly what those texts describe, and r6 C3's "by pid" remains literally
  true. The job is an addition on top, not a substitution — which is what keeps
  this below the amendment bar.

  Why: reported from a consumer repo (MIMIR) running lockstep as an editable
  dependency, observed live across multiple sessions — killing the orchestrator
  did not reliably kill the tree it spawned, and descendants survived
  `ERROR_ACCESS_DENIED` on both `taskkill /T /F` and `Stop-Process -Force` while
  an interactive human kill succeeded every time. `taskkill /T` needs two things
  a job does not: a parent-pid table that still describes the tree, and a
  permitted termination call per member. A harness node's
  `pi.cmd` → `cmd.exe` → `node.exe` chain stresses the first — Windows does NOT
  reparent orphans, so once a shim exits its children point at a dead and
  eventually recycled pid, and the walk finds nothing (or walks a stranger's
  tree). The reported incidents were the second: the walk enumerated the chain
  correctly and then failed at the termination call, consistent with endpoint
  protection denying `TerminateProcess` against `node.exe` for a
  non-interactive caller. Keep the two halves distinct:
  - **Structural.** `AssignProcessToJobObject` records membership in the kernel
    at assignment, so it survives the parent's death. Assignment is not atomic
    with `CreateProcess` (Popen closes the child's thread handle, so
    `CREATE_SUSPENDED` + `ResumeThread` is out of reach without a Toolhelp
    thread walk); measured at ~17 µs against Popen's own ~2 ms. A descendant
    born inside that window is in no job — and since its parent dies with the
    job, the pid walk cannot reach it either. It is **uncovered**, not covered
    by the fallback; running taskkill first is what narrows it, not what closes
    it.
  - **The vetoed call.** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` means driver death
    reaps the tree in the KERNEL when the last handle closes — lockstep makes no
    user-mode termination call on that path, so there is nothing there to deny.
    This is the half that answers the report. `lockstep cancel` still goes
    through `TerminateJobObject`, which *is* a user-mode call and could still be
    intercepted; that path is unproven against an affected machine and the
    operator guidance for it is unchanged (ask a human to end the tree
    interactively).

  **The success path stays symmetric with POSIX, deliberately.** A node's clean
  exit does NOT tear its job down: `_release_job_if_empty` reclaims the handle
  only when the job has no live member, and holds it when it does, so a process
  a node deliberately backgrounded for LATER nodes survives exactly as it would
  on POSIX (where `kill_tree` fires on timeout and never on clean exit). The
  first cut closed unconditionally; a consumer repo (MIMIR) runs a DuckDB
  connection holder across nodes, and DuckDB's single-writer file lock makes
  both directions expensive — killing it at node exit breaks the flow, and
  letting it outlive the run recreates the reported bug in its worst form,
  since an unkillable orphan holds the database lock and every later node fails
  to take it. What the job guarantees is that nothing outlives the RUN, not
  that nothing outlives its node: the survivor is still a job member, so the
  kernel reaps it when the driver exits. Cost is one held kernel handle per
  node that leaves something behind — bounded away from run length by closing
  the empty case immediately. A refused membership query counts as non-empty:
  never kill something because we could not ask about it. Pinned by
  `test_a_backgrounded_process_survives_its_node_and_dies_with_the_driver`,
  which asserts both halves.

  All seven kernel32 calls go through `ctypes`, so the Windows branch stays
  dependency-free (pydantic remains the only runtime dependency). The job name —
  not a handle, which cannot cross a process boundary — is recorded next to
  `pid.txt` as `phases/<node>/job_name.txt`, and removed when a spawn gets no
  job so a stale name never outlives the thing it names; the reason is written
  to `phases/<node>/job-unavailable.txt`, because a silent fallback to the
  mechanism that was reported broken is indistinguishable from the guarantee.
  That reason travels on the `Popen`, not in module state — nodes and map items
  run concurrently on a thread pool, and a global let one node's artifact report
  another node's `GetLastError`, which is worse than reporting none for a file
  whose only job is to stop an operator chasing the wrong cause.
  `kill_pid_tree` returns True only when the job had a LIVE member
  (`QueryInformationJobObject`): `TerminateJobObject` succeeds against an empty
  job, and `cmd_cancel` reads that return as "a kill was issued" and keeps its
  `CANCELLED` marker — which would rewrite a node that had just SUCCEEDED as
  `failed(cancelled)` and discard its result. A job whose membership query is
  itself REFUSED is terminated anyway rather than treated as empty: a denied
  query is the signature of the machine this exists for, so forfeiting the kill
  there would be exactly backwards. Nothing here touches `input_hash`: run-dir
  artifacts are not fingerprinted. Pinned by
  `tests/test_lifecycle.py::test_job_object_reaps_tree_when_only_the_top_pid_dies`
  (whose Windows branch issues no kill at all — closing the handle *is* the
  mechanism under test), plus `test_empty_job_is_not_reported_as_a_kill`,
  `test_spawn_handles_are_recorded_and_stale_job_names_cleared`,
  `test_record_spawn_handles_never_raises_into_a_live_child`,
  `test_spawn_leaks_no_job_handle_when_popen_rejects_the_argv`, and
  `test_job_unavailable_reason_is_per_spawn_not_global`.

  Two caveats a future reader should not have to rediscover. The reaps test
  SKIPS where no job is available, so it reports green-by-skip on the very
  machines that silently lost the guarantee — `test_empty_job_is_not_reported_as_a_kill`
  is the one that turns removal into a hard failure. And `cmd_cancel` reads only
  `phases/<node>/pid.txt`, so a MAP node's per-item handles (written under
  `phases/<node>/items/<i>/`) are unreachable from `cancel` at all — pre-existing,
  not introduced here.

- **2026-08-11 — the LESSONS-TO-MECHANISMS batch** (docs/notes/LESSONS-TO-MECHANISMS.md,
  distilled from the work-repo mirror's live-run lessons and re-verified against
  this source). One bug fix, several additive mechanisms; the departures that
  touch stated spec text are logged individually:

  - **Resume dispatch race (B1) fixed.** A pending node could dispatch while a
    dependency was `done` but still awaiting resume-time hash revalidation
    behind an invalidated upstream — it consumed the previous attempt's cached
    output, and nothing ever re-checked it (exit 0 with a stale result;
    observed live at the work repo as reviewers evaluating stale evidence for
    3 resume cycles). Dispatch, `when`-eval, and revalidation ordering now
    treat `done ∧ needs_check` as unsettled (`roles.py::_dep_settled`). We read
    §9.2/§9.3 as PROMISING this ("skipped when nothing changed" implies checked
    before consumed); recorded here in case the reading is disputed.
  - **§7 footer extended, twice.** (a) For `output: "json"` nodes the prompt
    now states the resolved contract's field names and enum values, generated
    from the same pydantic model the driver validates against
    (`contracts.py::describe_contract`) — models guessed field names
    (`approved` for `verdict`) and burned corrective re-spawns on cosmetic
    mismatches. (b) One footer line advertises `attempt-notes.md` (below) —
    in `FOOTER` only, not `FOOTER_READONLY` (a readonly node's argv forbids
    the write; same reasoning as r5 A1). Both are additive prompt text after
    the frozen §7 sentence; both fold into `input_hash`, so json-emitting and
    write-capable harness nodes respectively re-bill once on upgrade —
    deliberate (the prompt genuinely changed). The shell-only replay fixture
    is unaffected (shell fingerprints are argv only).
  - **File-channel results get the same fence salvage as stdout (§8.3).** The
    stdout fallback strips markdown fences; the file channel returned raw
    bytes, so valid JSON inside a ```json fence in `result.json` failed
    contract validation and burned the corrective re-spawn. The §8.3 file-first
    ORDER is unchanged; only the salvage is now symmetric.
  - **`spec.writes` is presence-keyed, not truthiness-keyed.** A DECLARED-empty
    scope (`writes: []`) now means "this node writes nothing" and is enforced —
    every change quarantines. Previously `[]` silently meant "unconstrained",
    i.e. the tightest possible declaration disabled the check. Key ABSENT keeps
    the v1 unconstrained behavior. New lints: `lint-missing-write-scope` (a
    write-capable work node with no declared scope; becomes a verify ERROR at
    format_version 1.1), `lint-unscoped-writes` (`["**"]` without
    `spec.writes_rationale`), `lint-ungated-mutation` (mutation with no gate or
    approval on either side; silenced by "ungated" in the flow description).
    All committed flows now declare scopes.
  - **Heal text is per-target and richer (§9.4.6).** The engine-composed heal
    prompt now restates the target's own declared write scope (gate findings
    naming out-of-scope files read as authorization to edit them — observed
    live) and folds in the target's own `attempt-notes.md` (tail-capped 4000
    chars) so a retry does not re-derive what a prior attempt established. Both
    fold into the persisted heal text, same resume-stability reasoning as the
    2026-07-27 entry.
  - **Baseline gates (`spec.baseline: true`, gate role only).** The gate body
    runs once against the PRE-RUN tree (inside the wall budget, spends per
    §9.5 if token-costing — a trip there sets the flag and exits 4, never a
    traceback); recorded findings are subtracted at evaluation (exact
    (file, claim) match) and a block whose findings ALL predate the run flips
    to pass. The STORED result for a baseline gate is the adjudicated verdict
    (the raw spawn output stays in the phase dir), so downstream
    `{steps.<gate>.json...}` references and `when` conditions read the same
    verdict `state.verdicts` records. Declared as a SPEC key, not a
    first-class Node field — §15 freezes format_version 1.x against new
    first-class fields, and the spec-key route is the same one `spec.writes`
    took (2026-08-08): an older verifier rejects it with a named spec-invalid
    §6 error instead of parsing it silently. New verify errors:
    `baseline-not-gate`, `baseline-gate-references-steps`. Why: a gate wired
    to an absolute target (`ruff check .`) discarded a 40-minute implementer
    per heal round over pre-existing debt. Sibling:
    `lockstep.gates.scoped_checks` runs checks only over the worktree's own
    changed files.
  - **Gate timeouts are named (§9.4.3 unchanged).** A timed-out gate still
    cannot heal (a timeout is not a valid block) but the terminal reason now
    says "timed out after Ns" with the remedy, instead of "no valid verdict
    emitted" — which sent operators hunting schema bugs in commands that ran
    out of window.
  - **Dirty-scope preflight (fresh runs only).** `run` refuses when uncommitted
    working-tree paths fall inside a declared write scope (they would be
    legally overwritten); `--allow-dirty-scope` overrides; resumes and replays
    are exempt (a resumed tree is expectedly dirty with the run's own work).
  - **Heal rollback warns on undeclared restores (E8-interim).** When every
    heal target declares a scope, a restored path outside their union is named
    loudly (`restored-undeclared` event) — an operator's out-of-band mid-run
    edit was silently reverted twice at the work repo. Narrowing the §9.4.4
    rollback scope itself is an r7 proposal, not done here.
  - **`lockstep wait <run_dir>`** blocks until the lock releases and exits with
    the run's meaning (0/2/3/6, 4 = stopped-resumable, 1 = --timeout). New
    command; no frozen exit code is repurposed. Meaning is derived from the
    persisted records with the engine's own precedence (gate-block >
    approval-rejected > failed): a rejection is the approval node `blocked`
    with "reject" in its error; rejection.txt (the cockpit's evidence
    artifact, which nothing ever deletes) counts only while some approval is
    still not done, and propagation blocks ("upstream failed or blocked",
    "gate X blocked: …") never count as the origin.
  - **Run provenance (V3).** `state.json` records the creating driver version;
    `resume` and `status` name a mismatch. Why: four work-repo lessons were
    folklore about orphan processes a newer driver already contains.
  - **`verify --config`** matches run/resume; without it a flow whose stanzas
    live in a shared config file always false-positived `no-executor-stanza`.

- **2026-08-12 — `run --seed <run_dir>`: cross-lineage warm start (E7).**
  Editing a flow changes `flow_hash` and starts a new lineage, so every
  completed node re-runs and re-bills — correct (hash integrity is the cache's
  basis) and expensive enough that authors avoided editing flows at all. The
  refusal is unchanged; `--seed` removes the cost. Each node is planned
  normally, its `input_hash` composed exactly as the engine composes it, and a
  SUCCESSFUL recording under that same hash in the seed run is served instead
  of spawned. Nothing is trusted but the hash: an edited node hashes
  differently and runs, and so does any node whose upstream produced a
  different result. The seed decision is made at PLAN time so a served node
  sets `costs_tokens = False` and never spends from the §9.5 spawn budget —
  `status` and `--estimate` stay honest.

  Provenance, which is what makes this recordable rather than silent:
  `PhaseRecord.seeded_from` (new optional field), an advisory `kind: "seed"`
  journal line per hit, and a `seeded:` line in `status` naming the count, the
  source, and the nodes. A reader of the run dir can always tell inherited
  work from work the run did — the seed's tree, config and provider are not
  this run's.

  Three deliberate limits, each of them a rule this feature declines to bend:
  **shell nodes are never seeded** (§0.1.7 — they always re-run, and a seed is
  a cache, so it obeys what the in-lineage cache obeys); **map items are never
  seeded** (the per-item hash appends `index:i` AFTER the executor plans, so a
  plan-time decision cannot see it, and an execute-time one would spend budget
  for a spawn that never happened — the cross-lineage per-item gap stays
  open); and **failures are never served** (a failure is not a result).
  `--seed` and `--replay` are refused together: replay serves every node and
  errors on a miss, a seed serves what matches and runs the rest, so the
  combination has no single meaning.

- **2026-08-12 — `spec.writes` may interpolate `{args.NAME}`.** Scopes were
  read raw, so a parameterized flow could not scope to the file it had been
  told to write and declared `["**"]` instead — the permit meant nothing on
  exactly the nodes whose target was least predictable (`evidence-approval`'s
  producer, now scoped to `{args.deliverable}`). Entries are rendered through
  the run's args at plan time, in the engine (one helper, `_writes_of`, behind
  all four readers: quarantine, dirty preflight, heal-text restatement,
  rollback warning) and in the executors that export `LOCKSTEP_WRITE_SCOPE`,
  so the in-harness guard and the driver enforce the same paths.

  **Args only.** A `{steps...}` reference would let a node's own upstream
  output decide what that node may write — a permission the graph can widen by
  writing a different answer — so it is the verify error
  `dynamic-write-scope`, and `render_scope` refuses it again at run time (a
  scope is the wrong place to assume an earlier check ran). The RENDERED value
  is re-checked for absolute paths and `..`, because `verify` inspected a
  different string and `--arg dir=../../etc` would otherwise turn a
  legal-looking scope into an escape. Scope entries also now count as
  reference sites for §6.4, so an arg used only in a scope no longer trips
  `unused-arg`. Hash impact: none beyond what args already carry — they fold
  into every node's `input_hash` already.

- **2026-08-13 — liveness, detachment, and per-step diffs (consumer report,
  MIMIR / ontology-dashboard phase 2).** Six items from one live multi-hour
  session; the two that were wrong are recorded in
  `docs/notes/LESSONS-TO-MECHANISMS.md` rather than acted on. No frozen surface
  moves: no exit code is repurposed, `format_version` is unchanged, hash
  composition is untouched (the new record fields are outputs, never inputs),
  and §7 fencing is unaffected.

  - **`PhaseRecord.tree_before` / `tree_after` + `probes.node_diff`.** For a
    node that declares `spec.writes`, holds the `tree` token and SUCCEEDS, the
    engine now records the two git tree objects it ran between — both already
    computed for the write-scope check, so no new tree walk exists. The probe
    diffs those two recorded trees, giving a reviewer an answer about ONE step
    that a later phase cannot move. `worktree_diff` (live tree) remains, with
    the new `lint-live-diff-per-phase` warning for a flow that captures the
    live tree more than once. The pair always brackets one attempt: a fresh
    baseline clears `tree_after`, and a quarantined attempt records none (it
    was rolled back). `flows/starter/two-phase-remediation.tg.json` is the
    worked template, executed end to end (fake executor, zero tokens) by
    `tests/test_two_phase_starter.py`. The engine also now takes ONE `scope-after` snapshot and
    shares it with the scope diff and the quarantine patch — the two-walk cost
    per scoped node is unchanged and still pinned by test; the violation path
    lost a third walk.
  - **`state.inspect_lock` + `STALE:` in `status`, `wait`, and a new
    `lockstep active [runs_dir]`.** SPEC §3's command list gains `active`
    (read-only, always exits 0, spends nothing). It lists runs something claims
    to be driving; a run left unfinished at a gate is counted, not listed,
    without `--all` — every such run is unfinished forever, and on this repo's
    own `runs/` the default listing was 18 rows of history and nothing live. `wait` now stops polling when
    the lock's holder is a dead SAME-HOST pid and reports the run's meaning
    instead of blocking forever — the exit codes it can return are unchanged.
    `foreign` (another host) and `unknown` (including `acquire_lock`'s
    create-then-write window) are never called dead, matching the rule
    `acquire_lock` already applies before clearing anything. Recycled pids read
    as alive, as they always have.
  - **`run|resume --detach`.** The driver spawns a detached copy of itself
    (`CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS` on Windows, falling back
    with an explicit WARNING when the surrounding job forbids breakaway;
    `setsid` on POSIX), waits until that child has actually become the run's
    driver, prints the run dir and the DRIVER's pid (read from the lock — a
    launcher-shim `python.exe` re-execs, so the spawned pid is not it), and
    exits 0. The child runs the same argv minus the flag, so no run semantics
    are special-cased; `--dry-run`/`--estimate`/`--replay` are refused (exit
    7), as is a spawn that fails. stdin is always the null device: an approval
    must auto-reject (exit 6), never wait unseen. Requires `__main__.py`, added
    for the same reason.
  - **A resume narrates its own cache misses.** When revalidation invalidates a
    `done` node, the engine now logs `re-running '<id>' (its cached result no
    longer matches): <parts>` alongside the journal entry it already wrote.
    Same information `explain` has always had, said at the moment of the
    decision — the reporter's item 1(b), and the reason their contamination was
    only discoverable after a confusing gate block.

  - **`lint-tools-drops-result-channel`.** A stanza that attaches an
    `--extension` and hands readonly nodes a `--tools` allowlist without
    `submit_result` silently disables that extension's structured-output tool.
    Nothing fails loudly — a readonly node answers on stdout (§8.3) — so what
    is lost is ENFORCEMENT of the envelope, which is why a lint and not an
    error.

- **2026-08-14 — persona-readonly lint and the unhashed-discovery flags
  (consumer report, MIMIR follow-ups).** Three items found while the consumer
  adopted the 2026-08-13 batch; full accounting in
  `docs/notes/LESSONS-TO-MECHANISMS.md`. No frozen surface moves: the lint is
  advisory (§6's exit code untouched), hash composition is unchanged (persona
  front-matter was already stripped before the body is hashed, so the new key
  is invisible to `input_hash`; the example-config argv edits re-bill the
  affected stanzas once through the existing stanza digest, which is that
  mechanism working, not changing).

  - **`lint-persona-not-readonly` + `readonly: true` persona front-matter.**
    `spec.persona` and `spec.readonly` are independent fields, so a node can
    wear a "you fix nothing" persona while keeping full write tools and the
    exclusive `tree` token. A persona whose front-matter declares
    `readonly: true` now lets the lint name that mismatch when the node sets
    neither `spec.readonly` nor `spec.writes`; a declared scope (including
    `writes: []`, the shape for a reviewer that must run `git diff`) is a
    stated decision and stays quiet. Chains into the existing
    `readonly-unenforced` verify ERROR once `spec.readonly` is set.
    `lint_flow` gains an optional `repo_root`; without it persona lints are
    skipped, matching the config-lint convention.
  - **`--no-context-files --no-skills` on every driven pi stanza.** pi 0.83.0
    loads `AGENTS.md`/`CLAUDE.md` from cwd and its parents — a node's default
    cwd is the repo root — and discovers skills; both are instruction channels
    `input_hash` cannot see. The flags close them in argv (ADDENDUM-A's
    enforce-never-enable: deleting them cannot change what a correct node can
    do; both verified with a live control — discovery loaded this repo's root
    CLAUDE.md and the global AGENTS.md into a headless spawn, the flags
    reduced that to NONE), and `contrib/AUTHORING-FOR-PI-ON-WINDOWS.md` §1's discovery claim,
    which a consumer falsified against pi's own README, is corrected. The
    claude-code stanza documents the same channel and why its documented off
    switch (`--bare`) is not adopted: it also restricts auth to
    ANTHROPIC_API_KEY, which breaks a subscription-backed stanza.

- **2026-08-14 — `heal.on_exhausted: "block" | "pass"` on `HealSpec`, a
  first-class optional field within `format_version` 1.x**
  (PROPOSAL-taskflow-parity-tiers 2.1, adopted 2026-08-13). SPEC 9.4 knows one
  exhaustion outcome: rounds run out, the gate blocks, exit 2. `"pass"`
  accepts the best-so-far instead - the loop pattern's exit - and it is
  deliberately loud everywhere a plain pass would lie: the STORED verdict is
  rewritten (the E4 route) to `accepted after N rounds without resolving:
  <reason>` with the unresolved findings kept, so downstream references,
  `when` conditions, `status`, and the cockpit all read the truth, and the
  journal gains a `heal-exhausted-pass` event. A timeout or malformed verdict
  never exhausts to pass (9.4.3: a gate that never decided). Guards:
  `on-exhausted-with-rollback` and `on-exhausted-without-rounds` are section-6
  ERRORS; `lint-on-exhausted-pass` names every user. Format note, why this is
  in this file: the Node model's own comment records that a first-class field
  is 1.1 territory and `spec.*` keys are the 1.x extension route - but heal is
  ENGINE behaviour with no executor SpecModel to validate a spec key, and the
  adopted proposal specifies `heal.on_exhausted`. Every existing flow keeps
  its exact meaning (default "block"); a flow that USES the key fails on an
  older driver at load with a named FlowError (pydantic `extra="forbid"`
  wrapped by `load_flow`), which is a loud, attributable failure rather than
  silent drift. One inherited edge, stated: `heal_round` is a LINEAGE budget
  that never resets (the 2026-07-25 resume entry: "exhausted heal budgets stay
  exhausted"), so a gate re-entered after an accepted cycle - upstream change,
  revalidation, re-run, block - accepts immediately, and its reason counts
  rounds spent on the previous cycle's work. Same shape as the default
  behaviour, where a re-entered exhausted gate re-blocks without new rounds;
  changing either means changing what the round budget is, which is not this
  entry. The engine also now composes the round number into the heal
  text ("This is repair round N of M") - prompt-and-hash-folded via the
  existing `heal_texts` mechanism, no new 7 reference form.

- **2026-08-14 — `spec.reads`: declared file inputs as hash parts (parity
  3.1, adopted 2026-08-13).** SPEC 9.2's fingerprint knows prompts, argv,
  persona, and config; it cannot see files a node opens by itself. `reads`
  folds each matched file's content digest into the declaring node's
  input_hash - PRECISION, not correctness: an undeclared read stays
  invisible, and every document that describes the feature says so (the
  spec.writes lesson). M3 additivity is the frozen-surface discipline: an
  absent or empty declaration contributes NOTHING, pinned by a
  byte-identical-parts test and by the replay fixture passing without
  re-recording. Two deliberate departures from the adopted text, both
  toward honesty: the per-process memo is keyed on (mtime_ns, size), not
  "each path hashed once" - a literal once-per-process memo would serve a
  digest of a tree a mid-run node has since rewritten; and reads may
  interpolate {args.NAME} through the same render_scope as writes, because
  two grammars for two scope-shaped keys is a trap. Glob semantics are
  pathlib (** crosses directories), NOT writes' fnmatch - reads enumerate
  the filesystem; the difference is documented where the feature is.
  Timing rides the journal as kind:"timing"/op:"reads-hash" lines;
  lint-broad-reads warns past 200 files; .git and the runs root are never
  hashed.
