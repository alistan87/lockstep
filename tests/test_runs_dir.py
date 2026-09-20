"""`[driver] runs_dir` (2026-09-19): where runs live, resolved ONE way for the
driver and every cockpit tool — flag > config key > ./runs. The key is never
hashed (run dirs are excluded from every fingerprint); a relative value is
relative to the config file, so a repo's lockstep.toml can point outside the
audited tree without every caller repeating `--runs-dir`."""

from __future__ import annotations

import json
from pathlib import Path

from lockstep.cli import main
from lockstep.registry import LockstepConfig, load_config, resolve_runs_dir


def test_resolution_order_flag_config_default(tmp_path):
    cfg_path = tmp_path / "repo" / "lockstep.toml"
    cfg_path.parent.mkdir()
    cfg_path.write_text('default = "c"\n[executors.c]\nargv = ["x", "{prompt}"]\n'
                        '[driver]\nruns_dir = "../lockstep-runs"\n', encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.runs_dir == "../lockstep-runs"
    # The flag wins outright.
    assert resolve_runs_dir(cfg, "elsewhere") == Path("elsewhere")
    # The key resolves against the config FILE's directory, not the cwd.
    assert resolve_runs_dir(cfg, None) == cfg_path.parent / "../lockstep-runs"
    # Absolute values are taken as they are.
    cfg.runs_dir = str(tmp_path / "abs")
    assert resolve_runs_dir(cfg, None) == tmp_path / "abs"
    # No key: the pre-key default, under the base when one is given.
    assert resolve_runs_dir(LockstepConfig(), None) == Path("runs")
    assert resolve_runs_dir(LockstepConfig(), None, base=tmp_path) == tmp_path / "runs"
    assert resolve_runs_dir(None, None) == Path("runs")


def test_a_bad_value_is_ignored_with_a_note_never_a_refusal(tmp_path, capsys):
    cfg_path = tmp_path / "lockstep.toml"
    cfg_path.write_text('default = "c"\n[executors.c]\nargv = ["x", "{prompt}"]\n'
                        '[driver]\nruns_dir = 7\n', encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.runs_dir is None
    assert "ignoring [driver] runs_dir" in capsys.readouterr().err


def test_the_key_never_enters_a_stanza_digest(tmp_path):
    from lockstep.executors.harness import stanza_digest
    base = 'default = "c"\n[executors.c]\nargv = ["x", "{prompt}"]\n'
    a = load_config(_write(tmp_path / "a.toml", base))
    b = load_config(_write(tmp_path / "b.toml", base + '[driver]\nruns_dir = "../r"\n'))
    assert stanza_digest("c", a.executors["c"]) == stanza_digest("c", b.executors["c"])


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def _flow(git_repo) -> Path:
    flow = git_repo / "f.tg.json"
    flow.write_text(json.dumps({"name": "rd", "nodes": [
        {"id": "a", "kind": "fake", "final": True, "spec": {"outputs": ["ok"]}}]}),
        encoding="utf-8")
    return flow


def test_run_honours_the_config_key_and_the_flag_overrides_it(tmp_path, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    (git_repo / "lockstep.toml").write_text(
        '[driver]\nruns_dir = "../cfg-runs"\n', encoding="utf-8")
    flow = _flow(git_repo)
    assert main(["run", str(flow)]) == 0
    cfg_runs = (git_repo / "../cfg-runs").resolve()
    assert cfg_runs.is_dir() and any(cfg_runs.iterdir())
    assert not (git_repo / "runs").exists(), "nothing landed under the old default"
    flagged = tmp_path / "flag-runs"
    assert main(["run", str(flow), "--runs-dir", str(flagged), "--fresh"]) == 0
    assert flagged.is_dir() and any(flagged.iterdir())


def test_gc_and_active_read_the_key_from_the_cwd(tmp_path, git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    (git_repo / "lockstep.toml").write_text(
        '[driver]\nruns_dir = "../cfg-runs"\n', encoding="utf-8")
    assert main(["run", str(_flow(git_repo))]) == 0
    assert main(["active", "--all"]) == 0
    out = capsys.readouterr().out
    assert "rd-" in out, out
    assert main(["gc"]) == 0
    assert "kept" in capsys.readouterr().out


def test_the_cockpit_resolves_exactly_as_the_driver(tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mission_view", Path(__file__).resolve().parents[1] / "contrib" / "mission_view.py")
    mv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert mv.default_runs_root(repo) == repo / "runs"
    (repo / "lockstep.toml").write_text('[driver]\nruns_dir = "../r"\n', encoding="utf-8")
    assert mv.default_runs_root(repo) == repo / "../r"
    assert mv.default_runs_root(repo, "explicit") == Path("explicit")
    # A toml the DRIVER would refuse: the default layout, and the absence is
    # NAMED — a silently wrong runs root would list the wrong runs as if
    # nothing were amiss (review 2026-09-19).
    (repo / "lockstep.toml").write_text("[driver\n", encoding="utf-8")
    assert mv.default_runs_root(repo) == repo / "runs"
    assert "lockstep.toml unreadable" in capsys.readouterr().err
