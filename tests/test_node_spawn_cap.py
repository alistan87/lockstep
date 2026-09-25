"""G3b first slice (OPEN-WORK item 10): `budget.max_spawns_per_node`.

The run-wide wallet (`max_agent_spawns`) is one pool, so a few failing nodes
or map items can drain it before mandatory downstream work gets a turn — a
downstream consumer measured 14 of 28 spawns gone with at least 30 still
required, and two oversized items that timed out twice under an explicit
`retry.max: 0` because the M4 auto-retry is additive.

What these tests hold down:
- the cap counts EVERY token-costing spawn of one node (or one map item):
  initial, retry, M4 auto-retry, contract corrective, scope corrective, heal
  round, and resume — persisted, so a new drive of the same lineage cannot
  reset it;
- a cap trip fails THAT node with the reason, spends nothing, and leaves the
  run-wide wallet for everyone else (it is not a run-level stop, exit 4);
- a spawn that costs nothing (shell, a served item) is never counted;
- absent key = byte-identical behaviour to before.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lockstep import EXIT_NODE_FAILED, EXIT_OK
from lockstep.state import ItemRecord, PhaseRecord, load_state
from lockstep.taskgraph import TaskGraph

from conftest import build, calls_of, rebuild

VALID = {"findings": [], "verdict": "pass", "reason": "ok"}
BLOCK = {"findings": [], "verdict": "block", "reason": "no"}


def events(run_dir, kind=None):
    out = []
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if kind is None or ev.get("kind") == kind:
            out.append(ev)
    return out


def flow(*nodes, cap=None, wallet=40, name="capped"):
    budget = {"max_agent_spawns": wallet}
    if cap is not None:
        budget["max_spawns_per_node"] = cap
    return {"name": name, "budget": budget, "nodes": list(nodes)}


class TestSchema:
    def test_absent_means_uncapped(self):
        tg = TaskGraph.model_validate({"name": "x", "nodes": [
            {"id": "a", "kind": "fake", "final": True}]})
        assert tg.budget.max_spawns_per_node is None

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_cap_below_one_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            TaskGraph.model_validate({"name": "x", "budget": {"max_spawns_per_node": bad},
                                      "nodes": [{"id": "a", "kind": "fake", "final": True}]})

    def test_old_records_load_with_zero_spawns(self):
        """Additive fields: a state.json written before the counter existed
        loads, and counts from zero on this driver onward."""
        rec = PhaseRecord.model_validate({"node_id": "a", "role": "work", "kind": "fake"})
        assert rec.token_spawns == 0
        assert ItemRecord.model_validate({}).token_spawns == 0


class TestCauses:
    def test_the_auto_retry_is_capped_and_the_wallet_survives(self, tmp_path, git_repo):
        """Acceptance 1: retry.max 0 plus an empty result would take the M4
        auto-retry; a cap of 1 stops it, the node says why, and an unrelated
        node still runs on the run-wide wallet."""
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 0}, "spec": {"task": "t", "empty_result": True}},
            {"id": "u", "role": "work", "kind": "fake", "final": True,
             "spec": {"task": "u", "outputs": ["ok"]}},
            cap=1, wallet=3,
        ), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert len(calls_of(h, "w")) == 1
        st = load_state(h.run_dir)
        assert st.nodes["w"].status == "failed"
        assert "max_spawns_per_node" in st.nodes["w"].error
        # The attempt that DID run is still named, so the reader learns both
        # why it failed and why it was not tried again.
        assert "no result emitted" in st.nodes["w"].error
        assert st.nodes["u"].status == "done"
        assert st.token_spawns == 2
        assert st.nodes["w"].token_spawns == 1
        trips = [e for e in events(h.run_dir, "budget") if e.get("op") == "node-cap"]
        assert trips == [{**trips[0], "node": "w", "spawns": 1, "cap": 1}]

    def test_without_the_key_the_auto_retry_still_happens(self, tmp_path, git_repo):
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 0}, "spec": {"task": "t", "empty_result": True}},
        ), git_repo)
        h.engine.run()
        assert len(calls_of(h, "w")) == 2
        assert not [e for e in events(h.run_dir, "budget") if e.get("op") == "node-cap"]

    def test_ordinary_retries_are_capped(self, tmp_path, git_repo):
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 5, "backoff_ms": 1},
             "spec": {"task": "t", "exit_code": 1, "outputs": ["x"]}},
            cap=2,
        ), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert len(calls_of(h, "w")) == 2
        err = load_state(h.run_dir).nodes["w"].error
        assert "max_spawns_per_node" in err and "exit code 1" in err

    def test_a_contract_corrective_is_capped(self, tmp_path, git_repo):
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "output": "json",
             "contract": "Verdict", "final": True,
             "spec": {"task": "t", "outputs": ['{"nope": 1}', VALID]}},
            cap=1,
        ), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert [c.corrective for c in calls_of(h, "w")] == [False]
        err = load_state(h.run_dir).nodes["w"].error
        assert "contract validation failed" in err and "max_spawns_per_node" in err

    def test_a_scope_corrective_is_capped(self, tmp_path, git_repo):
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "spec": {"task": "t", "outputs": ["ok"], "writes": ["in/**"],
                      "write_files": {"out/x.txt": "stray"}}},
            cap=1,
        ), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert len(calls_of(h, "w")) == 1
        err = load_state(h.run_dir).nodes["w"].error
        # The quarantine still happened and is still reported; the cap only
        # withholds the corrective.
        assert "write scope violated" in err and "max_spawns_per_node" in err
        assert not (git_repo / "out" / "x.txt").exists()

    def test_a_heal_round_is_capped(self, tmp_path, git_repo):
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake",
             "spec": {"task": "t", "outputs": ["v1", "v2"]}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
             "output": "json", "contract": "Verdict", "final": True,
             "heal": {"max_rounds": 2, "targets": ["w"]},
             "spec": {"task": "check", "costs_tokens": False, "outputs": [BLOCK, VALID]}},
            cap=1,
        ), git_repo)
        h.engine.run()
        assert len(calls_of(h, "w")) == 1
        st = load_state(h.run_dir)
        assert st.nodes["w"].status == "failed"
        assert "max_spawns_per_node" in st.nodes["w"].error
        # The refused heal attempt consumes its signal like a spawned one:
        # a one-shot left in state would label whatever ran next.
        assert "w" not in st.heal_pending

    def test_a_timed_out_gate_under_a_spent_cap_names_the_cap(
            self, tmp_path, git_repo, monkeypatch):
        """Review F2: the gate timeout branch advised "add `retry`" — advice a
        spent cap makes useless — and dropped the cap from the reason, so
        `status` had no `spawn cap:` line for it."""
        from lockstep.executors.fake import FakeExecutor
        from lockstep.protocols import RawResult

        from lockstep.executors.fake import FakeCall

        def timing_out(self, work, phase_dir, timeout_s):
            self.calls.append(FakeCall(node_id=work.meta["node_id"], prompt="",
                                       readonly=False, corrective=False))
            return RawResult(exit_code=-1, result_text=None, source="none",
                             timed_out=True, error="timeout")

        monkeypatch.setattr(FakeExecutor, "execute", timing_out)
        h = build(tmp_path, flow(
            {"id": "g", "role": "gate", "kind": "fake", "output": "json",
             "contract": "Verdict", "final": True, "retry": {"max": 0},
             "spec": {"task": "check"}},
            cap=1,
        ), git_repo)
        h.engine.run()
        assert len(calls_of(h, "g")) == 1
        err = load_state(h.run_dir).nodes["g"].error
        assert "spawn cap reached: g has spent" in err
        assert "add `retry`" not in err

    def test_a_capped_baseline_gate_says_so_and_records_nothing(self, tmp_path, git_repo):
        """A baseline spawn refused by the cap is not a broken gate body: it
        must not fall into the fail-open path that records an EMPTY baseline
        and blames the body."""
        h = build(tmp_path, flow(
            {"id": "g", "role": "gate", "kind": "fake", "output": "json",
             "contract": "Verdict", "final": True,
             "spec": {"task": "check", "baseline": True, "outputs": [VALID]}},
            cap=1,
        ), git_repo)
        h.state.nodes["g"].token_spawns = 1  # spent by an earlier drive
        h.engine.run()
        assert calls_of(h, "g") == []
        st = load_state(h.run_dir)
        assert "g" not in st.baseline_findings
        assert not any("body failed" in line for line in h.logs)
        assert any("max_spawns_per_node" in line and "baseline" in line for line in h.logs)

    def test_a_spawn_that_costs_nothing_is_never_counted(self, tmp_path, git_repo):
        """Shell nodes and served results set costs_tokens=False; the cap is a
        token ceiling like the wallet it sits under, so they pass through."""
        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 2, "backoff_ms": 1},
             "spec": {"task": "t", "costs_tokens": False, "exit_code": 1, "outputs": ["x"]}},
            cap=1,
        ), git_repo)
        h.engine.run()
        assert len(calls_of(h, "w")) == 3
        assert load_state(h.run_dir).nodes["w"].token_spawns == 0


class TestLineage:
    def test_the_count_survives_resume(self, tmp_path, git_repo):
        """Acceptance 2: attempts across separate drives accumulate. The third
        drive spawns nothing and says so."""
        f = flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 0}, "spec": {"task": "t", "exit_code": 1, "outputs": ["x"]}},
            cap=2,
        )
        h1 = build(tmp_path, f, git_repo)
        h1.engine.run()
        for expected in (1, 0):
            h = rebuild(tmp_path, json.loads(json.dumps(f)), git_repo, h1.run_dir)
            h.engine.prepare_resume()
            assert h.engine.run() == EXIT_NODE_FAILED
            assert len(calls_of(h, "w")) == expected
        st = load_state(h1.run_dir)
        assert st.nodes["w"].token_spawns == 2
        assert st.token_spawns == 2
        assert "max_spawns_per_node" in st.nodes["w"].error


class TestMapItems:
    def _map_flow(self, item_spec, *, cap=1, wallet=40, extra=()):
        return flow(
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'],
                      "readonly": True, "costs_tokens": False}},
            {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
             "over": "{steps.src.json.files}", "concurrency": 1,
             "final": not extra, "spec": item_spec},
            *extra,
            cap=cap, wallet=wallet,
        )

    def test_the_cap_is_per_item(self, tmp_path, git_repo):
        h = build(tmp_path, self._map_flow(
            {"task": "handle {item}", "empty_result": True}), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        # One spawn per item: each item got its own ceiling, and neither
        # item's auto-retry ran.
        assert len(calls_of(h, "m")) == 2
        st = load_state(h.run_dir)
        items = st.nodes["m"].items
        assert [items[k].token_spawns for k in ("0", "1")] == [1, 1]
        assert all("max_spawns_per_node" in items[k].error for k in ("0", "1"))
        trips = [e for e in events(h.run_dir, "budget") if e.get("op") == "node-cap"]
        assert sorted(e["item"] for e in trips) == [0, 1]

    def test_an_item_contract_corrective_is_capped(self, tmp_path, git_repo):
        f = self._map_flow({"task": "handle {item}", "outputs": ['{"nope": 1}', VALID]})
        f["nodes"][1].update({"output": "json", "contract": "Verdict"})
        h = build(tmp_path, f, git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert [c.corrective for c in calls_of(h, "m")] == [False, False]
        err = load_state(h.run_dir).nodes["m"].items["0"].error
        assert "contract validation failed" in err
        assert "spawn cap reached: m[0] has spent" in err

    def test_an_item_scope_corrective_is_capped(self, tmp_path, git_repo):
        h = build(tmp_path, self._map_flow(
            {"task": "handle {item}", "outputs": ["ok"], "writes": ["in/**"],
             "write_files": {"out/x.txt": "stray"}}), git_repo)
        assert h.engine.run() == EXIT_NODE_FAILED
        assert len(calls_of(h, "m")) == 2  # one per item, no corrective
        err = load_state(h.run_dir).nodes["m"].items["0"].error
        assert "write scope violated" in err
        assert "spawn cap reached: m[0] has spent" in err
        assert not (git_repo / "out" / "x.txt").exists()

    def test_a_healed_map_keeps_its_item_counts(self, tmp_path, git_repo):
        gate = {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["m"],
                "output": "json", "contract": "Verdict", "final": True,
                "heal": {"max_rounds": 1, "targets": ["m"]},
                "spec": {"task": "check", "costs_tokens": False, "outputs": [BLOCK, VALID]}}
        h = build(tmp_path, self._map_flow(
            {"task": "handle {item}", "outputs": ["ok"]}, extra=(gate,)), git_repo)
        h.engine.run()
        # Round 0 spent each item's one spawn; the heal round's item reset
        # must not hand them a fresh ceiling.
        assert len(calls_of(h, "m")) == 2
        st = load_state(h.run_dir)
        assert [st.nodes["m"].items[k].token_spawns for k in ("0", "1")] == [1, 1]
        assert st.nodes["m"].status == "failed"

    def test_a_forecast_warns_when_the_wallet_cannot_cover_the_known_minimum(
            self, tmp_path, git_repo):
        """Acceptance 3: once the item count is known, two items plus one
        mandatory downstream harness-cost node need three spawns; a wallet of
        two cannot cover that, and the run says so before spending."""
        tail = {"id": "t", "role": "work", "kind": "fake", "depends_on": ["m"],
                "final": True, "spec": {"task": "sum", "outputs": ["ok"]}}
        h = build(tmp_path, self._map_flow(
            {"task": "handle {item}", "outputs": ["ok"]}, cap=None, wallet=2,
            extra=(tail,)), git_repo)
        h.engine.run()
        fc = [e for e in events(h.run_dir, "budget") if e.get("op") == "forecast"]
        assert len(fc) == 1
        assert fc[0]["node"] == "m" and fc[0]["required"] == 3 and fc[0]["remaining"] == 2
        assert any("forecast" in line for line in h.logs)

    def test_no_forecast_when_the_wallet_covers_it(self, tmp_path, git_repo):
        h = build(tmp_path, self._map_flow(
            {"task": "handle {item}", "outputs": ["ok"]}, cap=None, wallet=10), git_repo)
        assert h.engine.run() == EXIT_OK
        assert not [e for e in events(h.run_dir, "budget") if e.get("op") == "forecast"]


class TestStatus:
    def test_status_names_capped_nodes_and_the_way_out(self, tmp_path, git_repo, capsys):
        from lockstep.cli import main

        h = build(tmp_path, flow(
            {"id": "w", "role": "work", "kind": "fake", "final": True,
             "retry": {"max": 0}, "spec": {"task": "t", "empty_result": True}},
            cap=1,
        ), git_repo)
        h.engine.run()
        capsys.readouterr()
        main(["status", str(h.run_dir)])
        out = capsys.readouterr().out
        line = next(l for l in out.splitlines() if l.startswith("spawn cap:"))
        assert "w" in line and "new lineage" in line

    def test_status_does_not_name_the_dependents_of_a_capped_gate(
            self, tmp_path, git_repo, capsys):
        """A terminal gate block copies the gate's reason onto every
        dependent (`gate g blocked: <reason>`). Those nodes spent nothing;
        naming them "capped" would send the reader to split the wrong node."""
        from lockstep.cli import main

        h = build(tmp_path, flow(
            {"id": "g", "role": "gate", "kind": "fake", "output": "json",
             "contract": "Verdict", "spec": {"task": "check", "empty_result": True}},
            {"id": "after", "role": "work", "kind": "fake", "depends_on": ["g"],
             "final": True, "spec": {"task": "t", "outputs": ["ok"]}},
            cap=1,
        ), git_repo)
        h.engine.run()
        st = load_state(h.run_dir)
        assert "max_spawns_per_node" in (st.nodes["after"].error or "")  # the premise
        capsys.readouterr()
        main(["status", str(h.run_dir)])
        line = next(l for l in capsys.readouterr().out.splitlines()
                    if l.startswith("spawn cap:"))
        assert line.startswith("spawn cap: g reached")
