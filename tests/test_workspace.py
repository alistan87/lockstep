"""GitWorkspace.restore is index-safe (SPEC §9.4).

`snapshot()` has always been scrupulous about the caller's index — it adds into
a temporary `GIT_INDEX_FILE` and writes a tree from that. `restore()` was not:
`git checkout <tree> -- <path>` writes the index as well as the worktree, so a
heal rollback destroyed whatever the operator had staged but not written out.
The baseline tree is built from the WORKTREE, so index-only content was never
captured in the first place — there is nothing for a rollback to restore it
from, and taking it is pure loss.

The fix is the idiom thirty lines above: run the checkout against a throwaway
index. It is still checkout, so eol/clean-smudge filters, file modes and
symlinks all round-trip; the alternative (`git cat-file blob` piped to the
worktree) silently corrupts every one of those.
"""

from __future__ import annotations

import os

import pytest

from lockstep.workspace import GitWorkspace

from conftest import git

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="core.filemode is false and symlinks need privilege on Windows"
)


def _staged(repo, rel: str) -> bytes:
    """The blob the index holds. Line endings normalized: `write_text` emits
    CRLF on Windows, which is a property of the fixture, not of restore."""
    import subprocess

    out = subprocess.run(
        ["git", "show", f":{rel}"], cwd=str(repo), capture_output=True, check=True
    ).stdout
    return out.replace(b"\r\n", b"\n")


def test_a_staged_but_unwritten_hunk_survives_a_rollback(git_repo):
    """Stage A, edit the worktree to B, snapshot, let the agent write C, roll
    back: the worktree returns to B and the index still holds A."""
    ws = GitWorkspace(git_repo)
    (git_repo / "a.txt").write_text("A\n", encoding="utf-8")
    git(git_repo, "add", "a.txt")
    (git_repo / "a.txt").write_text("B\n", encoding="utf-8")

    ref = ws.snapshot()
    (git_repo / "a.txt").write_text("C\n", encoding="utf-8")
    ws.restore(ref, ["a.txt"], git_repo / "discard")

    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "B\n"
    assert _staged(git_repo, "a.txt") == b"A\n"


def test_an_eol_normalized_file_is_restored_byte_for_byte(git_repo):
    """`* text=auto eol=crlf`: the blob is LF, the worktree is CRLF. A restore
    that writes the blob straight out would produce LF and `git status` would
    report nothing — the run says `restored` for a file it changed."""
    (git_repo / ".gitattributes").write_text("* text=auto eol=crlf\n", encoding="utf-8")
    git(git_repo, "add", ".gitattributes")
    git(git_repo, "commit", "-qm", "attrs")

    crlf = b"line one\r\nline two\r\n"
    (git_repo / "crlf.txt").write_bytes(crlf)

    ws = GitWorkspace(git_repo)
    ref = ws.snapshot()
    (git_repo / "crlf.txt").write_bytes(b"clobbered")
    ws.restore(ref, ["crlf.txt"], git_repo / "discard")

    assert (git_repo / "crlf.txt").read_bytes() == crlf


@posix_only
def test_an_executable_keeps_its_mode(git_repo):
    script = git_repo / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o755)

    ws = GitWorkspace(git_repo)
    ref = ws.snapshot()
    script.write_text("#!/bin/sh\necho clobbered\n", encoding="utf-8")
    script.chmod(0o644)
    ws.restore(ref, ["run.sh"], git_repo / "discard")

    assert script.stat().st_mode & 0o111, "exec bit lost by restore"


@posix_only
def test_a_symlink_is_restored_as_a_symlink(git_repo):
    link = git_repo / "link"
    link.symlink_to("a.txt")

    ws = GitWorkspace(git_repo)
    ref = ws.snapshot()
    link.unlink()
    link.write_text("a regular file now\n", encoding="utf-8")
    ws.restore(ref, ["link"], git_repo / "discard")

    assert link.is_symlink(), "symlink restored as a regular file"
    assert os.readlink(link) == "a.txt"


def test_a_created_path_is_still_moved_aside_not_deleted(git_repo):
    """The move-aside branch is unchanged by the index fix (SPEC §0.1 item 2)."""
    ws = GitWorkspace(git_repo)
    ref = ws.snapshot()
    (git_repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    discard = git_repo / "discard"
    ws.restore(ref, ["new.txt"], discard)

    assert not (git_repo / "new.txt").exists()
    assert (discard / "new.txt").read_text(encoding="utf-8") == "fresh\n"
