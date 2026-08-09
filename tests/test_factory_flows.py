"""D1/D5/D6 — the factory flows: statically clean, contracts resolvable, and
their deterministic tooling (collectors, build smoke) behaving."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

from lockstep.contracts import Verdict, resolve_contract
from lockstep.executors.fake import FakeExecutor
from lockstep.executors.harness import HarnessExecutor
from lockstep.executors.shell import ShellExecutor
from lockstep.policy import AllowAllPolicy
from lockstep.registry import ExecutorStanza, LockstepConfig, Registry
from lockstep.taskgraph import lint_flow, load_flow, verify_flow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIB = REPO_ROOT / "contrib"
FACTORY_FLOWS = sorted((REPO_ROOT / "flows" / "factory").glob("*.tg.json"))


def _load_contrib(name: str):
    spec = importlib.util.spec_from_file_location(name, CONTRIB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verify_setup():
    config = LockstepConfig(
        default="probe",
        executors={
            "probe": ExecutorStanza(
                argv=["probe", "{prompt}"],
                readonly_argv=["--readonly"],
            )
        },
    )
    reg = Registry()
    reg.register(FakeExecutor(repo_root=REPO_ROOT))
    reg.register(ShellExecutor(repo_root=REPO_ROOT))
    reg.register(HarnessExecutor(config=config, repo_root=REPO_ROOT))
    return config, reg


def test_factory_flows_exist():
    names = [p.name for p in FACTORY_FLOWS]
    assert "release-cut.tg.json" in names
    assert "research-report.tg.json" in names
    assert "status-digest.tg.json" in names


def test_factory_flows_verify_clean():
    config, reg = _verify_setup()
    for path in FACTORY_FLOWS:
        tg, _ = load_flow(path)
        issues = verify_flow(
            tg, registry=reg, config=config, repo_root=REPO_ROOT, policy=AllowAllPolicy()
        )
        errors = [i for i in issues if i.level == "error"]
        assert not errors, f"{path.name}: {[str(e) for e in errors]}"


def test_factory_flows_lint_clean():
    """The flows this programme ships must satisfy its own lint."""
    config, _ = _verify_setup()
    for path in FACTORY_FLOWS:
        tg, _ = load_flow(path)
        lints = [i for i in lint_flow(tg, config) if i.code != "lint-argv-prompt"]
        assert not lints, f"{path.name}: {[str(w) for w in lints]}"


def test_factory_contracts_resolve():
    module = "flows/factory_contracts.py"
    for name in ("SourceManifest", "SourceNote", "Outline", "ChangeOrder",
                 "TriageRecord", "ScoreCard"):
        ref = resolve_contract(name, module)
        assert ref.model.__name__ == name


# ------------------------------------------------------------- collectors


collectors = _load_contrib("collectors")


def test_collect_sources_fingerprints_and_ids(tmp_path):
    (tmp_path / "b.md").write_text("beta", encoding="utf-8")
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "skip.bin").write_text("x", encoding="utf-8")
    out = collectors.collect_sources(tmp_path, [".md"])
    ids = [s["id"] for s in out["sources"]]
    assert ids == ["S1", "S2"]
    assert all(len(s["fingerprint"]) == 16 for s in out["sources"])
    before = out["sources"][0]["fingerprint"]
    (tmp_path / "a.md").write_text("alpha CHANGED", encoding="utf-8")
    after = collectors.collect_sources(tmp_path, [".md"])["sources"][0]["fingerprint"]
    assert before != after, "the fingerprint is what invalidates per-item caching"


def test_collect_runs_reads_states(tmp_path):
    d = tmp_path / "runs" / "x-1"
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "flow_name": "f", "started_at": "t", "token_spawns": 3,
        "nodes": {"a": {"status": "done"}, "b": {"status": "failed"}},
    }), encoding="utf-8")
    out = collectors.collect_runs(tmp_path / "runs")
    assert out["count"] == 1
    assert out["runs"][0]["agent_spawns"] == 3
    assert out["runs"][0]["failed"] == 1


def test_collect_git_log_in_a_fresh_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        out = collectors.collect_git_log(days=7, since_last_tag=False)
    finally:
        os.chdir(cwd)
    assert out["count"] == 0 and out["last_tag"] is None


def test_collect_grep_fingerprints_matches_only(tmp_path):
    (tmp_path / "hit.py").write_text("import legacy_api\n", encoding="utf-8")
    (tmp_path / "miss.py").write_text("import shiny_api\n", encoding="utf-8")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "trap.py").write_text("import legacy_api\n", encoding="utf-8")
    out = collectors.collect_grep(r"legacy_api", [".py"], tmp_path)
    assert [s["path"] for s in out["sources"]] == ["hit.py"]


def test_collect_grep_ignores_the_roots_own_ancestry(tmp_path):
    """Exclusions apply UNDER the root: a repo living inside a directory named
    'runs' must not silently discover zero sites."""
    root = tmp_path / "runs" / "myrepo"
    root.mkdir(parents=True)
    (root / "hit.py").write_text("import legacy_api\n", encoding="utf-8")
    out = collectors.collect_grep(r"legacy_api", [".py"], root)
    assert [s["path"] for s in out["sources"]] == ["hit.py"]


def test_collect_run_facts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({
        "flow_name": "f", "flow_hash": "h", "format_version": "1.0", "args": {},
        "started_at": "t", "token_spawns": 4,
        "verdicts": {"gate": "block: tests red"},
        "nodes": {"a": {"node_id": "a", "role": "work", "kind": "fake",
                        "status": "failed", "attempts": 2, "error": "boom"}},
    }), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    facts = collectors.collect_run_facts(run_dir)
    assert facts["nodes"]["a"]["error"] == "boom"
    assert facts["verdicts"] == {"gate": "block: tests red"}
    assert facts["trace"]["ok"] is True
    assert "state.json" in facts["artifacts"]


save_result = _load_contrib("save_result")


def test_save_result_publishes_and_filters(tmp_path, capsys, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "phases" / "propose").mkdir(parents=True)
    (run_dir / "phases" / "emit").mkdir()
    orders = [
        {"file": "a.py", "change": "do x"},
        {"file": "b.py", "change": ""},
    ]
    (run_dir / "phases" / "propose" / "result.json").write_text(
        json.dumps(orders), encoding="utf-8"
    )
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run_dir / "phases" / "emit"))
    monkeypatch.chdir(tmp_path)
    code = save_result.main(["--node", "propose", "--out", "orders.json",
                             "--drop-empty", "change"])
    assert code == 0
    kept = json.loads((tmp_path / "orders.json").read_text(encoding="utf-8"))
    assert [o["file"] for o in kept] == ["a.py"]
    assert "1 empty entry dropped" in capsys.readouterr().out


bakeoff_gen = _load_contrib("bakeoff_gen")


def test_bakeoff_dedups_colliding_stanza_slugs(tmp_path):
    toml = tmp_path / "lockstep.toml"
    toml.write_text(
        '[executors.My_Agent]\nargv = ["a", "{prompt}"]\n'
        '[executors.my-agent]\nargv = ["b", "{prompt}"]\n',
        encoding="utf-8",
    )
    flow = bakeoff_gen.generate(toml, ["say hi"])
    ids = [n["id"] for n in flow["nodes"]]
    assert len(ids) == len(set(ids)), "legal stanza names must never collide into one id"
    import re
    for i in ids:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", i), i


def test_bakeoff_generates_a_verifiable_flow(tmp_path):
    toml = tmp_path / "lockstep.toml"
    toml.write_text(
        'default = "one"\n'
        '[executors.one]\nargv = ["one", "{prompt}"]\n'
        'readonly_argv = ["--ro"]\n'
        '[executors.two]\nargv = ["two", "{prompt}"]\n',
        encoding="utf-8",
    )
    flow = bakeoff_gen.generate(toml, ["say hi", "say bye"])
    from lockstep.registry import load_config
    from lockstep.taskgraph import TaskGraph

    tg = TaskGraph.model_validate(flow)
    assert len([n for n in tg.nodes if n.id.startswith("run-")]) == 4
    assert tg.node("run-one-t1").spec.get("readonly") is True
    assert "readonly" not in tg.node("run-two-t1").spec
    config = load_config(toml)
    _, reg = _verify_setup()
    from lockstep.executors.harness import HarnessExecutor
    reg.register(HarnessExecutor(config=config, repo_root=REPO_ROOT))
    issues = verify_flow(tg, registry=reg, config=config, repo_root=REPO_ROOT,
                         policy=AllowAllPolicy())
    errors = [i for i in issues if i.level == "error"]
    assert not errors, [str(e) for e in errors]


# ------------------------------------------------------------- build smoke


build_smoke = _load_contrib("build_smoke")


def test_build_smoke_blocks_fast_when_there_is_nothing_to_build(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no pyproject: pip wheel fails, venv never starts
    assert build_smoke.main([]) == 0
    v = json.loads(capsys.readouterr().out.strip())
    Verdict.model_validate(v)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "build"


def test_save_result_strips_a_fence_around_the_whole_result(tmp_path, capsys, monkeypatch):
    """Small local models wrap source in a ``` fence however plainly the prompt
    forbids it, and the alternative is a correctness gate that blocks forever on
    a formatting detail instead of on correctness."""
    phase = tmp_path / "phases" / "gate"
    (tmp_path / "phases" / "core").mkdir(parents=True)
    phase.mkdir(parents=True)
    (tmp_path / "phases" / "core" / "result.txt").write_text(
        "```python\ndef solve(grid):\n    return []\n```\n", encoding="utf-8")
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(phase))
    monkeypatch.chdir(tmp_path)

    assert save_result.main(["--node", "core", "--out", "core.py", "--strip-fence"]) == 0
    assert (tmp_path / "core.py").read_text(encoding="utf-8") == "def solve(grid):\n    return []\n"
    assert "unwrapped a ``` fence" in capsys.readouterr().out


def test_save_result_leaves_a_partial_fence_alone(tmp_path, capsys, monkeypatch):
    """A partial unwrap is a corruption a later gate reports as a syntax error
    with no clue where it came from."""
    phase = tmp_path / "phases" / "gate"
    (tmp_path / "phases" / "core").mkdir(parents=True)
    phase.mkdir(parents=True)
    body = "here is the code\n```python\nx = 1\n```\nhope that helps\n"
    (tmp_path / "phases" / "core" / "result.txt").write_text(body, encoding="utf-8")
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(phase))
    monkeypatch.chdir(tmp_path)

    assert save_result.main(["--node", "core", "--out", "core.py", "--strip-fence"]) == 0
    assert (tmp_path / "core.py").read_text(encoding="utf-8") == body
    assert "unwrapped" not in capsys.readouterr().out
