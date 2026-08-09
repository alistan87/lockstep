#!/usr/bin/env python
"""mission_server.py — MISSION as a read-only local trace page.

    python contrib/mission_server.py                    # http://127.0.0.1:8787
    python contrib/mission_server.py --port 9000 runs/<run>

Four levels of disclosure on ONE page, for one audience:

  L0  board     the headline, the stat row, the collapsed step list, the spend
                meter, both cost blocks, ACTIVITY, and — when a decision waits —
                the evidence, verbatim. Server-rendered; it works with
                JavaScript switched off.
  L1  timeline  every step on a shared time axis, IN PLACE OF the step list,
                with a server-rendered table twin beside it.
  L2  step      a drawer: what a step produced, by name and size.
  L3  raw       node id, hash parts, what moved, the chain head — each with a
                one-line gloss, pinned by test.

The PAGE itself uses two routes: `/` for the document and `/api/events` as its
heartbeat, which decides when `/api/state` is worth fetching. `/api/node/<id>`,
`/api/evidence` and `/api/question` are the same projections as JSON, for a
reader or another tool; the page reaches all three server-rendered, because it
has to work with JavaScript off. That is stated here, and pinned by test,
because a route the page is ASSUMED to call and does not is worse than one it
never claimed — see the heartbeat note on `JS`.

Every word and every formatted time comes from `mission_view`. The page's
JavaScript swaps server-rendered fragments and advances an event cursor; it
formats nothing, because a formatter in the browser is a rendering pytest
cannot execute. The table twin is the accessibility path, the no-JS fallback,
and the test surface — which is what makes "no logic that can be wrong lives in
the JS" a structural fact rather than a discipline.

THE APPROVAL NEVER MOVES. There is no form on this page, no POST handler, and no
route that writes anything — not as policy, but as the absence of the code. A
browser button is exactly the forgeable channel this design exists to prevent:
the whole guarantee is that a decision happens at a keyboard, in a terminal, at
a prompt nothing can type into. `a` and `r` are the keys the domain expert was
taught, so pressing them here says where the decision happens rather than doing
nothing at all — a silent no-op at a decision moment is the worst available
behaviour.

ONE RUN, ONE TOKEN, NO ENUMERATION. Run identity stays mechanical — pinned, or
newest by mtime, re-resolved per request. A picker would create N MISSIONs and
strip the referent from "when two surfaces disagree, MISSION is right". But a
POLLED page breaks something a meta-refresh page could not: when the next
segment starts, the server begins answering for a different run while the
client still holds the old cursor. So every response carries a run token, and
the client discards its cursor and its rendered state when the token changes.

Bound to loopback by default. `--host` requires an explicit value and prints a
warning naming what is being exposed: `runs/` holds prompts, diffs, model output
and — when one exists — `rejection.txt`, the human's own words. It is gitignored
for those reasons, and this server applies no authentication whatsoever.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import mission_view as mv  # noqa: E402

# ---------------------------------------------------------------- palette
#
# Validated, not eyeballed. The cost stack is categorical slots 1-4 in fixed
# order; the status steps are the reserved status palette. Recorded output of
#   node scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500" \
#        --mode dark --surface "#1a1a19"
#   [PASS] Lightness band · Chroma floor · Contrast vs surface
#   [PASS] CVD separation      worst adjacent #c98500<->#199e70 dE 8.4 (protan)
#   [PASS] Normal-vision floor worst adjacent #c98500<->#199e70 dE 19.8

COST_SERIES = ("input", "output", "cache read", "cache write")
COST_HEX = ("#3987e5", "#d95926", "#199e70", "#c98500")
COST_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")

# Step state -> a CSS class over the status palette. The keys are EXACTLY
# `mission_view.GLOSSARY`'s, and every one of them is drawn with its COST_ICON
# glyph and its glossary word beside it: colour never carries meaning alone.
#
# `running` takes no status hue — it is not a severity, and painting it as one
# would misstate it. `pending` and `skipped` draw an empty track.
#
# DECORATED FORMS. `node_word` synthesizes "sent back for rework (1 of 2)" and
# appends map counters; neither is a glossary entry. Rework is a MODIFIER on a
# base status: the row keeps its base status class and the redone segments draw
# `ser`. The map counter is text, never colour. An unknown status renders
# `mut` with whatever string `node_word` returned.
STATUS_CLASS = {
    "done": "good",
    "blocked": "warn",
    "failed": "crit",
    "running": "run",
    "pending": "mut",
    "skipped": "mut",
}

# L3's own vocabulary, glossed. Pinned by test exactly as GLOSSARY is: the page
# must not acquire DE-facing words that nothing checks.
L3_GLOSSARY = {
    "step id": "the name the system uses for this step",
    "input-hash parts": "the things this step was given — if any of them changes, "
                        "the step is done again rather than reused",
    "what moved": "which of those things changed last time, and so why this step "
                  "could not be reused",
    "record head": "a fingerprint of this run's whole history; it changes if "
                   "anything in that history is altered",
    "record check": "whether that history still adds up when it is recomputed, "
                    "just now",
}

TERMINAL_SENTENCE = (
    "This decision happens in your terminal, at the prompt — a to approve, "
    "r to send back. Those keys do nothing here, and that is deliberate: "
    "nothing but a person at a keyboard can answer."
)

OFFLINE_SENTENCE = (
    "This page has stopped hearing from the run. What you can see below is the "
    "last thing it knew, and it may be out of date — ask the assistant whether "
    "the run is still going."
)

# Defence in depth on a page whose every string came from somewhere else — a
# label from a sidecar, a note from an agent, evidence from a render node. The
# escaping is the guarantee (and is tested); these are what stop a miss from
# being exploitable, and they matter more once `--host` puts the page on a
# network. `connect-src 'self'` is the load-bearing one: it means even injected
# script could not send a run directory anywhere.
SECURITY_HEADERS = (
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
     "connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'; "
     "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)

POLL_MS = 1000
# How many quiet ticks pass before the page re-renders anyway. Only a running
# node makes the page change on its own (an elapsed clock, a bar drawing to
# now); with nothing running there is nothing for a re-render to say.
IDLE_REFRESH_TICKS = 5
FEED_LIMIT = 12


# ----------------------------------------------------------------- reading

def _trace_status(run_dir: Path) -> dict | None:
    try:
        from lockstep.state import trace_status
        return trace_status(Path(run_dir))
    except Exception:  # noqa: BLE001 - a view never raises
        return None


def _events(run_dir: Path) -> list[dict]:
    try:
        import cost_report
        return cost_report._read_events(Path(run_dir))
    except Exception:  # noqa: BLE001
        return []


def _events_after(run_dir: Path, after: int) -> list[dict]:
    """Only the journal lines past the cursor, JSON-parsed. The whole-file read
    is unavoidable; parsing every line of a long journal once a second is not.
    A torn trailing line is tolerated, as everywhere else (SPEC §10.3)."""
    path = Path(run_dir) / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for i, line in enumerate(lines):
        if i < after or not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:
                continue
            return []  # a mid-file tear: the view says nothing rather than lying
    return out


def _intervals(run_dir: Path) -> dict[str, list[tuple[str, str | None]]]:
    try:
        import cost_report
        return cost_report.node_intervals(_events(run_dir))
    except Exception:  # noqa: BLE001
        return {}


def _collect(run_dir: Path) -> dict | None:
    try:
        import cost_report
        return cost_report.collect_run(Path(run_dir), cost_report.load_field_maps(None))
    except Exception:  # noqa: BLE001
        return None


def _cap(run_dir: Path) -> int | None:
    try:
        import cost_report
        return cost_report._budget_cap(Path(run_dir))
    except Exception:  # noqa: BLE001
        return None


def run_token(run_dir: Path | None) -> str:
    """Which run every response is about.

    A meta-refresh page reset its whole client state by construction; a poll
    does not. Without this, the segment boundary leaves the client asking for
    `after=400` of a twelve-event run and getting nothing, forever.
    """
    if run_dir is None:
        return ""
    state = mv.read_json(Path(run_dir) / "state.json") or {}
    return f"{Path(run_dir).name}:{state.get('started_at', '')}"


# ------------------------------------------------------------- projections

def chain_chip(run_dir: Path) -> dict:
    """The four-way trace rule, and it belongs at L0, not three levels down.

    `ok` alone cannot be rendered: a tamper returns ok=False WITH a non-empty
    head, and a healthy fresh run returns ok=True with an empty one. A journal
    that renders BROKEN must appear on the landing view of the surface the
    domain expert is now expected to open.
    """
    s = _trace_status(run_dir)
    if s is None:
        return {"cls": "mut", "text": "record check unavailable", "detail": ""}
    if not s["ok"]:
        line = f" (line {s['first_bad_line']})" if s["first_bad_line"] else ""
        return {"cls": "crit", "text": f"BROKEN — the record does not add up{line}",
                "detail": s["detail"]}
    if not s["total"]:
        return {"cls": "mut", "text": "nothing to verify yet", "detail": s["detail"]}
    if not s["chained"]:
        return {"cls": "warn", "text": f"unchained — {s['total']} events carry no fingerprint",
                "detail": s["detail"]}
    return {"cls": "good", "text": f"record verified · {s['chained']} events",
            "detail": s["detail"]}


def spend_meter(runs: list[dict], caps: list[int | None]) -> dict:
    """`{used, cap, partial, label, pct, over}` — the meter's numbers.

    The denominator is `cost_report._budget_cap`, which reads THE RUN'S OWN
    FLOW COPY: the number the domain expert was quoted at the consent beat, not
    a live config and emphatically not the cockpit journal, which is
    agent-authored, has no schema, and is by doctrine evidence of what was said
    rather than truth about state.

    Two cases a bare meter cannot do. No cap declared -> the count with no
    denominator and no meter, as `plan_card.py` does. Several segments -> caps
    sum, degrading to `of at least N` when one of them declares none; the
    guard that fixed a real `used 38 of 25`.

    NO SEVERITY RAMP. Nothing in the run dir says 80% of a ceiling is a
    warning, and inventing that threshold would be the first editorial judgment
    on a view that is summary-free by construction. One hue, the ceiling
    marked; the only colour change is AT or OVER it, which is mechanical.
    """
    used = sum(int(r.get("token_spawns") or 0) for r in runs)
    known = [c for c in caps if c]
    cap = sum(known) if known else None
    partial = bool(known) and len(known) < len(runs)
    if cap is None:
        return {"used": used, "cap": None, "partial": False, "pct": None,
                "over": False, "label": f"agent tasks used {used}"}
    label = (f"agent tasks used {used} of at least {cap}" if partial
             else f"agent tasks used {used} of {cap}")
    return {"used": used, "cap": cap, "partial": partial,
            "pct": min(100.0, 100.0 * used / cap), "over": used >= cap, "label": label}


def cost_stack(run: dict | None) -> list[dict]:
    """The four cost series as `{name, hex, value, pct}`, in fixed order. Empty
    when nothing was reported — the page omits the block rather than drawing a
    bar of zeroes."""
    if not run:
        return []
    totals = [sum(int(r.get(f) or 0) for r in run["rows"]) for f in COST_FIELDS]
    grand = sum(totals)
    if not grand:
        return []
    return [
        {"name": name, "hex": hexv, "slot": i + 1, "value": value,
         "pct": 100.0 * value / grand}
        for i, (name, hexv, value) in enumerate(zip(COST_SERIES, COST_HEX, totals))
    ]


_TICK_LADDER = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400)


def _tick_step(span_s: float) -> int:
    for step in _TICK_LADDER:
        if 3 <= span_s / step <= 8:
            return step
    return _TICK_LADDER[-1] if span_s / _TICK_LADDER[-1] > 8 else _TICK_LADDER[0]


def waterfall(run_dir: Path, repo_root: Path | None = None,
              now: datetime | None = None) -> dict:
    """`{rows, ticks, plotted, span_s}` — the timeline's whole geometry, here.

    ONE SEGMENT PER INTERVAL, never one span per node: `state.json`'s
    `started_at` is the first start and `ended_at` the last end, kept across
    every attempt, heal round and resume, so a node blocked overnight would
    draw a fourteen-hour bar of which minutes were work. The table twin sums
    the same intervals, so the picture and the number cannot disagree.

    Row order is first `running` event; nodes that never ran sort last, in
    graph order, and draw an empty track.
    """
    steps = mv.step_rows(run_dir, repo_root, collapsed=False)
    spans = _intervals(run_dir)
    now = now or datetime.now(timezone.utc)

    def start_of(step: dict):
        got = spans.get(step["node_id"]) or []
        return mv._parse_ts(got[0][0]) if got else None

    ran = [s for s in steps if start_of(s)]
    never = [s for s in steps if not start_of(s)]
    ran.sort(key=start_of)  # type: ignore[arg-type]
    ordered = ran + never

    stamps: list[datetime] = []
    open_span = False
    for s in ran:
        for a, b in spans.get(s["node_id"], []):
            ta, tb = mv._parse_ts(a), mv._parse_ts(b)
            if ta:
                stamps.append(ta)
            if tb:
                stamps.append(tb)
            else:
                open_span = True
    t0 = min(stamps) if stamps else None
    t1 = max(stamps) if stamps else None
    if t0 is not None and (open_span or t1 is None or t1 < now):
        t1 = max(t1 or now, now)
    total = (t1 - t0).total_seconds() if (t0 and t1) else 0.0
    plotted = bool(t0) and total > 0

    def pct(t: datetime) -> float:
        return max(0.0, min(100.0, 100.0 * (t - t0).total_seconds() / total))  # type: ignore[operator]

    rows = []
    for step in ordered:
        node_spans = spans.get(step["node_id"], [])
        base = STATUS_CLASS.get(step["status"], "mut")
        segs = []
        worked = 0.0
        first_start = None
        for i, (a, b) in enumerate(node_spans):
            ta = mv._parse_ts(a)
            if ta is None:
                continue
            first_start = first_start or ta
            tb = mv._parse_ts(b) if b else None
            # An interval is only still running if the STEP is. An interval
            # left open by a crash belongs to a step that is not running now,
            # and measuring it to `now` would grow forever — the bar would
            # stretch across the plot and the twin would report the age of the
            # run dir as work.
            live = tb is None and step["status"] in ("running", "blocked")
            end = tb or (now if live else ta)
            seconds = max(0.0, (end - ta).total_seconds())
            # Open-but-live intervals count: the bar tip shows a running step's
            # elapsed-so-far, and a value the chart has and the table twin does
            # not is a value only reachable by hovering.
            worked += seconds
            # A done node's SUPERSEDED intervals — the attempt a heal round or
            # a re-run replaced — draw `serious`: a modifier on the base status,
            # not a status of their own. Same fact the cost panel labels
            # "(superseded)".
            cls = "ser" if (i < len(node_spans) - 1 and step["status"] == "done") else base
            if plotted:
                left, right = pct(ta), pct(end)
                segs.append({
                    "left": left, "width": max(0.0, right - left), "cls": cls,
                    "open": live,
                    "title": (f"{step['label']} — {step['word']} · "
                              f"{mv.format_clock(a)}–{mv.format_clock(b) if b else 'now'} · "
                              f"{mv.format_duration(seconds)}"),
                    # A duration at the bar tip ONLY for the running step and
                    # any failed step. Never a number on every bar.
                    "tip": (mv.format_duration(seconds)
                            if step["status"] in ("running", "failed", "blocked") else ""),
                })
        rows.append({
            **step,
            "cls": base,
            "segments": segs,
            "started": mv.format_clock(node_spans[0][0]) if node_spans else "",
            "worked": mv.format_duration(worked) if worked else "",
            "tries": len(node_spans),
        })

    ticks = []
    if plotted:
        step_s = _tick_step(total)
        first = t0 + timedelta(seconds=step_s - (t0.timestamp() % step_s))  # type: ignore[operator]
        cur = first
        while cur < t1:  # type: ignore[operator]
            ticks.append({"pct": pct(cur), "label": mv.format_clock(cur.isoformat())})
            cur = cur + timedelta(seconds=step_s)
    return {"rows": rows, "ticks": ticks, "plotted": plotted, "span_s": total}


def event_text(ev: dict, labels: dict[str, str]) -> str:
    """One journal line, in the DE's words and with the clock already applied.

    The client appends this string. It does not build it — a status word or a
    time formatted in JavaScript is a glossary pytest cannot execute.
    """
    when = mv.format_clock(ev.get("ts")) or "--:--"
    node = ev.get("node") or ""
    status = ev.get("status") or ""
    word = mv.GLOSSARY.get(status, status)
    if node:
        return f"{when}  {mv.label_for(labels, node)}  {word}".rstrip()
    return f"{when}  {word}".rstrip()


def node_drawer(run_dir: Path, node_id: str, repo_root: Path | None = None, *,
                state: dict | None = None, labels: dict[str, str] | None = None) -> dict:
    """L2. `node_detail`'s body, named in L0's words — the FULL label, without
    the board's 33-character truncation and without the `(step id: …)` suffix.
    The identifier lives at L3.

    Names and sizes, never stdout bodies: `stdout.log` is the harness envelope,
    i.e. the model's whole result text, and tailing it was rejected for good
    reason.
    """
    labels = labels if labels is not None else mv.load_labels(run_dir, repo_root)
    lines = mv.node_detail(run_dir, node_id, repo_root, state=state, labels=labels)
    body = [ln for ln in lines
            if not ln.startswith("=") and not ln.strip().startswith("(step id:")]
    # The label line node_detail prints as its heading is now the drawer title.
    if body and body[0].strip() == mv.label_for(labels, node_id):
        body = body[1:]
    return {"node_id": node_id, "label": mv.label_for(labels, node_id),
            "lines": [ln.rstrip() for ln in body]}


def raw_record(run_dir: Path, node_id: str | None = None) -> list[dict]:
    """L3, every term glossed. `{term, gloss, value}`."""
    state = mv.read_json(Path(run_dir) / "state.json") or {}
    rec = (state.get("nodes") or {}).get(node_id or "") or {}
    chain = _trace_status(run_dir) or {}
    parts = rec.get("hash_parts") or {}
    moved = rec.get("invalidated_by")
    out = []
    if node_id:
        out.append({"term": "step id", "gloss": L3_GLOSSARY["step id"], "value": node_id})
        out.append({"term": "input-hash parts", "gloss": L3_GLOSSARY["input-hash parts"],
                    "value": ", ".join(sorted(parts)) if parts else "—"})
        out.append({"term": "what moved", "gloss": L3_GLOSSARY["what moved"],
                    "value": ", ".join(moved) if moved else "nothing — it was reused"})
    out.append({"term": "record head", "gloss": L3_GLOSSARY["record head"],
                "value": (chain.get("head") or "—")[:16] or "—"})
    out.append({"term": "record check", "gloss": L3_GLOSSARY["record check"],
                "value": chain.get("detail") or "—"})
    return out


# ------------------------------------------------------------------- HTML

def e(text) -> str:
    return html.escape("" if text is None else str(text))


CSS = """
/* CALM DENSITY, dark-first, after Linear's chrome: a near-black plane, cards a
   hair lighter, separated by low-alpha hairlines rather than borders you can
   see; small cool-grey type; a tight vertical rhythm; and NO chrome accent.

   That last one is deliberate rather than austere. On this page every hue
   already means something — four categorical slots for the cost stack, four
   reserved status steps for state — so an accent introduced for selection
   would be a fifth meaning competing with them. Chrome is ink and surface;
   colour is data and state. Selection is a surface step, not a hue.

   The DATA colours are unchanged and still validated. Re-run after ANY surface
   change (this one was):
     node scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500" \
          --mode dark --surface "#141517"          -> ALL CHECKS PASS
   Every status step and every ink token gained contrast on the darker surface:
   status critical 3.62 -> 3.80, muted 4.85 -> 5.62, secondary ink 9.72 -> 10.20. */
:root{
 --plane:#0b0c0d;      /* page */
 --surface:#141517;    /* cards, and the chart surface the palette validates against */
 --inset:#0e0f11;      /* evidence, pre blocks, the meter track */
 --line:rgba(255,255,255,.07);
 --line-2:rgba(255,255,255,.045);
 --ink:#fff; --ink-2:#c3c2b7; --muted:#8a8f98;
 --grid:#232529; --axis:#2e3136;
 --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
 COST_VARS   /* generated from COST_HEX: one source, or the stylesheet and the
                stack drift apart and only one of them is pinned by test */
}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;padding:20px 20px 56px;background:var(--plane);color:var(--ink);
 font:13.5px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;transition:opacity .12s}
.wrap.stale{opacity:.62}
.top{display:flex;align-items:center;gap:9px;margin-bottom:16px;flex-wrap:wrap}
.brand{font-weight:600;letter-spacing:.02em;font-size:12.5px}
.runid{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12.5px}
.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:6px;
 border:1px solid var(--line);background:var(--surface);color:var(--ink-2);font-size:12px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--muted);flex:none}
.dot.good{background:var(--good)}.dot.warn{background:var(--warning)}
.dot.crit{background:var(--critical)}.dot.run{background:var(--ink)}
.live .dot{animation:pulse 1.8s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.live .dot{animation:none}}
.hero{font-size:21px;font-weight:600;line-height:1.32;letter-spacing:-.011em;margin:0 0 3px}
.hero-sub{color:var(--muted);margin:0 0 16px;font-size:12.5px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;
 margin-bottom:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;
 padding:10px 12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
.tile .k{color:var(--muted);font-size:11.5px;margin-bottom:3px}
.tile .v{font-size:20px;font-weight:600;letter-spacing:-.01em}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;
 padding:14px 16px;margin-bottom:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
.card>h2,.card .cardhead h2{font-size:11.5px;font-weight:500;color:var(--muted);margin:0 0 10px;
 letter-spacing:.02em}
.meter-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.meter-head .lab{color:var(--ink-2);font-size:13px}
.meter-head .num{font-size:14px;font-variant-numeric:tabular-nums}
.track{position:relative;height:6px;border-radius:3px;background:var(--inset);
 border:1px solid var(--line-2)}
.fill{position:absolute;inset:0 auto 0 0;border-radius:3px;background:var(--c1)}
.fill.over{background:var(--critical)}
.ceil{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink-2);right:0}
.meter-foot{color:var(--muted);font-size:11.5px;margin-top:7px}
.decide{border-left:2px solid var(--warning)}
.decide h2{color:var(--warning);font-size:12.5px}
/* A question is not a decision, and it is not a severity either, so it takes
   INK rather than a hue. Slot-1 blue here — which is what the first cut used —
   would be the cost stack's "input" colour doing duty as chrome, and a
   categorical hue meaning two things on one page is the collision the palette
   rules exist to prevent. The words already say which kind of attention it is. */
.ask{border-left:2px solid var(--line)}
.ask h2{color:var(--ink-2);font-size:12.5px}
.evidence{background:var(--inset);border:1px solid var(--line-2);border-radius:6px;
 padding:12px 14px;margin:2px 0 10px;
 font:12.5px/1.6 ui-monospace,Consolas,monospace;color:var(--ink-2);white-space:pre-wrap}
.stale-note{color:var(--warning);font-size:12.5px;margin:0 0 10px}
.terminal-note{color:var(--muted);font-size:12.5px}
.key-echo{color:var(--warning);font-size:12.5px;margin-top:7px}
.cardhead{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.cardhead h2{margin:0}
.viewswitch{display:flex;gap:4px;margin-left:auto}
.btn{background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:6px;
 padding:4px 9px;font:inherit;font-size:12px;cursor:pointer}
.btn:hover{color:var(--ink-2);background:rgba(255,255,255,.03)}
.btn[aria-pressed="true"]{background:rgba(255,255,255,.06);color:var(--ink);
 border-color:rgba(255,255,255,.14)}
.steps{display:grid;gap:1px}
.step{display:grid;grid-template-columns:18px 1fr auto;align-items:center;gap:9px;
 padding:5px 7px;border-radius:6px}
.step:hover{background:rgba(255,255,255,.03)}
.step .ico{text-align:center;font-size:12.5px}
.step .word{color:var(--muted);font-size:12.5px}
.step .name,.wf-lab a{color:var(--ink);text-decoration:none}
.step .name:hover,.wf-lab a:hover{text-decoration:underline}
.ico.good{color:var(--good)}.ico.warn{color:var(--warning)}.ico.crit{color:var(--critical)}
.ico.ser{color:var(--serious)}.ico.mut{color:var(--muted)}.ico.run{color:var(--ink)}
.note{grid-column:2/-1;color:var(--ink-2);font-size:12.5px;border-left:1px solid var(--axis);
 padding-left:9px;margin:1px 0 3px}
/* A STABLE SLOT, present from the first render even when it is empty. Every
   completion removes a row and increments `N finished`, at 1 Hz; if the
   counter line appeared only once there was something to count, the chrome
   below it would jump the first time a step finished. The prohibition is on
   CHROME jumping, not on the data changing — a row leaving is data. */
.tail{color:var(--muted);font-size:12.5px;padding:8px 7px 0;min-height:29px}
.wf-plot{position:relative;--gutter:236px}
.wf{display:grid;grid-template-columns:var(--gutter) 1fr;gap:0 14px}
.wf-row{display:contents}
.wf-lab{display:flex;flex-direction:column;justify-content:center;height:34px;font-size:12.5px;
 min-width:0}
.wf-lab .n{display:flex;align-items:center;gap:7px;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.wf-lab .w{color:var(--muted);font-size:11.5px;padding-left:19px;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.wf-track{position:relative;height:34px}
.wf-track.skip::before{opacity:.45}
.wf-track::before{content:"";position:absolute;left:0;right:0;top:15px;height:4px;
 background:var(--inset);border-radius:2px}
.seg{position:absolute;top:12px;height:10px;min-width:3px;border-radius:4px}
.seg.good{background:var(--good)}.seg.ser{background:var(--serious)}
.seg.crit{background:var(--critical)}.seg.warn{background:var(--warning)}
.seg.mut{background:var(--muted)}
.seg.run{background:transparent;border:2px solid var(--ink)}
.seg.open::after{content:"";position:absolute;right:-1px;top:-2px;bottom:-2px;width:30px;
 background:linear-gradient(90deg,transparent,var(--surface));border-radius:4px}
/* The hit area takes in the 2px gaps above and below and reaches ~26px, so a
   one-minute step is not a 10px target. A pseudo-element, so it costs no layout. */
.seg::before{content:"";position:absolute;inset:-8px -2px;border-radius:6px}
.seg:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
.seg .tip{position:absolute;left:calc(100% + 8px);top:-3px;font-size:11.5px;color:var(--ink-2);
 white-space:nowrap;font-variant-numeric:tabular-nums}
/* Hover and keyboard focus show the SAME thing. `title` would show on hover
   only, which would put a value behind a pointer. */
.seg .hint{display:none;position:absolute;left:0;bottom:calc(100% + 8px);z-index:3;
 background:var(--inset);border:1px solid var(--line);border-radius:6px;padding:5px 9px;
 font-size:11.5px;color:var(--ink-2);white-space:nowrap}
.seg.end .hint{left:auto;right:0}   /* clamped: neither may overflow the plot */
.seg.end .tip{left:auto;right:calc(100% + 8px)}
.seg.inside .hint{left:auto;right:0}
.seg.inside .tip{left:auto;right:7px;top:-1px;color:var(--plane);font-weight:600}
.seg.run.inside .tip{color:var(--ink);font-weight:400}
.seg:hover .hint,.seg:focus-visible .hint{display:block}
/* the axis and the gridlines share the TRACK column's coordinate space, not
   the card's, so a tick and a bar edge mean the same x */
.wf-scale{position:absolute;left:calc(var(--gutter) + 14px);right:0;top:0;bottom:28px;
 pointer-events:none}
.gridline{position:absolute;top:0;bottom:0;width:1px;background:var(--grid)}
.wf-axis{position:relative;height:24px;margin-top:4px;border-top:1px solid var(--axis);
 margin-left:calc(var(--gutter) + 14px)}
.tick{position:absolute;top:0;font-size:11px;color:var(--muted);transform:translateX(-50%);
 padding-top:5px;font-variant-numeric:tabular-nums}
.stack{display:flex;gap:2px;height:10px;margin:2px 0 10px}
.stack>i{display:block;border-radius:3px}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--ink-2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:9px;height:9px;border-radius:2px;display:inline-block}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 10px 5px 0;border-bottom:1px solid var(--line-2);
 color:var(--ink-2)}
th{color:var(--muted);font-weight:500;font-size:11.5px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
details>summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:8px;
 padding:3px 0;list-style:none}
details>summary::-webkit-details-marker{display:none}
/* The character itself, not a CSS escape: `\\203A` inside a PYTHON string is an
   OCTAL escape (\\203 -> U+0083), so the browser was handed a control character
   followed by a literal "A" and every disclosure triangle rendered as tofu. */
details>summary::before{content:"› ";display:inline-block;transition:transform .12s}
details[open]>summary::before{transform:rotate(90deg)}
pre{white-space:pre-wrap;margin:0;font:12.5px/1.55 ui-monospace,Consolas,monospace;
 color:var(--ink-2);background:var(--inset);border:1px solid var(--line-2);border-radius:6px;
 padding:10px 12px}
.feed{display:grid;gap:1px;font-size:12.5px;color:var(--ink-2)}
.feed div{padding:2px 0}
.foot{color:var(--muted);font-size:11.5px;margin-top:22px;text-align:center}
body.offline .live .dot{animation:none;background:var(--muted)}
@media (max-width:700px){.wf-plot{--gutter:118px}.wf-lab .w{padding-left:0}
 .seg .hint{white-space:normal;max-width:60vw}}
/* Under forced colours or on paper, the page falls to the TABLE VIEW. Both are
   contexts where a positioned bar says nothing; the twin carries every value a
   bar carries, which is why it exists. The ID selectors beat `[hidden]`. */
@media print,(forced-colors:active){
 .wf-plot,.stack,.track,.ceil{display:none}
 #l0,#l1{display:block!important}
 .viewswitch{display:none}
}
@media (forced-colors:active){.seg,.fill,.stack>i{forced-color-adjust:none}}
"""

# The whole of the client. It toggles two fragments, swaps server-rendered HTML,
# unhides two server-rendered sentences, and advances an integer. It renders no
# word, formats no time, and computes no geometry.
#
# The heartbeat is `/api/events`, not `/api/state`. The first cut polled the
# expensive route once a second and never called the cheap one at all — 128 ms
# and 227 KB per tick on a 40-node run, forever, for a page that mostly had not
# changed, while the cursor sat in a dataset attribute nothing read. A dead
# channel that looks wired is worse than no channel (`cockpit.ps1:302-308`).
#
# And a swap is not free to the READER either: `innerHTML` destroys their text
# selection, their open drawers and their focus. So the page is only taken away
# from them when the journal moved, when the run changed, or — while something
# is running, where the clock genuinely ticks — every few seconds; and never
# while they are selecting text.
JS = """
(function () {
  var wrap = document.querySelector('.wrap');
  function show(which) {
    var l0 = document.getElementById('l0'), l1 = document.getElementById('l1');
    if (!l0 || !l1) return;
    l0.hidden = which !== 'l0'; l1.hidden = which !== 'l1';
    document.querySelectorAll('.viewswitch .btn').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.view === which));
    });
    try { sessionStorage.setItem('lockstep-view', which); } catch (err) {}
  }
  window.lockstepShow = show;
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('.viewswitch .btn');
    if (b) show(b.dataset.view);
  });
  try { show(sessionStorage.getItem('lockstep-view') || 'l0'); } catch (err) { show('l0'); }

  var echoShown = false;
  // `a` and `r` are the keys the domain expert was taught. The sentence they
  // reveal is rendered by the server; this only unhides it.
  addEventListener('keydown', function (ev) {
    if (ev.key !== 'a' && ev.key !== 'r') return;
    echoShown = true;
    var echo = document.getElementById('key-echo');
    if (echo) echo.hidden = false;
  });

  var token = document.body.dataset.runToken || '';
  var cursor = parseInt(document.body.dataset.eventCursor || '0', 10);
  var quiet = 0, fails = 0, busy = false, dirty = false;

  function selecting() {
    var s = window.getSelection();
    return !!(s && !s.isCollapsed && s.anchorNode && wrap.contains(s.anchorNode));
  }
  function currentView() {
    var b = document.querySelector('.viewswitch .btn[aria-pressed="true"]');
    return b ? b.dataset.view : 'l0';
  }
  function openIds() {
    return Array.prototype.map.call(
      document.querySelectorAll('details[open][id]'), function (d) { return d.id; });
  }
  function offline(down) {
    var note = document.getElementById('offline-note');
    if (note) note.hidden = !down;
    // A live dot pulsing over data nobody is receiving is a lie told in
    // presentation. The class stops the animation; the WORDS are the server's.
    document.body.classList.toggle('offline', down);
  }

  function refresh() {
    if (busy || selecting()) return false;  // never take the page out from under a reader
    busy = true;
    var view = currentView(), open = openIds();
    var focused = document.activeElement && document.activeElement.id;
    wrap.classList.add('stale');       // hold the previous render, never a skeleton
    fetch('api/state', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (doc) {
        wrap.innerHTML = doc.html;
        show(view);
        open.forEach(function (id) { var d = document.getElementById(id); if (d) d.open = true; });
        if (focused) { var f = document.getElementById(focused); if (f) f.focus(); }
        if (echoShown) { var e = document.getElementById('key-echo'); if (e) e.hidden = false; }
      })
      .catch(function () {})
      .then(function () { wrap.classList.remove('stale'); busy = false; });
    return true;
  }

  function tick() {
    if (document.visibilityState === 'hidden') return;
    fetch('api/events?after=' + cursor, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) { throw r.status; } return r.json(); })
      .then(function (doc) {
        fails = 0; offline(false);
        // A new run: `doc.next` was computed against the OLD cursor and means
        // nothing here, so it is discarded rather than kept. Taking it would
        // leave the client asking for `after=400` of a twelve-event run, which
        // is the exact failure the run token exists to prevent.
        if (doc.token !== token) {
          token = doc.token; cursor = 0; quiet = 0; dirty = true;
          if (refresh()) { dirty = false; }
          return;
        }
        // `dirty` outlives the tick that saw the movement. Without it, a reader
        // who leaves text selected consumes the cursor advance and then never
        // sees the refresh it should have caused — the page would sit stale
        // until the NEXT event, which on a finished run never comes.
        if (doc.next !== cursor) { dirty = true; quiet = 0; } else { quiet += 1; }
        cursor = doc.next;
        if (dirty || (doc.live && quiet >= IDLE_REFRESH_TICKS)) {
          if (refresh()) { dirty = false; quiet = 0; }
        }
      })
      .catch(function () { if (++fails >= 3) { offline(true); } });
  }
  setInterval(tick, POLL_MS);
})();
"""


def stylesheet() -> str:
    """`CSS` with the cost slots substituted from `COST_HEX`, so the validated
    palette has exactly one home. It is `COST_HEX` because that is the tuple
    the validator output is pinned against."""
    slots = " ".join(f"--c{i + 1}:{hexv};" for i, hexv in enumerate(COST_HEX))
    return CSS.replace("COST_VARS", slots)


def client_js() -> str:
    """`JS` with its two tuning constants substituted. They live in Python so
    the cadence is one fact, testable, rather than a number in a string."""
    return (JS.replace("POLL_MS", str(POLL_MS))
              .replace("IDLE_REFRESH_TICKS", str(IDLE_REFRESH_TICKS)))


def _icon_span(row: dict) -> str:
    return f'<span class="ico {e(row["cls"])}">{e(row["icon"])}</span>'


def render_board(run_dir: Path, repo_root: Path | None) -> str:
    """L0's step list: the collapsed board, exactly `mission_rows`' row set."""
    out = ['<div id="l0" class="steps">']
    for row in mv.step_rows(run_dir, repo_root):
        cls = STATUS_CLASS.get(row["status"], "mut")
        out.append(
            f'<div class="step"><span class="ico {cls}">{e(row["icon"])}</span>'
            f'<a class="name" href="#step-{e(row["node_id"])}">{e(row["label"])}</a>'
            f'<span class="word">{e(row["word"])}</span></div>'
        )
        if row["note"]:
            out.append(f'<div class="note">{e(row["note"])}</div>')
    # ONE tail element, always present even when it says nothing: the counters
    # occupy a stable slot so a completion increments a number in place instead
    # of inserting a row and shifting everything below it, at 1 Hz.
    out.append(f'<div class="tail">{e("  ·  ".join(mv.collapse_tail(run_dir, repo_root)))}</div>')
    out.append("</div>")
    return "\n".join(out)


def render_timeline(wf: dict) -> str:
    """L1: the waterfall, and the table twin that is its fallback and its test
    surface. The switch REPLACES the step list — `mission_rows` synthesizes and
    reorders rows, so nothing here claims the two agree row for row. What must
    not be lost is a note, which travels as a marker on its row."""
    # NOT `hidden` in the served HTML. With JavaScript off, both views render
    # and nothing switches — which is the honest fallback. The client hides one
    # of them on load; a table twin behind a `hidden` attribute would be a
    # fallback that only works when the thing it falls back from does.
    out = ['<div id="l1">']
    if wf["plotted"]:
        out.append('<div class="wf-plot"><div class="wf-scale">')
        for tick in wf["ticks"]:
            out.append(f'<div class="gridline" style="left:{tick["pct"]:.2f}%"></div>')
        out.append('</div><div class="wf">')
        for row in wf["rows"]:
            marker = ' <span class="ico mut" title="this step left a note">•</span>' if row["note"] else ""
            out.append(
                '<div class="wf-row">'
                f'<div class="wf-lab"><span class="n">{_icon_span(row)}'
                f'<a href="#step-{e(row["node_id"])}">{e(row["label"])}</a>{marker}</span>'
                f'<span class="w">{e(row["word"])}</span></div>'
                f'<div class="wf-track{" skip" if row["status"] == "skipped" else ""}">'
            )
            for seg in row["segments"]:
                tip = f'<span class="tip">{e(seg["tip"])}</span>' if seg["tip"] else ""
                # `end` flips the hint to the right edge for a bar in the right
                # half of the plot: a tip overflowing the plot was one of the
                # four defects rendering the mockup caught.
                # A bar spanning the plot has no OUTSIDE left to put a tip on:
                # `end` pushed it 8px left of the plot, into the label gutter,
                # where it sat on top of the step names (seen on the pi run,
                # where core ran 15 minutes and filled the width). Wide enough
                # to hold the tip -> put it inside.
                inside = seg["width"] > 25
                extra = ("".join([" open" if seg["open"] else "",
                                  " inside" if inside else
                                  (" end" if seg["left"] + seg["width"] > 60 else "")]))
                out.append(
                    f'<div class="seg {e(seg["cls"])}{extra}" tabindex="0" role="img" '
                    f'aria-label="{e(seg["title"])}" '
                    f'style="left:{seg["left"]:.2f}%;width:{seg["width"]:.2f}%">'
                    f'<span class="hint" aria-hidden="true">{e(seg["title"])}</span>{tip}</div>'
                )
            out.append("</div></div>")
        out.append('</div><div class="wf-axis">')
        for tick in wf["ticks"]:
            out.append(f'<span class="tick" style="left:{tick["pct"]:.2f}%">{e(tick["label"])}</span>')
        out.append("</div></div>")

    out.append('<details id="table-twin" open><summary>the same thing as a table</summary><table>')
    out.append('<tr><th>step</th><th>state</th><th class="n">started</th>'
               '<th class="n">worked for</th><th class="n">tries</th></tr>')
    for row in wf["rows"]:
        note = f'<div class="note">{e(row["note"])}</div>' if row["note"] else ""
        out.append(
            f'<tr><td>{e(row["label"])}{note}</td><td>{e(row["word"])}</td>'
            f'<td class="n">{e(row["started"] or "—")}</td>'
            f'<td class="n">{e(row["worked"] or "—")}</td>'
            f'<td class="n">{e(row["tries"] or "—")}</td></tr>'
        )
    out.append("</table></details></div>")
    return "\n".join(out)


def _stat_tiles(state: dict, flow: dict | None, run: dict | None, meter: dict) -> str:
    """Four tiles, and the fourth is chosen mechanically: how far away the
    human's turn is when the flow has exactly one approval to measure against
    (`steps_to_decision` returns None otherwise), else how much has been sent
    back. Sentence-case labels, proportional figures — `tabular-nums` is for
    the table's columns and the axis ticks, never for a large standalone
    number."""
    recs = list((state.get("nodes") or {}).values())
    total = len(recs)
    settled = sum(1 for r in recs if r.get("status") in ("done", "skipped"))
    running = sum(1 for r in recs if r.get("status") == "running")
    heals = sum(int(r.get("heal_round") or 0) for r in recs)
    worked = sum((r.get("wall_s") or 0) for r in (run or {}).get("rows", []))
    to_go = mv.steps_to_decision(state, flow)
    if to_go is None:
        fourth = ("sent back for rework", str(heals))
    elif to_go <= 0:
        fourth = ("your decision", "recorded")
    elif to_go == 1:
        fourth = ("your decision", "next")
    else:
        fourth = ("steps to your decision", str(to_go))
    tiles = [
        ("step", f"{min(settled + running, total)} of {total}"),
        # NOT "worked for": this sums every node's time, so on a flow that fans
        # out it is legitimately larger than the clock on the wall, and a reader
        # comparing it with the headline's elapsed would have to work out why.
        # "node time" is the phrase `cost_report.compact_block` already puts in
        # front of the same person.
        ("node time", mv.format_duration(worked) or "—"),
        ("agent tasks", str(meter["used"])),
        fourth,
    ]
    return '<div class="stats">' + "".join(
        f'<div class="tile"><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>'
        for k, v in tiles
    ) + "</div>"


def _meter_card(meter: dict) -> str:
    if meter["cap"] is None:
        # No cap declared: the count, no denominator, no meter — as plan_card does.
        return (f'<div class="card"><div class="meter-head"><span class="lab">'
                f'{e(meter["label"])}</span></div><div class="meter-foot">'
                f'This flow declares no ceiling, so there is no number to be under.'
                f'</div></div>')
    over = " over" if meter["over"] else ""
    return (
        '<div class="card"><div class="meter-head">'
        '<span class="lab">agent tasks used</span>'
        f'<span class="num">{e(meter["label"].replace("agent tasks used ", ""))}</span></div>'
        f'<div class="track"><div class="fill{over}" style="width:{meter["pct"]:.1f}%"></div>'
        '<div class="ceil"></div></div>'
        '<div class="meter-foot">The ceiling is the number this flow declared — the one '
        'you agreed to before anything started.</div></div>'
    )


def spend_lines(run_dir: Path, repo_root: Path, runs_root: Path,
                usage: dict | None = None, drop: str | None = None) -> list[str]:
    """The qualifiers a bar cannot say: rework rounds, node time, tokens in/out,
    unmapped harnesses, and this session's own transcript spend.

    `usage` is the caller's already-computed `collect_run`. `drop` removes a
    line the caller has already drawn — `compact_block`'s first line is exactly
    `spend_meter`'s label, and printing the same sentence twice, adjacent, is
    the kind of thing a reader assumes must mean two different numbers.
    """
    try:
        import cost_report
        run = usage if usage is not None else cost_report.collect_run(
            Path(run_dir), cost_report.load_field_maps(None))
        cap = cost_report._budget_cap(Path(run_dir))
        text, _ = cost_report.compact_block([run], [cap])
        lines = [ln for ln in text.splitlines() if ln != drop]
    except Exception as exc:  # noqa: BLE001 - display-only, always
        return [f"(spend unavailable: {exc})"]
    try:
        import session_spend
        lines += session_spend.session_lines(Path(repo_root), Path(runs_root))
    except Exception as exc:  # noqa: BLE001
        lines += [f"(session spend unavailable: {exc})"]
    return lines


def _decision_card(run_dir: Path) -> str:
    """The evidence, verbatim, or the question card, verbatim — never a
    narration of either, and never both at once. Which kind of attention is
    wanted is named: a question, or a decision."""
    card = mv.question_card(run_dir)
    ev = mv.evidence_status(run_dir)
    if ev is not None and ev.get("text"):
        stale = ""
        if ev.get("stale"):
            stale = ('<p class="stale-note">This was written before the step that '
                     'produces it last ran — it may describe an earlier attempt.</p>')
        return (
            '<div class="card decide"><h2>⊗ needs you — a decision</h2>'
            f'{stale}<div class="evidence">{e(ev["text"])}</div>'
            f'<p class="terminal-note" id="terminal-note">{e(TERMINAL_SENTENCE)}</p>'
            f'<p class="key-echo" id="key-echo" role="status" hidden>{e(TERMINAL_SENTENCE)}</p>'
            "</div>"
        )
    if card:
        return (
            '<div class="card ask"><h2>◐ needs you — a question</h2>'
            f'<div class="evidence">{e(card)}</div>'
            f'<p class="terminal-note" id="terminal-note">{e(TERMINAL_SENTENCE)}</p>'
            f'<p class="key-echo" id="key-echo" role="status" hidden>{e(TERMINAL_SENTENCE)}</p>'
            "</div>"
        )
    return ""


def _cost_card(run_dir: Path, stack: list[dict], usage: dict | None = None) -> str:
    out = ['<div class="card"><h2>what it has cost</h2>']
    if stack:
        out.append('<div class="stack">')
        for s in stack:
            out.append(f'<i style="background:var(--c{s["slot"]});width:{s["pct"]:.2f}%" '
                       f'title="{e(s["name"])} {s["value"]:,}"></i>')
        out.append('</div><div class="legend">')
        for s in stack:
            out.append(f'<span><i class="sw" style="background:var(--c{s["slot"]})"></i>'
                       f'{e(s["name"])} {s["value"]:,}</span>')
        out.append("</div>")
    else:
        out.append('<p class="meter-foot">No usage was reported for this run.</p>')
    for mode, title in (("history", "per step, every attempt counted"),
                        ("head", "per step, kept attempts only")):
        body = "\n".join(mv.cost_lines(run_dir, mode=mode, usage=usage))
        out.append(f'<details id="cost-{mode}"><summary>{e(title)}</summary>'
                   f"<pre>{e(body)}</pre></details>")
    out.append("</div>")
    return "\n".join(out)


def _focus_node(state: dict) -> str | None:
    """Which step L3 opens on: whatever is happening now, then whatever wants
    the human, then the last thing recorded. Mechanical, not a guess."""
    nodes = state.get("nodes") or {}
    for want in ("running", "blocked", "failed"):
        for node_id, rec in nodes.items():
            if rec.get("status") == want:
                return node_id
    ran = [n for n, r in nodes.items() if r.get("started_at")]
    return ran[-1] if ran else (next(iter(nodes), None))


def _feed_card(run_dir: Path, events: list[dict], labels: dict[str, str],
               focus: str | None, repo_root: Path | None) -> str:
    out = ['<div class="card"><h2>what just happened</h2><div class="feed" id="feed">']
    for ev in events[-FEED_LIMIT:]:
        out.append(f"<div>{e(event_text(ev, labels))}</div>")
    if not events:
        out.append("<div>Nothing has been recorded yet.</div>")
    out.append("</div>")
    out.append('<details id="raw-record"><summary>show the raw record</summary><table>')
    out.append("<tr><th>term</th><th>what it means</th><th>value</th></tr>")
    for item in raw_record(run_dir, focus):
        out.append(f'<tr><td>{e(item["term"])}</td><td>{e(item["gloss"])}</td>'
                   f'<td>{e(item["value"])}</td></tr>')
    out.append("</table></details></div>")
    return "\n".join(out)


def _drawers(run_dir: Path, node_ids: list[str], repo_root: Path | None, *,
             state: dict | None = None, labels: dict[str, str] | None = None) -> str:
    """L2, one per step, reached by clicking a row in either view.

    The link is a fragment, not a fetch: a `<details>` a browser jumps into
    opens itself, so the drawer works with JavaScript off — and `/api/node/<id>`
    stays a route rather than the only way in.
    """
    out = ['<div class="card"><h2>what happened at each step</h2>']
    for node_id in node_ids:
        drawer = node_drawer(run_dir, node_id, repo_root, state=state, labels=labels)
        body = "\n".join(drawer["lines"])
        out.append(f'<details id="step-{e(node_id)}">'
                   f'<summary>{e(drawer["label"])}</summary>'
                   f"<pre>{e(body)}</pre></details>")
    if not node_ids:
        out.append("<p>(nothing to show yet)</p>")
    out.append("</div>")
    return "\n".join(out)


def render_wrap(run_dir: Path | None, repo_root: Path, runs_root: Path,
                now: datetime | None = None) -> tuple[str, int]:
    """The whole `.wrap` fragment, server-rendered, plus the event cursor it
    was rendered at. `GET /` embeds it; the poll swaps it."""
    if run_dir is None:
        return (
            '<div class="top"><span class="brand">MISSION</span>'
            '<span class="runid">no run yet</span></div>'
            '<p class="hero">Nothing is running.</p>'
            '<p class="hero-sub">Tell the assistant what you would like to work on; '
            "this page fills in by itself once something starts.</p>"
            '<p class="foot">This page only reads files. It never changes the run.</p>',
            0,
        )

    state = mv.read_json(run_dir / "state.json") or {}
    flow = mv.read_json(run_dir / "flow.tg.json")
    labels = mv.load_labels(run_dir, repo_root)
    run = _collect(run_dir)
    meter = spend_meter([run] if run else [], [_cap(run_dir)] if run else [])
    chain = chain_chip(run_dir)
    events = _events(run_dir)
    node_ids = list((state.get("nodes") or {}).keys())
    running = any(r.get("status") == "running" for r in (state.get("nodes") or {}).values())

    parts = [
        '<div class="top"><span class="brand">MISSION</span>'
        f'<span class="runid">{e(run_dir.name)}</span><span class="spacer"></span>'
        f'<span class="chip{" live" if running else ""}">'
        f'<span class="dot {"run" if running else "mut"}"></span>'
        f'{"running" if running else "not running"}</span>'
        f'<span class="chip" title="{e(chain["detail"])}">'
        f'<span class="dot {e(chain["cls"])}"></span>{e(chain["text"])}</span></div>',

        # ABOVE the fold, not under 200 KB of content. Server-worded, hidden,
        # unhidden by the client after three consecutive failed polls. The page
        # used to swallow every error and go on pulsing its live dot over frozen
        # data — a silently stale board is worse than a blank one, and the guide
        # promises blank never means broken.
        f'<p class="stale-note" id="offline-note" role="status" hidden>{e(OFFLINE_SENTENCE)}</p>',

        f'<p class="hero">{e(mv.headline(state, flow, now=now))}</p>',
        '<p class="hero-sub">This page only reads files. Decisions are not made here — '
        'when something needs you, it happens in the terminal.</p>',

        _stat_tiles(state, flow, run, meter),
        _meter_card(meter),
        '<div class="card"><h2>spend</h2><pre>'
        + e("\n".join(spend_lines(run_dir, repo_root, runs_root,
                                  usage=run, drop=meter["label"])))
        + "</pre></div>",
        _decision_card(run_dir),

        '<div class="card"><div class="cardhead"><h2>the steps</h2>'
        '<div class="viewswitch">'
        '<button class="btn" data-view="l0" aria-pressed="true">board</button>'
        '<button class="btn" data-view="l1" aria-pressed="false">show every step</button>'
        "</div></div>",
        render_board(run_dir, repo_root),
        render_timeline(waterfall(run_dir, repo_root, now=now)),
        "</div>",

        _cost_card(run_dir, cost_stack(run), usage=run),

        '<div class="card"><h2>ACTIVITY</h2><pre>'
        + e("\n".join(mv.activity_lines(run_dir, repo_root=repo_root)))
        + "</pre></div>",

        _feed_card(run_dir, events, labels, _focus_node(state), repo_root),
        _drawers(run_dir, node_ids, repo_root, state=state, labels=labels),

        '<p class="foot">This page only reads files. It never changes the run.</p>',
    ]
    if mv.needs_you(state):
        parts.insert(3, '<p class="hero-sub" style="color:var(--warning)">'
                        "NEEDS YOU &mdash; read the terminal pane.</p>")
    return "\n".join(p for p in parts if p), len(events)


def render_page(run_dir: Path | None, repo_root: Path, runs_root: Path,
                now: datetime | None = None) -> str:
    body, cursor = render_wrap(run_dir, repo_root, runs_root, now=now)
    name = run_dir.name if run_dir else "no run yet"
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>MISSION - {e(name)}</title>\n"
        f"<style>{stylesheet()}</style>\n"
        f'<body data-run-token="{e(run_token(run_dir))}" data-event-cursor="{cursor}">\n'
        f'<div class="wrap">{body}</div>\n'
        f"<script>{client_js()}</script>\n"
    )


# ----------------------------------------------------------------- routes
#
# Enumerated, and pinned by test. `/api/node/<id>` is the only one with a
# variable segment; node ids already match `^[a-z0-9][a-z0-9-]*$` and
# `node_detail` looks the id up in state.json before touching a path, so the
# allowlist below is for a clean 404 rather than for safety — the traversal
# test stays anyway.

ROUTES = ("/", "/index.html", "/api/state", "/api/events", "/api/node/<id>",
          "/api/evidence", "/api/question")

JSON_CT = "application/json; charset=utf-8"
HTML_CT = "text/html; charset=utf-8"


def _json(payload: dict) -> tuple[int, str, bytes]:
    return 200, JSON_CT, json.dumps(payload, ensure_ascii=False).encode("utf-8")


def handle(path: str, runs_root: Path, pinned: Path | None, repo_root: Path,
           now: datetime | None = None) -> tuple[int, str, bytes]:
    """(status, content type, body) for one GET. Every branch reads and formats.

    No branch writes — asserted two ways in `tests/test_cockpit_ux.py`: the
    handler class has no `do_*` method but `do_GET` (a mechanism), and a harness
    that makes every write API raise drives every route (coverage-bounded, and
    the docstring there says so).
    """
    parsed = urlparse(path)
    route = parsed.path
    run_dir = pinned or mv.newest_run(runs_root)
    token = run_token(run_dir)

    if route in ("/", "/index.html"):
        return 200, HTML_CT, render_page(run_dir, repo_root, runs_root, now=now).encode("utf-8")

    if route == "/api/state":
        body, cursor = render_wrap(run_dir, repo_root, runs_root, now=now)
        return _json({"token": token, "cursor": cursor, "html": body,
                      "run": run_dir.name if run_dir else None})

    if route == "/api/events":
        # THE CHEAP ROUTE, and the page's heartbeat. Only the lines after the
        # cursor are JSON-parsed, so a quiet second costs a file read and
        # nothing else — which is what lets the client stop re-rendering a page
        # that has not changed. `running` rides along because it is the one
        # thing that makes the page change with no journal entry behind it. It is
        # `live`, not `running`: a GLOSSARY word in the client is the second
        # glossary this design forbids, and the test that catches that must
        # stay a substring check with no exceptions in it.
        raw = (parse_qs(parsed.query).get("after") or ["0"])[0]
        if not raw.isdigit():          # rejects "abc", "-1", "1.5", ""
            return 404, HTML_CT, b"bad cursor"
        after = int(raw)
        if run_dir is None:
            return _json({"token": token, "next": 0, "events": [], "live": False})
        state = mv.read_json(run_dir / "state.json") or {}
        running = any(r.get("status") == "running"
                      for r in (state.get("nodes") or {}).values())
        fresh = _events_after(run_dir, after)
        labels = mv.load_labels(run_dir, repo_root) if fresh else {}
        return _json({
            "token": token,
            "next": after + len(fresh),
            "live": running,
            "events": [{"text": event_text(ev, labels), "node": ev.get("node") or "",
                        "status": ev.get("status") or ""} for ev in fresh],
        })

    if route.startswith("/api/node/"):
        node_id = route[len("/api/node/"):]
        if run_dir is None:
            return 404, HTML_CT, b"no run"
        state = mv.read_json(run_dir / "state.json") or {}
        if node_id not in (state.get("nodes") or {}):
            return 404, HTML_CT, b"no such step"
        drawer = node_drawer(run_dir, node_id, repo_root)
        drawer["raw"] = raw_record(run_dir, node_id)
        drawer["token"] = token
        return _json(drawer)

    if route == "/api/evidence":
        if run_dir is None:
            return 404, HTML_CT, b"no run"
        status = mv.evidence_status(run_dir)
        return _json({"token": token, "evidence": status,
                      "rejection": mv.rejection_text(run_dir),
                      "sentence": TERMINAL_SENTENCE})

    if route == "/api/question":
        if run_dir is None:
            return 404, HTML_CT, b"no run"
        return _json({"token": token, "question": mv.question_card(run_dir)})

    return 404, HTML_CT, b"not found"


def make_handler(runs_root: Path, pinned: Path | None, repo_root: Path):
    class Handler(BaseHTTPRequestHandler):
        # GET only. There is deliberately no do_POST, do_PUT or do_DELETE: the
        # absence of the method IS the guarantee. BaseHTTPRequestHandler answers
        # anything else with 501, which is the correct answer.
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            try:
                status, ctype, body = handle(self.path, runs_root, pinned, repo_root)
            except Exception as exc:  # noqa: BLE001 - a view never takes the run down
                status, ctype = 200, HTML_CT
                body = f"<pre>view error: {html.escape(str(exc))}</pre>".encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for header, value in SECURITY_HEADERS:
                self.send_header(header, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass  # a view that chatters into the console is a worse view

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="loopback by default; anything else exposes the run dir")
    ns = ap.parse_args(argv)

    if ns.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {ns.host}, not loopback.", file=sys.stderr)
        print("  This serves the contents of run directories - prompts, diffs and model",
              file=sys.stderr)
        print("  output - to anything that can reach this machine, with no authentication.",
              file=sys.stderr)
        print("  That includes rejection.txt, which is the human's own words.", file=sys.stderr)
        print("  runs/ is gitignored precisely because it is sensitive.", file=sys.stderr)

    handler = make_handler(Path(ns.runs_root),
                           Path(ns.run_dir) if ns.run_dir else None,
                           Path(ns.repo_root))
    server = ThreadingHTTPServer((ns.host, ns.port), handler)
    print(f"MISSION (read-only) on http://{ns.host}:{ns.port}  - Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
