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
import re
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
        # A FINISHED run's clock stops at its last ended_at. It used to keep
        # counting against the wall clock, so a completed run showed
        # "done - 35 h 56 m" days later — and a duration beside "done" reads as
        # what the work took.
        until = now or datetime.now(timezone.utc)
        if not (running or blocked or failed) and total and settled == total:
            ends = [t for t in (_parse_ts(r.get("ended_at")) for r in recs) if t]
            if ends:
                until = max(ends)
        mins = max(0, int((until - began).total_seconds() // 60))
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


def _walk_steps(run_dir: Path, repo_root: Path | None = None, *, collapsed: bool = True):
    """(state, flow, rows, counters) — the ONE implementation of the collapse
    rules. `mission_rows` formats it for a terminal, `step_rows` hands it to a
    caller that has its own layout; neither reimplements the other.

    What is NEVER collapsed: anything running, anything needing the human,
    anything that failed, anything sent back for rework, and any node that
    dropped a mission.txt. Collapsing is applied to the quiet majority so the
    loud minority is legible — not the other way round.

    `collapsed=False` returns every node in recorded order: a timeline shows
    every step by definition, so it is a different rendering of the step list,
    not an expansion of the collapsed one.
    """
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json")
    if state is None:
        return None, None, [], {}
    flow = read_json(run_dir / "flow.tg.json")
    budgets = heal_budgets(flow)
    labels = load_labels(run_dir, repo_root)
    kinds = {n.get("id"): n.get("kind") for n in ((flow or {}).get("nodes") or [])}

    rows: list[dict] = []
    collapsed_done = collapsed_skip = pending_shown = pending_hidden = 0

    for node_id, rec in (state.get("nodes") or {}).items():
        status = rec.get("status")
        healed = int(rec.get("heal_round") or 0) > 0
        is_map = bool(rec.get("items"))
        note_path = run_dir / "phases" / node_id / "mission.txt"
        has_note = note_path.is_file()

        if collapsed:
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

        note = ""
        if has_note:
            body = read_text(note_path) or ""
            note = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")

        rows.append({
            "node_id": node_id,
            "label": label_for(labels, node_id),
            "word": node_word(node_id, rec, budgets),
            "status": status or "",
            "icon": COST_ICON.get(status, "○"),
            "note": note,
            "kind": kinds.get(node_id) or rec.get("kind") or "",
        })

    counters = {
        "pending_hidden": pending_hidden,
        "collapsed_done": collapsed_done,
        "collapsed_skip": collapsed_skip,
    }
    return state, flow, rows, counters


def step_rows(run_dir: Path, repo_root: Path | None = None, *,
              collapsed: bool = True) -> list[dict]:
    """One dict per step: `{node_id, label, word, status, icon, note, kind}`.

    `mission_rows` returns preformatted terminal strings — `f"{name:<34} {word}"`
    with a 33-character truncation — and there was no accessor that returned the
    parts separately. Shipping 72-column padding into HTML, or re-splitting it
    in JavaScript, would each put a rendered word outside pytest's reach.

    `icon` is the existing `COST_ICON`; `word` is `node_word`. No new glyphs and
    no second glossary.
    """
    return _walk_steps(run_dir, repo_root, collapsed=collapsed)[2]


def collapse_tail(run_dir: Path, repo_root: Path | None = None) -> list[str]:
    """The rows `mission_rows` SYNTHESIZES rather than reads: `+ N more waiting`
    and `N finished, M not needed`. They have no per-node counterpart, which is
    why L0→L1 is a switch and not an expansion — a caller that shows every step
    must not show these, and one that collapses must."""
    counters = _walk_steps(run_dir, repo_root)[3]
    out: list[str] = []
    if counters.get("pending_hidden"):
        out.append(f"+ {counters['pending_hidden']} more waiting")
    counts = []
    if counters.get("collapsed_done"):
        counts.append(f"{counters['collapsed_done']} finished")
    if counters.get("collapsed_skip"):
        counts.append(f"{counters['collapsed_skip']} not needed")
    if counts:
        out.append(", ".join(counts))
    return out


def mission_rows(run_dir: Path, repo_root: Path | None = None,
                 now: datetime | None = None) -> list[tuple[str | None, str]]:
    """The DE tier, collapsed, as (node_id | None, text) rows.

    Rows carry their node id so a consumer can offer a drill-down without
    re-deriving which line is which — matching a rendered line back to a node by
    string prefix is the kind of cleverness that selects the wrong step the
    first time a label changes.

    The terminal rendering stays the thing that FORMATS: `step_rows` supplies
    the parts, this pads them.
    """
    state, flow, steps, counters = _walk_steps(run_dir, repo_root)
    if state is None:
        return [(None, "(reading state...)")]

    rows: list[tuple[str | None, str]] = [(None, headline(state, flow, now=now)), (None, "")]
    for step in steps:
        name = step["label"]
        if len(name) > LABEL_WIDTH:
            name = name[: LABEL_WIDTH - 1] + "…"
        rows.append((step["node_id"], f"{name:<{LABEL_WIDTH}} {step['word']}"))
        if step["note"]:
            rows.append((None, f"{'':<{LABEL_WIDTH}}   {step['note']}"))

    if counters["pending_hidden"]:
        rows.append((None, f"{'':<{LABEL_WIDTH}} + {counters['pending_hidden']} more waiting"))
    tail = []
    if counters["collapsed_done"]:
        tail.append(f"{counters['collapsed_done']} finished")
    if counters["collapsed_skip"]:
        tail.append(f"{counters['collapsed_skip']} not needed")
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
    if ago > 120:
        # Say what the numbers say. "still producing output" beside "last write
        # 114271s ago" (observed) restates the thinking/stuck ambiguity this
        # fallback exists to remove, as a contradiction on a single line.
        return f"no new output for {ago // 60} m - {kb:.1f} KB so far"
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


def node_detail(run_dir: Path, node_id: str, repo_root: Path | None = None, *,
                state: dict | None = None, labels: dict[str, str] | None = None) -> list[str]:
    """T2.3 — "what does 'stopped with a problem' mean for this step?"

    MISSION is a wall with no way in: a domain expert wanting the reason behind
    one line has to go through the chat, which puts a narrated answer between
    them and an artifact that already exists. This prints the artifact, and says
    where it lives so the answer is checkable rather than merely readable.

    `state` and `labels` let a caller rendering EVERY node hand over the two
    reads it has already done. `load_labels` runs a recursive `flows/**` glob,
    and a page that drew one drawer per node was doing that once per node.
    """
    run_dir = Path(run_dir)
    if state is None:
        state = read_json(run_dir / "state.json") or {}
    rec = (state.get("nodes") or {}).get(node_id)
    if rec is None:
        return [f"no such step in this run: {node_id}"]

    if labels is None:
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
    """ANY blocked node — a clarify gate as much as an approval. Not the
    handover predicate: that is `quiescent.py`'s, and nothing here re-derives
    it. A surface deciding whether to show approval evidence must use
    `approval_waiting`, or it puts an approval's evidence on screen at a
    clarification, where there is no approval and no decision to make."""
    if not state:
        return False
    return any(r.get("status") == "blocked" for r in (state.get("nodes") or {}).values())


# --------------------------------------------------- questions and evidence

def question_card(run_dir: Path) -> str | None:
    """`<run_dir>/question-card.txt`, verbatim, or None.

    The DE guide promises that at a clarification "the exact words the system
    used will appear on screen in the ACTIVITY area" — and only `cockpit.ps1`
    rendered the file. Neither this module nor the TUI read it, so every
    non-PowerShell surface carried the banner without the words. A pre-existing
    broken promise, not new work.
    """
    text = read_text(Path(run_dir) / "question-card.txt")
    if text is None or not text.strip():
        return None
    return text


def approval_waiting(run_dir: Path) -> str | None:
    """The node id of the one approval awaiting a decision, or None.

    Delegates to `quiescent.check` — the predicate that decides a handover is
    the predicate that decides whether evidence belongs on screen, and there is
    exactly one of it.
    """
    try:
        import quiescent
        approvals, blockers = quiescent.check(Path(run_dir))
    except Exception:  # noqa: BLE001 - a view never raises
        return None
    if blockers or len(approvals) != 1:
        return None
    return approvals[0]


def evidence_writer(flow: dict | None, approval_id: str | None) -> str | None:
    """Which node writes `approval-evidence.txt`, from the run's own flow copy.

    Mechanical, in two steps and no further: a shell node whose argv mentions
    `render_evidence`, else the approval's single direct shell dependency.
    Neither found means we cannot say — and saying so beats guessing, because
    the answer decides whether the DE is told their evidence is stale.
    """
    nodes = (flow or {}).get("nodes") or []
    for n in nodes:
        cmd = n.get("spec", {}).get("cmd") or []
        if any("render_evidence" in str(part) for part in cmd):
            return n.get("id")
    if approval_id:
        by_id = {n.get("id"): n for n in nodes}
        approval = by_id.get(approval_id) or {}
        shells = [d for d in (approval.get("depends_on") or [])
                  if (by_id.get(d) or {}).get("kind") == "shell"]
        if len(shells) == 1:
            return shells[0]
    return None


def evidence_status(run_dir: Path) -> dict | None:
    """The evidence a waiting approval is decided from, and whether it is fresh.

    `{approval, path, text, stale, writer}` — or None when no approval waits.
    `stale` is None when freshness cannot be established.

    `approval-evidence.txt` is a FIXED path holding a point-in-time snapshot
    (`render_evidence.impact()` counts `git status` at render time), so a later
    segment overwrites it and a stale one survives. Freshness is the file
    against **the writing node's most recent run interval** — not against the
    approval's `started_at`, which is stamped when the approval goes running,
    i.e. always AFTER the render node wrote the file, so every fresh file would
    be flagged stale.
    """
    run_dir = Path(run_dir)
    approval = approval_waiting(run_dir)
    if approval is None:
        return None
    path = run_dir / "approval-evidence.txt"
    text = read_text(path)
    if text is None:
        return {"approval": approval, "path": str(path), "text": None,
                "stale": None, "writer": None}

    flow = read_json(run_dir / "flow.tg.json")
    writer = evidence_writer(flow, approval)
    stale: bool | None = None
    if writer:
        try:
            import cost_report
            spans = cost_report.node_intervals(cost_report._read_events(run_dir)).get(writer) or []
            starts = [_parse_ts(a) for a, _ in spans]
            last = max([s for s in starts if s], default=None)
            if last is not None:
                written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                stale = written < last
        except Exception:  # noqa: BLE001 - a view never raises
            stale = None
    return {"approval": approval, "path": str(path), "text": text,
            "stale": stale, "writer": writer}


def rejection_text(run_dir: Path) -> str | None:
    """`<run_dir>/rejection.txt` — the human's own words, the one artifact the
    doctrine singles out as theirs. Reachable, never automatic: when an
    approval is waiting, its evidence is what is on screen."""
    text = read_text(Path(run_dir) / "rejection.txt")
    if text is None or not text.strip():
        return None
    return text


# --------------------------------------------------------------- COST panel
#
# Modeled on pi-taskflow's progress render (npm:pi-taskflow, dist/render.js):
# a one-line header with the aggregate cost, then DAG-ordered rows — each
# topological layer fanned out with a ┌ ├ └ rail — carrying a single-width
# status glyph, the model, the cost, wall time, and a ↻ retry counter.
#
# Two modes, one keybinding away in the TUI:
#   history — every attempt is summed (retries and correctives cost money too;
#             this is what was SPENT, and it matches the spend block's tally)
#   head    — each scope's kept attempt only (what the current result cost)
#
# Everything is a projection of cost_report.collect_run over artifacts the run
# already left behind — same reader, same field maps, same honest-limits notes,
# so this panel cannot disagree with the spend line above it.

COST_ICON = {
    "done": "✓",
    "running": "◐",
    "failed": "✗",
    "blocked": "⊗",
    "skipped": "⊘",
    "pending": "○",
}


def _cost_str(v: float | None) -> str | None:
    """pi-taskflow's costStr: 2 decimals normally, 4 below a cent. None when
    nothing was reported — the caller omits the column, never prints $0."""
    if v is None:
        return None
    return f"${v:.2f}" if v >= 0.01 else f"${v:.4f}"


def format_duration(seconds: float | None) -> str | None:
    """pi-taskflow's elapsed: 5s / 3m30s / 1h05m.

    Public because a time axis needs a duration renderer and the page must not
    have one of its own: a formatter in the browser is a rendering pytest
    cannot execute. It was `_elapsed_str`, private and cost-panel-only.
    """
    if seconds is None:
        return None
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def format_clock(iso: str | None, tz=None) -> str | None:
    """An absolute wall-clock tick label, `HH:MM`.

    A SECOND formatter, not a variant of the first: a time axis carries clock
    times and a running step carries a duration, and one function cannot
    produce both. Local time by default — the reader is watching a run now;
    `tz` pins it for a test.
    """
    stamp = _parse_ts(iso)
    if stamp is None:
        return None
    return stamp.astimezone(tz).strftime("%H:%M")


_elapsed_str = format_duration  # the cost panel's original name


def _short_model(model: str | None) -> str | None:
    if not model:
        return None
    return model.split("/")[-1]


def topo_layers(flow: dict | None, node_ids: list[str]) -> list[list[str]]:
    """Kahn layering over the run's own flow copy, restricted to the nodes the
    run actually recorded. Nodes absent from the flow (or the whole run when
    there is no flow copy) come back as one final layer in state order — the
    panel degrades to a flat list rather than guessing at edges."""
    deps: dict[str, list[str]] = {}
    for n in ((flow or {}).get("nodes") or []):
        nid = n.get("id")
        if nid in node_ids:
            deps[nid] = [d for d in (n.get("depends_on") or []) if d in node_ids]
    layers: list[list[str]] = []
    placed: set[str] = set()
    remaining = [nid for nid in node_ids if nid in deps]
    while remaining:
        layer = [nid for nid in remaining if all(d in placed for d in deps[nid])]
        if not layer:
            break  # a cycle cannot survive `lockstep verify`; defensive only
        layers.append(layer)
        placed.update(layer)
        remaining = [nid for nid in remaining if nid not in placed]
    leftovers = [nid for nid in node_ids if nid not in placed]
    if leftovers:
        layers.append(leftovers)
    return layers


def _rail(i: int, size: int) -> str:
    if size <= 1:
        return " "
    if i == 0:
        return "┌"
    if i == size - 1:
        return "└"
    return "├"


def _usage(run_dir: Path) -> dict | None:
    """cost_report.collect_run, defensively. None means the reader itself is
    unavailable or the state is mid-replace; the panel says so."""
    try:
        import cost_report
        maps = cost_report.load_field_maps(None)
        return cost_report.collect_run(Path(run_dir), maps)
    except Exception:  # noqa: BLE001 - a view never raises
        return None


def _mode_cost(row: dict, mode: str) -> float | None:
    if mode == "head" and row.get("status") != "running":
        return (row.get("head") or {}).get("cost")
    # A running node has no kept attempt yet; cost-so-far is the honest figure
    # in either mode.
    return row.get("cost")


def _attempt_label(detail: dict) -> str:
    m = re.search(r"attempt(\d+)", detail.get("log") or "")
    scope = detail.get("scope") or ""
    label = "kept" if detail.get("final") else (f"attempt {m.group(1)}" if m else "attempt ?")
    return f"{scope} {label}".strip()


def cost_detail(row: dict, mode: str) -> str:
    """The right-hand column for one node, pi-taskflow phaseDetail style."""
    status = row.get("status")
    if status == "pending":
        return "-"
    if status == "skipped":
        return "not needed"
    parts: list[str] = []
    items_total = None
    detail = row.get("attempts_detail") or []
    item_scopes = {d["scope"] for d in detail if d.get("scope")}
    if item_scopes:
        done_items = sum(1 for d in detail if d.get("scope") and d.get("final"))
        items_total = f"{done_items} items"
    model = _short_model(row.get("model"))
    if model:
        extra = len(row.get("models") or []) - 1
        parts.append(model + (f" +{extra}" if extra > 0 else ""))
    if items_total:
        parts.append(items_total)
    money = _cost_str(_mode_cost(row, mode))
    if money:
        parts.append(money)
    retries = max(0, int(row.get("attempts") or 0) - 1)
    if retries:
        parts.append(f"↻{retries}")
    if row.get("heal_rounds"):
        parts.append(f"rework {row['heal_rounds']}")
    t = _elapsed_str(row.get("wall_s"))
    if t:
        parts.append(t)
    if status in ("failed", "blocked"):
        parts.insert(0, GLOSSARY.get(status, status))
    if row.get("note"):
        parts.append(f"({row['note']})")
    return "  ".join(parts) if parts else GLOSSARY.get(status, status or "?")


def cost_lines(run_dir: Path, mode: str = "history",
               now: datetime | None = None, usage: dict | None = None) -> list[str]:
    """The whole cost panel: header + DAG-ordered rows (+ per-attempt history
    sub-lines in history mode).

    `usage` lets a caller that already has `cost_report.collect_run` for this
    run pass it in. The page renders two modes of this panel plus a spend block
    from the same reader; without it, one page render walks every phase dir and
    parses every envelope three times over.
    """
    run_dir = Path(run_dir)
    state = read_json(run_dir / "state.json")
    if state is None:
        return ["(reading state...)"]
    run = usage if usage is not None else _usage(run_dir)
    if run is None:
        return ["(cost data unavailable - state mid-replace or cost_report missing)"]
    flow = read_json(run_dir / "flow.tg.json")
    rows = {r["node"]: r for r in run["rows"]}
    node_ids = list(rows)

    statuses = [r.get("status") for r in rows.values()]
    done = sum(1 for s in statuses if s in ("done", "skipped"))
    running = sum(1 for s in statuses if s == "running")
    failed = sum(1 for s in statuses if s in ("failed", "blocked"))
    total_cost = None
    costs = [_mode_cost(r, mode) for r in rows.values()]
    if any(c is not None for c in costs):
        total_cost = sum(c for c in costs if c is not None)

    head_glyph = ("✗" if failed else "◐" if running else
                  "✓" if done == len(statuses) and statuses else "○")
    header = f"{head_glyph} {run['flow']}  {done}/{len(statuses)}"
    if running:
        header += f" · {running}▸"
    if failed:
        header += f" · {failed}✗"
    money = _cost_str(total_cost)
    if money:
        header += f" · {money}"
    began = _parse_ts(state.get("started_at"))
    if began:
        until = now or datetime.now(timezone.utc)
        ends = [t for t in (_parse_ts(r.get("ended_at"))
                            for r in (state.get("nodes") or {}).values()) if t]
        if not running and ends and done == len(statuses):
            until = max(ends)
        header += f" · {_elapsed_str(max(0.0, (until - began).total_seconds()))}"
    tag = ("history: every attempt is counted" if mode == "history"
           else "head: kept attempts only")
    out = [header, f"  ({tag})", ""]

    id_w = max((len(n) for n in node_ids), default=2)
    kind_w = max((len(rows[n].get("kind") or "?") for n in node_ids), default=4)
    prev_layer: set[str] = set()
    for layer in topo_layers(flow, node_ids):
        for i, nid in enumerate(layer):
            row = rows[nid]
            icon = COST_ICON.get(row.get("status"), "○")
            line = (f"  {_rail(i, len(layer))} {icon} {nid:<{id_w}}  "
                    f"{(row.get('kind') or '?'):<{kind_w}}  {cost_detail(row, mode)}")
            deps = []
            for n in ((flow or {}).get("nodes") or []):
                if n.get("id") == nid:
                    deps = [d for d in (n.get("depends_on") or []) if d not in prev_layer
                            and d in rows]
            if deps:
                line += f"  ↳ {', '.join(deps)}"
            out.append(line)
            if mode == "history":
                detail = row.get("attempts_detail") or []
                if len(detail) > 1:
                    for d in detail:
                        bits = [_attempt_label(d)]
                        model = _short_model(d.get("model"))
                        if model:
                            bits.append(model)
                        money = _cost_str(d.get("cost"))
                        if money:
                            bits.append(money)
                        if not d.get("final"):
                            bits.append("(superseded)")
                        out.append(f"  {' ':<{id_w + 6}}{'  '.join(bits)}")
        prev_layer = set(layer)
    return out
