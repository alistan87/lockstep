"""Interpolation, data fencing, spill-to-file, and `when` evaluation (SPEC §7).

Reference forms: {args.K} · {steps.ID.output} · {steps.ID.json} / {steps.ID.json.a.b}
· {item} / {item.field} (map body) · {previous.output}. "{{" escapes to "{".
Unresolved placeholder = hard error before spawn, except the skip rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STUB_HEAD_CHARS = 500
FENCE_BEGIN = "--- begin data: {ref} (untrusted) ---"
FENCE_END = "--- end data ---"

# A placeholder body: dotted path of identifier-ish segments.
_REF_RE = re.compile(r"\{([A-Za-z0-9_][A-Za-z0-9_.\-]*)\}")
_ESCAPE_SENTINEL = "\x00LBRACE\x00"

_WHEN_RE = re.compile(r"^\s*\{([A-Za-z0-9_][A-Za-z0-9_.\-]*)\}\s*(==|!=)\s*(.+?)\s*$")


class InterpolationError(Exception):
    pass


class SkippedReference(Exception):
    """A reference points at a skipped node; caller decides skip-vs-null (SPEC §7, A2)."""

    def __init__(self, ref: str):
        super().__init__(ref)
        self.ref = ref


def extract_refs(template: str) -> list[str]:
    """All placeholder bodies in a template, escapes honored. For static verification."""
    return _REF_RE.findall(template.replace("{{", _ESCAPE_SENTINEL))


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass
class ResolveCtx:
    """What a template resolves against."""

    args: dict[str, str] = field(default_factory=dict)
    # node_id -> raw result text (for .output) — None if node produced no text
    outputs: dict[str, str | None] = field(default_factory=dict)
    # node_id -> parsed JSON result (for .json)
    json_results: dict[str, Any] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    deps: list[str] = field(default_factory=list)  # for {previous.output}
    item: Any = None
    has_item: bool = False
    item_var: str = "item"  # role=map rename of {item}


def _navigate(value: Any, path: list[str], ref: str) -> Any:
    for seg in path:
        if isinstance(value, dict):
            if seg not in value:
                raise InterpolationError(f"unresolved reference {{{ref}}}: no key {seg!r}")
            value = value[seg]
        elif isinstance(value, list):
            try:
                value = value[int(seg)]
            except (ValueError, IndexError):
                raise InterpolationError(f"unresolved reference {{{ref}}}: bad index {seg!r}")
        else:
            raise InterpolationError(f"unresolved reference {{{ref}}}: cannot descend into {type(value).__name__}")
    return value


def resolve_ref(ref: str, ctx: ResolveCtx) -> tuple[Any, bool]:
    """Resolve one reference body. Returns (value, is_json_value).

    is_json_value=True means the value must be compact-JSON-serialized when
    rendered into text; False means it is inserted raw (already text).
    Raises SkippedReference for references into skipped nodes.
    """
    parts = ref.split(".")
    head = parts[0]
    if head == "args":
        if len(parts) != 2:
            raise InterpolationError(f"malformed args reference {{{ref}}}")
        k = parts[1]
        if k not in ctx.args:
            raise InterpolationError(f"unresolved reference {{{ref}}}: undeclared arg")
        return ctx.args[k], False
    if head == "previous":
        if parts[1:] != ["output"]:
            raise InterpolationError(f"malformed reference {{{ref}}}: only {{previous.output}} is supported")
        if len(ctx.deps) != 1:
            raise InterpolationError(
                f"{{previous.output}} requires exactly one dependency, node has {len(ctx.deps)}"
            )
        return resolve_ref(f"steps.{ctx.deps[0]}.output", ctx)
    if head == ctx.item_var or head == "item":
        if not ctx.has_item:
            raise InterpolationError(f"{{{ref}}} used outside a map body")
        value = _navigate(ctx.item, parts[1:], ref)
        # Strings insert raw (prompt-friendly); everything else compact JSON.
        return value, not isinstance(value, str)
    if head == "steps":
        if len(parts) < 3:
            raise InterpolationError(f"malformed reference {{{ref}}}")
        node_id, channel = parts[1], parts[2]
        if node_id in ctx.skipped:
            raise SkippedReference(ref)
        if channel == "output":
            if parts[3:]:
                raise InterpolationError(f"malformed reference {{{ref}}}: .output takes no sub-path")
            if node_id not in ctx.outputs:
                raise InterpolationError(f"unresolved reference {{{ref}}}: no result for node {node_id!r}")
            return ctx.outputs[node_id] or "", False
        if channel == "json":
            if node_id not in ctx.json_results:
                raise InterpolationError(f"unresolved reference {{{ref}}}: no JSON result for node {node_id!r}")
            value = _navigate(ctx.json_results[node_id], parts[3:], ref)
            return value, True
        raise InterpolationError(f"malformed reference {{{ref}}}: unknown channel {channel!r}")
    raise InterpolationError(f"unresolved reference {{{ref}}}")


@dataclass
class Rendered:
    """A rendered template.

    prompt_text: what the spawned process sees (fenced, spilled values as stubs).
    hash_text:   what enters input_hash (fenced, FULL pre-spill values).

    The FULL value's hash — not the stub — enters input_hash, so truncation never
    masks a change; and the stub's run-specific absolute spill path is deliberately
    EXCLUDED from the hash (it appears only in prompt_text). This asymmetry is per
    SPEC §7 and looks like a hash bug; it is not — do not "fix" it.
    """

    prompt_text: str
    hash_text: str
    spilled: dict[str, str] = field(default_factory=dict)  # ref -> spill file path


def _safe_filename(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", ref) or "value"


def _spill(ref: str, full: str, spill_dir: Path) -> tuple[str, str]:
    """Write the full value to a spill file; return (stub, path)."""
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{_safe_filename(ref)}.json"
    path.write_text(full, encoding="utf-8")
    stub = (
        full[:STUB_HEAD_CHARS]
        + f"\n[truncated: {len(full)} chars total]\n"
        + f"Full value at: {path.resolve()} — read that file for the full value."
    )
    return stub, str(path.resolve())


def fence_block(ref: str, content: str) -> str:
    return f"{FENCE_BEGIN.format(ref=ref)}\n{content}\n{FENCE_END}"


def render_template(
    template: str,
    ctx: ResolveCtx,
    *,
    fence: bool,
    max_interp_chars: int,
    spill_dir: Path | None,
    null_for_skipped: bool = False,
) -> Rendered:
    """Render a template.

    fence=True (harness prompts): every interpolated value is wrapped in data
    fences and spilled when longer than max_interp_chars. fence=False (shell
    argv, `over`): raw values, no fencing, no spill.
    null_for_skipped: substitute the literal `null` for skipped-node references
    (the `optional: true` path) instead of raising SkippedReference.
    """
    work = template.replace("{{", _ESCAPE_SENTINEL)
    spilled: dict[str, str] = {}

    def _sub(m: re.Match, *, for_hash: bool) -> str:
        ref = m.group(1)
        try:
            value, is_json = resolve_ref(ref, ctx)
        except SkippedReference:
            if not null_for_skipped:
                raise
            value, is_json = None, True
        text = compact_json(value) if is_json else str(value)
        if not fence:
            # A shell argv element is ALREADY a discrete string, so the quotes
            # from compact-JSON become part of the value: a path arrives as
            # `"docs/x.json"` and the program opens a file whose name starts
            # with a quote. Strings therefore render raw here — the same choice
            # `{item.field}` already makes one function away. Prompts
            # (fence=True) and `when` (eval_when, a separate path) keep §7's
            # compact-JSON semantics untouched. r7 candidate; see DEVIATIONS.
            return str(value) if isinstance(value, str) else text
        if for_hash:
            return fence_block(ref, text)
        if len(text) > max_interp_chars:
            if spill_dir is None:
                raise InterpolationError(f"value for {{{ref}}} exceeds cap and no spill dir given")
            stub, path = _spill(ref, text, spill_dir)
            spilled[ref] = path
            return fence_block(ref, stub)
        return fence_block(ref, text)

    hash_text = _REF_RE.sub(lambda m: _sub(m, for_hash=True), work)
    prompt_text = _REF_RE.sub(lambda m: _sub(m, for_hash=False), work)
    return Rendered(
        prompt_text=prompt_text.replace(_ESCAPE_SENTINEL, "{"),
        hash_text=hash_text.replace(_ESCAPE_SENTINEL, "{"),
        spilled=spilled,
    )


def fence_context_file(
    rel_path: str, content: str, *, max_interp_chars: int, spill_dir: Path | None
) -> tuple[str, str]:
    """Fence a spec.context file for the prompt; same spill treatment as values.
    Returns (prompt_block, hash_block) — hash gets the full content (see Rendered)."""
    ref = f"file:{rel_path}"
    hash_block = fence_block(ref, content)
    if len(content) > max_interp_chars:
        if spill_dir is None:
            # Mirror render_template: never silently embed an over-cap value
            # (audit finding — the branch is latent, but consistency matters).
            raise InterpolationError(f"context file {rel_path!r} exceeds cap and no spill dir given")
        stub, _ = _spill(ref, content, spill_dir)
        return fence_block(ref, stub), hash_block
    return hash_block, hash_block


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- `when` evaluation (SPEC §7; A2) -------------------------------------------

def parse_when(expr: str) -> tuple[str, str, str]:
    """Parse '{ref} ==|!= <literal>' -> (ref, op, literal_text). Literal must be
    valid JSON. Raises InterpolationError naming the construct otherwise."""
    m = _WHEN_RE.match(expr)
    if not m:
        raise InterpolationError(
            f"`when` must match '{{ref}} ==|!= <literal>'; got {expr!r}"
        )
    ref, op, literal = m.group(1), m.group(2), m.group(3)
    try:
        json.loads(literal)
    except json.JSONDecodeError:
        raise InterpolationError(
            f"`when` literal must be written in JSON form (true, null, 5, \"foo\"); got {literal!r}"
        )
    return ref, op, literal


def eval_when(expr: str, ctx: ResolveCtx) -> bool:
    """Evaluate a `when`. The resolved reference is compact-JSON-serialized and
    compared AS A STRING against the literal's compact serialization. References
    to skipped nodes resolve to null — `when` is exempt from transitive skip (A2)."""
    ref, op, literal = parse_when(expr)
    try:
        value, is_json = resolve_ref(ref, ctx)
        serialized = compact_json(value) if is_json else compact_json(str(value))
    except SkippedReference:
        serialized = "null"
    lit_norm = compact_json(json.loads(literal))
    eq = serialized == lit_norm
    return eq if op == "==" else not eq
