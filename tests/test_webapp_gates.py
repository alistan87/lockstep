"""The API and frontend gates for `webapp-local`, each against a working
implementation and against the ways a small model actually breaks it.

Same rule as `test_split_check.py`: a gate is a program you are asking to
refuse work, so it gets run against a known-good and a known-bad input before
a flow ever depends on it. Writing these caught one bad TEST — a "crashing"
server whose `raise` sat after `serve_forever()`, which of course never runs —
and the gate was right to pass it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lockstep.contracts import Verdict

ROOT = Path(__file__).resolve().parents[1]
API_GATE = ROOT / "contrib" / "demo" / "api_check.py"
UI_GATE = ROOT / "contrib" / "demo" / "ui_check.py"

SPLIT_PY = '''
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

SERVER_PY = '''
import json, sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from split import balances, settle

EXPENSES = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self.path == "/expenses":
            return self._send(200, {"expenses": EXPENSES})
        if self.path == "/balances":
            return self._send(200, {"balances": balances(EXPENSES)})
        if self.path == "/settlement":
            return self._send(200, {"settlement": settle(balances(EXPENSES))})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/expenses":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        EXPENSES.append(json.loads(self.rfile.read(n) or b"{}"))
        return self._send(201, {"ok": True, "count": len(EXPENSES)})


HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''

SPLIT_JS = """
export function formatAmount(n) { return Number(n).toFixed(2); }
export function summarise(balances) {
  return Object.keys(balances).sort().map((name) => {
    const v = balances[name];
    const text = Math.abs(v) < 0.005 ? `${name} is settled`
      : v > 0 ? `${name} is owed ${formatAmount(v)}`
      : `${name} owes ${formatAmount(-v)}`;
    return { name, text };
  });
}
export function renderSettlement(transfers) {
  return transfers.map((t) => `${t.from} pays ${t.to} ${formatAmount(t.amount)}`);
}
"""

INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>split</title></head>
<body><form id="expense-form"></form><div id="balances"></div><div id="settlement"></div>
<script type="module" src="./app.js"></script></body></html>
"""


def gate(*args, timeout=180) -> dict:
    proc = subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, f"a gate exits 0; got {proc.returncode}\n{proc.stderr[-600:]}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    Verdict.model_validate(data)
    return data


@pytest.fixture()
def app(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend" / "split.py").write_text(textwrap.dedent(SPLIT_PY), encoding="utf-8")
    (tmp_path / "backend" / "server.py").write_text(textwrap.dedent(SERVER_PY), encoding="utf-8")
    (tmp_path / "frontend" / "split.js").write_text(SPLIT_JS, encoding="utf-8")
    (tmp_path / "frontend" / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------- api

def test_a_working_server_passes(app):
    v = gate(API_GATE, app / "backend" / "server.py")
    assert v["verdict"] == "pass", v["findings"]


@pytest.mark.parametrize("name,edit,category", [
    ("unknown paths answer 200",
     ('return self._send(404, {"error": "not found"})', "return self._send(200, {})"), "notfound"),
    ("POST omits the count",
     ('{"ok": True, "count": len(EXPENSES)}', '{"ok": True}'), "post"),
    ("balances computed wrongly",
     ('{"balances": balances(EXPENSES)}', '{"balances": {}}'), "balances"),
])
def test_a_broken_server_blocks(app, name, edit, category):
    p = app / "backend" / "server.py"
    old, new = edit
    src = p.read_text(encoding="utf-8")
    assert old in src, name
    p.write_text(src.replace(old, new), encoding="utf-8")
    v = gate(API_GATE, p)
    assert v["verdict"] == "block", name
    assert v["findings"][0]["category"] == category, (name, v["findings"][0])


def test_a_server_that_ignores_the_given_port_does_not_pass(app):
    """The gate picks the port, because a hard-coded 8000 collides with whatever
    else is on the machine and would turn a correct server into a red gate — and
    a server that ignores the argument must not slip through either."""
    p = app / "backend" / "server.py"
    p.write_text(p.read_text(encoding="utf-8").replace("int(sys.argv[1])", "8123"),
                 encoding="utf-8")
    v = gate(API_GATE, p, "--boot-timeout", "6")
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "boot"


def test_a_server_that_dies_at_import_is_reported_with_its_traceback(app):
    p = app / "backend" / "server.py"
    p.write_text('raise RuntimeError("boom")\n' + p.read_text(encoding="utf-8"), encoding="utf-8")
    v = gate(API_GATE, p, "--boot-timeout", "8")
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "crash"
    assert "RuntimeError" in v["findings"][0]["evidence"]


def test_a_missing_server_blocks_rather_than_crashing_the_gate(app):
    v = gate(API_GATE, app / "backend" / "nope.py")
    assert v["verdict"] == "block"


# ---------------------------------------------------------------------- ui

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_a_working_frontend_passes(app):
    v = gate(UI_GATE, app / "frontend" / "split.js", app / "frontend" / "index.html")
    assert v["verdict"] == "pass", v["findings"]


@needs_node
@pytest.mark.parametrize("name,edit,category", [
    ("no two-decimal formatting", ("Number(n).toFixed(2)", "String(Number(n))"), "format"),
    ("rows not sorted", (".sort()", ""), "summarise"),
    ("a zero balance is not called settled", ("Math.abs(v) < 0.005", "false"), "summarise"),
    ("a missing export", ("export function renderSettlement", "function renderSettlement"), "api"),
])
def test_a_broken_module_blocks(app, name, edit, category):
    p = app / "frontend" / "split.js"
    old, new = edit
    src = p.read_text(encoding="utf-8")
    assert old in src, name
    p.write_text(src.replace(old, new), encoding="utf-8")
    v = gate(UI_GATE, p, app / "frontend" / "index.html")
    assert v["verdict"] == "block", name
    assert v["findings"][0]["category"] == category, (name, v["findings"][0])


@needs_node
def test_a_page_that_loads_from_a_cdn_blocks(app):
    """Not style. This flow runs on local models with no network, so a page that
    quietly depends on a CDN works on the machine that built it and nowhere
    else — the failure that looks like success until it matters."""
    p = app / "frontend" / "index.html"
    p.write_text(p.read_text(encoding="utf-8").replace(
        'src="./app.js"', 'src="https://cdn.example.com/app.js"'), encoding="utf-8")
    v = gate(UI_GATE, app / "frontend" / "split.js", p)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "offline"


@needs_node
def test_a_page_missing_an_element_id_blocks(app):
    p = app / "frontend" / "index.html"
    p.write_text(p.read_text(encoding="utf-8").replace('id="balances"', 'id="totals"'),
                 encoding="utf-8")
    v = gate(UI_GATE, app / "frontend" / "split.js", p)
    assert v["verdict"] == "block" and v["findings"][0]["category"] == "ids"
    assert "balances" in v["findings"][0]["evidence"]
