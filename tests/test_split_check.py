"""The settlement gate, against a correct module and against five wrong ones.

FLOW-AUTHORING's rule is to run a gate against a known-bad and a known-good
input before the flow ever sees it. These are those runs, kept — because the
gate is the entire healing signal for `webapp-local`, and a gate that cannot
fail would turn the run into an expensive way of accepting whatever the model
produced first.

Each wrong variant is a way a small model actually gets this wrong, not an
invented mutation: dividing by the wrong denominator, a greedy matcher that
stops after one transfer, paying everyone off pairwise, answering with prose
inside a markdown fence, and code that does not terminate.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "contrib" / "demo" / "split_check.py"

GOOD = '''
def balances(expenses):
    net = {}
    for e in expenses:
        share = e["amount"] / len(e["participants"])
        net[e["payer"]] = net.get(e["payer"], 0.0) + e["amount"]
        for p in e["participants"]:
            net[p] = net.get(p, 0.0) - share
    return net


def settle(bal):
    cred = sorted([[p, v] for p, v in bal.items() if v > 0.005], key=lambda x: -x[1])
    debt = sorted([[p, -v] for p, v in bal.items() if v < -0.005], key=lambda x: -x[1])
    out, i, j = [], 0, 0
    while i < len(cred) and j < len(debt):
        amt = min(cred[i][1], debt[j][1])
        out.append({"from": debt[j][0], "to": cred[i][0], "amount": round(amt, 2)})
        cred[i][1] -= amt
        debt[j][1] -= amt
        if cred[i][1] <= 0.005:
            i += 1
        if debt[j][1] <= 0.005:
            j += 1
    return out
'''


def run_gate(tmp_path, source: str, *, timeout: int = 30) -> dict:
    mod = tmp_path / "split.py"
    mod.write_text(textwrap.dedent(source), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(GATE), str(mod), "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"a gate exits 0; got {proc.returncode}\n{proc.stderr[-800:]}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    from lockstep.contracts import Verdict

    Verdict.model_validate(data)
    return data


def test_a_correct_module_passes(tmp_path):
    v = run_gate(tmp_path, GOOD)
    assert v["verdict"] == "pass", v["findings"]
    # The pass reason must enumerate what was actually established: reading it
    # back is how you notice a property you forgot to check.
    for claim in ("conserved", "order", "zeroes out", "transfers"):
        assert claim in v["reason"]


def test_the_pass_is_not_vacuous(tmp_path):
    """A module that returns nothing must not sail through on empty results."""
    v = run_gate(tmp_path, "def balances(e):\n    return {}\n\ndef settle(b):\n    return []\n")
    assert v["verdict"] == "block"


@pytest.mark.parametrize("name,source,category", [
    ("wrong denominator",
     GOOD.replace('e["amount"] / len(e["participants"])',
                  'e["amount"] / max(len(e["participants"]) - 1, 1)'),
     "balances"),
    ("pairwise half-payments",
     GOOD.split("def settle")[0] + '''
def settle(bal):
    out = []
    for p, v in bal.items():
        if v < -0.005:
            for q, w in bal.items():
                if w > 0.005:
                    out.append({"from": p, "to": q, "amount": round(min(-v, w) / 2, 2)})
    return out
''',
     "settle"),
    # A markdown fence IS a SyntaxError, and `syntax` is the more useful
    # diagnosis: it quotes the offending line instead of handing back a
    # traceback that points into this gate.
    ("prose in a markdown fence",
     "Here is the module:\n\n```python\ndef balances(x):\n    return {}\n```\n",
     "syntax"),
    ("no settle function",
     GOOD.split("def settle")[0],
     "api"),
])
def test_wrong_modules_block_with_the_right_category(tmp_path, name, source, category):
    v = run_gate(tmp_path, source)
    assert v["verdict"] == "block", name
    assert v["findings"][0]["category"] == category, (name, v["findings"][0])
    assert v["findings"][0]["fix_hint"], name


def test_a_blocking_finding_carries_a_runnable_counterexample(tmp_path):
    """`fix_hint` is the next prompt and `evidence` travels with it verbatim, so
    the failing input has to be something the model can actually re-run."""
    v = run_gate(tmp_path, GOOD.replace('e["amount"] / len(e["participants"])',
                                        'e["amount"] / max(len(e["participants"]) - 1, 1)'))
    evidence = v["findings"][0]["evidence"]
    assert "failing input: " in evidence
    case = json.loads(evidence.split("failing input: ", 1)[1])
    assert isinstance(case, list) and case and {"payer", "amount", "participants"} <= set(case[0])


def test_a_module_that_never_finishes_becomes_a_finding_not_a_hang(tmp_path):
    """Model-written loops hang often. If the gate hangs instead, the driver
    kills it at timeout_s, retries once, gets no verdict either time, and the
    run fails closed with "no valid verdict emitted" — correct and useless."""
    v = run_gate(tmp_path, GOOD + "\n\nwhile True:\n    pass\n", timeout=5)
    assert v["verdict"] == "block"
    assert v["findings"][0]["category"] == "timeout"


def test_the_gate_is_deterministic(tmp_path):
    """Seeded on purpose: a gate whose inputs vary per run gives different
    findings for identical code, and every downstream input hash moves with it."""
    a = run_gate(tmp_path, GOOD.replace("0.005", "0.5"))
    b = run_gate(tmp_path, GOOD.replace("0.005", "0.5"))
    assert a == b


# ------------------------------------------- the boundary, not the correctness

def test_extract_code_recovers_the_shapes_that_cost_heal_rounds(tmp_path):
    """Two of one webapp-local run's four heal rounds went on FORMATTING: a 24B
    wrapped its module in prose plus a ```python block, and then echoed
    lockstep's own `begin data` fence marker into its answer. Both produced a
    SyntaxError on line 1 — a round spent on nothing to do with the task.

    `--strip-fence` is right to leave those alone (it only unwraps a fence
    around the WHOLE result, and a partial unwrap is a corruption). This is the
    greedy sibling for nodes whose result is source code.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "save_result", Path(__file__).resolve().parents[1] / "contrib" / "save_result.py")
    sr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sr)

    bare = "def balances(e):\n    return {}\n"
    cases = {
        "prose around a fence": f"Here is the module:\n\n```python\n{bare}```\n\nHope that helps!",
        "echoed driver markers": f"begin data\n{bare}end data",
        "bare code untouched": bare,
        "unterminated fence": f"```python\n{bare}",
    }
    for name, raw in cases.items():
        out, note = sr.extract_code(raw)
        compile(out, "<candidate>", "exec")          # raises if we corrupted it
        assert "balances" in out, name
        assert note, f"{name}: the normalisation must say what it did"

    # Biggest block wins: a model that shows its work puts the answer in the
    # largest one, and taking the first would take the illustration.
    two = "```\nshort\n```\ntext\n```python\ndef balances(e):\n    return {}\n\n\ndef settle(b):\n    return []\n```"
    out, _ = sr.extract_code(two)
    assert "settle" in out and "short" not in out
