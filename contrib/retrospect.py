#!/usr/bin/env python
"""retrospect.py — what the system's own friction says about the flows.

    python contrib/retrospect.py runs/ [--out report.md] [--cohort <flow>]

Aggregates every run dir under a root, grouped by **(flow, flow_hash) cohort**.
The grouping is the measurement: every applied improvement edits a flow file
and therefore changes `flow_hash`, so before/after cohorts come free and a
change that made things worse is visible in the next report rather than in
someone's memory.

WHAT IT READS (and why not the obvious thing):
- `events.jsonl` — transitions, `heal-round`, timestamps. The per-round truth.
- `phases/<gate>/result.json` + rotated `result-attempt<n>.json` — findings per
  round, including the rounds that were later healed away.
- the run's `flow.tg.json` copy — gate budgets, so "2 rework rounds" can be
  reported against its ceiling.
- `state.json` — flow identity and the latest verdict only. It holds a LOSSY
  latest-verdict string and a counter; it is not sufficient alone.
- `prompt*.txt` — corrective re-spawns are not evented, so they are counted by
  matching the engine's fixed corrective preamble. Current AND rotated: a
  corrective that succeeded is often the last spawn, so its marker sits in
  `prompt.txt` and never rotates.
- `cockpit-journal.jsonl` — what the domain expert was TOLD, for the
  told-vs-state comparison below.

PRIVACY IS A PROJECTION, NOT A PROMISE. Findings quote code and prompts
routinely, so this script never emits a `claim`, `evidence`, `reason`, or heal
text. It emits ids, counts, severities, categories, and numbers. The projection
is applied at extraction, not at printing, so there is no path where a body
reaches the output by accident. pi `--session-dir` transcript subtrees are
skipped outright (ADDENDUM-A A.3.4: transcripts are never node inputs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The engine's fixed corrective preamble (executors are re-spawned output-only
# after a contract validation failure). Matching this is how correctives get
# counted until they are evented.
CORRECTIVE_MARKER = "produced output that failed contract validation"

# Directory names that are transcript subtrees, never run artifacts.
SKIP_DIRS = {"session", "sessions", ".pi", "transcripts"}

WORD = re.compile(r"[a-z0-9]+")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:
                continue     # trailing partial line on an append-only file
    return out


# --- projection ----------------------------------------------------------------

def project_findings(verdict: object) -> list[dict]:
    """Metadata only. This function is the privacy boundary: whatever it does
    not return cannot appear in the report."""
    if not isinstance(verdict, dict):
        return []
    out = []
    for f in verdict.get("findings") or []:
        if not isinstance(f, dict):
            continue
        out.append({
            "severity": str(f.get("severity", "?"))[:20],
            "category": str(f.get("category", "?"))[:40],
            "file": str(f.get("file") or "")[:120],   # a path, never its contents
        })
    return out


# --- per-run extraction --------------------------------------------------------

def collect_run(run_dir: Path) -> dict | None:
    state = _read_json(run_dir / "state.json")
    if not isinstance(state, dict):
        return None
    flow = _read_json(run_dir / "flow.tg.json") or {}
    gate_budget = {
        n.get("id"): (n.get("heal") or {}).get("max_rounds", 0)
        for n in (flow.get("nodes") or []) if isinstance(n, dict)
    }

    roles = {nid: (rec or {}).get("role", "?")
             for nid, rec in (state.get("nodes") or {}).items() if isinstance(rec, dict)}

    events = _read_jsonl(run_dir / "events.jsonl")
    heal_rounds: Counter = Counter()
    blocks: Counter = Counter()
    for e in events:
        node = e.get("node")
        if not node:
            continue
        if e.get("status") == "heal-round":
            heal_rounds[node] += 1
        elif e.get("status") == "blocked" and roles.get(node) == "gate":
            # Only a GATE blocking on its OWN judgment counts. Two kinds of
            # noise have to be excluded or blocks-per-gate — the metric this
            # whole report is judged on — inflates with the width of the graph:
            #   - non-gate descendants marked blocked by the cascade (role check)
            #   - GATES that were themselves cascade-blocked by an upstream
            #     gate, which never ran and never rendered a verdict. The engine
            #     labels those "gate <id> blocked: ..." or "upstream failed".
            err = str(e.get("error") or "")
            if err.startswith("gate ") or err.startswith("upstream "):
                continue
            blocks[node] += 1

    findings: list[dict] = []
    correctives = 0
    phases = run_dir / "phases"
    if phases.is_dir():
        for phase_dir in sorted(p for p in phases.iterdir() if p.is_dir()):
            if phase_dir.name in SKIP_DIRS:
                continue
            for result in sorted(phase_dir.glob("result*.json")):
                for f in project_findings(_read_json(result)):
                    findings.append({**f, "gate": phase_dir.name})
            # Correctives: current AND rotated prompts, marker match only. The
            # prompt body is never read into the report.
            for prompt in list(phase_dir.glob("prompt*.txt")) + \
                          list(phase_dir.glob("items/*/prompt*.txt")):
                try:
                    if CORRECTIVE_MARKER in prompt.read_text(encoding="utf-8", errors="replace"):
                        correctives += 1
                except OSError:
                    continue

    statuses = Counter()
    for rec in (state.get("nodes") or {}).values():
        if isinstance(rec, dict):
            statuses[rec.get("status", "?")] += 1

    return {
        "run_dir": str(run_dir),
        "flow": state.get("flow_name", "?"),
        "flow_hash": str(state.get("flow_hash", "?"))[:12],
        "spawns": state.get("token_spawns", 0),
        "heal_rounds": dict(heal_rounds),
        "blocks": dict(blocks),
        "gate_budget": gate_budget,
        "findings": findings,
        "correctives": correctives,
        "statuses": dict(statuses),
        "journal": _read_jsonl(run_dir / "cockpit-journal.jsonl"),
        # T1.2: the human's OWN words about why they rejected. A third artifact
        # class beside the journal (what the orchestrator said) and state.json
        # (what the engine did), and the only one written by the person whose
        # decision it was.
        "rejection": _read_text(run_dir / "rejection.txt"),
    }


# --- told-vs-state (M2) --------------------------------------------------------

def told_vs_state(run: dict) -> list[str]:
    """Audit the narrated channel against the mechanical one. The journal is
    evidence of what was SAID; state.json and the gate results are what was
    true. Both sides are fenced run-dir artifacts — pi transcripts stay out of
    this entirely."""
    drift: list[str] = []
    spend = run["spawns"]
    for entry in run["journal"]:
        kind = entry.get("kind")
        if kind == "consent":
            cap = entry.get("cap")
            if isinstance(cap, int) and spend > cap:
                drift.append(
                    f"consent cap {cap} stated, {spend} agent tasks actually spent"
                )
        elif kind == "handoff":
            # A handoff claims the run was quiescent-except-approval. If the
            # state shows work still pending, the claim was wrong.
            if run["statuses"].get("pending", 0) > 0 and entry.get("quiescent") is True:
                drift.append(
                    f"handoff for {entry.get('node', '?')} claimed quiescent, "
                    f"{run['statuses']['pending']} node(s) still pending"
                )
        elif kind == "stop":
            claimed = entry.get("spend")
            if isinstance(claimed, int) and claimed != spend:
                drift.append(f"stop reported spend {claimed}, state says {spend}")

    # T1.2, the symmetric check. The evidence rule exists because a narrated
    # summary at a decision point cannot be trusted; a rejection reason travels
    # the SAME road in the other direction. If the human wrote down why they
    # sent the work back and the journal never mentions it, the orchestrator
    # relayed something other than what they said — or nothing at all.
    if run.get("rejection"):
        said = " ".join(entry.get("note", "") + " " + entry.get("reason", "")
                        for entry in run["journal"] if isinstance(entry, dict))
        theirs = set(WORD.findall(_rejection_reason(run["rejection"]).lower()))
        mine = set(WORD.findall(said.lower()))
        if not mine:
            drift.append("the human recorded a rejection reason; the journal never mentions it")
        elif theirs and len(theirs & mine) / max(1, len(theirs)) < 0.25:
            drift.append(
                "the human's rejection reason and the journal have little in common "
                "— worth a human read"
            )
    return drift


def _rejection_reason(text: str) -> str:
    """The one line the human typed, out of the framing around it."""
    body = []
    for line in text.splitlines():
        line = line.strip()
        if not line or set(line) <= {"="} or line.startswith("WHY THIS WAS")                 or line.startswith("recorded "):
            continue
        body.append(line)
    return " ".join(body)


def fidelity_tripwire(run: dict, threshold: float = 0.25) -> list[str]:
    """A heuristic, deliberately: token overlap between the gate's question and
    the relay the human heard, and between their answer and the steer that was
    sent. It surfaces candidates for a human to read. It is NOT a metric with a
    target and it never auto-judges anything."""
    out = []
    for entry in run["journal"]:
        if entry.get("kind") != "clarify":
            continue
        pairs = (("finding", "relay"), ("answer", "steer"))
        for a, b in pairs:
            ta, tb = set(WORD.findall(str(entry.get(a, "")).lower())), \
                     set(WORD.findall(str(entry.get(b, "")).lower()))
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
            if overlap < threshold:
                out.append(
                    f"{a}->{b} overlap {overlap:.0%} on node "
                    f"{entry.get('target', '?')} — worth a human read"
                )
    return out


# --- cohorts and rendering -----------------------------------------------------

def build_cohorts(runs: list[dict]) -> dict:
    cohorts = defaultdict(list)
    for r in runs:
        cohorts[(r["flow"], r["flow_hash"])].append(r)
    return cohorts


def render(cohorts: dict) -> str:
    out = ["# lockstep friction report", "",
           "Grouped by (flow, flow_hash). A flow edit changes the hash, so the",
           "cohort boundary IS the before/after boundary for any improvement.",
           "Bodies (claims, evidence, reasons, heal text) are stripped by",
           "projection — this report is metadata only.", ""]

    ordered = sorted(cohorts.items(), key=lambda kv: (kv[0][0], -len(kv[1])))
    for (flow, fhash), runs in ordered:
        total_heals = sum(sum(r["heal_rounds"].values()) for r in runs)
        total_blocks = sum(sum(r["blocks"].values()) for r in runs)
        total_correctives = sum(r["correctives"] for r in runs)
        spawns = sum(r["spawns"] for r in runs)
        n = len(runs)

        out.append(f"## {flow} @ {fhash}  ({n} run{'s' if n != 1 else ''})")
        out.append("")
        out.append(f"- agent tasks: {spawns} total, {spawns / n:.1f} per run")
        out.append(f"- rework rounds: {total_heals} ({total_heals / n:.1f} per run)")
        out.append(f"- gate blocks: {total_blocks} ({total_blocks / n:.1f} per run)")
        out.append(f"- corrective re-spawns: {total_correctives} "
                   f"(contract mismatches — a prompt-craft signal, not a model failure)")
        out.append("")

        per_gate: Counter = Counter()
        for r in runs:
            for gate, count in r["blocks"].items():
                per_gate[gate] += count
        if per_gate:
            out.append("| gate | blocks | rework rounds | budget |")
            out.append("|---|---|---|---|")
            for gate, count in per_gate.most_common():
                heals = sum(r["heal_rounds"].get(gate, 0) for r in runs)
                budget = next((r["gate_budget"].get(gate) for r in runs
                               if r["gate_budget"].get(gate)), "-")
                out.append(f"| {gate} | {count} | {heals} | {budget} |")
            out.append("")

        cats = Counter((f["category"], f["severity"]) for r in runs for f in r["findings"])
        if cats:
            out.append("| finding category | severity | count |")
            out.append("|---|---|---|")
            for (cat, sev), count in cats.most_common(15):
                out.append(f"| {cat} | {sev} | {count} |")
            out.append("")

        drift = [d for r in runs for d in told_vs_state(r)]
        trip = [t for r in runs for t in fidelity_tripwire(r)]
        if drift:
            out.append("**told-vs-state drift** (what the expert was told vs what the "
                       "run recorded — a first-class finding, not a footnote):")
            out += [f"- {d}" for d in drift]
            out.append("")
        if trip:
            out.append("**translation tripwire** (low overlap between question and relay, "
                       "or answer and steer — candidates for a human read, never auto-judged):")
            out += [f"- {t}" for t in trip]
            out.append("")

    out.append("## trend")
    out.append("")
    by_flow = defaultdict(list)
    for (flow, fhash), runs in cohorts.items():
        heals = sum(sum(r["heal_rounds"].values()) for r in runs) / max(1, len(runs))
        blocks = sum(sum(r["blocks"].values()) for r in runs) / max(1, len(runs))
        # Sort key: the earliest run dir in the cohort. Run dirs are
        # `<flow>-<ISO timestamp>`, so this orders cohorts by when they FIRST
        # ran. Relying on dict insertion order happened to agree most of the
        # time, which is the worst property a before/after comparison can have —
        # "IMPROVED" and "REGRESSED" are read as claims about direction, and a
        # direction derived from incidental ordering is a coin flip wearing a
        # verdict's clothes.
        first_seen = min(r["run_dir"] for r in runs)
        by_flow[flow].append((first_seen, fhash, len(runs), heals, blocks))
    for flow, unsorted_entries in sorted(by_flow.items()):
        entries = [e[1:] for e in sorted(unsorted_entries)]
        if len(entries) < 2:
            out.append(f"- {flow}: one cohort only — no before/after yet "
                       f"(a second cohort appears the first time the flow is edited)")
            continue
        out.append(f"- {flow}: {len(entries)} cohorts")
        for fhash, n, heals, blocks in entries:
            out.append(f"  - {fhash} ({n} runs): {heals:.1f} rework/run, {blocks:.1f} blocks/run")
        first, last = entries[0], entries[-1]
        delta = last[2] - first[2]
        verdict = ("IMPROVED" if delta < -0.01 else "REGRESSED" if delta > 0.01 else "flat")
        out.append(f"  - rework per run {first[2]:.1f} -> {last[2]:.1f}: **{verdict}**"
                   + ("  <- a regression is a finding, not noise" if verdict == "REGRESSED" else ""))
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs_root", nargs="?", default="runs")
    ap.add_argument("--out", default=None, help="write here instead of stdout (stays under runs/)")
    ap.add_argument("--cohort", default=None, help="only this flow name")
    ns = ap.parse_args(argv)

    root = Path(ns.runs_root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    runs = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS or d.name == "lineages":
            continue
        r = collect_run(d)
        if r and (not ns.cohort or r["flow"] == ns.cohort):
            runs.append(r)
    if not runs:
        print(f"no run dirs under {root}", file=sys.stderr)
        return 2

    text = render(build_cohorts(runs))
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")
        print(f"wrote {ns.out} ({len(runs)} runs)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
