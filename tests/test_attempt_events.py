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
        evs = attempts_of(h.run_dir, "w")
        # Precondition, without which this passes with the feature REVERTED -
        # a reviewer proved exactly that by reverse-applying the hunk.
        assert evs, "precondition: attempt events were written"
        blob = (h.run_dir / "events.jsonl").read_text(encoding="utf-8")
        assert secret not in blob
        for ev in evs:
            assert secret not in json.dumps(ev)

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
        assert attempts_of(h.run_dir, "w"), "precondition: events in the chain"
        status = trace_status(h.run_dir)
        assert status["ok"] is True, status


# ------------------------------------------- adversarial round: the fixes


class TestReviewFindings:
    """Each of these reproduces a defect a reviewer found and ran."""

    def test_a_budget_trip_journals_no_phantom_attempt(self, tmp_path, git_repo):
        """BLOCKER: the event was written BEFORE _spend_spawn, so a trip
        recorded an attempt that never happened — and the resume then wrote
        the same ordinal again, leaving two byte-identical events for one
        real attempt. Exit 4 then resume is a documented normal outcome."""
        f = {
            "name": "budget",
            "budget": {"max_agent_spawns": 1, "max_run_minutes": 60},
            "nodes": [
                {"id": "a", "role": "work", "kind": "fake",
                 "spec": {"task": "t", "outputs": ["x"]}},
                {"id": "b", "role": "work", "kind": "fake", "depends_on": ["a"],
                 "final": True, "spec": {"task": "t", "outputs": ["y"]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        assert h.engine.run() == 4
        assert attempts_of(h.run_dir, "b") == [], "a spawn that never happened"

        h2 = rebuild(tmp_path, json.loads(json.dumps(f)), git_repo, h.run_dir)
        h2.engine.store.mutate(lambda st: setattr(st, "token_spawns", 0))
        h2.engine.prepare_resume()
        h2.engine.run()
        ordinals = [e["ordinal"] for e in attempts_of(h.run_dir, "b")]
        assert ordinals, "precondition: the resume actually ran `b`"
        assert len(ordinals) == len(set(ordinals)), f"duplicate ordinals: {ordinals}"

    def test_a_served_node_is_not_called_initial(self, tmp_path, git_repo):
        """A replay/seed serves results at the execute seam, so a served node
        still walks the attempt loop. Journalling it `initial` made a replay's
        journal assert that every node ran."""
        from lockstep.seed import SeedIndex, wrap_registry

        flow = _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "outputs": ["done"]},
        }, name="served")
        h1 = build(tmp_path, flow, git_repo)
        assert h1.engine.run() == 0

        h2 = build(tmp_path, json.loads(json.dumps(flow)), git_repo)
        wrap_registry(h2.engine.registry, SeedIndex.from_run_dir(h1.run_dir),
                      log=lambda *a: None, on_hit=h2.engine.note_seeded)
        assert h2.engine.run() == 0
        assert [e["cause"] for e in attempts_of(h2.run_dir, "w")] == ["served"]

    def test_a_baseline_gate_spawn_is_journalled(self, tmp_path, git_repo):
        """E4 baseline gates spend a real spawn and bumped `attempts`
        silently, so the gate's first ordinary attempt in a FRESH run read
        `resume` — the one billed spawn with no event created the lie."""
        f = {
            "name": "baseline",
            "nodes": [
                {"id": "w", "role": "work", "kind": "fake",
                 "spec": {"task": "t", "outputs": ["x"]}},
                {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
                 "output": "json", "contract": "Verdict", "final": True,
                 "spec": {"task": "check", "baseline": True, "outputs": [VALID, VALID]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        h.engine.run()
        causes = [e["cause"] for e in attempts_of(h.run_dir, "g")]
        assert causes and causes[0] == "baseline", causes
        assert "resume" not in causes, f"a fresh run claimed a resume: {causes}"

    def test_heal_round_does_not_persist_as_a_cause(self, tmp_path, git_repo):
        """`rec.heal_round` is the gate's running total and is never reset, so
        using it as a fallback made EVERY later attempt of a gate that once
        healed report `heal`, forever, across processes."""
        f = {
            "name": "healer2",
            "nodes": [
                {"id": "w", "role": "work", "kind": "fake",
                 "spec": {"task": "t", "outputs": ["v1", "v2"]}},
                {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
                 "output": "json", "contract": "Verdict", "final": True,
                 "heal": {"max_rounds": 1, "targets": ["w"]},
                 "spec": {"task": "check", "outputs": [
                     {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        h.engine.run()
        # A later drive whose gate re-runs on a hash miss, with NO cascade.
        f2 = json.loads(json.dumps(f))
        f2["nodes"][1]["spec"]["task"] = "check v2"
        f2["nodes"][1]["spec"]["outputs"] = [VALID]
        h2 = rebuild(tmp_path, f2, git_repo, h.run_dir)
        h2.engine.prepare_resume()
        h2.engine.run()
        last = attempts_of(h.run_dir, "g")[-1]
        assert last["cause"] != "heal", "a hash-miss re-run claimed a heal round"

    def test_a_healed_map_item_says_heal(self, tmp_path, git_repo):
        """The cascade marks the MAP node; items went through a path that
        never consulted the signal, so round 1's items were byte-identical to
        round 0's and the map's entry was popped by nobody."""
        f = {
            "name": "healmap",
            "nodes": [
                {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
                 "spec": {"outputs": ['{"files": ["p"], "notes": ""}'], "readonly": True}},
                {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
                 "over": "{steps.src.json.files}", "concurrency": 1,
                 "spec": {"task": "do {item}", "outputs": ["r1", "r2"]}},
                {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["m"],
                 "output": "json", "contract": "Verdict", "final": True,
                 "heal": {"max_rounds": 1, "targets": ["m"]},
                 "spec": {"task": "check", "outputs": [
                     {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        h.engine.run()
        causes = [e["cause"] for e in attempts_of(h.run_dir, "m")]
        assert "heal" in causes, f"healed items still claim a first attempt: {causes}"
        assert h.engine.store.state.heal_pending == {}, "the signal was never consumed"

    def test_events_carry_no_unbounded_parts_list(self, tmp_path, git_repo):
        """`parts` duplicated `hash_parts` (already in state.json) and grew
        with the matched file count — a 7 KB event line per attempt, per map
        item, in a file every cockpit surface reads whole."""
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "final": True,
            "spec": {"task": "t", "outputs": ["done"]},
        }), git_repo)
        assert h.engine.run() == 0
        for ev in attempts_of(h.run_dir, "w"):
            assert "parts" not in ev
            assert len(json.dumps(ev)) < 300


def test_a_scope_corrective_is_named(tmp_path, git_repo):
    """The cause the reviewer found had no test at all: `scope-corrective`
    appeared nowhere under tests/."""
    h = build(tmp_path, {"name": "scope", "nodes": [{
        "id": "w", "role": "work", "kind": "fake", "final": True,
        "spec": {"task": "t", "writes": ["allowed/**"],
                 "write_files_by_attempt": [{"forbidden.txt": "x"},
                                            {"allowed/ok.txt": "y"}],
                 "outputs": ["done"]},
    }]}, git_repo)
    h.engine.run()
    assert "scope-corrective" in [e["cause"] for e in attempts_of(h.run_dir, "w")]


def test_a_heal_signal_does_not_outlive_its_cycle(tmp_path, git_repo):
    """A node the cascade re-pends can end the drive without ever attempting.
    Its signal would then mislabel some later, unrelated attempt as rework.

    The first version of this test was VACUOUS - `heal_pending` was empty at
    every point it looked, so its assertion loop never ran, and it read from
    disk while `prepare_resume` mutates only the in-memory state. This one
    plants the stale entry directly, which is the state a `when`-skip or an
    edited-out node leaves behind.
    """
    flow = _flow({
        "id": "w", "role": "work", "kind": "fake", "final": True,
        "spec": {"task": "t", "outputs": ["done"]},
    }, name="healleak")
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0

    h2 = rebuild(tmp_path, json.loads(json.dumps(flow)), git_repo, h.run_dir)
    # `w` is done and `skipped_one` was skipped by `when` - the genuinely
    # reachable case, and the one that slipped past when this swept AFTER the
    # normalisation loop had already turned `skipped` into `pending`.
    # `ghost` is not in the flow at all.
    h2.engine.store.state.nodes["w"].status = "done"
    h2.engine.store.mutate(
        lambda st: st.heal_pending.update({"w": 1, "ghost": 2}))
    h2.engine.prepare_resume()
    left = h2.engine.store.state.heal_pending
    assert "w" not in left, "a settled node kept a heal signal"
    assert "ghost" not in left, "a node the flow no longer has kept a heal signal"


def test_a_budget_trip_leaves_the_heal_signal_for_the_resume(tmp_path, git_repo):
    """Round 2: "spend first" was applied to the journal write but not to the
    durable consume, so a trip between the cascade and the re-run ate the
    signal - the resume then reported `resume` for an attempt that was rework.
    The durable store removed the phantom event; consuming early would have
    made the mislabel permanent instead."""
    f = {
        "name": "healtrip",
        "budget": {"max_agent_spawns": 2, "max_run_minutes": 60},
        "nodes": [
            {"id": "w", "role": "work", "kind": "fake",
             "spec": {"task": "t", "outputs": ["v1", "v2"]}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
             "output": "json", "contract": "Verdict", "final": True,
             "heal": {"max_rounds": 1, "targets": ["w"]},
             "spec": {"task": "check", "outputs": [
                 {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 4, "precondition: the budget trips mid-heal"
    from lockstep.state import load_state

    assert load_state(h.run_dir).heal_pending.get("w") == 1, (
        "the trip consumed the heal signal the resume needs")


def test_a_stale_heal_round_is_not_stamped_on_a_later_attempt(tmp_path, git_repo):
    """Round 2: the CAUSE stopped falling back to the gate's never-reset
    `heal_round`, but the FIELD did not - so a later attempt was stamped with
    a round it was not part of, and the journal pane renders that as
    "(rework round N)"."""
    f = {
        "name": "healstamp",
        "nodes": [
            {"id": "w", "role": "work", "kind": "fake",
             "spec": {"task": "t", "outputs": ["v1", "v2"]}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
             "output": "json", "contract": "Verdict", "final": True,
             "heal": {"max_rounds": 1, "targets": ["w"]},
             "spec": {"task": "check", "outputs": [
                 {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
        ],
    }
    h = build(tmp_path, f, git_repo)
    h.engine.run()
    f2 = json.loads(json.dumps(f))
    f2["nodes"][1]["spec"]["task"] = "check v2"
    f2["nodes"][1]["spec"]["outputs"] = [VALID]
    h2 = rebuild(tmp_path, f2, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    h2.engine.run()
    last = attempts_of(h.run_dir, "g")[-1]
    assert last["cause"] != "heal"
    assert "heal_round" not in last, (
        f"a non-rework attempt was stamped with a round: {last}")


def test_a_map_item_resume_still_clears_its_error(tmp_path, git_repo):
    """SPEC 9.2 behaviour a round-1 fix accidentally swallowed: the heal
    sweeper was indented under its own loop, taking the map-item reset with
    it, so an item that failed in drive 1 and SUCCEEDED on resume kept its
    stale error string forever."""
    from lockstep.state import load_state

    f = {
        "name": "mapresume",
        "nodes": [
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'],
                      "readonly": True}},
            {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
             "over": "{steps.src.json.files}", "concurrency": 1, "final": True,
             "retry": {"max": 0},
             "spec": {"task": "do {item}", "spawn_error": "harness exploded"}},
        ],
    }
    h = build(tmp_path, f, git_repo)
    h.engine.run()
    assert any(i.error for i in load_state(h.run_dir).nodes["m"].items.values()), (
        "precondition: an item failed")

    f2 = json.loads(json.dumps(f))
    f2["nodes"][1]["spec"] = {"task": "do {item}", "outputs": ["ok"]}
    h2 = rebuild(tmp_path, f2, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    h2.engine.run()
    for idx, irec in load_state(h.run_dir).nodes["m"].items.items():
        if irec.status == "done":
            assert irec.error is None, f"item {idx} kept a stale error: {irec.error}"


def test_a_skipped_heal_descendant_drops_its_signal(tmp_path, git_repo):
    """The genuinely reachable case for the sweeper, end to end: a cascade
    invalidates a descendant, `when` then skips it, and its signal must not
    survive to mislabel a later attempt. This is the case that slipped past
    when the sweep read statuses AFTER the loop that normalises `skipped` to
    `pending` — it saw nothing to sweep and the test planted an unreachable
    state instead."""
    from lockstep.state import load_state

    f = {
        "name": "healskip2",
        "nodes": [
            {"id": "w", "role": "work", "kind": "fake", "output": "text",
             "spec": {"outputs": ['{"go": false}'], "readonly": True}},
            {"id": "d", "role": "work", "kind": "fake", "depends_on": ["w"],
             "when": '{steps.w.json.go} == true',
             "spec": {"task": "t", "outputs": ["x"]}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["w"],
             "output": "json", "contract": "Verdict", "final": True,
             "heal": {"max_rounds": 1, "targets": ["w"]},
             "spec": {"task": "check", "outputs": [
                 {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
        ],
    }
    h = build(tmp_path, f, git_repo)
    h.engine.run()
    st = load_state(h.run_dir)
    assert st.nodes["d"].status == "skipped", "precondition: `when` skipped it"

    h2 = rebuild(tmp_path, json.loads(json.dumps(f)), git_repo, h.run_dir)
    h2.engine.store.mutate(lambda s: s.heal_pending.update({"d": 1}))
    h2.engine.prepare_resume()
    assert "d" not in h2.engine.store.state.heal_pending, (
        "a skipped descendant kept a heal signal the sweep should have dropped")


def test_a_map_keeps_its_heal_signal_until_the_fan_out_finishes(tmp_path, git_repo):
    """Round 3: the signal was consumed by item 0, so a budget trip at item 51
    of 200 left nothing and the resume brought items 51..200 back labelled
    `initial` — the same rework round, half of it reported as a first
    attempt. It has to outlive every item that still has to run."""
    from lockstep.state import load_state

    f = {
        "name": "maptrip",
        "budget": {"max_agent_spawns": 4, "max_run_minutes": 60},
        "nodes": [
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": ['{"files": ["p", "q", "r"], "notes": ""}'],
                      "readonly": True}},
            {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
             "over": "{steps.src.json.files}", "concurrency": 1,
             "spec": {"task": "do {item}", "outputs": ["v1", "v2"]}},
            {"id": "g", "role": "gate", "kind": "fake", "depends_on": ["m"],
             "output": "json", "contract": "Verdict", "final": True,
             "heal": {"max_rounds": 1, "targets": ["m"]},
             "spec": {"task": "check", "outputs": [
                 {"findings": [], "verdict": "block", "reason": "no"}, VALID]}},
        ],
    }
    h = build(tmp_path, f, git_repo)
    code = h.engine.run()
    st = load_state(h.run_dir)
    if code == 4 and st.nodes["m"].status != "done":
        assert st.heal_pending.get("m") == 1, (
            "a trip mid-fan-out consumed the signal the rest of the items need")


def test_a_served_node_does_not_leak_its_heal_signal(tmp_path, git_repo):
    """Round 3: the consume was guarded on `heal_round is not None`, which the
    `served` branch forces to None — so a seeded run finished still holding
    the signal, a leak into whatever ran next."""
    from lockstep.seed import SeedIndex, wrap_registry

    flow = _flow({
        "id": "w", "role": "work", "kind": "fake", "final": True,
        "spec": {"task": "t", "outputs": ["done"]},
    }, name="servedleak")
    h1 = build(tmp_path, flow, git_repo)
    assert h1.engine.run() == 0

    h2 = build(tmp_path, json.loads(json.dumps(flow)), git_repo)
    h2.engine.store.mutate(lambda st: st.heal_pending.update({"w": 1}))
    wrap_registry(h2.engine.registry, SeedIndex.from_run_dir(h1.run_dir),
                  log=lambda *a: None, on_hit=h2.engine.note_seeded)
    assert h2.engine.run() == 0
    assert h2.engine.store.state.heal_pending == {}, "a served node kept the signal"


def test_a_retry_inside_a_heal_round_is_not_stamped_as_rework(tmp_path, git_repo):
    """`heal_round` must identify a REWORK attempt and nothing else — a retry
    inside the round kept the bound value and rendered as "(rework round 1)",
    which is the distinction the previous round's fix was about."""
    h = build(tmp_path, _flow({
        "id": "w", "role": "work", "kind": "fake", "final": True,
        "retry": {"max": 1, "backoff_ms": 1},
        "spec": {"task": "t", "exit_code": 1, "outputs": ["x"]},
    }, name="retrystamp"), git_repo)
    h.engine.run()
    for ev in attempts_of(h.run_dir, "w"):
        if ev["cause"] != "heal":
            assert "heal_round" not in ev, f"{ev['cause']} stamped as rework: {ev}"
