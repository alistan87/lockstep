"""Built-in output contracts (SPEC §5), frozen, plus the contract resolver.

`contract` on a node names either a built-in ("Verdict", "CheckResult[]" = JSON
array of), a name resolved via the flow's `contracts_module`, or an explicit
"module:Name" / "path/to/file.py:Name". The driver validates results against
these; no model is trusted to self-report conformance.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import types
from dataclasses import dataclass
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError


class CheckResult(BaseModel):
    schema_version: str = "1.0"
    command: str
    exit_code: int
    summary: str  # <= 5 lines by convention; not enforced


class StepResult(BaseModel):
    schema_version: str = "1.0"
    step_id: str
    status: Literal["done", "failed", "skipped"]
    files_written: list[str]
    notes: str = ""  # advisory only — never used for rollback (SPEC §9.4)


class Finding(BaseModel):
    schema_version: str = "1.0"
    severity: Literal["blocker", "major", "minor", "nit"]
    category: str
    file: str
    line: int | None = None
    claim: str
    evidence: str
    fix_hint: str = ""


class Verdict(BaseModel):
    """The gate contract (SPEC §9.3)."""

    schema_version: str = "1.0"
    findings: list[Finding]
    verdict: Literal["pass", "block"]
    reason: str


class PathManifest(BaseModel):
    schema_version: str = "1.0"
    files: list[str]
    notes: str = ""


# Defined now, used in v2 (SPEC §16):
class ProgressEvent(BaseModel):
    schema_version: str = "1.0"
    step: str
    pct: int | None = None
    note: str = ""


class SteerMessage(BaseModel):
    schema_version: str = "1.0"
    ts: str
    author: str
    message: str
    consumed: bool = False


BUILTINS: dict[str, type[BaseModel]] = {
    "CheckResult": CheckResult,
    "StepResult": StepResult,
    "Finding": Finding,
    "Verdict": Verdict,
    "PathManifest": PathManifest,
    "ProgressEvent": ProgressEvent,
    "SteerMessage": SteerMessage,
}


class ContractError(Exception):
    """A contract name that cannot be resolved, or a result that fails it."""


@dataclass(frozen=True)
class ContractRef:
    name: str  # as written in the flow
    model: type[BaseModel]
    is_array: bool


def _load_module(spec: str):
    """Import `spec` as a module: dotted path via importlib, *.py via file
    location. ANY failure — including a SyntaxError or an exception raised by
    the module body — becomes a ContractError, so `lockstep verify` reports a
    §6 finding (exit 5) instead of crashing with a traceback (audit finding)."""
    try:
        if spec.endswith(".py") or "/" in spec or "\\" in spec:
            modspec = importlib.util.spec_from_file_location("lockstep_contracts_ext", spec)
            if modspec is None or modspec.loader is None:
                raise ContractError(f"cannot load contracts module from file: {spec!r}")
            mod = importlib.util.module_from_spec(modspec)
            modspec.loader.exec_module(mod)
            return mod
        return importlib.import_module(spec)
    except ContractError:
        raise
    except Exception as e:
        raise ContractError(f"contracts module {spec!r} failed to load: {type(e).__name__}: {e}")


def resolve_contract(name: str, contracts_module: str | None = None) -> ContractRef:
    base = name[:-2] if name.endswith("[]") else name
    is_array = name.endswith("[]")
    if ":" in base:
        module_part, _, cls_name = base.rpartition(":")
        try:
            mod = _load_module(module_part)
        except (ImportError, OSError) as e:
            raise ContractError(f"contract {name!r}: module {module_part!r} not importable: {e}")
        model = getattr(mod, cls_name, None)
    elif base in BUILTINS:
        model = BUILTINS[base]
    elif contracts_module:
        try:
            mod = _load_module(contracts_module)
        except (ImportError, OSError) as e:
            raise ContractError(
                f"contract {name!r}: contracts_module {contracts_module!r} not importable: {e}"
            )
        model = getattr(mod, base, None)
    else:
        raise ContractError(f"contract {name!r} is not a built-in and no contracts_module is set")
    if model is None or not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ContractError(f"contract {name!r} does not resolve to a pydantic model")
    return ContractRef(name=name, model=model, is_array=is_array)


def _literal(a: Any) -> str:
    """One Literal arm, safely: enum members and bytes are legal typing but
    not JSON-serializable — pydantic validates them, so describing them must
    not raise where validating would not (adversarial-review finding 4)."""
    try:
        return json.dumps(a)
    except TypeError:
        return json.dumps(str(getattr(a, "value", a)))


def _json_type(ann: Any, seen: frozenset) -> str:
    """A compact, prompt-facing name for a field's JSON shape."""
    if ann is None or ann is type(None):
        return "null"
    origin = get_origin(ann)
    if origin is Literal:
        return " | ".join(_literal(a) for a in get_args(ann))
    if origin is list:
        args = get_args(ann)
        return f"array of {_json_type(args[0], seen) if args else 'any'}"
    if origin is dict:
        return "object"
    if origin is tuple:
        return "array"
    if origin in (Union, types.UnionType):
        return " or ".join(_json_type(a, seen) for a in get_args(ann))
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        if ann.__name__ in seen:
            return ann.__name__  # cycle guard: name only
        return f"object {{{_fields_desc(ann, seen | {ann.__name__})}}}"
    return {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}.get(
        ann, getattr(ann, "__name__", "value")
    )


def _fields_desc(model: type[BaseModel], seen: frozenset) -> str:
    parts = []
    for name, f in model.model_fields.items():
        opt = "" if f.is_required() else " (optional)"
        parts.append(f'"{name}": {_json_type(f.annotation, seen)}{opt}')
    return ", ".join(parts)


def describe_contract(ref: ContractRef) -> str:
    """Machine-generated statement of the contract's JSON shape, for inclusion
    in a harness prompt (LESSONS-TO-MECHANISMS E1). The driver already resolved
    the model it will validate against — telling the node nothing about it made
    every author hand-copy field names into prose, and models that guessed
    (`approved` for `verdict`, findings wrapped in fences) burned their retry
    budget on purely cosmetic mismatches. Generated from the SAME model
    `validate_result` uses, so prompt and validator cannot drift apart."""
    seen = frozenset({ref.model.__name__})
    body = _fields_desc(ref.model, seen)
    shape = (
        f"a JSON ARRAY of {ref.model.__name__} objects, each"
        if ref.is_array
        else f"ONE JSON object ({ref.model.__name__})"
    )
    return (
        f"Output contract {ref.name}: the result must be {shape} with fields "
        f"{{{body}}}. Use exactly these field names — never invent, rename, or wrap "
        f"them in an envelope; where a field lists quoted literals, the value must be "
        f"one of them verbatim. No markdown fences, no prose before or after the JSON."
    )


def validate_result(text: str, ref: ContractRef) -> Any:
    """Parse `text` as JSON and validate against the contract. Returns the parsed
    (plain-python) value on success; raises ContractError with a message suitable
    for a corrective re-spawn on failure."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise ContractError(f"result is not valid JSON: {e}")
    try:
        if ref.is_array:
            if not isinstance(value, list):
                raise ContractError(f"contract {ref.name}: expected a JSON array")
            for i, elem in enumerate(value):
                ref.model.model_validate(elem)
        else:
            ref.model.model_validate(value)
    except ValidationError as e:
        raise ContractError(f"result does not conform to {ref.name}: {e}")
    return value
