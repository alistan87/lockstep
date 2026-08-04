#!/usr/bin/env python
"""mission_view.py — the DE-tier view, as pure functions (proposal T3.1).

    from mission_view import mission_frame, activity_lines

Every function here is a projection of `state.json` + the run's own
`flow.tg.json` copy + `phases/<node>/*`. There is no model, no narration, and no
second source of truth: this is the same field mapping `cockpit.ps1` performs,
extracted so the TUI (`mission_tui.py`) and the read-only page
(`mission_server.py`) cannot disagree with each other.

WHY THIS IS PYTHON AND NOT MORE POWERSHELL. The view layer has never had a test.
`cockpit.ps1` remains the shipped default — correctness lives there, because it
ships to machines whose terminal nobody here controls — but a second, testable
implementation of the same mapping is how the mapping itself gets pinned.
`tests/test_mission_render.py` compares this module's glossary against the one
in `cockpit.ps1`, so the two cannot drift apart silently.

READER DISCIPLINE (L-B2, Python side). Every read here is open-read-close, never
held across a poll, and every failure returns None rather than raising. The
engine replaces `state.json` by atomic rename and rotates per-attempt files
underneath us; a view must never be the reason a run fails.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# The fixed glossary. Summary-free by construction: a field mapping, no model.
# This is the domain expert's trust anchor, so it must never acquire a narrated
# branch. Kept identical to $script:Glossary in cockpit.ps1, and a test enforces
# that.
GLOSSARY = {
    "pending": "waiting",
    "running": "running",
    "done": "done",
    "skipped": "not needed",
    "failed": "stopped with a problem",
    "blocked": "needs you",
}

WIDTH = 72
LABEL_WIDTH = 34


# --------------------------------------------------------------- reading

def read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_text(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def newest_run(runs_root: Path) -> Path | None:
    """-Follow semantics: track whichever run is newest.

    This is what lets a view exist BEFORE a run does and survive the gap between
    segments — the trust anchor must not blink out of existence exactly when
    somebody is looking at it for reassurance.
    """
    # The stat() has to be INSIDE the guard, not just the listing. A run dir can
    # vanish between the is_dir() check and the stat(), and this machine's AV
    # raises transient PermissionError on stats besides — so the window is not
    # theoretical. This module promises that every failure returns None rather
    # than raising, and mission_tui's loop has no other net: an escape here
    # kills the domain expert's view mid-watch.
    newest: tuple[float, Path] | None = None
    try:
        entries = list(Path(runs_root).iterdir())
    except OSError:
        return None
    for d in entries:
        try:
            if not d.is_dir() or not (d / "state.json").is_file():
                continue
            stamp = d.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, d)
    return newest[1] if newest else None


def load_labels(run_dir: Path, repo_root: Path | None = None) -> dict[str, str]:
    """T1.8 — human names for nodes, from a sidecar the VIEW owns.

    taskgraph `Node` is `extra="forbid"`, so there is nowhere in a flow file to
    put a name for a person. A sidecar keeps this mechanical (a file lookup, not
    a narrated branch) while leaving every flow written to date verifying
    unchanged.

    The run's own copy wins, so a label edited later cannot rewrite what a
    completed run was displayed as.
    """
    return _sidecar(run_dir, repo_root, "nodes")


def load_tiers(run_dir: Path, repo_root: Path | None = None) -> dict[str, str]:
    """Approval tiers from the same sidecar (T3.3).

    A SEPARATE function rather than a magic key inside the labels dict. The
    first cut smuggled these back as `labels["__tiers__"]`, which meant the one
    consumer that mattered — the evidence renderer — never read them, and a
    flow author following the documented sidecar shape got a silent no-op on
    exactly the approvals meant to be loud.
    """
    return _sidecar(run_dir, repo_root, "tiers")


def _sidecar(run_dir: Path, repo_root: Path | None, section: str) -> dict[str, str]:
    """One lookup order for every section of the sidecar.

    The run's own copy wins, so a label or tier edited later cannot rewrite what
    a completed run was displayed as.
    """
    out: dict[str, str] = {}
    candidates = [Path(run_dir) / "flow.labels.json"]

    state = read_json(Path(run_dir) / "state.json") or {}
    name = state.get("flow_name")
    if name and repo_root:
        candidates += sorted(Path(repo_root).glob(f"flows/**/{name}.labels.json"))

    for path in candidates:
        doc = read_json(path)
        if not isinstance(doc, dict):
            continue
        for k, v in (doc.get(section) or {}).items():
            out.setdefault(str(k), str(v))
    return out


def label_for(labels: dict[str, str], node_id: str) -> str:
    got = labels.get(node_id)
    return got if got else node_id


# --------------------------------------------------------------- headline

def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def steps_to_decision(state: dict, flow: dict | None) -> int | None:
    """T1.7 — how many steps until it is the human's turn.

    Defined narrowly enough to be checkable: the count of nodes the single
    awaiting approval transitively DEPENDS ON that are not yet done or skipped,
    plus the approval itself. A remaining-work count over the recorded graph.
    It is not a prediction and not an estimate of time.

    None when there is no approval, when there is more than one (the flow is
    unsegmented, so the number would be ambiguous), or with no flow copy.
    """
    if not flow:
        return None
    nodes = state.get("nodes") or {}
    approvals = [nid for nid, rec in nodes.items() if rec.get("role") == "approval"]
    if len(approvals) != 1:
        return None

    deps = {n["id"]: list(n.get("depends_on") or []) for n in (flow.get("nodes") or [])}
    seen: set[str] = set()
    stack = list(deps.get(approvals[0], []))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(deps.get(cur, []))

    remaining = sum(
        1 for nid in seen
        if nid in nodes and nodes[nid].get("status") not in ("done", "skipped")
    )
    if nodes[approvals[0]].get("status") != "done":
        remaining += 1
    return remaining


def headline(state: dict, flow: dict | None, now: datetime | None = None) -> str:
    """One line above the list. Every element is a count over state.json."""
    recs = list((state.get("nodes") or {}).values())
    total = len(recs)
    settled = sum(1 for r in recs if r.get("status") in ("done", "skipped"))
    running = [r for r in recs if r.get("status") == "running"]
    blocked = [r for r in recs if r.get("status") == "blocked"]
    failed = [r for r in recs if r.get("status") == "failed"]
    heals = sum(int(r.get("heal_round") or 0) for r in recs)

    parts = [f"step {min(settled + len(running), total)} of {total}"]
    if failed:
        parts.append("stopped with a problem")
    elif blocked:
        parts.append("needs you")
    elif running:
        parts.append("running")
    elif settled == total and total:
        parts.append("done")
    else:
        parts.append("waiting")

    began = _parse_ts(state.get("started_at"))
    if began:
        mins = int(((now or datetime.now(timezone.utc)) - began).total_seconds() // 60)
        parts.append(f"{mins // 60} h {mins % 60} m" if mins >= 90 else f"{mins} m")
    if heals:
        parts.append(f"{heals} rework round{'s' if heals != 1 else ''}")

    to_go = steps_to_decision(state, flow)
    if to_go is not None:
        if to_go <= 0:
            parts.append("your decision is recorded")
        elif to_go == 1:
            parts.append("your decision is next")
        else:
            parts.append(f"a decision is {to_go} steps away")
    return "  -  ".join(parts)


# --------------------------------------------------------------- MISSION

def heal_budgets(flow: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in ((flow or {}).get("nodes") or []):
        heal = n.get("heal") or {}
        rounds = int(heal.get("max_rounds") or 0)
        if rounds > 0:
            for t in (heal.get("targets") or []):
                out[t] = rounds
            out[n["id"]] = rounds
    return out


def node_word(node_id: str, rec: dict, budgets: dict[str, int]) -> str:
    """The status word for one node, with the rework counter and map counter.

    The rework count is PhaseRecord.heal_round. NOT state.heal_baselines, which
    maps a gate id to a git TREE SHA — reading that as a number throws on the
    first healed run.
    """
    word = GLOSSARY.get(rec.get("status"), rec.get("status") or "?")
    rounds = int(rec.get("heal_round") or 0)
    if rounds > 0:
        word = f"sent back for rework ({rounds} of {budgets.get(node_id, '?')})"
    items = rec.get("items") or {}
    if items:
        done = sum(1 for i in items.values() if i.get("status") == "done")
        redone = sum(1 for i in items.values() if int(i.get("attempts") or 0) > 1)
        word = f"{word} - {done} of {len(items)} checked"
        if redone:
            word += f", {redone} redone"
    return word


def mission_rows(run_dir: Path, repo_root: Path | None = None,
                 now: datetime | None = None) -> list[tuple[str | None, str]]:
    """The DE tier, collapsed, as (node_id | None, text) rows.

    Rows carry their node id so a consumer can offer a drill-down without
    re-deriving which line is which — matching a rendered line back to a node by
    string prefix is the kind of cleverness that selects the wrong step the
    first time a label changes.

    What is NEVER collapsed: anything running, anything needing the human,
    anything that failed, anything sent back for rework, and any node that
    dropped a mission.txt. Collapsing is applied to the quiet majority so the
    loud minority is legible — not the other way round.
    """
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json")
    if state is None:
        return [(None, "(reading state...)")]
    flow = read_json(run_dir / "flow.tg.json")
    budgets = heal_budgets(flow)
    labels = load_labels(run_dir, repo_root)

    rows: list[tuple[str | None, str]] = [(None, headline(state, flow, now=now)), (None, "")]
    collapsed_done = collapsed_skip = pending_shown = pending_hidden = 0

    for node_id, rec in (state.get("nodes") or {}).items():
        status = rec.get("status")
        healed = int(rec.get("heal_round") or 0) > 0
        is_map = bool(rec.get("items"))
        note_path = run_dir / "phases" / node_id / "mission.txt"
        has_note = note_path.is_file()

        if status == "done" and not healed and not is_map and not has_note:
            collapsed_done += 1
            continue
        if status == "skipped":
            collapsed_skip += 1
            continue
        if status == "pending" and not healed:
            if pending_shown >= 3:
                pending_hidden += 1
                continue
            pending_shown += 1

        name = label_for(labels, node_id)
        if len(name) > LABEL_WIDTH:
            name = name[: LABEL_WIDTH - 1] + "…"
        rows.append((node_id, f"{name:<{LABEL_WIDTH}} {node_word(node_id, rec, budgets)}"))

        if has_note:
            body = read_text(note_path) or ""
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            if first:
                rows.append((None, f"{'':<{LABEL_WIDTH}}   {first}"))

    if pending_hidden:
        rows.append((None, f"{'':<{LABEL_WIDTH}} + {pending_hidden} more waiting"))
    tail = []
    if collapsed_done:
        tail.append(f"{collapsed_done} finished")
    if collapsed_skip:
        tail.append(f"{collapsed_skip} not needed")
    if tail:
        rows += [(None, ""), (None, f"{'':<{LABEL_WIDTH}} {', '.join(tail)}")]
    return rows


def mission_lines(run_dir: Path, repo_root: Path | None = None,
                  now: datetime | None = None) -> list[str]:
    return [text for _, text in mission_rows(run_dir, repo_root=repo_root, now=now)]


# --------------------------------------------------------------- ACTIVITY

def frontier_node(state: dict) -> str | None:
    for node_id, rec in (state.get("nodes") or {}).items():
        if rec.get("status") == "running":
            return node_id
    return None


def format_progress(record: dict | str) -> str:
    """Render a WHOLE progress record: bar, step, note.

    The spawn contract instructs an agent to emit {"step", "pct", "note"} and
    `lockstep status` already renders pct; the pane printed `note` alone.

    Nothing is estimated. No pct means no bar — an invented denominator on a
    progress bar is the fastest way to teach a human that this view makes
    things up.
    """
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except ValueError:
            return record.strip()
    if not isinstance(record, dict):
        return str(record)

    parts: list[str] = []
    pct = record.get("pct")
    if pct is not None:
        try:
            value = max(0, min(100, int(float(pct))))
            filled = round(value / 10)
            parts.append(f"[{'#' * filled}{'-' * (10 - filled)}] {value:3d}%")
        except (TypeError, ValueError):
            pass
    if record.get("step"):
        parts.append(f"step {record['step']}")
    note = record.get("note") or record.get("message") or ""
    if note:
        parts.append(str(note))
    return "  ".join(parts) if parts else json.dumps(record)


def stdout_liveness(phase_dir: Path) -> str | None:
    """Mechanical liveness when the agent emits no progress at all.

    progress.jsonl is written BY THE AGENT, on instruction; a harness that
    ignores the instruction leaves a view showing only a clock for the whole
    node — the exact ambiguity the heartbeat principle exists to remove.

    Size and mtime only, never CONTENT. Tailing stdout.log was rejected for good
    reason (JSON-mode harnesses emit nothing for minutes, then dump raw JSON),
    and nothing here reverses that.
    """
    best = None
    for name in ("stdout.log", "stderr.log"):
        p = Path(phase_dir) / name
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size <= 0:
            continue
        if best is None or st.st_mtime > best[1].st_mtime:
            best = (p, st)
    if best is None:
        return None
    _, st = best
    kb = st.st_size / 1024
    ago = max(0, int(datetime.now(timezone.utc).timestamp() - st.st_mtime))
    return f"still producing output - {kb:.1f} KB, last write {ago}s ago"


def activity_lines(run_dir: Path, limit: int = 12,
                   repo_root: Path | None = None) -> list[str]:
    """The frontier node and its recent progress, or a mechanical idle line.

    Idle placeholders are mechanical (L-M1): blank must never be ambiguous
    between dead, thinking, and waiting-on-you.
    """
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json")
    if state is None:
        return ["(reading state...)"]

    node_id = frontier_node(state)
    if node_id is None:
        statuses = {r.get("status") for r in (state.get("nodes") or {}).values()}
        if "blocked" in statuses:
            return ["needs you - nothing is spending"]
        if "pending" not in statuses:
            return ["segment done - nothing is spending"]
        return ["waiting - nothing is spending"]

    labels = load_labels(run_dir, repo_root)
    out = [f"{label_for(labels, node_id)}"]
    phase = run_dir / "phases" / node_id

    body = read_text(phase / "progress.jsonl") or ""
    records = [ln for ln in body.splitlines() if ln.strip()]
    if records:
        out += ["  " + format_progress(ln) for ln in records[-limit:]]
    else:
        live = stdout_liveness(phase)
        out.append("  " + (live if live else "started - no progress reported yet"))
    return out


# --------------------------------------------------------------- the frame

def mission_frame(run_dir: Path, spend: list[str] | None = None,
                  repo_root: Path | None = None, now: datetime | None = None) -> list[str]:
    """The whole MISSION block, ready to paint."""
    run_dir = Path(run_dir)
    frame = [f"MISSION  {run_dir.name}", "-" * WIDTH]
    frame += mission_lines(run_dir, repo_root=repo_root, now=now)
    frame += ["-" * WIDTH]
    frame += spend if spend else ["(spend unavailable)"]
    return frame


def visible_nodes(run_dir: Path, repo_root: Path | None = None) -> list[str]:
    """Node ids in the order MISSION shows them — the drill-down index.

    Derived from mission_rows rather than recomputed, so pressing `3` selects
    the third thing on screen by construction. A second implementation of the
    collapse rules would drift from the first and silently mis-select.
    """
    return [nid for nid, _ in mission_rows(run_dir, repo_root=repo_root) if nid]


def node_detail(run_dir: Path, node_id: str, repo_root: Path | None = None) -> list[str]:
    """T2.3 — "what does 'stopped with a problem' mean for this step?"

    MISSION is a wall with no way in: a domain expert wanting the reason behind
    one line has to go through the chat, which puts a narrated answer between
    them and an artifact that already exists. This prints the artifact, and says
    where it lives so the answer is checkable rather than merely readable.
    """
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json") or {}
    rec = (state.get("nodes") or {}).get(node_id)
    if rec is None:
        return [f"no such step in this run: {node_id}"]

    labels = load_labels(run_dir, repo_root)
    name = label_for(labels, node_id)
    out = ["=" * WIDTH, f"  {name}"]
    if name != node_id:
        out.append(f"  (step id: {node_id})")
    out += ["=" * WIDTH]
    out.append(f"  state      : {GLOSSARY.get(rec.get('status'), rec.get('status'))}")
    out.append(f"  attempts   : {rec.get('attempts')}")
    if rec.get("heal_round"):
        out.append(f"  rework     : round {rec['heal_round']}")
    for field in ("started_at", "ended_at"):
        if rec.get(field):
            out.append(f"  {field:<11}: {rec[field]}")

    if rec.get("error"):
        out += ["", "  what went wrong"]
        out += [f"    {rec['error']}"]

    verdict = (state.get("verdicts") or {}).get(node_id)
    if verdict:
        # state.json holds only a LOSSY latest verdict; per-round truth lives in
        # the rotated result-attempt<n>.json files. Say which one this is.
        out += ["", "  latest verdict (lossy - per-round truth is in the rotated files)",
                f"    {verdict}"]

    # Sizes are stat()ed inside the guard for the same reason as newest_run:
    # the engine ROTATES per-attempt files, so a name listed a moment ago can be
    # gone by the time it is measured.
    phase = run_dir / "phases" / node_id
    sized: list[str] = []
    try:
        for p in sorted(phase.iterdir()):
            try:
                if p.is_file():
                    sized.append(f"    {p.name:<28} {p.stat().st_size:>8,} bytes")
            except OSError:
                continue
    except OSError:
        pass
    if sized:
        out += ["", "  artifacts"] + sized
    return out


def needs_you(state: dict | None) -> bool:
    if not state:
        return False
    return any(r.get("status") == "blocked" for r in (state.get("nodes") or {}).values())
