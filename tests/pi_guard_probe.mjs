/* Drive contrib/pi-extension/lockstep-guard.ts the way live pi drives it, and
 * print the handler's return value as JSON.
 *
 * A child process per case on purpose: the guard reads its env into
 * module-level consts at load time, so a cached import would answer every case
 * with the first case's scope.
 *
 * argv: <path-to-guard.ts> <toolName> <targetPath>
 * stdout: {"loaded":bool,"blocked":bool,"reason":string|null}
 */
import { pathToFileURL } from "node:url";

const [guardPath, toolName, target] = process.argv.slice(2);

const handlers = new Map();
const pi = {
  on: (event, fn) => handlers.set(event, fn),
  registerTool: () => {},
};

const mod = await import(pathToFileURL(guardPath).href);
if (typeof mod.default !== "function") {
  // The exact defect that made live pi say "pi is not defined".
  console.log(JSON.stringify({ loaded: false, blocked: false, reason: "no default export" }));
  process.exit(0);
}
mod.default(pi);

const onToolCall = handlers.get("tool_call");
if (!onToolCall) {
  console.log(JSON.stringify({ loaded: false, blocked: false, reason: "no tool_call handler" }));
  process.exit(0);
}

// pi names the tool in `toolName`; the event shape is otherwise the real one.
const result = await onToolCall({ type: "tool_call", toolName, toolCallId: "t1", input: { path: target } }, {});
console.log(JSON.stringify({
  loaded: true,
  blocked: result?.block === true,
  reason: result?.reason ?? null,
}));
