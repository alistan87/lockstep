"""A4 — the doctor record: a successful probe leaves evidence; `run` prints
one advisory line when it is missing, stale, or a stanza drifted."""

from __future__ import annotations

import json

from lockstep.cli import main as lockstep_main
from lockstep.doctor import doctor_advisory, doctor_record_path, write_doctor_record
from lockstep.registry import ExecutorStanza, LockstepConfig


def _config(**stanzas) -> LockstepConfig:
    if not stanzas:
        stanzas = {"c": ExecutorStanza(argv=["c", "{prompt}"])}
    return LockstepConfig(default=next(iter(stanzas)), executors=dict(stanzas))


def test_no_executors_means_no_advisory(tmp_path):
    assert doctor_advisory(tmp_path, LockstepConfig()) is None


def test_missing_record_advises(tmp_path):
    msg = doctor_advisory(tmp_path, _config())
    assert msg and "no successful probe" in msg


def test_fresh_record_is_silent_and_drift_is_named(tmp_path):
    cfg = _config()
    write_doctor_record(tmp_path, cfg)
    assert doctor_advisory(tmp_path, cfg) is None
    drifted = _config(c=ExecutorStanza(argv=["c", "--new-flag", "{prompt}"]))
    msg = doctor_advisory(tmp_path, drifted)
    assert msg and "changed since the last successful probe" in msg and "c" in msg


def test_stale_record_advises_with_age(tmp_path):
    cfg = _config()
    write_doctor_record(tmp_path, cfg)
    record = json.loads(doctor_record_path(tmp_path).read_text(encoding="utf-8"))
    record["ts"] = "2020-01-01T00:00:00.000000Z"
    doctor_record_path(tmp_path).write_text(json.dumps(record), encoding="utf-8")
    msg = doctor_advisory(tmp_path, cfg)
    assert msg and "days ago" in msg
    generous = cfg.model_copy(update={"doctor_max_age_days": 100000})
    assert doctor_advisory(tmp_path, generous) is None


def test_unreadable_record_degrades_to_advice_not_a_crash(tmp_path):
    doctor_record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    doctor_record_path(tmp_path).write_text("not json", encoding="utf-8")
    msg = doctor_advisory(tmp_path, _config())
    assert msg and "doctor" in msg


def test_garbage_doctor_knob_never_blocks_config_load(tmp_path, capsys):
    """The advisory's knob is advisory too: a typo must not turn every
    `lockstep run` into exit 7."""
    from lockstep.registry import load_config

    path = tmp_path / "lockstep.toml"
    path.write_text(
        'default = "c"\n[doctor]\nmax_age_days = "weekly"\n'
        '[executors.c]\nargv = ["c", "{prompt}"]\n', encoding="utf-8"
    )
    cfg = load_config(path)
    assert cfg.doctor_max_age_days == 7
    assert "ignoring [doctor] max_age_days" in capsys.readouterr().err


def test_run_prints_the_advisory_line(tmp_path, capsys):
    (tmp_path / "lockstep.toml").write_text(
        'default = "c"\n[executors.c]\nargv = ["c", "{prompt}"]\n', encoding="utf-8"
    )
    flow = tmp_path / "f.tg.json"
    flow.write_text(json.dumps({
        "name": "adv",
        "nodes": [{"id": "a", "kind": "fake", "final": True, "spec": {}}],
    }), encoding="utf-8")
    code = lockstep_main([
        "run", str(flow), "--repo-root", str(tmp_path),
        "--runs-dir", str(tmp_path / "runs"),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "doctor: no successful probe" in out
    # Zero-token operations are not nagged: dry-run, estimate, and replay
    # never touch a harness.
    code = lockstep_main([
        "run", str(flow), "--repo-root", str(tmp_path),
        "--runs-dir", str(tmp_path / "runs"), "--dry-run",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "doctor:" not in out
