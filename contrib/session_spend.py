#!/usr/bin/env python
"""session_spend.py — what THIS session has spent: the orchestrator's own
model calls plus the nodes of every lockstep run it has started.

    python contrib/session_spend.py [--repo-root .] [--runs-root runs]

Read-only, like every cockpit tool: no writes, no model calls, no tokens.
Also importable — `cost_report.py --session` and the mission views render the
same block through `session_lines()`.

WHAT "THE SESSION" IS (stated, because it is an assumption, not a fact the
run dirs record): the newest orchestrator transcript for this repo. The
orchestrator — the interactive agent driving lockstep (COCKPIT-THEORY-OF-
OPERATIONS §"you") — keeps its own transcript outside the repo:

- pi:           ~/.pi/agent/sessions/<munged cwd>/<stamp>_<id>.jsonl
                (assistant messages carry model + usage incl. provider-computed
                cost — on Copilot these track credit billing)
- claude code:  ~/.claude/projects/<munged cwd>/<session id>.jsonl
                (assistant entries carry message.model + message.usage;
                dollars only where the transcript reports them)

Newest mtime across both wins. A lockstep run belongs to the session when its
state.json `started_at` is at or after the session's first timestamp.

Honest-limits policy (same as cost_report): a transcript that reports no
dollars yields tokens with no dollar figure — never a fake $0. The session
total is labeled with what it covers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cost_report  # noqa: E402


# --- locating the orchestrator transcript --------------------------------------

def _munge(path: Path, keep_spaces: bool) -> str:
    """The path-to-dirname flattening both harnesses use, reduced to what can
    be matched reliably: separators and punctuation become '-'. pi keeps
    spaces and pads with '--'; claude code replaces every non-alphanumeric.
    Comparison happens on the '-'-stripped, casefolded form, so the pad and
    the exact pad width never matter."""
    pattern = r"[:\\/]" if keep_spaces else r"[^A-Za-z0-9]"
    return re.sub(pattern, "-", str(path)).strip("-").casefold()


def _match_dir(root: Path, wanted: str) -> Path | None:
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return None
    for d in dirs:
        if d.name.strip("-").casefold() == wanted:
            return d
    return None


def find_transcripts(repo_root: Path, home: Path | None = None) -> list[tuple[str, Path]]:
    """Every (source, jsonl path) candidate for this repo, both harnesses."""
    home = home or Path.home()
    out: list[tuple[str, Path]] = []
    pi_dir = _match_dir(home / ".pi" / "agent" / "sessions", _munge(repo_root, keep_spaces=True))
    if pi_dir:
        out += [("pi", p) for p in pi_dir.glob("*.jsonl")]
    cc_dir = _match_dir(home / ".claude" / "projects", _munge(repo_root, keep_spaces=False))
    if cc_dir:
        out += [("claude", p) for p in cc_dir.glob("*.jsonl")]
    return out


def newest_transcript(repo_root: Path, home: Path | None = None) -> tuple[str, Path] | None:
    best: tuple[float, str, Path] | None = None
    for source, path in find_transcripts(repo_root, home):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, source, path)
    return (best[1], best[2]) if best else None


# --- reading a transcript ------------------------------------------------------

def _ts(value) -> datetime | None:
    """ISO string or epoch millis, else None. Both transcript formats are
    third-party; be liberal."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    return None


def _iter_lines(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue  # a torn trailing line on a live transcript is normal


def read_pi_transcript(path: Path) -> dict:
    """Sum assistant usage from a pi session file. Same fields pi's own
    stream carries: usage{input, output, cacheRead, cacheWrite, cost{total}}."""
    sums: dict[str, float] = {}
    models: dict[str, float] = {}
    started: datetime | None = None
    messages = 0
    saw_cost = False
    for obj in _iter_lines(path):
        started = started or _ts(obj.get("timestamp"))
        msg = obj.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        usage = msg.get("usage") or {}
        messages += 1
        for field, dotted in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_read_tokens", "cacheRead"),
            ("cache_write_tokens", "cacheWrite"),
            ("cost", "cost.total"),
        ):
            v = cost_report.dig(usage, dotted)
            if v is not None:
                sums[field] = sums.get(field, 0.0) + v
                if field == "cost":
                    saw_cost = True
        model = msg.get("model")
        if isinstance(model, str) and model:
            w = cost_report.dig(usage, "cost.total") or cost_report.dig(usage, "output") or 1
            models[model] = models.get(model, 0.0) + float(w)
    if not saw_cost:
        sums.pop("cost", None)
    return {"sums": sums, "models": cost_report.dominant_models(models),
            "messages": messages, "started": started}


def read_claude_transcript(path: Path) -> dict:
    """Sum assistant usage from a claude code project transcript.

    Deduped on (requestId, message.id): one API response can appear as several
    entries (tool-use segmentation), each repeating the same usage — summing
    them raw over-reports the orchestrator several-fold. Last occurrence wins.
    Dollars only when an entry carries costUSD (older transcript format);
    current transcripts report tokens only, and that is what gets shown."""
    dedup: dict[str, dict] = {}
    started: datetime | None = None
    models: dict[str, float] = {}
    saw_cost = False
    for obj in _iter_lines(path):
        started = started or _ts(obj.get("timestamp"))
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        key = f"{obj.get('requestId')}:{msg.get('id')}"
        rec = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "cache_write_tokens": usage.get("cache_creation_input_tokens"),
            "cost": obj.get("costUSD"),
            "model": msg.get("model"),
        }
        dedup[key] = rec
    sums: dict[str, float] = {}
    for rec in dedup.values():
        for field in cost_report.KNOWN_FIELDS:
            v = rec.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                sums[field] = sums.get(field, 0.0) + v
                if field == "cost":
                    saw_cost = True
        model = rec.get("model")
        if isinstance(model, str) and model and model != "<synthetic>":
            w = rec.get("output_tokens") or 1
            models[model] = models.get(model, 0.0) + float(w)
    if not saw_cost:
        sums.pop("cost", None)
    return {"sums": sums, "models": cost_report.dominant_models(models),
            "messages": len(dedup), "started": started}


# --- the session ----------------------------------------------------------------

def _run_started(run_dir: Path) -> datetime | None:
    state = cost_report.read_state(run_dir, retries=1)
    return _ts((state or {}).get("started_at"))


def collect_session(repo_root: Path, runs_root: Path,
                    maps: dict | None = None, home: Path | None = None) -> dict | None:
    """The session block's data, or None when no transcript exists for this
    repo. `maps` is a cost-fields map (loaded if omitted)."""
    found = newest_transcript(Path(repo_root), home)
    if found is None:
        return None
    source, path = found
    orch = (read_pi_transcript if source == "pi" else read_claude_transcript)(path)
    started = orch["started"]
    if maps is None:
        maps = cost_report.load_field_maps(None)

    runs = []
    root = Path(runs_root)
    if root.is_dir() and started is not None:
        for d in sorted(root.iterdir()):
            try:
                if not d.is_dir() or not (d / "state.json").is_file():
                    continue
            except OSError:
                continue
            begun = _run_started(d)
            if begun is None or begun < started:
                continue
            try:
                run = cost_report.collect_run(d, maps)
            except (OSError, ValueError, FileNotFoundError):
                continue  # display-only; a torn run dir never kills the block
            rows = run["rows"]
            totals = {
                f: (sum(r[f] for r in rows if r[f] is not None)
                    if any(r[f] is not None for r in rows) else None)
                for f in cost_report.KNOWN_FIELDS
            }
            runs.append({
                "run_dir": str(d),
                "name": d.name,
                "flow": run["flow"],
                "token_spawns": run["token_spawns"],
                **totals,
            })
    return {"source": source, "transcript": str(path), "started": started,
            "orchestrator": orch, "runs": runs}


# --- rendering ------------------------------------------------------------------

RUN_LINES = 4        # newest runs listed individually; the rest are summed


def _tok(n: float | None) -> str:
    return "-" if n is None else f"{n:,.0f}"


def _money(v: float | None) -> str | None:
    return None if v is None else (f"${v:.2f}" if v >= 0.01 else f"${v:.4f}")


def session_lines(repo_root: Path, runs_root: Path,
                  maps: dict | None = None, home: Path | None = None) -> list[str]:
    """The compact session block. ASCII separators only (cmd.exe consoles),
    same discipline as cost_report's compact block."""
    got = collect_session(repo_root, runs_root, maps, home)
    if got is None:
        return ["this session: no orchestrator transcript found for this repo"]
    orch = got["orchestrator"]
    sums = orch["sums"]
    models = orch["models"]
    when = got["started"].astimezone().strftime("%H:%M") if got["started"] else "?"

    label = f"{got['source']} ({cost_report.short_model(models[0])})" if models else got["source"]
    tok_in = f"tokens: {_tok(sums.get('input_tokens'))} in"
    if sums.get("cache_read_tokens"):
        # claude transcripts count cache reads apart from input; "87 in" beside
        # a million cached-in tokens would read as a broken number.
        tok_in += f" (+{_tok(sums['cache_read_tokens'])} cached)"
    bits = [f"{tok_in} / {_tok(sums.get('output_tokens'))} out"]
    money = _money(sums.get("cost"))
    if money:
        bits.append(f"{money} notional")
    else:
        bits.append("no dollar figure in the transcript")
    lines = [f"this session (since {when}) - orchestrator {label}: " + " | ".join(bits)]

    run_cost_total = 0.0
    run_cost_known = False
    # One line per run, NEWEST FIRST and capped. A working session starts a run
    # a minute; this block was fifteen lines deep and pushing the decision below
    # the fold on a page whose whole job is that the decision is above it. The
    # tail is summed rather than dropped, because a total that quietly excludes
    # older runs is the one thing a spend figure may not do.
    shown = got["runs"][-RUN_LINES:][::-1]
    hidden = got["runs"][:-RUN_LINES] if len(got["runs"]) > RUN_LINES else []
    for run in shown:
        parts = [f"{run['token_spawns']} agent tasks"]
        if run.get("input_tokens") is not None or run.get("output_tokens") is not None:
            parts.append(f"{_tok(run.get('input_tokens'))} in / {_tok(run.get('output_tokens'))} out")
        money = _money(run.get("cost"))
        if money:
            parts.append(money)
            run_cost_total += run["cost"]
            run_cost_known = True
        elif run.get("input_tokens") is None:
            parts.append("no usage reported")
        lines.append(f"  run {run['name']}: " + " | ".join(parts))
    if hidden:
        spawns = sum(r["token_spawns"] for r in hidden)
        for r in hidden:
            if r.get("cost"):
                run_cost_total += r["cost"]
                run_cost_known = True
        lines.append(f"  + {len(hidden)} earlier run(s) this session: {spawns} agent tasks")
    if not got["runs"]:
        lines.append("  no lockstep runs started this session yet")

    total_bits = []
    if run_cost_known and sums.get("cost") is not None:
        total_bits.append(f"${run_cost_total + sums['cost']:.2f} notional (orchestrator + runs)")
    elif run_cost_known:
        total_bits.append(f"runs ${run_cost_total:.2f} notional (orchestrator reports no dollars)")
    if total_bits:
        lines.append("  session total: " + " | ".join(total_bits))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--runs-root", default=None, help="default <repo-root>/runs")
    ap.add_argument("--fields", default=None, help="cost-fields.toml path")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo_root)
    runs = Path(ns.runs_root) if ns.runs_root else repo / "runs"
    maps = cost_report.load_field_maps(ns.fields)
    for line in session_lines(repo, runs, maps):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
