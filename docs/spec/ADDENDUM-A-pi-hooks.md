---
type: addendum
title: Lockstep Addendum A — Pi Extension Hooks Integration
resource: docs/spec/ADDENDUM-A-pi-hooks.md
---
> **Adopted as informative 2026-07-26, with the reconciliation notes below**
> (the addendum was drafted against spec revision 3; this repo has since adopted
> AMENDMENTS r4–r6). **Revised in place 2026-07-31** after a three-lens
> adversarial review (spec conformance, claims-vs-code, internal logic); the
> original draft wording survives in git history.
>
> 1. **A.3.4 vs the live pi stanza:** the working `lockstep.toml` passes
>    `--no-session` (keeps doctor probes out of pi's session store). Lineage
>    capture is an OPT-IN stanza variant: `--session-dir` pointed INTO the
>    phase dir via the `{phase_dir}` argv placeholder (added for this addendum;
>    run-specific, so excluded from `input_hash` — see the DEVIATIONS entry on
>    template-vs-rendered argv hashing). The variant is sanctioned only on the
>    condition that pi in print mode starts a FRESH session per spawn and never
>    resumes from that dir — a property of pi, not of Lockstep, so verify it
>    with `doctor` after any pi upgrade; if a pi version auto-resumes, point
>    the flag at a per-attempt subdirectory instead (else a retried node
>    inherits its own prior transcript — exactly note 4's contamination class).
> 2. **`LOCKSTEP_WORKSPACE_SCOPE` source:** this env var carries the resolved
>    `spec.cwd`, and continues to — a node's DECLARED write scope now travels
>    separately in `LOCKSTEP_WRITE_SCOPE` (a JSON array, empty string when the
>    node declares none; see `spec.writes` and DEVIATIONS 2026-08-02). The two
>    were kept apart deliberately: this one is documented as a single directory
>    and `lockstep-guard.ts` prefix-matches against it, so repurposing it would
>    have broken the extension silently. Caveat (A.1): `cwd`
>    is a working directory, NOT a write boundary — a correct agent may
>    legitimately write elsewhere in the repo. Scope enforcement is therefore
>    per-flow opt-in: enable the extension's hard block only for flows whose
>    nodes' legitimate writes really are confined to their `cwd`; anything else
>    fails A.1's own deletion test by over-blocking a correct agent. Opt-in
>    mechanism, until the r7 scope field exists: presence of the project-local
>    extension (`.pi/extensions/`, versioned with the workspace — so
>    A.4.4-reconstructible from the checkout; a global install is hidden state
>    and does not qualify). That granularity is per-REPO, not truly per-flow —
>    a repo whose flows are not uniformly cwd-confined must leave the hard
>    block off. **SUPERSEDED 2026-08-09 — see the resolution block below.**
>    The guard blocks against the node's declared `spec.writes`, not `cwd`, and
>    attaches per stanza via `--extension <path>`; both halves of this note
>    (the wrong boundary, the per-repo granularity) are answered.
> 3. **Readonly via extension (SPEC §6.11):** an extension `tool_call` gate
>    could be pi's readonly enforcement, but §6.11 requires argv-visible
>    enforcement (`readonly_argv`). Reconciling the two is an r7 candidate;
>    until then, readonly nodes on pi remain a verification error.
>    **CLOSED 2026-08-09 — see the resolution block below.** No r7 field was
>    needed: pi's own `--tools` allowlist is argv-visible, so §6.11 is
>    satisfied by the stanza, not by the extension.
> 4. **A.4.4 and re-run contamination:** "reconstructible from env + run dir"
>    must also honor the re-run isolation lesson (implementation-level, pinned
>    by tests; an r7 spec candidate — not r6 text): extension artifacts in the
>    phase dir are write-only outputs, never inputs to a re-spawned node
>    (prior-attempt artifacts poisoned a live audit once; see the
>    self-contamination fix). The verdict file gets the same treatment one node
>    downstream: it IS an input to the gate, so the driver rotates it per
>    attempt (§A.3.3) — a gate must never read a stale block from a superseded
>    attempt.
>
> Lockstep-side A.7 items 1/3/5 are implemented (env identity incl.
> `LOCKSTEP_CONTRACT`, verdict-file gate convention + per-attempt rotation +
> offline tests, `{phase_dir}` substitution — honored by doctor probes too);
> item 4 is realized as the opt-in `--session-dir` stanza variant of note 1,
> not the post-run copy step the original draft described; item 2 is the
> reference extension at `contrib/pi-extension/lockstep-guard.ts` (its
> path-scope block is verified against live pi 0.83.0 as of 2026-08-09 — see
> below; it implements the path-scope half of A.3.1, the ActionType/ontology
> half is Mimir-side and not written). A.7.3 needed no
> driver code: a deterministic shell gate reads the verdict file — the spec's
> preferred gate form.

> **Resolved 2026-08-09, against live pi 0.83.0.** Three of the notes above
> were parked on missing pi capability or unverified code. Each was checked
> with a CONTROL run, because two of the defects found were silent:
>
> - **Note 3 (readonly on pi) is closed.** pi takes `--tools`, an argv-visible
>   allowlist over built-in, extension and custom tools — which is exactly the
>   enforcement SPEC §6.11 requires, so `spec.readonly: true` is now legal on
>   pi via `readonly_argv = ["--tools", "read,submit_result"]`.
>   Verified: unrestricted, the model created the file; with the allowlist it
>   did not, while still replying "DONE". `submit_result` is named in the list
>   because the allowlist covers EXTENSION tools too and would otherwise
>   remove A.3.2's own tool; naming an absent tool is harmless. Consequence
>   worth having: readonly nodes drop the `tree` token, so reviewers fan out
>   in parallel on pi for the first time.
> - **Note 2's granularity complaint is answered.** The opt-in was "presence of
>   the project-local extension", which is per-REPO. pi 0.83.0 loads an
>   extension from `--extension <path>` (a `.pi/extensions/` drop-in was not
>   discovered at all here), so the guard attaches PER STANZA from the argv:
>   visible in the recorded spawn, and folded into the stanza digest, so
>   attaching or removing it re-bills exactly the nodes whose enforcement
>   changed. And note 2's substantive objection — that `cwd` is not a write
>   boundary, so hard-blocking against it over-blocks a correct agent — is
>   gone: the guard now blocks against the node's DECLARED `spec.writes`
>   (`LOCKSTEP_WRITE_SCOPE`), resolved against a new `LOCKSTEP_REPO_ROOT`,
>   which is the same boundary the driver enforces post-hoc.
> - **A.7.2's "UNTESTED" is discharged for the scope gate only.** It did not
>   work. Three corrections: `pi` is a parameter of a default-exported function,
>   not a global (loud failure); a `tool_call` handler must RETURN
>   `{block: true}` rather than mutate the event (silent); and the event names
>   the tool in `toolName`, not `tool` (silent). The scope gate is now verified
>   with a control — in-scope write lands, out-of-scope write blocked and
>   recorded to `verdicts.jsonl`. **`submit_result` remains unverified.**
>
> The lesson generalises past pi: two of those three defects produced a guard
> that loaded, ran, and enforced nothing. Only a control run — proving the
> write would otherwise have landed — could tell that apart from success.

# Lockstep Addendum A — Pi Extension Hooks Integration

**Status:** Informative addendum: it does not amend the frozen Lockstep v1 spec (`docs/spec/SPEC.md`, revision 3, as amended by AMENDMENTS r4–r6), and `lockstep verify` enforces none of it. Nothing here changes the taskgraph format, CLI contract, exit codes, or phase lifecycle; the driver-side support it motivated (the `{phase_dir}` argv placeholder, node-identity env vars) is additive executor-config and spawn-environment surface, logged in the preamble. Within its scope this document is a binding working agreement for ALL Lockstep-driven nodes on the `pi` executor — not only Mimir flows — and for Mimir's interactive sessions: it records how Pi coding-agent extension hooks relate to Lockstep and constrains how extensions may be used so that harness replaceability is preserved.

**Applies to:** Lockstep harness nodes executed via the `pi` executor, and interactive Mimir sessions run in Pi outside Lockstep entirely.

---

## A.1 Layering model

Lockstep and Pi extensions operate at different layers and must not blur:

| Layer | Owner | Responsibilities |
|---|---|---|
| Outer control plane | Lockstep | DAG execution, gates, approvals, heal rounds, contracts, budgets, run-dir state, audit events |
| Inner harness session | Pi extension hooks | In-session enforcement, structured output shaping, trace capture |
| Interactive investigation | Pi extension hooks | Working-set tracking, graph widgets, traversal-as-tool-discovery, dialogs — no Lockstep in the loop |

**Governing principle (binding for every Lockstep-driven `pi` node — see Status): extensions may only *enforce*, never *enable*.**

Test: if deleting a Pi extension changes what a *correctly behaving* agent can accomplish — not merely what a misbehaving one can get away with — the extension has become load-bearing and violates the Lockstep principle that harnesses are replaceable config, not dependencies. A taskgraph must produce equivalent results on `pi`, Claude Code, or Copilot CLI executors; extensions may only make failure faster and conformance easier on `pi`.

Corollary: all control flow stays in Lockstep. Extensions never retry, never dispatch, never approve, never decide — and in headless runs they never reshape what the model sees or can call (§A.4.1).

---

## A.2 Pi extension hook inventory (reference)

Pi extensions are TypeScript modules subscribing to lifecycle events via `pi.on(event, handler)` and registering tools via `pi.registerTool()`. The module's **default export** is the registration function (`export default function (pi) { ... }`); a bare top-level `pi.on(...)` never runs.

Loading: the mechanism verified here is the argv flag **`--extension <path>`**, which is what Lockstep uses — it puts the guard in the recorded spawn and in the stanza digest, so attaching or removing it re-bills exactly the affected nodes. Directory drop-ins (`~/.pi/agent/extensions/`, `.pi/extensions/`) are described by pi's own docs but were not observed to load in 0.83.0 on this machine, and Lockstep does not depend on them. Hooks relevant to this addendum:

- `tool_call` — fires before each tool executes; can **block** by RETURNING `{ block: true, reason }` (mutating the event object is not enough) or mutate `event.input` in place. The tool name has been seen on both `event.toolName` and `event.tool`; read `event.toolName ?? event.tool` so a rename cannot silently disable the gate.
- `tool_result` — fires after execution; can modify the result; handlers chain like middleware.
- `before_agent_start` — fires before the agent loop; can inject a message or replace the system prompt for the turn.
- `context` — fires before each LLM call; can filter/modify the message list non-destructively.
- `session_before_compact` / `session_compact` — intercept or customize compaction.
- `session_start` / `session_shutdown` + `pi.appendEntry()` — session-scoped state persistence and reconstruction.
- `input` — intercept/transform user input (interactive only in practice).
- Tool registration with `terminate: true` results — structured-output pattern that ends the run on a final tool call.
- Dynamic tool loading — `pi.setActiveTools()` called additively during a tool's execution activates new tools before the next model request.
- `ctx.ui.*` (widgets, status, dialogs, entry renderers) — **interactive TUI only**; see §A.5.

Session transcripts persist as JSONL per session (see §A.4.4).

---

## A.3 Sanctioned integration points (headless, Lockstep-driven)

Four uses are sanctioned for `pi` executor nodes. All four satisfy the enforce-never-enable test.

### A.3.1 Defense-in-depth manifest gate

Lockstep's existing enforcement is post-hoc: output contract validation, gate phases, and heal rollback (SPEC §9.4). (The tree fingerprint, §9.2, is NOT part of this net: it detects external edits at the lineage head on resume — every completed node legitimately changes it, and v1 has no write-scope field at all, per preamble note 2.) A `tool_call` hook adds in-session, pre-damage enforcement:

- Block `write`/`edit`/`bash` targeting paths outside the node's declared workspace scope — subject to preamble note 2's caveat: until a real scope field exists this is per-flow opt-in, because `cwd` is not a write boundary and hard-blocking outside it can over-block a correct agent (an A.1 violation).
- Block direct writes to ontology files that bypass registered ActionType tools (Mimir's Actions-as-sole-write-path invariant).

**Matcher asymmetry, and which way it must fail.** The driver matches `spec.writes` with `fnmatch`; the reference extension prefix-matches resolved paths, because a `tool_call` hook sees one path at a time and has no repo listing to glob against. Where the two disagree the extension MUST be the more permissive: blocking a write the driver would allow is an A.1 violation — deleting the extension would then change what a correct agent can accomplish — while allowing one the driver would block costs nothing, since the driver's post-hoc check still quarantines it. So a globbed entry collapses to the literal directory above its first glob character (`flows/pi-guard-*.tmp` → `flows`), and an entry that globs at the top level (`*.md`) collapses to the repo root and enforces nothing. This is enforcement-only degradation, which is the sanctioned direction.

Effect: an out-of-scope write is refused before the damage lands, instead of surfacing later — if at all — through failed contracts or gate checks. Whether the node then fails is governed by the verdict-file gate policy (§A.3.3), not by luck. Lockstep remains the source of truth; the extension only fails faster.

**Manifest selection:** the driver exports node identity into the session environment of every spawned node — `LOCKSTEP_NODE_ID`, `LOCKSTEP_ROLE`, `LOCKSTEP_WORKSPACE_SCOPE`, `LOCKSTEP_WRITE_SCOPE`, `LOCKSTEP_VERDICT_FILE`, `LOCKSTEP_PHASE_DIR`, `LOCKSTEP_CONTRACT`, `LOCKSTEP_REPO_ROOT` (driver code, uniform across executors; the argv template plays no part). `LOCKSTEP_WRITE_SCOPE` is the node's declared `spec.writes` as a JSON array — the real write boundary, and what the guard enforces; `LOCKSTEP_REPO_ROOT` is the absolute root those relative globs resolve against, without which a guard running in a `cwd` below the root mis-resolves every path and blocks nothing. Pi's bash tool already exposes session env to spawned commands; the extension reads these at load to select the manifest. A node run on a non-`pi` executor simply lacks this layer: out-of-scope writes there are caught only indirectly (contracts, gates) or not at all — acceptable, because the layer (with note 2's opt-in discipline observed) only ever removes damage a MISBEHAVING agent could do, never capability a correct one needs.

### A.3.2 Structured output at the terminate boundary

Register a `submit_result` tool whose parameter schema **is** the node's output envelope (e.g. `Finding`, `KnowledgeDelta`), returning `terminate: true` so the session ends on that final tool call.

- The harness node ends by emitting exactly what `contracts.py` expects to validate.
- Machine-check-before-model-judgment is preserved: Lockstep validates the envelope independently regardless of how it was produced.
- The extension makes conformance the path of least resistance for the model; it does not replace validation.

On non-`pi` executors, the persona prompt instructs the model to emit the same JSON envelope to stdout/file — same contract, no schema-enforced assist.

**Schema selection:** the extension learns the node's envelope from `LOCKSTEP_CONTRACT` and resolves built-in contract names (`contracts.py`) against schemas it ships; an unknown or absent name degrades to a permissive object schema — degradation by design, never substitution. (One residual: a KNOWN name resolved against a stale shipped schema — the extension lagging `contracts.py` — is still a wrong schema. Harmless in outcome, because Lockstep's independent validation catches it with a corrective re-spawn, but keep the shipped schemas in sync.) Schema distribution for non-built-in envelopes is unspecified here (a Mimir-side concern). Readonly nodes: currently moot on pi (preamble note 3 — verification error); when r7 reconciles readonly-on-pi, note that `submit_result` writes the result file into the phase dir (the §8.3 result channel, not the workspace) while `FOOTER_READONLY` assumes the stdout channel — that collision must be resolved then, not papered over.

### A.3.3 Deterministic BLOCK signaling

When the `tool_call` hook blocks a call, the extension writes a structured verdict record to a per-node file in that node's phase dir (path supplied via env: `LOCKSTEP_VERDICT_FILE`) rather than relying on the model to self-report its failure.

- Lockstep's gate phase reads a machine-parseable BLOCK verdict instead of inferring failure from mangled model output.
- This closes the "heal firing on malformed verdicts" defect class (spec revision history) from a second direction: the verdict originates from deterministic code, not model prose.
- Format: one JSON object per line — `{ ts, node_id, tool, reason, input_digest }`. The file is per-node (parallel nodes and map items never share a writer); `node_id` is defensive provenance for later cross-run aggregation, not a filtering key.
- **Lifecycle:** the driver rotates `verdicts.jsonl` with the other per-attempt artifacts before every re-spawn — auto-retry, corrective, heal re-run, resume: every path re-invokes the executor, and rotation runs at the top of `execute` — so a gate reads only the FINAL attempt's in-session blocks; an agent that was blocked, then succeeded on a clean retry, is not failed by its own superseded attempt. Rotation is best-effort per r5 A4 (it never blocks a spawn); a failed rename fails CLOSED — a stale block can cause a false gate block, never a silent pass.
- **Degradation and strictness:** on non-`pi` executors no verdict file exists; a verdict-file gate's absent-file branch is therefore `pass` (the gate detects in-session blocks, it does not require them). A crashed or never-loaded extension degrades `pi` to this same baseline — silently; Lockstep's post-hoc checks still hold. (Making that state observable would need a non-block record kind; the current format has none, and a gate must treat every record as a block.) Composed with §A.3.1 this means an agent blocked and self-corrected within one attempt fails the gate on `pi` but passes elsewhere. The divergence is deliberate and A.1-clean — it penalizes only misbehaving agents — but it IS a policy choice: flows preferring forgiveness can gate on final output alone and keep the verdict file as forensics.

### A.3.4 Audit lineage capture

Pi persists each session as a JSONL file. The opt-in `pi` stanza variant points pi's session store into the node's phase dir (`"--session-dir", "{phase_dir}"`; the committed stanzas ship `--no-session` — see preamble note 1, including the fresh-session-per-spawn condition), so the transcript lands in the run dir with the node's other artifacts. There is no copy step. (The driver's per-attempt rotation, §A.3.3, does not reach into the session store's subtree — pi owns its layout — which is why note 1's fallback for an auto-resuming pi is a per-attempt subdirectory, not rotation.)

- Yields a complete tool-call-level trace per harness node, deeper than `events.jsonl` alone.
- In Mimir terms: each `ActionRun` node can reference its session transcript as provenance, giving audit lineage down to individual tool calls.
- Transcripts are provenance for humans and post-hoc audit ONLY. Interpolating a session transcript as an input to another node would make the `pi` executor load-bearing (the artifact does not exist on other executors) — prohibited under A.1.

---

## A.4 Prohibited / constrained patterns

### A.4.1 No context or capability divergence in Lockstep-driven nodes

Personas are portable markdown precisely so the same taskgraph works across `pi`, Claude Code, and Copilot CLI. If Lens/subgraph rendering happens inside a Pi hook, the same node produces different context on different harnesses — a silent portability break.

**Rule:** Lens rendering (NodeSelector output, scoped subgraph slices, permitted ActionType listings) belongs in Lockstep's interpolation layer — data rendered into the prompt before spawn. Hooks do enforcement only. (Interactive sessions outside Lockstep are exempt; see §A.5.)

**The rule covers every context- and capability-shaping channel in §A.2's inventory, not just `before_agent_start`.** In headless Lockstep-driven nodes, hooks must not:

- mutate `event.input` in `tool_call` — blocking is the ONLY sanctioned intervention; a mutated call silently rewrites what the agent did;
- modify results in `tool_result` — covert context injection into everything the model reads back;
- filter or rewrite the message list in `context`, or transform `input` — the prompt Lockstep hashed into `input_hash` is no longer the prompt the model saw, a divergence invisible to every post-hoc check;
- activate tools dynamically (`pi.setActiveTools()`) — tools materializing on `pi` that no other executor has is the purest possible "enable"; traversal-as-tool-discovery is interactive-only (§A.5);
- customize compaction (`session_before_compact` / `session_compact`) — a custom summary rewrites the message list mid-session, the same divergence class as `context`; compaction pinning is interactive-only (§A.5);
- inject messages or replace the system prompt in `before_agent_start` — the Lens case above is one instance of this, not the whole of it.

These bullets name the channels known in §A.2 — they illustrate the rule, they do not bound it: any hook, present or future, that shapes context or the toolset falls under it. Two boundaries: tool registration at load per §A.3.2 is exempt (the capability it shapes — emitting the envelope — exists on every executor; only the ergonomics differ), and block-and-record (§A.3.3) is the one sanctioned MID-SESSION intervention.

### A.4.2 No approvals inside headless sessions

Lockstep spawns Pi in print mode, where `ctx.hasUI` is false. Any hook that attempts `ctx.ui.confirm()` / `select()` / `input()` will no-op or fail — which of the two is pi-version-dependent, and a compliant hook never finds out: it branches on `ctx.hasUI` before any UI call (§A.5).

**Rule:** in headless runs, hooks auto-block and record (§A.3.3); they never ask. Approvals remain exclusively a Lockstep phase (exit code 6 semantics unchanged).

### A.4.3 No control flow in extensions

Extensions must not use `pi.sendUserMessage()`, `ctx.newSession()`, session switching, or self-triggered turns to implement retries, chaining, or dispatch within a Lockstep-driven node. Retries, heal rounds, and sequencing are Lockstep's exclusively.

### A.4.4 Extension state is disposable

Extension working state (manifests, verdict buffers) must be reconstructible from env + run dir alone. No hidden state that a resume (`lockstep resume`) or a different harness could not reproduce.

---

## A.5 Interactive-only capabilities (out of Lockstep scope)

The following are valuable for Mimir but exist only in Pi's interactive TUI, where Lockstep is not in the loop:

- **Working-set tracking:** harvest node/edge references from `tool_call` / `tool_result` traffic (deterministic — derived from what agents actually read/wrote, not model self-report) and display via `ctx.ui.setWidget()` / `setStatus()` (e.g. `graph: 7 nodes, 12 edges | 2 pending deltas`).
- **Transcript graph cards:** `pi.registerEntryRenderer()` for collapsible current-subgraph views that never enter LLM context.
- **`/graph` command:** dump the working set to Mermaid/Graphviz HTML from the git-backed typed files, color-coded by state (in-context, stale, pending-delta). Note `lockstep render` already emits Mermaid for taskgraphs; these are different graphs (session working set vs. flow DAG) and should not share a command name.
- **Compaction pinning:** `session_before_compact` custom summaries that pin node IDs, open Findings, and un-merged `KnowledgeDelta`s verbatim so long investigations don't lose graph state.
- **Traversal-as-tool-discovery:** dynamic tool loading where landing on a node kind additively activates its bound ActionType tools — the executable-edges differentiator, realized at the harness level.
- **Interactive manifest gates with dialogs:** `tool_call` blocks that *ask* (`ctx.ui.confirm`) instead of auto-blocking.

These may share code with the headless extension (same manifest logic, same working-set tracker) but must branch on `ctx.hasUI` / `ctx.mode`.

---

## A.6 Relationship to open question §15

§15 of the Lockstep spec preserves the question of whether Lockstep becomes the Mimir domain runtime or stays a build-loop tool. This addendum reduces pressure toward the former: with interactive Mimir sessions getting their own in-harness enforcement layer (§A.5), Lockstep does not need to grow session-level features to keep interactive work governed. The split stands as:

- **Lockstep:** dark-factory build loop; all control flow; harness-agnostic.
- **Pi extensions:** in-session enforcement mirroring Lockstep contracts (headless) plus the full interactive investigation experience.

§15 remains open, but the default answer trends toward "build-loop tool."

---

## A.7 Suggested v1.x work items (non-blocking)

1. Node-identity env on every spawn: `LOCKSTEP_NODE_ID` / `LOCKSTEP_ROLE` / `LOCKSTEP_WORKSPACE_SCOPE` / `LOCKSTEP_WRITE_SCOPE` / `LOCKSTEP_VERDICT_FILE` / `LOCKSTEP_PHASE_DIR` / `LOCKSTEP_CONTRACT` / `LOCKSTEP_REPO_ROOT` — exported by shared driver code for every spawned process (shell and harness spawns alike; the fake test double spawns nothing); the `lockstep.toml.example` pi stanza documents them. *(Done.)*
2. `~100`-line reference extension: env identity at load → `tool_call` path-scope gate → verdict file writer → contract-keyed `submit_result` structured-output tool. Single file, branches on `ctx.hasUI` for interactive reuse. *(Done — `contrib/pi-extension/lockstep-guard.ts`. The `tool_call` path-scope block is verified against live pi 0.83.0 with a control run; loaded per stanza via `--extension <path>`. The ActionType half of A.3.1 is not included.)*
3. Gate-phase reader for the verdict file format (§A.3.3) — realized as a deterministic shell gate: no driver changes, no exit-code changes, offline-tested. *(Done.)*
4. Session-JSONL capture for the `pi` executor — realized as the opt-in `--session-dir {phase_dir}` stanza variant (§A.3.4, preamble note 1), not a post-run copy hook. *(Done, opt-in.)*
5. Offline test: FakeExecutor variant that emits a verdict file, proving the gate path without a model. *(Done — plus per-attempt verdict rotation tests.)*
