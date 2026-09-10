"""Deletion-only JSON repair (throughput-parity C2/C3, DEVIATIONS 2026-09-09).

The corrective re-spawn (AMENDMENTS-r5 A2) is the engine's most expensive
recovery: original prompt plus fenced invalid output, roughly 2.5× the
node's tokens — and on a request-metered harness, a second billed request.
This module is the cheaper step the engine tries first, under one hard
rule: a repair may only DELETE bytes, never synthesize them. A closing
bracket the model never wrote is a finding the review never made —
shape-only validation accepts any-length lists, so a synthesized `]` would
pass a stream truncated at `[{f1},{f2},` as a valid two-finding review and
truncation just after `[` as a CLEAN one (rev-2 blocker F-E2). Truncated
output falls through to the corrective re-spawn, where the model completes
its own prefix; `longest_near_object` (C3) is what that corrective fences
so the model corrects its own output instead of re-deriving it.

Scope is the engine's to enforce, not this module's: harness-kind nodes on
the contract-validation-failure path only — never gates (a verdict's
consumers act without a human re-reading raw bytes), never shell
(deterministic output, terminal on mismatch — AMENDMENTS A4).
"""

from __future__ import annotations

import json
import re

_decoder = json.JSONDecoder()
# Same line-anchored pattern as extract_last_json: a JSON string cannot hold
# a literal newline, so a whole line of backticks is never valid content.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$", re.MULTILINE)


def _decode_deleting_commas(s: str, start: int) -> tuple[str, int | None, int]:
    """Strict raw_decode at `start`, deleting a dangling comma whenever the
    decoder's failure position points at an EXISTING closer whose previous
    non-space character is a comma. Error-driven, so a comma inside a string
    literal is unreachable — the decoder never fails there. Returns
    (possibly-shortened s, end-or-None, commas_deleted)."""
    deleted = 0
    while True:
        try:
            _, end = _decoder.raw_decode(s, start)
            return s, end, deleted
        except json.JSONDecodeError as e:
            p = e.pos
            if p is not None and start <= p < len(s) and s[p] in "]}":
                q = p - 1
                while q > start and s[q].isspace():
                    q -= 1
                if q >= start and s[q] == ",":
                    s = s[:q] + s[q + 1:]  # delete exactly the comma
                    deleted += 1
                    continue
            return s, None, deleted


def _decode_travel(s: str, start: int) -> int:
    """How far a strict decode from `start` reaches before failing — the
    extent of a broken container, used to detect fragments inside it."""
    try:
        _, end = _decoder.raw_decode(s, start)
        return end
    except json.JSONDecodeError as e:
        return max(e.pos if e.pos is not None else start + 1, start + 1)


def repair_json(text: str, *, single_value: bool = False) -> tuple[str, list[str]] | None:
    """Deletion-only repair of a result that failed to decode: strip markdown
    fence lines, take the LONGEST raw_decode-complete value (extraction takes
    the last; the intended output beats trailing chatter), drop everything
    around it, and remove dangling commas before existing closers. Returns
    (repaired_text, human-readable deletions) or None when no deletion
    produces a decodable value — including when the text already decodes
    (wrong shape is not repair's problem) and when the only fix would be to
    synthesize a closing token.

    `single_value=True` is the FILE-channel posture (adversarial round 2,
    finding 1): the §7 footer says the result file contains ONLY the JSON,
    so repair there may fix byte damage to that one value — and must refuse
    any file holding more than one value-shaped span. Without this, a
    narrated schema example (which validates by construction, being the
    contract's own shape) or a superseded draft sitting BEFORE a truncated
    real answer would be adopted as the result; the corrective, whose C3
    fence carries the truncated REAL answer, owns those files."""
    deletions: list[str] = []
    work = text
    if _FENCE_RE.search(work):
        work = _FENCE_RE.sub("", work)
        deletions.append("stripped markdown fence line(s)")
    candidates: list[tuple[int, int, int, str, int]] = []  # (len, start, end, s, commas)
    failed: list[tuple[int, int]] = []  # (start, how far the decode travelled)
    i = 0
    while i < len(work):
        if work[i] in "{[":
            s2, end, commas = _decode_deleting_commas(work, i)
            if end is not None:
                candidates.append((end - i, i, end, s2, commas))
                i = end + commas  # skip the span in ORIGINAL coordinates
                continue
            # A broken container: record how far the strict decode reached, so
            # a complete value INSIDE it is recognized as a fragment below.
            travelled = _decode_travel(work, i)
            failed.append((i, travelled))
        i += 1
    if single_value and (failed or len(candidates) != 1):
        return None
    # The F-E2 rule with teeth: a complete value enclosed by a broken outer
    # container is not "a value surrounded by garbage" — it is a fragment of
    # a truncated result, and accepting it silently drops the rest. Refuse;
    # the corrective (with the C3 near-object fence) owns that case.
    best: tuple[int, int, int, str, int] | None = None
    for cand in sorted(candidates, reverse=True):
        if any(fs < cand[1] and fe > cand[1] for fs, fe in failed):
            continue
        best = cand
        break
    if best is None:
        return None
    _, start, end, s2, commas = best
    candidate = s2[start:end]
    if commas:
        deletions.append(f"removed {commas} dangling comma(s) before an existing closer")
    if s2[:start].strip():
        deletions.append(f"dropped {len(s2[:start].strip())} char(s) before the value")
    if s2[end:].strip():
        deletions.append(f"dropped {len(s2[end:].strip())} char(s) after the value")
    if not deletions:
        # Nothing was deleted: the text (modulo whitespace) already decodes,
        # so validation failed on shape and would fail again identically.
        return None
    return candidate, deletions


def longest_near_object(text: str) -> str | None:
    """C3 (ROADMAP-NOTES 2026-08-15, chronicle forensics): the longest
    `{`/`[`-rooted span a strict decode travels into before FAILING — the
    model's truncated or one-token-corrupted outer output. Complete decodes
    are deliberately not candidates: a complete value is what extraction
    already reached (including a harness envelope, which must not displace
    its own unwrapped result). Returns the near-object substring or None."""
    text = _FENCE_RE.sub("", text)
    best: tuple[int, int, int] | None = None  # (len, start, end)
    i = 0
    while i < len(text):
        if text[i] in "{[":
            try:
                _, end = _decoder.raw_decode(text, i)
                i = end  # complete: not a candidate; skip past it
                continue
            except json.JSONDecodeError as e:
                end = max(e.pos if e.pos is not None else i + 1, i + 1)
                if best is None or end - i > best[0]:
                    best = (end - i, i, end)
        i += 1
    if best is None:
        return None
    return text[best[1]:best[2]]
