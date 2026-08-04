"""Regressions for the defects an adversarial review found in the cockpit UX work.

They are grouped in one file because they share one property: each was a
specific, confident, WRONG statement made to a non-programmer on the surface the
design tells them to trust over anything said in the chat. That is a worse
failure than a crash — a crash is visible.

  B1  `impact()` could not see untracked files, so the shipped starter flow's
      approval pane said "nothing changed" about a brand new deliverable.
  B2  Approval tiers declared in the labels sidecar had no reader anywhere.
  B3  (PowerShell, covered by contrib/cockpit.ps1's Update-Spend) the spend
      block could go backwards or blank out.

Plus the crash/hang class: a view that dies mid-watch breaks "blank never means
dead" exactly as thoroughly as a frozen one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRIB = ROOT / "contrib"
sys.path.insert(0, str(CONTRIB))

import pytest  # noqa: E402

import mission_view as mv  # noqa: E402
import plan_card  # noqa: E402
import question_card  # noqa: E402
import render_evidence as re_mod  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, *, tracked: tuple[str, ...] = ("seed.txt",),
          then: dict[str, str | None] | None = None) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    for name in tracked:
        (repo / name).write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for name, content in (then or {}).items():
        if content is None:
            (repo / name).unlink()
        else:
            (repo / name).write_text(content, encoding="utf-8")
    return repo


def _doc(tmp_path: Path) -> Path:
    p = tmp_path / "d.md"
    if not p.exists():
        p.write_text("# heading\nbody\n", encoding="utf-8")
    return p


# ============================================================ B1: blast radius

def test_impact_sees_an_untracked_new_file(tmp_path):
    """THE canonical cockpit case, and it read as "nothing changed".

    The shipped starter flow has an agent write a brand new deliverable. Every
    flavour of `git diff` is blind to untracked files, so the evidence pane told
    a human nothing had changed about the very file they were approving.
    """
    repo = _repo(tmp_path, then={"DRAFT.md": "the new deliverable\n"})
    line = re_mod.impact(repo)[0]
    assert "1 file" in line
    assert "1 new" in line
    assert "nothing changed" not in line


def test_diffstat_lists_untracked_files_too(tmp_path):
    repo = _repo(tmp_path, then={"DRAFT.md": "x\n"})
    body = "\n".join(re_mod.diffstat(repo))
    assert "DRAFT.md" in body
    assert body.strip() != "(no changes against HEAD)"


def test_impact_total_always_equals_the_number_of_changes(tmp_path):
    """The invariant. An undercount is worse than no count: it is a specific
    wrong number on the surface a human weighs a decision on."""
    repo = _repo(tmp_path, tracked=("a.txt", "b.txt", "c.txt"),
                 then={"a.txt": "edited\n", "c.txt": None,
                       "new1.md": "x\n", "new2.md": "y\n"})
    truth = [ln for ln in subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo,
        capture_output=True, text=True).stdout.splitlines() if ln.strip()]
    assert len(truth) == 4
    assert f"{len(truth)} files" in re_mod.impact(repo)[0]


def test_impact_never_silently_drops_a_status_code(tmp_path, monkeypatch):
    # T (typechange), C (copy) and U (unmerged) are real codes the first cut
    # counted as zero while still printing a confident total.
    monkeypatch.setattr(re_mod, "_git", lambda cwd, *a: subprocess.CompletedProcess(
        a, 0, stdout="T  typed.txt\nC  copied.txt\nUU conflict.txt\nXX weird.txt\n", stderr=""))
    lines = re_mod.impact(tmp_path)
    assert "4 files" in lines[0]
    assert "CONFLICTED" in lines[0]
    assert any("conflicted state" in ln for ln in lines)
    assert any("could not name" in ln for ln in lines)   # XX -> other, never dropped


def test_impact_still_shouts_about_deletion(tmp_path):
    repo = _repo(tmp_path, tracked=("gone.txt",), then={"gone.txt": None})
    lines = re_mod.impact(repo)
    assert "1 DELETED" in lines[0]
    assert any("something is deleted" in ln for ln in lines)


def test_impact_is_honest_about_an_empty_tree(tmp_path):
    assert "nothing changed" in re_mod.impact(_repo(tmp_path))[0]


def test_impact_counts_files_inside_a_new_directory(tmp_path):
    """The count and the list on the same pane must agree.

    `git status --porcelain` collapses an untracked DIRECTORY to one entry while
    `ls-files --others` lists its files, so the evidence read "2 new" directly
    above a list of five new files. Two numbers for the same thing, one wrong.
    """
    repo = _repo(tmp_path)
    (repo / "out").mkdir()
    for n in ("a.md", "b.md", "c.md"):
        (repo / "out" / n).write_text("x\n", encoding="utf-8")
    line = re_mod.impact(repo)[0]
    assert "3 files" in line
    assert "3 new" in line
    assert len(re_mod.untracked(repo)) == 3


# ============================================================== B2: tiers wired

def _run_with_tiers(tmp_path: Path, tiers: dict) -> Path:
    run = tmp_path / "run"
    (run / "phases" / "render").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({"flow_name": "f", "nodes": {}}),
                                    encoding="utf-8")
    (run / "flow.labels.json").write_text(json.dumps({"tiers": tiers}), encoding="utf-8")
    return run


def test_a_tier_declared_in_the_sidecar_actually_fires(tmp_path, monkeypatch):
    """The proposal said tiers live in the labels sidecar. Nothing read them.

    A flow author following the documented shape got a silent no-op on exactly
    the approvals the mechanism exists to make loud.
    """
    run = _run_with_tiers(tmp_path, {"approve": "irreversible"})
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    assert re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "IRREVERSIBLE" in text
    assert "NOT CHARACTERISED" in text      # no --impact on an irreversible tier


def test_an_explicit_tier_flag_still_wins(tmp_path, monkeypatch):
    run = _run_with_tiers(tmp_path, {"approve": "irreversible"})
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out), "--tier", "routine"])
    assert "ROUTINE" in out.read_text(encoding="utf-8")


def test_ambiguous_tiers_refuse_to_guess(tmp_path, monkeypatch):
    # A banner attached to the wrong decision is worse than no banner.
    run = _run_with_tiers(tmp_path, {"one": "irreversible", "two": "routine"})
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "IRREVERSIBLE" not in text
    assert "ROUTINE" not in text
    assert "pass --approval" in text        # the flow author is told, not ignored


def test_approval_selects_among_several_tiers(tmp_path, monkeypatch):
    run = _run_with_tiers(tmp_path, {"one": "irreversible", "two": "routine"})
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out), "--approval", "two"])
    assert "ROUTINE" in out.read_text(encoding="utf-8")


def test_a_tier_for_an_unnamed_approval_says_so(tmp_path, monkeypatch):
    run = _run_with_tiers(tmp_path, {"one": "irreversible"})
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out), "--approval", "other"])
    assert "none for 'other'" in out.read_text(encoding="utf-8")


def test_no_sidecar_is_simply_standard(tmp_path, monkeypatch):
    run = tmp_path / "run"
    (run / "phases" / "render").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({"flow_name": "f", "nodes": {}}),
                                    encoding="utf-8")
    monkeypatch.setenv("LOCKSTEP_PHASE_DIR", str(run / "phases" / "render"))
    out = tmp_path / "ev.txt"
    re_mod.main(["--full", str(_doc(tmp_path)), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "IRREVERSIBLE" not in text and "ROUTINE" not in text
    assert "Decide from this pane" in text


def test_load_tiers_is_a_real_function_not_a_magic_key(tmp_path):
    # The first cut smuggled tiers back as labels["__tiers__"], which meant the
    # one consumer that mattered never read them.
    run = _run_with_tiers(tmp_path, {"approve": "routine"})
    assert mv.load_tiers(run) == {"approve": "routine"}
    assert "__tiers__" not in mv.load_labels(run)


def test_the_starter_flow_keeps_the_promise_its_guide_makes():
    """COCKPIT-FOR-DOMAIN-EXPERTS.md tells the DE the pane shows "scale of the
    change" and "if this turns out wrong". A promise to a non-programmer that
    depends on an opt-in nobody took is a promise broken."""
    flow = json.loads((ROOT / "flows" / "starter" / "evidence-approval.tg.json")
                      .read_text(encoding="utf-8"))
    cmd = [n for n in flow["nodes"] if n["id"] == "render-evidence"][0]["spec"]["cmd"]
    assert "--impact" in cmd
    assert "--reversible" in cmd


# ==================================================== crash / hang: view robustness

def _fail_stat_after(monkeypatch, name_suffix: str, survives: int):
    """Let `stat()` succeed `survives` times for a path, then raise.

    Necessary because `Path.is_dir()` and `Path.is_file()` are THEMSELVES
    implemented on `stat()` and swallow OSError. A stub that raises on the first
    call is caught inside `is_dir()`, the entry is skipped, and the code under
    test never reaches its own `stat()` — so the test passes whether the guard
    is there or not. That tautology is what this helper exists to avoid: the
    failure has to land on the LATER call, which is the one a vanishing file
    actually breaks.
    """
    seen: dict[str, int] = {}
    real_stat = Path.stat

    def staged_stat(self, *a, **kw):
        if str(self).endswith(name_suffix):
            seen[str(self)] = seen.get(str(self), 0) + 1
            if seen[str(self)] > survives:
                raise FileNotFoundError(self)
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", staged_stat)


def test_the_view_never_raises_when_a_run_dir_vanishes(tmp_path, monkeypatch):
    """mission_view promises every failure returns None rather than raising.

    newest_run stat()ed OUTSIDE its guard, so a run dir removed between the
    listing and the stat propagated FileNotFoundError — and mission_tui's loop
    has no other net, so the domain expert's view died mid-watch.
    """
    (tmp_path / "gone").mkdir()
    (tmp_path / "gone" / "state.json").write_text("{}", encoding="utf-8")
    # is_dir() consumes the first stat; the guard has to cover the second.
    _fail_stat_after(monkeypatch, "gone", survives=1)
    assert mv.newest_run(tmp_path) is None       # must not raise


def test_newest_run_still_picks_the_newest_when_one_vanishes(tmp_path):
    import os
    import time
    for i, name in enumerate(("old", "new")):
        d = tmp_path / name
        d.mkdir()
        (d / "state.json").write_text("{}", encoding="utf-8")
        os.utime(d, (time.time() + i * 100, time.time() + i * 100))
    assert mv.newest_run(tmp_path).name == "new"


def test_node_detail_survives_a_rotating_phase_dir(tmp_path, monkeypatch):
    # The engine rotates per-attempt files; a name listed a moment ago can be
    # gone by the time it is measured.
    run = tmp_path / "run"
    (run / "phases" / "w").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps(
        {"flow_name": "f", "nodes": {"w": {"role": "work", "status": "done", "attempts": 1}}}),
        encoding="utf-8")
    (run / "phases" / "w" / "result-attempt1.json").write_text("{}", encoding="utf-8")
    (run / "phases" / "w" / "result.json").write_text("{}", encoding="utf-8")
    # is_file() consumes the first stat; the size lookup is the second.
    _fail_stat_after(monkeypatch, "result-attempt1.json", survives=1)
    body = "\n".join(mv.node_detail(run, "w"))   # must not raise
    assert "attempt1" not in body
    assert "result.json" in body                # the survivors are still listed


# =================================================== consent card / question card

FLOW = {
    "name": "cardflow",
    "format_version": "1.0",
    "nodes": [{"id": "a", "kind": "shell", "output": "text",
               "spec": {"cmd": ["echo", "hi"]}, "final": True}],
}


def test_plan_card_accepts_a_flow_the_engine_accepts(tmp_path):
    """SPEC §4 merges `x-lockstep` and drops other `x-*`. Parsing the flow raw
    made the consent card reject flows the engine runs happily — the artifact
    behind "shall I start?" failing on a valid flow."""
    flow = dict(FLOW)
    flow["x-lockstep"] = {"budget": {"max_agent_spawns": 7}}
    flow["x-some-other-tool"] = {"ignored": True}
    path = tmp_path / "f.tg.json"
    path.write_text(json.dumps(flow), encoding="utf-8")
    assert plan_card.main([str(path), "--runs-dir", str(tmp_path / "runs"),
                           "--out", str(tmp_path / "card.txt")]) == 0
    assert "ceiling: 7 agent tasks" in (tmp_path / "card.txt").read_text(encoding="utf-8")


def test_plan_card_reports_an_unreadable_flow_without_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.tg.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert plan_card.main([str(bad), "--runs-dir", str(tmp_path / "runs")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_question_card_will_not_delete_a_file_it_did_not_write(tmp_path):
    # Pointed at approval-evidence.txt, the stale-card cleanup would have eaten
    # the artifact a human decides from.
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json").write_text(json.dumps({"flow_name": "f", "nodes": {}}),
                                    encoding="utf-8")
    victim = run / "approval-evidence.txt"
    victim.write_text("the thing the human decides from", encoding="utf-8")
    assert question_card.main([str(run), "--out", str(victim)]) == 1
    assert victim.is_file()


def test_question_card_still_clears_its_own_stale_card(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json").write_text(json.dumps({"flow_name": "f", "nodes": {}}),
                                    encoding="utf-8")
    card = run / "question-card.txt"
    card.write_text("an answered question", encoding="utf-8")
    assert question_card.main([str(run)]) == 1
    assert not card.exists()


# ================================================== B3: the monotonic spend guard
#
# PowerShell, and therefore the first behaviour in contrib/cockpit.ps1 ever put
# under test. It is worth the awkwardness: the guard exists because the spend
# figure is the DE's only quantitative trust anchor, and cost_report.py's own
# --watch implementation of it explicitly does not apply to the pane they read.

import shutil  # noqa: E402

import pytest  # noqa: E402

pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not on PATH")

_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$src = Get-Content -Raw -LiteralPath '{script}'
# Pull just the two functions under test out of the shipped script, so this
# tests the REAL code rather than a copy that can drift from it.
foreach ($name in @('Get-SpawnCount', 'Update-Spend')) {{
  $m = [regex]::Match($src, "(?ms)^function $name \{{.*?^\}}")
  if (-not $m.Success) {{ throw "cockpit.ps1 no longer defines $name" }}
  Invoke-Expression $m.Value
}}
$good = @('agent tasks used 9 of 25', '12m of node time')
$r = @{{}}
$r['placeholder'] = (@(Update-Spend -Current $good -Candidate @('(spend unavailable)')))[0]
$r['smaller']     = (@(Update-Spend -Current $good -Candidate @('agent tasks used 3 of 25')))[0]
$r['larger']      = (@(Update-Spend -Current $good -Candidate @('agent tasks used 11 of 25')))[0]
$r['first']       = (@(Update-Spend -Current @('(spend unavailable)') -Candidate $good))[0]
$r | ConvertTo-Json -Compress
"""


@pwsh
def test_spend_never_goes_backwards_or_blank():
    """`Get-SpendLine` returns "(spend unavailable)" on ANY failure, and this
    machine's AV causes transient read failures as a documented standing quirk.

    Without the guard, one unlucky poll replaces a good spend block with a
    placeholder or a smaller count — and the DE was told this number "cannot
    flatter or round off". A figure that visibly shrinks and comes back destroys
    that in a way no correctness elsewhere repairs.
    """
    script = (CONTRIB / "cockpit.ps1").as_posix()
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", _HARNESS.format(script=script)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["placeholder"] == "agent tasks used 9 of 25"   # cannot blank out
    assert got["smaller"] == "agent tasks used 9 of 25"       # cannot go backwards
    assert got["larger"] == "agent tasks used 11 of 25"       # real progress lands
    assert got["first"] == "agent tasks used 9 of 25"         # placeholder yields


@pwsh
def test_show_mission_actually_uses_the_guard():
    # A guard nothing calls is the defect it was written to fix.
    body = (CONTRIB / "cockpit.ps1").read_text(encoding="utf-8")
    show = body[body.index("function Show-Mission"):]
    assert "Update-Spend" in show[:show.index("\nfunction ")]


# ================================================ review minors (2026-08-04 pass)

def test_a_finished_runs_clock_stops(tmp_path):
    """A duration beside "done" reads as what the work took.

    It used to count against the wall clock forever: the shipped demo showed
    "step 8 of 8 - done - 35 h 56 m" days after it finished.
    """
    from datetime import datetime, timedelta, timezone
    began = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    ended = began + timedelta(minutes=20)
    much_later = began + timedelta(days=3)
    state = {
        "started_at": began.isoformat().replace("+00:00", "Z"),
        "nodes": {"a": {"role": "work", "status": "done", "attempts": 1,
                        "ended_at": ended.isoformat().replace("+00:00", "Z")}},
    }
    assert "20 m" in mv.headline(state, None, now=much_later)


def test_a_live_runs_clock_keeps_running(tmp_path):
    from datetime import datetime, timedelta, timezone
    began = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    state = {
        "started_at": began.isoformat().replace("+00:00", "Z"),
        "nodes": {"a": {"role": "work", "status": "running", "attempts": 1}},
    }
    assert "30 m" in mv.headline(state, None, now=began + timedelta(minutes=30))


def test_stale_output_is_not_called_liveness(tmp_path):
    """"still producing output - last write 114271s ago" (observed) restates the
    thinking/stuck ambiguity this fallback exists to remove, as a contradiction
    on one line."""
    import os
    import time
    phase = tmp_path / "phase"
    phase.mkdir()
    log = phase / "stdout.log"
    log.write_text("x" * 4096, encoding="utf-8")
    stale = time.time() - 3600
    os.utime(log, (stale, stale))
    line = mv.stdout_liveness(phase)
    assert "still producing output" not in line
    # 59 or 60: the seconds elapsed between utime() and the read truncate down.
    assert re.search(r"no new output for (59|60) m", line), line
    assert "4.0 KB" in line


def test_fresh_output_still_reads_as_liveness(tmp_path):
    phase = tmp_path / "phase"
    phase.mkdir()
    (phase / "stdout.log").write_text("x" * 1024, encoding="utf-8")
    assert "still producing output" in mv.stdout_liveness(phase)


def test_question_card_warns_when_a_named_gate_is_not_blocked(tmp_path, capsys):
    # --gate used to bypass the blocked check silently, so a card could be
    # written for a question the human already answered.
    run = tmp_path / "run"
    (run / "phases" / "g").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps(
        {"flow_name": "f", "nodes": {"g": {"role": "gate", "status": "done"}}}),
        encoding="utf-8")
    (run / "phases" / "g" / "result.json").write_text(
        json.dumps({"verdict": "block", "reason": "r",
                    "findings": [{"category": "question", "claim": "which one?"}]}),
        encoding="utf-8")
    assert question_card.main([str(run), "--gate", "g"]) == 0
    assert "not a blocked gate" in capsys.readouterr().err


def test_the_tui_keeps_every_keypress_in_a_tick():
    """Returning only the last key dropped input: `3` then `e` within one poll
    interval lost the `3`. A view that eats keystrokes teaches the person using
    it that it is unreliable."""
    import mission_tui

    class FakeKeys:
        def __init__(self, seq):
            self.seq = list(seq)

        def get(self):
            return self.seq.pop(0) if self.seq else None

    assert mission_tui._drain(FakeKeys(["3", "E"])) == ["3", "e"]
    assert mission_tui._drain(FakeKeys([])) == []


@pytest.mark.skipif(os.name != "nt", reason="the msvcrt key path is Windows-only")
@pytest.mark.parametrize("keystroke,expect", [
    (["\xe0", "Q"], []),        # PgDn — used to lowercase to 'q' and QUIT
    (["\xe0", "R"], []),        # Insert — used to force a repaint
    (["\xe0", "H"], []),        # up arrow
    (["\x00", ";"], []),        # F1 (the other prefix)
    (["e"], ["e"]),             # a real command still gets through
    (["3"], ["3"]),
])
def test_windows_extended_keys_do_not_alias_onto_commands(keystroke, expect, monkeypatch):
    """PgDn is ('\\xe0', 'Q') on Windows — two reads, and the second lowercased
    to `q`, which CLOSED the view. It is the most natural keystroke for someone
    facing a wall of text, so the monitoring surface vanished exactly when they
    were trying to read it.

    Driven through the real `Keys.get()` with a stand-in msvcrt, so it fails if
    the second read stops being consumed — not merely if a constant is renamed.
    """
    import mission_tui

    class FakeMsvcrt:
        def __init__(self, seq):
            self.seq = list(seq)

        def kbhit(self):
            return bool(self.seq)

        def getwch(self):
            return self.seq.pop(0)

    fake = FakeMsvcrt(keystroke)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    assert mission_tui._drain(mission_tui.Keys()) == expect
    assert fake.seq == [], "the extended-key code byte was left in the buffer"


_WIDTH_HARNESS = """
$src = Get-Content -Raw -LiteralPath '{script}'
foreach ($n in @('Get-PaneWidth', 'Format-InPlace')) {{
  $m = [regex]::Match($src, "(?ms)^function $n \\{{.*?^\\}}")
  if (-not $m.Success) {{ throw "cockpit.ps1 no longer defines $n" }}
  Invoke-Expression $m.Value
}}
$long = '  working - 14 m elapsed - still producing output - 0.5 KB, last write 114271s ago'
@{{
  width    = (Get-PaneWidth)
  isLonger = ($long.Length -gt 78)
  trimmed  = (Format-InPlace -Text $long -Width 40).Length
  padded   = (Format-InPlace -Text 'short' -Width 40).Length
  content  = (Format-InPlace -Text $long -Width 40)
}} | ConvertTo-Json -Compress
"""


@pwsh
def test_the_pane_status_line_is_sized_to_the_pane():
    """A carriage return goes to the start of the last WRAPPED row, not the
    logical line. So any in-place status longer than the pane wraps, the next
    tick overwrites only its tail, and the pane scrolls one junk line per second
    — the wall-of-heartbeats failure T1.6 was meant to end.

    ACTIVITY is spawned at a 45% split, so "narrower than 80" is the normal
    case, and the realistic stdout-liveness beat is 82+ characters and grows
    with the KB and the seconds in it. Padding alone could never fix that.
    """
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
         _WIDTH_HARNESS.format(script=(CONTRIB / "cockpit.ps1").as_posix())],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["width"] >= 20
    # The realistic long beat really is longer than the old fixed budget, which
    # is why truncation and not merely padding is the fix.
    assert got["isLonger"] is True
    assert got["trimmed"] == 40      # truncated, so it cannot wrap
    assert got["padded"] == 40       # padded, so it fully erases what was there
    assert got["content"].startswith("  working - 14 m elapsed")


def test_no_raw_carriage_return_writes_outside_the_helpers():
    """One place knows the pane width. Every other in-place write goes through
    it, or the sizing fix is only true where somebody remembered."""
    body = (CONTRIB / "cockpit.ps1").read_text(encoding="utf-8")
    for helper in ("Write-InPlace", "Clear-InPlace"):
        m = re.search(r"(?ms)^function " + helper + r" \{.*?^\}", body)
        assert m, f"cockpit.ps1 no longer defines {helper}"
        body = body.replace(m.group(0), "")
    offenders = [ln.strip() for ln in body.splitlines()
                 if "`r" in ln and "-NoNewline" in ln]
    assert offenders == [], offenders


