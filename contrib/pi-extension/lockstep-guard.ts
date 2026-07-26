/**
 * lockstep-guard — reference Pi extension for Lockstep harness nodes.
 * Implements ADDENDUM-A §A.7.2: manifest gate (A.3.1), deterministic BLOCK
 * verdicts (A.3.3), and a structured-output submit_result tool (A.3.2).
 *
 * STATUS: UNTESTED against a live pi install — written to the hook surface
 * documented in docs/ADDENDUM-A-pi-hooks.md §A.2. Verify against your pi
 * version before relying on it (the lockstep `doctor` principle applies:
 * the only check that catches API drift is running it).
 *
 * Governing rule (A.1): enforce, never enable. Deleting this file must not
 * change what a correctly behaving agent can accomplish on any executor.
 * All control flow stays in Lockstep: this extension never retries, never
 * dispatches, never approves, never decides.
 *
 * Install: copy to .pi/extensions/ (project-local) or ~/.pi/agent/extensions/.
 * Activation is keyed on LOCKSTEP_NODE_ID being present in the environment —
 * interactive pi sessions without Lockstep env vars are left untouched except
 * for the (UI-gated) working-set status line.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

type ToolCallEvent = {
  tool: string;
  input: Record<string, unknown>;
  block?: boolean;
  reason?: string;
};

// --- Lockstep node identity (env, per A.3.1; A.4.4: env + run dir only) ------

const NODE_ID = process.env.LOCKSTEP_NODE_ID ?? "";
const ROLE = process.env.LOCKSTEP_ROLE ?? "";
const SCOPE = process.env.LOCKSTEP_WORKSPACE_SCOPE ?? "";
const VERDICT_FILE = process.env.LOCKSTEP_VERDICT_FILE ?? "";
const PHASE_DIR = process.env.LOCKSTEP_PHASE_DIR ?? "";
const ACTIVE = NODE_ID !== "";

const WRITE_TOOLS = new Set(["write", "edit", "bash"]);

function digest(input: unknown): string {
  return crypto.createHash("sha256").update(JSON.stringify(input)).digest("hex").slice(0, 16);
}

/** A.3.3: verdicts come from deterministic code, never model prose.
 *  A.4.4 + r6 contamination rule: this file is WRITE-ONLY — never read back. */
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

function insideScope(target: string): boolean {
  if (!SCOPE) return true; // no declared scope => Lockstep's post-hoc checks own it
  const resolved = path.resolve(String(target));
  const scope = path.resolve(SCOPE);
  return resolved === scope || resolved.startsWith(scope + path.sep);
}

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
  if (!WRITE_TOOLS.has(event.tool)) return;
  // A.3.1: block writes escaping the node's declared workspace scope. For
  // bash we can only check an explicit cwd-ish input field; deeper command
  // parsing is out of scope for a reference extension.
  const target = (event.input?.path ?? event.input?.file_path ?? event.input?.cwd) as
    | string
    | undefined;
  if (target !== undefined && !insideScope(target)) {
    const reason = `path ${target} escapes workspace scope ${SCOPE}`;
    recordVerdict(event.tool, reason, event.input);
    // A.4.2: headless hooks auto-block and record; they NEVER ask.
    event.block = true;
    event.reason = `lockstep-guard: ${reason}`;
  }
});

// --- A.3.2: structured output at the terminate boundary ----------------------
// The schema mirrors contracts.py Verdict as an example envelope; swap the
// parameters block for the node's actual contract. Lockstep validates the
// result independently either way — this only makes conformance the path of
// least resistance.

pi.registerTool({
  name: "submit_result",
  description:
    "Submit this node's final result envelope and end the session. The content " +
    "must satisfy the node's Lockstep output contract; it is validated externally.",
  parameters: {
    type: "object",
    properties: {
      result: { type: "object", description: "The exact JSON envelope for this node's contract." },
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
