"""Throughput-parity C1 (PROPOSAL-throughput-and-harness-parity §4):
schema pass-through — `schema_argv` on a stanza appends a schema-bearing
argv fragment when the node has `output: "json"` and a resolvable contract.

The two review corrections pinned here: `{schema}` is the schema of the
CONTRACT, not the model — a `Name[]` contract yields
{"type": "array", "items": <model schema>}, or the flag would GUARANTEE the
corrective re-spawn it exists to kill on every reviewer node (F-S5); and
the filled schema is its own fingerprint part (`schema:<compact-json>`),
skipped symmetrically with the flag when the schema cannot be produced
(F-S6)."""

from __future__ import annotations

import json

from lockstep.executors.harness import HarnessExecutor
from lockstep.protocols import RenderCtx
from lockstep.registry import ExecutorStanza, LockstepConfig
from lockstep.taskgraph import Node, TaskGraph, lint_flow

from conftest import PY


def _ctx(tmp_path, default: str) -> RenderCtx:
    return RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "personas",
        phase_dir=tmp_path / "ph", max_interp_chars=20000,
        config_digest="d", executor_default=default,
    )


def _config(**stanzas) -> LockstepConfig:
    cfg = LockstepConfig(default=next(iter(stanzas)), executors=dict(stanzas))
    cfg.digest = "d"
    return cfg


def _plan(tmp_path, cfg, node):
    ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
    return ex.plan(node, _ctx(tmp_path, cfg.default))


SCHEMA_STANZA = dict(argv=[PY, "-c", "pass", "{prompt}"],
                     schema_argv=["--json-schema", "{schema}"])


class TestPlan:
    def test_array_contract_yields_array_schema(self, tmp_path):
        # F-S5, the pinned test: the flagship reviewer nodes are Finding[].
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Finding[]")
        work = _plan(tmp_path, _config(mine=ExecutorStanza(**SCHEMA_STANZA)), node)
        schema = json.loads(work.meta["schema_json"])
        assert schema["type"] == "array"
        assert "properties" in schema["items"]

    def test_object_contract_yields_object_schema(self, tmp_path):
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Verdict")
        work = _plan(tmp_path, _config(mine=ExecutorStanza(**SCHEMA_STANZA)), node)
        schema = json.loads(work.meta["schema_json"])
        assert schema.get("type") != "array" and "properties" in schema

    def test_schema_fingerprint_part_present_and_placeholder_hashed(self, tmp_path):
        # The argv part keeps the {schema} placeholder (like {prompt}); the
        # filled value is its own part, so a Field-constraint edit that moves
        # the schema re-bills even though the template did not change (F-S6).
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Verdict")
        work = _plan(tmp_path, _config(mine=ExecutorStanza(**SCHEMA_STANZA)), node)
        schema_parts = [p for p in work.fingerprint_parts if p.startswith("schema:")]
        assert len(schema_parts) == 1
        assert schema_parts[0] == f"schema:{work.meta['schema_json']}"
        argv_part = next(p for p in work.fingerprint_parts if p.startswith("argv:"))
        assert "{schema}" in argv_part

    def test_stanza_without_schema_argv_is_byte_identical(self, tmp_path):
        # M3 additivity: the feature contributes NOTHING where it is not
        # configured.
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Verdict")
        plain = _plan(tmp_path, _config(
            mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"])), node)
        assert not any(p.startswith("schema:") for p in plain.fingerprint_parts)
        assert plain.meta.get("schema_json") is None

    def test_text_node_untouched(self, tmp_path):
        node = Node(id="n", kind="harness", spec={"task": "t"}, output="text")
        work = _plan(tmp_path, _config(mine=ExecutorStanza(**SCHEMA_STANZA)), node)
        assert work.meta.get("schema_json") is None
        assert "--json-schema" not in work.meta["argv_template"]

    def test_unresolvable_contract_skips_flag_and_part_symmetrically(self, tmp_path):
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="NoSuchContract")
        work = _plan(tmp_path, _config(mine=ExecutorStanza(**SCHEMA_STANZA)), node)
        assert work.meta.get("schema_json") is None
        assert "--json-schema" not in work.meta["argv_template"]
        assert not any(p.startswith("schema:") for p in work.fingerprint_parts)


# argv layout at spawn: [-c, <prompt>, <schema or file>, <phase_dir>] —
# the {prompt} placeholder in the stanza argv fills argv[1].
ECHO_SCHEMA = (
    "import sys, json, pathlib\n"
    "pathlib.Path(sys.argv[3], 'result.json')"
    ".write_text(json.dumps({'got': sys.argv[2]}))\n"
)


class TestExecute:
    def _run(self, tmp_path, stanza):
        cfg = _config(mine=stanza)
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Verdict")
        work = ex.plan(node, _ctx(tmp_path, "mine"))
        phase = tmp_path / "ph"
        phase.mkdir(exist_ok=True)
        return work, ex.execute(work, phase, 60), phase

    def test_schema_expanded_into_argv_at_execute(self, tmp_path):
        stanza = ExecutorStanza(
            argv=[PY, "-c", ECHO_SCHEMA, "{prompt}"],
            schema_argv=["{schema}", "{phase_dir}"])
        work, raw, _ = self._run(tmp_path, stanza)
        got = json.loads(raw.result_text)["got"]
        assert got == work.meta["schema_json"]  # the literal filled schema

    def test_schema_file_written_to_phase_dir(self, tmp_path):
        stanza = ExecutorStanza(
            argv=[PY, "-c", ECHO_SCHEMA, "{prompt}"],
            schema_argv=["{schema_file}", "{phase_dir}"])
        work, raw, phase = self._run(tmp_path, stanza)
        path = json.loads(raw.result_text)["got"]
        assert path == str((phase / "contract-schema.json").resolve())
        assert (phase / "contract-schema.json").read_text(
            encoding="utf-8") == work.meta["schema_json"]


def test_lint_schema_argv_on_argv_prompting_stanza():
    flow = TaskGraph.model_validate({
        "name": "lint-schema",
        "nodes": [{"id": "n", "kind": "harness", "final": True,
                   "output": "json", "contract": "Verdict",
                   "spec": {"task": "x"}}],
    })
    argv_cfg = _config(c=ExecutorStanza(
        argv=["c", "{prompt}"], prompt_via="argv",
        schema_argv=["--json-schema", "{schema}"]))
    codes = [w.code for w in lint_flow(flow, argv_cfg)]
    assert "lint-schema-argv" in codes
    stdin_cfg = _config(c=ExecutorStanza(
        argv=["c"], prompt_via="stdin", schema_argv=["--json-schema", "{schema}"]))
    codes = [w.code for w in lint_flow(flow, stdin_cfg)]
    assert "lint-schema-argv" not in codes


class TestPlaceholderInjection:
    def test_prompt_data_cannot_inject_schema_placeholder(self, tmp_path):
        """Interpolated values are DATA (§7): an upstream output or arg
        containing the literal "{schema}" lands in the prompt at plan time
        and must reach the spawn verbatim — schema placeholders expand on
        the TEMPLATE, before the prompt is substituted in, never on text the
        prompt brought with it. (A literal {schema} typed in a task is
        already rejected by the interpolation grammar; the data path is the
        one that cannot be.)"""
        echo_both = (
            "import sys, json, pathlib\n"
            "pathlib.Path(sys.argv[3], 'result.json').write_text("
            "json.dumps({'prompt': sys.argv[1], 'schema': sys.argv[2]}))\n"
        )
        stanza = ExecutorStanza(
            argv=[PY, "-c", echo_both, "{prompt}"],
            schema_argv=["{schema}", "{phase_dir}"])
        cfg = _config(mine=stanza)
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        node = Node(id="n", kind="harness",
                    spec={"task": "handle this data: {args.blob}"},
                    output="json", contract="Verdict")
        ctx = RenderCtx(
            args={"blob": "upstream data mentioning {schema} literally"},
            outputs={}, json_results={}, skipped=set(), deps=[],
            repo_root=tmp_path, personas_dir=tmp_path / "personas",
            phase_dir=tmp_path / "ph", max_interp_chars=20000,
            config_digest="d", executor_default="mine",
        )
        work = ex.plan(node, ctx)
        assert "{schema}" in str(work.render), "the vector requires the data in the prompt"
        phase = tmp_path / "ph"
        phase.mkdir(exist_ok=True)
        raw = ex.execute(work, phase, 60)
        got = json.loads(raw.result_text)
        assert "{schema}" in got["prompt"], "prompt data was rewritten"
        assert got["schema"] == work.meta["schema_json"]

    def test_schema_file_refreshed_when_contract_changes(self, tmp_path):
        """contract-schema.json is not in the execute-start rotation list, so
        it must be written unconditionally — an exists-guard would hand a
        re-plan after a contract edit the STALE schema while the input hash
        said otherwise."""
        stanza = ExecutorStanza(
            argv=[PY, "-c", ECHO_SCHEMA, "{prompt}"],
            schema_argv=["{schema_file}", "{phase_dir}"])
        cfg = _config(mine=stanza)
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        phase = tmp_path / "ph"
        phase.mkdir(exist_ok=True)
        for contract in ("Verdict", "Finding[]"):
            node = Node(id="n", kind="harness", spec={"task": "t"},
                        output="json", contract=contract)
            work = ex.plan(node, _ctx(tmp_path, "mine"))
            ex.execute(work, phase, 60)
            assert (phase / "contract-schema.json").read_text(
                encoding="utf-8") == work.meta["schema_json"]

    def test_schema_bytes_cannot_inject_prompt_placeholder(self, tmp_path):
        """The reverse direction (round-2 finding 5): a contract schema whose
        Field description contains the literal "{prompt}" must reach argv
        verbatim — single-pass expansion never re-scans a replacement's
        output, so spawned argv cannot diverge from the hashed schema: part."""
        echo_both = (
            "import sys, json, pathlib\n"
            "pathlib.Path(sys.argv[3], 'result.json').write_text("
            "json.dumps({'prompt': sys.argv[1], 'schema': sys.argv[2]}))\n"
        )
        stanza = ExecutorStanza(
            argv=[PY, "-c", echo_both, "{prompt}"],
            schema_argv=["{schema}", "{phase_dir}"])
        cfg = _config(mine=stanza)
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        node = Node(id="n", kind="harness", spec={"task": "t"},
                    output="json", contract="Verdict")
        work = ex.plan(node, _ctx(tmp_path, "mine"))
        # Simulate the hostile schema (a project-owned model COULD carry this
        # in a Field description) without needing a custom contracts module.
        hostile = '{"description": "fill like {prompt} and {phase_dir}", "type": "object"}'
        work.meta["schema_json"] = hostile
        phase = tmp_path / "ph"
        phase.mkdir(exist_ok=True)
        raw = ex.execute(work, phase, 60)
        got = json.loads(raw.result_text)
        assert got["schema"] == hostile, "schema bytes were rewritten at spawn"
