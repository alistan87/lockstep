#!/usr/bin/env python
"""play_check.py — does the generated front end actually play? -> Verdict.

    python contrib/demo/play_check.py Deliverables/sudoku

The second gate of `flows/demo/sudoku-local.tg.json`. It drives `play.py` as a
real subprocess with a scripted stdin, because that is the only way to find out
whether a command-line program works: importing it proves nothing, and a model
asked for `if __name__ == "__main__":` will sometimes put the loop at module
scope, where importing it hangs forever on `input()`.

Checked, in order:
  - importing `play.py` runs nothing and does not block (a 15s timeout, so a
    module-scope input() loop is caught rather than hanging the run);
  - running it prints a grid;
  - `solve` fills the grid in;
  - `quit` exits cleanly, without a traceback.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lockstep.gates._common import emit, finding  # noqa: E402

IMPORT_PROBE = (
    "import importlib.util,sys\n"
    "s=importlib.util.spec_from_file_location('play',sys.argv[1])\n"
    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
    "print('IMPORT-CLEAN')\n"
)


def bad(claim: str, evidence: str, fix: str, path: str) -> dict:
    return finding("blocker", "correctness", path, claim, evidence, fix)


def run(argv: list[str], stdin: str, cwd: Path, timeout: int):
    return subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          cwd=str(cwd), timeout=timeout, shell=False,
                          encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="play_check")
    ap.add_argument("out_dir")
    ns = ap.parse_args(argv)
    out = Path(ns.out_dir)
    play = out / "play.py"
    rel = str(play).replace("\\", "/")

    if not play.is_file():
        return emit([bad("the front end was not written", f"{rel} does not exist",
                         "Emit the complete Python source and nothing else.", rel)], "")

    try:
        probe = run([sys.executable, "-c", IMPORT_PROBE, "play.py"], "", out, 15)
    except subprocess.TimeoutExpired:
        return emit([bad(
            "importing the module blocks",
            "the import did not return within 15s — the game loop runs at module scope",
            "Put every statement that reads input or prints the board inside "
            '`if __name__ == "__main__":`, so importing the file runs nothing.',
            rel)], "")
    if "IMPORT-CLEAN" not in probe.stdout:
        return emit([bad("the file is not importable Python",
                         (probe.stderr.strip().splitlines() or ["no stderr"])[-1],
                         "Return ONLY the Python source. No markdown fences, no prose.",
                         rel)], "")

    findings = []
    try:
        session = run([sys.executable, "play.py"], "solve\nquit\n", out, 60)
    except subprocess.TimeoutExpired:
        return emit([bad("the game did not exit", "still running 60s after `solve` then `quit`",
                         "`quit` must break the loop and end the program.", rel)], "")

    text = session.stdout
    digits = sum(ch.isdigit() for ch in text)
    if digits < 81:
        findings.append(bad("no board was printed",
                            f"stdout carried {digits} digits over {len(text)} chars",
                            "Print the generated puzzle before the first prompt, using "
                            "sudoku_core.format_grid(grid).", rel))
    if session.returncode != 0:
        last = (session.stderr.strip().splitlines() or ["no stderr"])[-1]
        findings.append(bad(f"the game exited {session.returncode}", last,
                            "`solve` then `quit` must run to a clean exit.", rel))
    elif "Traceback" in session.stderr:
        findings.append(bad("the game raised while playing",
                            session.stderr.strip().splitlines()[-1],
                            "Handle `solve` and `quit` before parsing a line as a move.", rel))

    return emit(findings, f"imports cleanly, prints a board, accepts `solve`, exits on `quit` "
                          f"({digits} digits printed)")


if __name__ == "__main__":
    raise SystemExit(main())
