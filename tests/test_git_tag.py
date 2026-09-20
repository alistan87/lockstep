"""Tests for `contrib/git_tag.py` — the release-cut flow's tag node body.

The defect this program exists to fix, from the live 0.17.0 cut
(`runs/release-cut-20260920T171840Z`): the node ran bare `git tag <tag>`,
which is SILENT on success. `executors/shell.py` only sets `result_text`
when `stdout.strip()` is truthy, and `roles.py::_finish` fails any attempt
whose `result_text is None` regardless of the exit code — so a successful
tag was scored as a failure, auto-retried 28 ms later, and the retry could
only fail with "tag already exists". The tag was correct; the node read
`failed`.

Two properties therefore matter more than the rest:

  1. it PRINTS on success, so the result channel is never empty;
  2. re-running it is not an error when the tag already points where it
     should — an auto-retry after a successful attempt must not turn a
     finished release into a failed node.

A tag that exists and points SOMEWHERE ELSE is still a hard failure: that is
a real conflict and silently moving it would be the destructive reading of
"idempotent".
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

CONTRIB = Path(__file__).resolve().parents[1] / "contrib"


def _load():
    spec = importlib.util.spec_from_file_location("git_tag", CONTRIB / "git_tag.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["git_tag"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("one", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "one")
    return r


def _run(repo: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, str(CONTRIB / "git_tag.py"), *args],
                       cwd=repo, capture_output=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def test_creates_the_tag_and_prints_something(repo: Path):
    """The whole point: a successful tag must leave a non-empty stdout, or
    the engine scores the attempt as resultless and retries it."""
    code, out, err = _run(repo, "--tag", "v1.0.0")
    assert code == 0, err
    assert out.strip(), "silent success is the bug this program exists to fix"
    assert "v1.0.0" in out
    assert _git(repo, "rev-parse", "v1.0.0^{commit}") == _git(repo, "rev-parse", "HEAD")


def test_the_printed_line_names_the_commit_it_tagged(repo: Path):
    head = _git(repo, "rev-parse", "HEAD")
    code, out, _ = _run(repo, "--tag", "v1.0.0")
    assert code == 0
    assert head[:8] in out, out


def test_rerunning_on_the_same_commit_succeeds(repo: Path):
    """The auto-retry case. Attempt 1 creates it; attempt 2 must not fail."""
    assert _run(repo, "--tag", "v1.0.0")[0] == 0
    code, out, err = _run(repo, "--tag", "v1.0.0")
    assert code == 0, err
    assert out.strip()
    assert "already" in out.lower()


def test_a_tag_pointing_elsewhere_is_a_hard_failure(repo: Path):
    """Idempotent must not mean "move the tag"."""
    _run(repo, "--tag", "v1.0.0")
    (repo / "f.txt").write_text("two", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")

    code, _, err = _run(repo, "--tag", "v1.0.0")
    assert code != 0
    assert "v1.0.0" in err
    # the tag did NOT move
    assert _git(repo, "rev-parse", "v1.0.0^{commit}") != _git(repo, "rev-parse", "HEAD")


def test_annotated_tags_are_supported_and_reported(repo: Path):
    code, out, err = _run(repo, "--tag", "v1.0.0", "--message", "release one")
    assert code == 0, err
    assert "annotated" in out.lower()
    assert _git(repo, "cat-file", "-t", "v1.0.0") == "tag"


def test_an_annotated_rerun_on_the_same_commit_succeeds(repo: Path):
    assert _run(repo, "--tag", "v1.0.0", "--message", "release one")[0] == 0
    code, out, err = _run(repo, "--tag", "v1.0.0", "--message", "release one")
    assert code == 0, err
    assert "already" in out.lower()


def test_a_git_failure_is_not_swallowed(repo: Path):
    """Unlike a probe, this body must fail the node when git fails."""
    code, _, err = _run(repo, "--tag", "not a valid tag name")
    assert code != 0
    assert err.strip()


def test_tagging_an_explicit_commit(repo: Path):
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "f.txt").write_text("two", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")

    code, out, err = _run(repo, "--tag", "v1.0.0", "--commit", first)
    assert code == 0, err
    assert _git(repo, "rev-parse", "v1.0.0^{commit}") == first
    assert first[:8] in out
