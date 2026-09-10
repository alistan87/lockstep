"""Throughput-parity C2/C3 (PROPOSAL-throughput-and-harness-parity §4):
deletion-only JSON repair before the corrective re-spawn, and the corrective
fence that embeds the longest near-object instead of salvaged rubble.

The hard rule under test everywhere here: repair may only DELETE bytes.
A synthesized closing bracket would pass a truncated review as a clean one
(rev-2 blocker F-E2) — truncation must fall through to the corrective,
where the model completes its own prefix."""

from __future__ import annotations

import json

from lockstep.repair import longest_near_object, repair_json
from lockstep.state import load_state

from conftest import build, calls_of


# ---------------------------------------------------------------- unit: repair_json


class TestRepairJson:
    def _ok(self, text):
        out = repair_json(text)
        assert out is not None, f"expected a repair for {text!r}"
        repaired, deletions = out
        json.loads(repaired)  # every accepted repair must decode
        assert deletions
        return repaired, deletions

    def test_dangling_comma_before_array_closer(self):
        repaired, deletions = self._ok('[1, 2,]')
        assert json.loads(repaired) == [1, 2]
        assert any("comma" in d for d in deletions)

    def test_dangling_comma_before_object_closer(self):
        repaired, _ = self._ok('{"a": 1,}')
        assert json.loads(repaired) == {"a": 1}

    def test_markdown_fences_stripped(self):
        repaired, deletions = self._ok('```json\n{"a": 1}\n```')
        assert json.loads(repaired) == {"a": 1}
        assert any("fence" in d for d in deletions)

    def test_leading_and_trailing_garbage_dropped(self):
        repaired, deletions = self._ok('Sure! Here it is: {"a": 1} Hope that helps.')
        assert json.loads(repaired) == {"a": 1}
        assert len(deletions) == 2  # leading + trailing

    def test_longest_value_wins_not_last(self):
        # extract_last_json takes the LAST balanced value; repair prefers the
        # LONGEST — the intended output over trailing chatter.
        repaired, _ = self._ok('{"findings": [1, 2, 3]} ok {"b": 1}')
        assert json.loads(repaired) == {"findings": [1, 2, 3]}

    def test_truncated_list_is_never_completed(self):
        # The F-E2 blocker: a review truncated at two of three findings must
        # NOT repair to a valid two-finding review.
        assert repair_json('[{"a": 1}, {"b": 2},') is None

    def test_truncation_just_after_opener_is_never_a_clean_result(self):
        assert repair_json('[') is None
        assert repair_json('{"findings": [') is None

    def test_valid_json_returns_none(self):
        # Wrong SHAPE is not repair's problem: nothing to delete.
        assert repair_json('{"a": 1}') is None
        assert repair_json('  {"a": 1}  ') is None

    def test_comma_inside_string_untouched(self):
        repaired, _ = self._ok('x {"note": ", ]"} y')
        assert json.loads(repaired) == {"note": ", ]"}

    def test_double_comma_is_not_repairable(self):
        # Deleting one of two commas would be guessing at data, not deleting
        # garbage around it.
        assert repair_json('[1,,2]') is None

    def test_empty_and_no_json(self):
        assert repair_json('') is None
        assert repair_json('no json here at all') is None


# ---------------------------------------------------------------- unit: longest_near_object


class TestLongestNearObject:
    def test_corrupted_token_yields_outer_prefix(self):
        # The chronicle-forensics case: one corrupted token deep in the outer
        # object; extract_last_json falls back to the last balanced INNER
        # value ([]), and the corrective then fences rubble.
        text = '{"schema_observations": [], "char_span": [4177, 418ff]}'
        near = longest_near_object(text)
        assert near is not None
        assert near.startswith('{"schema_observations"')
        assert "char_span" in near
        assert len(near) > len("[]")

    def test_truncated_stream_yields_whole_prefix(self):
        text = '[{"a": 1}, {"b": 2},'
        near = longest_near_object(text)
        assert near == text

    def test_complete_json_is_not_a_near_object(self):
        # A complete decode is what extraction already got; C3 exists for the
        # value extraction could NOT reach.
        assert longest_near_object('{"a": 1}') is None

    def test_no_candidates(self):
        assert longest_near_object('plain prose') is None


# ---------------------------------------------------------------- engine: C2


def _flow(node):
    return {"name": "repair-flow", "nodes": [node]}


REPAIRABLE = '{"findings": [], "verdict": "pass", "reason": "ok",}'  # dangling comma
VALID = {"findings": [], "verdict": "pass", "reason": "ok"}


class TestEngineRepair:
    def test_repairable_output_skips_the_corrective(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [REPAIRABLE]},
        }), git_repo)
        assert h.engine.run() == 0
        assert len(calls_of(h, "w")) == 1, "repair must not spend a spawn"
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.repaired is True
        assert json.loads(open(rec.result_path, encoding="utf-8").read()) == VALID

    def test_raw_bytes_rotated_before_recording(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [REPAIRABLE]},
        }), git_repo)
        assert h.engine.run() == 0
        phase = h.run_dir / "phases" / "w"
        rotated = phase / "result-attempt1.json"
        assert rotated.exists(), "pre-repair bytes must survive rotation"
        assert rotated.read_text(encoding="utf-8") == REPAIRABLE
        assert json.loads((phase / "result.json").read_text(encoding="utf-8")) == VALID

    def test_repair_event_journaled(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [REPAIRABLE]},
        }), git_repo)
        assert h.engine.run() == 0
        events = [json.loads(line) for line in
                  (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        repair = [e for e in events if e.get("kind") == "repair"]
        assert len(repair) == 1
        assert repair[0]["node"] == "w"
        assert repair[0]["rotated"] == "result-attempt1.json"
        assert repair[0]["deleted"]

    def test_unrepairable_falls_to_corrective(self, tmp_path, git_repo):
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": ['{"findings": [{"severity": "minor", "title"', VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        calls = calls_of(h, "w")
        assert len(calls) == 2 and calls[1].corrective
        assert load_state(h.run_dir).nodes["w"].repaired is False

    def test_wrong_shape_after_repair_falls_to_corrective(self, tmp_path, git_repo):
        # Repair produced valid JSON, the contract still rejects it: the
        # corrective runs and the record must NOT claim a repair.
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": ['{"not_a_verdict": 1,}', VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        calls = calls_of(h, "w")
        assert len(calls) == 2 and calls[1].corrective
        assert load_state(h.run_dir).nodes["w"].repaired is False

    def test_gate_is_never_repaired(self, tmp_path, git_repo):
        # A verdict's consumers act without a human re-reading raw bytes:
        # a gate's validation failure goes straight to the corrective.
        h = build(tmp_path, _flow({
            "id": "g", "role": "gate", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [REPAIRABLE, VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        calls = calls_of(h, "g")
        assert len(calls) == 2 and calls[1].corrective
        assert load_state(h.run_dir).nodes["g"].repaired is False

    def test_map_items_repair_per_item(self, tmp_path, git_repo):
        f = {
            "name": "repair-map",
            "nodes": [
                {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
                 "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'], "readonly": True}},
                {"id": "m", "role": "map", "kind": "fake", "depends_on": ["src"],
                 "over": "{steps.src.json.files}", "concurrency": 1,
                 "output": "json", "contract": "Verdict", "final": True,
                 "spec": {"task": "handle {item}", "outputs": [REPAIRABLE]}},
            ],
        }
        h = build(tmp_path, f, git_repo)
        assert h.engine.run() == 0
        assert len(calls_of(h, "m")) == 2, "one spawn per item, no correctives"
        rec = load_state(h.run_dir).nodes["m"]
        assert rec.items["0"].repaired is True
        assert rec.items["1"].repaired is True


# ---------------------------------------------------------------- engine: C3


class TestCorrectiveFence:
    def test_fence_embeds_near_object_not_inner_rubble(self, tmp_path, git_repo):
        # File channel, corrupted outer token: extraction salvages the last
        # balanced INNER value ("[]"), which validates as nothing. The
        # corrective fence must carry the longest near-object — the model's
        # own truncated outer output — so it corrects instead of re-deriving.
        corrupted = '{"schema_observations": [], "char_span": [4177, 418ff]}'
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [corrupted, VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        calls = calls_of(h, "w")
        assert len(calls) == 2 and calls[1].corrective
        assert "char_span" in calls[1].prompt, "near-object missing from the fence"

    def test_fence_keeps_result_text_when_no_longer_near_object(self, tmp_path, git_repo):
        # A complete-but-wrong-shape value: nothing near about it; the fence
        # carries the value itself, exactly as before C3.
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "outputs": [{"not_a_verdict": 1}, VALID]},
        }), git_repo)
        assert h.engine.run() == 0
        calls = calls_of(h, "w")
        assert len(calls) == 2
        assert '"not_a_verdict"' in calls[1].prompt


# ------------------------------------------- engine: the REAL file channel


class TestRealFileChannel:
    """The fake executor hands the engine the raw file text; the REAL harness
    executor first salvages a non-JSON result file down to its last balanced
    inner value (the E2 fence salvage). A dangling-comma file therefore
    arrives at validation as `[]` — and repair must reach back to the raw
    FILE bytes, or C2 misses exactly its headline case. File channel only:
    on the stdout channel the raw text is narration, and a narrated example
    object must never be adopted as the result."""

    def test_dangling_comma_file_repairs_without_corrective(self, tmp_path, git_repo):
        from lockstep.registry import ExecutorStanza
        from conftest import PY, make_config

        write_bad = (
            "import sys, pathlib\n"
            "pathlib.Path(sys.argv[2], 'result.json').write_text("
            "'{\"findings\": [], \"verdict\": \"pass\", \"reason\": \"ok\",}')\n"
        )
        cfg = make_config(writer=ExecutorStanza(
            argv=[PY, "-c", write_bad, "{prompt}", "{phase_dir}"]))
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "harness", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "t", "executor": "writer"},
        }), git_repo, config=cfg)
        assert h.engine.run() == 0
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.repaired is True
        assert rec.attempts == 1, "repair must not spend the corrective"
        assert json.loads(open(rec.result_path, encoding="utf-8").read()) == VALID
        phase = h.run_dir / "phases" / "w"
        assert (phase / "result-attempt1.json").read_text(encoding="utf-8") == (
            '{"findings": [], "verdict": "pass", "reason": "ok",}')

    def test_repaired_flag_resets_on_clean_rerun(self, tmp_path, git_repo):
        """`repaired` describes the RECORDED result: a hash-missed re-run
        that produces clean output must clear it, or `status` and the
        mission drawer claim a repair that never touched the current bytes.
        A revalidation-KEPT result keeps the flag with its kept bytes."""
        from conftest import rebuild

        flow1 = _flow({
            "id": "w", "role": "work", "kind": "fake", "output": "json",
            "contract": "Verdict", "final": True,
            "spec": {"task": "v1", "outputs": [REPAIRABLE]},
        })
        h1 = build(tmp_path, flow1, git_repo)
        assert h1.engine.run() == 0
        assert load_state(h1.run_dir).nodes["w"].repaired is True

        # Unchanged resume: result kept, flag kept.
        h2 = rebuild(tmp_path, json.loads(json.dumps(flow1)), git_repo, h1.run_dir)
        h2.engine.prepare_resume()
        assert h2.engine.run() == 0
        assert load_state(h1.run_dir).nodes["w"].repaired is True

        # Task edit -> hash miss -> clean re-run: flag must clear.
        flow2 = json.loads(json.dumps(flow1))
        flow2["nodes"][0]["spec"]["task"] = "v2"
        flow2["nodes"][0]["spec"]["outputs"] = [VALID]
        h3 = rebuild(tmp_path, flow2, git_repo, h1.run_dir)
        h3.engine.prepare_resume()
        assert h3.engine.run() == 0
        assert load_state(h1.run_dir).nodes["w"].repaired is False


# ------------------------------------------- single-value rule (file channel)


DECOY_FILE = (
    'Schema example: [{"severity": "minor", "title": "example", "file": "x", '
    '"evidence": "e"}]\n'
    'Actual findings:\n'
    '[{"severity": "major", "title": "real-1", "file": "y", "evidence": "e"}, '
    '{"severity": "major", "title": "real-2 truncat'
)


class TestSingleValueMode:
    """Adversarial round 2, finding 1: on the file channel, repair may fix
    byte damage to THE one value the footer contract says the file contains —
    it must never let a narrated example (which validates by construction) or
    a superseded draft displace a truncated real answer. Multi-value files go
    to the corrective, where C3 fences the truncated REAL answer."""

    def test_example_before_truncated_real_is_refused(self):
        assert repair_json(DECOY_FILE, single_value=True) is None

    def test_two_complete_values_are_refused(self):
        draft_and_final = '{"a": 1, "big": "draft"} {"a": 2}'
        assert repair_json(draft_and_final, single_value=True) is None
        # permissive mode (stdout, already-extracted single values) keeps
        # today's longest-wins behaviour
        assert repair_json(draft_and_final) is not None

    def test_single_damaged_value_still_repairs(self):
        repaired, _ = repair_json(
            '{"findings": [], "verdict": "pass", "reason": "ok",}', single_value=True)
        assert json.loads(repaired) == VALID

    def test_fenced_single_value_still_repairs(self):
        repaired, _ = repair_json(
            '```json\n{"findings": [], "verdict": "pass", "reason": "ok"}\n```',
            single_value=True)
        assert json.loads(repaired) == VALID

    def test_decoy_file_goes_to_corrective_not_repair(self, tmp_path, git_repo):
        from lockstep.registry import ExecutorStanza
        from conftest import PY, make_config

        write_decoy = (
            "import sys, pathlib\n"
            "pathlib.Path(sys.argv[2], 'result.json').write_text("
            + repr(DECOY_FILE) + ")\n"
        )
        cfg = make_config(writer=ExecutorStanza(
            argv=[PY, "-c", write_decoy, "{prompt}", "{phase_dir}"]))
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "harness", "output": "json",
            "contract": "Finding[]", "final": True,
            "spec": {"task": "t", "executor": "writer"},
        }), git_repo, config=cfg)
        h.engine.run()  # corrective re-writes the same decoy: terminal failure
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.repaired is False, "the example array must NOT become the result"
        assert rec.attempts == 2, "the corrective ran instead"


# ------------------------------------------- provenance across --seed


def test_seed_serves_repaired_provenance(tmp_path, git_repo):
    """Round 2, finding 3: a seeded record's result IS the source's repaired
    bytes — the new run's status/drawer must say so, not present them
    unmarked (the surfaces the repaired flag exists for)."""
    from lockstep.seed import SeedIndex, wrap_registry

    flow = _flow({
        "id": "w", "role": "work", "kind": "fake", "output": "json",
        "contract": "Verdict", "final": True,
        "spec": {"task": "t", "outputs": [REPAIRABLE]},
    })
    h1 = build(tmp_path, flow, git_repo)
    assert h1.engine.run() == 0
    assert load_state(h1.run_dir).nodes["w"].repaired is True

    h2 = build(tmp_path, json.loads(json.dumps(flow)), git_repo)
    wrap_registry(h2.engine.registry, SeedIndex.from_run_dir(h1.run_dir),
                  log=lambda *a: None, on_hit=h2.engine.note_seeded)
    assert h2.engine.run() == 0
    rec = load_state(h2.run_dir).nodes["w"]
    assert rec.seeded_from, "precondition: the result was served, not spawned"
    assert rec.repaired is True, "served repaired bytes must keep the marker"


# ------------------------------- round 3: the salvage layer + trailing bytes


EARLY_TRUNC_DECOY = (
    '[{"severity": "minor", "title": "example", "file": "x", "evidence": "e"}]\n'
    'Actual findings:\n'
    '[{"severity": "major", "title": "real-1 truncat'
)  # truncation BEFORE the first real finding completes: the last complete
   # value in the file is the decoy itself


class TestSalvageLayer:
    """Round-3 major: the E2 file salvage ran extract_last_json (last
    complete value) BEFORE validation — so when truncation lands before the
    first real value completes, the narrated example was the last complete
    value, validated by construction, and the node went done with the decoy;
    repair and its single-value rule were never consulted. The file-channel
    salvage now carries the same discipline: one value-shaped span, no
    failed span, nothing but whitespace after the value."""

    def test_early_truncation_decoy_never_goes_done(self, tmp_path, git_repo):
        from lockstep.registry import ExecutorStanza
        from conftest import PY, make_config

        write_decoy = (
            "import sys, pathlib\n"
            "pathlib.Path(sys.argv[2], 'result.json').write_text("
            + repr(EARLY_TRUNC_DECOY) + ")\n"
        )
        cfg = make_config(writer=ExecutorStanza(
            argv=[PY, "-c", write_decoy, "{prompt}", "{phase_dir}"]))
        h = build(tmp_path, _flow({
            "id": "w", "role": "work", "kind": "harness", "output": "json",
            "contract": "Finding[]", "final": True,
            "spec": {"task": "t", "executor": "writer"},
        }), git_repo, config=cfg)
        h.engine.run()
        rec = load_state(h.run_dir).nodes["w"]
        assert rec.repaired is False
        assert rec.attempts == 2, "must reach the corrective, not adopt the example"
        if rec.status == "done":  # the corrective re-wrote the same decoy
            raise AssertionError("decoy adopted as the result")

    def test_fence_salvage_still_free(self):
        # The case E2 exists for survives the stricter salvage.
        from lockstep.repair import salvage_file_value
        assert salvage_file_value('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_salvage_refuses_multi_span_and_trailing_bytes(self):
        from lockstep.repair import salvage_file_value
        assert salvage_file_value(EARLY_TRUNC_DECOY) is None       # failed span
        assert salvage_file_value('{"a":1} {"b":2}') is None       # two values
        assert salvage_file_value('{"a":1} trailing prose') is None  # tail bytes
        assert salvage_file_value('Header line:\n{"a": 1}') == '{"a": 1}'  # leading ok
        assert salvage_file_value('{"a": 1,}') is None  # damage is repair's job

    def test_bracket_free_truncated_tail_refused_at_both_layers(self):
        # Round-3 minor: a decoy followed by a truncated tail with no {/[
        # opener — invisible to the span scanner, caught by the trailing-
        # bytes rule at BOTH layers.
        from lockstep.repair import salvage_file_value
        text = '{"findings": [], "verdict": "pass", "reason": "decoy"} x"verdict": "fail", "reason": "truncated real answ'
        assert salvage_file_value(text) is None
        assert repair_json(text, single_value=True) is None
        # permissive (stdout single-extracted-value) mode unaffected
        assert repair_json(text) is not None
