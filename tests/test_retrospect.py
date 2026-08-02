"""Offline tests for contrib/retrospect.py (proposal §C v0).

Synthetic run dirs shaped like the real thing: events with heal rounds and
blocks, gate results including ROTATED per-round copies, corrective markers in
current and rotated prompts, and cockpit journals for the told-vs-state
comparator.

The privacy projection is tested as a hard boundary, not a style preference:
finding bodies quote code and prompts, and this report is the artifact most
likely to be shown to someone outside the run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "retrospect", Path(__file__).resolve().parents[1] / "contrib" / "retrospect.py"
)
retrospect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(retrospect)

SECRET = "def compute_seasoning_baseline(chamber):  # PROPRIETARY"


def make_run(root: Path, name: str, *, flow: str = "hygiene", fhash: str = "aaaa1111",
             spawns: int = 6, heals: int = 0, blocks: int = 0, correctives: int = 0,
             journal: list[dict] | None = None, pending: int = 0) -> Path:
    run = root / name
    (run / "phases" / "gate").mkdir(parents=True)
    nodes = {"impl": {"node_id": "impl", "role": "work", "kind": "harness", "status": "done"},
             "gate": {"node_id": "gate", "role": "gate", "kind": "harness", "status": "done"}}
    for i in range(pending):
        nodes[f"todo{i}"] = {"node_id": f"todo{i}", "role": "work", "kind": "harness",
                             "status": "pending"}
    (run / "state.json").write_text(json.dumps({
        "flow_name": flow, "flow_hash": fhash, "token_spawns": spawns, "nodes": nodes,
    }), encoding="utf-8")
    (run / "flow.tg.json").write_text(json.dumps({
        "name": flow,
        "nodes": [{"id": "gate", "heal": {"max_rounds": 2, "targets": ["impl"]}}],
    }), encoding="utf-8")

    events = [{"ts": "2026-08-01T10:00:00Z", "node": "impl", "status": "running"},
              {"ts": "2026-08-01T10:01:00Z", "node": "impl", "status": "done"}]
    for _ in range(heals):
        events.append({"ts": "2026-08-01T10:02:00Z", "node": "gate", "status": "heal-round"})
    for _ in range(blocks):
        events.append({"ts": "2026-08-01T10:03:00Z", "node": "gate", "status": "blocked"})
    (run / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + '\n{"ts": "trunc', encoding="utf-8")

    verdict = {"verdict": "block", "reason": SECRET, "findings": [
        {"severity": "major", "category": "misclassification", "file": "atoms/x.md",
         "claim": SECRET, "evidence": SECRET, "fix_hint": SECRET},
        {"severity": "minor", "category": "style", "file": "atoms/y.md",
         "claim": SECRET, "evidence": SECRET},
    ]}
    (run / "phases" / "gate" / "result.json").write_text(json.dumps(verdict), encoding="utf-8")
    # A rotated per-round result: the rounds that were healed away still count.
    (run / "phases" / "gate" / "result-attempt1.json").write_text(
        json.dumps({"verdict": "block", "reason": SECRET, "findings": [
            {"severity": "blocker", "category": "misclassification", "claim": SECRET}]}),
        encoding="utf-8")

    impl = run / "phases" / "impl"
    impl.mkdir()
    (impl / "prompt.txt").write_text("do the thing\n", encoding="utf-8")
    for i in range(correctives):
        # The engine's real preamble wording — matched, never quoted into output.
        target = impl / ("prompt.txt" if i == 0 else f"prompt-attempt{i}.txt")
        target.write_text(
            "original task\n\nA previous attempt at this task produced output that "
            "failed contract validation. The invalid output was:\n" + SECRET,
            encoding="utf-8")

    if journal is not None:
        (run / "cockpit-journal.jsonl").write_text(
            "\n".join(json.dumps(e) for e in journal) + "\n", encoding="utf-8")
    return run


# --- extraction ----------------------------------------------------------------

def test_collects_heals_blocks_and_rotated_findings(tmp_path):
    run = make_run(tmp_path, "r1", heals=2, blocks=1)
    got = retrospect.collect_run(run)
    assert got["heal_rounds"]["gate"] == 2
    assert got["blocks"]["gate"] == 1
    # 2 findings in result.json + 1 in the rotated attempt = every round counted
    assert len(got["findings"]) == 3
    assert got["gate_budget"]["gate"] == 2


def test_corrective_markers_counted_in_current_and_rotated_prompts(tmp_path):
    run = make_run(tmp_path, "r1", correctives=2)
    assert retrospect.collect_run(run)["correctives"] == 2


def test_a_run_with_no_correctives_counts_zero(tmp_path):
    run = make_run(tmp_path, "r1")
    assert retrospect.collect_run(run)["correctives"] == 0


def test_cascade_blocks_are_not_counted_as_gate_blocks(tmp_path):
    """When a gate blocks, every descendant is marked blocked too. Counting
    those would inflate blocks-per-gate by the width of the graph below the
    gate — and blocks-per-gate is the metric the whole report is judged on."""
    run = make_run(tmp_path, "r1", blocks=1)
    events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()[:-1]
    events.append(json.dumps({"ts": "2026-08-01T10:04:00Z", "node": "impl",
                              "status": "blocked"}))          # work node, cascade
    events.append(json.dumps({"ts": "2026-08-01T10:04:01Z", "node": "approve",
                              "status": "blocked"}))          # approval rejection
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["approve"] = {"node_id": "approve", "role": "approval",
                                 "kind": "", "status": "blocked"}
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")

    blocks = retrospect.collect_run(run)["blocks"]
    assert blocks == {"gate": 1}          # not impl, not approve


def test_a_gate_cascade_blocked_by_an_upstream_gate_is_not_counted(tmp_path):
    """A downstream gate that never ran rendered no verdict. The engine labels
    that block 'gate <id> blocked: ...' — counting it would credit a gate with
    a judgment it never made."""
    run = make_run(tmp_path, "r1", blocks=1)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["gate2"] = {"node_id": "gate2", "role": "gate", "kind": "shell",
                               "status": "blocked"}
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()[:-1]
    events.append(json.dumps({"ts": "2026-08-01T10:05:00Z", "node": "gate2",
                              "status": "blocked",
                              "error": "gate gate blocked: upstream said no"}))
    (run / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")

    assert retrospect.collect_run(run)["blocks"] == {"gate": 1}


def test_transcript_subtrees_are_skipped(tmp_path):
    run = make_run(tmp_path, "r1")
    sessions = run / "phases" / "session"
    sessions.mkdir()
    (sessions / "result.json").write_text(json.dumps(
        {"findings": [{"severity": "blocker", "category": "from-a-transcript"}]}),
        encoding="utf-8")
    cats = {f["category"] for f in retrospect.collect_run(run)["findings"]}
    assert "from-a-transcript" not in cats


# --- the privacy boundary ------------------------------------------------------

def test_projection_strips_every_body(tmp_path):
    run = make_run(tmp_path, "r1", heals=1, blocks=1, correctives=1)
    got = retrospect.collect_run(run)
    blob = json.dumps(got)
    assert SECRET not in blob
    assert all(set(f) <= {"severity", "category", "file", "gate"} for f in got["findings"])


def test_secret_never_reaches_the_rendered_report(tmp_path):
    make_run(tmp_path, "r1", heals=1, blocks=1, correctives=1)
    make_run(tmp_path, "r2", heals=2, blocks=1)
    runs = [retrospect.collect_run(d) for d in sorted(tmp_path.iterdir())]
    text = retrospect.render(retrospect.build_cohorts(runs))
    assert SECRET not in text
    assert "misclassification" in text      # the metadata still lands


# --- cohorts and trend ---------------------------------------------------------

def test_cohorts_split_on_flow_hash(tmp_path):
    make_run(tmp_path, "before1", fhash="old00000", heals=3)
    make_run(tmp_path, "before2", fhash="old00000", heals=3)
    make_run(tmp_path, "after1", fhash="new11111", heals=1)
    runs = [retrospect.collect_run(d) for d in sorted(tmp_path.iterdir())]
    cohorts = retrospect.build_cohorts(runs)
    assert len(cohorts) == 2
    assert {len(v) for v in cohorts.values()} == {2, 1}


def test_trend_names_an_improvement_and_a_regression(tmp_path):
    make_run(tmp_path, "a-before", fhash="old00000", heals=4)
    make_run(tmp_path, "b-after", fhash="new11111", heals=1)
    runs = [retrospect.collect_run(d) for d in sorted(tmp_path.iterdir())]
    text = retrospect.render(retrospect.build_cohorts(runs))
    assert "IMPROVED" in text

    other = tmp_path / "regressed"
    other.mkdir()
    make_run(other, "a-before", fhash="old00000", heals=1)
    make_run(other, "b-after", fhash="new11111", heals=4)
    runs2 = [retrospect.collect_run(d) for d in sorted(other.iterdir())]
    text2 = retrospect.render(retrospect.build_cohorts(runs2))
    assert "REGRESSED" in text2
    assert "a finding, not noise" in text2


def test_trend_direction_follows_time_not_dict_order(tmp_path):
    """The cohort that ran FIRST is the 'before'. Feeding the cohorts in
    reverse must not flip the verdict — a direction derived from incidental
    ordering is a coin flip wearing a verdict's clothes."""
    make_run(tmp_path, "flow-20260101T000000Z", fhash="old00000", heals=4)
    make_run(tmp_path, "flow-20260201T000000Z", fhash="new11111", heals=1)
    runs = [retrospect.collect_run(d) for d in sorted(tmp_path.iterdir())]

    forward = retrospect.render(retrospect.build_cohorts(runs))
    reverse = retrospect.render(retrospect.build_cohorts(list(reversed(runs))))
    assert "IMPROVED" in forward
    assert "IMPROVED" in reverse, "verdict flipped when cohorts arrived in a different order"


def test_single_cohort_says_no_before_after_yet(tmp_path):
    make_run(tmp_path, "only")
    runs = [retrospect.collect_run(tmp_path / "only")]
    assert "no before/after yet" in retrospect.render(retrospect.build_cohorts(runs))


# --- told-vs-state + tripwire (M2/M3) -----------------------------------------

def test_spending_past_the_stated_consent_cap_is_drift(tmp_path):
    run = make_run(tmp_path, "r1", spawns=40,
                   journal=[{"kind": "consent", "cap": 25, "deliverable": "weekly"}])
    drift = retrospect.told_vs_state(retrospect.collect_run(run))
    assert any("25" in d and "40" in d for d in drift)


def test_handoff_claiming_quiescence_with_pending_work_is_drift(tmp_path):
    run = make_run(tmp_path, "r1", pending=2,
                   journal=[{"kind": "handoff", "node": "approve", "quiescent": True}])
    drift = retrospect.told_vs_state(retrospect.collect_run(run))
    assert any("claimed quiescent" in d for d in drift)


def test_honest_consent_and_handoff_produce_no_drift(tmp_path):
    run = make_run(tmp_path, "r1", spawns=9,
                   journal=[{"kind": "consent", "cap": 25, "deliverable": "weekly"},
                            {"kind": "handoff", "node": "approve", "quiescent": True}])
    assert retrospect.told_vs_state(retrospect.collect_run(run)) == []


def test_tripwire_flags_a_relay_that_shares_nothing_with_the_question(tmp_path):
    run = make_run(tmp_path, "r1", journal=[{
        "kind": "clarify", "target": "draft",
        "finding": "Which chamber set applies to the CX-09 seasoning baseline?",
        "relay": "should we include weekends in the totals",
        "answer": "use CX-09 only", "steer": "use CX-09 only",
    }])
    trip = retrospect.fidelity_tripwire(retrospect.collect_run(run))
    assert any("finding->relay" in t for t in trip)
    assert not any("answer->steer" in t for t in trip)


def test_tripwire_is_silent_on_a_faithful_relay(tmp_path):
    run = make_run(tmp_path, "r1", journal=[{
        "kind": "clarify", "target": "draft",
        "finding": "Which chamber set applies to the CX-09 seasoning baseline?",
        "relay": "Which chamber set applies for the CX-09 seasoning baseline?",
        "answer": "CX-09 only, exclude CX-07", "steer": "CX-09 only, exclude CX-07",
    }])
    assert retrospect.fidelity_tripwire(retrospect.collect_run(run)) == []


# --- CLI -----------------------------------------------------------------------

def test_main_writes_a_report(tmp_path, capsys):
    make_run(tmp_path, "r1", heals=1)
    out = tmp_path / "report.md"
    assert retrospect.main([str(tmp_path), "--out", str(out)]) == 0
    assert "friction report" in out.read_text(encoding="utf-8")


def test_lineages_directory_is_not_mistaken_for_a_run(tmp_path):
    make_run(tmp_path, "r1")
    (tmp_path / "lineages").mkdir()
    (tmp_path / "lineages" / "weekly.runs").write_text("x\n", encoding="utf-8")
    assert retrospect.main([str(tmp_path)]) == 0


def test_empty_root_is_exit_2(tmp_path, capsys):
    assert retrospect.main([str(tmp_path)]) == 2
