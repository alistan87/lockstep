"""Executor config (lockstep.toml, SPEC §8.2) and the kind -> Executor registry.

An executor entry is an argv template: a new harness or a renamed flag is a
config edit, never a code change. Spawned via argv lists only — never shell=True.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class ExecutorStanza(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str]
    prompt_via: Literal["argv", "stdin"] = "argv"
    json_field: str | None = None  # unwrap this envelope field from stdout; omit for raw
    persona_flag: list[str] = []  # empty ⇒ prepend persona body to the prompt
    readonly_argv: list[str] | None = None  # appended for spec.readonly nodes; absent ⇒
    # readonly nodes on this executor are a verification error (SPEC §6.11)


class LockstepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str | None = None
    executors: dict[str, ExecutorStanza] = {}
    digest: str = ""  # sha256 of the config file bytes; a harness fingerprint part
    path: str = ""


class ConfigError(Exception):
    """lockstep.toml unreadable or invalid (exit 7)."""


def load_config(path: Path | None) -> LockstepConfig:
    if path is None or not Path(path).exists():
        return LockstepConfig(digest=hashlib.sha256(b"").hexdigest())
    raw = Path(path).read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot parse {path}: {e}")
    try:
        cfg = LockstepConfig.model_validate(
            {
                "default": data.get("default"),
                "executors": data.get("executors", {}),
            }
        )
    except ValidationError as e:
        raise ConfigError(f"invalid executor config in {path}: {e}")
    cfg.digest = hashlib.sha256(raw).hexdigest()
    cfg.path = str(path)
    return cfg


class Registry:
    """kind -> Executor lookup. Unknown kinds are rejected by verification with a
    named error, never ignored."""

    def __init__(self) -> None:
        self._executors: dict[str, object] = {}

    def register(self, executor) -> None:
        self._executors[executor.kind] = executor

    def get(self, kind: str):
        return self._executors.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._executors)


def build_registry(config: LockstepConfig, repo_root: Path) -> Registry:
    # Imported here, not at module top: executors import protocols which imports
    # taskgraph; keeping registry import-light avoids a cycle.
    from .executors.harness import HarnessExecutor
    from .executors.shell import ShellExecutor

    reg = Registry()
    reg.register(HarnessExecutor(config=config, repo_root=repo_root))
    reg.register(ShellExecutor(repo_root=repo_root))
    return reg
