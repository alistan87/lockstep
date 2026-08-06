"""A1 — the cache-miss explainer: labelled part digests recorded beside every
input_hash, the invalidation reason captured at the decision site, and the
`lockstep explain` reader over both."""

from __future__ import annotations

import hashlib
import json

from lockstep import EXIT_CONFIG, EXIT_OK
from lockstep.explain import explain_node
from lockstep.state import compose_hash, diff_labels, label_parts, load_state

from conftest import build, rebuild

FLOW = {
    "name": "exp",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"task": "do the thing", "outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"task": "then: {steps.a.output}", "outputs": ["two"]}},
    ],
}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- label_parts


def test_label_parts_uses_the_part_prefix_as_label():
    parts = ["prompt:hello", "config:digest", "argv:[]"]
    labelled = label_parts(parts)
    assert set(labelled) == {"prompt", "config", "argv"}
    assert labelled["prompt"] == _sha("prompt:hello")


def test_label_parts_disambiguates_duplicate_labels_and_merges_detail():
    labelled = label_parts(["prompt:a", "prompt:b"], {"prompt.heal": "d1"})
    assert set(labelled) == {"prompt", "prompt#2", "prompt.heal"}
    assert labelled["prompt.heal"] == "d1"


def test_label_parts_handles_unlabelled_parts():
    assert set(label_parts(["no label here", "also none"])) == {"part", "part#2"}


def test_diff_labels_names_changed_added_and_removed():
    old = {"prompt": "1", "config": "2", "gone": "3"}
    new = {"prompt": "9", "config": "2", "fresh": "4"}
    assert diff_labels(old, new) == [
        "fresh: only in new", "gone: only in old", "prompt: changed",
    ]
    assert diff_labels(None, new) == ["unrecorded (run predates part recording)"]
    assert diff_labels({"a": "1"}, {"a": "1"}) == [
        "no labelled part differs (role, kind, contract, or an unlabelled part moved)"
    ]


# ------------------------------------------------------------- recording


def test_run_records_labelled_parts_beside_the_hash(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    state = load_state(h.run_dir)
    rec = state.nodes["a"]
    assert rec.hash_parts is not None
    assert set(rec.hash_parts) == {"prompt", "config", "prompt.task"}
    # The recorder is a spectator on compose_hash's inputs: same site, same
    # parts — so recorded digests must match sha256 of the planned parts.
    assert rec.hash_parts["config"] == _sha("config:test-config-digest")


def test_unchanged_resume_skips_and_records_no_invalidation(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    h2 = rebuild(tmp_path, FLOW, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    assert h2.fake.calls == [], "nothing changed; nothing may re-run"
    assert load_state(h.run_dir).nodes["a"].invalidated_by is None


def test_invalidation_names_the_part_that_moved(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    edited = json.loads(json.dumps(FLOW))
    edited["nodes"][0]["spec"]["task"] = "do a DIFFERENT thing"
    h2 = rebuild(tmp_path, edited, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    rec = load_state(h.run_dir).nodes["a"]
    assert rec.invalidated_by == ["prompt: changed", "prompt.task: changed"]
    events = (h.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "invalidated_by" in events, "the decision site must journal the reason"


def test_map_items_record_parts(tmp_path, git_repo):
    flow = {
        "name": "expmap",
        "nodes": [
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": [{"files": ["x", "y"], "notes": ""}]}},
            {"id": "fan", "role": "map", "kind": "fake", "final": True,
             "depends_on": ["src"], "over": "{steps.src.json.files}",
             "concurrency": 1, "spec": {"task": "item {item}", "outputs": ["ok"]}},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["fan"]
    assert set(rec.hash_parts) == {"over", "spec"}
    for irec in rec.items.values():
        assert irec.hash_parts is not None
        assert "index" in irec.hash_parts


# ------------------------------------------------------------- explain reader


def _capture():
    lines: list[str] = []
    return lines, lines.append


def test_explain_prints_recorded_parts_and_reason(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    edited = json.loads(json.dumps(FLOW))
    edited["nodes"][0]["spec"]["task"] = "do a DIFFERENT thing"
    h2 = rebuild(tmp_path, edited, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    h2.engine.run()
    lines, out = _capture()
    assert explain_node(h.run_dir, "a", out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "prompt.task" in text
    assert "this node last re-ran because:" in text
    assert "prompt: changed" in text


def test_explain_against_diffs_two_runs(tmp_path, git_repo):
    h1 = build(tmp_path, FLOW, git_repo)
    h1.engine.run()
    edited = json.loads(json.dumps(FLOW))
    edited["nodes"][0]["spec"]["task"] = "do a DIFFERENT thing"
    h2 = build(tmp_path / "second", edited, git_repo)
    h2.engine.run()
    lines, out = _capture()
    assert explain_node(h2.run_dir, "a", against=h1.run_dir, out=out) == EXIT_OK
    text = "\n".join(lines)
    assert "prompt: changed" in text
    assert "config" not in [l.strip().split(":")[0] for l in lines if "changed" in l]


def test_explain_rejects_unknown_node_and_missing_run(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    lines, out = _capture()
    assert explain_node(h.run_dir, "nope", out=out) == EXIT_CONFIG
    assert explain_node(tmp_path / "not-a-run", "a", out=out) == EXIT_CONFIG


def test_recompose_pin_recorded_parts_agree_with_the_hash(tmp_path, git_repo):
    """The recorder must digest the same list compose_hash consumed: plan the
    node exactly as the engine does and recompose."""
    h = build(tmp_path, FLOW, git_repo)
    h.engine.run()
    state = load_state(h.run_dir)
    rec = state.nodes["a"]
    node = h.tg.node("a")
    ctx = h.engine._render_ctx(node, h.store.phase_dir("a"))
    work = h.fake.plan(node, ctx)
    assert compose_hash(node.role, node.kind, node.contract, work.fingerprint_parts) == rec.input_hash
    assert label_parts(work.fingerprint_parts, work.meta.get("hash_detail")) == rec.hash_parts
