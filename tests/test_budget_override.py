"""G3a (upstream-response-ow07-feedback, 0.11.0): `resume --max-agent-spawns`.

The OW-07 shape: a lineage stalls at `budget.max_agent_spawns`, and the only
way to raise the cap was editing the flow — new flow_hash, new lineage, then
`--seed` back. Three commands and a new run dir for one operator decision.
The override is this-drive-only (the flow's ceiling stays the consent
artifact; each override is an explicit act), journaled (the ceiling is part
of the consent story, so an override must leave an artifact, not a memory),
and lowering is allowed — "stop spending" is a coherent intent.
"""

from __future__ import annotations

import json

from lockstep.cli import main
from lockstep.state import load_state, read_events


def _chain_flow(git_repo, n=4, cap=1):
    nodes = []
    for i in range(n):
        node = {"id": f"n{i}", "kind": "fake", "spec": {"outputs": [f"O{i}"]}}
        if i:
            node["depends_on"] = [f"n{i-1}"]
        nodes.append(node)
    nodes[-1]["final"] = True
    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(json.dumps({
        "name": "capped", "budget": {"max_agent_spawns": cap}, "nodes": nodes,
    }), encoding="utf-8")
    return flow_path


def test_resume_override_raises_the_cap_for_one_drive(tmp_path, git_repo, monkeypatch, capsys):
    flow_path = _chain_flow(git_repo, n=3, cap=1)
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 4  # budget stop
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    assert load_state(run_dir).token_spawns == 1
    capsys.readouterr()
    assert main(["resume", str(run_dir), "--max-agent-spawns", "5"]) == 0
    out = capsys.readouterr().out
    assert "1 -> 5" in out and "this drive only" in out
    st = load_state(run_dir)
    assert st.token_spawns == 3
    assert all(r.status == "done" for r in st.nodes.values())
    # The artifact: a journaled budget event, not a memory.
    evs = [e for e in read_events(run_dir) if e.get("kind") == "budget"]
    assert evs and evs[-1]["op"] == "override"
    assert evs[-1]["from"] == 1 and evs[-1]["to"] == 5
    # `status` echoes the audit trail.
    assert main(["status", str(run_dir)]) == 0
    assert "max agent spawns" in capsys.readouterr().out


def test_lowering_below_spent_warns_and_stops_at_budget(tmp_path, git_repo, monkeypatch, capsys):
    flow_path = _chain_flow(git_repo, n=4, cap=1)
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 4
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    capsys.readouterr()
    assert main(["resume", str(run_dir), "--max-agent-spawns", "3"]) == 4  # 2 more, stops
    assert load_state(run_dir).token_spawns == 3
    capsys.readouterr()
    # Below already-spent: allowed — "stop spending" is coherent — but warned,
    # and the engine stops at the budget check as it always does.
    assert main(["resume", str(run_dir), "--max-agent-spawns", "2"]) == 4
    out = capsys.readouterr().out
    assert "below" in out and "already spent" in out
    assert load_state(run_dir).token_spawns == 3  # nothing new billed


def test_the_flow_ceiling_returns_on_the_next_plain_resume(tmp_path, git_repo, monkeypatch, capsys):
    # This-drive-only: the flow's ceiling is the consent artifact, and a plain
    # resume must be governed by it, not by a sticky override nobody re-stated.
    flow_path = _chain_flow(git_repo, n=3, cap=1)
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 4
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    assert main(["resume", str(run_dir), "--max-agent-spawns", "2"]) == 4  # one more, stops
    assert load_state(run_dir).token_spawns == 2
    capsys.readouterr()
    assert main(["resume", str(run_dir)]) == 4  # plain: flow cap (1) governs, already exceeded
    assert load_state(run_dir).token_spawns == 2
