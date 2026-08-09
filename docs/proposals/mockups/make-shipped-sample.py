"""Render docs/proposals/mockups/trace-page-shipped.html from the REAL renderer.

Not a mockup: a snapshot of what `contrib/mission_server.py` actually emits,
over a synthetic run dir built to exercise every state the page can draw. Run
from the repo root:

    .venv\\Scripts\\python.exe .make_sample.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "contrib"))
sys.path.insert(0, str(ROOT / "src"))

import mission_server as ms  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
T0 = NOW - timedelta(minutes=18)


def iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def envelope(inp: int, out: int, cr: int, cw: int, cost: float) -> str:
    return json.dumps({
        "type": "result", "is_error": False, "total_cost_usd": cost,
        "model": "claude-opus-5",
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw},
        "result": "done",
    })


# id, label, kind, status, heal_round, spans, tokens
NODES = [
    ("plan",     "plan the brief",        "harness", "done",    0, [(0, 2.5)],            (38200, 4100, 12000, 2200, 0.31)),
    ("draft",    "draft the sections",    "harness", "done",    1, [(2.5, 6.2), (7.7, 8.9)], (91400, 11800, 41000, 5400, 0.94)),
    ("numbers",  "check the numbers",     "harness", "done",    0, [(6.2, 7.7), (8.9, 9.4)], (27900, 3300, 9000, 1200, 0.22)),
    ("figures",  "fix the figures",       "harness", "done",    0, [(9.4, 9.6)],          (12050, 1900, 4000, 800, 0.11)),
    ("evidence", "write the evidence",    "shell",   "done",    0, [(9.6, 10.1)],         None),
    ("render",   "render the appendix",   "harness", "done",    0, [(10.1, 11.4)],        (8600, 1400, 3000, 600, 0.08)),
    ("approve",  "approve the brief",     "",        "blocked", 0, [(11.4, None)],        None),
    ("publish",  "publish to the drive",  "shell",   "pending", 0, [],                    None),
    ("notify",   "notify the team",       "shell",   "skipped", 0, [],                    None),
]


def build(run: Path) -> None:
    (run / "phases").mkdir(parents=True)
    nodes, flow_nodes, events = {}, [], []
    prev = None
    for nid, _label, kind, status, heal, spans, tok in NODES:
        phase = run / "phases" / nid
        phase.mkdir()
        role = "approval" if nid == "approve" else "work"
        rec = {
            "node_id": nid, "role": role, "kind": kind, "status": status,
            "attempts": 0 if status in ("pending", "skipped") else len(spans) or 1,
            "heal_round": heal,
            "hash_parts": {"prompt.task": "aa11", "config": "bb22"},
            "invalidated_by": ["prompt.task"] if heal else None,
        }
        if spans:
            rec["started_at"] = iso(spans[0][0])
            if spans[-1][1] is not None:
                rec["ended_at"] = iso(spans[-1][1])
        nodes[nid] = rec
        node = {"id": nid, "kind": kind or "shell"}
        if role == "approval":
            node["role"] = "approval"
        if nid == "numbers":
            node["role"] = "gate"
            node["heal"] = {"max_rounds": 2, "targets": ["draft"], "rollback": True}
        if prev:
            node["depends_on"] = [prev]
        if kind == "shell":
            cmd = "render_evidence" if nid == "evidence" else nid
            node["spec"] = {"cmd": ["python", f"contrib/{cmd}.py"]}
        flow_nodes.append(node)
        prev = nid
        for a, b in spans:
            events.append({"ts": iso(a), "node": nid, "status": "running"})
            if b is not None:
                events.append({"ts": iso(b), "node": nid,
                               "status": "blocked" if nid == "approve" else "done"})
        if heal:
            events.append({"ts": iso(spans[0][1] + 0.1), "node": "numbers", "status": "heal-round"})
        if tok:
            (phase / "stdout.log").write_text(envelope(*tok), encoding="utf-8")
            (phase / "argv.json").write_text(
                json.dumps(["claude", "-p", "...", "--output-format", "json"]),
                encoding="utf-8")
        (phase / "result.txt").write_text("ok\n", encoding="utf-8")

    events.sort(key=lambda e: e["ts"])
    (run / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "flow_name": "quarterly-brief", "started_at": iso(0), "token_spawns": 9,
        "verdicts": {"numbers": "pass"}, "nodes": nodes,
    }, indent=1), encoding="utf-8")
    (run / "flow.tg.json").write_text(json.dumps({
        "format_version": "1.0", "name": "quarterly-brief",
        "budget": {"max_agent_spawns": 25},
        "nodes": flow_nodes,
    }), encoding="utf-8")
    (run / "flow.labels.json").write_text(json.dumps({
        "nodes": {nid: label for nid, label, *_ in NODES}}), encoding="utf-8")
    (run / "phases" / "draft" / "mission.txt").write_text(
        "two figures disagreed with the source; sent back once\n", encoding="utf-8")
    (run / "approval-evidence.txt").write_text(
        "Approve: publish the quarterly brief\n\n"
        "What this changes if you approve\n"
        "  3 files written under Deliverables/2026-Q2/\n"
        "  1 file replaced: Deliverables/2026-Q2/brief.md (was 4.1 KB, now 7.8 KB)\n"
        "  Nothing outside Deliverables/ is touched.\n\n"
        "--impact      3 files, +214 / -38 lines, one directory\n"
        "--reversible  yes - the previous brief is kept in the run folder\n",
        encoding="utf-8")
    import os
    stamp = (T0 + timedelta(minutes=10, seconds=6)).timestamp()
    os.utime(run / "approval-evidence.txt", (stamp, stamp))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lockstep-sample-"))
    try:
        runs = tmp / "runs"
        run = runs / "2026-08-08-quarterly-brief-a41c"
        build(run)
        # An empty repo root: `session_spend` reads THIS session's orchestrator
        # transcript, and a committed sample must not carry one.
        html = ms.render_page(run, tmp / "repo", runs, now=NOW)
        dest = ROOT / "docs" / "proposals" / "mockups" / "trace-page-shipped.html"
        dest.write_text(html, encoding="utf-8")
        print(f"wrote {dest}  ({len(html):,} bytes)")
        for probe in ("Ali", "Sucipto", "AppData", "D:\\\\Shared", str(tmp)):
            assert probe not in html, f"sample leaks {probe!r}"
        print("no local paths or session data in the sample")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
