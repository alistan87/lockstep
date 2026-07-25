from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lockstep.executors.fake import FakeExecutor
from lockstep.executors.harness import HarnessExecutor
from lockstep.executors.shell import ShellExecutor
from lockstep.policy import AllowAllPolicy
from lockstep.registry import ExecutorStanza, LockstepConfig, Registry
from lockstep.roles import Engine
from lockstep.state import PhaseRecord, RunState, load_state, new_run_dir, utcnow, write_state
from lockstep.store import FileStore
from lockstep.taskgraph import TaskGraph
from lockstep.workspace import GitWorkspace, NullWorkspace

PY = sys.executable


def make_config(**stanzas) -> LockstepConfig:
    if not stanzas:
        stanzas = {"test-exec": ExecutorStanza(argv=[PY, "-c", "print('probe')"])}
    cfg = LockstepConfig(default=next(iter(stanzas)), executors=dict(stanzas))
    cfg.digest = "test-config-digest"
    return cfg


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@test")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "a.txt").write_text("original\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def plain_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "plain"
    repo.mkdir()
    return repo


def build(
    tmp_path: Path,
    flow: dict,
    repo_root: Path,
    *,
    args: dict[str, str] | None = None,
    config: LockstepConfig | None = None,
    run_dir: Path | None = None,
    state: RunState | None = None,
    max_workers: int = 2,
) -> SimpleNamespace:
    tg = TaskGraph.model_validate(flow)
    config = config or make_config()
    if run_dir is None:
        run_dir = new_run_dir(tmp_path / "runs", tg.name)
    is_git = (repo_root / ".git").exists()
    workspace = GitWorkspace(repo_root) if is_git else NullWorkspace(repo_root)
    if state is None:
        resolved_args = {k: v for k, v in tg.args.items() if v is not None}
        resolved_args.update(args or {})
        state = RunState(
            flow_name=tg.name,
            flow_hash="test-flow-hash",
            format_version=tg.format_version,
            args=resolved_args,
            nodes={n.id: PhaseRecord(node_id=n.id, role=n.role, kind=n.kind) for n in tg.nodes},
            started_at=utcnow(),
            workspace_kind="git" if is_git else "null",
        )
        write_state(run_dir, state)
    fake = FakeExecutor(repo_root=repo_root)
    reg = Registry()
    reg.register(fake)
    reg.register(ShellExecutor(repo_root=repo_root))
    reg.register(HarnessExecutor(config=config, repo_root=repo_root))
    store = FileStore(run_dir, state)
    logs: list[str] = []
    engine = Engine(
        tg=tg,
        registry=reg,
        config=config,
        workspace=workspace,
        store=store,
        policy=AllowAllPolicy(),
        repo_root=repo_root,
        max_workers=max_workers,
        log=logs.append,
    )
    return SimpleNamespace(
        engine=engine, run_dir=run_dir, fake=fake, state=state, tg=tg, store=store,
        logs=logs, config=config,
    )


def rebuild(tmp_path: Path, flow: dict, repo_root: Path, run_dir: Path, **kw) -> SimpleNamespace:
    """Fresh engine + fresh FakeExecutor over an existing run dir (a resume)."""
    return build(tmp_path, flow, repo_root, run_dir=run_dir, state=load_state(run_dir), **kw)


def calls_of(h: SimpleNamespace, node_id: str):
    return [c for c in h.fake.calls if c.node_id == node_id]
