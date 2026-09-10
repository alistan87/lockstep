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

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .taskgraph import RetrySpec


class ExecutorStanza(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str]
    prompt_via: Literal["argv", "stdin"] = "argv"
    json_field: str | None = None  # unwrap this envelope field from stdout; omit for raw
    persona_flag: list[str] = []  # empty ⇒ prepend persona body to the prompt
    readonly_argv: list[str] | None = None  # appended for spec.readonly nodes; absent ⇒
    # readonly nodes on this executor are a verification error (SPEC §6.11)
    # --- Fields below were added after v1. Digest rule (throughput-parity A1,
    # DEVIATIONS 2026-09-09): classify each new field when it is added —
    # scheduling-only fields go in SCHEDULING_FIELDS and are never hashed;
    # behaviour-bearing fields are hashed only when set away from a default
    # whose absence-semantics equal the pre-field behaviour. ---
    default_retry: RetrySpec | None = None  # scheduling-only (A2): stanza-tier
    # retry default between node `retry` and the kind default. Changes when a
    # failed spawn retries, never what the spawn is — unhashed, like node
    # `retry` (r5 B3) and the persona `readonly` key.
    schema_argv: list[str] | None = None  # behaviour-bearing (C1): argv
    # fragment appended when the node has output:"json" and a resolvable
    # contract — {schema} fills with the CONTRACT's JSON schema (array
    # wrapper for Name[]), {schema_file} with a path to it in the phase dir.
    # Hashed only when set (A1); the FILLED schema is its own fingerprint
    # part (`schema:<compact-json>`), because the template cannot see a
    # Field-constraint edit.
    envelope: Literal["pi-stream"] | None = None  # behaviour-bearing (D): the
    # §8.3 stdout-fallback leg parses pi's JSONL event stream instead of
    # extracting the last balanced JSON value. Hashed only when set (A1) —
    # it changes what the result IS. The file channel still wins.

    @model_validator(mode="after")
    def _empty_schema_argv_is_absent(self):
        # A1 hygiene: [] is behaviourally identical to absent (plan() checks
        # truthiness), so hashing it would re-bill on inert config — the
        # spurious-invalidation class the digest carve-out exists to kill.
        if self.schema_argv == []:
            self.schema_argv = None
        return self

    @model_validator(mode="after")
    def _envelope_excludes_json_field(self):
        if self.envelope is not None and self.json_field is not None:
            raise ValueError(
                "envelope and json_field are mutually exclusive: json_field "
                "unwraps ONE stdout envelope object, envelope parses an event "
                "stream — a stanza cannot speak both"
            )
        return self


# The v1 field set: ALWAYS serialized into the stanza digest, defaults
# included — byte-identical to the pre-A1 model_dump() digest for every
# stanza that existed then (pinned by tests/test_stanza_digest.py RECORDED).
V1_DIGEST_FIELDS: tuple[str, ...] = (
    "argv", "prompt_via", "json_field", "persona_flag", "readonly_argv",
)
# Scheduling-only fields: change when/whether a spawn retries or waits, never
# what the spawn is. NEVER hashed — hashing one re-bills cached nodes on a
# knob that does not reach the spawn, the spurious-invalidation class r5 B1
# exists to kill.
SCHEDULING_FIELDS: frozenset[str] = frozenset({"default_retry"})


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
