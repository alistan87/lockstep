"""kind="shell": a plain argv subprocess — deterministic, no model, no tokens
(SPEC §1). fingerprint_parts is the rendered argv only, which is constant across
runs, so shell nodes ALWAYS re-run (cacheable=False): deliberate — cheap, and it
eliminates the silent-skip footgun where a forgotten input glob meant a skipped
test suite (SPEC §0.1 item 7, §9.2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..interpolate import ResolveCtx, render_scope, render_template
from ..protocols import PlannedWork, RawResult, RenderCtx
from ..taskgraph import Node
from .proc import record_spawn_handles, resolve_inside, spawn, wait_or_kill


def node_env(work: PlannedWork, phase_dir: Path) -> dict[str, str]:
    """Node identity for the spawned session (ADDENDUM-A A.3.1 / A.7.1): lets
    an in-harness enforcement layer (e.g. a pi extension) select its manifest
    and write deterministic verdicts — enforce, never enable. WORKSPACE_SCOPE
    carries the resolved cwd until a dedicated scope field exists (r7).
    CONTRACT names the node's output contract (A.3.2) so an extension can
    offer the matching submit_result schema; empty when the node has none.
    REPO_ROOT is what WRITE_SCOPE's entries are relative to — without it a
    guard has to resolve them against the process cwd, which is only the same
    thing while no node sets `spec.cwd`, and silently over-blocks when one
    does. Over-blocking a CORRECT agent is the failure ADDENDUM-A A.1 forbids,
    so the guard needs the root stated rather than inferred."""
    return {
        # Every spawn's stdout is a redirected pipe, and on Windows a redirected
        # Python stdout defaults to the LOCALE encoding — cp1252 here. A node
        # printing an arrow, a curly quote or a non-Latin filename then died
        # with UnicodeEncodeError, exit 1, and an EMPTY stdout.log, which the
        # driver can only report as a failed node. Measured: a gate emitting
        # `"reason": "café → ok"` produced zero bytes and exit 1.
        #
        # Set for every child, not just Python ones: harnesses are Python,
        # Node and Go, and the variable is simply ignored where it means
        # nothing. It is environment, not argv, so it does not enter the input
        # hash and does not re-bill anything.
        "PYTHONIOENCODING": "utf-8",
        "LOCKSTEP_PHASE_DIR": str(phase_dir.resolve()),
        "LOCKSTEP_REPO_ROOT": str(work.meta.get("repo_root", "")),
        "LOCKSTEP_NODE_ID": str(work.meta.get("node_id", "")),
        "LOCKSTEP_ROLE": str(work.meta.get("role", "")),
        "LOCKSTEP_WORKSPACE_SCOPE": str(work.meta.get("cwd", "")),
        # The declared write scope as a JSON array, empty string when the node
        # declares none. A NEW variable rather than a change to
        # WORKSPACE_SCOPE: that one is documented as a single directory
        # (ADDENDUM-A preamble note 2) and lockstep-guard.ts prefix-matches
        # against it, so repurposing it would silently break the extension.
        # Presence-keyed like the driver's own check (V1): meta["writes"] is
        # None when the flow declared nothing, and a LIST — including [] —
        # when it declared a scope. `writes: []` must reach the guard as "[]"
        # (block every write), not as "" (no scope): truthiness here silently
        # disarmed the in-harness layer for exactly the tightest declaration
        # (adversarial-review finding 2).
        "LOCKSTEP_WRITE_SCOPE": (
            json.dumps(work.meta["writes"], ensure_ascii=False)
            if work.meta.get("writes") is not None
            else ""
        ),
        "LOCKSTEP_VERDICT_FILE": str((phase_dir / "verdicts.jsonl").resolve()),
        "LOCKSTEP_CONTRACT": str(work.meta.get("contract", "")),
    }


class ShellSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cmd: list[str]
    cwd: str = "."  # relative to the invocation directory / --repo-root (SPEC §4)
    # Declared write scope, repo-root-relative. Key ABSENT = unconstrained (the
    # v1 behavior); PRESENT — even [] — is enforced, so `writes: []` declares
    # "this command writes nothing" (a read-only probe) rather than silently
    # meaning nothing at all (LESSONS-TO-MECHANISMS V1; DEVIATIONS 2026-08-11).
    # The driver DETECTS violations after the fact; an in-harness extension can
    # PREVENT them from LOCKSTEP_WRITE_SCOPE.
    writes: list[str] = []
    # Required by `verify --lint` when writes is ["**"] (whole-tree access must
    # be a stated decision, not an omission).
    writes_rationale: str = ""
    # role=gate only (E4): run this gate's body once against the PRE-RUN tree;
    # the engine subtracts the recorded findings at evaluation. A spec key,
    # not a first-class Node field — §15 keeps format_version 1.0 (same
    # reasoning as `writes`).
    baseline: bool = False


def resolve_ctx_of(ctx: RenderCtx) -> ResolveCtx:
    return ResolveCtx(
        args=ctx.args,
        outputs=ctx.outputs,
        json_results=ctx.json_results,
        skipped=ctx.skipped,
        deps=ctx.deps,
        item=ctx.item,
        has_item=ctx.has_item,
        item_var=ctx.item_var,
    )


class ShellExecutor:
    kind = "shell"
    cacheable = False  # shell nodes always re-run
    supports_corrective_respawn = False  # deterministic: re-running re-produces the
    # same bytes; schema mismatch is immediately terminal (AMENDMENTS A4)
    SpecModel = ShellSpec

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def plan(self, node: Node, ctx: RenderCtx) -> PlannedWork:
        spec = ShellSpec.model_validate(node.spec)
        rctx = resolve_ctx_of(ctx)
        argv: list[str] = []
        for part in spec.cmd:
            rendered = render_template(
                part,
                rctx,
                fence=False,  # argv is not a prompt: raw values, no fencing, no spill
                max_interp_chars=ctx.max_interp_chars,
                spill_dir=None,
                null_for_skipped=ctx.allow_null_for_skipped,
            )
            argv.append(rendered.prompt_text)
        cwd = resolve_inside(self.repo_root, spec.cwd)
        return PlannedWork(
            render=argv,
            fingerprint_parts=[f"argv:{json.dumps(argv, ensure_ascii=False)}"],
            costs_tokens=False,
            # A shell node mutates the tree like any other writer, and the
            # write-scope check compares against a WHOLE-TREE baseline: a
            # token-less writer running beside a scoped node invalidates that
            # comparison, so the scoped node is accused of writes it did not
            # make — and under quarantine would have a peer's live file
            # reverted. Measured cost of serializing: +0.15s on the one shipped
            # flow with a parallel shell wave (status-digest, 53.69 -> 53.84s
            # at --max-workers 3).
            exclusive=["tree"],
            meta={"cwd": str(cwd), "output": node.output, "node_id": node.id, "role": node.role,
                  "contract": node.contract or "",
                  # None = key absent (unconstrained); [] = declared-empty,
                  # enforced. The distinction must survive to LOCKSTEP_WRITE_SCOPE.
                  # Rendered (args only), same reason as harness.py: the guard
                  # and the driver must be talking about the same paths.
                  "writes": render_scope(list(spec.writes), ctx.args) if "writes" in node.spec else None,
                  "repo_root": str(Path(self.repo_root).resolve())},
        )

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        argv = list(work.render)  # type: ignore[arg-type]
        # A bare "python"/"python3" argv[0] resolves to the interpreter running
        # lockstep. Flows call `python -m lockstep.gates.*` (and contrib
        # scripts that import lockstep); whichever python happens to be on the
        # spawned PATH usually cannot import it — the driver is typically
        # invoked as .venv\Scripts\lockstep.exe with no venv activated. At
        # EXECUTE time only: the planned argv (and therefore input_hash) keeps
        # the portable "python", never a machine-specific venv path. A pathy
        # or versioned interpreter ("./py", "python3.11") is left alone.
        # Deviation logged in docs/spec/DEVIATIONS.md (2026-08-05).
        if argv and argv[0].lower() in ("python", "python3"):
            argv[0] = sys.executable
        stdout_path = phase_dir / "stdout.log"
        stderr_path = phase_dir / "stderr.log"
        # r5 A4: rotate prior-attempt logs (best-effort) — shell retries were
        # overwriting the evidence, unlike harness attempts. verdicts.jsonl and
        # the result files rotate for the same reasons as in harness.py: gate
        # staleness (A.3.3) and the driver-persisted result.json shadowing the
        # §8.3 file-first channel on re-execution.
        for p in (stdout_path, stderr_path, phase_dir / "verdicts.jsonl",
                  phase_dir / "result.json", phase_dir / "result.txt"):
            if p.exists():
                n = 1
                while (phase_dir / f"{p.stem}-attempt{n}{p.suffix}").exists():
                    n += 1
                try:
                    p.rename(phase_dir / f"{p.stem}-attempt{n}{p.suffix}")
                except OSError:
                    pass
        try:
            proc = spawn(
                argv,
                cwd=Path(work.meta["cwd"]),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                extra_env=node_env(work, phase_dir),
            )
        except OSError as e:
            return RawResult(exit_code=127, result_text=None, source="none", error=f"spawn failed: {e}")
        # r6 C3: record the child pid so `lockstep cancel` can kill the tree,
        # and the Job Object name (Windows) so it can do so without a pid walk.
        record_spawn_handles(phase_dir, proc)
        exit_code, timed_out = wait_or_kill(proc, timeout_s)
        # Result channel (SPEC §8.3): file first, stdout fallback.
        result_text: str | None = None
        source = "none"
        for name in ("result.json", "result.txt"):
            p = phase_dir / name
            if p.exists():
                result_text = p.read_text(encoding="utf-8")
                source = "file"
                break
        if result_text is None:
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            if stdout.strip():
                # §8.3 fallback: for JSON output, the LAST balanced top-level
                # JSON value in stdout — a chatty script (warnings before the
                # verdict) must still yield its JSON.
                if work.meta.get("output") == "json":
                    from .harness import extract_last_json

                    result_text = extract_last_json(stdout) or stdout
                else:
                    result_text = stdout
                source = "stdout"
        return RawResult(
            exit_code=exit_code,
            result_text=result_text,
            source=source,  # type: ignore[arg-type]
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            timed_out=timed_out,
            error="timeout" if timed_out else None,
        )
