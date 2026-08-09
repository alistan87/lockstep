/**
 * lockstep-guard — reference Pi extension for Lockstep harness nodes.
 * Implements ADDENDUM-A §A.7.2: path-scope gate (A.3.1 — the ActionType/
 * ontology half of A.3.1 is NOT implemented here), deterministic BLOCK
 * verdicts (A.3.3), and a structured-output submit_result tool (A.3.2)
 * whose schema is selected by LOCKSTEP_CONTRACT.
 *
 * The scope gate blocks against the node's DECLARED write scope
 * (LOCKSTEP_WRITE_SCOPE = `spec.writes`), resolved against
 * LOCKSTEP_REPO_ROOT. It used to prefix-match the resolved `spec.cwd`, which
 * is a working directory and not a write boundary — the reason ADDENDUM-A
 * preamble note 2 made the hard block per-REPO opt-in. A declared scope is
 * per NODE and the author wrote it down, so the extension now enforces
 * exactly the boundary the driver enforces post-hoc, and only sooner.
 *
 * STATUS: the SCOPE GATE is verified against live pi 0.83.0 with a control —
 * without the extension both writes land; with it the in-scope write lands and
 * the out-of-scope one is blocked and recorded to verdicts.jsonl. Getting
 * there took three corrections the "UNTESTED" warning was hiding: `pi` is a
 * parameter of a default-exported function and not a global; a handler must
 * RETURN `{block: true}` rather than mutate the event; and the event names the
 * tool in `toolName`, not `tool`. Each of the first two failed loudly, the
 * third silently.
 *
 * The submit_result half remains UNVERIFIED — no test here calls it.
 *
 * Re-check after any pi upgrade. The lockstep `doctor` principle applies with
 * force: two of those three defects were invisible without a CONTROL run
 * proving the write would otherwise have landed.
 *
 * PREFER ARGV WHERE ARGV WILL DO. pi takes `--tools`, an argv-visible
 * allowlist, and SPEC §6.11 wants enforcement visible in the argv; that is how
 * `spec.readonly` is enforced on pi. This extension is for what argv cannot
 * express: a per-path boundary, and a typed terminate-tool. Note that
 * `--tools` is an allowlist over EXTENSION tools too, so a readonly stanza
 * must name `submit_result` in it or this extension\'s tool disappears.
 *
 * Governing rule (A.1): enforce, never enable. Deleting this file must not
 * change what a correctly behaving agent can accomplish on any executor.
 * All control flow stays in Lockstep: this extension never retries, never
 * dispatches, never approves, never decides.
 *
 * Load it per STANZA, from argv — see the note on the default export below:
 *     argv = ["pi", "-p", "--extension", "contrib/pi-extension/lockstep-guard.ts", ...]
 * (`lockstep.toml.example`, stanza `pi-guarded`). Directory drop-ins
 * (.pi/extensions/, ~/.pi/agent/extensions/) are in pi's docs but were not
 * observed to load in 0.83.0 here, and argv is the better mechanism anyway:
 * per-stanza rather than per-repo, and visible in the recorded spawn.
 * Activation is keyed on LOCKSTEP_NODE_ID being present in the environment —
 * interactive pi sessions without Lockstep env vars are left untouched except
 * for the (UI-gated) working-set status line.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

/** What pi's session actually passes (`agent-session.js` `_installAgentToolHooks`):
 *  `{ type, toolName, toolCallId, input }`. The name field is `toolName` — the
 *  first cut read `event.tool`, got undefined, and returned early on every
 *  call, so the guard loaded and enforced nothing. `tool` is kept as a
 *  fallback in case another pi version spells it that way. */
type ToolCallEvent = {
  type?: string;
  toolName?: string;
  tool?: string;
  toolCallId?: string;
  input?: Record<string, unknown>;
};

// --- Lockstep node identity (env, per A.3.1; A.4.4: env + run dir only) ------

const NODE_ID = process.env.LOCKSTEP_NODE_ID ?? "";
const ROLE = process.env.LOCKSTEP_ROLE ?? "";
const SCOPE = process.env.LOCKSTEP_WORKSPACE_SCOPE ?? "";
const REPO_ROOT = process.env.LOCKSTEP_REPO_ROOT ?? "";

/** The node's DECLARED write scope (`spec.writes`), repo-root-relative, as a
 *  JSON array — empty when the node declares none.
 *
 *  This is what the guard blocks against now. It used to prefix-match
 *  LOCKSTEP_WORKSPACE_SCOPE, which carries the resolved `spec.cwd` — and a
 *  working directory is NOT a write boundary. ADDENDUM-A preamble note 2 says
 *  so, and made the hard block per-REPO opt-in because of it: a correct agent
 *  writing legitimately outside its cwd would be blocked, which fails A.1's
 *  own deletion test by removing capability a correct agent needs.
 *
 *  A declared scope has neither problem. It is per NODE, the author wrote it
 *  down, and the driver enforces the same boundary post-hoc — so the extension
 *  is doing exactly what A.1 permits: making the same failure arrive sooner. */
const WRITE_SCOPE: string[] = (() => {
  const raw = process.env.LOCKSTEP_WRITE_SCOPE ?? "";
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return []; // malformed => no scope => the driver's post-hoc check owns it
  }
})();
const VERDICT_FILE = process.env.LOCKSTEP_VERDICT_FILE ?? "";
const PHASE_DIR = process.env.LOCKSTEP_PHASE_DIR ?? "";
const CONTRACT = process.env.LOCKSTEP_CONTRACT ?? "";
const ACTIVE = NODE_ID !== "";

const WRITE_TOOLS = new Set(["write", "edit", "bash"]);

function digest(input: unknown): string {
  return crypto.createHash("sha256").update(JSON.stringify(input)).digest("hex").slice(0, 16);
}

/** A.3.3: verdicts come from deterministic code, never model prose.
 *  A.4.4 + the re-run isolation rule (ADDENDUM-A preamble note 4): this file
 *  is WRITE-ONLY — never read back. */
function recordVerdict(tool: string, reason: string, input: unknown): void {
  if (!VERDICT_FILE) return;
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    node_id: NODE_ID,
    tool,
    reason,
    input_digest: digest(input),
  });
  fs.appendFileSync(VERDICT_FILE, line + "\n", "utf-8");
}

function under(child: string, parent: string): boolean {
  return child === parent || child.startsWith(parent + path.sep);
}

/** The literal directory prefix of a `spec.writes` entry.
 *
 *  The driver matches `spec.writes` with fnmatch; this guard prefix-matches.
 *  Where the two differ the guard MUST be the more permissive one. Blocking a
 *  write the driver would have allowed is an A.1 violation — the extension
 *  would be removing capability a correct agent needs, so deleting it would
 *  change what that agent can accomplish. Under-blocking costs nothing: the
 *  driver's post-hoc scope check is still there and still quarantines.
 *
 *  So a globbed entry collapses to the literal path above its first glob
 *  character: `flows/pi-guard-*.tmp` -> `flows`, `docs/**` -> `docs`. An entry
 *  that globs at the top level (`*.md`) collapses to "" — the repo root —
 *  which allows everything, correctly: the driver alone can decide that one.
 */
function scopePrefix(entry: string): string {
  const glob = entry.search(/[*?[]/);
  if (glob === -1) return entry;
  const cut = entry.slice(0, glob).lastIndexOf("/");
  return cut === -1 ? "" : entry.slice(0, cut);
}

function insideScope(target: string): boolean {
  // No DECLARED scope => nothing to enforce. The driver's post-hoc check owns
  // it, and blocking on cwd instead would over-block a correct agent.
  if (WRITE_SCOPE.length === 0) return true;
  const resolved = path.resolve(String(target));

  // The node's phase dir is the sanctioned result channel (SPEC §8.3): writes
  // there are result delivery, not workspace mutation — blocking them would
  // break the driver's own footer contract for scope-narrowed nodes. It is
  // also where an agent harness does its scratch work (pi writes test files
  // beside its result), and the driver excludes the run dir from the scope
  // check for exactly that reason.
  if (PHASE_DIR && under(resolved, path.resolve(PHASE_DIR))) return true;

  // `spec.writes` entries are relative to the REPO ROOT, not to cwd. Resolving
  // them against the process cwd is only the same thing while no node sets
  // `spec.cwd`; when one does, every entry silently points somewhere else.
  const base = REPO_ROOT || process.cwd();
  for (const entry of WRITE_SCOPE) {
    if (under(resolved, path.resolve(base, scopePrefix(entry)))) return true;
  }
  return false;
}


/** pi 0.83.0 hands the extension API to a DEFAULT-EXPORTED function; `pi` is
 *  not a global. The first cut assumed a global and live pi refused it with
 *  `Failed to load extension: pi is not defined` — which is what the header's
 *  UNTESTED warning was for. Verified against a working extension
 *  (pi-taskflow 0.2.6: `export default function (pi) { pi.on(...) }`).
 *
 *  Load it per NODE from the stanza argv:
 *      argv = ["pi.cmd", "-p", "--extension", "contrib/pi-extension/lockstep-guard.ts", ...]
 *  which is better than a project-local install: `--extension` is argv, so it
 *  is visible in the recorded spawn, it is per-stanza rather than per-repo
 *  (ADDENDUM-A preamble note 2's granularity complaint), and it folds into the
 *  stanza digest, so attaching or removing the guard re-bills the nodes whose
 *  enforcement changed instead of silently altering them. */
export default function (pi: any) {
  // --- Hooks -------------------------------------------------------------------

  pi.on("session_start", async (_event: unknown, ctx: any) => {
    if (!ACTIVE) return;
    // A.5: interactive niceties are UI-gated; headless runs stay silent.
    if (ctx?.hasUI) {
      ctx.ui.setStatus?.(`lockstep: node ${NODE_ID} (${ROLE}) scope=${SCOPE || "unscoped"}`);
    }
  });

  pi.on("tool_call", async (event: ToolCallEvent, ctx: any) => {
    if (!ACTIVE) return;
    const toolName = event.toolName ?? event.tool ?? "";
    if (!WRITE_TOOLS.has(toolName)) return undefined;
    // A.3.1: block writes escaping the node's declared workspace scope. For
    // bash we can only check an explicit cwd-ish input field; deeper command
    // parsing is out of scope for a reference extension.
    const target = (event.input?.path ?? event.input?.file_path ?? event.input?.cwd) as
      | string
      | undefined;
    if (target !== undefined && !insideScope(target)) {
      const reason = `path ${target} is outside this node's declared write scope (${WRITE_SCOPE.join(", ")})`;
      recordVerdict(toolName, reason, event.input);
      // A.4.2: headless hooks auto-block and record; they NEVER ask.
      //
      // RETURN the decision — do not mutate the event. pi's extension runner
      // (`emitToolCall`) ignores a handler that returns nothing: it reads
      // `handlerResult.block` off the RETURN VALUE. The first cut set
      // `event.block = true` and returned undefined, so it loaded, ran, and
      // blocked nothing — the quietest possible failure for a guard, and
      // invisible without a control that proves the write would otherwise
      // land. ADDENDUM-A §A.2 documented the shape correctly (`{ block: true,
      // reason }`); this code did not follow it.
      return { block: true, reason: `lockstep-guard: ${reason}` };
    }
    return undefined;
  });


// --- A.3.2: structured output at the terminate boundary ----------------------
// Schemas mirror the built-in contracts in contracts.py, selected by the
// LOCKSTEP_CONTRACT env var. An unknown (or absent) contract name degrades to
// the permissive object schema — never to a WRONG schema, which would let the
// extension "enable" a divergent envelope. Lockstep validates the result
// independently either way — this only makes conformance the path of least
// resistance.

const FINDING_SCHEMA = {
  type: "object",
  properties: {
    schema_version: { type: "string" },
    severity: { enum: ["blocker", "major", "minor", "nit"] },
    category: { type: "string" },
    file: { type: "string" },
    line: { type: ["integer", "null"] },
    claim: { type: "string" },
    evidence: { type: "string" },
    fix_hint: { type: "string" },
  },
  required: ["severity", "category", "file", "claim", "evidence"],
};

const CONTRACT_SCHEMAS: Record<string, object> = {
  Finding: FINDING_SCHEMA,
  Verdict: {
    type: "object",
    properties: {
      schema_version: { type: "string" },
      findings: { type: "array", items: FINDING_SCHEMA },
      verdict: { enum: ["pass", "block"] },
      reason: { type: "string" },
    },
    required: ["findings", "verdict", "reason"],
  },
};

const RESULT_SCHEMA = CONTRACT_SCHEMAS[CONTRACT] ?? {
  type: "object",
  description: "The exact JSON envelope for this node's contract.",
};

  // Registered ONLY for Lockstep-driven sessions: an interactive session must
  // not grow a session-terminating tool (header promise: untouched without env).
  if (ACTIVE) pi.registerTool({
    name: "submit_result",
    description:
      "Submit this node's final result envelope and end the session. The content " +
      "must satisfy the node's Lockstep output contract; it is validated externally.",
    parameters: {
      type: "object",
      properties: {
        result: RESULT_SCHEMA,
      },
      required: ["result"],
    },
    async execute(input: { result: unknown }) {
      if (ACTIVE && PHASE_DIR) {
        // File-first result channel (SPEC §8.3).
        fs.writeFileSync(
          path.join(PHASE_DIR, "result.json"),
          JSON.stringify(input.result),
          "utf-8",
        );
      }
      return { output: JSON.stringify(input.result), terminate: true };
    },
  });
}
