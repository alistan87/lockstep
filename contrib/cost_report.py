#!/usr/bin/env python
"""cost_report.py — per-node / per-run / per-deliverable cost tables over
lockstep run dirs (PROPOSAL-domain-cockpit, feature B v0). No driver changes:
everything is read from artifacts the run already left behind.

    python contrib/cost_report.py <run_dir> [<run_dir> ...] [--fields cost-fields.toml]
    python contrib/cost_report.py --runs-from <slug|file> [--compact]
    python contrib/cost_report.py --watch <run_dir>          # live pane block

Multiple run dirs roll up into a combined total — with terminal-approval
segmentation, one deliverable spans a chain of runs. Deliverable identity is
the lineage index (rev 7 R-B2): runs/lineages/<slug>.runs, one run-dir path
per line, with the cockpit journal's `consent.deliverable` as a back-reference
so a lost index can be rebuilt by scanning journals.

--compact/--watch (rev 7 §B v0.5) render a few-line block against a run dir
that is STILL EXECUTING: running nodes, partially written stdout, and a
state.json caught mid-replace all render as `in progress` or the last good
value — never a crash, never a fake 0, never a total that goes backwards.

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
import hashlib
import json
import os
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

KNOWN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost")
KNOWN_KEYS = KNOWN_FIELDS + ("format",)
TERMINAL = {"done", "failed", "blocked"}
RUNNING = "running"


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
                        k: v for k, v in fields.items() if k in KNOWN_KEYS and isinstance(v, str)
                    }
            return maps
    return {}


def dig(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj if isinstance(obj, (int, float)) and not isinstance(obj, bool) else None


# --- lineage index (R-B2) ------------------------------------------------------

LINEAGE_DIR = "lineages"


def lineage_path(runs_root: Path, slug: str) -> Path:
    return Path(runs_root) / LINEAGE_DIR / f"{slug}.runs"


def append_lineage(runs_root: Path, slug: str, run_dir: Path) -> None:
    """Append a run dir to a deliverable's index. Open-append-write-close, same
    discipline as the cockpit journal — no held handles on anything under runs/.
    Idempotent: a run dir already listed is not appended twice."""
    path = lineage_path(runs_root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = str(Path(run_dir))
    if path.is_file():
        existing = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
        if entry in existing:
            return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")


def _key(p: str) -> str:
    """Identity of a run dir for dedupe. RESOLVED, because the same run reaches
    us by different spellings: the index is written relative (`runs\\x`) while a
    journal rebuild globs an absolute root (`D:\\repo\\runs\\x`). Comparing the
    raw strings counts one run twice and silently doubles a deliverable's
    reported cost — the exact number the consent beat is judged against."""
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(Path(p)).lower()


def _dedupe(paths: list[str]) -> list[str]:
    seen, out = set(), []
    for p in paths:
        key = _key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def rebuild_lineage(runs_root: Path, slug: str) -> list[str]:
    """Reconstruct a lineage from cockpit journals when the index is missing or
    incomplete: every run whose `consent` entry names this deliverable. Ordered
    by the consent timestamp when present, else by run-dir name (which is
    timestamped by the driver), so segments come back in the order they ran."""
    found: list[tuple[str, str]] = []
    root = Path(runs_root)
    if not root.is_dir():
        return []
    for journal in sorted(root.glob("*/cockpit-journal.jsonl")):
        stamp = None
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # a partial trailing line is normal on an append-only file
            if obj.get("kind") == "consent" and obj.get("deliverable") == slug:
                stamp = obj.get("ts") or journal.parent.name
                break
        if stamp is not None:
            found.append((stamp, str(journal.parent)))
    return [p for _, p in sorted(found)]


def resolve_runs_from(spec: str, runs_root: Path | None = None) -> tuple[list[str], list[str]]:
    """`--runs-from` accepts a file path OR a bare slug resolved under
    <runs_root>/lineages/. Returns (run_dirs, notes). A missing index is not an
    error while journals survive — it falls back to a journal rebuild, and says
    so, because silently reporting a partial deliverable total is the one
    outcome worth avoiding here."""
    notes: list[str] = []
    root = Path(runs_root) if runs_root else Path.cwd() / "runs"
    candidate = Path(spec)
    if candidate.is_file():
        listed = [ln.strip() for ln in candidate.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return _dedupe(listed), notes
    slug = spec
    idx = lineage_path(root, slug)
    if idx.is_file():
        listed = [ln.strip() for ln in idx.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rebuilt = rebuild_lineage(root, slug)
        known = {_key(x) for x in listed}
        extra = [r for r in rebuilt if _key(r) not in known]
        if extra:
            notes.append(
                f"index {idx} is missing {len(extra)} run(s) present in journals; included"
            )
        return _dedupe(listed + extra), notes
    rebuilt = rebuild_lineage(root, slug)
    if rebuilt:
        notes.append(f"no index at {idx}; rebuilt {len(rebuilt)} run(s) from cockpit journals")
        return _dedupe(rebuilt), notes
    notes.append(f"no index at {idx} and no journal names deliverable {slug!r}")
    return [], notes


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


def pi_stream_usage(text: str) -> tuple[dict[str, float], int]:
    """Sum per-message usage from a pi `--mode json` event stream (probed
    against pi 0.83.0): each assistant `message_end` carries
    usage{input, output, cacheRead, cacheWrite, cost{total}} — provider-
    computed, so on Copilot these are the credit-accurate dollars. Only
    `message_end` is summed: `turn_end`/`agent_end` repeat the same messages
    and would double-count."""
    sums: dict[str, float] = {}
    seen = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") != "message_end":
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        usage = msg.get("usage") or {}
        seen += 1
        for field, path in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_read_tokens", "cacheRead"),
            ("cache_write_tokens", "cacheWrite"),
            ("cost", "cost.total"),
        ):
            v = dig(usage, path)
            if v is not None:
                sums[field] = sums.get(field, 0.0) + v
    return sums, seen


def node_tokens(phase_dir: Path, maps: dict[str, dict[str, str]]) -> dict:
    """Sum usage over every attempt's stdout (map items included). Two
    formats: a single JSON envelope with dotted field paths (claude-code
    style), or `format = "pi-stream"` (pi's per-message event stream)."""
    logs = sorted(phase_dir.glob("stdout*.log")) + sorted(phase_dir.glob("items/*/stdout*.log"))
    binary = binary_of(phase_dir)
    if binary is None:
        for item in sorted(phase_dir.glob("items/*")):
            binary = binary_of(item)
            if binary:
                break
    fmap = maps.get(binary or "")
    stream_mode = bool(fmap) and fmap.get("format") == "pi-stream"
    sums: dict[str, float] = {}
    envelopes = 0
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        if stream_mode:
            got, seen = pi_stream_usage(text)
            envelopes += 1 if seen else 0
            for k, v in got.items():
                sums[k] = sums.get(k, 0.0) + v
            continue
        env = last_envelope(text)
        if env is None:
            continue
        envelopes += 1
        if fmap:
            for field, path in fmap.items():
                if field == "format":
                    continue
                v = dig(env, path)
                if v is not None:
                    sums[field] = sums.get(field, 0.0) + v
    note = ""
    if logs and envelopes == 0:
        note = "no envelope"
    elif envelopes and not fmap:
        note = f"no field map ({binary or 'unknown'})"
    return {"sums": sums, "note": note}


def read_state(run_dir: Path, retries: int = 3) -> dict | None:
    """state.json is written by atomic replace WITH RETRIES because this
    machine's AV trips file replaces (ops notes). A reader polling every second
    will therefore catch it absent or half-visible sooner or later; that is a
    normal moment in a healthy run, not an error. Returns None so the caller can
    hold the previous frame rather than render a zero."""
    path = Path(run_dir) / "state.json"
    for attempt in range(retries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if attempt < retries - 1:
                time.sleep(0.15)
    return None


def collect_run(run_dir: Path, maps: dict[str, dict[str, str]]) -> dict:
    state = read_state(run_dir)
    if state is None:
        raise FileNotFoundError(f"{run_dir}: state.json unreadable")
    wall, heals = wall_and_heals(_read_events(run_dir))
    rows = []
    for node_id, rec in state.get("nodes", {}).items():
        phase_dir = run_dir / "phases" / node_id
        status = rec.get("status", "?")
        attempts = rec.get("attempts", 0) + sum(
            i.get("attempts", 0) for i in rec.get("items", {}).values()
        )
        tokens = (
            node_tokens(phase_dir, maps)
            if rec.get("kind") != "shell" and phase_dir.is_dir()
            else {"sums": {}, "note": ""}
        )
        note = tokens["note"]
        if status == RUNNING:
            # A running node's envelope has not been written yet; "no envelope"
            # would read as a defect when it is simply not finished.
            note = "in progress"
        rows.append({
            "node": node_id,
            "kind": rec.get("kind", "?"),
            "status": status,
            "attempts": attempts,
            "heal_rounds": heals.get(node_id, 0),
            "wall_s": wall.get(node_id, _running_wall(rec) if status == RUNNING else None),
            **{f: tokens["sums"].get(f) for f in KNOWN_FIELDS},
            "note": note,
        })
    return {
        "run_dir": str(run_dir),
        "flow": state.get("flow_name", "?"),
        "token_spawns": state.get("token_spawns", 0),
        "rows": rows,
    }


def _running_wall(rec: dict) -> float | None:
    """Elapsed-so-far for a node with no terminal transition yet. events.jsonl
    carries no end timestamp for it — by definition — so this is the one place
    wall time comes from the record and `now`."""
    t = _ts(rec.get("started_at", "") or "")
    if t is None:
        return None
    now = datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds())


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


# --- compact / live rendering (§B v0.5) ----------------------------------------

def _budget_cap(run_dir: Path) -> int | None:
    """`of N` for the spend line comes from the run's own flow copy — the
    denominator the DE was quoted at the consent beat, not a live config."""
    for name in ("flow.tg.json", "flow.json"):
        p = Path(run_dir) / name
        try:
            flow = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cap = (flow.get("budget") or {}).get("max_agent_spawns")
        return cap if isinstance(cap, int) else None
    return None


def _human_secs(s: float | None) -> str:
    if s is None:
        return "-"
    s = int(s)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def compact_block(runs: list[dict], caps: list[int | None], floor: dict | None = None) -> tuple[str, dict]:
    """A few lines for a pane. Returns (text, totals) — totals feed the
    monotonic guard: a poll that catches state.json mid-replace or a log
    mid-write must never render a smaller number than the poll before it."""
    spawns = sum(r["token_spawns"] for r in runs)
    # The denominator must cover the same runs as the numerator. Each segment is
    # a separate run carrying its own budget stanza, so a deliverable's ceiling
    # is the SUM of its segments' caps — taking the first one produced lines
    # like "agent tasks used 38 of 25", which is the number a domain expert was
    # quoted at the consent beat and the one the boot protocol restates. A
    # spend figure that reads as already-over-budget destroys the only
    # quantitative trust anchor the DE has.
    known = [c for c in caps if c]
    cap = sum(known) if known else None
    cap_partial = bool(known) and len(known) < len(runs)
    tok_in = sum((r[f] or 0) for run in runs for r in run["rows"] for f in ("input_tokens",))
    tok_out = sum((r[f] or 0) for run in runs for r in run["rows"] for f in ("output_tokens",))
    cost = sum((r["cost"] or 0) for run in runs for r in run["rows"])
    heals = sum(r["heal_rounds"] for run in runs for r in run["rows"])
    totals = {"spawns": spawns, "in": tok_in, "out": tok_out, "cost": cost, "heals": heals}
    if floor:
        for k in totals:
            totals[k] = max(totals[k], floor.get(k, 0))
    elapsed = max(
        (r["wall_s"] or 0) for run in runs for r in run["rows"]
    ) if any(run["rows"] for run in runs) else 0
    wall_total = sum((r["wall_s"] or 0) for run in runs for r in run["rows"])
    running = [r["node"] for run in runs for r in run["rows"] if r["status"] == RUNNING]
    # Two very different facts, kept apart because a DE reads them differently:
    # "no envelope" is a property of the HARNESS (copilot-cli has no JSON mode
    # and never will report usage — not a fault, nothing to fix), while "no
    # field map" is a property of the OPERATOR'S CONFIG (someone must add the
    # binary to cost-fields.toml). Collapsing them teaches the DE to ignore a
    # line that sometimes means "your setup is incomplete".
    no_env = sorted({
        "no envelope" for run in runs for r in run["rows"] if r["note"] == "no envelope"
    })
    no_map = sorted({
        r["note"].split("(")[-1].rstrip(")")
        for run in runs for r in run["rows"] if r["note"].startswith("no field map")
    })
    spend = f"agent tasks used {totals['spawns']}"
    if cap:
        # "of at least N" when some segment declares no budget: better an
        # admittedly incomplete ceiling than a precise-looking wrong one.
        spend += f" of at least {cap}" if cap_partial else f" of {cap}"
    lines = [spend]
    second = []
    if totals["heals"]:
        second.append(f"{totals['heals']} rework round{'s' if totals['heals'] != 1 else ''}")
    second.append(f"{_human_secs(wall_total)} of node time")
    if totals["in"] or totals["out"]:
        second.append(f"tokens: {totals['in']:,.0f} in / {totals['out']:,.0f} out")
    if totals["cost"]:
        second.append(f"${totals['cost']:.2f} notional")
    if no_env:
        second.append("some steps report no usage (harness has no JSON mode)")
    if no_map:
        second.append(f"unmapped harness: {', '.join(no_map)} — add it to cost-fields.toml")
    # ASCII separators only: this line is read in cmd.exe consoles too, where a
    # middle dot arrives as a question mark and reads like a rendering fault.
    lines.append(" | ".join(second))
    lines.append(
        f"running: {', '.join(running)}" if running else "running: nothing - waiting or done"
    )
    del elapsed
    return "\n".join(lines), totals


def _watch(run_dirs: list[str], maps, interval: float) -> int:
    """Standalone live loop. cockpit.ps1 does not use this — it calls --compact
    per poll so the pane owns its own cadence — but a bare terminal wants it."""
    floor: dict = {}
    last = ""
    while True:
        runs, caps = [], []
        for rd in run_dirs:
            try:
                runs.append(collect_run(Path(rd), maps))
                caps.append(_budget_cap(Path(rd)))
            except (OSError, ValueError, FileNotFoundError):
                continue  # display-only: a transient read never kills the view
        if runs:
            text, floor = compact_block(runs, caps, floor)
            if text != last:
                os.system("cls" if os.name == "nt" else "clear")
                print(text, flush=True)
                last = text
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dirs", nargs="*", metavar="run_dir")
    ap.add_argument("--fields", default=None, help="cost-fields.toml path")
    ap.add_argument("--runs-from", default=None, metavar="SLUG|FILE",
                    help="deliverable lineage: runs/lineages/<slug>.runs, or a file of run dirs")
    ap.add_argument("--runs-root", default=None, help="runs root for slug resolution (default ./runs)")
    ap.add_argument("--compact", action="store_true", help="pane-sized block instead of the tables")
    ap.add_argument("--watch", action="store_true", help="compact block on a loop until Ctrl-C")
    ap.add_argument("--interval", type=float, default=2.0, help="--watch poll seconds (default 2)")
    ns = ap.parse_args(argv)
    maps = load_field_maps(ns.fields)
    if not maps and not ns.compact:
        print("note: no cost-fields.toml found - tokens/cost will read 'no field map'", file=sys.stderr)

    run_dirs = list(ns.run_dirs)
    if ns.runs_from:
        resolved, notes = resolve_runs_from(ns.runs_from, Path(ns.runs_root) if ns.runs_root else None)
        for n in notes:
            print(f"note: {n}", file=sys.stderr)
        run_dirs = resolved + run_dirs
    if not run_dirs:
        print("error: no run dirs (pass run_dir... or --runs-from)", file=sys.stderr)
        return 2

    if ns.watch:
        return _watch(run_dirs, maps, ns.interval)

    runs, caps, missing = [], [], []
    for rd in run_dirs:
        run_dir = Path(rd)
        if not (run_dir / "state.json").is_file():
            # A listed run whose dir is gone is REPORTED, never dropped: a
            # deliverable total that silently omits a segment is worse than one
            # that admits it is incomplete.
            missing.append(rd)
            continue
        try:
            runs.append(collect_run(run_dir, maps))
        except (FileNotFoundError, ValueError):
            missing.append(rd)
            continue
        caps.append(_budget_cap(run_dir))
    for rd in missing:
        print(f"error: {rd} has no state.json (not a run dir?) — counted as unknown, "
              f"never as zero", file=sys.stderr)
    if not runs:
        return 2
    if ns.compact:
        text, _ = compact_block(runs, caps)
        print(text)
    else:
        print(render(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
