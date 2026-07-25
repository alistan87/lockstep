"""Test 3 (SPEC §13.1): result channel and contracts — file first, stdout
fallback, json_field unwrapping, exactly one output-only corrective re-spawn."""

from __future__ import annotations

import json

from lockstep.registry import ExecutorStanza
from lockstep.state import load_state

from conftest import PY, build, make_config

WRITE_RESULT_FILE = (
    "import os, json, pathlib; d = os.environ['LOCKSTEP_PHASE_DIR']; "
    "pathlib.Path(d, 'result.json').write_text(json.dumps({'step_id': 'from-file', "
    "'status': 'done', 'files_written': []})); "
    "print(json.dumps({'step_id': 'from-stdout', 'status': 'done', 'files_written': []}))"
)

STDOUT_ENVELOPE = (
    "import json; print('chatty harness preamble'); "
    "print(json.dumps({'result': {'step_id': 'unwrapped', 'status': 'done', 'files_written': []}}))"
)


def harness_flow(script: str) -> dict:
    return {
        "name": "hf",
        "nodes": [
            {
                "id": "n", "kind": "harness",
                "spec": {"task": "do the thing"},
                "output": "json", "contract": "StepResult", "final": True,
            }
        ],
    }


def test_result_file_preferred_over_stdout(tmp_path, git_repo):
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", WRITE_RESULT_FILE]))
    h = build(tmp_path, harness_flow(WRITE_RESULT_FILE), git_repo, config=config)
    assert h.engine.run() == 0
    result = json.loads(open(load_state(h.run_dir).nodes["n"].result_path, encoding="utf-8").read())
    assert result["step_id"] == "from-file"


def test_stdout_fallback_with_json_field_unwrap(tmp_path, git_repo):
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", STDOUT_ENVELOPE], json_field="result"))
    h = build(tmp_path, harness_flow(STDOUT_ENVELOPE), git_repo, config=config)
    assert h.engine.run() == 0
    result = json.loads(open(load_state(h.run_dir).nodes["n"].result_path, encoding="utf-8").read())
    assert result["step_id"] == "unwrapped"


def test_schema_failure_one_corrective_respawn_then_ok(tmp_path, git_repo):
    f = {
        "name": "corr",
        "nodes": [
            {
                "id": "n", "kind": "fake",
                "spec": {"outputs": ["this is not json", {"step_id": "x", "status": "done", "files_written": []}]},
                "output": "json", "contract": "StepResult", "final": True,
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    assert len(h.fake.calls) == 2
    retry = h.fake.calls[1]
    assert retry.corrective
    # Writing nodes: the retry must not repeat side effects — assert the wording.
    assert "Do NOT modify, create, or delete any file" in retry.prompt
    assert "describing what you already did" in retry.prompt


def test_readonly_corrective_wording_differs(tmp_path, git_repo):
    f = {
        "name": "corr-ro",
        "nodes": [
            {
                "id": "n", "kind": "fake",
                "spec": {
                    "readonly": True,
                    "outputs": ["nope", {"step_id": "x", "status": "done", "files_written": []}],
                },
                "output": "json", "contract": "StepResult", "final": True,
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    retry = h.fake.calls[1]
    # A reviewer must NOT be told "your files are already written" (SPEC §9.3).
    assert "your previous analysis" in retry.prompt
    assert "files are already written" not in retry.prompt


def test_second_schema_failure_fails_node(tmp_path, git_repo):
    f = {
        "name": "corr2",
        "nodes": [
            {
                "id": "n", "kind": "fake",
                "spec": {"outputs": ["bad", "still bad"]},
                "output": "json", "contract": "StepResult", "final": True,
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3  # node failed after retries
    assert len(h.fake.calls) == 2, "exactly ONE corrective re-spawn"
    rec = load_state(h.run_dir).nodes["n"]
    assert rec.status == "failed"
    assert "twice" in rec.error


PROSE_THEN_FENCED_JSON = (
    "import json; "
    "inner = 'Based on my audit, everything checks out.\\n\\n```json\\n"
    '[{\\"severity\\": \\"nit\\", \\"category\\": \\"c\\", \\"file\\": \\"f\\", '
    '\\"claim\\": \\"x\\", \\"evidence\\": \\"e\\"}]\\n```'
    "'; "
    "print(json.dumps({'result': inner}))"
)


def test_json_extracted_from_unwrapped_prose(tmp_path, git_repo):
    # §8.3: last balanced JSON AFTER json_field unwrapping — a model that
    # narrates and ends with a fenced JSON block still yields its JSON.
    config = make_config(x=ExecutorStanza(argv=[PY, "-c", PROSE_THEN_FENCED_JSON], json_field="result"))
    f = {
        "name": "prose",
        "nodes": [
            {"id": "n", "kind": "harness", "spec": {"task": "audit"},
             "output": "json", "contract": "Finding[]", "final": True}
        ],
    }
    h = build(tmp_path, f, git_repo, config=config)
    assert h.engine.run() == 0
    result = json.loads(open(load_state(h.run_dir).nodes["n"].result_path, encoding="utf-8").read())
    assert result[0]["severity"] == "nit"


def test_shell_json_extracted_from_chatty_stdout(tmp_path, git_repo):
    script = (
        "import json; print('warning: something benign'); "
        "print(json.dumps({'command': 'x', 'exit_code': 0, 'summary': 'ok'}))"
    )
    f = {
        "name": "chatty",
        "nodes": [
            {"id": "s", "kind": "shell", "spec": {"cmd": [PY, "-c", script]},
             "output": "json", "contract": "CheckResult", "final": True}
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 0
    result = json.loads(open(load_state(h.run_dir).nodes["s"].result_path, encoding="utf-8").read())
    assert result["summary"] == "ok"


def test_empty_result_gets_one_automatic_retry(tmp_path, git_repo):
    f = {
        "name": "empty",
        "nodes": [{"id": "n", "kind": "fake", "spec": {"empty_result": True}, "final": True}],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    # M4: one automatic retry on empty result, additive, even with retry.max == 0.
    assert len(h.fake.calls) == 2
