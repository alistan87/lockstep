"""Offline tests for the repo-hygiene cockpit demo (contrib/demo/).

The demo exists to show the cockpit end to end, but its deterministic half is
the part that must actually be correct: it decides what a model never sees
(cost escalation) and it is the gate that stands between a proposed manifest
and someone's repo. All of that is testable without a token.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "contrib" / "demo"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEMO / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


catalog = _load("hygiene_catalog")
validate = _load("validate_manifest")
evidence = _load("hygiene_evidence")


def entry(path="docs/a.md", action="keep", target=None, confidence="high", why="because"):
    return {"path": path, "action": action, "target_path": target,
            "okf_type": "notes", "confidence": confidence, "rule_ref": None, "why": why}


# --- the rule engine -----------------------------------------------------------

def test_first_matching_rule_wins():
    """Rule ORDER is the policy: an earlier rule governs, it does not conflict."""
    rule, okf, disp = catalog.classify("docs/SPEC.md")
    assert (rule, okf, disp) == ("R-001", "spec", "conforming")


def test_unmatched_file_is_unknown_not_guessed():
    rule, okf, disp = catalog.classify("docs/some-new-thing.md")
    assert disp == "unknown" and rule == "-"


def test_two_rules_at_equal_precedence_are_a_real_conflict():
    """The clarify gate's whole reason to exist: both rules are legitimate and
    only the taxonomy's owner can say which wins."""
    assert catalog.conflicts_for("docs/repo-hygiene-work-order.md") == ["R-030", "R-032"]


def test_a_file_matching_one_rule_is_not_a_conflict():
    assert catalog.conflicts_for("docs/SPEC.md") == []


# --- the deterministic gate ----------------------------------------------------

def test_clean_manifest_passes():
    findings, verdict = validate.validate([entry()])
    assert verdict == "pass" and findings == []


@pytest.mark.parametrize("target", ["../../etc/passwd", "/etc/passwd", "C:/windows/x.md",
                                    "docs/../../escape.md"])
def test_paths_that_leave_the_repo_are_blockers(target):
    findings, verdict = validate.validate([entry(action="move", target=target)])
    assert verdict == "block"
    assert any(f["category"] in ("escape", "illegal-target") for f in findings)


def test_target_outside_allowed_roots_is_a_blocker():
    findings, verdict = validate.validate([entry(action="move", target="src/lockstep/x.md")])
    assert verdict == "block"
    assert any(f["category"] == "illegal-target" for f in findings)


def test_two_files_onto_one_target_is_a_blocker():
    """Without this check the apply engine silently destroys one of them."""
    findings, verdict = validate.validate([
        entry(path="docs/a.md", action="move", target="atoms/x.md"),
        entry(path="docs/b.md", action="move", target="atoms/x.md"),
    ])
    assert verdict == "block"
    assert any(f["category"] == "collision" for f in findings)


def test_move_without_a_destination_is_a_blocker():
    findings, verdict = validate.validate([entry(action="move", target=None)])
    assert verdict == "block"
    assert any(f["category"] == "unexecutable" for f in findings)


def test_same_file_twice_is_a_blocker():
    findings, verdict = validate.validate([entry(path="docs/a.md"), entry(path="docs/a.md")])
    assert verdict == "block"
    assert any(f["category"] == "duplicate" for f in findings)


def test_unsorted_entries_flag_nondeterminism():
    findings, _ = validate.validate([entry(path="docs/b.md"), entry(path="docs/a.md")])
    assert any(f["category"] == "nondeterminism" for f in findings)


def test_move_onto_itself_is_a_no_op():
    findings, _ = validate.validate([entry(path="docs/a.md", action="move", target="docs/a.md")])
    assert any(f["category"] == "no-op" for f in findings)


def test_map_output_is_flattened_from_nested_arrays(tmp_path):
    """The map node collects an ARRAY OF ARRAYS, one per item."""
    p = tmp_path / "m.json"
    p.write_text(json.dumps([[entry(path="docs/a.md")], [entry(path="docs/b.md")]]),
                 encoding="utf-8")
    assert len(validate.load_entries(f"@{p}")) == 2


def test_spilled_manifest_path_is_read(tmp_path):
    """Large interpolations spill to a file and the argv carries the path."""
    p = tmp_path / "spilled.json"
    p.write_text(json.dumps([[entry()]]), encoding="utf-8")
    assert len(validate.load_entries(str(p))) == 1


def test_unreadable_manifest_blocks_but_the_node_still_succeeds(capsys):
    """A gate reports failure through its VERDICT. A crashed gate node would
    look like infrastructure trouble instead of a rejected manifest."""
    assert validate.main(["not json at all"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "block" and "unreadable" in out["reason"]


# --- the evidence extract ------------------------------------------------------

def test_every_low_confidence_entry_appears_in_full():
    entries = [entry(path=f"docs/h{i}.md") for i in range(30)]
    entries.append(entry(path="docs/uncertain.md", confidence="low", why="not sure at all"))
    entries.append(entry(path="docs/flagged.md", action="flag", why="two rules claim it"))
    text = evidence.render(entries, None)
    assert "docs/uncertain.md" in text and "not sure at all" in text
    assert "docs/flagged.md" in text


def test_every_move_appears_in_full():
    entries = [entry(path=f"docs/h{i}.md") for i in range(30)]
    entries.append(entry(path="docs/moved.md", action="move", target="atoms/moved.md"))
    text = evidence.render(entries, None)
    assert "atoms/moved.md" in text


def test_high_confidence_entries_are_sampled_not_dumped():
    entries = [entry(path=f"docs/h{i}.md") for i in range(50)]
    text = evidence.render(entries, None)
    shown = sum(1 for i in range(50) if f"docs/h{i}.md" in text)
    assert 0 < shown < 50


def test_sampling_is_deterministic():
    """The same manifest must produce the same pane twice, or the human cannot
    trust that what they approved is what they saw."""
    entries = [entry(path=f"docs/h{i}.md") for i in range(50)]
    assert evidence.render(entries, None) == evidence.render(entries, None)


def test_a_mostly_uncertain_manifest_says_the_rules_are_the_problem():
    entries = [entry(path=f"docs/u{i}.md", confidence="low") for i in range(10)]
    text = evidence.render(entries, None)
    assert "the RULES are out of date" in text


def test_a_small_wholly_uncertain_manifest_still_warns():
    """Three of three uncertain is the loudest possible signal that the rules
    have nothing to say about this corpus — a count threshold would miss it."""
    entries = [entry(path=f"docs/u{i}.md", confidence="low") for i in range(3)]
    assert "the RULES are out of date" in evidence.render(entries, None)


def test_a_confident_manifest_does_not_cry_wolf():
    entries = [entry(path=f"docs/h{i}.md") for i in range(10)]
    assert "the RULES are out of date" not in evidence.render(entries, None)


def test_a_couple_of_uncertain_entries_in_a_big_manifest_is_not_alarming():
    entries = [entry(path=f"docs/h{i}.md") for i in range(30)]
    entries += [entry(path=f"docs/u{i}.md", confidence="low") for i in range(3)]
    assert "the RULES are out of date" not in evidence.render(entries, None)


def test_evidence_states_what_was_already_checked():
    text = evidence.render([entry()], {"verdict": "pass", "reason": "ok", "findings": []})
    assert "no path escapes" in text and "deterministic gate: pass" in text
