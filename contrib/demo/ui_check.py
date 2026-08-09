#!/usr/bin/env python
"""ui_check.py — is the frontend logic right, and does the page work offline? -> Verdict.

    python contrib/demo/ui_check.py frontend/split.js frontend/index.html

Two checks that between them cover what a browser would tell you, without one:

**The logic**, driven by Node's BUILT-IN runtime — no npm, no install step, no
lockfile, nothing to go stale. The pure module must export

    formatAmount(n)            -> "12.35"        (always 2 decimals)
    summarise(balances)        -> [{name, text}] (sorted by name)
    renderSettlement(list)     -> ["ben pays ana 4.00", ...]

Keeping the logic in a pure module — and the DOM wiring in a separate file that
is not gated — is the whole reason a frontend can be checked deterministically
at all. A model asked to put logic and DOM in one file produces something only
a browser can judge, and then the gate becomes a human.

**The page**, checked as text: the element ids the wiring needs must exist, and
there must be NO external URL. That second one is not style. This flow runs on
local models with no network, and a page that quietly depends on a CDN works on
the machine that built it and nowhere else — the exact failure that looks like
success right up until it matters.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NODE_TIMEOUT_S = 60
REQUIRED_IDS = ("expense-form", "balances", "settlement")
EXTERNAL_URL = re.compile(r"""(?:src|href)\s*=\s*["']\s*(https?:)?//""", re.I)

# Node driver: imports the module and checks it. Plain assertions and one JSON
# line, rather than `node --test`, so the report shape matches the Python gates
# and a failure names the case instead of printing a TAP stream.
DRIVER = r"""
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const fail = (stage, detail) => {
  console.log(JSON.stringify({ stage, ok: false, detail: String(detail) }));
  process.exit(0);
};
for (const name of ["formatAmount", "summarise", "renderSettlement"]) {
  if (typeof mod[name] !== "function") fail("api", `no exported function ${name}`);
}
try {
  for (const [input, want] of [[12.345, "12.35"], [4, "4.00"], [0, "0.00"], [-3.5, "-3.50"]]) {
    const got = mod.formatAmount(input);
    if (got !== want) fail("format", `formatAmount(${input}) = ${JSON.stringify(got)}, want "${want}"`);
  }

  const rows = mod.summarise({ ben: -4, ana: 20, cy: 0 });
  if (!Array.isArray(rows)) fail("summarise", `returned ${typeof rows}, expected an array`);
  if (rows.length !== 3) fail("summarise", `returned ${rows.length} rows for 3 people`);
  const names = rows.map((r) => r && r.name);
  if (names.join(",") !== "ana,ben,cy") fail("summarise", `rows are ${names.join(",")}, expected them sorted by name`);
  const text = (n) => (rows.find((r) => r.name === n) || {}).text || "";
  if (!/20\.00/.test(text("ana")) || !/owed/i.test(text("ana")))
    fail("summarise", `ana's text is ${JSON.stringify(text("ana"))}; it should say she is owed 20.00`);
  if (!/4\.00/.test(text("ben")) || !/owes/i.test(text("ben")))
    fail("summarise", `ben's text is ${JSON.stringify(text("ben"))}; it should say he owes 4.00`);
  if (!/settled/i.test(text("cy")))
    fail("summarise", `cy has a zero balance; his text is ${JSON.stringify(text("cy"))} and should say settled`);

  const lines = mod.renderSettlement([{ from: "ben", to: "ana", amount: 4 }]);
  if (!Array.isArray(lines) || lines.length !== 1)
    fail("settlement", `renderSettlement returned ${JSON.stringify(lines)}`);
  if (!/ben/.test(lines[0]) || !/ana/.test(lines[0]) || !/4\.00/.test(lines[0]))
    fail("settlement", `line is ${JSON.stringify(lines[0])}; it should name both people and 4.00`);
  if (mod.renderSettlement([]).length !== 0)
    fail("settlement", "an empty settlement should render no lines");
} catch (e) {
  fail("crash", (e && e.stack) || e);
}
console.log(JSON.stringify({ stage: "all", ok: true }));
"""

FIX = {
    "node": "install Node 18+ so the frontend logic can be checked without a browser",
    "missing": "write the file the flow expects at that path",
    "api": "export exactly formatAmount, summarise and renderSettlement from the module",
    "format": "formatAmount must always give two decimals — toFixed(2)",
    "summarise": "return one row per person sorted by name, with text naming the amount to "
                 "two decimals and saying owed / owes / settled",
    "settlement": "return one line per transfer naming both people and the amount, and an "
                  "empty array for an empty settlement",
    "crash": "the module throws when imported or called",
    "ids": f"give the page elements with ids: {', '.join(REQUIRED_IDS)}",
    "offline": "remove every external src/href — this runs with no network, so the page must "
               "be self-contained",
}
SUMMARY = dict(FIX)
SUMMARY.update({
    "node": "node is not installed", "missing": "a required file is missing",
    "api": "the module does not export what the page needs",
    "format": "amounts are not formatted correctly",
    "summarise": "the balance summary is wrong", "settlement": "the settlement lines are wrong",
    "crash": "the module throws", "ids": "the page is missing required element ids",
    "offline": "the page loads something from the network",
})


def check_page(html_path: Path) -> tuple[str, str] | None:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    missing = [i for i in REQUIRED_IDS if f'id="{i}"' not in text and f"id='{i}'" not in text]
    if missing:
        return "ids", f"no element with id: {', '.join(missing)}"
    m = EXTERNAL_URL.search(text)
    if m:
        line = text[: m.start()].count("\n") + 1
        return "offline", f"external resource referenced at line {line}: {m.group(0).strip()!r}"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ui_check")
    ap.add_argument("module", help="the generated frontend/split.js")
    ap.add_argument("page", help="the generated frontend/index.html")
    ap.add_argument("--timeout", type=int, default=NODE_TIMEOUT_S)
    ns = ap.parse_args(argv)

    stage = detail = None
    for label, p in (("module", Path(ns.module)), ("page", Path(ns.page))):
        if not p.is_file():
            stage, detail = "missing", f"{p} does not exist"
            break

    if stage is None and shutil.which("node") is None:
        stage, detail = "node", "node is not on PATH"

    if stage is None:
        found = check_page(Path(ns.page))
        if found:
            stage, detail = found

    if stage is None:
        with tempfile.TemporaryDirectory() as td:
            driver = Path(td) / "drive.mjs"
            driver.write_text(DRIVER, encoding="utf-8")
            try:
                child = subprocess.run(
                    ["node", str(driver), str(Path(ns.module).resolve())],
                    capture_output=True, text=True, timeout=ns.timeout,
                )
                lines = (child.stdout or "").strip().splitlines()
                report = json.loads(lines[-1]) if lines else {
                    "stage": "crash", "ok": False,
                    "detail": (child.stderr or "no output").strip()[-600:]}
            except subprocess.TimeoutExpired:
                report = {"stage": "crash", "ok": False,
                          "detail": f"the module did not finish within {ns.timeout}s"}
            except ValueError as e:
                report = {"stage": "crash", "ok": False, "detail": str(e)}
            if not report.get("ok"):
                stage, detail = report.get("stage", "crash"), report.get("detail", "")

    if stage is None:
        out = {"findings": [], "verdict": "pass",
               "reason": "the module exports the three functions and formats amounts, "
                         "summaries and settlement lines correctly, and the page carries the "
                         "required ids with no external resources"}
    else:
        out = {"findings": [{
            "severity": "blocker", "category": stage,
            "file": ns.page if stage in ("ids", "offline") else ns.module, "line": None,
            "claim": SUMMARY.get(stage, stage), "evidence": str(detail)[:800],
            "fix_hint": FIX.get(stage, "fix the frontend"),
        }], "verdict": "block", "reason": SUMMARY.get(stage, stage)}

    sys.stdout.flush()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
