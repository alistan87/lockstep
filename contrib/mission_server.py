#!/usr/bin/env python
"""mission_server.py — MISSION as a read-only local page (proposal T3.2).

    python contrib/mission_server.py                    # http://127.0.0.1:8787
    python contrib/mission_server.py --port 9000 runs/<run>

Renders the same functions as the TUI (`mission_view.py`), so the page and the
pane cannot disagree. It removes the WezTerm dependency for OBSERVATION and
works from a phone on the same machine.

THE APPROVAL NEVER MOVES. There is no form on this page, no POST handler, and no
route that writes anything — not as policy, but as the absence of the code. A
browser button is exactly the forgeable channel this design exists to prevent:
the whole guarantee is that a decision happens at a keyboard, in a terminal, at
a prompt nothing can type into. The page says so in its own header, because a
surface that shows a decision without offering it has to explain why.

Bound to loopback by default. `--host` requires an explicit value and prints a
warning naming what is being exposed: `runs/` holds prompts, diffs and model
output, it is gitignored for that reason, and this server applies no
authentication whatsoever.
"""

from __future__ import annotations

import argparse
import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mission_view as mv  # noqa: E402

REFRESH_S = 5

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>MISSION - {name}</title>
<style>
  body {{ background:#111; color:#ddd; font:14px/1.5 ui-monospace,Consolas,monospace;
         margin:0; padding:1rem; }}
  h1 {{ font-size:1rem; color:#6cf; margin:0 0 .25rem; }}
  .note {{ color:#888; font-size:.8rem; margin:0 0 1rem; }}
  .decide {{ background:#3a2a00; color:#fc6; padding:.5rem .75rem; border-radius:4px;
             margin:0 0 1rem; }}
  pre {{ white-space:pre-wrap; margin:0 0 1rem; }}
  .spend {{ color:#dc4; }}
  .needs {{ color:#f66; font-weight:bold; }}
  hr {{ border:0; border-top:1px solid #333; margin:1rem 0; }}
</style>
<h1>MISSION &mdash; {name}</h1>
<p class="note">This page only reads files. It never changes the run,
and it updates by itself every {refresh} seconds.</p>
<p class="decide">Decisions are not made here. When something needs you, it happens
in the terminal &mdash; that is what makes it impossible for anything but a person
to answer.</p>
{needs}
<pre>{mission}</pre>
<pre class="spend">{spend}</pre>
{costs}
<hr>
<h1>ACTIVITY</h1>
<pre>{activity}</pre>
<hr>
<h1>what happened at each step</h1>
{details}
"""


def spend_lines(run_dir: Path, repo_root: Path, runs_root: Path) -> list[str]:
    try:
        import cost_report
        maps = cost_report.load_field_maps(None)
        run = cost_report.collect_run(Path(run_dir), maps)
        cap = cost_report._budget_cap(Path(run_dir))
        text, _ = cost_report.compact_block([run], [cap])
        lines = text.splitlines()
    except Exception as e:  # noqa: BLE001 - display-only, always
        return [f"(spend unavailable: {e})"]
    try:
        import session_spend
        lines += session_spend.session_lines(Path(repo_root), Path(runs_root))
    except Exception as e:  # noqa: BLE001
        lines += [f"(session spend unavailable: {e})"]
    return lines


def cost_details(run_dir: Path) -> str:
    """The cost panel, both modes, as collapsed sections — the page has no
    keyboard, so the TUI's `c` toggle becomes two <details> blocks."""
    out = []
    for mode, title in (("history", "costs — history (every attempt is counted)"),
                        ("head", "costs — head (kept attempts only)")):
        body = "\n".join(mv.cost_lines(run_dir, mode=mode))
        out.append(f"<details><summary>{html.escape(title)}</summary>"
                   f"<pre>{html.escape(body)}</pre></details>")
    return "\n".join(out)


def render_page(run_dir: Path | None, repo_root: Path, runs_root: Path) -> str:
    if run_dir is None:
        return PAGE.format(
            refresh=REFRESH_S, name="no run yet", needs="",
            mission="Tell the assistant what you would like to work on;\n"
                    "this page fills in by itself once something starts.",
            spend="", costs="", activity="", details="")

    state = mv.read_json(run_dir / "state.json")
    needs = ('<p class="needs">NEEDS YOU &mdash; read the terminal pane.</p>'
             if mv.needs_you(state) else "")

    # Read the sidecar ONCE. It was being re-read (state.json plus a recursive
    # flows/** glob) for every node on every request, twice over.
    labels = mv.load_labels(run_dir, repo_root)
    details = []
    for node_id in mv.visible_nodes(run_dir, repo_root):
        body = "\n".join(mv.node_detail(run_dir, node_id, repo_root))
        details.append(
            f"<details><summary>{html.escape(mv.label_for(labels, node_id))}"
            f"</summary><pre>{html.escape(body)}</pre></details>")

    return PAGE.format(
        refresh=REFRESH_S,
        name=html.escape(run_dir.name),
        needs=needs,
        mission=html.escape("\n".join(mv.mission_lines(run_dir, repo_root=repo_root))),
        spend=html.escape("\n".join(spend_lines(run_dir, repo_root, runs_root))),
        costs=cost_details(run_dir),
        activity=html.escape("\n".join(mv.activity_lines(run_dir, repo_root=repo_root))),
        details="\n".join(details) or "<p>(nothing to show yet)</p>",
    )


def make_handler(runs_root: Path, pinned: Path | None, repo_root: Path):
    class Handler(BaseHTTPRequestHandler):
        # GET only. There is deliberately no do_POST, do_PUT or do_DELETE: the
        # absence of the method IS the guarantee. BaseHTTPRequestHandler answers
        # anything else with 501, which is the correct answer.
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            run_dir = pinned or mv.newest_run(runs_root)
            try:
                body = render_page(run_dir, repo_root, runs_root).encode("utf-8")
            except Exception as e:  # noqa: BLE001 - a view never takes the run down
                body = f"<pre>view error: {html.escape(str(e))}</pre>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass  # a view that chatters into the console is a worse view

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="loopback by default; anything else exposes the run dir")
    ns = ap.parse_args(argv)

    if ns.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {ns.host}, not loopback.", file=sys.stderr)
        print("  This serves the contents of run directories - prompts, diffs and model",
              file=sys.stderr)
        print("  output - to anything that can reach this machine, with no authentication.",
              file=sys.stderr)
        print("  runs/ is gitignored precisely because it is sensitive.", file=sys.stderr)

    handler = make_handler(Path(ns.runs_root),
                           Path(ns.run_dir) if ns.run_dir else None,
                           Path(ns.repo_root))
    server = ThreadingHTTPServer((ns.host, ns.port), handler)
    print(f"MISSION (read-only) on http://{ns.host}:{ns.port}  - Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
