#!/usr/bin/env python
"""Render the three stopped states side by side, for the §7 look.

Work-order §7: Batch 0's look includes all three stopped states — failed,
dead-driver, refused — because the whole point of F8 is that they must not be
confusable, and confusability is exactly what a screenshot sees and a test
does not. Plus an over-threshold run for Batch 2's degraded drawers.

Throwaway by design: writes `look-*.html` into a directory you pass (default:
a temp dir, printed), builds its run dirs in a temp tree, renders against an
empty repo root (same rule as make-shipped-sample.py — no local paths, no
session data). Not committed output; the committed snapshot stays
`trace-page-shipped.html`.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # mockups -> proposals -> docs -> repo
sys.path.insert(0, str(ROOT / "contrib"))
sys.path.insert(0, str(ROOT / "src"))

import mission_server as ms  # noqa: E402

T0 = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(minutes=50)


def _iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def base_run(root: Path, name: str) -> Path:
    run = root / name
    (run / "phases" / "draft").mkdir(parents=True)
    flow = {"format_version": "1.0", "name": "look", "budget": {"max_agent_spawns": 25},
            "nodes": [{"id": "draft", "kind": "harness"},
                      {"id": "check", "kind": "shell", "depends_on": ["draft"],
                       "spec": {"cmd": ["python", "-c", "pass"]}},
                      {"id": "publish", "kind": "shell", "depends_on": ["check"],
                       "spec": {"cmd": ["python", "-c", "pass"]}}]}
    (run / "flow.tg.json").write_text(json.dumps(flow), encoding="utf-8")
    (run / "flow.labels.json").write_text(json.dumps({"nodes": {
        "draft": "draft the report", "check": "check the numbers",
        "publish": "publish to the drive"}}), encoding="utf-8")
    state = {"flow_name": "look", "started_at": _iso(0), "token_spawns": 3,
             "verdicts": {}, "nodes": {
                 "draft": {"node_id": "draft", "role": "work", "kind": "harness",
                           "status": "done", "attempts": 1, "heal_round": 0,
                           "started_at": _iso(0), "ended_at": _iso(9)},
                 "check": {"node_id": "check", "role": "work", "kind": "shell",
                           "status": "pending", "attempts": 0, "heal_round": 0},
                 "publish": {"node_id": "publish", "role": "work", "kind": "shell",
                             "status": "pending", "attempts": 0, "heal_round": 0}}}
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run / "events.jsonl").write_text(
        json.dumps({"ts": _iso(0), "node": "draft", "status": "running"}) + "\n"
        + json.dumps({"ts": _iso(9), "node": "draft", "status": "done"}) + "\n",
        encoding="utf-8")
    return run


def mutate(run: Path, **node_updates) -> dict:
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    for nid, upd in node_updates.items():
        state["nodes"][nid].update(upd)
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(
        prefix="lockstep-look-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lockstep-look-runs-"))
    empty_repo = tmp / "empty-repo"
    empty_repo.mkdir()
    runs = tmp / "runs"
    runs.mkdir()

    # 1. failed — the blocker card, error verbatim, stalled-behind counts
    failed = base_run(runs, "look-failed-20260911T090000Z")
    mutate(failed, check={"status": "failed", "attempts": 1,
                          "error": "exit code 128 (no result emitted)",
                          "started_at": _iso(9), "ended_at": _iso(12)})

    # 2. dead driver — "stopped unexpectedly", the lock's words, frozen clock
    stale = base_run(runs, "look-stale-20260911T091000Z")
    mutate(stale, check={"status": "running", "attempts": 1, "started_at": _iso(9)})
    (stale / "lock").write_text(json.dumps(
        {"pid": 4999999, "hostname": socket.gethostname(),
         "started": _iso(0)}), encoding="utf-8")

    # 3. refused — terminal record, no restart card
    refused = base_run(runs, "look-refused-20260911T092000Z")
    state = json.loads((refused / "state.json").read_text(encoding="utf-8"))
    for rec in state["nodes"].values():
        rec.update({"status": "pending", "attempts": 0})
        rec.pop("started_at", None)
        rec.pop("ended_at", None)
    state["terminal"] = {"reason": "dirty_tree",
                         "message": "the tree carries uncommitted changes"}
    (refused / "state.json").write_text(json.dumps(state), encoding="utf-8")

    # 4. over-threshold — degraded settled drawers, loud one kept
    big = runs / "look-big-20260911T093000Z"
    (big / "phases").mkdir(parents=True)
    n = ms.DRAWER_INLINE_MAX + 8
    nodes = {f"s{i:02d}": {"node_id": f"s{i:02d}", "role": "work", "kind": "harness",
                           "status": "done", "attempts": 1, "heal_round": 0,
                           "started_at": _iso(i * 0.5), "ended_at": _iso(i * 0.5 + 0.4)}
             for i in range(n)}
    nodes["broke"] = {"node_id": "broke", "role": "work", "kind": "harness",
                      "status": "failed", "attempts": 1, "heal_round": 0,
                      "error": "provider said 429", "started_at": _iso(20),
                      "ended_at": _iso(21)}
    (big / "state.json").write_text(json.dumps(
        {"flow_name": "look-big", "started_at": _iso(0), "token_spawns": n,
         "verdicts": {}, "nodes": nodes}), encoding="utf-8")
    (big / "events.jsonl").write_text("", encoding="utf-8")

    for run, name in ((failed, "look-failed"), (stale, "look-stale"),
                      (refused, "look-refused"), (big, "look-big")):
        html = ms.render_page(run, empty_repo, runs, now=NOW)
        local = str(ROOT).replace("\\", "/")
        assert local.lower() not in html.lower(), "local path leaked into sample"
        (out_dir / f"{name}.html").write_text(html, encoding="utf-8")
        print(f"wrote {out_dir / (name + '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
