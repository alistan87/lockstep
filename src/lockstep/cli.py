"""CLI (SPEC §3): run / resume / verify / render / status / doctor / init,
with steer / cancel reserved for v2. Exit codes are frozen — see __init__.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    EXIT_CONFIG,
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_VERIFY,
    FORMAT_VERSION,
    __version__,
)
from .contracts import ContractError
from .doctor import run_doctor
from .executors.fake import FakeExecutor
from .executors.harness import HarnessError
from .executors.proc import PathEscapeError
from .interpolate import InterpolationError
from .policy import AllowAllPolicy
from .registry import ConfigError, LockstepConfig, Registry, build_registry, load_config
from .render import render_mermaid
from .roles import Engine, RunRefusal
from .state import (
    LockHeld,
    PhaseRecord,
    RunState,
    acquire_lock,
    find_attachable_run,
    load_state,
    new_run_dir,
    read_events,
    release_lock,
    utcnow,
    write_state,
)
from .store import FileStore
from .taskgraph import FlowError, TaskGraph, load_flow, verify_flow, _topo_depths
from .workspace import GitWorkspace, NullWorkspace, WorkspaceError


def _fail(msg: str, code: int) -> int:
    print(f"lockstep: {msg}", file=sys.stderr)
    return code


def _load(flow_path: str):
    return load_flow(Path(flow_path))


def _registry_for(config: LockstepConfig, repo_root: Path) -> Registry:
    reg = build_registry(config, repo_root)
    reg.register(FakeExecutor(repo_root=repo_root))  # offline test double, kind="fake"
    return reg


def _workspace_for(repo_root: Path):
    if (repo_root / ".git").exists():
        return GitWorkspace(repo_root)
    return NullWorkspace(repo_root)


def _do_verify(tg: TaskGraph, config: LockstepConfig, repo_root: Path) -> tuple[int, bool]:
    issues = verify_flow(tg, registry=_registry_for(config, repo_root), config=config, repo_root=repo_root)
    for issue in issues:
        print(issue, file=sys.stderr if issue.level == "error" else sys.stdout)
    has_errors = any(i.level == "error" for i in issues)
    return (EXIT_VERIFY if has_errors else EXIT_OK), has_errors


def _parse_args_kv(pairs: list[str], tg: TaskGraph) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise FlowError(f"--arg expects k=v, got {pair!r}")
        k, _, v = pair.partition("=")
        out[k] = v
    for name, default in tg.args.items():
        if name not in out:
            if default is None:
                raise FlowError(f"required arg {name!r} not provided (--arg {name}=...)")
            out[name] = default
    unknown = set(out) - set(tg.args)
    if unknown:
        raise FlowError(f"unknown args: {sorted(unknown)}")
    return out


def _fresh_state(tg: TaskGraph, flow_hash: str, args: dict[str, str], workspace_kind: str) -> RunState:
    return RunState(
        flow_name=tg.name,
        flow_hash=flow_hash,
        format_version=tg.format_version,
        args=args,
        nodes={
            n.id: PhaseRecord(node_id=n.id, role=n.role, kind=n.kind)
            for n in tg.nodes
        },
        started_at=utcnow(),
        workspace_kind=workspace_kind,
    )


def _print_plan(tg: TaskGraph, config: LockstepConfig) -> None:
    depths = _topo_depths(tg)
    by_depth: dict[int, list] = {}
    for n in tg.nodes:
        by_depth.setdefault(depths.get(n.id, 0), []).append(n)
    print(f"plan: {tg.name} ({len(tg.nodes)} nodes)")
    for d in sorted(by_depth):
        print(f"  wave {d}:")
        held: dict[str, list[str]] = {}
        for n in by_depth[d]:
            eff = list(n.exclusive)
            if n.kind in ("harness", "fake") and not n.spec.get("readonly"):
                eff.append("tree")
            for t in eff:
                held.setdefault(t, []).append(n.id)
            extras = []
            if n.kind == "harness":
                stanza = n.spec.get("executor") or tg.executor_default or config.default
                extras.append(f"executor={stanza}")
                if n.spec.get("persona"):
                    extras.append(f"persona={n.spec['persona']}")
                if n.spec.get("readonly"):
                    extras.append("readonly")
            if n.role == "map":
                extras.append(f"over={n.over} (fan-out resolved at run time)")
            if n.when:
                extras.append(f"when={n.when!r}")
            print(f"    {n.id}  [{n.role}/{n.kind}]" + (f"  {' '.join(extras)}" if extras else ""))
        for token, holders in held.items():
            if len(holders) > 1:
                print(f"    (serialized on {token!r}: {', '.join(holders)})")


def _run_engine(tg, flow_hash, config, run_dir: Path, state: RunState, repo_root: Path, max_workers: int, resume: bool) -> int:
    workspace = _workspace_for(repo_root)
    store = FileStore(run_dir, state)
    engine = Engine(
        tg=tg,
        registry=_registry_for(config, repo_root),
        config=config,
        workspace=workspace,
        store=store,
        policy=AllowAllPolicy(),
        repo_root=repo_root,
        max_workers=max_workers,
    )
    if state.workspace_kind == "null":
        print("workspace: null (external-edit detection off)")  # AMENDMENTS M6
    if resume:
        engine.prepare_resume()
    write_state(run_dir, state)
    code = engine.run()
    print(f"run dir: {run_dir}")
    print(f"exit: {code}")
    return code


def cmd_run(ns) -> int:
    repo_root = Path(ns.repo_root).resolve()
    try:
        tg, flow_hash = _load(ns.flow)
        config = load_config(Path(ns.config) if ns.config else repo_root / "lockstep.toml")
    except (FlowError, ConfigError) as e:
        return _fail(str(e), EXIT_VERIFY if isinstance(e, FlowError) else EXIT_CONFIG)
    if ns.executor_default:
        tg = tg.model_copy(update={"executor_default": ns.executor_default})
    code, has_errors = _do_verify(tg, config, repo_root)
    if has_errors:
        return code
    try:
        args = _parse_args_kv(ns.arg or [], tg)
    except FlowError as e:
        return _fail(str(e), EXIT_VERIFY)
    if ns.dry_run:
        _print_plan(tg, config)
        return EXIT_OK
    runs_dir = Path(ns.runs_dir)
    workspace_kind = "git" if (repo_root / ".git").exists() else "null"
    attach = None if ns.fresh else find_attachable_run(runs_dir, flow_hash, args)
    if attach is not None:
        run_dir = attach
        state = load_state(run_dir)
        resume = True
        print(f"attaching to existing run lineage: {run_dir}")
    else:
        run_dir = new_run_dir(runs_dir, tg.name)
        state = _fresh_state(tg, flow_hash, args, workspace_kind)
        write_state(run_dir, state)
        # A copy of the flow file travels with the run so `lockstep resume
        # <run_dir>` needs no other argument (SPEC §3); hash-identical by
        # construction, so the lineage check still holds.
        (run_dir / "flow.tg.json").write_bytes(Path(ns.flow).read_bytes())
        resume = False
    try:
        acquire_lock(run_dir)
    except LockHeld as e:
        return _fail(f"run dir {run_dir} is locked by {e.holder} (exit 8)", EXIT_LOCKED)
    try:
        return _run_engine(tg, flow_hash, config, run_dir, state, repo_root, ns.max_workers, resume)
    except (RunRefusal, HarnessError, WorkspaceError, PathEscapeError, ContractError) as e:
        return _fail(str(e), EXIT_CONFIG)
    finally:
        release_lock(run_dir)


def cmd_resume(ns) -> int:
    run_dir = Path(ns.run_dir)
    if not (run_dir / "state.json").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    state = load_state(run_dir)
    # Resume requires the identical flow definition: run-attach compares
    # flow_hash; editing the flow file starts a new lineage by design (§9.2).
    flow_path = ns.flow or (run_dir / "flow.tg.json")
    if not Path(flow_path).exists():
        return _fail(
            f"{run_dir} carries no flow.tg.json copy; pass --flow <flow.tg.json>", EXIT_CONFIG
        )
    repo_root = Path(ns.repo_root).resolve()
    try:
        tg, flow_hash = _load(flow_path)
        config = load_config(Path(ns.config) if ns.config else repo_root / "lockstep.toml")
    except (FlowError, ConfigError) as e:
        return _fail(str(e), EXIT_VERIFY if isinstance(e, FlowError) else EXIT_CONFIG)
    if flow_hash != state.flow_hash:
        return _fail(
            "flow file does not match this run's flow_hash — editing a flow starts a NEW "
            "lineage (run `lockstep run` instead; --attach is deferred to v2, SPEC §16.3)",
            EXIT_CONFIG,
        )
    code, has_errors = _do_verify(tg, config, repo_root)
    if has_errors:
        return code
    try:
        acquire_lock(run_dir, force=ns.force_unlock)
    except LockHeld as e:
        return _fail(f"run dir {run_dir} is locked by {e.holder} (exit 8)", EXIT_LOCKED)
    try:
        return _run_engine(tg, flow_hash, config, run_dir, state, repo_root, ns.max_workers, resume=True)
    except (RunRefusal, HarnessError, WorkspaceError, PathEscapeError, ContractError) as e:
        return _fail(str(e), EXIT_CONFIG)
    finally:
        release_lock(run_dir)


def cmd_verify(ns) -> int:
    repo_root = Path(ns.repo_root).resolve()
    try:
        tg, _ = _load(ns.flow)
    except FlowError as e:
        return _fail(str(e), EXIT_VERIFY)
    try:
        # Static only; runtime flags never consulted (SPEC §6) — but personas/
        # and lockstep.toml are part of the static surface.
        config = load_config(repo_root / "lockstep.toml")
    except ConfigError as e:
        return _fail(str(e), EXIT_CONFIG)  # §3: 7 = executor/config error, not 5
    code, _ = _do_verify(tg, config, repo_root)
    if code == EXIT_OK:
        print(f"ok: {tg.name} ({len(tg.nodes)} nodes)")
    return code


def cmd_render(ns) -> int:
    try:
        tg, _ = _load(ns.flow)
    except FlowError as e:
        return _fail(str(e), EXIT_VERIFY)
    print(render_mermaid(tg))
    return EXIT_OK


def cmd_status(ns) -> int:
    run_dir = Path(ns.run_dir)
    try:
        state = load_state(run_dir)
    except (OSError, ValueError) as e:
        return _fail(f"cannot read state: {e}", EXIT_CONFIG)
    print(f"flow: {state.flow_name}   started: {state.started_at}   token spawns: {state.token_spawns}")
    if state.workspace_kind == "null":
        print("workspace: null (external-edit detection off)")
    try:
        events = read_events(run_dir)  # tolerates a trailing partial line (§10.3)
    except Exception as e:
        # Mid-file corruption is beyond the §10.3 guarantee; status still
        # renders rather than dying with an unfrozen exit code (audit r6 nit).
        print(f"warning: events.jsonl unreadable ({e}); continuing without events")
        events = []
    # r6 C1: latest progress per node — advisory display only.
    progress: dict[str, dict] = {}
    for ev in events:
        if ev.get("kind") == "progress" and ev.get("node"):
            progress[ev["node"]] = ev
    print(f"{'node':<24} {'status':<9} {'attempts':<8} {'heal':<5} ended")
    for rec in state.nodes.values():
        print(f"{rec.node_id:<24} {rec.status:<9} {rec.attempts:<8} {rec.heal_round:<5} {rec.ended_at or ''}")
        p = progress.get(rec.node_id)
        if p:
            pct = f" {p['pct']}%" if p.get("pct") is not None else ""
            print(f"  progress: {p.get('step', '')}{pct} {p.get('note', '')}".rstrip())
        for idx, irec in sorted(rec.items.items(), key=lambda kv: int(kv[0])):
            print(f"  [{idx}] {irec.status} (attempts {irec.attempts})")
    for gate, verdict in state.verdicts.items():
        print(f"verdict {gate}: {verdict}")
    if events:
        print(f"events: {len(events)} (last: {events[-1].get('node')} -> {events[-1].get('status')})")
    return EXIT_OK


def cmd_doctor(ns) -> int:
    repo_root = Path(".").resolve()
    try:
        config = load_config(Path(ns.config) if ns.config else repo_root / "lockstep.toml")
    except ConfigError as e:
        return _fail(str(e), EXIT_CONFIG)
    return run_doctor(config)


EXAMPLE_TOML = '''# lockstep executor config (SPEC §8.2). An executor entry is an argv template:
# a new harness or a renamed flag is a config edit, never a code change.
# VERIFY flag names against your installed harness (`claude --help` etc.) and
# pin here; `lockstep doctor` checks each stanza actually runs. Run doctor after
# any harness upgrade and on a weekly cadence.

default = "claude-code"

[executors.claude-code]                    # personal machine, Claude subscription
argv = ["claude", "-p", "{prompt}", "--output-format", "json"]
prompt_via = "argv"                        # "argv" | "stdin"
json_field = "result"                      # unwrap this envelope field; omit for raw
persona_flag = []                          # empty => prepend persona body to the prompt
readonly_argv = ["--disallowed-tools", "Edit,Write"]   # appended for spec.readonly nodes;
                                           # absent => readonly nodes error at verify

[executors.pi]                             # work machine: pi carries the Copilot login
argv = ["pi", "--print", "--json", "{prompt}"]
prompt_via = "argv"

[executors.copilot-cli]
argv = ["copilot", "-p", "{prompt}"]
prompt_via = "argv"
'''


def cmd_init(ns) -> int:
    target = Path("lockstep.toml")
    if target.exists():
        return _fail("lockstep.toml already exists; not overwriting", EXIT_CONFIG)
    target.write_text(EXAMPLE_TOML, encoding="utf-8")
    print("wrote ./lockstep.toml — edit the argv templates, then run `lockstep doctor`")
    return EXIT_OK


def cmd_steer(ns) -> int:
    """r6 C2: append a SteerMessage; consumed at defined checkpoints only."""
    from .state import append_steer

    run_dir = Path(ns.run_dir)
    if not (run_dir / "state.json").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    state = load_state(run_dir)
    if ns.node_id not in state.nodes:
        return _fail(f"unknown node {ns.node_id!r} (nodes: {sorted(state.nodes)})", EXIT_CONFIG)
    append_steer(run_dir, ns.node_id, ns.message)
    status = state.nodes[ns.node_id].status
    print(f"steered {ns.node_id} (currently {status})")
    if status == "done":
        print(f"done node: will re-run on next `lockstep resume {run_dir}`")
    else:
        print("consumed at the next checkpoint: node spawn, heal round, or map item (concurrency 1)")
    return EXIT_OK


def cmd_cancel(ns) -> int:
    """r6 C3: kill the node's recorded process tree; the driver marks it
    failed(cancelled) with no retries."""
    from .executors.proc import kill_pid_tree
    from .state import utcnow

    run_dir = Path(ns.run_dir)
    if not (run_dir / "state.json").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    state = load_state(run_dir)
    if ns.node_id not in state.nodes:
        return _fail(f"unknown node {ns.node_id!r} (nodes: {sorted(state.nodes)})", EXIT_CONFIG)
    phase = run_dir / "phases" / ns.node_id
    pid_file = phase / "pid.txt"
    if not pid_file.exists():
        return _fail(f"node {ns.node_id!r} has no recorded pid (never spawned?)", EXIT_CONFIG)
    marker = phase / "CANCELLED"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(utcnow(), encoding="utf-8")
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    if not kill_pid_tree(pid):
        marker.unlink(missing_ok=True)  # nothing was killed; don't poison the next spawn
        return _fail(f"no live process tree at pid {pid} (node already finished?)", EXIT_CONFIG)
    print(f"cancelled {ns.node_id} (pid {pid}); it restarts from a known input, not mid-thought")
    print(f"  steer it first:  lockstep steer {run_dir} {ns.node_id} \"...\"")
    print(f"  then:            lockstep resume {run_dir}")
    return EXIT_OK


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 7 (executor/config error), not argparse's default 2 —
    SPEC §3 freezes 2 to mean gate BLOCK, and a mistyped invocation must not be
    indistinguishable from a quality gate blocking the run (audit finding)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"lockstep: {message}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)


def main(argv: list[str] | None = None) -> int:
    p = _Parser(
        prog="lockstep",
        description=f"lockstep {__version__} — taskgraph driver (format {FORMAT_VERSION}). "
        "Note: wall clock may exceed budget.max_run_minutes by up to the largest "
        "in-flight timeout_s — in-flight nodes finish rather than being killed (SPEC §9.5).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="run a flow")
    pr.add_argument("flow")
    pr.add_argument("--arg", action="append", default=[], metavar="k=v")
    pr.add_argument("--max-workers", type=int, default=2)
    pr.add_argument("--runs-dir", default="runs")
    pr.add_argument("--repo-root", default=".")
    pr.add_argument("--config", default=None)
    pr.add_argument("--executor-default", default=None)
    pr.add_argument("--fresh", action="store_true")
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(fn=cmd_run)

    pres = sub.add_parser("resume", help="resume a run dir")
    pres.add_argument("run_dir")
    pres.add_argument("--flow", default=None, help="the flow file this run was started from")
    pres.add_argument("--config", default=None)
    pres.add_argument("--repo-root", default=".")
    pres.add_argument("--max-workers", type=int, default=2)
    pres.add_argument("--force-unlock", action="store_true")
    pres.set_defaults(fn=cmd_resume)

    pv = sub.add_parser("verify", help="static verification only")
    pv.add_argument("flow")
    pv.add_argument("--repo-root", default=".")
    pv.set_defaults(fn=cmd_verify)

    prend = sub.add_parser("render", help="Mermaid to stdout")
    prend.add_argument("flow")
    prend.set_defaults(fn=cmd_render)

    pst = sub.add_parser("status", help="run status")
    pst.add_argument("run_dir")
    pst.set_defaults(fn=cmd_status)

    pd = sub.add_parser("doctor", help="probe each configured executor")
    pd.add_argument("--config", default=None)
    pd.set_defaults(fn=cmd_doctor)

    pi = sub.add_parser("init", help="write lockstep.toml.example to ./lockstep.toml")
    pi.set_defaults(fn=cmd_init)

    psteer = sub.add_parser("steer", help="append a steering message for a node (r6 C2)")
    psteer.add_argument("run_dir")
    psteer.add_argument("node_id")
    psteer.add_argument("message")
    psteer.set_defaults(fn=cmd_steer)

    pcan = sub.add_parser("cancel", help="kill a running node's process tree (r6 C3)")
    pcan.add_argument("run_dir")
    pcan.add_argument("node_id")
    pcan.set_defaults(fn=cmd_cancel)

    ns = p.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
