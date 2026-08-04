"""Cockpit UX proposal T1.3: `resume --cockpit` narrows the approval prompt.

`e` (edit) lets an operator substitute an approval's result text. That is a
coherent operator affordance and an incoherent thing to offer a non-programmer
who has been told in two places never to use it — and whose only escape from it
is Ctrl-Z then Enter. These tests pin three things, in order of importance:

  1. without the flag, SPEC §9.3 behaviour is unchanged, including `e`;
  2. with the flag, `e` is refused and the prompt says what to do instead;
  3. with the flag, the non-TTY auto-reject still fires first.

(3) matters most: the flag narrows what a PRESENT human may answer. If it ever
made stdin look answerable, the structural guarantee that an orchestrator
cannot approve would be gone.
"""

from __future__ import annotations

import builtins
from types import SimpleNamespace

from lockstep.state import load_state

from conftest import build

FLOW = {
    "name": "cockpit-approval",
    "nodes": [{"id": "ask", "role": "approval", "final": True}],
}


def _tty(monkeypatch, answers: list[str]) -> list[str]:
    """Pretend stdin is a terminal and feed `answers` to input(). Returns the
    list that collects every prompt string the engine actually displayed."""
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    seen: list[str] = []
    queue = list(answers)

    def fake_input(prompt: str = "") -> str:
        seen.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr(builtins, "input", fake_input)
    return seen


def test_default_prompt_is_unchanged(tmp_path, git_repo, monkeypatch):
    seen = _tty(monkeypatch, ["a"])
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    assert seen[0] == "[approval:ask] [a]pprove / [r]eject / [e]dit: "


def test_default_still_accepts_edit(tmp_path, git_repo, monkeypatch):
    # The operator affordance survives. Cockpit mode narrows the DE's surface;
    # it must not quietly remove a capability from everyone else.
    _tty(monkeypatch, ["e", "some replacement text"])
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["ask"].status == "done"
    assert "some replacement text" in (h.run_dir / "phases" / "ask" / "result.txt").read_text(
        encoding="utf-8"
    )


def test_cockpit_prompt_offers_only_two_answers(tmp_path, git_repo, monkeypatch):
    seen = _tty(monkeypatch, ["a"])
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 0
    assert seen[0] == "[approval:ask] [a]pprove / [r]eject: "
    assert "[e]dit" not in seen[0]


def test_cockpit_refuses_edit_and_says_what_to_do(tmp_path, git_repo, monkeypatch):
    _tty(monkeypatch, ["e", "a"])
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 0
    st = load_state(h.run_dir)
    assert st.nodes["ask"].status == "done"
    # `e` did NOT open the free-text editor: the result is the plain approval.
    assert (h.run_dir / "phases" / "ask" / "result.txt").read_text(encoding="utf-8") == "approved"
    # And the human was told what to type instead, rather than facing a prompt
    # that silently re-asks and reads as a frozen terminal.
    assert any("Only a (approve) or r (reject)" in line for line in h.logs)


def test_cockpit_reject_still_exits_6(tmp_path, git_repo, monkeypatch):
    _tty(monkeypatch, ["r"])
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 6
    assert load_state(h.run_dir).nodes["ask"].error == "approval rejected"


def test_cockpit_does_not_weaken_the_non_tty_guarantee(tmp_path, git_repo):
    # No monkeypatching: pytest's stdin is not a TTY. The flag must not make it
    # answerable — that is the guarantee the whole handoff rests on.
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 6
    assert "non-TTY" in load_state(h.run_dir).nodes["ask"].error


def test_cockpit_empty_line_does_not_spam(tmp_path, git_repo, monkeypatch):
    # Someone leaning on Enter should re-see the prompt, not a wall of advice.
    _tty(monkeypatch, ["", "", "a"])
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 0
    assert not [line for line in h.logs if "Only a (approve)" in line]


# ------------------------------------------- "nobody was there" vs "they said no"

def test_eof_is_recorded_as_auto_rejected_not_as_a_decision(tmp_path, git_repo, monkeypatch):
    """On Windows `NUL` is a CHARACTER DEVICE, so `sys.stdin.isatty()` is True
    for the cockpit's own documented launch idiom (`lockstep run <flow> < NUL`).
    The isatty guard does not fire; the first read EOFs instead.

    The outcome was always right — reject, exit 6, and an orchestrator still
    cannot approve, because writing to that stdin means a pipe and a pipe is not
    a character device — but the run was RECORDED as "approval rejected", which
    reads as a person having decided. Triage cannot tell "the human said no"
    from "nobody was there", and neither can the cockpit's rejection-reason
    prompt, which must not ask an absent human why.
    """
    _tty(monkeypatch, [])          # a TTY that immediately EOFs: the NUL shape
    h = build(tmp_path, FLOW, git_repo)
    assert h.engine.run() == 6
    error = load_state(h.run_dir).nodes["ask"].error
    assert "auto-rejected" in error
    assert error != "approval rejected"


def test_eof_in_cockpit_mode_does_not_loop(tmp_path, git_repo, monkeypatch):
    # Cockpit mode `continue`s on an unrecognised answer. EOF must exit, not
    # spin: a run that cannot be answered and will not stop is worse than
    # either outcome on its own.
    _tty(monkeypatch, [])
    h = build(tmp_path, FLOW, git_repo, cockpit=True)
    assert h.engine.run() == 6
    assert "auto-rejected" in load_state(h.run_dir).nodes["ask"].error
