# Unreleased (targets 0.12.0)

Folded into CHANGELOG.md by release-cut at the next version bump.

## `lockstep adopt` — settle a human-remediated artifact (S3)

The OW-07 gap, closed: after a gate block and a legitimate human edit, every
road destroyed the edit or the lineage — `resume` re-ran the producer on a
hash miss and legally overwrote it, `--allow-dirty-scope` waived the E9
preflight wholesale, a second flow severed lineage. `lockstep adopt
<run_dir> <writer> --reason-file <f>` records the fact the engine had no way
to hold: this artifact is settled, a human put it there, and its consumers
have not seen it yet.

Semantics are Sol's, not Gemini's (DESIGN-NOTE-adopt §1, adopted+built
2026-09-08): the unit is an ARTIFACT adopted into a `done` writer whose
consumers re-run unweakened — never a failed node marked done because the
tree looks right.

- **The pin holds even against a hash miss** — the one deliberate departure
  from "nothing is trusted except the hash" (DEVIATIONS 2026-09-08); what is
  trusted instead is the chained `adoption` event (per-path before/after
  content hashes, the reason verbatim, `source:
  external-approved-remediation`). `input_hash` is never rewritten:
  `status`/`explain`/`explain --graph` say `settled-by-adoption`, never a
  cache hit.
- **Consumers re-pend explicitly** (reads-hashes cannot see an undeclared
  read); map consumers clear their item records, the same rule heal
  invalidation applies.
- **The M7 fingerprint refreshes for the adopted paths only** — the adoption
  is an external edit by construction, and without this the next resume's
  sweep re-pended the writer over its own adoption (D3, found in code while
  writing the design note). Unrelated edits still warn by name.
- **The pin dissolves** on `adopt --release` (journaled, the original event
  never deleted), and automatically when a heal round or a steering message
  re-spawns the writer — a re-spawn's output is model output and must not
  inherit the label.
- **Refusals, all exit 7**: live lock; wrong repo root; non-`done` writer;
  paths outside the writer's declared `spec.writes`; paths a second writer's
  scope also covers; consumers interpolating the writer's RECORDED result
  text ({steps.<id>.output}/.json/{previous.output}) — `--force` overrides
  with the override journaled in the adoption record. The site scanner is
  `_node_templates`, the same enumerator §6 verification uses — one list to
  keep honest, `when` and flow-child args included.
- **A seed names the adoption and never transfers the pin** (a new lineage
  is a new consent); `--force-stale <writer>` or re-adopt after the run.
- `adoption-reason.txt` is gc-protected like `rejection.txt` — human-authored
  artifacts are not the engine's to expire.

Pinned by tests/test_adopt.py (the design note's nine acceptance tests plus
the two dissolve paths); replay fixture passes un-re-recorded.
