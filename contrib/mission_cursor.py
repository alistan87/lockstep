"""The journal cursor: read only what was appended (S1.1).

Before this, a heartbeat with nothing to report read and split the ENTIRE
`events.jsonl` once a second, forever — measured at 732 KB per quiet tick on
a 740 KB journal, growing x16 across a x16 sweep
(`contrib/mission_bench.py`). The page's own docstring called the whole-file
read unavoidable. It is not.

A cursor is opaque to the client, which only echoes it back and compares it
for change. It carries three things:

    <gen>.<offset>.<ordinal>        e.g. "3f2a91c0.48213.512"

- `gen` — a digest of the journal's FIRST LINE. An append-only file's first
  line never changes, so this identifies the file's generation: a rotated,
  replaced, or recreated journal produces a different one and forces an
  explicit reset rather than a read from a meaningless offset.
- `offset` — the byte offset of the first UNCONSUMED byte, always on a line
  boundary.
- `ordinal` — how many events have been consumed. Display and diagnostics
  only; nothing keys off it.

`"0"` is the canonical start/reset cursor, which is what the client sends on
first load and after a run token change.

Two properties preserved from the line-count implementation, because both
are load-bearing (SPEC §10.3, and the driver appends while the page reads):

- **A torn trailing line is never consumed.** With byte offsets this stops
  being a special case: the cursor advances only to the end of the last
  COMPLETE line, so a half-written final line is simply bytes not yet read,
  and the next tick sees it whole.
- **A mid-file tear refuses.** A complete line that will not parse means the
  file is damaged somewhere a reader cannot reason about; the view says
  nothing and does not advance, rather than reporting a prefix as if it were
  the whole story.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

# A first event line is far under this. Read in one go so `gen` costs one
# small read rather than a scan, and so a quiet tick's cost is CONSTANT.
_HEAD_BYTES = 4096
# How far back to look for the last newline when positioning a fresh cursor.
# A journal line is far under this; the fallback below covers the rest.
_TAIL_BYTES = 65536
# A first line CAN exceed the head read: `record_terminal` journals a refusal
# whose text lists every dirty path in every declared write scope, and that is
# the journal's first line. Read on to find it - bounded, because an
# unbounded read is the cost this module exists to remove.
_GEN_SCAN_MAX = 1 << 20

_CURSOR_RE = re.compile(r"^([0-9a-f]{8})\.(\d+)\.(\d+)$")

START = ("", 0, 0)


def parse_cursor(raw: str) -> tuple[str, int, int] | None:
    """`None` means MALFORMED — the caller 404s, as it always has for a
    cursor that is not a cursor. A well-formed cursor that turns out to be
    STALE is a different thing entirely and resets silently (see `read`):
    syntax is the client's fault, staleness is the file's.
    """
    if raw == "0":
        return START
    m = _CURSOR_RE.match(raw or "")
    if not m:
        return None
    return (m.group(1), int(m.group(2)), int(m.group(3)))


def format_cursor(gen: str, offset: int, ordinal: int) -> str:
    if not gen:
        return "0"
    return f"{gen}.{offset}.{ordinal}"


def _generation(head: bytes, fh=None) -> str:
    """A digest of the first LINE, or "" while no complete line exists yet.

    Deliberately not "the first N bytes": a journal shorter than N bytes
    would digest a different slice once it grew past N, and every tick after
    that would see a generation change and reset — a cache that invalidates
    itself exactly while the run is starting.

    When the first line is longer than the head read, keep reading for it
    (bounded by `_GEN_SCAN_MAX`) rather than giving up. Giving up used to
    mean returning "" forever, which silently froze the whole events feed:
    a refusal record listing every dirty path under a broad write scope
    lands as line 1 and can easily clear 4 KB, and `cli` re-drives append to
    the SAME journal, so that line stays line 1 for the lineage's life.
    """
    nl = head.find(b"\n")
    if nl >= 0:
        return hashlib.sha256(head[:nl]).hexdigest()[:8]
    if fh is None:
        return ""
    buf = bytearray(head)
    while len(buf) < _GEN_SCAN_MAX:
        chunk = fh.read(_HEAD_BYTES * 16)
        if not chunk:
            return ""          # no complete first line yet: self-heals
        buf += chunk
        nl = buf.find(b"\n")
        if nl >= 0:
            return hashlib.sha256(bytes(buf[:nl])).hexdigest()[:8]
    # Pathologically long first line (past _GEN_SCAN_MAX). Digest the SCANNED
    # PREFIX rather than giving up: the prefix is a fixed length, so it does
    # not move as the file grows, and the cursor stays usable. Returning ""
    # here meant `read()` handed back zero events forever - the client then
    # sees an unchanged cursor every tick and never refreshes, which is a
    # silent permanent freeze of the whole feed, not the "serves from the
    # start each tick" this comment used to claim.
    return hashlib.sha256(bytes(buf[:_GEN_SCAN_MAX])).hexdigest()[:8]


def read(run_dir: Path, cursor: tuple[str, int, int]) -> tuple[list[dict], str]:
    """(events appended since `cursor`, the cursor to send next).

    One open, one head read, one tail read. A quiet tick costs `_HEAD_BYTES`
    and nothing else, whatever the journal weighs.
    """
    gen_want, offset, ordinal = cursor
    path = Path(run_dir) / "events.jsonl"
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
            gen = _generation(head, fh)
            if not gen:
                # No complete first line yet: there is nothing a cursor could
                # point into. Read what is there (it is tiny by definition)
                # and stay at the start until the first line lands.
                return [], "0"
            size = fh.seek(0, os.SEEK_END)
            if gen != gen_want or offset > size:
                # Rotated, truncated, or a cursor from another generation.
                # Reset EXPLICITLY and re-serve from the top rather than
                # reading from an offset that means nothing in this file.
                offset, ordinal = 0, 0
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], "0"

    # Byte-level split: the trailing element is whatever follows the last
    # newline — empty when the file ends cleanly, a torn line when the driver
    # is mid-append. Either way it is NOT consumed, and `consumed` counts only
    # bytes belonging to complete lines.
    parts = chunk.split(b"\n")
    tail = parts.pop()
    consumed = len(chunk) - len(tail)

    out: list[dict] = []
    for raw_line in parts:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            # A mid-file tear. Say nothing and do not advance: the next tick
            # re-reads the same bytes, and if the file was being written it
            # will parse then.
            return [], format_cursor(gen, offset, ordinal)
    return out, format_cursor(gen, offset + consumed, ordinal + len(out))


def initial(run_dir: Path) -> str:
    """The cursor a freshly rendered page starts at: everything currently in
    the journal is already reflected in the body it was rendered with, so the
    client must not be handed those events again.

    Positioned WITHOUT parsing. The first cut called `read(run_dir, START)`,
    which read and `json.loads`-ed the entire journal and then threw the
    events away to keep the offset — so S1.1 removed a whole-journal read
    from the quiet tick and added one to `render_wrap`, which is every page
    load and every refresh. Same cost class, moved rather than removed. All
    that is needed is the offset of the byte after the last complete line.

    `ordinal` starts at 0 here: it is display-only and counting it would
    mean reading the file this function exists not to read.
    """
    path = Path(run_dir) / "events.jsonl"
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
            gen = _generation(head, fh)
            if not gen:
                return "0"
            size = fh.seek(0, os.SEEK_END)
            window = min(size, _TAIL_BYTES)
            fh.seek(size - window)
            tail = fh.read(window)
            nl = tail.rfind(b"\n")
            if nl >= 0:
                return format_cursor(gen, size - window + nl + 1, 0)
            if window >= size:
                return format_cursor(gen, 0, 0)   # no complete line at all
            # A final line longer than the window: fall back to the honest
            # scan rather than guessing a boundary.
            fh.seek(0)
            data = fh.read()
            last = data.rfind(b"\n")
            return format_cursor(gen, last + 1 if last >= 0 else 0, 0)
    except OSError:
        return "0"
