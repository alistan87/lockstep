"""A3 — scrubbed fixtures and the zero-token replay suite."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from lockstep.cli import main as lockstep_main
from lockstep.replay import ReplayIndex, wrap_registry

from conftest import build

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"


def _load_contrib(name: str):
    spec = importlib.util.spec_from_file_location(name, CONTRIB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


export_fixture = _load_contrib("export_fixture")
replay_suite = _load_contrib("replay_suite")

FLOW = {
    "name": "fixt",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"outputs": ["two"]}},
    ],
}


def _recorded_run(tmp_path, git_repo):
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    # Decoys: sensitive artifacts that must NOT survive the export.
    (h.run_dir / "phases" / "a" / "prompt.txt").write_text("SECRET PROMPT", encoding="utf-8")
    (h.run_dir / "phases" / "a" / "stdout.log").write_text("chatty", encoding="utf-8")
    (h.run_dir / "approval-evidence.txt").write_text("evidence", encoding="utf-8")
    return h


def test_export_copies_the_allowlist_and_nothing_else(tmp_path, git_repo):
    h = _recorded_run(tmp_path, git_repo)
    dest = tmp_path / "fixture"
    kept = export_fixture.export(h.run_dir, dest)
    assert sorted(kept) == ["phases/a/result.txt", "phases/b/result.txt", "state.json"]
    exported = sorted(str(p.relative_to(dest)).replace("\\", "/")
                      for p in dest.rglob("*") if p.is_file())
    assert exported == ["phases/a/result.txt", "phases/b/result.txt", "state.json"]
    assert "SECRET PROMPT" not in " ".join(
        p.read_text(encoding="utf-8") for p in dest.rglob("*") if p.is_file()
    )


def test_export_refuses_nonempty_dest_and_binary_results(tmp_path, git_repo):
    h = _recorded_run(tmp_path, git_repo)
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "x").write_text("y", encoding="utf-8")
    with pytest.raises(SystemExit, match="not empty"):
        export_fixture.export(h.run_dir, dest)
    (h.run_dir / "phases" / "a" / "result.txt").write_bytes(b"bin\x00ary")
    with pytest.raises(SystemExit, match="NUL"):
        export_fixture.export(h.run_dir, tmp_path / "fresh")


def test_exported_fixture_replays(tmp_path, git_repo):
    h = _recorded_run(tmp_path, git_repo)
    dest = tmp_path / "fixture"
    export_fixture.export(h.run_dir, dest)
    h2 = build(tmp_path / "replayed", FLOW, git_repo)
    wrap_registry(h2.engine.registry, ReplayIndex.from_run_dir(dest), strict=True,
                  log=h2.engine.log)
    assert h2.engine.run() == 0
    assert h2.fake.calls == [], "the fixture must serve every result"


def test_replay_suite_end_to_end(tmp_path, git_repo, capsys):
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    (flows_dir / "fixt.tg.json").write_text(
        json.dumps({"format_version": "1.0", **FLOW}), encoding="utf-8"
    )
    rec_runs = tmp_path / "rec-runs"
    code = lockstep_main([
        "run", str(flows_dir / "fixt.tg.json"), "--runs-dir", str(rec_runs),
        "--repo-root", str(git_repo),
    ])
    assert code == 0
    run_dir = next(p for p in rec_runs.iterdir() if (p / "state.json").exists())
    fixtures = tmp_path / "fixtures"
    export_fixture.export(run_dir, fixtures / "fixt")
    code = replay_suite.main([
        "--flows", str(flows_dir), "--fixtures", str(fixtures),
        "--repo-root", str(git_repo),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "1/1 passed" in out


def test_replay_suite_reports_a_missing_flow(tmp_path, git_repo, capsys):
    h = _recorded_run(tmp_path, git_repo)
    fixtures = tmp_path / "fixtures"
    export_fixture.export(h.run_dir, fixtures / "fixt")
    empty_flows = tmp_path / "no-flows"
    empty_flows.mkdir()
    code = replay_suite.main([
        "--flows", str(empty_flows), "--fixtures", str(fixtures),
        "--repo-root", str(git_repo),
    ])
    assert code == 1
    assert "no flow named" in capsys.readouterr().out


def test_replay_suite_bad_expected_exit_fails_the_fixture_not_the_suite(
    tmp_path, git_repo, capsys
):
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    (flows_dir / "fixt.tg.json").write_text(
        json.dumps({"format_version": "1.0", **FLOW}), encoding="utf-8"
    )
    h = _recorded_run(tmp_path, git_repo)
    fixtures = tmp_path / "fixtures"
    export_fixture.export(h.run_dir, fixtures / "fixt")
    (fixtures / "fixt" / "expected_exit.txt").write_text("sometimes", encoding="utf-8")
    code = replay_suite.main([
        "--flows", str(flows_dir), "--fixtures", str(fixtures),
        "--repo-root", str(git_repo),
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "bad expected_exit.txt" in out


def test_replay_suite_with_no_fixtures_says_nothing_was_checked(tmp_path, capsys):
    """An empty net exits 0 — nothing regressed, because nothing ran. That
    distinction has to be LOUD and on stderr, or this is the exit-0 placeholder
    the SSSF review rejected as the first thing that will lie to you."""
    assert replay_suite.main(["--fixtures", str(tmp_path / "absent")]) == 0
    err = capsys.readouterr().err
    assert "0/0" in err and "NOTHING WAS CHECKED" in err
    assert "export_fixture.py" in err, "it says how to fix it"
    assert "ALL-SHELL" in err, "and why the directory is empty"


def test_require_fixtures_turns_an_empty_net_into_a_failure(tmp_path):
    """For a caller that wants coverage rather than an all-clear."""
    assert replay_suite.main(
        ["--fixtures", str(tmp_path / "absent"), "--require-fixtures"]) == 1


# ----------------------------------------- the committed net, actually run

ROOT = Path(__file__).resolve().parents[1]


def test_the_committed_fixtures_replay(capsys):
    """The regression net, run for real over `tests/fixtures/replay`.

    Running the suite from pytest is what makes it a net rather than a command
    somebody remembers. It costs no tokens and spawns nothing: every executor
    is wrapped by `ReplayExecutor`, so the recorded result is served and the
    STRICT `input_hash` comparison is the whole point.
    """
    code = replay_suite.main(["--flows", str(ROOT / "flows"),
                              "--fixtures", str(ROOT / "tests" / "fixtures" / "replay"),
                              "--repo-root", str(ROOT),
                              "--require-fixtures"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0/0" not in out
    assert "passed" in out


def test_the_selftest_flow_is_shell_only_so_its_hashes_are_portable():
    """A harness node's input hash includes the LOCAL executor-config digest,
    so a fixture recorded from one replays only on the machine that recorded
    it. A shell node's is composed from its rendered argv alone. That is the
    whole reason this flow exists in the shape it does — and the reason a
    kind change here would quietly make the committed fixture machine-local."""
    from lockstep.taskgraph import load_flow

    tg, _ = load_flow(ROOT / "flows" / "selftest-replay.tg.json")
    assert {n.kind for n in tg.nodes} == {"shell"}
    assert (tg.budget.max_agent_spawns or 0) == 0
    # and nothing it interpolates comes from an upstream result: every hash
    # input is declared in the flow file or carried in the recorded args.
    for node in tg.nodes:
        assert "{steps." not in json.dumps(node.spec)


def test_the_committed_fixture_carries_no_machine_local_paths():
    """`export_fixture` clears `result_path` and `fingerprint_detail`; this is
    the assertion that a re-recorded fixture cannot be committed with somebody's
    home directory in it."""
    state = json.loads(
        (ROOT / "tests" / "fixtures" / "replay" / "selftest-replay" / "state.json")
        .read_text(encoding="utf-8")
    )
    assert state["fingerprint_detail"] == {}
    for rec in state["nodes"].values():
        assert rec["result_path"] is None
