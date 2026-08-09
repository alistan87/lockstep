"""The probe library: observe, report, never fail the node.

Probes exist so `spec.readonly` can remove EVERY write vector — including the
shell — without crippling the node downstream. That makes their contract
narrow and worth pinning: always exit 0, always UTF-8, never write.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lockstep.probes import command_output, worktree_diff

ROOT = Path(__file__).resolve().parents[1]


def run(module, argv, capsysbinary) -> str:
    assert module.main(argv) == 0, "a probe always exits 0"
    return capsysbinary.readouterr().out.decode("utf-8")


# ------------------------------------------------------- command_output


def test_a_failing_command_is_an_observation_not_a_failure(capsysbinary):
    """The whole point: the diagnostician must SEE the failure. A non-zero exit
    here would fail the node and the flow would never reach the diagnosis."""
    out = run(command_output,
              [f'{sys.executable} -c "import sys; print(\'boom\'); sys.exit(3)"',
               "--label", "repro"], capsysbinary)
    assert "exit code: 3" in out and "FAILED" in out
    assert "boom" in out


def test_a_passing_command_says_so(capsysbinary):
    out = run(command_output, [f'{sys.executable} -c "print(42)"'], capsysbinary)
    assert "exit code: 0" in out and "passed" in out and "42" in out


def test_a_missing_binary_is_reported_not_raised(capsysbinary):
    out = run(command_output, ["definitely-not-a-real-binary-xyz --flag"], capsysbinary)
    assert "could not run it" in out


def test_an_empty_command_is_reported(capsysbinary):
    assert "nothing to run" in run(command_output, ["   "], capsysbinary)


def test_output_is_capped_from_the_middle():
    """Both ends survive: a traceback's cause is at the top and its assertion at
    the bottom, so a head-only cut loses the half that names the failure."""
    text = "\n".join(f"line{i}" for i in range(500))
    got = command_output._cap(text, 20)
    assert "line0" in got and "line499" in got
    assert "omitted" in got


def test_quoted_arguments_survive_the_split():
    """The command arrives as ONE string from `--arg`, and a shell would have
    stripped the quotes around a `-k` expression."""
    assert command_output.split_command('pytest -k "a or b"')[-1] == "a or b"


@pytest.mark.skipif(os.name != "nt", reason="the Windows path-separator case")
def test_windows_backslashes_are_not_escapes():
    assert "C:\\repo\\test_x.py" in command_output.split_command(
        r"python -m pytest C:\repo\test_x.py"
    )


# ------------------------------------------------------- worktree_diff


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.py").write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_it_reports_a_clean_tree_without_pretending_otherwise(repo, monkeypatch, capsysbinary):
    monkeypatch.chdir(repo)
    out = run(worktree_diff, [], capsysbinary)
    assert "(nothing changed)" in out
    assert "(no tracked changes)" in out


def test_created_files_are_shown_because_a_diff_would_hide_them(repo, monkeypatch, capsysbinary):
    """`git diff` covers tracked files only. "You added a file and nobody
    reviewed it" is the failure this probe exists to prevent."""
    (repo / "brand_new.py").write_text("def added():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    out = run(worktree_diff, [], capsysbinary)
    assert "brand_new.py" in out
    assert "def added()" in out, "the contents, not just the name"


def test_a_new_directory_is_expanded_to_its_files(repo, monkeypatch, capsysbinary):
    """`git status --short` collapses a new directory to one `?? pkg/` entry,
    and the first cut then tried to read a directory."""
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    out = run(worktree_diff, [], capsysbinary)
    assert "pkg/mod.py" in out.replace("\\", "/")
    assert "X = 1" in out
    assert "could not read" not in out


def test_tracked_edits_appear_as_a_diff(repo, monkeypatch, capsysbinary):
    (repo / "tracked.py").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    out = run(worktree_diff, [], capsysbinary)
    assert "-original" in out and "+changed" in out


def test_truncation_says_that_it_truncated(repo, monkeypatch, capsysbinary):
    (repo / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(300)), encoding="utf-8")
    monkeypatch.chdir(repo)
    out = run(worktree_diff, ["--max-lines", "10"], capsysbinary)
    assert "not shown" in out


def test_a_non_git_directory_is_an_observation(tmp_path, monkeypatch, capsysbinary):
    monkeypatch.chdir(tmp_path)
    out = run(worktree_diff, [], capsysbinary)
    assert "not a git repository" in out


def test_probes_emit_utf8_under_a_redirected_pipe(repo, tmp_path):
    """Same defect class as the gate library: source files are full of arrows
    and non-Latin strings, and a redirected Python stdout on Windows defaults
    to cp1252."""
    (repo / "unicode_new.py").write_text('ARROW = "a \u2192 b"\n', encoding="utf-8")
    out = tmp_path / "stdout.log"
    with open(out, "wb") as fh:
        proc = subprocess.run(
            [sys.executable, "-m", "lockstep.probes.worktree_diff"],
            cwd=repo, stdout=fh, stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": ""},
        )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
    assert "a \u2192 b" in out.read_text(encoding="utf-8")


def test_a_probe_never_writes_to_the_workspace(repo, monkeypatch, capsysbinary):
    """No working-tree file is created, removed or touched.

    `.git/` is excluded deliberately, not to make the test pass: `git status`
    refreshes the index's stat cache, which is git's own bookkeeping and not a
    workspace mutation. It cannot affect lockstep either way — `GitWorkspace`
    snapshots and restores through a throwaway `GIT_INDEX_FILE`, so the real
    index is not what any baseline is computed from.
    """
    (repo / "tracked.py").write_text("changed\n", encoding="utf-8")

    def snapshot():
        return {
            p.relative_to(repo).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(repo).parts
        }

    before = snapshot()
    monkeypatch.chdir(repo)
    run(worktree_diff, [], capsysbinary)
    assert snapshot() == before, "a probe observes; it must not touch the tree"
