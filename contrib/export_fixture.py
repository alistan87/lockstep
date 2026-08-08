#!/usr/bin/env python
"""export_fixture.py — a scrubbed, replayable copy of a run dir (A3).

    python contrib/export_fixture.py <run_dir> <dest>

Copies ONLY what `lockstep run --replay` actually reads:

    state.json, flow.tg.json,
    phases/<node>/result.json | result.txt,
    phases/<node>/items/<i>/result.json | result.txt

Everything else — prompts, spill inputs, stdout/stderr logs, the journal,
mailbox, rotated attempts, approval evidence, snapshots — is sensitive and
stays behind. The allowlist is written out, not inferred: a scrubber that
guesses what is sensitive eventually guesses wrong in the expensive direction.

Two FIELDS of the copied `state.json` are cleared as well, because the
allowlist governs files and these travel inside one: `result_path` (an
absolute path from the recording machine, which replay ignores) and
`fingerprint_detail` (a map of that machine's dirty working tree).

What it keeps is still model OUTPUT. The tool lists every file it kept and
ends by saying so — review before committing a fixture remains a human act.
Files containing NUL bytes are refused outright (results are text channels;
a binary result in a fixture is a bug somewhere else).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

KEEP_TOP = ("state.json", "flow.tg.json")
RESULTS = ("result.json", "result.txt")


def _copy(src: Path, dest_root: Path, rel: Path, kept: list[str]) -> None:
    if b"\x00" in src.read_bytes():
        raise SystemExit(f"refusing {src}: contains NUL bytes (results are text channels)")
    target = dest_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    kept.append(str(rel).replace("\\", "/"))


def export(run_dir: Path, dest: Path) -> list[str]:
    run_dir, dest = Path(run_dir), Path(dest)
    if not (run_dir / "state.json").exists():
        raise SystemExit(f"{run_dir} has no state.json — not a run dir")
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} exists and is not empty — refusing to mix fixtures")
    kept: list[str] = []
    for name in KEEP_TOP:
        src = run_dir / name
        if src.exists():
            _copy(src, dest, Path(name), kept)
    phases = run_dir / "phases"
    if phases.is_dir():
        for node_dir in sorted(p for p in phases.iterdir() if p.is_dir()):
            for res in RESULTS:
                src = node_dir / res
                if src.exists():
                    _copy(src, dest, src.relative_to(run_dir), kept)
            items = node_dir / "items"
            if items.is_dir():
                for item_dir in sorted(p for p in items.iterdir() if p.is_dir()):
                    for res in RESULTS:
                        src = item_dir / res
                        if src.exists():
                            _copy(src, dest, src.relative_to(run_dir), kept)
    _scrub_state(dest / "state.json")
    return kept


def _scrub_state(path: Path) -> None:
    """Drop the two machine-local fields `state.json` carries.

    `result_path` is an ABSOLUTE path from the recording machine — replay
    deliberately ignores it and reads the phase dir instead (`replay.py`
    `_read_result`), so it is dead weight that names somebody's home directory
    in a committed file. `fingerprint_detail` is a path → content-hash map of
    the recording machine's dirty working tree; nothing in replay reads it.

    The allowlist above decides which FILES leave; this decides which fields
    of the one structured file do.
    """
    if not path.exists():
        return
    state = json.loads(path.read_text(encoding="utf-8"))
    state["fingerprint_detail"] = {}
    for rec in (state.get("nodes") or {}).values():
        rec["result_path"] = None
        for irec in (rec.get("items") or {}).values():
            irec["result_path"] = None
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("dest")
    ns = ap.parse_args(argv)
    kept = export(Path(ns.run_dir), Path(ns.dest))
    for rel in kept:
        print(f"kept: {rel}")
    print(f"{len(kept)} file(s) exported to {ns.dest}")
    print("NOTE: results are model output. Read every kept file before committing this fixture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
