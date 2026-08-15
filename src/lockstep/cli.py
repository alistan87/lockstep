"""CLI (SPEC §3): run / resume / verify / render / status / doctor / init,
with steer / cancel reserved for v2. Exit codes are frozen — see __init__.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import (
    EXIT_APPROVAL_REJECTED,
    EXIT_BUDGET,
    EXIT_CONFIG,
    EXIT_GATE_BLOCK,
    EXIT_LOCKED,
    EXIT_NODE_FAILED,
    EXIT_OK,
    EXIT_VERIFY,
    FORMAT_VERSION,
    __version__,
)
from .contracts import ContractError
from .doctor import doctor_advisory, run_doctor
from .estimate import estimate_flow, render_estimate
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
    chain_head,
    configure_spans,
    find_attachable_run,
    inspect_lock,
    load_state,
    new_run_dir,
    read_events,
    release_lock,
    utcnow,
    verify_trace,
    write_state,
)
from .store import FileStore
from .taskgraph import FlowError, TaskGraph, lint_flow, load_flow, verify_flow, _topo_depths
from .workspace import GitWorkspace, NullWorkspace, WorkspaceError


def _fail(msg: str, code: int) -> int:
    print(f"lockstep: {msg}", file=sys.stderr)
    return code


def _load(flow_path: str):
    return load_flow(Path(flow_path))


def _registry_for(config: LockstepConfig, repo_root: Path) -> Registry:
    reg = build_registry(config, repo_root)
    reg.register(FakeExecutor(repo_root=repo_root))  # offline test double, kind="fake"
    from .executors.flow import FlowExecutor

    # Composition (kind="flow"): children resolve every kind the parent can —
    # the factory recurses, and RunResources.depth caps how far.
    reg.register(FlowExecutor(
        config=config, repo_root=repo_root,
        make_registry=lambda: _registry_for(config, repo_root),
    ))
    return reg


def _workspace_for(repo_root: Path):
    if (repo_root / ".git").exists():
        return GitWorkspace(repo_root)
    return NullWorkspace(repo_root)


def _do_verify(tg: TaskGraph, config: LockstepConfig, repo_root: Path) -> tuple[int, bool]:
    issues = verify_flow(
        tg,
        registry=_registry_for(config, repo_root),
        config=config,
        repo_root=repo_root,
        policy=AllowAllPolicy(),
    )
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
        # V3 provenance: which driver created this run. A resume with a
        # different installed version warns — a drifted mirror otherwise
        # generates folklore about already-fixed behaviour.
        driver_version=__version__,
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
            eff = set(n.exclusive)  # dedupe: explicit ["tree"] + default must not double-count
            if n.role != "approval" and n.kind in ("harness", "fake") and not n.spec.get("readonly"):
                eff.add("tree")
            for t in sorted(eff):
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


def _run_engine(
    tg, flow_hash, config, run_dir: Path, state: RunState, repo_root: Path,
    max_workers: int, resume: bool, replay: str | None = None, replay_any: bool = False,
    otel_file: str | None = None, cockpit: bool = False, check_dirty_scope: bool = False,
    seed: str | None = None, force_stale: list[str] | None = None,
) -> int:
    if otel_file is not None:
        # Bare flag ⇒ alongside the run's other artifacts; a path ⇒ a shared
        # file a collector already watches. The run id is the trace id's seed,
        # so a resume joins the same trace.
        target = Path(otel_file) if otel_file else run_dir / "spans.jsonl"
        configure_spans(target, run_dir.name)
        print(f"otel: OTLP/JSON spans -> {target}")
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
        cockpit=cockpit,
        check_dirty_scope=check_dirty_scope,
    )
    if replay:
        from .replay import ReplayIndex, wrap_registry

        wrap_registry(
            engine.registry,
            ReplayIndex.from_run_dir(Path(replay)),
            strict=not replay_any,
            log=engine.log,
        )
        # Baseline-gate bodies must not execute under replay: the wrapper
        # would serve the gate's recorded (post-run, adjudicated) verdict as
        # the "pre-run" baseline. Recorded results are already adjudicated.
        engine.replaying = True
        print(f"replay: serving recorded results from {replay} — no spawns, no tokens")
        if replay_any:
            print("replay: --replay-any is set; stale recordings are served with a warning")
    if seed:
        from .seed import SeedIndex, forced_set, wrap_registry as wrap_seed

        forced = forced_set(tg, force_stale) if force_stale else set()
        wrap_seed(
            engine.registry,
            SeedIndex.from_run_dir(Path(seed)),
            log=engine.log,
            on_hit=engine.note_seeded,
            forced=forced,
            on_forced=engine.note_forced,
        )
        print(f"seed: reusing hash-matched results from {seed}; everything else runs")
        if forced:
            print(f"force-stale: {len(forced)} node(s) will NOT be served regardless of "
                  f"hash — {', '.join(sorted(forced))} (the named frontier plus everything "
                  f"downstream)")
    if state.workspace_kind == "null":
        print("workspace: null (external-edit detection off)")  # AMENDMENTS M6
    if resume:
        engine.prepare_resume()
    write_state(run_dir, state)
    code = engine.run()
    print(f"run dir: {run_dir}")
    head = chain_head(run_dir)
    if head:
        # Record this off-box and `verify-trace --head` pins the whole journal;
        # without it the chain only proves internal consistency.
        print(f"trace head: {head}")
    print(f"exit: {code}")
    return code


def _detach(ns, runs_dir: Path, locate) -> int:
    """Re-invoke this exact command in a process that outlives us (item 3).

    The child runs the SAME argv with `--detach` removed, so nothing about the
    run's semantics is special-cased for detaching: the dirty-scope preflight,
    attach-vs-fresh, budgets and the lock are all decided by the child exactly
    as they would be in the foreground. All this parent does is verify (already
    done by the time we get here — a broken flow fails in the caller's terminal,
    not in a log), spawn, and confirm the child took the lock.
    """
    from .detach import await_start, driver_argv, mark, spawn_detached, tail
    from .state import utcnow

    if getattr(ns, "dry_run", False) or getattr(ns, "estimate", False) or getattr(ns, "replay", None):
        return _fail(
            "--detach has nothing to detach: --dry-run, --estimate and --replay are "
            "synchronous and spend nothing",
            EXIT_CONFIG,
        )
    # Strip every spelling argparse accepts for this flag, not just the literal
    # one: argparse honours unambiguous PREFIXES, so `lockstep run f --det`
    # sets detach=True while a `!= "--detach"` filter leaves it in the child's
    # command line — and that child detaches another child, forever. A fork
    # bomb, one keystroke away, found by adversarial review before it shipped.
    def _is_detach(a: str) -> bool:
        return a.startswith("--") and len(a) > 2 and "--detach".startswith(a)

    argv = [a for a in (getattr(ns, "_argv", None) or []) if not _is_detach(a)]
    if not argv:  # pragma: no cover — main() always sets _argv
        return _fail("--detach could not reconstruct its own command line", EXIT_CONFIG)
    stamp = utcnow().replace(":", "").replace("-", "").split(".")[0].rstrip("Z")
    log = runs_dir / f"detached-{stamp}Z.log"
    # BEFORE the spawn (see detach.mark).
    pre = locate()
    before = mark(pre)
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        proc, note = spawn_detached(driver_argv(argv), cwd=Path.cwd(), log_path=log)
    except OSError as e:
        # A spawn that never happened must exit with a frozen code, not a
        # traceback — and on this class of machine an AV hold on the log file
        # is a routine transient.
        return _fail(f"--detach could not launch a driver: {e}", EXIT_CONFIG)
    if note:
        print(f"WARNING: {note}")
    print(f"detached: launched (log: {log})")
    run_dir, code, confirmed = await_start(proc, locate, pre=pre, before=before)
    if confirmed:
        print(f"  run dir: {run_dir}")
        holder = inspect_lock(run_dir)
        if holder.pid is not None:
            # The DRIVER's pid, read back from the lock — NOT the one Popen
            # returned. A launcher shim (a uv-built venv `python.exe` is one)
            # re-execs, so the spawned pid never appears in the lock and cannot
            # be found in the process table. This is the pid `status` and
            # `active` cross-reference, so it is the only one worth printing.
            print(f"  driver pid: {holder.pid}")
        if code is not None:
            print(f"  (it already finished, exit {code} — `lockstep status` has the detail)")
        print(f"  follow:  lockstep status {run_dir}")
        print(f"  block:   lockstep wait {run_dir}")
        print("stdin is the null device: an approval auto-rejects (exit 6) rather than "
              "waiting for a prompt nobody can see")
        return EXIT_OK
    if code is not None:
        # It died before it ever held the lock — a held lock, a bad config, a
        # missing stanza. That belongs in this terminal, not only in the log.
        print(f"  the detached driver exited {code} before taking a run lock", file=sys.stderr)
        for line in tail(log):
            print(f"    {line}", file=sys.stderr)
        return code if code != EXIT_OK else EXIT_CONFIG
    print(f"  a process is alive but has not taken a run lock yet — "
          f"check `lockstep active {runs_dir}` and the log above")
    return EXIT_OK


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
    if getattr(ns, "detach", False):
        # Before the free synchronous modes, so `--detach --dry-run` is
        # refused rather than silently ignored. The child decides
        # attach-vs-fresh itself; we look for the dir it settles on by the same
        # lineage lookup it uses.
        return _detach(
            ns, Path(ns.runs_dir),
            lambda: find_attachable_run(Path(ns.runs_dir), flow_hash, args),
        )
    if not (ns.dry_run or ns.estimate or ns.replay):
        # Zero-token operations never touch a harness — nagging them (or every
        # replay_suite fixture, whose throwaway runs-dir has no record) would
        # teach people to ignore the one line that matters before a real run.
        advisory = doctor_advisory(Path(ns.runs_dir), config)
        if advisory:
            print(advisory)  # one line, advisory only (A4); never blocks
    if ns.dry_run or ns.estimate:
        _print_plan(tg, config)
        if ns.estimate:
            # Deliberately before any run dir exists: a preflight that created
            # state would already have changed the thing it is estimating.
            print()
            print(render_estimate(estimate_flow(tg, Path(ns.runs_dir), flow_hash)))
        return EXIT_OK
    if getattr(ns, "seed", None):
        # Both serve recorded results, but on opposite defaults (replay errors
        # on a miss, a seed runs it), so a combination has no single meaning.
        if ns.replay:
            return _fail("--seed and --replay cannot be combined: replay serves every node "
                         "and fails on a miss, a seed serves what matches and runs the rest",
                         EXIT_CONFIG)
        if not (Path(ns.seed) / "state.json").is_file():
            return _fail(f"--seed {ns.seed} is not a run directory (no state.json)", EXIT_CONFIG)
    if getattr(ns, "force_stale", None):
        # Parity 3.3: --force-stale is a modifier of the SEED decision — with
        # no seed there is nothing to decline, and silently accepting it would
        # read as a re-run guarantee this run makes anyway.
        if not getattr(ns, "seed", None):
            return _fail("--force-stale requires --seed <run_dir> (it names what the seed "
                         "must NOT serve); a plain run re-runs everything already", EXIT_CONFIG)
        from .seed import forced_set

        try:
            forced_set(tg, ns.force_stale)  # validate names early, before any state exists
        except ValueError as e:
            return _fail(str(e), EXIT_CONFIG)
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
        resume = False
    try:
        acquire_lock(run_dir)
    except LockHeld as e:
        return _fail(f"run dir {run_dir} is locked by {e.holder} (exit 8)", EXIT_LOCKED)
    if not resume:
        # AFTER the lock: `wait` reads state-without-lock as "settled", so a
        # fresh run must never be observable in that order
        # (adversarial-review finding 8).
        write_state(run_dir, state)
        # A copy of the flow file travels with the run so `lockstep resume
        # <run_dir>` needs no other argument (SPEC §3); hash-identical by
        # construction, so the lineage check still holds.
        (run_dir / "flow.tg.json").write_bytes(Path(ns.flow).read_bytes())
    try:
        return _run_engine(
            tg, flow_hash, config, run_dir, state, repo_root, ns.max_workers, resume,
            replay=ns.replay, replay_any=ns.replay_any, otel_file=ns.otel_file,
            # E9: fresh runs only — a resumed tree is expected dirty with the
            # run's own prior work; replays write nothing.
            check_dirty_scope=not resume and not ns.replay and not ns.allow_dirty_scope,
            seed=getattr(ns, "seed", None),
            force_stale=getattr(ns, "force_stale", None),
        )
    except (RunRefusal, HarnessError, WorkspaceError, PathEscapeError, ContractError) as e:
        return _fail(str(e), EXIT_CONFIG)
    finally:
        release_lock(run_dir)


def cmd_resume(ns) -> int:
    run_dir = Path(ns.run_dir)
    if not (run_dir / "state.json").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    state = load_state(run_dir)
    if state.driver_version and state.driver_version != __version__:
        # V3: name the drift; do not block on it. Cached hashes may
        # legitimately differ across versions and `explain` will say why.
        print(
            f"note: run created by lockstep {state.driver_version}, resuming with "
            f"{__version__} — behaviour and hash composition may differ"
        )
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
    if getattr(ns, "detach", False):
        return _detach(ns, run_dir.parent, lambda: run_dir)
    try:
        acquire_lock(run_dir, force=ns.force_unlock)
    except LockHeld as e:
        return _fail(f"run dir {run_dir} is locked by {e.holder} (exit 8)", EXIT_LOCKED)
    try:
        return _run_engine(tg, flow_hash, config, run_dir, state, repo_root, ns.max_workers,
                           resume=True, otel_file=ns.otel_file,
                           cockpit=getattr(ns, "cockpit", False))
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
        # and the executor config are part of the static surface. --config
        # matches run/resume (C1): without it, a flow whose stanzas live only
        # in a shared config always reported no-executor-stanza here while
        # resolving fine at run time.
        config = load_config(
            Path(ns.config) if getattr(ns, "config", None) else repo_root / "lockstep.toml"
        )
    except ConfigError as e:
        return _fail(str(e), EXIT_CONFIG)  # §3: 7 = executor/config error, not 5
    code, _ = _do_verify(tg, config, repo_root)
    if getattr(ns, "lint", False):
        # Advisory only — the exit code is §6's alone, and a lint never moves it.
        lints = lint_flow(tg, config, repo_root=repo_root)
        for issue in lints:
            print(f"lint {issue}")
        if not config.executors:
            print("lint: executor-config lints SKIPPED (no lockstep.toml stanzas found)")
        if not lints:
            print("lint: clean")
    if code == EXIT_OK:
        print(f"ok: {tg.name} ({len(tg.nodes)} nodes)")
    return code


def cmd_gc(ns) -> int:
    """A5: estimate-aware retention for runs/. Dry-run unless --apply."""
    from .gc import apply_gc, plan_gc

    plan = plan_gc(Path(ns.runs_dir), keep_per_flow=ns.keep_per_flow, keep_days=ns.keep_days)
    for d, reason in plan.candidates:
        print(f"delete: {d}")
        print(f"  nothing protects it: {reason}")
    print(
        f"gc: {len(plan.candidates)} candidate(s), {plan.kept} kept, "
        f"{plan.skipped} non-run dir(s) untouched"
    )
    if not plan.candidates:
        return EXIT_OK
    if ns.apply:
        deleted = apply_gc(plan)
        print(f"gc: deleted {deleted} run dir(s)")
    else:
        print("gc: dry run — nothing deleted (pass --apply to delete the above)")
    return EXIT_OK


def cmd_active(ns) -> int:
    """Every unfinished run under a runs root, with who (if anyone) is driving
    it (consumer report 2026-08-13, minor item).

    The question this answers is "is it safe to touch the working tree" — which
    previously meant listing run dirs by hand, reading each `lock`, and
    cross-referencing pids against the OS process table. Read-only; spends
    nothing; always exits 0 (a listing has no verdict to report)."""
    root = Path(ns.runs_dir)
    dirs = sorted(root.iterdir()) if root.is_dir() else []
    rows = 0
    live = 0
    idle = 0
    for d in dirs:
        if not (d / "state.json").exists():
            continue
        try:
            state = load_state(d)
        except (OSError, ValueError):
            continue
        unfinished = sorted(
            n for n, r in state.nodes.items() if r.status in ("pending", "running", "blocked")
        )
        info = inspect_lock(d)
        if not unfinished and info.state == "none":
            continue
        running = sorted(n for n, r in state.nodes.items() if r.status == "running")
        if info.state == "alive":
            tag, live = "RUNNING", live + 1
        elif info.state == "foreign":
            tag = "FOREIGN"
        elif info.state == "unknown":
            tag = "STARTING?"
        elif info.state == "dead" or running:
            tag = "STALE"
        else:
            # Unfinished, but nobody ever claimed it and nothing says `running`
            # — a run stopped at a gate or a budget, possibly months ago. Every
            # such run is unfinished forever, so listing them by default buries
            # the one question this command exists to answer.
            tag = "IDLE"
            idle += 1
            if not ns.all:
                continue
        rows += 1
        print(f"{tag:<10} {d.name}   flow: {state.flow_name}")
        print(f"  {info.describe()}")
        print(f"  unfinished: {len(unfinished)} node(s)"
              + (f"; running: {', '.join(running)}" if running else ""))
        if tag == "STALE":
            print(f"  reclaim:    lockstep resume {d}")
    idle_note = (
        "" if (ns.all or not idle)
        else f"; {idle} idle unfinished run(s) not shown (--all)"
    )
    if not rows:
        print(f"active: nothing is driving a run under {root}{idle_note}")
        return EXIT_OK
    print(f"active: {rows} run(s) under {root}; {live} with a live driver{idle_note}")
    if live:
        # The reason a domain expert asks: a live run may be writing the tree.
        print("a RUNNING run may be writing the working tree — leave it alone until it settles")
    return EXIT_OK


def cmd_explain(ns) -> int:
    """A1: which recorded hash inputs moved. The node modes read state only;
    `--graph` (parity 3.2) plans every node into a throwaway directory and
    compares against the record — still zero spawns, zero tokens."""
    from .explain import explain_graph, explain_node

    if getattr(ns, "graph", False):
        if ns.node_id:
            return _fail("--graph takes no node id (it covers the whole graph)", EXIT_CONFIG)
        repo_root = Path(ns.repo_root).resolve()
        try:
            config = load_config(
                Path(ns.config) if getattr(ns, "config", None) else repo_root / "lockstep.toml"
            )
        except ConfigError as e:
            return _fail(str(e), EXIT_CONFIG)
        return explain_graph(Path(ns.run_dir), repo_root=repo_root, config=config)
    if not ns.node_id:
        return _fail("pass a node id, or --graph for the whole-graph dry run", EXIT_CONFIG)
    return explain_node(
        Path(ns.run_dir), ns.node_id, Path(ns.against) if ns.against else None
    )


def cmd_render(ns) -> int:
    try:
        tg, _ = _load(ns.flow)
    except FlowError as e:
        return _fail(str(e), EXIT_VERIFY)
    print(render_mermaid(tg))
    return EXIT_OK


def cmd_wait(ns) -> int:
    """C2 (LESSONS-TO-MECHANISMS, lesson 23): block until the run's driver
    exits, then exit with the run's meaning — replaces the fragile
    `sleep N && lockstep status` loop and the platform-specific
    `tail -F events.jsonl | grep -m1` incantation.

    Exit: 0 all required nodes done; 2 something blocked; 3 something failed;
    6 an approval was rejected; 4 stopped with runnable work remaining
    (budget/limit/kill — a plain resume continues); 1 --timeout expired with
    the lock still held."""
    run_dir = Path(ns.run_dir)
    # A locked dir without state.json is a run whose driver holds the lock but
    # has not written its first state yet — wait, don't fail (cmd_run acquires
    # the lock BEFORE writing state so this window reads as "running").
    if not (run_dir / "state.json").exists() and not (run_dir / "lock").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    deadline = time.monotonic() + ns.timeout if ns.timeout else None
    while (run_dir / "lock").exists():
        # A driver that died holding the lock releases it never, so the old
        # `while lock exists` loop waited forever on a run nobody was
        # advancing (reported live: 97 minutes). A dead SAME-HOST holder is
        # the one case we can call with certainty; `foreign` and `unknown`
        # keep waiting, exactly as `acquire_lock` refuses to clear them.
        info = inspect_lock(run_dir)
        if info.state == "dead":
            print(f"wait: STALE — the lock {info.describe()}")
            print(f"  nothing is driving this run; `lockstep resume {run_dir}` reclaims it")
            break
        if deadline is not None and time.monotonic() > deadline:
            print(f"wait: lock still held after {ns.timeout:g}s (a crashed driver leaves "
                  f"a stale lock — check `lockstep status` and the lock file)")
            return 1
        time.sleep(ns.poll)
    if not (run_dir / "state.json").exists():
        return _fail(f"{run_dir} has no state.json", EXIT_CONFIG)
    state = load_state(run_dir)
    recs = list(state.nodes.values())
    statuses = [r.status for r in recs]
    counts = {s: statuses.count(s) for s in sorted(set(statuses))}
    # Mirror the engine's own precedence (gate_block > approval_rejected >
    # failed). The engine records EVERY rejection as the approval node
    # `blocked` with "reject" in the error; rejection.txt is only the
    # cockpit's evidence artifact — and it is STICKY (nothing deletes it, gc
    # protects it), so it may only count while some approval is still not
    # done. Otherwise a rejected-then-resumed-and-approved run reports 6
    # forever (adversarial-review finding 3).
    rejected = any(
        r.role == "approval" and r.status == "blocked" and "reject" in (r.error or "")
        for r in recs
    ) or (
        (run_dir / "rejection.txt").exists()
        and any(r.role == "approval" and r.status != "done" for r in recs)
    )
    # A gate blocked by PROPAGATION (an upstream failure/rejection, or another
    # gate) is not the origin — the origin gate carries its own verdict reason,
    # while propagation errors are "upstream failed or blocked" / "gate X
    # blocked: ...". Without the filter, a gate downstream of a rejected
    # approval would mask the rejection as exit 2.
    gate_blocked = any(
        r.role == "gate" and r.status == "blocked"
        and not (r.error or "").startswith(("upstream ", "gate "))
        for r in recs
    )
    if gate_blocked:
        code = EXIT_GATE_BLOCK
    elif rejected:
        code = EXIT_APPROVAL_REJECTED
    elif any(s == "failed" for s in statuses):
        code = EXIT_NODE_FAILED
    elif all(s in ("done", "skipped") for s in statuses):
        code = EXIT_OK
    else:
        code = EXIT_BUDGET  # stopped mid-run: budget, provider limit, or kill
    print(f"wait: run settled — {counts} (exit {code})")
    return code


def _liveness_lines(run_dir: Path, state: RunState) -> list[str]:
    """Is anything actually driving this run? (consumer report 2026-08-13 item 4)

    `status` used to print `running` for a node whose driver had been dead for
    an hour and a half, because nothing but `acquire_lock` ever asked. The
    determination is free and read-only; the only thing that was missing was
    somewhere to say it.
    """
    info = inspect_lock(run_dir)
    running = sorted(n for n, r in state.nodes.items() if r.status == "running")
    lines: list[str] = []
    if info.state == "dead":
        lines.append(f"STALE: the lock {info.describe()}")
    elif info.state != "none":
        lines.append(f"lock: {info.describe()}")
    if info.state in ("dead", "none") and running:
        lines.append(
            f"STALE: {len(running)} node(s) recorded 'running' with no live driver: "
            + ", ".join(running)
        )
    if lines and lines[-1].startswith("STALE"):
        lines.append(f"  nothing is advancing this run — `lockstep resume {run_dir}` reclaims it")
    return lines


def cmd_status(ns) -> int:
    run_dir = Path(ns.run_dir)
    try:
        state = load_state(run_dir)
    except (OSError, ValueError) as e:
        return _fail(f"cannot read state: {e}", EXIT_CONFIG)
    print(f"flow: {state.flow_name}   started: {state.started_at}   token spawns: {state.token_spawns}")
    for line in _liveness_lines(run_dir, state):
        print(line)
    if state.driver_version:
        drift = "" if state.driver_version == __version__ else f"  (installed: {__version__})"
        print(f"driver: {state.driver_version}{drift}")
    if state.workspace_kind == "null":
        print("workspace: null (external-edit detection off)")
    seeded = sorted(n for n, r in state.nodes.items() if r.seeded_from)
    if seeded:
        # E7 provenance where a reader will actually meet it: these results
        # were produced by ANOTHER run, under its tree and its provider. The
        # token-spawn count above is honest precisely because they cost none.
        source = state.nodes[seeded[0]].seeded_from
        print(f"seeded: {len(seeded)} node(s) served from {source} — {', '.join(seeded)}")
    forced = sorted(
        n for n, r in state.nodes.items()
        if any("forced stale" in reason for reason in (r.invalidated_by or []))
    )
    if forced:
        # Parity 3.3: forced ≠ hash-missed. These re-billed on instruction,
        # with inputs that may not have moved at all.
        print(f"forced stale: {len(forced)} node(s) re-ran by --force-stale — "
              f"{', '.join(forced)}")
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


def cmd_verify_trace(ns) -> int:
    """Recompute the run journal's hash chain. Exit 5 on a broken chain or a
    head mismatch — this is verification, and 5 is its frozen code."""
    run_dir = Path(ns.run_dir)
    if not (run_dir / "events.jsonl").exists():
        return _fail(f"{run_dir} has no events.jsonl", EXIT_CONFIG)
    ok, head, bad, detail = verify_trace(run_dir)
    if not ok:
        print(f"lockstep: trace BROKEN at line {bad}: {detail}", file=sys.stderr)
        print("the journal has been altered since it was written", file=sys.stderr)
        return EXIT_VERIFY
    if head:
        print(f"trace ok: {detail}")
        print(f"chain head: {head}")
    else:
        print(f"trace {detail}")
    if ns.head:
        if ns.head != head:
            print(
                f"lockstep: head mismatch — expected {ns.head}, computed {head or '(none)'}",
                file=sys.stderr,
            )
            return EXIT_VERIFY
        print("head matches the expected digest")
    return EXIT_OK


def cmd_doctor(ns) -> int:
    repo_root = Path(".").resolve()
    if getattr(ns, "setup", False):
        # --setup spends nothing and needs no config: it is what a domain
        # expert runs alone on a machine nobody can inspect for them.
        return run_doctor(LockstepConfig(), repo_root=repo_root, setup_only=True)
    try:
        config = load_config(Path(ns.config) if ns.config else repo_root / "lockstep.toml")
    except ConfigError as e:
        return _fail(str(e), EXIT_CONFIG)
    return run_doctor(config, repo_root=repo_root, runs_dir=Path(ns.runs_dir))


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
# pi >= 0.81: JSON output is `--mode json`. `--no-session` keeps doctor probes
# out of the session store; for audit-lineage capture (ADDENDUM-A A.3.4) use
# `"--session-dir", "{phase_dir}"` instead — {phase_dir} expands at spawn and
# stays out of input_hash. Every spawn also exports LOCKSTEP_NODE_ID / _ROLE /
# _WORKSPACE_SCOPE / _VERDICT_FILE / _PHASE_DIR for in-harness enforcement
# extensions (contrib/pi-extension/lockstep-guard.ts).
argv = ["pi", "-p", "--mode", "json", "--no-session", "{prompt}"]
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
    if state.nodes[ns.node_id].kind == "flow":
        # Post-build composition review: a flow node has no prompt, and its
        # hash deliberately ignores steering — accepting the message would
        # re-run the node, serve the cached child, and mark the message
        # consumed having changed NOTHING. Refuse loudly instead.
        children = sorted(p.name for p in (run_dir / "children").glob(f"{ns.node_id}-*"))
        hint = (f"steer its child directly: lockstep steer "
                f"{run_dir / 'children' / children[-1]} <node> \"...\""
                if children else
                "the child has not started yet; steer its nodes once it exists "
                f"under {run_dir / 'children'}")
        return _fail(
            f"{ns.node_id!r} is a flow node — it has no prompt to steer, and the "
            f"message would be consumed without changing anything; {hint}",
            EXIT_CONFIG,
        )
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
    # The Job Object name, when the spawn got one, is the reliable handle on the
    # tree; the pid is the fallback. Both are recorded by the executor.
    job_file = phase / "job_name.txt"
    jn = job_file.read_text(encoding="utf-8").strip() if job_file.exists() else None
    if not kill_pid_tree(pid, jn or None):
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
    pr.add_argument("--detach", action="store_true",
                    help="run in a process that outlives this one; prints the run dir and pid "
                         "and exits 0 (stdin is the null device: approvals auto-reject)")
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--estimate", action="store_true",
                    help="plan plus an honest cost floor from prior runs; spends nothing")
    pr.add_argument("--replay", default=None, metavar="RUN_DIR",
                    help="serve results recorded in RUN_DIR instead of spawning; spends nothing")
    pr.add_argument("--replay-any", action="store_true",
                    help="with --replay: use recordings whose input_hash no longer matches")
    pr.add_argument("--seed", default=None, metavar="RUN_DIR",
                    help="warm-start a new lineage: serve any node whose input_hash matches a "
                         "successful result in RUN_DIR, run the rest (E7). Trusts a prior "
                         "RESULT, not a prior TREE — not a 'start over cleanly' mechanism; "
                         "use --fresh when the tree state is what went wrong")
    pr.add_argument("--force-stale", action="append", default=None, metavar="NODE",
                    help="with --seed: never serve NODE or anything downstream of it — they "
                         "run for real, everything else seeds as usual (recompute with "
                         "--apply, parity 3.3; `explain <seed_run> --graph` is the dry run). "
                         "Repeatable. `status` and the journal record forced vs hash-missed")
    pr.add_argument("--otel-file", nargs="?", const="", default=None, metavar="PATH",
                    help="write OTLP/JSON spans (GenAI semantic conventions); "
                         "bare flag writes <run_dir>/spans.jsonl")
    pr.add_argument("--allow-dirty-scope", action="store_true",
                    help="start even when uncommitted working-tree changes fall inside a "
                         "node's declared spec.writes (they may be legally overwritten)")
    pr.set_defaults(fn=cmd_run)

    pres = sub.add_parser("resume", help="resume a run dir")
    pres.add_argument("run_dir")
    pres.add_argument("--flow", default=None, help="the flow file this run was started from")
    pres.add_argument("--config", default=None)
    pres.add_argument("--repo-root", default=".")
    pres.add_argument("--max-workers", type=int, default=2)
    pres.add_argument("--force-unlock", action="store_true")
    pres.add_argument("--detach", action="store_true",
                      help="resume in a process that outlives this one; prints the run dir and "
                           "pid and exits 0 (stdin is the null device: approvals auto-reject)")
    pres.add_argument("--otel-file", nargs="?", const="", default=None, metavar="PATH",
                      help="write OTLP/JSON spans; a resume joins the run's existing trace")
    pres.add_argument("--cockpit", action="store_true",
                      help="restrict approval prompts to [a]pprove / [r]eject; the cockpit's "
                           "APPROVAL pane passes this so a domain expert cannot reach [e]dit")
    pres.set_defaults(fn=cmd_resume)

    pv = sub.add_parser("verify", help="static verification only")
    pv.add_argument("flow")
    pv.add_argument("--repo-root", default=".")
    pv.add_argument("--config", default=None,
                    help="executor config to verify against (default: <repo-root>/lockstep.toml); "
                         "without it a flow whose stanzas live in a shared config file "
                         "false-positives no-executor-stanza (C1)")
    pv.add_argument("--lint", action="store_true",
                    help="also print advisory anti-pattern warnings; never changes the exit code")
    pv.set_defaults(fn=cmd_verify)

    prend = sub.add_parser("render", help="Mermaid to stdout")
    prend.add_argument("flow")
    prend.set_defaults(fn=cmd_render)

    pst = sub.add_parser("status", help="run status")
    pst.add_argument("run_dir")
    pst.set_defaults(fn=cmd_status)

    pw = sub.add_parser(
        "wait",
        help="block until the run's driver exits, then exit with the run's meaning "
             "(0 done / 2 blocked / 3 failed / 6 rejected / 4 stopped-resumable / "
             "1 --timeout expired)",
    )
    pw.add_argument("run_dir")
    pw.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                    help="give up after SECONDS with exit 1 (default: wait forever)")
    pw.add_argument("--poll", type=float, default=2.0, metavar="SECONDS",
                    help="lock-check interval (default 2s)")
    pw.set_defaults(fn=cmd_wait)

    pvt = sub.add_parser("verify-trace", help="recompute the run journal's hash chain")
    pvt.add_argument("run_dir")
    pvt.add_argument("--head", default=None,
                     help="the chain head recorded when the run finished; pins the whole journal")
    pvt.set_defaults(fn=cmd_verify_trace)

    pd = sub.add_parser("doctor", help="check the setup, then probe each configured executor")
    pd.add_argument("--config", default=None)
    pd.add_argument("--setup", action="store_true",
                    help="setup checks only: free, no model calls, no config needed")
    pd.add_argument("--runs-dir", default="runs",
                    help="where the success record lands (read by `run`'s staleness advisory)")
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

    pgc = sub.add_parser("gc", help="estimate-aware retention for runs/ (dry-run unless --apply)")
    pgc.add_argument("runs_dir", nargs="?", default="runs")
    pgc.add_argument("--keep-per-flow", type=int, default=5,
                     help="newest runs kept per flow definition (the history --estimate mines)")
    pgc.add_argument("--keep-days", type=int, default=14)
    pgc.add_argument("--apply", action="store_true", help="actually delete; default is a dry run")
    pgc.set_defaults(fn=cmd_gc)

    pact = sub.add_parser(
        "active", help="runs under a runs root that something claims to be driving (--all: every "
                       "unfinished run)"
    )
    pact.add_argument("runs_dir", nargs="?", default="runs")
    pact.add_argument("--all", action="store_true",
                      help="also list unfinished runs nobody is driving (stopped at a gate or "
                           "a budget, possibly long ago)")
    pact.set_defaults(fn=cmd_active)

    pex = sub.add_parser("explain", help="which recorded hash inputs moved for a node (A1); "
                                         "--graph = whole-graph staleness dry run (3.2)")
    pex.add_argument("run_dir")
    pex.add_argument("node_id", nargs="?", default=None)
    pex.add_argument("--against", default=None, metavar="RUN_DIR",
                     help="diff this run's recorded parts against another run dir's")
    pex.add_argument("--graph", action="store_true",
                     help="plan the WHOLE graph against the current tree and report what "
                          "would re-run and why; plans into a throwaway dir, spawns nothing")
    pex.add_argument("--repo-root", default=".")
    pex.add_argument("--config", default=None, metavar="TOML",
                     help="config to plan against (default: <repo-root>/lockstep.toml)")
    pex.set_defaults(fn=cmd_explain)

    ns = p.parse_args(argv)
    # `--detach` re-invokes this exact command in a child, so it needs the
    # command line verbatim — reconstructing it from the namespace would drop
    # anything a future flag adds.
    ns._argv = list(argv) if argv is not None else sys.argv[1:]
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
