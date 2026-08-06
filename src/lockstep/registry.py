"""Executor config (lockstep.toml, SPEC §8.2) and the kind -> Executor registry.

An executor entry is an argv template: a new harness or a renamed flag is a
config edit, never a code change. Spawned via argv lists only — never shell=True.
"""

from __future__ import annotations

import hashlib
import sys
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
    # [doctor] max_age_days: how old the last successful doctor probe may be
    # before `run` prints its one advisory line (A4). Advisory only.
    doctor_max_age_days: int = 7


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
    max_age = (data.get("doctor") or {}).get("max_age_days", 7)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 0:
        # The doctor advisory is advisory-only; a typo in its knob must not
        # hard-block every run through config validation.
        print(
            f"lockstep: ignoring [doctor] max_age_days = {max_age!r} (not a "
            "non-negative integer); using 7",
            file=sys.stderr,
        )
        max_age = 7
    try:
        cfg = LockstepConfig.model_validate(
            {
                "default": data.get("default"),
                "executors": data.get("executors", {}),
                "doctor_max_age_days": max_age,
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
