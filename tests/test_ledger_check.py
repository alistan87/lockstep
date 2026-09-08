"""The adjudicated-review programme's memory (upstream-response-ow07-feedback
S2+G5): `ledger_check` owns the ledger file; the model only proposes.

The acceptance shape from the consumer report, verbatim: round 1 emits A/B,
round 2 emits B/C — the ledger reports A resolved, B persisting, C new. And
the test no prompt could pass: an accepted-risk disposition cannot be
silently removed by a later model response, because no model response ever
touches the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from lockstep.contracts import Verdict
from lockstep.gates import ledger_check


def run_gate(module, argv, capsys) -> dict:
    assert module.main(argv) == 0, "gates exit 0; a blocking verdict is a result"
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    Verdict.model_validate(data)
    return data


def F(claim, severity="major", category="correctness", file="src/x.py", **kw):
    base = {"severity": severity, "category": category, "file": file,
            "claim": claim, "evidence": f"evidence for {claim}", "fix_hint": ""}
    base.update(kw)
    return base


def _round(tmp_path, capsys, findings, name="r.json", at="major"):
    p = tmp_path / name
    p.write_text(json.dumps(findings), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    return run_gate(ledger_check, ["--at", at, "--ledger", str(ledger), str(p)],
                    capsys), ledger


def _entries(ledger: Path) -> dict:
    data = json.loads(ledger.read_text(encoding="utf-8"))
    return {e["claim"]: e for e in data["entries"]}, data


def test_rounds_track_resolved_persisting_new(tmp_path, capsys):
    v, ledger = _round(tmp_path, capsys, [F("A"), F("B")], "r1.json")
    assert v["verdict"] == "block"
    entries, data = _entries(ledger)
    assert data["round"] == 1
    assert entries["A"]["state"] == "new" and entries["B"]["state"] == "new"

    v, _ = _round(tmp_path, capsys, [F("B"), F("C")], "r2.json")
    assert v["verdict"] == "block"
    entries, data = _entries(ledger)
    assert data["round"] == 2
    assert entries["A"]["state"] == "reported-resolved"
    assert "round 2" in entries["A"]["disposition"]
    assert entries["B"]["state"] == "persisting"
    assert entries["B"]["first_round"] == 1 and entries["B"]["last_seen_round"] == 2
    assert entries["C"]["state"] == "new" and entries["C"]["first_round"] == 2

    v, _ = _round(tmp_path, capsys, [], "r3.json")
    assert v["verdict"] == "pass"
    entries, data = _entries(ledger)
    assert data["round"] == 3
    assert all(e["state"] == "reported-resolved" for e in entries.values())
    assert "ledger round 3" in v["reason"]


def test_accepted_risk_stands_and_never_gates(tmp_path, capsys):
    v, ledger = _round(tmp_path, capsys, [F("A")], "r1.json")
    assert v["verdict"] == "block"
    # A human accepts the risk, attributably, in the file.
    data = json.loads(ledger.read_text(encoding="utf-8"))
    data["entries"][0]["state"] = "accepted-risk"
    data["entries"][0]["disposition"] = "owner 2026-09-08: acceptable until v2"
    ledger.write_text(json.dumps(data), encoding="utf-8")
    # The model re-raises the same finding; the standing decision holds.
    v, _ = _round(tmp_path, capsys, [F("A")], "r2.json")
    assert v["verdict"] == "pass"
    entries, data = _entries(ledger)
    assert entries["A"]["state"] == "accepted-risk"
    assert entries["A"]["disposition"].startswith("owner")
    assert entries["A"]["last_seen_round"] == data["round"]


def test_unattributed_accepted_risk_blocks_and_writes_nothing(tmp_path, capsys):
    _, ledger = _round(tmp_path, capsys, [F("A")], "r1.json")
    data = json.loads(ledger.read_text(encoding="utf-8"))
    data["entries"][0]["state"] = "accepted-risk"  # no disposition: not a decision
    ledger.write_text(json.dumps(data), encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")
    v, _ = _round(tmp_path, capsys, [F("A")], "r2.json")
    assert v["verdict"] == "block"
    assert "not attributable" in json.dumps(v["findings"])
    assert ledger.read_text(encoding="utf-8") == before, "fail closed: no write"


def test_rerun_with_identical_input_is_idempotent(tmp_path, capsys):
    # Shell gates always re-run (§0.1.7): resume revalidation must not
    # inflate the round counter or rewrite history.
    v1, ledger = _round(tmp_path, capsys, [F("A")], "r1.json")
    before = ledger.read_text(encoding="utf-8")
    v2, _ = _round(tmp_path, capsys, [F("A")], "r1b.json")
    assert v1["verdict"] == v2["verdict"] == "block"
    assert ledger.read_text(encoding="utf-8") == before
    assert json.loads(before)["round"] == 1


def test_a_returned_finding_goes_back_to_persisting_with_history(tmp_path, capsys):
    _, ledger = _round(tmp_path, capsys, [F("A")], "r1.json")
    _round(tmp_path, capsys, [], "r2.json")           # A reported-resolved
    v, _ = _round(tmp_path, capsys, [F("A")], "r3.json")  # it returns
    assert v["verdict"] == "block"
    entries, _ = _entries(ledger)
    assert entries["A"]["state"] == "persisting"
    assert "returned in round 3" in entries["A"]["disposition"]
    assert entries["A"]["first_round"] == 1, "identity survives the round trip"


def test_minor_findings_are_remembered_but_never_gate(tmp_path, capsys):
    v, ledger = _round(tmp_path, capsys, [F("nitpick", severity="nit")], "r1.json")
    assert v["verdict"] == "pass"
    entries, _ = _entries(ledger)
    assert entries["nitpick"]["state"] == "new", "recorded, not gating"


def test_malformed_severity_blocks_and_never_enters_the_ledger(tmp_path, capsys):
    v, ledger = _round(tmp_path, capsys, [F("A", severity="critical")], "r1.json")
    assert v["verdict"] == "block"
    assert not ledger.exists(), "fail closed: nothing written"


def test_unreadable_ledger_blocks_rather_than_overwrites(tmp_path, capsys):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{torn", encoding="utf-8")
    p = tmp_path / "r.json"
    p.write_text(json.dumps([F("A")]), encoding="utf-8")
    v = run_gate(ledger_check, ["--ledger", str(ledger), str(p)], capsys)
    assert v["verdict"] == "block"
    assert ledger.read_text(encoding="utf-8") == "{torn", "history preserved"


def test_stable_id_survives_whitespace_and_case(tmp_path, capsys):
    _, ledger = _round(tmp_path, capsys,
                       [F("The  API returns  NULL", file="SRC/x.py")], "r1.json")
    v, _ = _round(tmp_path, capsys,
                  [F("the api returns null", file="src/x.py")], "r2.json")
    entries, _ = _entries(ledger)
    assert len(entries) == 1, "normalization: one identity, not two"
    assert list(entries.values())[0]["state"] == "persisting"
