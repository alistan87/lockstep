---
type: amendment
title: Lockstep spec — Revision 5 amendments
resource: docs/spec/AMENDMENTS-r5.md
---
# Lockstep spec — Revision 5 amendments

**Status: adopted.** Delta from Revision 4 (`AMENDMENTS-r4.md`); where documents
disagree, the later revision wins (r5 > r4 > SPEC.md r3). Sections A1–A5
formalize behavior that shipped as logged deviations after the first live
multi-model runs (2026-07-25) — they change spec text, not behavior. Sections
B1–B3 authorize behavior changes earned by those runs' failures; each cites the
incident that motivated it.

---

## A1. Readonly footer variant (§7)

The §7 standard footer instructs writing `result.json`, but a readonly node's
`readonly_argv` disables write tools — guaranteeing a denied tool call and an
empty result (observed: first `audit-spec` run). Adopted: harness nodes with
`spec.readonly: true` receive `FOOTER_READONLY`, which states that write tools
are disabled and that the node's FINAL response is the result (the §8.3 stdout
fallback channel); it omits the `progress.jsonl` offer (progress requires
writing files). The readonly footer is part of the §7 contract and a
fingerprint input like the standard one.

## A2. Corrective re-spawns carry context (§9.3)

A headless harness spawn is stateless: the bare Revision 3 corrective wording
reached a fresh session with nothing to correct (observed: "I don't see any
previous analysis in our conversation history"). Adopted: the corrective
prompt is the original rendered prompt, then the invalid output fenced as data
(`previous.invalid.output`), then the §9.3 mode-specific instruction — whose
frozen wording already carries the validation error ("… : `<validation
error>`"), so the error is not repeated as a separate segment. **"Output-only" constrains side effects, not
context.** The one-re-spawn limit and the readonly/writing wording fork are
unchanged.

## A3. Resume replays the archived flow copy (§3, §9.2)

The run dir carries a byte-identical copy of the flow file (`flow.tg.json`),
written once at lineage creation — this is what makes the §3
`resume <run_dir>` signature (no flow argument) possible. Consequences, now
stated plainly: `resume` executes the ARCHIVED definition; edits to the
original flow file are neither adopted nor rejected by a plain resume. The
`flow_hash` refusal fires only when `--flow <file>` is passed explicitly and
does not match. To adopt an edited flow, `run <flow>` — the changed hash
starts a new lineage (unchanged from §9.2). `run --fresh` forces a new lineage
for an unchanged flow; `--fresh` does not exist on `resume`.

## A4. Per-attempt artifact rotation (§10.1)

Retries and corrective re-spawns previously overwrote `prompt.txt` /
`argv.json` / `stdout.log` / `stderr.log`, destroying the evidence that
explains later attempts. Adopted: before each spawn, existing artifacts rotate
to `<name>-attempt<n>.<ext>`; the unsuffixed names always hold the LATEST
attempt (§10.1 layout unchanged for readers that only know the base names).
Rotation is best-effort (never blocks a spawn) and applies to **both harness
and shell** executors.

## A5. Housekeeping

1. Reserved commands (`steer`, `cancel`) print "reserved for v2" and exit 7.
2. `resume` re-marks `blocked` nodes as pending alongside failed/stale-running
   (`heal_round` is preserved, so exhausted heal budgets stay exhausted).
3. `kind: "fake"` (the test double) is admitted wherever "harness-kind" is
   required: `tree` exclusion default, heal targets, corrective re-spawns.
4. Argparse usage errors exit 7, never argparse's default 2 (frozen: gate
   BLOCK). A contracts module that fails to load — any exception, including
   SyntaxError — is a ContractError, hence an ordinary §6 finding (exit 5).

---

## B1. Per-stanza executor-config digests (§8.2, §0.1.4)

**Incident:** during a sustained Haiku 529 outage, repointing the one broken
stanza would have invalidated completed nodes that used *other* stanzas —
re-billing an expensive completed review to fix a cheap failed one — because
§8.2 hashed the whole `lockstep.toml` into every harness node.

**Adopted:** the harness fingerprint part is the digest of the **resolved
stanza only**: sha256 over the stanza's name plus a canonical (sorted-key
JSON) serialization of its fields. The §0.1.4 guarantee — "changing a model
flag correctly invalidates cached phases" — holds at stanza granularity:

- editing a stanza invalidates exactly the nodes that resolve to it;
- editing the config `default` key re-resolves defaulted nodes to a different
  stanza name+content, invalidating exactly them;
- editing an unrelated stanza invalidates nothing.

`RenderCtx.config_digest` (whole-file) remains for executors without stanza
structure (the fake test double).

## B2. Kind-level default retry (§9.3)

**Incident:** transient provider errors (429 session limit, 529 Overloaded)
surface as nonzero exits; the M4 automatic retry covers only timeouts and
empty results, and `retry.max` defaulted to 0 — so a single 529 failed a node
mid-flow, twice in one day.

**Adopted:** an Executor MAY declare `default_retry: RetrySpec`. The harness
executor declares `RetrySpec(max=2, backoff_ms=60000, factor=2)` (minute-scale
backoff outlives most transient incidents). Resolution: a node that sets
`retry` in the flow file (field present, even `{"max": 0}`) uses it verbatim;
otherwise the executor's `default_retry` if declared; otherwise the model
default. Shell stays at `max: 0` — deterministic processes rarely deserve
blind retries.

## B3. Provider-limit diagnosis (§9.3 — diagnostic only)

**Incident:** a session-limit 429 and an overload 529 both surfaced as bare
"exit code 1", requiring manual envelope archaeology.

**Adopted:** on a failed spawn, the harness executor inspects its stdout
envelope (best-effort, harness-format-tolerant); a recognizable
provider-limit/overload signal (HTTP status 429/529, or "session limit" /
"overloaded" in the error text) is recorded in the node's `error` and the
driver prints a wait-then-resume hint naming the run dir. **Hard rule:** this
is diagnosis only — it never affects scheduling, hashing, budgets, or retry
counts.

---

## Test-list deltas

- B1: an unrelated-stanza config edit does NOT change a harness node's hash;
  an own-stanza edit does.
- B2: a node without `retry` uses the executor's `default_retry`; an explicit
  `retry` (including `max: 0`) overrides it entirely.
- B3: a 529-style envelope on a failed spawn yields an error naming the
  provider limit and a logged resume hint.
- A4: shell attempts rotate like harness attempts.

*End of Revision 5 amendments.*
