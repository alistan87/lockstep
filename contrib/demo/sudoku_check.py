#!/usr/bin/env python
"""sudoku_check.py — does the generated module actually work? -> Verdict.

    python contrib/demo/sudoku_check.py Deliverables/sudoku/sudoku_core.py

A gate body for `flows/demo/sudoku-local.tg.json`. It imports the file the
model produced and puts it through the checks a sudoku implementation either
passes or does not:

  - it imports at all (a model that answered with prose, or left a markdown
    fence around the code, fails HERE rather than three steps downstream);
  - `solve` completes a known-solvable grid, and the completion is legal —
    every row, column and box a permutation of 1..9;
  - `generate` returns a 9x9 grid with at least 17 clues (below that no puzzle
    has a unique solution) and no clue that breaks a rule;
  - a generated puzzle is solvable by `solve`.

EVERY ONE OF THOSE RUNS IN A CHILD PROCESS WITH A CLOCK ON IT. Model-written
backtracking loops forever surprisingly often, and a gate that hangs is worse
than one that fails: lockstep kills it at `timeout_s`, retries once, and gets
no verdict either time, so the flow fails closed with `no valid verdict
emitted` — correct, and useless to whoever has to fix it. A timeout here
becomes a finding that names the function that hung, which is what the heal
round hands back to the generator.

Deterministic and offline: no model, no network. A BLOCK is the next prompt,
so write each `fix_hint` as an instruction.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lockstep.gates._common import emit, finding  # noqa: E402

PROBE_TIMEOUT_S = 25
ROWS = range(9)
SOLVABLE = [
    [5, 3, 0, 0, 7, 0, 0, 0, 0], [6, 0, 0, 1, 9, 5, 0, 0, 0],
    [0, 9, 8, 0, 0, 0, 0, 6, 0], [8, 0, 0, 0, 6, 0, 0, 0, 3],
    [4, 0, 0, 8, 0, 3, 0, 0, 1], [7, 0, 0, 0, 2, 0, 0, 0, 6],
    [0, 6, 0, 0, 0, 0, 2, 8, 0], [0, 0, 0, 4, 1, 9, 0, 0, 5],
    [0, 0, 0, 0, 8, 0, 0, 7, 9],
]


def bad(claim: str, evidence: str, fix: str, path: str) -> dict:
    return finding("blocker", "correctness", path, claim, evidence, fix)


# --------------------------------------------------------------- the probe
#
# Runs in a CHILD process. Prints one JSON object; never raises past main().

def is_grid(g) -> bool:
    return (isinstance(g, list) and len(g) == 9
            and all(isinstance(r, list) and len(r) == 9
                    and all(isinstance(v, int) and 0 <= v <= 9 for v in r) for r in g))


def units(g):
    for r in ROWS:
        yield f"row {r}", [g[r][c] for c in ROWS]
    for c in ROWS:
        yield f"column {c}", [g[r][c] for r in ROWS]
    for br in (0, 3, 6):
        for bc in (0, 3, 6):
            yield f"box {br},{bc}", [g[br + i][bc + j] for i in range(3) for j in range(3)]


def illegal_completion(g) -> str | None:
    for name, vals in units(g):
        if sorted(vals) != list(range(1, 10)):
            return f"{name} is {vals}, not a permutation of 1..9"
    return None


def illegal_clues(g) -> str | None:
    for name, vals in units(g):
        filled = [v for v in vals if v]
        if len(filled) != len(set(filled)):
            return f"{name} repeats a clue: {vals}"
    return None


def _completion(returned, grid):
    """The completed grid, whichever convention the module chose.

    `solve(grid) -> completed grid` and `solve(grid) -> bool, mutating in
    place` are both standard, and the second is what almost every sudoku
    tutorial writes. Insisting on one of them is a check on style, not on
    correctness — what this gate is for is whether the puzzle actually gets
    legally solved. Returns None if neither convention produced a full grid.
    """
    if is_grid(returned) and all(v for row in returned for v in row):
        return returned
    if is_grid(grid) and all(v for row in grid for v in row):
        return grid
    return None


def probe(path: str) -> dict:
    """{stage, ok, detail, clues} — the child's report."""
    try:
        spec = importlib.util.spec_from_file_location("sudoku_core", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # type: ignore[union-attr]
    except Exception:
        last = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return {"stage": "import", "ok": False, "detail": last}

    missing = [n for n in ("solve", "generate") if not callable(getattr(mod, n, None))]
    if missing:
        public = sorted(n for n in vars(mod) if not n.startswith("_"))
        return {"stage": "api", "ok": False, "detail": f"{missing} missing; module defines {public}"}

    grid = [row[:] for row in SOLVABLE]
    try:
        returned = mod.solve(grid)
    except Exception:
        return {"stage": "solve", "ok": False,
                "detail": traceback.format_exc(limit=3).strip().splitlines()[-1]}
    solved = _completion(returned, grid)
    if solved is None:
        return {"stage": "solve-shape", "ok": False,
                "detail": f"returned {type(returned).__name__} ({str(returned)[:60]}) and left "
                          f"the grid it was given unfinished"}
    problem = illegal_completion(solved)
    if problem:
        return {"stage": "solve-legal", "ok": False, "detail": problem}

    try:
        puzzle = mod.generate()
    except Exception:
        return {"stage": "generate", "ok": False,
                "detail": traceback.format_exc(limit=3).strip().splitlines()[-1]}
    if not is_grid(puzzle):
        return {"stage": "generate-shape", "ok": False,
                "detail": f"returned {type(puzzle).__name__}: {str(puzzle)[:120]}"}
    problem = illegal_clues(puzzle)
    if problem:
        return {"stage": "generate-legal", "ok": False, "detail": problem}
    clues = sum(1 for r in puzzle for v in r if v)
    if clues < 17:
        return {"stage": "generate-clues", "ok": False,
                "detail": f"{clues} clues; no 9x9 sudoku with fewer than 17 has a unique solution"}

    work = [row[:] for row in puzzle]
    try:
        again = _completion(mod.solve(work), work)
    except Exception:
        again = None
    if again is None or illegal_completion(again):
        return {"stage": "round-trip", "ok": False,
                "detail": f"solve() did not legally complete the {clues}-clue puzzle it was given"}
    return {"stage": "done", "ok": True, "detail": "", "clues": clues}


# ---------------------------------------------------------------- the gate

FIX = {
    "import": "Return ONLY the Python source. Do not wrap it in ``` fences and do not "
              "write any prose before or after it.",
    "api": "Define module-level functions named exactly solve(grid) and generate().",
    "solve": "solve(grid) must take a 9x9 list of lists (0 = empty) and return a completed grid.",
    "solve-shape": "solve(grid) must finish the grid: either fill it IN PLACE and return True, or return the completed 9x9 grid.",
    "solve-legal": "Every row, column and 3x3 box must contain 1..9 exactly once.",
    "generate": "generate() must take no required arguments and return a 9x9 grid.",
    "generate-shape": "Return a list of 9 lists of 9 ints, 0 for an empty cell.",
    "generate-legal": "Remove cells from a SOLVED grid; never place clues at random.",
    "generate-clues": "Keep at least 25 clues.",
    "round-trip": "generate() must remove cells from a grid solve() can complete.",
    "timeout": "Use plain backtracking that returns as soon as the grid is full, and build "
               "the puzzle by removing a FIXED number of cells from one solved grid. Do not "
               "search for a minimal puzzle and do not re-check uniqueness in a loop.",
}

CLAIM = {
    "import": "the file is not importable Python",
    "api": "the required functions are missing",
    "solve": "solve() raised on a known-solvable grid",
    "solve-shape": "solve() did not return a 9x9 grid of ints",
    "solve-legal": "solve() returned an illegal solution",
    "generate": "generate() raised",
    "generate-shape": "generate() did not return a 9x9 grid of ints",
    "generate-legal": "generate() produced a puzzle that breaks the rules",
    "generate-clues": "generate() left too few clues",
    "round-trip": "a generated puzzle is not solvable by solve()",
    "timeout": "the module does not finish",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sudoku_check")
    ap.add_argument("path", help="the generated module")
    ap.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    ns = ap.parse_args(argv)

    if ns.probe:                       # the child
        print(json.dumps(probe(ns.path)))
        return 0

    path = Path(ns.path)
    rel = str(path).replace("\\", "/")
    if not path.is_file():
        return emit([bad("the module was not written", f"{rel} does not exist",
                         FIX["import"], rel)], "")
    try:
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(path), "--probe"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, shell=False,
            encoding="utf-8", errors="replace")
        report = json.loads(child.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        report = {"stage": "timeout", "ok": False,
                  "detail": f"solve() or generate() did not return within {PROBE_TIMEOUT_S}s"}
    except Exception:
        report = {"stage": "import", "ok": False,
                  "detail": f"the check itself could not run: "
                            f"{traceback.format_exc(limit=2).strip().splitlines()[-1]}"}

    if report.get("ok"):
        return emit([], f"imports, solves a known grid, and generates a legal "
                        f"{report.get('clues')}-clue puzzle that solve() completes")
    stage = report.get("stage", "import")
    return emit([bad(CLAIM.get(stage, stage), report.get("detail", ""),
                     FIX.get(stage, FIX["import"]), rel)], "")


if __name__ == "__main__":
    raise SystemExit(main())
