"""E7: warm-start a NEW lineage from a prior run's results (`run --seed`).

Editing a flow starts a new lineage and re-bills every completed node. The
refusal is correct — hash integrity is what the cache rests on — but it made a
one-word prompt fix cost the whole graph, so authors stopped editing flows.
A seed keeps the refusal and removes the cost: a node whose input_hash matches
a SUCCESSFUL result in the seed run is served; everything else runs.

The tests below are all about the boundary of that "matches": what is served,
what deliberately is not, and whether a reader of the new run dir can tell the
difference.
"""

from __future__ import annotations

import json

from lockstep.seed import SeedIndex, wrap_registry
from lockstep.state import load_state

from conftest import build, calls_of

FLOW = {
    "name": "seedy",
    "nodes": [
        {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
        {"id": "b", "kind": "fake", "final": True, "depends_on": ["a"],
         "spec": {"outputs": ["two"], "task": "original"}},
    ],
}


def _edited(task: str, node: str = "b") -> dict:
    """The same flow with ONE node's prompt changed — the whole motivating
    case. Deep-copied so the module-level FLOW stays pristine."""
    flow = json.loads(json.dumps(FLOW))
    for n in flow["nodes"]:
        if n["id"] == node:
            n["spec"]["task"] = task
    return flow


def _record(tmp_path, git_repo, flow=FLOW):
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    return h.run_dir


def _seeded(tmp_path, git_repo, source, flow=FLOW, *, name="seeded"):
    h = build(tmp_path / name, flow, git_repo)
    wrap_registry(
        h.engine.registry,
        SeedIndex.from_run_dir(source),
        log=h.engine.log,
        on_hit=h.engine.note_seeded,
    )
    return h


def test_an_unchanged_node_is_served_not_spawned(tmp_path, git_repo):
    source = _record(tmp_path, git_repo)
    h = _seeded(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    assert calls_of(h, "a") == [], "a matching node must not spawn"
    assert calls_of(h, "b") == []
    st = load_state(h.run_dir)
    assert st.nodes["a"].status == "done"
    assert st.nodes["b"].status == "done"


def test_an_edited_node_runs_and_only_it(tmp_path, git_repo):
    """The motivating case: one prompt changed, one node re-billed."""
    source = _record(tmp_path, git_repo)
    h = _seeded(tmp_path, git_repo, source, flow=_edited("changed my mind"))
    assert h.engine.run() == 0
    assert calls_of(h, "a") == [], "the untouched upstream is inherited"
    assert len(calls_of(h, "b")) == 1, "the edited node runs for real"


def test_a_different_upstream_RESULT_invalidates_what_reads_it(tmp_path, git_repo):
    """Soundness: `b` interpolates `a`, so when the re-run `a` produces
    something else, `b`'s input_hash moves and `b` cannot be served either.
    No special casing — it falls out of the hash."""
    flow = json.loads(json.dumps(FLOW))
    flow["nodes"][1]["spec"]["task"] = "reads {steps.a.output}"
    source = _record(tmp_path, git_repo, flow=flow)

    edited = json.loads(json.dumps(flow))
    edited["nodes"][0]["spec"]["task"] = "produce something else"
    edited["nodes"][0]["spec"]["outputs"] = ["ONE, DIFFERENTLY"]
    h = _seeded(tmp_path, git_repo, source, flow=edited)
    assert h.engine.run() == 0
    assert len(calls_of(h, "a")) == 1
    assert len(calls_of(h, "b")) == 1, "downstream of a CHANGED result cannot be inherited"


def test_a_re_run_upstream_that_lands_on_the_same_result_keeps_the_cache(tmp_path, git_repo):
    """The flip side, and the reason this is content-addressed rather than
    lineage-addressed: `a`'s PROMPT changed, so `a` re-runs — but it produced
    the same output, so `b`'s inputs never moved and `b` is still served.
    Re-running an ancestor is not by itself a reason to re-bill its readers.
    """
    flow = json.loads(json.dumps(FLOW))
    flow["nodes"][1]["spec"]["task"] = "reads {steps.a.output}"
    source = _record(tmp_path, git_repo, flow=flow)

    edited = json.loads(json.dumps(flow))
    edited["nodes"][0]["spec"]["task"] = "say the same thing, differently asked"
    h = _seeded(tmp_path, git_repo, source, flow=edited)
    assert h.engine.run() == 0
    assert len(calls_of(h, "a")) == 1, "the edited node itself runs"
    assert calls_of(h, "b") == [], "its reader saw identical inputs"
    assert load_state(h.run_dir).nodes["b"].seeded_from


def test_a_served_node_spends_no_spawn_budget(tmp_path, git_repo):
    """A seeded node spawns nothing, so §9.5's counter must not move — the
    decision is made at plan time precisely so this stays honest."""
    source = _record(tmp_path, git_repo)
    before = load_state(source).token_spawns
    assert before == 2, "both fake nodes cost a spawn in the seed run"
    h = _seeded(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    assert load_state(h.run_dir).token_spawns == 0


def test_provenance_is_on_the_record_and_in_the_journal(tmp_path, git_repo):
    """A reader must be able to tell inherited work from work this run did:
    the seed's tree, config and provider are not this run's."""
    source = _record(tmp_path, git_repo)
    h = _seeded(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    rec = load_state(h.run_dir).nodes["a"]
    assert rec.seeded_from and str(source) in rec.seeded_from
    events = [json.loads(ln) for ln in
              (h.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    seeded = [e for e in events if e.get("kind") == "seed"]
    assert {e["node"] for e in seeded} == {"a", "b"}


def test_a_failed_recording_is_never_served(tmp_path, git_repo):
    """A failure is not a result. Re-running it is the point of running again."""
    flow = json.loads(json.dumps(FLOW))
    flow["nodes"][1]["spec"]["exit_code"] = 1
    source = _record_failing(tmp_path, git_repo, flow)
    h = _seeded(tmp_path, git_repo, source, flow=flow)
    h.engine.run()
    assert calls_of(h, "a") == [], "the successful node is still inherited"
    assert len(calls_of(h, "b")) >= 1, "the failed one runs again"


def _record_failing(tmp_path, git_repo, flow):
    h = build(tmp_path, flow, git_repo)
    h.engine.run()
    return h.run_dir


def test_map_items_are_not_seeded(tmp_path, git_repo):
    """A map's per-item hash includes `index:i`, appended by the engine AFTER
    the executor plans — a plan-time decision cannot see it, and deciding at
    execute time would spend budget for a spawn that never happened. The
    documented limit: every item runs.
    """
    flow = {
        "name": "seedmap",
        "nodes": [
            {"id": "src", "kind": "fake", "output": "json", "contract": "PathManifest",
             "spec": {"outputs": [{"files": ["x", "y"], "notes": ""}]}},
            {"id": "m", "role": "map", "kind": "fake", "final": True,
             "depends_on": ["src"], "over": "{steps.src.json.files}", "concurrency": 1,
             "spec": {"outputs": ["done"], "task": "item {item}"}},
        ],
    }
    source = _record(tmp_path, git_repo, flow=flow)
    h = _seeded(tmp_path, git_repo, source, flow=flow)
    assert h.engine.run() == 0
    assert calls_of(h, "src") == [], "the ordinary node upstream is still served"
    assert len(calls_of(h, "m")) == 2, "every item runs"


def test_a_shell_node_is_never_seeded(tmp_path, git_repo):
    """Shell nodes always re-run (SPEC §0.1.7): cheap, and it kills the
    silent-skip footgun. A seed is a cache, so it obeys the rule the in-lineage
    cache obeys — otherwise `--seed` would skip work a plain resume re-runs.
    """
    import sys

    flow = {
        "name": "seedshell",
        "nodes": [
            {"id": "a", "kind": "fake", "spec": {"outputs": ["one"]}},
            {"id": "s", "kind": "shell", "final": True, "depends_on": ["a"],
             "spec": {"cmd": [sys.executable, "-c", "print('ran')"]}},
        ],
    }
    source = _record(tmp_path, git_repo, flow=flow)
    h = _seeded(tmp_path, git_repo, source, flow=flow)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["a"].seeded_from, "the harness-side node is inherited"
    assert st.nodes["s"].seeded_from is None, "the shell node ran again"
    assert (h.run_dir / "phases" / "s" / "stdout.log").read_text(encoding="utf-8").strip() == "ran"


def test_cli_refuses_a_seed_that_is_not_a_run_dir(tmp_path, git_repo, capsys):
    """Fail before anything runs, and say which of the two words is wrong."""
    from lockstep import EXIT_CONFIG
    from lockstep.cli import main

    flow_path = tmp_path / "f.tg.json"
    flow_path.write_text(json.dumps(FLOW), encoding="utf-8")
    code = main(["run", str(flow_path), "--seed", str(tmp_path / "nope"),
                 "--repo-root", str(git_repo), "--runs-dir", str(tmp_path / "runs")])
    assert code == EXIT_CONFIG
    assert "not a run directory" in capsys.readouterr().err


def test_cli_refuses_seed_with_replay(tmp_path, git_repo, capsys):
    """Both serve recorded results, on opposite defaults — replay errors on a
    miss, a seed runs it — so the combination has no single meaning."""
    from lockstep import EXIT_CONFIG
    from lockstep.cli import main

    source = _record(tmp_path, git_repo)
    flow_path = tmp_path / "f.tg.json"
    flow_path.write_text(json.dumps(FLOW), encoding="utf-8")
    code = main(["run", str(flow_path), "--seed", str(source), "--replay", str(source),
                 "--repo-root", str(git_repo), "--runs-dir", str(tmp_path / "runs")])
    assert code == EXIT_CONFIG
    assert "cannot be combined" in capsys.readouterr().err


def test_status_names_what_was_inherited(tmp_path, git_repo, capsys):
    from lockstep.cli import main

    source = _record(tmp_path, git_repo)
    h = _seeded(tmp_path, git_repo, source)
    assert h.engine.run() == 0
    capsys.readouterr()
    main(["status", str(h.run_dir)])
    out = capsys.readouterr().out
    assert "seeded: 2 node(s) served from" in out
    assert "token spawns: 0" in out
