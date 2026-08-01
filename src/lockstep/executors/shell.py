"""kind="shell": a plain argv subprocess — deterministic, no model, no tokens
(SPEC §1). fingerprint_parts is the rendered argv only, which is constant across
runs, so shell nodes ALWAYS re-run (cacheable=False): deliberate — cheap, and it
eliminates the silent-skip footgun where a forgotten input glob meant a skipped
test suite (SPEC §0.1 item 7, §9.2).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..interpolate import ResolveCtx, render_template
from ..protocols import PlannedWork, RawResult, RenderCtx
from ..taskgraph import Node
from .proc import resolve_inside, spawn, wait_or_kill


def node_env(work: PlannedWork, phase_dir: Path) -> dict[str, str]:
    """Node identity for the spawned session (ADDENDUM-A A.3.1 / A.7.1): lets
    an in-harness enforcement layer (e.g. a pi extension) select its manifest
    and write deterministic verdicts — enforce, never enable. WORKSPACE_SCOPE
    carries the resolved cwd until a dedicated scope field exists (r7).
    CONTRACT names the node's output contract (A.3.2) so an extension can
    offer the matching submit_result schema; empty when the node has none."""
    return {
        "LOCKSTEP_PHASE_DIR": str(phase_dir.resolve()),
        "LOCKSTEP_NODE_ID": str(work.meta.get("node_id", "")),
        "LOCKSTEP_ROLE": str(work.meta.get("role", "")),
        "LOCKSTEP_WORKSPACE_SCOPE": str(work.meta.get("cwd", "")),
        "LOCKSTEP_VERDICT_FILE": str((phase_dir / "verdicts.jsonl").resolve()),
        "LOCKSTEP_CONTRACT": str(work.meta.get("contract", "")),
    }


class ShellSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cmd: list[str]
    cwd: str = "."  # relative to the invocation directory / --repo-root (SPEC §4)


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
            exclusive=[],
            meta={"cwd": str(cwd), "output": node.output, "node_id": node.id, "role": node.role,
                  "contract": node.contract or ""},
        )

    def execute(self, work: PlannedWork, phase_dir: Path, timeout_s: int) -> RawResult:
        argv = list(work.render)  # type: ignore[arg-type]
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
        # r6 C3: record the child pid so `lockstep cancel` can kill the tree.
        (phase_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
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
