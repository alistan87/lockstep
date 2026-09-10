"""Throughput-parity A1/A2 (PROPOSAL-throughput-and-harness-parity §2):
stanza-digest frozen-field canonicalization with the scheduling carve-out,
and its first consumer — per-stanza `default_retry`.

The claim A1 must keep true forever: adding a field to ExecutorStanza does
NOT change the digest of a stanza that never sets it. The recorded-digest
test is that claim against the shipped example file; the byte-identity test
is the same claim against the pre-A1 formula itself."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lockstep.executors.harness import HarnessExecutor, stanza_digest
from lockstep.protocols import RenderCtx
from lockstep.registry import (
    SCHEDULING_FIELDS,
    V1_DIGEST_FIELDS,
    ExecutorStanza,
    LockstepConfig,
    load_config,
)
from lockstep.roles import Engine
from lockstep.taskgraph import Node, RetrySpec

from conftest import PY

EXAMPLE = Path(__file__).resolve().parents[1] / "lockstep.toml.example"

# Digests of every stanza in lockstep.toml.example, recorded under the
# pre-A1 code (2026-09-09). A1's canonicalization must reproduce these
# byte-for-byte — if this test fails, an upgrade just re-billed every cached
# harness node, which is the exact class of invalidation r5 B1 exists to kill.
RECORDED = {
    # claude-code moved 2026-09-09 when B1 added --exclude-dynamic-system-
    # prompt-sections to its argv — a DELIBERATE one-time re-bill (argv is
    # hashed by design); claude-code-resilient is B2, new the same day.
    "claude-code": "1d7bf5a51c60a9e1e51154d182d548e254ab0832beb8779cc398d07c780f58b4",
    "claude-code-resilient": "a02b9a623ac5f41843f5e754d95e43541c8f08cc987eb39579dfc90e74200957",
    "copilot-cli": "b9e3e42caca5cd483b011292965882cd7e71b32be5e196c75984e6928ebabd1c",
    "local-best": "9ef94850d9f1cb3ba33a665eb55e2e420fd5c775781c2135586db437cef8ba67",
    "local-coder": "8f8459854d19e533ac3700f21c5a22dadfa406879178e1b05ed846b5606bdf8e",
    "local-coder-big": "0f0f2dd9dac76f6a86eea99f41434de61a58d5e34c1edc79093406012c66d323",
    "local-fast": "07307425ce3c0ac6401e7c96fe6944a057254cad584a05b1092e5e5afe96cfe9",
    "local-mid": "f4051e18c23872f0b20588f110ff5f337e5aa7e3023c73def1f59c3fa11037df",
    "local-smart": "a6f849daed0dd135442643ab6268c07d65f0774280357e4e80a7697be51037a5",
    "local-strong": "ada486b24442b2ec128dc86fce2b26fee01255cad091f90b5e2c88d5bdfde3af",
    "pi": "7b8a6f54b01de6908799916f60d882270284e7632c5a588f724c96e280de76e1",
    "pi-coder": "bb0923c1807bd2349b8456eb9a420fb6817cb67371a66f5e59fc8ebff077e789",
    "pi-guarded": "ed782de5545a7b58833bcb8200e268e441fff8dbe7d6ecc182c36c74729da7be",
    "pi-reasoner": "0111000a4694f95d6b59b39bc599326bd446994a286d93759f90358476f1a1f5",
    # pi-review moved 2026-09-09 when D added --mode json + envelope =
    # "pi-stream" — a deliberate one-time re-bill for the telemetry it buys.
    "pi-review": "c8852d6b42d7f2cc194344a8d5cc37c22869ab66aa79489bafdf5630b9cbba0f",
}


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


def _plan(tmp_path, cfg: LockstepConfig, node: Node):
    ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
    return ex.plan(node, _ctx(tmp_path, cfg.default))


class TestA1Canonicalization:
    def test_example_stanza_digests_unchanged(self):
        cfg = load_config(EXAMPLE)
        got = {name: stanza_digest(name, s) for name, s in cfg.executors.items()}
        assert got == RECORDED

    def test_byte_identical_to_v1_formula(self):
        """A stanza that sets nothing beyond the v1 fields digests exactly as
        the pre-A1 code did: json.dumps(model_dump(), sort_keys=True) over
        the five v1 fields."""
        s = ExecutorStanza(argv=["x", "{prompt}"], json_field="result")
        v1_dump = {k: s.model_dump()[k] for k in V1_DIGEST_FIELDS}
        legacy = hashlib.sha256(
            ("n\x00" + json.dumps(v1_dump, sort_keys=True, ensure_ascii=False)).encode("utf-8")
        ).hexdigest()
        assert stanza_digest("n", s) == legacy

    def test_scheduling_field_never_hashed(self):
        plain = ExecutorStanza(argv=["x", "{prompt}"])
        scheduled = ExecutorStanza(argv=["x", "{prompt}"], default_retry=RetrySpec(max=0))
        assert stanza_digest("n", plain) == stanza_digest("n", scheduled)

    def test_field_classes_are_disjoint_and_complete(self):
        """The A1 rule with teeth: every ExecutorStanza field is classified —
        v1, scheduling-only, or behaviour-bearing-by-default. A field in two
        classes (or a typo'd name in either list) breaks here, not in a
        digest six weeks later."""
        fields = set(ExecutorStanza.model_fields)
        assert set(V1_DIGEST_FIELDS) <= fields
        assert SCHEDULING_FIELDS <= fields
        assert not set(V1_DIGEST_FIELDS) & SCHEDULING_FIELDS

    def test_setting_default_retry_rebills_nothing(self, tmp_path):
        """The whole point of the carve-out: the fingerprint of a planned
        node is unchanged when its stanza gains a retry default."""
        node = Node(id="n", kind="harness", spec={"task": "t"}, output="text")
        without = _config(mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"]))
        with_retry = _config(
            mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"], default_retry=RetrySpec(max=0))
        )
        assert (
            _plan(tmp_path, without, node).fingerprint_parts
            == _plan(tmp_path, with_retry, node).fingerprint_parts
        )


class TestA2DefaultRetry:
    NODE = Node(id="n", kind="harness", spec={"task": "t"}, output="text")

    def test_plan_carries_stanza_retry_in_meta(self, tmp_path):
        cfg = _config(
            mine=ExecutorStanza(
                argv=[PY, "-c", "pass", "{prompt}"],
                default_retry=RetrySpec(max=0, backoff_ms=100),
            )
        )
        work = _plan(tmp_path, cfg, self.NODE)
        assert work.meta["stanza_default_retry"] == {"max": 0, "backoff_ms": 100, "factor": 2.0}

    def test_plan_meta_none_when_stanza_silent(self, tmp_path):
        cfg = _config(mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"]))
        assert _plan(tmp_path, cfg, self.NODE).meta["stanza_default_retry"] is None

    def test_node_retry_beats_stanza_default(self, tmp_path):
        """Precedence: node `retry` (field present, even {"max": 0}) > stanza
        default_retry > kind default (r5 B2 + the A2 stanza tier)."""
        cfg = _config(
            mine=ExecutorStanza(
                argv=[PY, "-c", "pass", "{prompt}"], default_retry=RetrySpec(max=7)
            )
        )
        node = Node.model_validate(
            {"id": "n", "kind": "harness", "spec": {"task": "t"}, "retry": {"max": 1}}
        )
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        work = ex.plan(node, _ctx(tmp_path, cfg.default))
        assert Engine._effective_retry(node, ex, work).max == 1

    def test_stanza_default_beats_kind_default(self, tmp_path):
        cfg = _config(
            mine=ExecutorStanza(
                argv=[PY, "-c", "pass", "{prompt}"], default_retry=RetrySpec(max=0)
            )
        )
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        work = ex.plan(self.NODE, _ctx(tmp_path, cfg.default))
        assert Engine._effective_retry(self.NODE, ex, work).max == 0
        assert HarnessExecutor.default_retry.max == 2  # and it would have been 2

    def test_kind_default_when_stanza_silent(self, tmp_path):
        cfg = _config(mine=ExecutorStanza(argv=[PY, "-c", "pass", "{prompt}"]))
        ex = HarnessExecutor(config=cfg, repo_root=tmp_path)
        work = ex.plan(self.NODE, _ctx(tmp_path, cfg.default))
        assert Engine._effective_retry(self.NODE, ex, work) == HarnessExecutor.default_retry

    def test_effective_retry_without_work_still_resolves(self, tmp_path):
        """The pre-A2 signature keeps working (r5 tests call it with two
        args); no work object means no stanza tier."""
        from conftest import make_config

        ex = HarnessExecutor(config=make_config(), repo_root=tmp_path)
        assert Engine._effective_retry(self.NODE, ex) == HarnessExecutor.default_retry
