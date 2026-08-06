#!/usr/bin/env python
"""compose_flow.py — splice a FRAGMENT flow into a host flow (B2).

    python contrib/compose_flow.py host.tg.json fragment.tg.json \
        --prefix clarify --after draft --feed report --out composed.tg.json

clarify-gate and evidence-approval ship as FRAGMENT flows meant to be copied
by hand; this makes them actual includes with no engine change and no new
format. The whole feature set, deliberately:

  - fragment node ids gain '<prefix>-'; fragment-internal depends_on,
    heal.targets, `over`, and {steps.X...} references are rewritten to match;
  - fragment ROOT nodes (no depends_on) gain depends_on [--after <host node>];
  - the host node named by --feed gains a dependency on the fragment's final
    node (omit --feed for a fragment that ends the flow);
  - fragment `final` flags are stripped unless --feed is omitted;
  - args are unioned; the same arg declared with DIFFERENT defaults is an
    error, not a guess. budget/concurrency/etc stay the host's;
    contracts_module must agree when both declare one.

The output must then pass `lockstep verify` — the composer does not
re-implement §6, and requests for templating features get "no": the taskgraph
format staying dumb is a feature this repo has already paid for.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _rewrite_refs(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = re.sub(r"\{steps\." + re.escape(old) + r"\.", "{steps." + new + ".", text)
    return text


def _rewrite_node(node: dict, mapping: dict[str, str]) -> dict:
    node = json.loads(json.dumps(node))  # deep copy
    node["id"] = mapping[node["id"]]
    node["depends_on"] = [mapping.get(d, d) for d in node.get("depends_on", [])]
    if not node["depends_on"]:
        del node["depends_on"]
    heal = node.get("heal")
    if heal and heal.get("targets"):
        heal["targets"] = [mapping.get(t, t) for t in heal["targets"]]
    if isinstance(node.get("over"), str):
        node["over"] = _rewrite_refs(node["over"], mapping)
    if isinstance(node.get("when"), str):
        node["when"] = _rewrite_refs(node["when"], mapping)
    spec = node.get("spec", {})
    if isinstance(spec.get("task"), str):
        spec["task"] = _rewrite_refs(spec["task"], mapping)
    if isinstance(spec.get("cmd"), list):
        # Two rewrites per argv element: {steps.X...} refs, and BARE node ids —
        # the gate library (--node/--from-node/--doc-node) and render_evidence
        # (--approval) address sibling nodes as plain argv strings, and a
        # fragment whose gate reads its own sibling must keep doing so after
        # the prefix. Exact-match only: a path or flag never equals a node id.
        spec["cmd"] = [
            mapping.get(part, _rewrite_refs(part, mapping)) if isinstance(part, str) else part
            for part in spec["cmd"]
        ]
    return node


def compose(host: dict, fragment: dict, prefix: str, after: str, feed: str | None) -> dict:
    host = json.loads(json.dumps(host))
    host_ids = {n["id"] for n in host.get("nodes", [])}
    if after not in host_ids:
        raise SystemExit(f"--after {after!r} is not a node of the host flow")
    if feed is not None and feed not in host_ids:
        raise SystemExit(f"--feed {feed!r} is not a node of the host flow")
    mapping = {n["id"]: f"{prefix}-{n['id']}" for n in fragment.get("nodes", [])}
    collision = set(mapping.values()) & host_ids
    if collision:
        raise SystemExit(f"prefixed fragment ids collide with host ids: {sorted(collision)}")

    frag_nodes = [_rewrite_node(n, mapping) for n in fragment.get("nodes", [])]
    frag_final = None
    for n in frag_nodes:
        if n.pop("final", False):
            frag_final = n["id"]
        if not n.get("depends_on"):
            n["depends_on"] = [after]
    if frag_final is None:
        frag_final = frag_nodes[-1]["id"]  # the format's own default-final rule
    if feed is not None:
        for n in host["nodes"]:
            if n["id"] == feed and frag_final not in n.get("depends_on", []):
                n.setdefault("depends_on", []).append(frag_final)
    else:
        for n in host["nodes"]:
            n.pop("final", None)
        for n in frag_nodes:
            if n["id"] == frag_final:
                n["final"] = True

    host_args = dict(host.get("args", {}))
    for name, default in (fragment.get("args") or {}).items():
        if name in host_args and host_args[name] != default:
            raise SystemExit(
                f"arg {name!r} declared with different defaults "
                f"(host {host_args[name]!r}, fragment {default!r}) — resolve by hand"
            )
        host_args[name] = default
    if host_args:
        host["args"] = host_args
    host_cm = host.get("contracts_module")
    frag_cm = fragment.get("contracts_module")
    if frag_cm and host_cm and frag_cm != host_cm:
        raise SystemExit(
            f"contracts_module conflict (host {host_cm!r}, fragment {frag_cm!r})"
        )
    if frag_cm and not host_cm:
        host["contracts_module"] = frag_cm
    host["nodes"] = list(host["nodes"]) + frag_nodes
    host["description"] = (
        host.get("description", "") + f" [composed: {fragment.get('name', 'fragment')} "
        f"as '{prefix}-*' after '{after}'"
        + (f", feeding '{feed}'" if feed else ", ending the flow") + "]"
    ).strip()
    return host


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("fragment")
    ap.add_argument("--prefix", required=True, help="id prefix for the fragment's nodes")
    ap.add_argument("--after", required=True, help="host node the fragment roots depend on")
    ap.add_argument("--feed", default=None,
                    help="host node that gains a dep on the fragment's final node; "
                         "omit if the fragment ends the flow")
    ap.add_argument("--out", default="-", help='output path; "-" prints to stdout')
    ns = ap.parse_args(argv)
    host = json.loads(Path(ns.host).read_text(encoding="utf-8"))
    fragment = json.loads(Path(ns.fragment).read_text(encoding="utf-8"))
    composed = compose(host, fragment, ns.prefix, ns.after, ns.feed)
    text = json.dumps(composed, indent=2, ensure_ascii=False) + "\n"
    if ns.out == "-":
        print(text, end="")
    else:
        Path(ns.out).write_text(text, encoding="utf-8")
        print(f"wrote {ns.out} — now: lockstep verify {ns.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
