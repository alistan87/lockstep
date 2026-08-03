"""Offline tests for contrib/quiescent.py (proposal rev 6/7, B2 + R-B3).

This predicate decides whether the cockpit may hand a run to a non-programmer.
A wrong `0` puts a live work queue inside the DE's terminal, so every exit code
is pinned here as a unit rather than being exercised only through the manual
pane drill.

The fixtures mirror the engine's resume semantics (roles.py `_resume_reset`):
running/failed/blocked become pending, a done approval re-runs, and a done node
with unconsumed mail re-runs (r6 C2).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "quiescent", Path(__file__).resolve().parents[1] / "contrib" / "quiescent.py"
)
quiescent = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(quiescent)


def make_run(tmp_path: Path, nodes: dict, flow_nodes: list | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    (run / "state.json").write_text(json.dumps({
        "schema_version": "1.0", "flow_name": "f", "flow_hash": "h",
        "format_version": "1.0", "args": {}, "nodes": nodes,
    }), encoding="utf-8")
    if flow_nodes is not None:
        (run / "flow.tg.json").write_text(json.dumps({"name": "f", "nodes": flow_nodes}),
                                          encoding="utf-8")
    return run


def node(role="work", status="done", kind="harness", **extra):
    return {"node_id": "x", "role": role, "kind": kind, "status": status, **extra}


def steer(run: Path, node_id: str, message: str = "use the Q2 dataset", consumed: bool = False):
    box = run / "mailbox"
    box.mkdir(exist_ok=True)
    with open(box / f"{node_id}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-08-01T10:00:00Z", "author": "local-user",
                             "message": message, "consumed": consumed}) + "\n")


# --- exit 0: the one safe shape ------------------------------------------------

def test_blocked_approval_with_everything_else_done_is_quiescent(tmp_path, capsys):
    run = make_run(tmp_path, {
        "plan": node(status="done"),
        "review": node(role="gate", status="done"),
        "approve": node(role="approval", kind="", status="blocked"),
    })
    assert quiescent.main([str(run)]) == 0
    assert capsys.readouterr().out.strip() == "approve"


def test_pending_approval_is_also_quiescent(tmp_path):
    run = make_run(tmp_path, {"plan": node(), "approve": node(role="approval", status="pending")})
    assert quiescent.main([str(run)]) == 0


def test_mail_addressed_to_the_approval_itself_is_not_a_blocker(tmp_path):
    """It folds into the prompt the DE is about to answer — nothing re-runs."""
    run = make_run(tmp_path, {"plan": node(), "approve": node(role="approval", status="blocked")})
    steer(run, "approve")
    assert quiescent.main([str(run)]) == 0


def test_consumed_mail_on_a_done_node_is_not_a_blocker(tmp_path):
    run = make_run(tmp_path, {"plan": node(), "approve": node(role="approval", status="blocked")})
    steer(run, "plan", consumed=True)
    assert quiescent.main([str(run)]) == 0


# --- a finished run is not a pending decision ----------------------------------

def test_a_completed_run_is_not_offered_for_handoff(tmp_path, capsys):
    """An approval record stays `done` after the human answers, and a done
    approval re-runs on resume (approvals are never resume-skipped). Without a
    guard, a FINISHED run reports quiescent, the cockpit spawns an approval
    pane, and the human is asked to decide something already decided and
    already delivered. "Needs you" has to mean something needs them."""
    run = make_run(
        tmp_path,
        {"produce": node(status="done"),
         "approve": node(role="approval", status="done"),
         "deliver": node(kind="shell", status="done")},
        flow_nodes=[{"id": "produce"}, {"id": "approve", "depends_on": ["produce"]},
                    {"id": "deliver", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 1
    assert "FINISHED" in capsys.readouterr().err


def test_a_done_approval_with_unfinished_work_downstream_IS_offered(tmp_path, capsys):
    """The real case this must not break: the human approved, the delivery node
    then failed, and the run genuinely needs the approval re-answered."""
    run = make_run(
        tmp_path,
        {"produce": node(status="done"),
         "approve": node(role="approval", status="done"),
         "deliver": node(kind="shell", status="failed")},
        flow_nodes=[{"id": "produce"}, {"id": "approve", "depends_on": ["produce"]},
                    {"id": "deliver", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 0
    assert capsys.readouterr().out.strip() == "approve"


def test_a_skipped_only_tail_still_counts_as_finished(tmp_path):
    run = make_run(
        tmp_path,
        {"approve": node(role="approval", status="done"),
         "optional": node(kind="shell", status="skipped")},
        flow_nodes=[{"id": "approve"}, {"id": "optional", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 1


# --- the trivial tail: what the segmentation rule actually permits -------------

def test_a_shell_node_after_the_approval_is_the_sanctioned_shape(tmp_path, capsys):
    """The flagship flow is approval -> copy the deliverable out. That shell
    node runs in the human's process for a second, by design. Treating it as a
    blocker would make the handoff impossible for every correct flow."""
    run = make_run(
        tmp_path,
        {"produce": node(status="done"),
         "approve": node(role="approval", status="blocked"),
         "deliver": node(kind="shell", status="blocked")},
        flow_nodes=[{"id": "produce"},
                    {"id": "approve", "depends_on": ["produce"]},
                    {"id": "deliver", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 0
    assert capsys.readouterr().out.strip() == "approve"


def test_a_harness_node_after_the_approval_blocks_the_handoff(tmp_path, capsys):
    """This is the sdlc-e2e shape: an implement phase downstream of an approval
    would execute inside the human's terminal for many minutes."""
    run = make_run(
        tmp_path,
        {"plan": node(status="done"),
         "approve": node(role="approval", status="blocked"),
         "implement": node(kind="harness", status="blocked")},
        flow_nodes=[{"id": "plan"},
                    {"id": "approve", "depends_on": ["plan"]},
                    {"id": "implement", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 1
    err = capsys.readouterr().err
    assert "implement" in err and "segmentation rule" in err


def test_transitive_descendants_of_the_approval_are_recognised(tmp_path):
    run = make_run(
        tmp_path,
        {"approve": node(role="approval", status="blocked"),
         "copy": node(kind="shell", status="blocked"),
         "summarise": node(kind="shell", status="blocked")},
        flow_nodes=[{"id": "approve"},
                    {"id": "copy", "depends_on": ["approve"]},
                    {"id": "summarise", "depends_on": ["copy"]}],
    )
    assert quiescent.main([str(run)]) == 0


def test_pending_work_NOT_downstream_of_the_approval_still_blocks(tmp_path, capsys):
    run = make_run(
        tmp_path,
        {"sibling": node(status="pending"),
         "approve": node(role="approval", status="blocked"),
         "deliver": node(kind="shell", status="blocked")},
        flow_nodes=[{"id": "sibling"}, {"id": "approve"},
                    {"id": "deliver", "depends_on": ["approve"]}],
    )
    assert quiescent.main([str(run)]) == 1
    assert "sibling" in capsys.readouterr().err


def test_without_a_flow_copy_the_strict_answer_is_used(tmp_path, capsys):
    """No flow file means a trivial tail cannot be told from real work, so the
    check refuses rather than guessing in the permissive direction."""
    run = make_run(tmp_path, {"approve": node(role="approval", status="blocked"),
                              "deliver": node(kind="shell", status="blocked")})
    assert quiescent.main([str(run)]) == 1
    assert "no flow copy" in capsys.readouterr().err


# --- exit 1: every way a handoff is unsafe -------------------------------------

def test_unconsumed_steer_on_a_done_node_blocks(tmp_path, capsys):
    run = make_run(tmp_path, {"plan": node(), "approve": node(role="approval", status="blocked")})
    steer(run, "plan")
    assert quiescent.main([str(run)]) == 1
    err = capsys.readouterr().err
    assert "plan" in err and "unconsumed steer" in err


def test_pending_work_node_blocks(tmp_path, capsys):
    run = make_run(tmp_path, {
        "plan": node(), "impl": node(status="pending"),
        "approve": node(role="approval", status="blocked"),
    })
    assert quiescent.main([str(run)]) == 1
    assert "impl" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["running", "failed", "blocked"])
def test_reactivated_statuses_block(tmp_path, capsys, status):
    run = make_run(tmp_path, {
        "impl": node(status=status), "approve": node(role="approval", status="blocked"),
    })
    assert quiescent.main([str(run)]) == 1
    assert "impl" in capsys.readouterr().err


def test_pending_map_item_under_a_done_node_blocks(tmp_path, capsys):
    run = make_run(tmp_path, {
        "classify": node(role="map", status="done",
                         items={"0": {"status": "done"}, "1": {"status": "pending"}}),
        "approve": node(role="approval", status="blocked"),
    })
    assert quiescent.main([str(run)]) == 1
    assert "classify[1]" in capsys.readouterr().err


def test_pending_work_but_no_approval_node_is_not_quiescent(tmp_path, capsys):
    """Work left to do and nothing to decide: resuming would run it, but there
    is no handoff to make. (An all-done flow with no approval reports FINISHED
    instead — see the completed-run test above.)"""
    run = make_run(tmp_path, {"plan": node(), "ship": node(kind="shell", status="pending")})
    assert quiescent.main([str(run)]) == 1
    err = capsys.readouterr().err
    assert "ship" in err
    # The orchestrator must be told resuming will never yield a handoff here,
    # or it loops forever waiting for a decision point that does not exist.
    assert "NO approval awaiting" in err


def test_two_awaiting_approvals_block(tmp_path, capsys):
    """Segmentation exists so the DE's process owns one decision, not a chain."""
    run = make_run(tmp_path, {
        "approve-a": node(role="approval", status="blocked"),
        "approve-b": node(role="approval", status="pending"),
    })
    assert quiescent.main([str(run)]) == 1
    assert "2 approvals" in capsys.readouterr().err


def test_the_canonical_non_quiescent_case_answered_clarification(tmp_path, capsys):
    """The drill from the test plan: the DE answered a clarification, the steer
    landed on a done node, and the run then reached the approval. Handing over
    now would re-run the steered node in the DE's terminal."""
    run = make_run(tmp_path, {
        "draft": node(status="done"),
        "clarify": node(role="gate", status="done"),
        "approve": node(role="approval", status="blocked"),
    })
    steer(run, "draft", "chamber CX-09, not CX-07")
    assert quiescent.main([str(run)]) == 1
    assert "draft" in capsys.readouterr().err


# --- exit 2: unreadable --------------------------------------------------------

def test_missing_run_dir_is_exit_2(tmp_path):
    assert quiescent.main([str(tmp_path / "nope")]) == 2


def test_half_written_state_json_is_exit_2_not_a_crash(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json").write_text('{"nodes": {"a": ', encoding="utf-8")
    assert quiescent.main([str(run)]) == 2


def test_quiet_mode_prints_nothing(tmp_path, capsys):
    run = make_run(tmp_path, {"approve": node(role="approval", status="blocked")})
    assert quiescent.main([str(run), "--quiet"]) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_a_skipped_node_blocks_because_its_when_re_evaluates(tmp_path, capsys):
    """The engine resets `skipped` to pending on resume so the node's `when`
    re-evaluates against possibly-re-run upstreams — meaning a skipped node CAN
    run in the human's terminal. Omitting it from REACTIVATED made this check
    fail OPEN, the one direction it must never fail."""
    run = make_run(
        tmp_path,
        {"maybe": node(status="skipped"),
         "approve": node(role="approval", status="blocked")},
        flow_nodes=[{"id": "maybe"}, {"id": "approve", "depends_on": ["maybe"]}],
    )
    assert quiescent.main([str(run)]) == 1
    assert "maybe" in capsys.readouterr().err


def test_reactivated_mirrors_the_engine_reset_set():
    """Pin the hand-copy against the engine's own source. If _resume_reset
    grows a status and this list does not, the drift is silent and unsafe."""
    from pathlib import Path as _P
    src = _P(__file__).resolve().parents[1] / "src" / "lockstep" / "roles.py"
    text = src.read_text(encoding="utf-8")
    assert 'rec.status in ("running", "failed", "blocked")' in text, \
        "engine reset set changed; re-check contrib/quiescent.py REACTIVATED"
    assert 'elif rec.status == "skipped"' in text, \
        "engine no longer resets skipped; re-check contrib/quiescent.py REACTIVATED"
