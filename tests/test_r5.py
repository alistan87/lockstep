"""AMENDMENTS-r5 test deltas: per-stanza digests (B1), kind-level default
retry (B2), provider-limit diagnosis (B3), shell attempt rotation (A4)."""

from __future__ import annotations

import json

from lockstep.executors.harness import HarnessExecutor, diagnose_provider_error
from lockstep.protocols import RenderCtx
from lockstep.registry import ExecutorStanza, LockstepConfig
from lockstep.state import load_state
from lockstep.taskgraph import Node, RetrySpec

from conftest import PY, build, make_config


def _ctx(tmp_path, default: str) -> RenderCtx:
    return RenderCtx(
        args={}, outputs={}, json_results={}, skipped=set(), deps=[],
        repo_root=tmp_path, personas_dir=tmp_path / "personas",
        phase_dir=tmp_path / "ph", max_interp_chars=20000,
        config_digest="whole-file-digest", executor_default=default,
    )


def _config(**stanzas) -> LockstepConfig:
    cfg = LockstepConfig(default=next(iter(stanzas)), executors=dict(stanzas))
    cfg.digest = "whole-file-digest"
    return cfg


class TestPerStanzaDigest:
    NODE = Node(id="n", kind="harness", spec={"task": "t"}, output="text")

    def _parts(self, tmp_path, cfg: LockstepConfig) -> list[str]:
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        return ex.plan(self.NODE, _ctx(tmp_path, cfg.default)).fingerprint_parts

    def test_unrelated_stanza_edit_does_not_invalidate(self, tmp_path):
        a = ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"])
        cfg1 = _config(mine=a, other=ExecutorStanza(argv=["x", "{prompt}"]))
        cfg2 = _config(mine=a, other=ExecutorStanza(argv=["y", "--changed", "{prompt}"]))
        assert self._parts(tmp_path, cfg1) == self._parts(tmp_path, cfg2)

    def test_own_stanza_edit_invalidates(self, tmp_path):
        cfg1 = _config(mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"]))
        cfg2 = _config(mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}", "--model", "new"]))
        assert self._parts(tmp_path, cfg1) != self._parts(tmp_path, cfg2)


class TestDefaultRetry:
    def test_harness_declares_minute_scale_default(self):
        assert HarnessExecutor.default_retry == RetrySpec(max=2, backoff_ms=60000, factor=2.0)

    def test_resolution_rules(self, tmp_path):
        from lockstep.roles import Engine

        ex = HarnessExecutor(config=make_config(), repo_root=tmp_path)
        implicit = Node(id="a", kind="harness", spec={"task": "t"})
        assert Engine._effective_retry(implicit, ex) == HarnessExecutor.default_retry
        explicit_zero = Node.model_validate(
            {"id": "b", "kind": "harness", "spec": {"task": "t"}, "retry": {"max": 0}}
        )
        assert Engine._effective_retry(explicit_zero, ex).max == 0

    def test_executor_default_used_in_engine(self, tmp_path, git_repo):
        f = {
            "name": "r5-retry",
            "nodes": [
                {"id": "n", "kind": "fake", "spec": {"exit_code": 1, "outputs": ["x"]}, "final": True}
            ],
        }
        h = build(tmp_path, f, git_repo)
        h.fake.default_retry = RetrySpec(max=1, backoff_ms=10)
        assert h.engine.run() == 3
        assert load_state(h.run_dir).nodes["n"].attempts == 2, "default_retry applied"

        f["nodes"][0]["retry"] = {"max": 0}
        h2 = build(tmp_path, f, git_repo)
        h2.fake.default_retry = RetrySpec(max=1, backoff_ms=10)
        assert h2.engine.run() == 3
        assert load_state(h2.run_dir).nodes["n"].attempts == 1, "explicit retry overrides"


ENVELOPE_529 = (
    "import json, sys; "
    "print(json.dumps({'is_error': True, 'api_error_status': 529, "
    "'result': 'API Error: 529 Overloaded. This is a server-side issue.'})); "
    "sys.exit(1)"
)


class TestProviderDiagnosis:
    def test_diagnose_recognizes_signals(self):
        env = json.dumps({"api_error_status": 529, "result": "overloaded"})
        assert "529" in diagnose_provider_error(env)
        env2 = json.dumps({"api_error_status": None, "result": "You've hit your session limit"})
        assert "session limit" in diagnose_provider_error(env2)
        assert diagnose_provider_error(json.dumps({"result": "all fine"})) is None
        assert diagnose_provider_error("not json at all") is None

    def test_failed_spawn_names_the_limit_and_hints_resume(self, tmp_path, git_repo):
        config = make_config(x=ExecutorStanza(argv=[PY, "-c", ENVELOPE_529]))
        f = {
            "name": "r5-529",
            "nodes": [
                {"id": "n", "kind": "harness", "spec": {"task": "t"},
                 "retry": {"max": 0}, "final": True}
            ],
        }
        h = build(tmp_path, f, git_repo, config=config)
        assert h.engine.run() == 3
        rec = load_state(h.run_dir).nodes["n"]
        assert "provider limit/overload (529)" in rec.error
        assert any("lockstep resume" in line for line in h.logs)


def test_shell_attempts_rotate(tmp_path, git_repo):
    f = {
        "name": "r5-rotate",
        "nodes": [
            {"id": "s", "kind": "shell", "retry": {"max": 1, "backoff_ms": 10},
             "spec": {"cmd": [PY, "-c", "import sys; print('try'); sys.exit(1)"]}, "final": True}
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    phase = h.run_dir / "phases" / "s"
    assert (phase / "stdout-attempt1.log").exists(), "prior shell attempt preserved"
    assert (phase / "stdout.log").exists()
