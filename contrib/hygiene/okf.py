#!/usr/bin/env python
"""okf.py — Open Knowledge Format v0.2 frontmatter: parse, validate, inject.

Pinned against docs/okf/OKF-v0.2-conformance.md. The format is deliberately
liberal — a consumer must not reject a bundle for unknown types, missing
optional fields, or broken links — so this validator FAILS only on the two
things conformance actually requires: unparseable frontmatter, and a missing or
empty `type`. Everything else is a note.

The one hard guarantee here: **injection never alters the document body.** The
body is preserved byte for byte, and a test pins its sha. A tool that quietly
reflows someone's prose while adding metadata is worse than no tool.

No YAML dependency. pydantic is this repo's only runtime dependency and that is
a rule worth keeping, so this parses and emits the small, flat subset of YAML
the format needs: `key: value`, `key: [a, b]`, and nested one-level maps. It
REFUSES rather than guesses on anything richer, which is the honest failure for
a document somebody hand-wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FENCE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
RESERVED = ("index.md", "log.md")

# `type:` at the top level, found without a full parse. Needed because this
# module handles a deliberately small YAML subset, and "my parser cannot
# represent this" is NOT the same fact as "this frontmatter is invalid". The
# format documents block sequences (`sources:` entries) as standard spelling, so
# reporting them as a conformance failure would fail valid documents.
TYPE_LINE = re.compile(r"^type:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


class Unrepresentable(ValueError):
    """Valid YAML that this module declines to rewrite.

    Distinct from malformed frontmatter: the first means "hands off, we cannot
    round-trip this safely", the second is a conformance failure. Conflating
    them made the validator reject documents the format explicitly permits.
    """

# A line we can represent: `key: value`, `key:` (nested block follows), or a
# list item. Anything else and we decline to rewrite the file.
KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


@dataclass
class Frontmatter:
    fields: dict = field(default_factory=dict)
    raw: str = ""
    present: bool = False


@dataclass
class Doc:
    path: Path
    fm: Frontmatter
    body: str          # everything after the frontmatter fence, byte-exact
    newline: str = "\n"


def parse(text: str) -> tuple[Frontmatter, str]:
    """Split a document into (frontmatter, body). Body is returned untouched."""
    m = FENCE.match(text)
    if not m:
        return Frontmatter(), text
    raw = m.group(1)
    fm = Frontmatter(fields=_parse_flat_yaml(raw), raw=raw, present=True)
    return fm, text[m.end():]


def _parse_flat_yaml(raw: str) -> dict:
    """The small subset described in the module docstring. Unrepresentable
    structure raises, so callers can decline to touch the file."""
    out: dict = {}
    stack: list[tuple[int, dict]] = [(-1, out)]
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- "):
            raise Unrepresentable("block sequences are not supported by this parser")
        m = KEY_LINE.match(line)
        if not m:
            raise Unrepresentable(f"unrepresentable frontmatter line: {line!r}")
        indent, key, value = len(m.group(1)), m.group(2), m.group(3).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(value)
    return out


def _scalar(value: str):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        # Splitting on every comma is only correct for flat, unquoted scalars.
        # A quoted value containing a comma, or a nested map, would be silently
        # mis-parsed and then written BACK into the document by render(). The
        # module promises to refuse what it cannot represent; this is where it
        # has to keep that promise.
        if any(ch in inner for ch in "{}\"'"):
            raise Unrepresentable(f"flow sequence needs a real YAML parser: [{inner}]")
        return [_scalar(p.strip()) for p in inner.split(",")]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def emit(fields: dict, indent: int = 0) -> str:
    lines = []
    for key, value in fields.items():
        pad = " " * indent
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(emit(value, indent + 2).rstrip("\n"))
        elif isinstance(value, list):
            rendered = ", ".join(_quote(v) for v in value)
            lines.append(f"{pad}{key}: [{rendered}]")
        else:
            lines.append(f"{pad}{key}: {_quote(value)}")
    return "\n".join(lines) + "\n"


def _quote(value) -> str:
    s = str(value)
    # Quote only when the value would otherwise be ambiguous to a YAML reader.
    if s == "" or s[0] in "[]{}#&*!|>%@`\"'" or ": " in s or s.endswith(":"):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def load(path: Path) -> Doc:
    """Read WITHOUT newline translation.

    `Path.read_text()` opens in universal-newlines mode, which silently turns
    every `\\r\\n` into `\\n` before this module sees a single byte. The body was
    therefore already rewritten at load time — one layer earlier than the
    render() bug, and invisible to any test that compared a body it had itself
    loaded the same lossy way. `newline=""` is what makes "byte for byte" true
    rather than merely intended.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    newline = "\r\n" if "\r\n" in text[:4096] else "\n"
    fm, body = parse(text)
    return Doc(path=path, fm=fm, body=body, newline=newline)


def render(doc: Doc, fields: dict) -> str:
    """Frontmatter + the ORIGINAL body. Existing keys keep their position and
    value; new keys are appended. Nothing else about the file changes."""
    merged = dict(doc.fm.fields)
    for k, v in fields.items():
        merged.setdefault(k, v)
    # Normalise ONLY the frontmatter being emitted. The previous version ran the
    # newline fix over the whole document, rewriting every line ending in the
    # BODY — falsifying this module's one hard guarantee for any file with
    # mixed endings, and invisible to a sha test that compared a body already
    # normalised by a lossy read.
    block = emit(merged)
    head = f"---\n{block}---\n"
    if doc.newline == "\r\n":
        head = head.replace("\n", "\r\n")
    return head + doc.body


def validate(path: Path) -> list[str]:
    """Conformance problems, most severe first. Empty list = conformant.

    Only the two hard rules produce entries; the format explicitly forbids
    rejecting a bundle over anything else.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"unreadable: {e}"]

    reserved = path.name in RESERVED
    try:
        fm, _ = parse(text)
    except Unrepresentable:
        # Valid YAML this parser declines to rewrite. Conformance asks whether
        # the document HAS a non-empty `type`, not whether our subset covers it,
        # so fall back to finding the key without a full parse.
        m = TYPE_LINE.search(text.split("---", 2)[1] if text.startswith("---") else "")
        if reserved or m:
            return []
        return ["frontmatter has no non-empty `type`"]
    except ValueError as e:
        return [f"frontmatter is not parseable: {e}"]

    if not fm.present:
        # A reserved file is exempt from `type`, NOT from being read. The
        # earlier version returned before opening it, so the repo's own
        # generated indexes could have carried unparseable frontmatter and been
        # reported clean.
        return [] if reserved else ["no frontmatter block"]
    if reserved:
        return []
    kind = fm.fields.get("type")
    if not isinstance(kind, str) or not kind.strip():
        return ["frontmatter has no non-empty `type`"]
    return []


def notes(path: Path) -> list[str]:
    """Recommended-but-absent fields. Advisory ONLY — never a conformance
    failure, because a consumer must not reject a bundle for missing optional
    fields."""
    try:
        fm, _ = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f"no `{k}`" for k in ("title", "description", "tags") if k not in fm.fields]


if __name__ == "__main__":  # pragma: no cover - tiny CLI for spot checks
    import sys
    bad = 0
    for arg in sys.argv[1:]:
        for problem in validate(Path(arg)):
            print(f"{arg}: {problem}")
            bad += 1
    raise SystemExit(1 if bad else 0)
