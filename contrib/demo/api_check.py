#!/usr/bin/env python
"""api_check.py — does the generated server honour the API contract? -> Verdict.

    python contrib/demo/api_check.py backend/server.py

The integration half of `flows/demo/webapp-local.tg.json`. `split_check.py`
tests the settlement logic as pure functions; this starts the real server in a
child process, drives it over HTTP with the standard library, and kills it
again. Nothing is installed and nothing is imported from the model's code — if
the server only works when called from inside its own process, that is a defect
this gate exists to find.

The contract, which the node's prompt states verbatim:

    POST /expenses    {"payer","amount","participants"} -> 201 {"ok":true,"count":n}
    GET  /expenses                                      -> 200 {"expenses":[...]}
    GET  /balances                                      -> 200 {"balances":{name:net}}
    GET  /settlement                                    -> 200 {"settlement":[{from,to,amount}]}
    GET  /health                                        -> 200 {"ok":true}
    anything else                                       -> 404

Three things it is careful about, each learned the expensive way:

  - **The port is chosen here**, not by the model, and passed as argv[1]. A
    hard-coded 8000 collides with whatever else is on the machine and turns a
    correct implementation into a red gate.
  - **Readiness is polled, not slept for.** A fixed sleep is either flaky or
    slow, and on a heal loop you pay for it every round.
  - **The child is killed in a `finally`, whatever happens.** A gate that
    leaves a server holding a port makes the NEXT round fail for a reason that
    has nothing to do with the code under test.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BOOT_TIMEOUT_S = 20
CALL_TIMEOUT_S = 10


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port: int, method: str, path: str, body: dict | None = None):
    """(status, parsed-json-or-raw-text). Never raises for an HTTP status."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_S) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


CASES = [
    {"payer": "ana", "amount": 30.0, "participants": ["ana", "ben", "cy"]},
    {"payer": "ben", "amount": 12.0, "participants": ["ben", "cy"]},
]


def probe(port: int) -> tuple[str, str] | None:
    """(stage, detail) on the first failure, or None if everything holds."""
    st, body = call(port, "GET", "/health")
    if st != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        return "health", f"GET /health returned {st} {body!r}, expected 200 {{'ok': true}}"

    for i, case in enumerate(CASES, 1):
        st, body = call(port, "POST", "/expenses", case)
        if st != 201:
            return "post", f"POST /expenses returned {st}, expected 201 (body: {body!r})"
        if not isinstance(body, dict) or body.get("count") != i:
            return "post", f"POST /expenses returned {body!r}; count should be {i}"

    st, body = call(port, "GET", "/expenses")
    if st != 200 or not isinstance(body, dict) or len(body.get("expenses") or []) != len(CASES):
        return "list", f"GET /expenses returned {st} {body!r}, expected {len(CASES)} expenses"

    st, body = call(port, "GET", "/balances")
    if st != 200 or not isinstance(body, dict) or not isinstance(body.get("balances"), dict):
        return "balances", f"GET /balances returned {st} {body!r}, expected {{'balances': {{}}}}"
    bal = body["balances"]
    # ana paid 30 for 3, ben paid 12 for 2: ana +20, ben -4, cy -16.
    want = {"ana": 20.0, "ben": -4.0, "cy": -16.0}
    for name, v in want.items():
        if abs(float(bal.get(name, 0.0)) - v) > 0.01:
            return "balances", (f"{name} = {bal.get(name)!r}, expected {v} "
                                f"(from {json.dumps(CASES)})")

    st, body = call(port, "GET", "/settlement")
    if st != 200 or not isinstance(body, dict) or not isinstance(body.get("settlement"), list):
        return "settlement", f"GET /settlement returned {st} {body!r}"
    residual = dict(want)
    for t in body["settlement"]:
        try:
            residual[t["from"]] = residual.get(t["from"], 0.0) + float(t["amount"])
            residual[t["to"]] = residual.get(t["to"], 0.0) - float(t["amount"])
        except (TypeError, KeyError, ValueError):
            return "settlement", f"transfer is not {{from,to,amount}}: {t!r}"
    off = max(residual, key=lambda p: abs(residual[p]))
    if abs(residual[off]) > 0.01:
        return "settlement", (f"after the returned transfers {off} is still "
                              f"{round(residual[off], 2)} from settled")

    st, _ = call(port, "GET", "/no-such-route")
    if st != 404:
        return "notfound", f"GET /no-such-route returned {st}, expected 404"
    return None


FIX = {
    "boot": "the server must start and serve GET /health within seconds; bind the port given "
            "as argv[1] on 127.0.0.1 and do no other work at startup",
    "health": "add GET /health returning 200 with {\"ok\": true}",
    "post": "POST /expenses must return 201 and {\"ok\": true, \"count\": <total so far>}",
    "list": "GET /expenses must return {\"expenses\": [...]} with every expense posted so far",
    "balances": "GET /balances must return {\"balances\": {...}} computed by split.balances",
    "settlement": "GET /settlement must return {\"settlement\": [...]} from split.settle",
    "notfound": "unknown paths must return 404, not 200 or a traceback",
    "crash": "the server exited or could not be started",
}
SUMMARY = {k: k for k in FIX}
SUMMARY.update({
    "boot": "the server does not come up", "crash": "the server crashed",
    "health": "no working /health", "post": "POST /expenses is wrong",
    "list": "GET /expenses is wrong", "balances": "GET /balances is wrong",
    "settlement": "GET /settlement is wrong", "notfound": "unknown paths are not 404",
})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="api_check")
    ap.add_argument("server", help="the generated backend/server.py")
    ap.add_argument("--boot-timeout", type=int, default=BOOT_TIMEOUT_S)
    ns = ap.parse_args(argv)

    path = Path(ns.server)
    stage = detail = None
    proc = None
    if not path.is_file():
        stage, detail = "crash", f"{ns.server} does not exist"
    else:
        port = free_port()
        try:
            proc = subprocess.Popen(
                [sys.executable, str(path), str(port)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                cwd=str(path.parent.parent if path.parent.name == "backend" else Path.cwd()),
            )
            deadline = time.time() + ns.boot_timeout
            up = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    err = (proc.stderr.read() or "")[-600:]
                    stage, detail = "crash", f"the server exited immediately:\n{err.strip()}"
                    break
                try:
                    st, _ = call(port, "GET", "/health")
                    if st:
                        up = True
                        break
                except (urllib.error.URLError, OSError, TimeoutError):
                    time.sleep(0.25)
            if stage is None and not up:
                stage, detail = "boot", f"nothing answered on port {port} within {ns.boot_timeout}s"
            if stage is None:
                found = probe(port)
                if found:
                    stage, detail = found
        except OSError as e:
            stage, detail = "crash", f"could not start it: {e}"
        finally:
            # Always. A gate that leaks a server makes the next heal round fail
            # for a reason unrelated to the code under test.
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass

    if stage is None:
        out = {"findings": [], "verdict": "pass",
               "reason": "the server booted, answered /health, accepted two expenses, and "
                         "returned correct balances, a settlement that zeroes them, and 404 "
                         "for an unknown path"}
    else:
        out = {"findings": [{
            "severity": "blocker", "category": stage, "file": ns.server, "line": None,
            "claim": SUMMARY.get(stage, stage), "evidence": str(detail)[:800],
            "fix_hint": FIX.get(stage, "fix the server"),
        }], "verdict": "block", "reason": SUMMARY.get(stage, stage)}

    sys.stdout.flush()
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
