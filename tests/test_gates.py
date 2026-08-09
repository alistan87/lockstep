"""B1 — the gate library: each gate is a tested program, not an embedded
one-liner. Every gate prints exactly one Verdict JSON object and exits 0."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lockstep.contracts import Verdict
from lockstep.gates import (
    block_on_severity,
    citation_check,
    coverage_delta,
    numbers_check,
    pi_guard_smoke,
    pytest_verdict,
    required_sections,
    version_sync,
)

ROOT_SRC = Path(__file__).resolve().parents[1] / "src"


def run_gate(module, argv, capsys) -> dict:
    assert module.main(argv) == 0, "gates exit 0; a blocking verdict is a result"
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    Verdict.model_validate(data)  # every gate speaks the built-in contract
    return data


# ------------------------------------------------------- block_on_severity


FINDINGS = [
    {"severity": "blocker", "category": "a", "file": "f", "line": None,
     "claim": "c", "evidence": "e", "fix_hint": "h"},
    {"severity": "major", "category": "a", "file": "f", "line": None,
     "claim": "c", "evidence": "e", "fix_hint": "h"},
    {"severity": "nit", "category": "a", "file": "f", "line": None,
     "claim": "c", "evidence": "e", "fix_hint": "h"},
]


def test_block_on_severity_thresholds(tmp_path, capsys):
    p = tmp_path / "findings.json"
    p.write_text(json.dumps(FINDINGS), encoding="utf-8")
    v = run_gate(block_on_severity, ["--at", "major", str(p)], capsys)
    assert v["verdict"] == "block" and len(v["findings"]) == 2
    v = run_gate(block_on_severity, ["--at", "blocker", str(p)], capsys)
    assert len(v["findings"]) == 1
    p.write_text("[]", encoding="utf-8")
    v = run_gate(block_on_severity, ["--at", "major", str(p)], capsys)
    assert v["verdict"] == "pass"


def test_block_on_severity_unreadable_input_blocks_not_crashes(tmp_path, capsys):
    v = run_gate(block_on_severity, ["--at", "major", str(tmp_path / "missing.json")], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "gate-error"


def test_block_on_severity_node_mode_resolves_via_phase_dir(tmp_path, capsys, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "phases" / "review").mkdir(parents=True)
    (run_dir / "phases" / "gate").mkdir()
    (run_dir / "phases" / "review" / "result.json").write_text(
        json.dumps(FINDINGS), encoding="utf-8"
    )
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run_dir / "phases" / "gate"))
    v = run_gate(block_on_severity, ["--at", "major", "--node", "review"], capsys)
    assert v["verdict"] == "block" and len(v["findings"]) == 2


def test_block_on_severity_requires_exactly_one_source(tmp_path):
    with pytest.raises(SystemExit):
        block_on_severity.main(["--at", "major"])


def test_block_on_severity_unknown_severity_fails_closed(tmp_path, capsys):
    p = tmp_path / "findings.json"
    p.write_text(json.dumps([
        {"severity": "critical", "category": "a", "file": "f", "line": None,
         "claim": "c", "evidence": "e", "fix_hint": "h"},
    ]), encoding="utf-8")
    v = run_gate(block_on_severity, ["--at", "major", str(p)], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "gate-error"
    assert "unknown severity" in v["findings"][0]["claim"]


def test_shell_resolves_bare_python_to_the_driver_interpreter(tmp_path, git_repo):
    """DEVIATIONS 2026-08-05: `python -m lockstep.gates.*` must work even when
    the PATH python cannot import lockstep; the planned argv (and the hash)
    keeps the portable "python"."""
    import sys
    from conftest import build

    flow = {
        "name": "pyres",
        "nodes": [{"id": "which", "kind": "shell", "final": True,
                   "spec": {"cmd": ["python", "-c", "import sys; print(sys.executable)"]}}],
    }
    h = build(tmp_path, flow, git_repo)
    assert h.engine.run() == 0
    reported = (h.run_dir / "phases" / "which" / "result.txt").read_text(encoding="utf-8").strip()
    assert reported == sys.executable
    # The hash side must keep the portable "python": the recorded argv part
    # digest matches the UNRESOLVED command line.
    import hashlib
    from lockstep.state import load_state

    portable = "argv:" + json.dumps(
        ["python", "-c", "import sys; print(sys.executable)"], ensure_ascii=False
    )
    parts = load_state(h.run_dir).nodes["which"].hash_parts
    assert parts["argv"] == hashlib.sha256(portable.encode("utf-8")).hexdigest()


# ------------------------------------------------------- required_sections


def test_required_sections(tmp_path, capsys):
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n## Goal\nx\n## Test plan\ny\n", encoding="utf-8")
    v = run_gate(required_sections, [str(doc), "Goal, Test plan"], capsys)
    assert v["verdict"] == "pass"
    v = run_gate(required_sections, [str(doc), "Goal, Risks"], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "missing-section"
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    v = run_gate(required_sections, [str(empty), "Goal"], capsys)
    assert v["findings"][0]["category"] == "empty-doc"


# ------------------------------------------------------- version_sync


def _project(tmp_path, init_version="0.4.0", py_version="0.4.0"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo-pkg"\nversion = "{py_version}"\n', encoding="utf-8"
    )
    pkg = tmp_path / "src" / "demo_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{init_version}"\n', encoding="utf-8")


def test_version_sync_agreement(tmp_path, capsys, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    v = run_gate(version_sync, [], capsys)
    assert v["verdict"] == "pass" and "0.4.0" in v["reason"]


def test_version_sync_drift_blocks(tmp_path, capsys, monkeypatch):
    _project(tmp_path, init_version="0.3.9")
    monkeypatch.chdir(tmp_path)
    v = run_gate(version_sync, [], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "version-drift"


def test_version_sync_tag_and_changelog(tmp_path, capsys, monkeypatch):
    _project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# log\n## 0.4.0\n- stuff\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    v = run_gate(version_sync, ["--changelog", "CHANGELOG.md", "--tag", "v0.4.0"], capsys)
    assert v["verdict"] == "pass"
    v = run_gate(version_sync, ["--tag", "v0.5.0"], capsys)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "tag"
    v = run_gate(version_sync, ["--tag", "vv0.4.0"], capsys)
    assert v["verdict"] == "block", "only one leading v is a tag convention"
    # A "## 0.4.00" or "## 0.4.0.1" heading must not satisfy 0.4.0 by substring.
    (tmp_path / "CHANGELOG.md").write_text("# log\n## 0.4.0.1\n- stuff\n", encoding="utf-8")
    v = run_gate(version_sync, ["--changelog", "CHANGELOG.md"], capsys)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "changelog"


# ------------------------------------------------------- citation_check


def test_citation_check_sources_mode(tmp_path, capsys):
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"sources": [{"id": "S1"}, {"id": "S2"}]}), encoding="utf-8")
    doc = tmp_path / "report.md"
    doc.write_text("# R\n## One\nclaim [S1]\n## References\nlist\n", encoding="utf-8")
    v = run_gate(citation_check, [str(doc), "--sources", str(manifest), "--per-section"], capsys)
    assert v["verdict"] == "pass"
    doc.write_text("# R\n## One\nclaim [S9]\n## Two\nno citation\n", encoding="utf-8")
    v = run_gate(citation_check, [str(doc), "--sources", str(manifest), "--per-section"], capsys)
    cats = {f["category"] for f in v["findings"]}
    assert cats == {"dangling-citation", "uncited-section"}
    doc.write_text("# R\nnothing cited at all\n", encoding="utf-8")
    v = run_gate(citation_check, [str(doc), "--sources", str(manifest)], capsys)
    assert v["findings"][0]["category"] == "no-citations"


def test_citation_check_paths_mode(tmp_path, capsys):
    root = tmp_path / "run"
    (root / "phases").mkdir(parents=True)
    (root / "phases" / "state.txt").write_text("x", encoding="utf-8")
    doc = tmp_path / "pm.md"
    doc.write_text("failed [artifact: phases/state.txt]\n", encoding="utf-8")
    v = run_gate(citation_check, [str(doc), "--paths", str(root)], capsys)
    assert v["verdict"] == "pass"
    doc.write_text("failed [artifact: phases/nope.txt]\n", encoding="utf-8")
    v = run_gate(citation_check, [str(doc), "--paths", str(root)], capsys)
    assert v["verdict"] == "block"


def test_citation_check_doc_node_flattens_a_map_result(tmp_path, capsys, monkeypatch):
    """A draft map's aggregated result is a JSON array of section texts; the
    gate must see real headings and citations through it."""
    run_dir = tmp_path / "run"
    (run_dir / "phases" / "draft").mkdir(parents=True)
    (run_dir / "phases" / "sources").mkdir()
    (run_dir / "phases" / "gate").mkdir()
    sections = ["## One\nclaim [S1]", "## Two\nno citation here"]
    (run_dir / "phases" / "draft" / "result.json").write_text(
        json.dumps(sections), encoding="utf-8"
    )
    (run_dir / "phases" / "sources" / "result.json").write_text(
        json.dumps({"sources": [{"id": "S1"}]}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run_dir / "phases" / "gate"))
    v = run_gate(citation_check,
                 ["--doc-node", "draft", "--sources-node", "sources", "--per-section"],
                 capsys)
    assert v["verdict"] == "block"
    assert {f["category"] for f in v["findings"]} == {"uncited-section"}
    sections[1] = "## Two\nnow cited [S1]"
    (run_dir / "phases" / "draft" / "result.json").write_text(
        json.dumps(sections), encoding="utf-8"
    )
    v = run_gate(citation_check,
                 ["--doc-node", "draft", "--sources-node", "sources", "--per-section"],
                 capsys)
    assert v["verdict"] == "pass"


# ------------------------------------------------------- numbers_check


def test_numbers_check(tmp_path, capsys):
    collector = tmp_path / "collect.json"
    collector.write_text(json.dumps({"count": 42, "wall": "13 m", "pct": 87.5}), encoding="utf-8")
    doc = tmp_path / "digest.md"
    doc.write_text(
        "On 2026-08-04 (v0.3.1) the run made 42 spawns in 13 m (87.5%), in 3 steps.\n",
        encoding="utf-8",
    )
    v = run_gate(numbers_check, [str(doc), "--from", str(collector)], capsys)
    assert v["verdict"] == "pass", v
    doc.write_text("The run made 99 spawns.\n", encoding="utf-8")
    v = run_gate(numbers_check, [str(doc), "--from", str(collector)], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "unsourced-number"
    assert "99" in v["findings"][0]["claim"]


def test_numbers_check_matches_display_formats_by_value(tmp_path, capsys):
    """"87.50%" in prose is the same sourced number as collector 87.5."""
    collector = tmp_path / "collect.json"
    collector.write_text(json.dumps({"pct": 87.5, "ratio": 0.3}), encoding="utf-8")
    doc = tmp_path / "digest.md"
    doc.write_text("coverage held at 87.50% with a ratio of 0.30\n", encoding="utf-8")
    v = run_gate(numbers_check, [str(doc), "--from", str(collector)], capsys)
    assert v["verdict"] == "pass", v


def test_numbers_check_allow_regex(tmp_path, capsys):
    collector = tmp_path / "collect.json"
    collector.write_text("{}", encoding="utf-8")
    doc = tmp_path / "digest.md"
    doc.write_text("ticket #4711 is referenced\n", encoding="utf-8")
    v = run_gate(numbers_check, [str(doc), "--from", str(collector)], capsys)
    assert v["verdict"] == "block"
    v = run_gate(
        numbers_check, [str(doc), "--from", str(collector), "--allow", r"#\d+"], capsys
    )
    assert v["verdict"] == "pass"


def test_numbers_check_from_node(tmp_path, capsys, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "phases" / "collect").mkdir(parents=True)
    (run_dir / "phases" / "gate").mkdir()
    (run_dir / "phases" / "collect" / "result.txt").write_text(
        json.dumps({"count": 42}), encoding="utf-8"
    )
    doc = tmp_path / "digest.md"
    doc.write_text("42 things happened\n", encoding="utf-8")
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run_dir / "phases" / "gate"))
    v = run_gate(numbers_check, [str(doc), "--from-node", "collect"], capsys)
    assert v["verdict"] == "pass"
    v = run_gate(numbers_check, [str(doc), "--from-node", "absent"], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "gate-error"


# ------------------------------------------------------- coverage_delta


def test_coverage_delta(tmp_path, capsys):
    baseline = tmp_path / "base.json"
    baseline.write_text("80.0", encoding="utf-8")
    current = tmp_path / "coverage.json"
    current.write_text(json.dumps({"totals": {"percent_covered": 85.2}}), encoding="utf-8")
    v = run_gate(coverage_delta, ["--baseline", str(baseline), "--current", str(current)], capsys)
    assert v["verdict"] == "pass"
    current.write_text(json.dumps({"totals": {"percent_covered": 75.0}}), encoding="utf-8")
    v = run_gate(coverage_delta, ["--baseline", str(baseline), "--current", str(current)], capsys)
    assert v["verdict"] == "block"
    v = run_gate(
        coverage_delta,
        ["--baseline", str(baseline), "--current", str(current), "--tolerance", "10"],
        capsys,
    )
    assert v["verdict"] == "pass"


# ------------------------------------------------------- fingerprint_check


def test_fingerprint_check(tmp_path, capsys, monkeypatch):
    import hashlib
    from lockstep.gates import fingerprint_check

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("original", encoding="utf-8")
    fp = hashlib.sha256(b"original").hexdigest()[:16]
    orders = tmp_path / "orders.json"
    orders.write_text(json.dumps([{"file": "a.py", "fingerprint": fp, "change": "x"}]),
                      encoding="utf-8")
    v = run_gate(fingerprint_check, [str(orders)], capsys)
    assert v["verdict"] == "pass"
    (tmp_path / "a.py").write_text("drifted", encoding="utf-8")
    v = run_gate(fingerprint_check, [str(orders)], capsys)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "stale"
    orders.write_text("[]", encoding="utf-8")
    v = run_gate(fingerprint_check, [str(orders)], capsys)
    assert v["verdict"] == "block", "an empty staleness check must not pass vacuously"


# ------------------------------------------------------- pytest_verdict


def test_pytest_verdict_green_and_red(tmp_path, capsys, monkeypatch):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    v = run_gate(pytest_verdict, ["--no-ruff"], capsys)
    assert v["verdict"] == "pass"
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    v = run_gate(pytest_verdict, ["--no-ruff"], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "tests"


# ------------------------------------------------------- pi_guard_smoke


def _probe_dirs(tmp_path):
    """(gate phase dir, probe phase dir) under one synthetic run."""
    phases = tmp_path / "runs" / "r" / "phases"
    gate = phases / "guard-gate"
    probe = phases / "scope-probe"
    gate.mkdir(parents=True)
    probe.mkdir(parents=True)
    return gate, probe


def test_pi_guard_smoke_passes_when_the_guard_recorded_a_block(tmp_path, capsys, monkeypatch):
    gate, probe = _probe_dirs(tmp_path)
    (probe / "verdicts.jsonl").write_text(
        json.dumps({"ts": "t", "node_id": "scope-probe", "tool": "write",
                    "reason": "outside scope", "input_digest": "d"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(gate))
    monkeypatch.setenv("LOCKSTEP_REPO_ROOT", str(tmp_path))
    v = run_gate(pi_guard_smoke, [], capsys)
    assert v["verdict"] == "pass" and "1 verdict record" in v["reason"]


def test_pi_guard_smoke_blocks_when_no_verdict_was_recorded(tmp_path, capsys, monkeypatch):
    """The extension did not load, or the hook API drifted."""
    gate, _ = _probe_dirs(tmp_path)
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(gate))
    monkeypatch.setenv("LOCKSTEP_REPO_ROOT", str(tmp_path))
    v = run_gate(pi_guard_smoke, [], capsys)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "guard-missing"


def test_pi_guard_smoke_blocks_on_an_escape_and_removes_it(tmp_path, capsys, monkeypatch):
    """A guard that loaded but let the path through. The gate must also clean
    up: leaving the escape file behind would make the NEXT run fail for a
    reason that no longer exists."""
    gate, probe = _probe_dirs(tmp_path)
    (probe / "verdicts.jsonl").write_text(
        json.dumps({"ts": "t", "tool": "write", "reason": "r"}) + "\n", encoding="utf-8"
    )
    escape = tmp_path / "pi-guard-escape.tmp"
    escape.write_text("escaped\n", encoding="utf-8")
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(gate))
    monkeypatch.setenv("LOCKSTEP_REPO_ROOT", str(tmp_path))
    v = run_gate(pi_guard_smoke, [], capsys)
    assert v["verdict"] == "block"
    assert [f["category"] for f in v["findings"]] == ["guard-bypassed"]
    assert not escape.exists(), "the gate must remove the file it found"


def test_pi_guard_smoke_reports_a_gate_error_without_the_spawn_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("LOCKSTEP_PHASE_DIR", raising=False)
    v = run_gate(pi_guard_smoke, [], capsys)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "gate-error"


# ------------------------------------------------- encoding of the verdict


def test_a_gate_emits_utf8_under_a_redirected_pipe(tmp_path):
    """A gate's stdout is a redirected pipe, and on Windows a redirected Python
    stdout defaults to cp1252. `emit` uses `ensure_ascii=False`, so ONE arrow,
    curly quote or non-Latin filename in a model-written finding used to raise
    UnicodeEncodeError: exit 1, empty stdout.log, and a node the driver could
    only report as failed with the cause buried in stderr.

    `block_on_severity` re-emits reviewer prose verbatim, so this was reachable
    on any run whose reviewer used a character cp1252 lacks.

    Spawned as a real child with stdout redirected to a FILE, because that is
    the only arrangement that reproduces it — capsys never had the problem.
    """
    import subprocess
    import sys

    prog = (
        "import sys; sys.path.insert(0, %r)\n"
        "from lockstep.gates._common import emit, finding\n"
        "emit([finding('major', 'c', 'f\u00e9e.py', 'a \u2192 b', '\u2265 3 cases', 'fix')],"
        " 'clean', 'one finding')\n"
    ) % str(ROOT_SRC)
    out = tmp_path / "stdout.log"
    with open(out, "wb") as fh:
        proc = subprocess.run(
            [sys.executable, "-c", prog], stdout=fh, stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": ""},   # the hostile case
        )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
    data = json.loads(out.read_text(encoding="utf-8"))
    Verdict.model_validate(data)
    assert data["findings"][0]["claim"] == "a \u2192 b"
    assert data["findings"][0]["evidence"] == "\u2265 3 cases"


def test_the_spawn_env_forces_utf8_on_children():
    """Belt to the gate library's braces: the driver sets PYTHONIOENCODING for
    every spawned node, so a user's own `python -c` shell node gets the same
    protection without knowing about it."""
    from lockstep.executors.shell import node_env

    class _W:
        meta = {"repo_root": ".", "node_id": "n", "role": "work", "cwd": ".", "writes": []}

    assert node_env(_W(), Path(".")).get("PYTHONIOENCODING") == "utf-8"
