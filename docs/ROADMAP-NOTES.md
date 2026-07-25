# Roadmap notes from dogfooding (candidates for a revision-5 amendment)

Observations from live multi-model runs on 2026-07-25 that suggest spec-level
refinements. None are v1 deviations; v1 behavior follows the spec as amended.

- **Per-stanza executor-config digests.** §8.2 hashes the WHOLE lockstep.toml
  into every harness node's fingerprint. During a sustained Haiku 529 outage
  this meant repointing the one broken stanza would have invalidated completed
  nodes that used *other* stanzas — the expensive Opus review would have been
  re-billed to fix the cheap Haiku one. Hashing only the stanza a node
  actually resolves (plus the `default` key when relied upon) preserves the
  §0.1.4 invalidation guarantee at stanza granularity.
- **Default retry for harness kinds.** Transient provider errors (429 session
  limits, 529 overload) surface as nonzero exits; the M4 automatic retry
  covers only timeouts and empty results, and `retry.max` defaults to 0. A
  kind-level default (e.g. harness ⇒ `max: 2`, minute-scale backoff) would
  match reality; today the burden is on flow authors (see /flow-authoring).
- **Resume-vs-archived-flow wording for §9.2.** Resume replays the run dir's
  archived `flow.tg.json` (DEVIATIONS entry); a future amendment should state
  this in the spec proper — the "editing a flow starts a new lineage" sentence
  reads as if `resume` compares against the edited working file.
- **Readonly result-channel formalization.** `FOOTER_READONLY` (DEVIATIONS
  entry) works, but the spec's §7 footer contract should acquire the readonly
  variant officially rather than by deviation.
