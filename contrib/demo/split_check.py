#!/usr/bin/env python
"""split_check.py — does the generated settlement module actually settle? -> Verdict.

    python contrib/demo/split_check.py backend/split.py

The healing signal for `flows/demo/webapp-local.tg.json`. An expense splitter
was chosen over a CRUD app for one reason: it has invariants a gate can check
with ITS OWN implementation, so a wrong answer produces a concrete
counterexample instead of a shrug. "Did it store the row" is either trivially
right or trivially broken and neither teaches the model anything; "these three
expenses settle to a transfer list that leaves Bob 4.17 short" is a fix_hint.

The module under test must expose two PURE functions — no server, no I/O:

    balances(expenses) -> {name: net}      net > 0 means they are owed
    settle(balances)   -> [{"from","to","amount"}, ...]

where `expenses` is a list of {"payer", "amount", "participants"} and each
expense splits `amount` equally among its participants.

What is checked, and why each one earns its place:

  - **it imports at all.** A model that answered with prose, or left a markdown
    fence around the code, fails HERE rather than three nodes downstream.
  - **balances matches a reference** on seeded scenarios. This one the gate can
    compute exactly, so it does.
  - **balances sum to zero.** Money is conserved; a splitter that invents or
    destroys it is wrong in a way no amount of endpoint testing would reveal.
  - **the settlement actually settles.** Apply every transfer and each balance
    must land within a cent of zero. This is the check that catches the greedy
    matcher that leaves residue on the last pair — the single most common way a
    small model gets this wrong.
  - **at most n-1 transfers.** Not optimality (that is NP-hard and not the
    model's job); just the bound any correct greedy settlement satisfies. A
    module that pays everyone off pairwise passes every other check and is
    still useless.
  - **no zero, negative or self transfers.** Cheap, and each has a distinct
    fix.
  - **order independence.** Shuffling the expense list must not change the
    balances. Catches accumulator bugs that only show under a particular order,
    which is exactly the class a single hand-written test misses.

SEEDED, NOT RANDOM. A gate whose inputs vary per run produces different
findings for identical code, which would make its verdict — and the input hash
of everything downstream — non-reproducible.

EVERYTHING RUNS IN A CHILD PROCESS WITH A CLOCK. Model-written code hangs
surprisingly often, and a gate that hangs is worse than one that fails: the
driver kills it at `timeout_s`, retries once, gets no verdict either time, and
the flow fails closed with "no valid verdict emitted" — correct, and useless to
whoever has to fix it. A timeout caught HERE becomes a finding that names the
function.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT_S = 60
TOL = 0.01          # a cent: these are money, not physics
SEED = 20260809


# --------------------------------------------------------------- the reference

def ref_balances(expenses: list[dict]) -> dict[str, float]:
    net: dict[str, float] = {}
    for e in expenses:
        share = e["amount"] / len(e["participants"])
        net[e["payer"]] = net.get(e["payer"], 0.0) + e["amount"]
        for p in e["participants"]:
            net[p] = net.get(p, 0.0) - share
    return net


def scenarios() -> list[list[dict]]:
    """Seeded cases, small enough that a counterexample fits in a fix_hint."""
    rng = random.Random(SEED)
    people = ["ana", "ben", "cy", "dee", "eli"]
    out = [
        # A hand-written minimum: two people, one expense.
        [{"payer": "ana", "amount": 10.0, "participants": ["ana", "ben"]}],
        # Everyone pays once — balances should cancel to nothing.
        [{"payer": p, "amount": 30.0, "participants": people[:3]} for p in people[:3]],
        # A payer who is NOT a participant: they are owed the whole amount.
        [{"payer": "ana", "amount": 9.0, "participants": ["ben", "cy", "dee"]}],
        # Thirds: 10/3 does not divide evenly, which is where cents go missing.
        [{"payer": "ana", "amount": 10.0, "participants": ["ana", "ben", "cy"]}],
    ]
    for _ in range(8):
        n = rng.randint(2, 5)
        group = people[:n]
        out.append([
            {"payer": rng.choice(group),
             "amount": round(rng.uniform(1, 200), 2),
             "participants": rng.sample(group, rng.randint(1, n))}
            for _ in range(rng.randint(1, 5))
        ])
    return out


# --------------------------------------------------------------- the worker

def worker(module_path: str) -> int:
    """Runs in the child. Prints one JSON report; never raises past the guard."""
    import importlib.util
    import traceback

    def fail(stage, detail, case=None):
        print(json.dumps({"stage": stage, "ok": False, "detail": detail, "case": case}))
        return 0

    path = Path(module_path)
    if not path.is_file():
        return fail("import", f"{module_path} does not exist")
    try:
        spec = importlib.util.spec_from_file_location("candidate_split", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return fail("import", traceback.format_exc(limit=3).strip())

    for name in ("balances", "settle"):
        if not callable(getattr(mod, name, None)):
            return fail("api", f"module defines no callable {name!r}")

    rng = random.Random(SEED + 1)
    for case in scenarios():
        want = ref_balances(case)
        try:
            got = mod.balances(case)
        except Exception:
            return fail("balances", traceback.format_exc(limit=3).strip(), case)
        if not isinstance(got, dict):
            return fail("balances", f"returned {type(got).__name__}, expected dict", case)
        names = set(want) | set(got)
        for p in names:
            if abs(float(got.get(p, 0.0)) - want.get(p, 0.0)) > 1e-6:
                return fail(
                    "balances",
                    f"{p} = {got.get(p)!r}, reference says {round(want.get(p, 0.0), 4)}",
                    case,
                )
        if abs(sum(float(v) for v in got.values())) > 1e-6:
            return fail("conserve", f"balances sum to {sum(got.values())!r}, not 0", case)

        shuffled = case[:]
        rng.shuffle(shuffled)
        try:
            got2 = mod.balances(shuffled)
        except Exception:
            return fail("order", traceback.format_exc(limit=3).strip(), shuffled)
        for p in set(got) | set(got2):
            if abs(float(got.get(p, 0.0)) - float(got2.get(p, 0.0))) > 1e-6:
                return fail("order", f"{p} changes when the expense order changes", case)

        try:
            transfers = mod.settle(dict(got))
        except Exception:
            return fail("settle", traceback.format_exc(limit=3).strip(), case)
        if not isinstance(transfers, list):
            return fail("settle", f"returned {type(transfers).__name__}, expected list", case)

        residual = {p: float(got.get(p, 0.0)) for p in names}
        for t in transfers:
            try:
                src, dst, amt = t["from"], t["to"], float(t["amount"])
            except (TypeError, KeyError, ValueError):
                return fail("settle", f"transfer is not {{from,to,amount}}: {t!r}", case)
            if src == dst:
                return fail("settle", f"self-transfer for {src!r}", case)
            if amt <= 0:
                return fail("settle", f"non-positive transfer amount {amt!r}", case)
            residual[src] = residual.get(src, 0.0) + amt
            residual[dst] = residual.get(dst, 0.0) - amt
        worst = max(names, key=lambda p: abs(residual.get(p, 0.0)), default=None)
        if worst is not None and abs(residual[worst]) > TOL:
            return fail(
                "settle",
                f"after applying {len(transfers)} transfer(s), {worst} is still "
                f"{round(residual[worst], 2)} away from settled",
                case,
            )
        involved = len([p for p in names if abs(float(got.get(p, 0.0))) > TOL])
        if involved and len(transfers) > max(involved - 1, 0):
            return fail(
                "minimal",
                f"{len(transfers)} transfers for {involved} people with a non-zero "
                f"balance; at most {involved - 1} are ever needed",
                case,
            )

    print(json.dumps({"stage": "all", "ok": True, "cases": len(scenarios())}))
    return 0


# --------------------------------------------------------------- the gate

FIX = {
    "import": "make the file a plain importable Python module — no markdown fence, no prose, "
              "no server startup or other work at import time",
    "api": "define exactly the two required functions at module level",
    "balances": "credit the payer the FULL amount and debit each participant an equal share "
                "(amount / len(participants)); a payer who is also a participant gets both",
    "conserve": "every expense must add exactly as much credit as it adds debt",
    "order": "accumulate into a fresh dict each call and never mutate the input",
    "settle": "keep matching the largest creditor with the largest debtor, transferring the "
              "SMALLER of the two magnitudes, until every balance is within a cent of zero",
    "minimal": "each transfer should fully settle at least one of the two people involved, so "
               "one person leaves the pool per transfer",
    "timeout": "the module does not finish; remove any loop that can fail to terminate",
    "crash": "the gate could not run the module",
}
SUMMARY = {
    "import": "the module does not import",
    "api": "the module does not expose the required functions",
    "balances": "balances are wrong",
    "conserve": "money is not conserved",
    "order": "balances depend on expense order",
    "settle": "the settlement does not settle",
    "minimal": "too many transfers",
    "timeout": "the module does not finish",
    "crash": "the gate could not run the module",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="split_check")
    ap.add_argument("module", help="the generated backend/split.py")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=int, default=PROBE_TIMEOUT_S)
    ns = ap.parse_args(argv)

    if ns.worker:
        return worker(ns.module)

    try:
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), ns.module, "--worker"],
            capture_output=True, text=True, timeout=ns.timeout, shell=False,
        )
        line = (child.stdout or "").strip().splitlines()
        report = json.loads(line[-1]) if line else {
            "stage": "crash", "ok": False,
            "detail": (child.stderr or "no output").strip()[-500:],
        }
    except subprocess.TimeoutExpired:
        report = {"stage": "timeout", "ok": False,
                  "detail": f"no result within {ns.timeout}s"}
    except (ValueError, OSError) as e:
        report = {"stage": "crash", "ok": False, "detail": str(e)}

    if report.get("ok"):
        out = {"findings": [], "verdict": "pass",
               "reason": f"settlement correct on {report.get('cases', '?')} seeded scenarios: "
                         f"balances match a reference, money is conserved, order does not "
                         f"matter, every settlement zeroes out, and none uses more than n-1 "
                         f"transfers"}
    else:
        stage = report.get("stage", "crash")
        evidence = str(report.get("detail", ""))[:600]
        if report.get("case") is not None:
            # The counterexample IS the value of this gate — heal appends
            # findings verbatim to the next prompt, so it has to be runnable.
            evidence += "\n\nfailing input: " + json.dumps(report["case"])
        out = {
            "findings": [{
                "severity": "blocker", "category": stage, "file": ns.module, "line": None,
                "claim": SUMMARY.get(stage, stage),
                "evidence": evidence,
                "fix_hint": FIX.get(stage, "fix the module"),
            }],
            "verdict": "block",
            "reason": SUMMARY.get(stage, stage),
        }
    sys.stdout.flush()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
