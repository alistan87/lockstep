"""S3 engine half: `kind:"attempt"` journal events.

The gap they close: rotated artifact names record THAT an attempt happened,
never why. A downstream consumer asked for a per-attempt manifest ARTIFACT;
upstream countered with journal events, because the journal is already
hash-chained, kind-tagged, forward-tolerant, and read by every cockpit
surface — and an attempt record is engine-recorded fact, which belongs
inside trace integrity rather than beside it.

What these tests hold down: the cause enum is the engine's and is never
inferred, the events carry no prompt or context text, and nothing about
them reaches an input hash.
"""

from __future__ import annotations

import json

from lockstep.state import load_state

from conftest import build, rebuild


def attempts_of(run_dir, node_id=None):
    out = []
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("kind") == "attempt" and (node_id is None or ev.get("node") == node_id):
            out.append(ev)
    return out


VALID = {"findings": [], "verdict": "pass", "reason": "ok"}


def _flow(node, name="attempts"):
    return {"name": name, "nodes": [node]}


class TestCause:
    def test_a_clean_node_records_one_initial_attempt(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "outputs": ["done"]},
        }), git_repo)
        assert h.engine.run() == 0
        evs = attempts_of(h.run_dir, "w")
        assert [e["cause"] for e in evs] == ["initial"]
        assert evs[0]["ordinal"] == 1

    def test_an_auto_retry_is_named_as_one(self, tmp_path, git_repo):
        """M4's free auto-retry is not a `retry` — they have different budgets
        and different meanings, and a reader who cannot tell them apart cannot
        tell a flaky harness from a failing one."""
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "empty_result": True},
        }), git_repo)
        h.engine.run()
        assert [e["cause"] for e in attempts_of(h.run_dir, "w")] == [
            "initial", "auto-retry"]

    def test_a_retry_is_distinct_from_an_auto_retry(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "retry": {"max": 1, "backoff_ms": 1},
            "spec": {"task": "t", "exit_code": 1, "outputs": ["x"]},
        }), git_repo)
        h.engine.run()
        assert [e["cause"] for e in attempts_of(h.run_dir, "w")] == ["initial", "retry"]

    def test_a_contract_corrective_is_named(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": ['{"nope": 1}', VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        assert [e["cause"] for e in attempts_of(h.run_dir, "w")] == [
            "initial", "corrective"]

    def test_a_heal_round_is_named(self, tmp_path, git_repo):
        f = {
            "name": "healer",
            "nodes": [
                {"id": "w", "role": "work", "kind": "fake",
                 "spec": {"task": "t", "outputs": ["v1", "v2"]}},
                {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
                 "output": "json", "contract": "Verdict", "final": True,
                 "heal": {"max_rounds": 1, "targets": ["w"]},
                 "spec": {"task": "check", "outputs": [
                     {"findings": [], "verdict": "block", "reason": "no"},
                     VALID]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        h.engine.run()
        causes = [e["cause"] for e in attempts_of(h.run_dir, "w")]
        assert causes[0] == "initial"
        assert "heal" in causes, f"heal round not named: {causes}"
        healed = [e for e in attempts_of(h.run_dir, "w") if e["cause"] == "heal"]
        assert healed[0]["heal_round"] >= 1

    def test_a_resumed_node_is_not_called_initial(self, tmp_path, git_repo):
        """A second drive of the same node is a resume, not a first attempt.
        Inferring that from filenames is exactly what these events replace."""
        flow = _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "retry": {"max": 0},
            "spec": {"task": "t", "exit_code": 1, "outputs": ["x"]},
        })
        h1 = build(tmp_path, flow, git_repo)
        h1.engine.run()
        h2 = rebuild(tmp_path, json.loads(json.dumps(flow)), git_repo, h1.run_dir)
        h2.engine.prepare_resume()
        h2.engine.run()
        assert [e["cause"] for e in attempts_of(h1.run_dir, "w")] == ["initial", "resume"]


class TestMapItems:
    def test_each_item_records_its_own_attempts(self, tmp_path, git_repo):
        f = {
            "name": "mapper",
            "nodes": [
                {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
                 "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'],
                          "readonly": True}},
                {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
                 "over": "{steps.src.json.files}", "concurrency": 1, "final": True,
                 "spec": {"task": "handle {item}", "outputs": ["ok"]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        assert h.engine.run() == 0
        evs = attempts_of(h.run_dir, "m")
        assert sorted(e["item"] for e in evs) == [0, 1]
        assert all(e["cause"] == "initial" and e["ordinal"] == 1 for e in evs)


class TestPrivacyAndHashing:
    def test_events_carry_no_prompt_or_context_text(self, tmp_path, git_repo):
        secret = "SECRET-TASK-TEXT-THAT-MUST-NOT-BE-JOURNALLED"
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": secret, "outputs": ["done"]},
        }), git_repo)
        assert h.engine.run() == 0
        blob = (h.run_dir / "events.jsonl").read_text(encoding="utf-8")
        assert secret not in blob
        for ev in attempts_of(h.run_dir, "w"):
            # `parts` names the hash parts; it never carries their contents.
            for name in ev.get("parts", []):
                assert secret not in name

    def test_attempt_events_do_not_move_the_input_hash(self, tmp_path, git_repo):
        """M3: the journal is not a hash input, and this must stay true or
        every cached node re-bills on upgrade."""
        flow = _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "outputs": ["done"]},
        })
        h = build(tmp_path, flow, git_repo)
        assert h.engine.run() == 0
        first = load_state(h.run_dir).nodes["w"].input_hash
        assert attempts_of(h.run_dir, "w"), "precondition: events were written"

        h2 = rebuild(tmp_path, json.loads(json.dumps(flow)), git_repo, h.run_dir)
        h2.engine.prepare_resume()
        assert h2.engine.run() == 0
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.input_hash == first, "a journalled attempt changed the hash"
        assert rec.attempts == 1, "the node must not have re-run"

    def test_the_chain_still_verifies(self, tmp_path, git_repo):
        from lockstep.state import trace_status

        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "outputs": ["done"]},
        }), git_repo)
        assert h.engine.run() == 0
        status = trace_status(h.run_dir)
        assert status["ok"] is True, status
