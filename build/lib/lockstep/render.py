"""Mermaid renderer (SPEC §3): `lockstep render <flow.tg.json>` to stdout."""

from __future__ import annotations

from .taskgraph import TaskGraph


def _shape(role: str, label: str) -> tuple[str, str]:
    if role == "gate":
        return "{{", "}}"
    if role == "approval":
        return "[/", "/]"
    if role == "map":
        return "[[", "]]"
    return "[", "]"


def render_mermaid(tg: TaskGraph) -> str:
    lines = ["flowchart TD"]
    for n in tg.nodes:
        label = f"{n.id}<br/>{n.role}:{n.kind}" if n.role != "approval" else f"{n.id}<br/>approval"
        left, right = _shape(n.role, label)
        suffix = ""
        if n.when:
            suffix = " (when)"
        lines.append(f'    {n.id}{left}"{label}{suffix}"{right}')
    for n in tg.nodes:
        for dep in n.depends_on:
            arrow = "-.->" if n.when else "-->"
            lines.append(f"    {dep} {arrow} {n.id}")
    for n in tg.nodes:
        if n.role == "gate":
            lines.append(f"    style {n.id} stroke-width:3px")
    return "\n".join(lines)
