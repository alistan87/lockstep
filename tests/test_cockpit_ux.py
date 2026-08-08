"""Cockpit UX proposal: the artifacts a human decides from.

Three surfaces, one rule between them — a decision point is served by an
artifact, never by a narration:

  T1.9  plan_card.py      the consent beat, backed by prior runs
  T2.1  question_card.py  clarification findings, verbatim
  T2.4  render_evidence   blast radius, reversibility, and prose that fits

The T2.4 tests carry the most weight. F8 recorded the shipped hygiene demo
rendering a ~350-word gate verdict as a single unbroken paragraph on one line
in the evidence a person was asked to decide from — and the domain expert's
guide tells them that not understanding what they are approving is a defect in
the work rather than in them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRIB = ROOT / "contrib"
sys.path.insert(0, str(CONTRIB))

import mission_server  # noqa: E402
import plan_card  # noqa: E402
import question_card  # noqa: E402
import render_evidence as re_mod  # noqa: E402
from lockstep.taskgraph import TaskGraph  # noqa: E402

PY = sys.executable


# ------------------------------------------------------------------ T2.4

def test_wrap_prose_breaks_a_wall_of_text():
    wall = "word " * 400
    lines = re_mod.wrap_prose(wall)
    assert len(lines) <= re_mod.MAX_VERDICT_LINES + 1
    assert all(len(ln) <= re_mod.WIDTH for ln in lines)


def test_wrap_prose_says_that_it_truncated():
    # A truncation that announces itself is honest; a wall that dares the reader
    # to finish it is not.
    lines = re_mod.wrap_prose("word " * 400)
    assert "more lines" in lines[-1]


def test_wrap_prose_leaves_short_text_intact():
    assert re_mod.wrap_prose("a short reason", indent="  ") == ["  a short reason"]
    assert re_mod.wrap_prose("") == []


def test_wrap_prose_can_be_uncapped():
    assert len(re_mod.wrap_prose("word " * 400, max_lines=0)) > re_mod.MAX_VERDICT_LINES


def _evidence(tmp_path: Path, *args: str) -> str:
    out = tmp_path / "ev.txt"
    rc = re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out), *args])
    assert rc == 0
    return out.read_text(encoding="utf-8")


def _doc(tmp_path: Path) -> Path:
    p = tmp_path / "d.md"
    if not p.exists():
        p.write_text("# heading\nbody\n", encoding="utf-8")
    return p


def test_reversibility_is_always_stated_one_way_or_the_other(tmp_path):
    # Silence about reversibility reads as "reversible". It must not.
    assert "not stated by this flow" in _evidence(tmp_path)
    assert "git checkout" in _evidence(tmp_path, "--reversible", "git checkout -- docs/")


def test_irreversible_tier_banners_and_demands_impact(tmp_path):
    text = _evidence(tmp_path, "--tier", "irreversible")
    assert "IRREVERSIBLE" in text
    # No --impact on an irreversible approval is a flow declining to say what it
    # is about to do permanently. That gets said out loud.
    assert "NOT CHARACTERISED" in text


def test_routine_tier_is_labelled_but_still_asks(tmp_path):
    text = _evidence(tmp_path, "--tier", "routine")
    assert "ROUTINE" in text
    # No tier skips the human. Tiering changes presentation and required
    # evidence, never whether a person is asked.
    assert "Decide from this pane" in text


def test_impact_counts_files_and_shouts_about_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout="M\tone.py\nA\ttwo.py\nD\tgone.py\nR100\told.py\tnew.py\n", stderr=""))
    lines = re_mod.impact(tmp_path)
    assert "4 files" in lines[0]
    assert "1 DELETED" in lines[0]
    assert any("something is deleted" in ln for ln in lines)


def test_impact_is_quiet_when_nothing_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout="", stderr=""))
    assert "nothing changed" in re_mod.impact(tmp_path)[0]


def test_impact_never_raises_outside_a_git_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 128, stdout="", stderr="fatal"))
    assert "unavailable" in re_mod.impact(tmp_path)[0]


def test_a_missing_source_is_itself_the_evidence(tmp_path):
    out = tmp_path / "ev.txt"
    assert re_mod.main(["--full", str(tmp_path / "absent.md"), "--out", str(out)]) == 0
    assert "NOT FOUND" in out.read_text(encoding="utf-8")


# ------------------------------------------------------------------ T1.9

FLOW = {
    "name": "cardflow",
    "budget": {"max_agent_spawns": 25},
    "nodes": [
        {"id": "a", "kind": "shell", "spec": {"argv": ["echo", "hi"]}},
        {"id": "b", "kind": "shell", "depends_on": ["a"], "spec": {"argv": ["echo", "hi"]}},
        {"id": "g", "role": "gate", "kind": "shell", "depends_on": ["b"],
         "output": "json", "contract": "Verdict", "spec": {"argv": ["echo", "hi"]},
         "heal": {"max_rounds": 2, "targets": ["b"]}},
        {"id": "ask", "role": "approval", "depends_on": ["g"], "final": True},
    ],
}


def test_plan_card_counts_in_the_domain_experts_vocabulary():
    lines = plan_card.shape(TaskGraph.model_validate(FLOW))
    assert "2 steps of work" in lines
    assert "1 automatic check" in lines
    assert "1 decision from you" in lines
    assert "up to 2 rework rounds" in lines


def test_plan_card_says_when_there_is_no_decision_point():
    # "no decision from you" is a fact about the flow the human should hear
    # BEFORE consenting, not discover when it finishes without asking them.
    flow = dict(FLOW, nodes=[dict(FLOW["nodes"][0], final=True)])
    assert any("no decision point" in ln for ln in plan_card.shape(TaskGraph.model_validate(flow)))


def test_plan_card_states_the_ceiling(tmp_path):
    text = plan_card.render(TaskGraph.model_validate(FLOW), tmp_path / "runs", "h")
    assert "ceiling: 25 agent tasks" in text
    assert "Nothing has started" in text


def test_plan_card_admits_when_there_is_no_history(tmp_path):
    text = plan_card.render(TaskGraph.model_validate(FLOW), tmp_path / "runs", "h")
    assert "none on this machine" in text
    # And never quotes a dollar figure: both target machines bill in quota and
    # one harness never reports usage at all.
    assert "$" not in text


def test_plan_card_never_spends(tmp_path, capsys):
    out = tmp_path / "card.txt"
    flow_file = tmp_path / "f.tg.json"
    flow_file.write_text(json.dumps(dict(FLOW, format_version="1.0")), encoding="utf-8")
    assert plan_card.main([str(flow_file), "--runs-dir", str(tmp_path / "runs"),
                           "--out", str(out)]) == 0
    assert out.is_file()
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


# ------------------------------------------------------------------ T2.1

def _clarify_run(tmp_path: Path, findings: list[dict], reason: str = "two questions") -> Path:
    run = tmp_path / "run"
    (run / "phases" / "ask-expert").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "flow_name": "clar",
        "nodes": {"ask-expert": {"role": "gate", "status": "blocked", "kind": "harness"}},
    }), encoding="utf-8")
    (run / "phases" / "ask-expert" / "result.json").write_text(
        json.dumps({"verdict": "block", "reason": reason, "findings": findings}),
        encoding="utf-8")
    return run


def test_question_card_renders_findings_verbatim(tmp_path):
    run = _clarify_run(tmp_path, [
        {"category": "question", "claim": "Which dataset is authoritative for Q3?"},
        {"category": "question", "claim": "Does 'active' include suspended accounts?"},
    ])
    assert question_card.main([str(run)]) == 0
    text = (run / "question-card.txt").read_text(encoding="utf-8")
    assert "Which dataset is authoritative for Q3?" in text
    assert "Does 'active' include suspended accounts?" in text
    assert "nothing is spending" in text


def test_question_card_labels_findings_that_are_not_questions(tmp_path):
    run = _clarify_run(tmp_path, [{"category": "defect", "claim": "the manifest is empty"}])
    question_card.main([str(run)])
    text = (run / "question-card.txt").read_text(encoding="utf-8")
    assert "[defect]" in text


def test_question_card_says_so_when_a_gate_asked_nothing(tmp_path):
    run = _clarify_run(tmp_path, [])
    question_card.main([str(run)])
    assert "defect in the" in (run / "question-card.txt").read_text(encoding="utf-8")


def test_question_card_removes_a_stale_card(tmp_path):
    # A question the human already answered, still on screen, reads as an
    # unanswered one.
    run = _clarify_run(tmp_path, [{"category": "question", "claim": "x?"}])
    question_card.main([str(run)])
    assert (run / "question-card.txt").is_file()
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["ask-expert"]["status"] = "done"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert question_card.main([str(run)]) == 1
    assert not (run / "question-card.txt").exists()


def test_question_card_does_not_truncate_a_question(tmp_path):
    # A finding too long to read is a defect in the gate's CONTRACT. The card
    # renders what is there rather than paraphrasing around it.
    long_q = "why " * 200
    run = _clarify_run(tmp_path, [{"category": "question", "claim": long_q}])
    question_card.main([str(run)])
    text = (run / "question-card.txt").read_text(encoding="utf-8")
    assert "more lines" not in text
    assert text.count("why") >= 190


# ------------------------------------------------------------------ T3.2

def test_the_page_has_no_route_that_writes():
    handler = mission_server.make_handler(Path("runs"), None, ROOT)
    assert hasattr(handler, "do_GET")
    # The guarantee is the ABSENCE of the code, not a policy about it.
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert not hasattr(handler, verb), f"{verb} must not exist on the MISSION handler"


def test_the_page_renders_from_the_same_functions(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json").write_text(json.dumps({
        "flow_name": "x", "nodes": {"a": {"role": "work", "status": "blocked", "attempts": 1}},
    }), encoding="utf-8")
    page = mission_server.render_page(run, ROOT, tmp_path)
    assert "NEEDS YOU" in page
    assert "needs you" in page          # the glossary word, from mission_view
    assert "Decisions are not made here" in page


def test_the_page_survives_an_empty_run_root():
    assert "no run yet" in mission_server.render_page(None, ROOT, ROOT / "runs")


def test_loopback_is_the_default_and_anything_else_warns(tmp_path, monkeypatch, capsys):
    """What somebody who never reads a flag actually gets.

    runs/ holds prompts, diffs and model output and is gitignored for that
    reason; this server applies no authentication whatsoever. Binding it wider
    than loopback has to be a decision somebody made on purpose and was told
    about.
    """
    served: dict = {}

    class FakeServer:
        def __init__(self, addr, handler):
            served["addr"] = addr

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(mission_server, "ThreadingHTTPServer", FakeServer)

    assert mission_server.main(["--runs-root", str(tmp_path)]) == 0
    assert served["addr"][0] == "127.0.0.1"
    assert "WARNING" not in capsys.readouterr().err

    assert mission_server.main(["--runs-root", str(tmp_path), "--host", "0.0.0.0"]) == 0
    assert served["addr"][0] == "0.0.0.0"
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "no authentication" in err
