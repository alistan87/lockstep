"""A5 — `lockstep gc`: retention that never touches what the estimator or a
human still relies on, dry-run by default."""

from __future__ import annotations

import datetime as _dt

from lockstep.cli import main as lockstep_main
from lockstep.gc import apply_gc, plan_gc
from lockstep.state import PhaseRecord, RunState, write_state

NOW = _dt.datetime(2026, 8, 4, tzinfo=_dt.UTC)


def _mk_run(runs_dir, name, *, flow_hash="fh", args=None, days_old=30,
            approval_status=None, lock=False, rejection=False):
    d = runs_dir / name
    d.mkdir(parents=True)
    started = (NOW - _dt.timedelta(days=days_old)).isoformat()
    nodes = {"a": PhaseRecord(node_id="a", role="work", kind="fake", status="done")}
    if approval_status:
        nodes["ok"] = PhaseRecord(node_id="ok", role="approval", kind="harness",
                                  status=approval_status)
    write_state(d, RunState(flow_name="f", flow_hash=flow_hash, format_version="1.0",
                            args=args or {}, nodes=nodes, started_at=started))
    if lock:
        (d / "lock").write_text("{}", encoding="utf-8")
    if rejection:
        (d / "rejection.txt").write_text("no", encoding="utf-8")
    return d


def test_keep_rules(tmp_path):
    runs = tmp_path / "runs"
    # Eight old runs of one flow: newest 5 kept, older 3 are candidates unless protected.
    dirs = [_mk_run(runs, f"f-{i:02d}", days_old=100 - i) for i in range(8)]
    plan = plan_gc(runs, keep_per_flow=5, keep_days=14, now=NOW)
    candidate_names = {d.name for d, _ in plan.candidates}
    # newest 5 = smallest days_old = f-07..f-03; candidates = f-00..f-02 (oldest three)
    assert candidate_names == {"f-00", "f-01", "f-02"}
    assert plan.kept == 5
    reason = dict((d.name, r) for d, r in plan.candidates)["f-00"]
    assert "no lockfile" in reason and "days old" in reason


def test_protections_beat_age_and_rank(tmp_path):
    runs = tmp_path / "runs"
    for i in range(6):
        _mk_run(runs, f"f-{i:02d}", days_old=100 - i)
    locked = _mk_run(runs, "locked", days_old=200, lock=True)
    waiting = _mk_run(runs, "waiting", days_old=200, approval_status="pending")
    rejected = _mk_run(runs, "rejected", days_old=200, rejection=True)
    young = _mk_run(runs, "young", days_old=1)
    plan = plan_gc(runs, keep_per_flow=1, keep_days=14, now=NOW)
    protected = {locked.name, waiting.name, rejected.name, young.name}
    assert protected.isdisjoint({d.name for d, _ in plan.candidates})


def test_lineages_rank_per_flow_hash_AND_args(tmp_path):
    """Attachment is keyed per (flow_hash, args); gc deleting a lineage head
    would silently fork that lineage on the next `run --arg ...`."""
    runs = tmp_path / "runs"
    _mk_run(runs, "a-new", args={"t": "a"}, days_old=1)
    old_head_b = _mk_run(runs, "b-old-head", args={"t": "b"}, days_old=300)
    plan = plan_gc(runs, keep_per_flow=1, keep_days=14, now=NOW)
    assert old_head_b.name not in {d.name for d, _ in plan.candidates}, \
        "the newest run of EACH lineage is kept, however old"


def test_newest_of_a_lineage_survives_keep_per_flow_zero(tmp_path):
    runs = tmp_path / "runs"
    _mk_run(runs, "only", days_old=500)
    plan = plan_gc(runs, keep_per_flow=0, keep_days=1, now=NOW)
    assert plan.candidates == [] and plan.kept == 1


def test_answered_approval_does_not_protect(tmp_path):
    runs = tmp_path / "runs"
    _mk_run(runs, "newest", days_old=1)
    _mk_run(runs, "old-answered", days_old=200, approval_status="done")
    plan = plan_gc(runs, keep_per_flow=1, keep_days=14, now=NOW)
    assert {d.name for d, _ in plan.candidates} == {"old-answered"}


def test_non_run_dirs_are_never_touched(tmp_path):
    runs = tmp_path / "runs"
    (runs / "lineages").mkdir(parents=True)
    (runs / "doctor-record-holder").mkdir()
    plan = plan_gc(runs, now=NOW)
    assert plan.candidates == [] and plan.skipped == 2


def test_apply_deletes_only_candidates(tmp_path):
    runs = tmp_path / "runs"
    keep = _mk_run(runs, "keep", days_old=1)
    drop = _mk_run(runs, "drop", days_old=200, flow_hash="other")
    _mk_run(runs, "other-newest", days_old=199, flow_hash="other")
    plan = plan_gc(runs, keep_per_flow=1, keep_days=14, now=NOW)
    assert {d.name for d, _ in plan.candidates} == {"drop"}
    assert apply_gc(plan) == 1
    assert keep.exists() and not drop.exists()


def test_cli_dry_run_by_default(tmp_path, capsys):
    runs = tmp_path / "runs"
    _mk_run(runs, "newest", days_old=1)
    doomed = _mk_run(runs, "doomed", days_old=400, flow_hash="x")
    _mk_run(runs, "x-newest", days_old=399, flow_hash="x")
    code = lockstep_main(["gc", str(runs), "--keep-per-flow", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "dry run" in out and "nothing protects it" in out
    assert doomed.exists(), "dry run must not delete"
    code = lockstep_main(["gc", str(runs), "--keep-per-flow", "1", "--apply"])
    assert code == 0 and not doomed.exists()
