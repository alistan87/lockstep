> **Adopted as informative 2026-07-26, with the following reconciliation notes**
> (the addendum was drafted against spec revision 3; this repo has since adopted
> AMENDMENTS r4–r6):
>
> 1. **A.3.4 vs the live pi stanza:** the working `lockstep.toml` passes
>    `--no-session` (keeps doctor probes out of pi's session store). Lineage
>    capture instead uses `--session-dir` pointed INTO the phase dir via the
>    `{phase_dir}` argv placeholder (added for this addendum; run-specific, so
>    excluded from `input_hash` like all run-specific paths).
> 2. **`LOCKSTEP_WORKSPACE_SCOPE` source:** v1 nodes declare no write-scope
>    field; the env var carries the resolved `spec.cwd` until an amendment adds
>    a dedicated scope field (r7 candidate, ROADMAP-NOTES).
> 3. **Readonly via extension (SPEC §6.11):** an extension `tool_call` gate
>    could be pi's readonly enforcement, but §6.11 requires argv-visible
>    enforcement (`readonly_argv`). Reconciling the two is an r7 candidate;
>    until then, readonly nodes on pi remain a verification error.
> 4. **A.4.4 and re-run contamination:** "reconstructible from env + run dir"
>    must also honor the r6 lesson — extension artifacts in the phase dir are
>    write-only outputs, never inputs to a re-spawned node (prior-attempt
>    artifacts poisoned a live audit once; see the self-contamination fix).
>
> Lockstep-side A.7 items 1/3/4/5 are implemented (env vars, verdict-file gate
> convention + offline test, `{phase_dir}` substitution); item 2 is the
> reference extension at `contrib/pi-extension/lockstep-guard.ts` (UNTESTED
> against live pi). A.7.3 needed no driver code: a deterministic shell gate
> reads the verdict file — the spec's preferred gate form.

# Lockstep Addendum A — Pi Extension Hooks Integration

**Status:** Informative addendum. Does not modify the frozen Lockstep v1 spec (`lockstep-spec.md`, revision 3). Nothing here changes the taskgraph format, CLI contract, exit codes, or phase lifecycle. This addendum records how Pi coding-agent extension hooks relate to Lockstep and to Mimir's interactive sessions, and constrains how extensions may be used so that harness replaceability is preserved.

**Applies to:** Lockstep harness nodes executed via the `pi` executor, and interactive Mimir sessions run in Pi outside Lockstep entirely.

---

## A.1 Layering model

Lockstep and Pi extensions operate at different layers and must not blur:

| Layer | Owner | Responsibilities |
|---|---|---|
| Outer control plane | Lockstep | DAG execution, gates, approvals, heal rounds, contracts, budgets, run-dir state, audit events |
| Inner harness session | Pi extension hooks | In-session enforcement, structured output shaping, trace capture |
| Interactive investigation | Pi extension hooks | Working-set tracking, graph widgets, traversal-as-tool-discovery, dialogs — no Lockstep in the loop |

**Governing principle (normative for Mimir usage): extensions may only *enforce*, never *enable*.**

Test: if deleting a Pi extension changes what a *correctly behaving* agent can accomplish — not merely what a misbehaving one can get away with — the extension has become load-bearing and violates the Lockstep principle that harnesses are replaceable config, not dependencies. A taskgraph must produce equivalent results on `pi`, Claude Code, or Copilot CLI executors; extensions may only make failure faster and conformance easier on `pi`.

Corollary: all control flow stays in Lockstep. Extensions never retry, never dispatch, never approve, never decide.

---

## A.2 Pi extension hook inventory (reference)

Pi extensions are TypeScript modules loaded from `~/.pi/agent/extensions/` (global) or `.pi/extensions/` (project-local), subscribing to lifecycle events via `pi.on(event, handler)` and registering tools via `pi.registerTool()`. Hooks relevant to this addendum:

- `tool_call` — fires before each tool executes; can **block** (`{ block: true, reason }`) or mutate `event.input` in place.
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

Lockstep's existing enforcement is post-hoc: output contract validation, tree fingerprint comparison, gate phases. A `tool_call` hook adds in-session, pre-damage enforcement:

- Block `write`/`edit`/`bash` targeting paths outside the node's declared workspace scope.
- Block direct writes to ontology files that bypass registered ActionType tools (Mimir's Actions-as-sole-write-path invariant).

Effect: the failure class that the tree fingerprint catches after the fact is caught before the damage lands, and a heal round is never triggered. Lockstep remains the source of truth; the extension only fails faster.

**Manifest selection:** the executor argv template in `lockstep.toml` passes node identity into the session environment (e.g. `LOCKSTEP_NODE_ID`, `LOCKSTEP_ROLE`, `LOCKSTEP_WORKSPACE_SCOPE`). Pi's bash tool already exposes session env to spawned commands; the extension reads these at `session_start` to select the manifest. A node run on a non-`pi` executor simply lacks this layer and relies on Lockstep's post-hoc checks — which is the acceptable degradation mode.

### A.3.2 Structured output at the terminate boundary

Register a `submit_result` tool whose parameter schema **is** the node's output envelope (e.g. `Finding`, `KnowledgeDelta`), returning `terminate: true` so the session ends on that final tool call.

- The harness node ends by emitting exactly what `contracts.py` expects to validate.
- Machine-check-before-model-judgment is preserved: Lockstep validates the envelope independently regardless of how it was produced.
- The extension makes conformance the path of least resistance for the model; it does not replace validation.

On non-`pi` executors, the persona prompt instructs the model to emit the same JSON envelope to stdout/file — same contract, no schema-enforced assist.

### A.3.3 Deterministic BLOCK signaling

When the `tool_call` hook blocks a call, the extension writes a structured verdict record to a file in the run dir (path supplied via env, e.g. `LOCKSTEP_VERDICT_FILE`) rather than relying on the model to self-report its failure.

- Lockstep's gate phase reads a machine-parseable BLOCK verdict instead of inferring failure from mangled model output.
- This closes the "heal firing on malformed verdicts" defect class (spec revision history) from a second direction: the verdict originates from deterministic code, not model prose.
- Format: one JSON object per line — `{ ts, node_id, tool, reason, input_digest }`.

### A.3.4 Audit lineage capture

Pi persists each session as a JSONL file. The `pi` executor configuration (or a post-node step) copies the session file into the run dir alongside `events.jsonl`.

- Yields a complete tool-call-level trace per harness node, deeper than `events.jsonl` alone.
- In Mimir terms: each `ActionRun` node can reference its session transcript as provenance, giving audit lineage down to individual tool calls.

---

## A.4 Prohibited / constrained patterns

### A.4.1 No Lens rendering in `before_agent_start` for Lockstep-driven nodes

Personas are portable markdown precisely so the same taskgraph works across `pi`, Claude Code, and Copilot CLI. If Lens/subgraph rendering happens inside a Pi hook, the same node produces different context on different harnesses — a silent portability break.

**Rule:** Lens rendering (NodeSelector output, scoped subgraph slices, permitted ActionType listings) belongs in Lockstep's interpolation layer — data rendered into the prompt before spawn. Hooks do enforcement only. (Interactive sessions outside Lockstep are exempt; see §A.6.)

### A.4.2 No approvals inside headless sessions

Lockstep spawns Pi in print mode, where `ctx.hasUI` is false. Any hook that attempts `ctx.ui.confirm()` / `select()` / `input()` will no-op or fail.

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

1. `pi` executor template in `lockstep.toml.example` passing `LOCKSTEP_NODE_ID` / `LOCKSTEP_ROLE` / `LOCKSTEP_WORKSPACE_SCOPE` / `LOCKSTEP_VERDICT_FILE` env vars.
2. `~100`-line reference extension: `session_start` manifest load → `tool_call` scope/ActionType gate → verdict file writer → `submit_result` structured-output tool. Single file, branches on `ctx.hasUI` for interactive reuse.
3. Gate-phase reader for the verdict file format (§A.3.3) — additive, no exit-code changes.
4. Session-JSONL capture step in the `pi` executor post-run hook (§A.3.4).
5. Offline test: FakeExecutor variant that emits a verdict file, proving the gate path without a model.
