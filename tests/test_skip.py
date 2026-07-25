"""Test 6 (SPEC §13.1): skip propagation, plus the A2 `when` exemption."""

from __future__ import annotations

from lockstep.state import load_state

from conftest import build


def test_false_when_skips_and_propagates(tmp_path, git_repo):
    f = {
        "name": "skips",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": [{"go": False}]}, "output": "json", "contract": "PathManifest"},
        ],
    }
    # PathManifest wouldn't validate {"go": false}; use a plain json-emitting node.
    f = {
        "name": "skips",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": ['{"go": false}']}, "output": "text"},
            {
                "id": "cond", "kind": "fake", "depends_on": ["src"],
                "when": '{steps.src.json.go} == true', "spec": {"outputs": ["ran"]},
            },
            {
                "id": "child", "kind": "fake", "depends_on": ["cond"],
                "spec": {"task": "use {steps.cond.output}", "outputs": ["child"]},
            },
            {
                "id": "opt", "kind": "fake", "depends_on": ["cond"], "optional": True,
                "spec": {"task": "got {steps.cond.output}", "outputs": ["opt-ran"]}, "final": True,
            },
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["cond"].status == "skipped"
    assert st.nodes["child"].status == "skipped", "transitive skip via body reference"
    assert st.nodes["opt"].status == "done", "optional runs with null substituted"
    opt_call = [c for c in h.fake.calls if c.node_id == "opt"][0]
    assert "null" in opt_call.prompt


def test_when_null_fires_on_skipped_upstream_without_optional(tmp_path, git_repo):
    # A2: `when` evaluation is exempt from transitive skip.
    f = {
        "name": "a2",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": ['{"go": false}']}, "output": "text"},
            {"id": "cond", "kind": "fake", "depends_on": ["src"], "when": '{steps.src.json.go} == true', "spec": {}},
            {
                "id": "fallback", "kind": "fake", "depends_on": ["cond"],
                "when": "{steps.cond.json} == null",
                "spec": {"task": "fallback path", "outputs": ["fell back"]}, "final": True,
            },
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["cond"].status == "skipped"
    assert st.nodes["fallback"].status == "done", "when == null must fire on a skipped upstream"


def test_passing_when_does_not_rescue_body_reference(tmp_path, git_repo):
    # A2 case 2: `when` true, but the body references the skipped node => still skipped.
    f = {
        "name": "a2b",
        "nodes": [
            {"id": "src", "kind": "fake", "spec": {"outputs": ['{"go": false}']}, "output": "text"},
            {"id": "cond", "kind": "fake", "depends_on": ["src"], "when": '{steps.src.json.go} == true', "spec": {}},
            {
                "id": "consumer", "kind": "fake", "depends_on": ["cond"],
                "when": "{steps.cond.json} == null",
                "spec": {"task": "use {steps.cond.output}"}, "final": True,
            },
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).nodes["consumer"].status == "skipped"
