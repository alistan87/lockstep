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
