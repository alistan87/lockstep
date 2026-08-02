#!/usr/bin/env python
"""cost_report.py — per-node / per-run / per-deliverable cost tables over
lockstep run dirs (PROPOSAL-domain-cockpit, feature B v0). No driver changes:
everything is read from artifacts the run already left behind.

    python contrib/cost_report.py <run_dir> [<run_dir> ...] [--fields cost-fields.toml]

Multiple run dirs roll up into a combined total — with terminal-approval
segmentation, one deliverable spans a chain of runs.

Sources (and their honest limits):
- tokens/cost: harness JSON envelopes preserved verbatim in stdout.log and
  rotated stdout-attempt*.log (retries and correctives cost money too), map
  items included. Field paths per harness BINARY come from an operator-owned
  cost-fields.toml (see cost-fields.toml.example — probe YOUR harness
  versions); executors with no envelope or no field map are reported as
  such, never as a fake 0.
- attempts / heal rounds: state.json + events.jsonl.
- wall time: events.jsonl running->terminal transition pairs (state.json
  spans mislead on re-runs; map items emit no transition events, so item
  wall time is not reported).

Units policy: spawns/tokens/wall-time are the primary columns; dollars are
printed only where the envelope reports them and both machines bill in
quota/limits, so dollars are labeled NOTIONAL.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path

KNOWN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost")
TERMINAL = {"done", "failed", "blocked"}


# --- field maps ----------------------------------------------------------------

def load_field_maps(explicit: str | None) -> dict[str, dict[str, str]]:
    """binary-basename -> {field -> dotted envelope path}. Search order:
    --fields, ./cost-fields.toml, <script dir>/cost-fields.toml."""
    candidates = [Path(explicit)] if explicit else [
        Path.cwd() / "cost-fields.toml",
        Path(__file__).resolve().parent / "cost-fields.toml",
    ]
    for path in candidates:
        if path.is_file():
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            maps: dict[str, dict[str, str]] = {}
            for binary, fields in raw.items():
                if isinstance(fields, dict):
                    maps[binary.lower()] = {
                        k: v for k, v in fields.items() if k in KNOWN_FIELDS and isinstance(v, str)
                    }
            return maps
    return {}


def dig(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj if isinstance(obj, (int, float)) and not isinstance(obj, bool) else None


# --- envelope extraction -------------------------------------------------------

try:  # prefer the driver's battle-tested extractor when lockstep is installed
    from lockstep.executors.harness import extract_last_json as _extract
except Exception:  # pragma: no cover - standalone fallback
    _extract = None


def last_envelope(text: str) -> dict | None:
    if _extract is not None:
        raw = _extract(text)
        if raw:
            try:
                obj = json.loads(raw)
                return obj if isinstance(obj, dict) else None
            except ValueError:
                return None
        return None
    # Fallback: scanning '{' offsets from the END, the first decode that
    # consumes the remaining tail is the OUTERMOST final JSON object.
    dec = json.JSONDecoder()
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "{":
            continue
        try:
            obj, end = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and not text[i + end:].strip():
            return obj
    return None


def binary_of(phase_dir: Path) -> str | None:
    """argv[0] basename (extension stripped) from argv.json / rotated copies."""
    for name in ("argv.json", *sorted(p.name for p in phase_dir.glob("argv-attempt*.json"))):
        p = phase_dir / name
        if p.is_file():
            try:
                argv = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(argv, list) and argv:
                stem = Path(str(argv[0])).name.lower()
                for ext in (".exe", ".cmd", ".bat"):
                    stem = stem.removesuffix(ext)
                return stem
    return None


# --- per-run collection --------------------------------------------------------

def _read_events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:
                continue  # trailing partial line after a crash: tolerated (SPEC §10.3)
            raise
    return out


def _ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def wall_and_heals(events: list[dict]) -> tuple[dict[str, float], dict[str, int]]:
    wall: dict[str, float] = {}
    heals: dict[str, int] = {}
    open_at: dict[str, datetime] = {}
    for e in events:
        node, status = e.get("node"), e.get("status")
        if not node:
            continue
        if status == "heal-round":
            heals[node] = heals.get(node, 0) + 1
            continue
        t = _ts(e.get("ts", ""))
        if t is None:
            continue
        if status == "running":
            open_at[node] = t
        elif status in TERMINAL and node in open_at:
            wall[node] = wall.get(node, 0.0) + (t - open_at.pop(node)).total_seconds()
    return wall, heals


def node_tokens(phase_dir: Path, maps: dict[str, dict[str, str]]) -> dict:
    """Sum envelope fields over every attempt's stdout (map items included)."""
    logs = sorted(phase_dir.glob("stdout*.log")) + sorted(phase_dir.glob("items/*/stdout*.log"))
    binary = binary_of(phase_dir)
    if binary is None:
        for item in sorted(phase_dir.glob("items/*")):
            binary = binary_of(item)
            if binary:
                break
    fmap = maps.get(binary or "")
    sums: dict[str, float] = {}
    envelopes = 0
    for log in logs:
        env = last_envelope(log.read_text(encoding="utf-8", errors="replace"))
        if env is None:
            continue
        envelopes += 1
        if fmap:
            for field, path in fmap.items():
                v = dig(env, path)
                if v is not None:
                    sums[field] = sums.get(field, 0.0) + v
    note = ""
    if logs and envelopes == 0:
        note = "no envelope"
    elif envelopes and not fmap:
        note = f"no field map ({binary or 'unknown'})"
    return {"sums": sums, "note": note}


def collect_run(run_dir: Path, maps: dict[str, dict[str, str]]) -> dict:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    wall, heals = wall_and_heals(_read_events(run_dir))
    rows = []
    for node_id, rec in state.get("nodes", {}).items():
        phase_dir = run_dir / "phases" / node_id
        attempts = rec.get("attempts", 0) + sum(
            i.get("attempts", 0) for i in rec.get("items", {}).values()
        )
        tokens = (
            node_tokens(phase_dir, maps)
            if rec.get("kind") != "shell" and phase_dir.is_dir()
            else {"sums": {}, "note": ""}
        )
        rows.append({
            "node": node_id,
            "kind": rec.get("kind", "?"),
            "status": rec.get("status", "?"),
            "attempts": attempts,
            "heal_rounds": heals.get(node_id, 0),
            "wall_s": wall.get(node_id),
            **{f: tokens["sums"].get(f) for f in KNOWN_FIELDS},
            "note": tokens["note"],
        })
    return {
        "run_dir": str(run_dir),
        "flow": state.get("flow_name", "?"),
        "token_spawns": state.get("token_spawns", 0),
        "rows": rows,
    }


# --- rendering -----------------------------------------------------------------

def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "-"
    if money:
        return f"${v:.4f}"
    if isinstance(v, float):
        return f"{v:.0f}"
    return str(v)


def render(runs: list[dict]) -> str:
    out: list[str] = ["# lockstep cost report", ""]
    grand: dict[str, float] = {}
    grand_spawns = 0
    for run in runs:
        out.append(f"## {run['flow']}  `{run['run_dir']}`")
        out.append("")
        out.append("| node | kind | attempts | heal | wall s | in tok | out tok | cache r | cache w | cost* | note |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        totals: dict[str, float] = {}
        for r in run["rows"]:
            for f in KNOWN_FIELDS:
                if r[f] is not None:
                    totals[f] = totals.get(f, 0.0) + r[f]
            out.append(
                f"| {r['node']} | {r['kind']} | {r['attempts']} | {r['heal_rounds']} "
                f"| {_fmt(r['wall_s'])} | {_fmt(r['input_tokens'])} | {_fmt(r['output_tokens'])} "
                f"| {_fmt(r['cache_read_tokens'])} | {_fmt(r['cache_write_tokens'])} "
                f"| {_fmt(r['cost'], money=True)} | {r['note']} |"
            )
        out.append(
            f"| **total** |  |  |  |  | {_fmt(totals.get('input_tokens'))} "
            f"| {_fmt(totals.get('output_tokens'))} | {_fmt(totals.get('cache_read_tokens'))} "
            f"| {_fmt(totals.get('cache_write_tokens'))} | {_fmt(totals.get('cost'), money=True)} "
            f"| token spawns: {run['token_spawns']} |"
        )
        out.append("")
        for f, v in totals.items():
            grand[f] = grand.get(f, 0.0) + v
        grand_spawns += run["token_spawns"]
    if len(runs) > 1:
        out.append("## deliverable total (all runs)")
        out.append("")
        out.append(
            f"- token spawns: {grand_spawns} | in: {_fmt(grand.get('input_tokens'))} "
            f"| out: {_fmt(grand.get('output_tokens'))} | cost*: {_fmt(grand.get('cost'), money=True)}"
        )
        out.append("")
    out.append("\\* dollars are NOTIONAL (envelope-reported; both machines bill in quota/limits).")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="+", metavar="run_dir")
    ap.add_argument("--fields", default=None, help="cost-fields.toml path")
    ns = ap.parse_args(argv)
    maps = load_field_maps(ns.fields)
    if not maps:
        print("note: no cost-fields.toml found - tokens/cost will read 'no field map'", file=sys.stderr)
    runs = []
    for rd in ns.run_dirs:
        run_dir = Path(rd)
        if not (run_dir / "state.json").is_file():
            print(f"error: {rd} has no state.json (not a run dir?)", file=sys.stderr)
            return 2
        runs.append(collect_run(run_dir, maps))
    print(render(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
