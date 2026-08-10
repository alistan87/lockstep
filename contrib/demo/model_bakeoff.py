#!/usr/bin/env python
"""model_bakeoff.py — which local models can actually write the settlement module?

    python contrib/demo/model_bakeoff.py qwen3.6:35b gemma4:26b

Takes the REAL `write-logic` prompt out of flows/demo/webapp-local.tg.json,
sends it to each named ollama model through pi (tool-less, exactly as the flow
does), and puts the answer through the REAL gate. Prints verdict, category and
wall time per model.

This exists because "the local models cannot do it" was an inference from two
data points, and the machine has ten models on it. `lockstep doctor` answers
"does the stanza run"; this answers "is this model good enough for THIS node",
which is the question `spec.executor` exists to let you act on.

It also applies the code extraction that `save_result.py --strip-fence` does
not yet do, so a model is judged on its code rather than on whether it wrapped
it in a fence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FLOW = REPO / "flows" / "demo" / "webapp-local.tg.json"
GATE = REPO / "contrib" / "demo" / "split_check.py"

FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)(?:\n\s*```|\Z)", re.DOTALL)


def extract_code(text: str) -> str:
    """The boundary normalisation `--strip-fence` is missing.

    Two failures cost real heal rounds on 2026-08-09: a model echoed lockstep's
    own `begin data` fence marker into its answer, and another emitted prose
    around a ```python block, which `--strip-fence` leaves alone because it
    only unwraps a fence around the ENTIRE result. Take the LARGEST fenced
    block when there is one; otherwise drop the driver's own markers.
    """
    blocks = FENCE.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    keep = [
        ln for ln in text.splitlines()
        if ln.strip() not in ("begin data", "end data")
        and not ln.strip().startswith("```")
    ]
    return "\n".join(keep).strip() + "\n"


def write_logic_prompt() -> str:
    flow = json.loads(FLOW.read_text(encoding="utf-8"))
    node = next(n for n in flow["nodes"] if n["id"] == "write-logic")
    return node["spec"]["task"]


def run_model(model: str, prompt: str, out_dir: Path, timeout: int) -> tuple[str, float, str]:
    """(verdict-or-error, seconds, category)."""
    started = time.time()
    try:
        proc = subprocess.run(
            ["pi.cmd", "-p", "--no-session", "--no-tools",
             "--provider", "ollama", "--model", model],
            input=prompt, capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.time() - started, f"no answer in {timeout}s"
    elapsed = time.time() - started
    raw = proc.stdout or ""
    if not raw.strip():
        return "EMPTY", elapsed, (proc.stderr or "")[-120:].strip()

    mod = out_dir / f"{model.replace(':', '_').replace('.', '_')}.py"
    mod.write_text(extract_code(raw), encoding="utf-8")
    gate = subprocess.run([sys.executable, str(GATE), str(mod)],
                          capture_output=True, text=True, timeout=300)
    try:
        v = json.loads(gate.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "GATE-ERROR", elapsed, (gate.stderr or "")[-120:]
    if v["verdict"] == "pass":
        return "PASS", elapsed, ""
    f = v["findings"][0]
    return "block", elapsed, f"{f['category']}: {f['evidence'].splitlines()[0][:70]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="+")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default=None, help="where to keep the generated modules")
    ns = ap.parse_args(argv)

    out_dir = Path(ns.out) if ns.out else REPO / "runs" / "bakeoff"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = write_logic_prompt()
    print(f"prompt: {len(prompt)} chars, from write-logic\n")

    rows = []
    for m in ns.models:
        print(f"  {m} ...", flush=True)
        verdict, secs, note = run_model(m, prompt, out_dir, ns.timeout)
        rows.append((m, verdict, secs, note))
        print(f"    {verdict}  {secs:.0f}s  {note}", flush=True)

    print(f"\n{'model':<24} {'verdict':<10} {'secs':>6}  first failure")
    print("-" * 88)
    for m, verdict, secs, note in rows:
        print(f"{m:<24} {verdict:<10} {secs:>6.0f}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
