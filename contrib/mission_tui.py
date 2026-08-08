#!/usr/bin/env python
"""mission_tui.py — MISSION and ACTIVITY in one process (proposal T3.1).

    python contrib/mission_tui.py                 # follow the newest run
    python contrib/mission_tui.py runs/<run>      # a specific run

Keys:  1-9  what happened at that step      e  the approval evidence
       c    the cost panel (press again to  q  close the view (never the run)
            toggle history <-> head)        r  force a repaint

WHY NOT TEXTUAL. It was evaluated and it would have been less code: scrollback,
collapsible sections, keybinds and 60 fps repaint, all for free. It was rejected
on this repo's own working agreement — `pydantic` is the only runtime
dependency, and the rule is to prefer deleting a feature over adding one. A
cockpit that is pip-install-fragile on a work laptop nobody here administers is
worse than a plainer cockpit that always starts, and the promise made to the
domain expert is "double-click start-cockpit.cmd; it is the same every time".

The second reason is that the rendering lives in `mission_view.py`, which is
ordinary Python and therefore tested — moving the view layer into the half of
the repo that has a test suite is worth more than a nicer widget set.

WHAT THIS FIXES STRUCTURALLY, rather than by being careful:

- one process instead of two polling PowerShell loops plus a python subprocess
  spawned once a second (F4);
- a line-diffing repaint instead of a full-screen clear per tick (F5);
- one idea of which node is the frontier, instead of two panes computing it
  independently and disagreeing during a transition.

This is ADDITIVE. `cockpit.ps1` remains the default and is unchanged in
behaviour; correctness still lives there, because it is the path that ships to
machines whose terminal configuration nobody here controls.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mission_view as mv  # noqa: E402

ALT_ON, ALT_OFF = "\x1b[?1049h", "\x1b[?1049l"
CURSOR_OFF, CURSOR_ON = "\x1b[?25l", "\x1b[?25h"
DIM, CYAN, YELLOW, RED, RESET = "\x1b[90m", "\x1b[36m", "\x1b[33m", "\x1b[31m", "\x1b[0m"

SPEND_EVERY_S = 10.0
SESSION_EVERY_S = 30.0


# ------------------------------------------------------------------ input

class Keys:
    """Non-blocking single keypress, or None. Windows and POSIX.

    A view that blocks on input stops updating, which for this pane means the
    liveness promise ("blank never means dead") quietly stops being true.
    """

    def __init__(self) -> None:
        self._posix = None
        if os.name != "nt":
            try:
                import termios
                import tty
                self._termios, self._tty = termios, tty
                self._fd = sys.stdin.fileno()
                self._saved = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
                self._posix = True
            except Exception:  # noqa: BLE001 - no tty: keys are simply unavailable
                self._posix = False

    # Windows delivers an extended key (arrows, PgUp/PgDn, Home, Insert, F-keys)
    # as TWO reads: a prefix, then a code that collides with ordinary letters.
    # PgDn is ('\xe0', 'Q') — lowercased to 'q', which QUIT THE VIEW. A
    # non-programmer facing a wall of text presses PgDn to scroll, and their
    # monitoring surface vanished. Insert ('\xe0','R') forced a repaint.
    _EXTENDED_PREFIXES = ("\x00", "\xe0")

    def get(self) -> str | None:
        if os.name == "nt":
            try:
                import msvcrt
                if not msvcrt.kbhit():
                    return None
                ch = msvcrt.getwch()
                if ch in self._EXTENDED_PREFIXES:
                    # Consume the code byte and report nothing: this view has no
                    # scrolling to bind them to, and swallowing is the only safe
                    # answer while they alias onto real commands.
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    return None
                return ch
            except Exception:  # noqa: BLE001
                return None
        if not self._posix:
            return None
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None

    def restore(self) -> None:
        if self._posix:
            try:
                self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------- spend

def spend_lines(run_dir: Path, floor: dict) -> tuple[list[str], dict]:
    """The compact spend block, computed in-process.

    cockpit.ps1 shells out to cost_report.py for this. Importing it instead is
    the whole point of collapsing to one process — the numbers are identical
    because it is literally the same function.

    `floor` is compact_block's monotonic guard, threaded through every poll.
    cockpit.ps1 keeps its own guard (Update-Spend) and cost_report --watch
    keeps one; this caller passed nothing, so a poll that caught a log
    mid-write showed a total that went BACKWARDS — the tally inconsistency
    this fixes. Reset it (pass {}) when the bound run changes.
    """
    try:
        import cost_report
        maps = cost_report.load_field_maps(None)
        run = cost_report.collect_run(Path(run_dir), maps)
        cap = cost_report._budget_cap(Path(run_dir))
        text, floor = cost_report.compact_block([run], [cap], floor)
        return text.splitlines(), floor
    except Exception as e:  # noqa: BLE001 - display-only, always
        return [f"(spend unavailable: {e})"], floor


def session_lines(repo_root: Path, runs_root: Path) -> list[str]:
    """The session block: the orchestrator's own transcript spend plus every
    run started this session (contrib/session_spend.py)."""
    try:
        import session_spend
        return session_spend.session_lines(Path(repo_root), Path(runs_root))
    except Exception as e:  # noqa: BLE001 - display-only, always
        return [f"(session spend unavailable: {e})"]


# ---------------------------------------------------------------- painting

class Screen:
    """Line-diffing painter over the alternate screen buffer.

    Only lines that changed are rewritten, so the terminal's own scrollback and
    the human's sense of what is new both survive. The full-screen clear this
    replaces repainted everything once a second, which destroyed both.
    """

    def __init__(self) -> None:
        self._painted: list[str] = []

    def paint(self, lines: list[str]) -> None:
        out: list[str] = []
        for row, text in enumerate(lines):
            if row < len(self._painted) and self._painted[row] == text:
                continue
            out.append(f"\x1b[{row + 1};1H\x1b[2K{text}")
        for row in range(len(lines), len(self._painted)):
            out.append(f"\x1b[{row + 1};1H\x1b[2K")
        if out:
            sys.stdout.write("".join(out))
            sys.stdout.flush()
        self._painted = list(lines)

    def reset(self) -> None:
        self._painted = []


def compose(run_dir: Path, repo_root: Path, spend: list[str], session: list[str],
            overlay: list[str] | None, rows: int,
            cost_mode: str | None = None) -> list[str]:
    if overlay is not None:
        room = max(1, rows - 2)
        shown = overlay[:room]
        # Every other truncation in this codebase announces itself; a decision
        # surface that silently drops the end of the evidence must not be the
        # exception.
        if len(overlay) > room:
            shown = shown[:-1] + [f"{DIM}... {len(overlay) - room + 1} more lines "
                                  f"- see the file itself{RESET}"]
        return shown + ["", f"{DIM}any key to go back   q quits the view{RESET}"]

    if cost_mode is not None:
        # The cost panel (pi-taskflow-styled; mission_view.cost_lines). Unlike
        # the static overlays it is recomposed every tick, so a running node's
        # cost-so-far stays live.
        frame = [f"{CYAN}COSTS  {run_dir.name}{RESET}", "-" * mv.WIDTH]
        frame += mv.cost_lines(run_dir, mode=cost_mode)
        frame += ["-" * mv.WIDTH]
        frame += [f"{YELLOW}{ln}{RESET}" for ln in spend]
        frame += [f"{DIM}{ln}{RESET}" for ln in session]
        frame += ["", f"{DIM}  c toggle history <-> head   any other key back "
                      f"  q close this view   {datetime.now().strftime('%H:%M:%S')}{RESET}"]
        return frame[:rows]

    frame = [f"{CYAN}MISSION  {run_dir.name}{RESET}", "-" * mv.WIDTH]

    # Rows carry their node id, so the number beside a line and the node the key
    # selects are the same fact rather than two computations that agree today.
    idx = 0
    for node_id, text in mv.mission_rows(run_dir, repo_root=repo_root):
        if node_id:
            idx += 1
            frame.append(f"{DIM}{idx}{RESET} {text}" if idx <= 9 else f"  {text}")
        else:
            frame.append(f"  {text}")

    frame += ["-" * mv.WIDTH]
    frame += [f"{YELLOW}{ln}{RESET}" for ln in spend]
    frame += [f"{DIM}{ln}{RESET}" for ln in session]
    frame += ["-" * mv.WIDTH, f"{CYAN}ACTIVITY{RESET}"]
    frame += ["  " + ln for ln in mv.activity_lines(run_dir, repo_root=repo_root)]

    state = mv.read_json(Path(run_dir) / "state.json")
    if mv.needs_you(state):
        frame += ["", f"{RED}  NEEDS YOU - read the approval pane, or the chat{RESET}"]

    frame += [""]
    frame += [f"{DIM}  1-9 what happened   c costs   e evidence   r repaint "
              f"  q close this view   {datetime.now().strftime('%H:%M:%S')}{RESET}"]
    return frame[:rows]


# ------------------------------------------------------------------- main

def run(runs_root: Path, run_dir: Path | None, repo_root: Path, interval: float) -> int:
    keys = Keys()
    screen = Screen()
    sys.stdout.write(ALT_ON + CURSOR_OFF)
    sys.stdout.flush()
    overlay: list[str] | None = None
    cost_mode: str | None = None
    spend: list[str] = ["(spend unavailable)"]
    session: list[str] = []
    floor: dict = {}
    spend_at = 0.0
    session_at = 0.0
    bound: Path | None = None
    was_needing = False
    try:
        while True:
            current = run_dir or mv.newest_run(runs_root)
            if current is None:
                screen.paint([f"{CYAN}MISSION{RESET}", "-" * mv.WIDTH, "",
                              "  no run yet.", "",
                              "  Tell the assistant what you would like to work on;",
                              "  this view fills in by itself once something starts."])
                _sleep(interval)
                if "q" in _drain(keys):
                    return 0
                continue

            if current != bound:
                bound, spend_at, overlay, was_needing = current, 0.0, None, False
                cost_mode = None
                floor = {}  # the monotonic guard is per-run; a new run starts at 0
                screen.reset()

            now = datetime.now(timezone.utc).timestamp()
            if now - spend_at >= SPEND_EVERY_S:
                spend, floor = spend_lines(current, floor)
                spend_at = now
            if now - session_at >= SESSION_EVERY_S:
                # Slower cadence than spend: this re-reads the orchestrator's
                # whole transcript, which grows for as long as the session does.
                session = session_lines(repo_root, runs_root)
                session_at = now

            # Notify on the EDGE, not the level: a signal that repeats every
            # second is an alarm, and an alarm gets muted.
            state = mv.read_json(current / "state.json")
            needing = mv.needs_you(state)
            if needing and not was_needing:
                sys.stdout.write("\a")
                sys.stdout.flush()
            was_needing = needing

            rows = _rows()
            try:
                frame = compose(current, repo_root, spend, session, overlay, rows,
                                cost_mode=cost_mode)
            except Exception as e:  # noqa: BLE001
                # A view must never be the reason anything stops — including
                # itself. Every reader below is already defensive; this is the
                # backstop for the one nobody thought of, and it says what
                # happened rather than leaving a frozen screen.
                frame = [f"{RED}the view hit an error and is still watching{RESET}",
                         f"  {type(e).__name__}: {e}",
                         "", f"{DIM}the run is unaffected - r to repaint, q to close{RESET}"]
            screen.paint(frame)

            for key in _drain(keys):
                if key == "q":
                    return 0
                # The overlay check comes FIRST so "any key to go back" is true.
                # With `r` tested above it, pressing the labelled dismiss key
                # repainted the overlay instead of closing it.
                if overlay is not None:
                    overlay = None
                    screen.reset()
                elif cost_mode is not None:
                    # Inside the cost panel `c` is the labelled toggle
                    # (history <-> head); anything else goes back, same
                    # convention as the overlays.
                    cost_mode = ("head" if cost_mode == "history" else "history") \
                        if key == "c" else None
                    screen.reset()
                elif key == "c":
                    cost_mode = "history"
                    screen.reset()
                elif key == "r":
                    screen.reset()
                elif key == "e":
                    text = mv.read_text(current / "approval-evidence.txt")
                    overlay = (text.splitlines() if text else
                               ["(no approval evidence has been rendered for this run)"])
                    screen.reset()
                elif key.isdigit() and key != "0":
                    order = mv.visible_nodes(current, repo_root)
                    pick = int(key) - 1
                    if pick < len(order):
                        overlay = mv.node_detail(current, order[pick], repo_root)
                        screen.reset()
            _sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        keys.restore()
        sys.stdout.write(CURSOR_ON + ALT_OFF)
        sys.stdout.flush()


def _rows() -> int:
    try:
        return max(12, os.get_terminal_size().lines - 1)
    except OSError:
        return 40


def _drain(keys: Keys) -> list[str]:
    """Every key buffered since the last tick, in order.

    Returning only the LAST one lost input: pressing `3` then `e` inside one
    poll interval dropped the `3` silently. A view that eats keystrokes teaches
    the person using it that it is unreliable, which is expensive for a surface
    whose whole job is to be trusted.
    """
    out: list[str] = []
    while True:
        k = keys.get()
        if k is None:
            return out
        out.append(k.lower())


def _sleep(seconds: float) -> None:
    import time
    time.sleep(max(0.25, seconds))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--interval", type=float, default=1.0)
    ns = ap.parse_args(argv)
    if not sys.stdout.isatty():
        print("mission_tui needs a terminal; use contrib/mission_server.py for a page,\n"
              "or contrib/cockpit.ps1 -Role mission for a pane.", file=sys.stderr)
        return 2
    return run(Path(ns.runs_root), Path(ns.run_dir) if ns.run_dir else None,
               Path(ns.repo_root), ns.interval)


if __name__ == "__main__":
    raise SystemExit(main())
