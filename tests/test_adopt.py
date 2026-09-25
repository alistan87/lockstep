"""S3 (DESIGN-NOTE-adopt, adopted): `lockstep adopt` — settle a human-remediated
artifact into a run.

The nine acceptance tests from the design note's §6, plus the two pin-dissolve
paths (heal, steer) its D2 discussion demanded. All zero-token: fake executor,
tmp git trees, in-process `main()`.

The scenario under test is OW-07's, exactly: a writer produced an artifact, a
human legitimately edited it, and before `adopt` every road was bad — resume
re-ran the producer on a hash miss and overwrote the edit, `--seed` refused
the dirty scope, `--allow-dirty-scope` waived the one protection that
mattered.
"""

from __future__ import annotations

import json
import os
import socket

from lockstep.adopt import adopt, result_text_consumers
from lockstep.cli import main
from lockstep.state import load_state, read_events
from lockstep.taskgraph import TaskGraph

from conftest import build, git, rebuild

HUMAN = "human remediation\n"
MODEL = "model version\n"


def _flow(consumer_task: str = "review the artifact on disk", extra_nodes=()):
    return {
        "name": "adoptable",
        "nodes": [
            {"id": "writer", "kind": "fake",
             "spec": {"outputs": ["model-out"], "writes": ["artifact.txt"],
                      "write_files": {"artifact.txt": MODEL}}},
            *extra_nodes,
            {"id": "consumer", "kind": "fake", "depends_on": ["writer"],
             "spec": {"outputs": ["reviewed"], "task": consumer_task, "readonly": True},
             "final": True},
        ],
    }


def _run_and_edit(tmp_path, git_repo, monkeypatch, flow=None):
    """Run the flow to done via the real CLI, then apply the human's edit.
    Returns (run_dir, flow_path, reason_file)."""
    flow = flow or _flow()
    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 0
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    (git_repo / "artifact.txt").write_text(HUMAN, encoding="utf-8")
    reason = tmp_path / "owner-decision.md"
    reason.write_text("Kept the human fix: the model's version dropped the caveat.\n",
                      encoding="utf-8")
    return run_dir, flow_path, reason


# --- acceptance 1: consumers re-run, the writer does not ------------------------

def test_adopt_reruns_consumers_never_the_writer(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    out = capsys.readouterr().out
    assert "adopted: 1 path(s) into writer" in out
    assert "consumers marked pending (1): consumer" in out
    st = load_state(run_dir)
    assert st.nodes["writer"].adopted is not None
    assert st.nodes["writer"].adopted.source == "external-approved-remediation"
    assert st.nodes["consumer"].status == "pending"
    # The recorded hash is deliberately NOT rewritten (D2: the pin is visible
    # or it is not a pin).
    assert st.nodes["writer"].input_hash is not None

    assert main(["resume", str(run_dir)]) == 0
    st = load_state(run_dir)
    assert st.nodes["writer"].attempts == 1, "the writer must not re-run"
    assert st.nodes["consumer"].attempts == 2, "the consumer re-runs unweakened"
    assert (git_repo / "artifact.txt").read_text(encoding="utf-8") == HUMAN
    # adoption-reason.txt carries the human's words and gc protects it.
    assert "the model's version dropped the caveat" in (
        (run_dir / "adoption-reason.txt").read_text(encoding="utf-8"))


# --- acceptance 2 (D2): the pin holds even against a hash miss ------------------

def test_pin_survives_a_hash_miss(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    # A changed config digest misses EVERY fake node's hash — the OW-07 shape
    # (volatile upstream input), reproduced deterministically. Outside the
    # repo so the tree itself stays clean.
    cfg = tmp_path / "other.toml"
    cfg.write_text("# a different digest\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["resume", str(run_dir), "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "settled-by-adoption 'writer'" in out
    st = load_state(run_dir)
    assert st.nodes["writer"].attempts == 1, "hash missed, pin held"
    assert st.nodes["consumer"].attempts == 2
    assert (git_repo / "artifact.txt").read_text(encoding="utf-8") == HUMAN
    # status renders the pin, never a cache hit.
    capsys.readouterr()
    assert main(["status", str(run_dir)]) == 0
    assert "settled by adoption: writer" in capsys.readouterr().out
    # explain names it above the hash detail.
    capsys.readouterr()
    assert main(["explain", str(run_dir), "writer"]) == 0
    assert "settled-by-adoption" in capsys.readouterr().out


# --- acceptance 3 (D3): the M7 sweep neither re-pends the writer nor goes blind -

def test_fingerprint_refresh_spares_the_writer_but_unrelated_edits_still_warn(
        tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    capsys.readouterr()
    assert main(["resume", str(run_dir)]) == 0
    out = capsys.readouterr().out
    # The adoption itself must NOT register as an external edit — that was the
    # bug this decision exists to prevent (the sweep re-pending the writer).
    assert "changed OUTSIDE lockstep" not in out
    assert load_state(run_dir).nodes["writer"].attempts == 1

    # An unrelated edit in the same window is still external, still named.
    (git_repo / "a.txt").write_text("out-of-band\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["resume", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "changed OUTSIDE lockstep" in out and "a.txt" in out
    assert load_state(run_dir).nodes["writer"].attempts == 1, "pin survives the sweep"


# --- acceptance 4 (D1): recorded-result-text consumers refuse; --force journals -

def test_result_text_consumer_refuses_and_force_overrides(
        tmp_path, git_repo, monkeypatch, capsys):
    flow = _flow(consumer_task="review this: {steps.writer.output}")
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch, flow=flow)
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 7
    out = capsys.readouterr().out
    assert "RECORDED result text" in out
    assert "consumer (spec.task: {steps.writer.output})" in out
    assert load_state(run_dir).nodes["writer"].adopted is None, "refusal wrote nothing"

    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason),
                 "--force"]) == 0
    assert "warning: --force" in capsys.readouterr().out
    st = load_state(run_dir)
    assert st.nodes["writer"].adopted.forced_result_text_consumers, "override journaled"
    ev = [e for e in read_events(run_dir) if e.get("kind") == "adoption"][-1]
    assert ev["forced_result_text_consumers"]


def test_the_scanner_covers_every_interpolation_site():
    """D1's finding: _body_referenced_deps covers three sites and excludes
    `when` by design (A2); the adopt scanner must cover all of them, flow-node
    child args included."""
    tg = TaskGraph.model_validate({
        "name": "sites",
        "nodes": [
            {"id": "writer", "kind": "fake", "spec": {"outputs": ["x"]}},
            {"id": "via-task", "kind": "fake", "depends_on": ["writer"],
             "spec": {"task": "{steps.writer.output}"}},
            {"id": "via-cmd", "kind": "shell", "depends_on": ["writer"],
             "spec": {"cmd": ["echo", "{steps.writer.json.field}"]}},
            {"id": "via-when", "kind": "fake", "depends_on": ["writer"],
             "when": '{steps.writer.output} == "x"', "spec": {}},
            {"id": "via-over", "role": "map", "kind": "fake", "depends_on": ["writer"],
             "over": "{steps.writer.json}", "spec": {}},
            {"id": "via-flow-args", "kind": "flow", "depends_on": ["writer"],
             "spec": {"flow": "child.tg.json", "args": {"x": "{steps.writer.output}"}}},
            {"id": "via-previous", "kind": "fake", "depends_on": ["writer"],
             "spec": {"task": "{previous.output}"}},
            {"id": "clean", "kind": "fake", "depends_on": ["writer"],
             "spec": {"task": "reads the file instead"}},
        ],
    })
    hits = result_text_consumers(tg, "writer")
    hit_nodes = {h.split(" ")[0] for h in hits}
    assert hit_nodes == {"via-task", "via-cmd", "via-when", "via-over",
                         "via-flow-args", "via-previous"}
    assert "clean" not in hit_nodes


# --- acceptance 5: scope refusals -----------------------------------------------

def test_paths_outside_the_writers_scope_refuse(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    (git_repo / "c.txt").write_text("stray\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason),
                 "--path", "c.txt"]) == 7
    assert "outside" in capsys.readouterr().out
    assert load_state(run_dir).nodes["writer"].adopted is None


def test_a_path_inside_another_writers_scope_refuses(tmp_path, git_repo, monkeypatch, capsys):
    flow = _flow(extra_nodes=(
        {"id": "writer2", "kind": "fake",
         "spec": {"outputs": ["noop"], "writes": ["artifact.txt"]}},
    ))
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch, flow=flow)
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 7
    out = capsys.readouterr().out
    assert "writer2" in out and "second writer" in out
    assert load_state(run_dir).nodes["writer"].adopted is None


def test_nothing_dirty_in_scope_refuses(tmp_path, git_repo, monkeypatch, capsys):
    flow = _flow()
    flow_path = git_repo / "f.tg.json"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.chdir(git_repo)
    runs = tmp_path / "runs"
    assert main(["run", str(flow_path), "--runs-dir", str(runs)]) == 0
    run_dir = next(d for d in runs.iterdir() if (d / "state.json").exists())
    reason = tmp_path / "r.md"
    reason.write_text("x\n", encoding="utf-8")
    # No human edit happened: artifact.txt is exactly what the writer wrote —
    # dirty (uncommitted) but that IS in scope, so it is adoptable; delete it
    # to test the true nothing-to-adopt case... no: an unedited dirty artifact
    # is legitimately adoptable (adopt records the human's decision to keep
    # it). The nothing-in-scope case is a clean tree:
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "settle")
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 7
    assert "no modified paths" in capsys.readouterr().out


# --- acceptance 6: lock and root refusals ---------------------------------------

def test_adopt_refuses_under_a_live_lock_and_from_the_wrong_root(
        tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    lock = run_dir / "lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "hostname": socket.gethostname(),
                                "started": "now"}), encoding="utf-8")
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 7
    assert "driver may be live" in capsys.readouterr().out
    assert lock.exists(), "a refusal must not clear someone else's lock"
    assert load_state(run_dir).nodes["writer"].adopted is None
    lock.unlink()

    st = load_state(run_dir)
    st.repo_root = str(tmp_path / "elsewhere")
    from lockstep.state import write_state
    write_state(run_dir, st)
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 7
    out = capsys.readouterr().out
    assert "elsewhere" in out and str(git_repo.name) in out, "both paths named"


# --- acceptance 7: the journal stays verifiable ---------------------------------

def test_verify_trace_covers_the_adoption_event(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    assert main(["verify-trace", str(run_dir)]) == 0
    ev = [e for e in read_events(run_dir) if e.get("kind") == "adoption"]
    assert len(ev) == 1
    assert ev[0]["source"] == "external-approved-remediation"
    assert "artifact.txt" in ev[0]["paths"]
    assert ev[0]["paths"]["artifact.txt"]["before"] != ev[0]["paths"]["artifact.txt"]["after"]


# --- acceptance 8 (D5): a seed names the adoption and does not transfer it ------

def test_seed_from_an_adopted_run_warns_and_does_not_transfer(
        tmp_path, git_repo, monkeypatch, capsys):
    run_dir, flow_path, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    # The natural post-adoption step: the human commits their artifact.
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "adopted artifact settled")
    # Edit the flow (the documented seed use-case) so a new lineage forks.
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["description"] = "edited mid-campaign"
    flow_path.write_text(json.dumps(flow), encoding="utf-8")
    capsys.readouterr()
    assert main(["run", str(flow_path), "--runs-dir", str(tmp_path / "runs"),
                 "--seed", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "settled by adoption in the seed source" in out
    assert "--force-stale writer" in out
    new_run = max(
        (d for d in (tmp_path / "runs").iterdir() if (d / "state.json").exists()),
        key=lambda d: d.stat().st_mtime,
    )
    assert new_run != run_dir
    assert load_state(new_run).nodes["writer"].adopted is None, "the pin never transfers"


# --- acceptance 9: forward compatibility ----------------------------------------

def test_an_older_driver_reading_a_newer_state_degrades(tmp_path, git_repo, monkeypatch):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    # The RunState unknown-field property (pinned since 2e1fdba), exercised on
    # the shapes adopt adds: a state carrying fields THIS driver has never
    # heard of still loads, which is what lets a 0.11.0 driver read a state
    # containing `adopted` and simply not see it.
    raw = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    raw["nodes"]["writer"]["from_the_future"] = {"x": 1}
    raw["nodes"]["writer"]["adopted"]["future_field"] = "y"
    raw["a_future_run_field"] = True
    (run_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
    st = load_state(run_dir)
    assert st.nodes["writer"].adopted is not None
    assert st.nodes["writer"].adopted.paths == ["artifact.txt"]


# --- D4: release ----------------------------------------------------------------

def test_release_dissolves_the_pin_and_hash_governs_again(
        tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    assert main(["adopt", str(run_dir), "writer", "--release"]) == 0
    st = load_state(run_dir)
    assert st.nodes["writer"].adopted is None
    evs = [e for e in read_events(run_dir) if e.get("kind") == "adoption"]
    assert [e.get("op") for e in evs] == [None, "release"], "the original event survives"
    # With the pin gone a hash miss governs again — and legally overwrites.
    cfg = tmp_path / "other.toml"
    cfg.write_text("# different digest\n", encoding="utf-8")
    assert main(["resume", str(run_dir), "--config", str(cfg)]) == 0
    st = load_state(run_dir)
    assert st.nodes["writer"].attempts == 2
    assert (git_repo / "artifact.txt").read_text(encoding="utf-8") == MODEL


def test_release_without_an_adoption_refuses(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, _ = _run_and_edit(tmp_path, git_repo, monkeypatch)
    capsys.readouterr()
    assert main(["adopt", str(run_dir), "writer", "--release"]) == 7
    assert "no adoption to release" in capsys.readouterr().out


# --- the engine dissolves the pin when it re-spawns the node --------------------

def test_a_heal_round_dissolves_the_pin(tmp_path, git_repo):
    """A gate that re-reviews the adopted artifact and blocks may heal its
    writer — and the pin must not survive the re-spawn, or the NEXT model
    output would read as settled-by-adoption."""
    flow = {
        "name": "heal-adopt",
        "nodes": [
            {"id": "writer", "kind": "fake",
             "spec": {"outputs": ["model-out"], "writes": ["artifact.txt"],
                      "write_files": {"artifact.txt": MODEL}}},
            {"id": "gate", "role": "gate", "kind": "fake", "depends_on": ["writer"],
             "spec": {"outputs": [{"findings": [], "verdict": "pass", "reason": "ok"}],
                      "readonly": True},
             "output": "json", "contract": "Verdict",
             "heal": {"max_rounds": 1, "targets": ["writer"], "rollback": False},
             "final": True},
        ],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    (git_repo / "artifact.txt").write_text(HUMAN, encoding="utf-8")
    reason = tmp_path / "r.md"
    reason.write_text("keep mine\n", encoding="utf-8")
    (h.run_dir / "flow.tg.json").write_text(json.dumps(flow), encoding="utf-8")
    assert adopt(h.run_dir, "writer", repo_root=git_repo, reason_file=reason,
                 out=lambda *a: None) == 0

    # Resume: the re-run gate now BLOCKS and heals the writer.
    flow2 = json.loads(json.dumps(flow))
    flow2["nodes"][1]["spec"]["outputs"] = [
        {"findings": [], "verdict": "block", "reason": "the human version broke X"},
        {"findings": [], "verdict": "pass", "reason": "fixed"},
    ]
    h2 = rebuild(tmp_path, flow2, git_repo, h.run_dir)
    h2.engine.prepare_resume()
    assert h2.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["writer"].adopted is None, "heal dissolved the pin"
    assert st.nodes["writer"].attempts == 2, "the heal re-spawn ran"
    ev = [e for e in read_events(h.run_dir)
          if e.get("kind") == "adoption" and e.get("op") == "dissolved"]
    assert ev and "heal round" in ev[0]["cause"]
    # The heal re-spawn's output is model output — and it legally overwrote.
    assert (git_repo / "artifact.txt").read_text(encoding="utf-8") == MODEL


def test_a_steering_message_dissolves_the_pin(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    assert main(["steer", str(run_dir), "writer", "regenerate with the caveat kept"]) == 0
    assert main(["resume", str(run_dir)]) == 0
    st = load_state(run_dir)
    assert st.nodes["writer"].adopted is None, "steer dissolved the pin"
    assert st.nodes["writer"].attempts == 2
    ev = [e for e in read_events(run_dir)
          if e.get("kind") == "adoption" and e.get("op") == "dissolved"]
    assert ev and "steering" in ev[0]["cause"]


# --- explain --graph renders the pin --------------------------------------------

def test_explain_graph_labels_the_pin(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    capsys.readouterr()
    assert main(["explain", str(run_dir), "--graph"]) == 0
    out = capsys.readouterr().out
    assert "settled by adoption writer" in out
    assert "settled by adoption: 1" in out


# --- gc protects the human's words ----------------------------------------------

def test_gc_protects_adoption_reason(tmp_path, git_repo, monkeypatch, capsys):
    run_dir, flow_path, reason = _run_and_edit(tmp_path, git_repo, monkeypatch)
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    # A NEWER run of the same lineage demotes the adopted one from the
    # unconditional newest-of-lineage protection — without that, this test
    # would pass for any run at all.
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "settle so the fresh run's preflight passes")
    assert main(["run", str(flow_path), "--runs-dir", str(run_dir.parent),
                 "--fresh"]) == 0
    from lockstep.gc import plan_gc
    plan = plan_gc(run_dir.parent, keep_per_flow=0, keep_days=0)
    assert not any(d == run_dir for d, _ in plan.candidates), \
        "adoption-reason.txt (human-authored) must protect the run"
    # The control: with the human's words gone, the same run is expendable.
    (run_dir / "adoption-reason.txt").unlink()
    plan = plan_gc(run_dir.parent, keep_per_flow=0, keep_days=0)
    assert any(d == run_dir for d, _ in plan.candidates)


def test_adopt_keeps_a_map_consumers_item_spawn_counts(tmp_path, git_repo, monkeypatch, capsys):
    """G3b review: adoption resets a map consumer's items, and the per-item
    spawn counter must survive it — the cap is per lineage, and a human
    re-pending the cone must not hand every item a fresh ceiling."""
    src = {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
           "spec": {"outputs": ['{"files": ["p", "q"], "notes": ""}'], "readonly": True}}
    fan = {"id": "fan", "role": "map", "kind": "fake", "depends_on": ["writer", "src"],
           "over": "{steps.src.json.files}", "concurrency": 1,
           "spec": {"task": "check {item}", "outputs": ["ok"], "readonly": True}}
    run_dir, _, reason = _run_and_edit(tmp_path, git_repo, monkeypatch,
                                       flow=_flow(extra_nodes=(src, fan)))
    before = load_state(run_dir).nodes["fan"].items
    assert [before[k].token_spawns for k in ("0", "1")] == [1, 1]  # the premise
    assert main(["adopt", str(run_dir), "writer", "--reason-file", str(reason)]) == 0
    items = load_state(run_dir).nodes["fan"].items
    assert all(items[k].status == "pending" for k in ("0", "1"))
    assert [items[k].token_spawns for k in ("0", "1")] == [1, 1]
