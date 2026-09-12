"""The trace page: four disclosure levels over one run (contrib/mission_server.py).

What is asserted here is everything a reader could otherwise only check by
looking: the route table, that no route writes, that the landing view renders
with JavaScript switched off, that every word and every formatted time comes
from `mission_view`, and that the waterfall and its table twin cannot disagree
— because they are one walk over the same intervals.

The table twin is doing triple duty: it is the accessibility path, the no-JS
fallback, and the surface these tests read. That is what makes "no logic that
can be wrong lives in the JS" a structural fact rather than a discipline.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRIB = ROOT / "contrib"
sys.path.insert(0, str(CONTRIB))

import mission_server  # noqa: E402
import mission_view as mv  # noqa: E402
from lockstep.state import append_event  # noqa: E402

PAGE_NOW = datetime(2026, 8, 8, 9, 20, tzinfo=timezone.utc)
T0 = datetime(2026, 8, 8, 9, 2, tzinfo=timezone.utc)

PAGE_FLOW = {
    "format_version": "1.0",
    "name": "brief",
    "budget": {"max_agent_spawns": 25},
    "nodes": [
        {"id": "produce", "kind": "harness"},
        {"id": "render-evidence", "kind": "shell", "depends_on": ["produce"],
         "spec": {"cmd": ["python", "contrib/render_evidence.py"]}},
        {"id": "approve", "role": "approval", "depends_on": ["render-evidence"]},
        {"id": "deliver", "kind": "shell", "depends_on": ["approve"],
         "spec": {"cmd": ["python", "contrib/deliver.py"]}},
    ],
}

SPANS = [("produce", 0, 4), ("produce", 6, 9), ("render-evidence", 9, 10)]


def _iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def page_run(tmp_path: Path, *, name: str = "2026-08-08-brief-a41c",
             chained: bool = False, evidence: bool = True,
             question: bool = False, cap: int | None = 25) -> Path:
    """A run dir in the canonical approval shape: `produce` healed (two run
    intervals, so the timeline has something a merged span would hide), a
    render node, a waiting approval, and the sanctioned trivial shell tail.

    Events are hand-stamped by default, for deterministic geometry;
    `chained=True` runs them through `append_event` so the chain is real, which
    is what the trace-integrity tests need.
    """
    run = tmp_path / name
    (run / "phases" / "produce").mkdir(parents=True)
    (run / "phases" / "render-evidence").mkdir(parents=True)

    flow = json.loads(json.dumps(PAGE_FLOW))
    if cap is None:
        flow.pop("budget")
    else:
        flow["budget"]["max_agent_spawns"] = cap
    (run / "flow.tg.json").write_text(json.dumps(flow), encoding="utf-8")
    (run / "flow.labels.json").write_text(json.dumps({"nodes": {
        "produce": "draft the sections", "render-evidence": "write the evidence",
        "approve": "approve the brief", "deliver": "publish to the drive",
    }}), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "flow_name": "brief", "started_at": _iso(0), "token_spawns": 9, "verdicts": {},
        "nodes": {
            "produce": {"node_id": "produce", "role": "work", "kind": "harness",
                        "status": "done", "attempts": 2, "heal_round": 1,
                        "started_at": _iso(0), "ended_at": _iso(9),
                        "hash_parts": {"prompt.task": "aa", "config": "bb"},
                        "invalidated_by": ["prompt.task"]},
            "render-evidence": {"node_id": "render-evidence", "role": "work",
                                "kind": "shell", "status": "done", "attempts": 1,
                                "heal_round": 0, "started_at": _iso(9),
                                "ended_at": _iso(10)},
            "approve": {"node_id": "approve", "role": "approval", "kind": "",
                        "status": "blocked", "attempts": 0, "heal_round": 0,
                        "started_at": _iso(10)},
            "deliver": {"node_id": "deliver", "role": "work", "kind": "shell",
                        "status": "pending", "attempts": 0, "heal_round": 0},
        },
    }), encoding="utf-8")

    if chained:
        for node, _a, _b in SPANS:
            append_event(run, {"node": node, "status": "running"})
            append_event(run, {"node": node, "status": "done"})
    else:
        (run / "events.jsonl").write_text("".join(
            json.dumps({"ts": _iso(a), "node": node, "status": "running"}) + "\n"
            + json.dumps({"ts": _iso(b), "node": node, "status": "done"}) + "\n"
            for node, a, b in SPANS), encoding="utf-8")

    (run / "phases" / "produce" / "mission.txt").write_text(
        "read 40 files\n", encoding="utf-8")
    if evidence:
        ev = run / "approval-evidence.txt"
        ev.write_text("Approve: publish the quarterly brief\n\n"
                      "--impact      3 files, +214 / -38 lines\n"
                      "--reversible  yes\n", encoding="utf-8")
        stamp = (T0 + timedelta(minutes=10, seconds=30)).timestamp()
        os.utime(ev, (stamp, stamp))
    if question:
        (run / "question-card.txt").write_text(
            "Which of the two schemas is authoritative?\n", encoding="utf-8")
    return run


def get(run: Path | None, path: str, tmp_path: Path):
    return mission_server.handle(path, tmp_path, run, ROOT, now=PAGE_NOW)


# --------------------------------------- routes, and that none of them writes

def test_the_route_table_is_enumerated_and_pinned():
    assert mission_server.ROUTES == (
        "/", "/index.html", "/api/state", "/api/events", "/api/node/<id>",
        "/api/evidence", "/api/question",
    )


def test_the_page_has_no_route_that_writes():
    """The MECHANISM half of the guarantee: the absence of the method IS the
    promise. BaseHTTPRequestHandler answers anything else with 501."""
    handler = mission_server.make_handler(Path("runs"), None, ROOT)
    assert hasattr(handler, "do_GET")
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_HEAD"):
        assert not hasattr(handler, verb), f"{verb} must not exist on the MISSION handler"


def test_no_route_writes_anything(tmp_path, monkeypatch):
    """COVERAGE-BOUNDED, not structural, and the difference is worth stating.

    This drives every route with the write APIs made to raise, which proves
    purity FOR THE INPUTS EXERCISED. AST inspection cannot do better — one
    level of indirection defeats it, and the transitive closure is where writes
    live. The mechanism half is the test above; this is the coverage half.
    """
    run = page_run(tmp_path, question=True)
    real_open = open

    def no_write_open(file, mode="r", *a, **kw):
        if any(ch in str(mode) for ch in "wxa+"):
            raise AssertionError(f"a route opened {file!r} for writing")
        return real_open(file, mode, *a, **kw)

    def forbid(name):
        def boom(*_a, **_kw):
            raise AssertionError(f"a route called {name}")
        return boom

    monkeypatch.setattr("builtins.open", no_write_open)
    for target in ("write_text", "write_bytes", "unlink", "mkdir", "touch", "rename"):
        monkeypatch.setattr(Path, target, forbid(f"Path.{target}"))
    monkeypatch.setattr(os, "replace", forbid("os.replace"))
    monkeypatch.setattr(shutil, "move", forbid("shutil.move"))

    for path in ("/", "/index.html", "/api/state", "/api/events?after=0",
                 "/api/node/produce", "/api/evidence", "/api/question"):
        status, _ctype, body = get(run, path, tmp_path)
        assert status == 200, path
        assert body


def test_a_bad_cursor_and_a_traversal_are_404(tmp_path):
    run = page_run(tmp_path)
    for path in ("/api/events?after=abc", "/api/events?after=-1", "/api/events?after=1.5"):
        assert get(run, path, tmp_path)[0] == 404, path
    for path in ("/api/node/../../etc/passwd", "/api/node/nope", "/api/node/",
                 "/wat", "/api/"):
        assert get(run, path, tmp_path)[0] == 404, path


def test_the_cursor_advances_and_never_replays(tmp_path):
    """The cursor is OPAQUE since S1.1 (`<gen>.<offset>.<ordinal>`), so this
    asserts the property rather than the representation: it advances off the
    start, a re-request at it yields nothing, and it stays put when nothing
    was appended."""
    run = page_run(tmp_path)
    first = json.loads(get(run, "/api/events?after=0", tmp_path)[2])
    assert first["events"]
    assert first["next"] != "0", "the cursor must move off the start"
    again = json.loads(get(run, f"/api/events?after={first['next']}", tmp_path)[2])
    assert again["events"] == [] and again["next"] == first["next"]


def test_the_cursor_carries_its_generation_and_resets_on_a_new_journal(tmp_path):
    """A cursor from another generation of the file must not be read as an
    offset into this one. The journal is append-only within a run, so the
    guard is cheap insurance against a recreated file — and the reset is
    silent and explicit, not a 404: staleness is the file's business."""
    run = page_run(tmp_path)
    first = json.loads(get(run, "/api/events?after=0", tmp_path)[2])
    gen, offset, ordinal = first["next"].split(".")
    assert len(gen) == 8 and int(offset) > 0 and int(ordinal) > 0

    forged = f"{'0' * 8}.{offset}.{ordinal}"          # well-formed, wrong generation
    reset = json.loads(get(run, f"/api/events?after={forged}", tmp_path)[2])
    assert reset["events"] == first["events"], "a foreign cursor re-serves from the top"
    assert reset["next"] == first["next"]


def test_a_cursor_past_the_end_of_the_file_resets(tmp_path):
    run = page_run(tmp_path)
    first = json.loads(get(run, "/api/events?after=0", tmp_path)[2])
    gen, offset, ordinal = first["next"].split(".")
    beyond = f"{gen}.{int(offset) + 10_000_000}.{ordinal}"
    doc = json.loads(get(run, f"/api/events?after={beyond}", tmp_path)[2])
    assert doc["events"] == first["events"] and doc["next"] == first["next"]


def test_a_torn_trailing_line_is_not_consumed(tmp_path):
    """The driver appends while the page reads (SPEC §10.3). A half-written
    final line must be left for the next tick, whole — with byte offsets this
    stops being a special case, and this test is what keeps it that way."""
    run = page_run(tmp_path)
    first = json.loads(get(run, "/api/events?after=0", tmp_path)[2])
    journal = run / "events.jsonl"
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-01-01T00:00:00+00:00", "node": "a", "sta')
    torn = json.loads(get(run, f"/api/events?after={first['next']}", tmp_path)[2])
    assert torn["events"] == [] and torn["next"] == first["next"], (
        "a torn line must not advance the cursor")
    # ... and once it lands whole, it is delivered exactly once.
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write('tus": "done"}\n')
    healed = json.loads(get(run, f"/api/events?after={first['next']}", tmp_path)[2])
    assert len(healed["events"]) == 1
    assert healed["next"] != first["next"]


def test_the_run_token_changes_across_a_segment_boundary(tmp_path):
    """A meta-refresh page reset its client state by construction; a poll does
    not. Without the token the client holds segment A's cursor against segment
    B and gets an empty answer, forever."""
    a = page_run(tmp_path, name="2026-08-08-brief-a41c")
    b = page_run(tmp_path, name="2026-08-08-brief-b52d")
    ta = json.loads(get(a, "/api/events?after=0", tmp_path)[2])["token"]
    tb = json.loads(get(b, "/api/events?after=0", tmp_path)[2])["token"]
    assert ta and tb and ta != tb
    for path in ("/api/state", "/api/node/produce", "/api/evidence", "/api/question"):
        assert json.loads(get(a, path, tmp_path)[2])["token"] == ta, path


# ----------------------------------------------------- trace integrity at L0

def test_a_tampered_journal_renders_broken_on_the_landing_view(tmp_path):
    """The four-way rule is worthless three levels down."""
    run = page_run(tmp_path, chained=True)
    lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    doc = json.loads(lines[2])
    doc["status"] = "skipped"
    lines[2] = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    (run / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert mission_server.chain_chip(run)["cls"] == "crit"
    assert "BROKEN" in get(run, "/", tmp_path)[2].decode("utf-8")


def test_a_fresh_run_says_nothing_to_verify(tmp_path):
    run = page_run(tmp_path)
    (run / "events.jsonl").unlink()
    assert mission_server.chain_chip(run)["text"] == "nothing to verify yet"


def test_an_unchained_journal_says_unchained(tmp_path):
    run = page_run(tmp_path)  # hand-stamped events carry no `h`
    assert "unchained" in mission_server.chain_chip(run)["text"]


def test_a_verified_chain_says_so(tmp_path):
    run = page_run(tmp_path, chained=True)
    assert mission_server.chain_chip(run)["cls"] == "good"


# ---------------------------------------------- L0, with JavaScript disabled

def test_l0_renders_server_side(tmp_path):
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    head, _, _script = body.partition("<script>")
    assert "step 2 of 4" in head                  # the headline, from mission_view
    assert "needs you" in head                    # a glossary word
    assert "approve the brief" in head            # the label sidecar
    assert "agent tasks used" in head and "9 of 25" in head   # the spend figure
    assert "the same thing as a table" in head    # the table twin, not behind JS
    assert "Decisions are not made here" in head
    # And the twin is not merely PRESENT but visible: the client hides one view
    # on load, so a `hidden` attribute in the served HTML would be a fallback
    # that only works when the thing it falls back from does.
    assert '<div id="l1">' in head


def test_nothing_the_old_page_showed_is_gone(tmp_path):
    """Batch 1 (mission-ux work order §5.2) predicted this test would trip,
    and that is its job. Nothing was REMOVED: the two cost disclosures became
    one disclosure with a mode switch (both modes still server-rendered,
    reachable, and stacked with JS off), and every meter fact moved into the
    agent-tasks tile. The assertions below track the words to their new
    homes."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "ACTIVITY" in body
    assert "every attempt" in body and "kept only" in body
    assert "history: every attempt is counted" in body    # the mode tag lines
    assert "head: kept attempts only" in body             # (cost_lines, shared with the TUI)
    assert "what happened at each step" in body
    assert "NEEDS YOU" in body


def test_the_page_survives_an_empty_run_root(tmp_path):
    assert "no run yet" in mission_server.render_page(None, ROOT, tmp_path)
    assert get(None, "/", tmp_path)[0] == 200


def test_l0_row_set_matches_mission_rows(tmp_path):
    run = page_run(tmp_path)
    board = mission_server.render_board(run, ROOT)
    expected = [nid for nid, _ in mv.mission_rows(run, repo_root=ROOT) if nid]
    assert [r["node_id"] for r in mv.step_rows(run, ROOT)] == expected
    for node_id in expected:
        assert mv.label_for(mv.load_labels(run, ROOT), node_id) in board


def test_a_note_row_survives_the_switch_to_the_timeline(tmp_path):
    """`mission_rows` injects a node's mission.txt first line as an extra row;
    L1 has no such row. So a node with a note carries a MARKER, or the switch
    silently loses content."""
    run = page_run(tmp_path)
    assert "read 40 files" in mission_server.render_board(run, ROOT)
    timeline = mission_server.render_timeline(
        mission_server.waterfall(run, ROOT, now=PAGE_NOW))
    assert "this step left a note" in timeline
    assert "read 40 files" in timeline            # and the text itself, in the twin


# -------------------------------------- L1: the waterfall and its table twin

def test_a_healed_nodes_segments_sum_to_its_table_duration(tmp_path):
    """One segment per interval, and the table sums the same intervals — the
    picture and the number are one walk, so they cannot disagree."""
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    row = next(r for r in wf["rows"] if r["node_id"] == "produce")
    assert len(row["segments"]) == 2, "a merged span would draw the idle time as work"
    drawn = sum(s["width"] for s in row["segments"]) / 100.0 * wf["span_s"]
    assert row["worked"] == "7m00s"               # 4 minutes plus 3 minutes
    assert abs(drawn - 7 * 60) < 1.0, "what is drawn is what is summed"


def test_every_waterfall_value_is_in_the_table_twin(tmp_path):
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    table = mission_server.render_timeline(wf).split("the same thing as a table")[1]
    for row in wf["rows"]:
        assert row["label"] in table
        assert row["word"] in table
        if row["started"]:
            assert row["started"] in table
        if row["worked"]:
            assert row["worked"] in table
        for seg in row["segments"]:
            # a tip is a value the chart shows; the twin must show it too, or it
            # is only reachable by hovering
            assert not seg["tip"] or seg["tip"] in table, seg["tip"]


def test_a_node_that_never_ran_sorts_last_with_an_empty_track(tmp_path):
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    assert wf["rows"][-1]["node_id"] == "deliver"
    assert wf["rows"][-1]["segments"] == []


def test_rows_are_ordered_by_first_run(tmp_path):
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    assert [r["node_id"] for r in wf["rows"]][:2] == ["produce", "render-evidence"]


def test_a_duration_is_drawn_only_where_it_carries_something(tmp_path):
    """A number on every bar is noise; the running step and any stopped step
    are the two places it is the answer to the question being asked."""
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    tipped = {r["node_id"] for r in wf["rows"] if any(s["tip"] for s in r["segments"])}
    assert tipped == set(), "nothing is running or stopped in this fixture"

    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["produce"]["status"] = "failed"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    wf2 = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    row = next(r for r in wf2["rows"] if r["node_id"] == "produce")
    assert all(s["tip"] for s in row["segments"])


def test_the_axis_and_the_gridlines_share_the_track_column():
    """A real defect caught by rendering the mockup: ticks in the CARD's
    coordinate space do not line up with bars in the TRACK's."""
    css = mission_server.CSS
    assert ".wf-scale{position:absolute;left:calc(var(--gutter) + 14px)" in css
    assert "margin-left:calc(var(--gutter) + 14px)" in css
    assert "min-width:3px" in css                 # a fast step cannot vanish
    assert "border-radius:4px" in css             # a span, rounded BOTH ends


def test_the_timeline_still_draws_for_a_three_node_flow(tmp_path):
    """No minimum step count. L1 is opt-in: if the reader asked for every step,
    three bars is the honest answer to what they asked — and the cockpit's own
    canonical flows are three and four nodes."""
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    del state["nodes"]["deliver"]
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    assert wf["plotted"] is True and len(wf["rows"]) == 3


# ------------------------------------------------------ interaction (§4.6.5)

def test_hover_and_keyboard_focus_show_the_same_thing(tmp_path):
    """`title` shows on hover only, which would put a value behind a pointer.
    Every bar is focusable and carries the same text three ways: as its
    accessible name, as a hint shown on hover, and as a hint shown on focus."""
    run = page_run(tmp_path)
    timeline = mission_server.render_timeline(
        mission_server.waterfall(run, ROOT, now=PAGE_NOW))
    assert timeline.count('tabindex="0"') == 3        # one per segment
    assert 'role="img"' in timeline and "aria-label=" in timeline
    # the hint is the VISUAL rendering of the accessible name, so it is hidden
    # from assistive tech — otherwise a focused bar is announced twice
    assert '<span class="hint" aria-hidden="true">' in timeline
    css = mission_server.CSS
    assert ".seg:hover .hint,.seg:focus-visible .hint{display:block}" in css
    assert ".seg:focus-visible{outline:" in css


def test_a_bars_hit_area_is_bigger_than_the_bar(tmp_path):
    """A 12px bar is not a target. The hit area takes in the 2px gaps and
    reaches ~28px, as a pseudo-element so it costs no layout."""
    css = mission_server.CSS
    assert ".seg::before{content:\"\";position:absolute;inset:-8px -2px" in css


def test_a_hint_on_a_late_bar_cannot_overflow_the_plot(tmp_path):
    """One of the four defects rendering the mockup caught. A bar past the
    midpoint anchors its hint to the right edge instead of the left."""
    run = page_run(tmp_path)
    # `now` just after the last step ended, so its bar sits in the right half.
    wf = mission_server.waterfall(run, ROOT, now=T0 + timedelta(minutes=11))
    late = [s for r in wf["rows"] for s in r["segments"] if s["left"] + s["width"] > 60]
    assert late, "the fixture needs a bar in the right half for this to mean anything"
    timeline = mission_server.render_timeline(wf)
    assert "seg good end" in timeline
    css = mission_server.CSS
    assert ".seg.end .hint{left:auto;right:0}" in css
    # The TIP is the other element that hangs off a bar end, and it overflowed
    # the card for a bar reaching the right edge — caught by screenshotting the
    # page, not by any assertion here.
    assert ".seg.end .tip{left:auto;right:calc(100% + 8px)}" in css


def test_a_skipped_step_reads_as_deliberately_absent(tmp_path):
    """An empty track looks like `not yet` unless a skipped one is visibly
    dimmer than a waiting one; the icon and the word carry it, this helps."""
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"]["status"] = "skipped"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    timeline = mission_server.render_timeline(
        mission_server.waterfall(run, ROOT, now=PAGE_NOW))
    assert '<div class="wf-track skip">' in timeline
    assert ".wf-track.skip::before{opacity:" in mission_server.CSS


def test_the_stylesheet_has_no_stray_control_characters():
    """`\\203A` inside a PYTHON string is an OCTAL escape, so a CSS escape
    written that way reached the browser as U+0083 plus a literal `A` and every
    disclosure triangle rendered as tofu. Nothing in the sheet says it is
    wrong; only looking at it did."""
    import re
    for name, text in (("CSS", mission_server.stylesheet()), ("JS", mission_server.JS)):
        stray = re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", text)
        assert not stray, f"{name} carries control characters: {stray!r}"


def test_a_card_heading_is_a_card_heading_wherever_it_sits(tmp_path):
    """`.card>h2` did not match the one h2 that lives inside `.cardhead`, so
    "the steps" rendered at the browser's default size while every other card
    heading was 11.5px muted."""
    assert ".card>h2,.card .cardhead h2{font-size:11.5px" in mission_server.CSS


def test_no_row_animation_fires_on_every_refresh():
    """A fade-in on each step row re-fires on every swap, because the swap
    recreates the elements — the whole list flashed once a second. The stable
    tail slot is what actually prevents the chrome jump."""
    assert "rowin" not in mission_server.CSS


def test_the_tail_counters_occupy_a_stable_slot(tmp_path):
    """Every completion removes a row and increments `N finished`, at 1 Hz. If
    the counter line only appeared once there was something to count, the
    chrome below it would jump the first time a step finished."""
    run = page_run(tmp_path)
    board = mission_server.render_board(run, ROOT)
    assert board.count('class="tail"') == 1
    assert "min-height:29px" in mission_server.CSS

    # ...including when there is nothing to count yet.
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    for rec in state["nodes"].values():
        rec["status"] = "running"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert mv.collapse_tail(run, ROOT) == []
    assert mission_server.render_board(run, ROOT).count('class="tail"') == 1


def test_the_live_pulse_respects_prefers_reduced_motion():
    css = mission_server.CSS
    assert "@media (prefers-reduced-motion:reduce){.live .dot{animation:none}}" in css


# --------------------------------- the table view is also the fallback (§4.6.6)

def test_forced_colors_and_print_fall_to_the_table_view():
    """Both are contexts where a positioned bar says nothing. The twin carries
    every value a bar carries, which is why it exists."""
    css = mission_server.CSS
    block = css.split("@media print,(forced-colors:active){")[1].split("\n}")[0]
    assert ".wf-plot,.stack,.track,.ceil{display:none}" in block
    assert "#l0,#l1{display:block!important}" in block, "ID selectors beat [hidden]"
    assert ".viewswitch{display:none}" in block


def test_the_table_twin_is_open_by_default(tmp_path):
    """A `<details>` a browser has not been told to open prints closed."""
    run = page_run(tmp_path)
    timeline = mission_server.render_timeline(
        mission_server.waterfall(run, ROOT, now=PAGE_NOW))
    assert '<details id="table-twin" open><summary>the same thing as a table' in timeline


# ---------------------------------------------------- the stat row (§4.6.4)

def test_the_fourth_stat_tile_is_chosen_mechanically(tmp_path):
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    # one approval, and only it is left: `steps_to_decision` says 1
    assert "your decision" in body and ">next<" in body

    # no approval in the graph at all: the number would be undefined, so the
    # tile falls back to something that is always countable
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["approve"]["role"] = "work"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "sent back for rework" in body


def test_a_large_standalone_number_is_not_tabular(tmp_path):
    """`tabular-nums` is for the table's columns and the axis ticks."""
    css = mission_server.CSS
    tile = css.split(".tile .v{")[1].split("}")[0]
    assert "tabular-nums" not in tile
    assert "font-variant-numeric:tabular-nums" in css.split(".tick{")[1].split("}")[0]


# ------------------------------------------- the colour and icon contract

def test_the_status_map_keys_are_exactly_the_glossary():
    assert set(mission_server.STATUS_CLASS) == set(mv.GLOSSARY)
    assert set(mission_server.STATUS_CLASS) == set(mv.COST_ICON)


def test_running_takes_no_status_hue_and_absent_states_take_none():
    """`running` is not a severity; `pending` and `skipped` draw an empty
    track. Painting either as a severity would misstate it."""
    assert mission_server.STATUS_CLASS["running"] == "run"
    assert mission_server.STATUS_CLASS["pending"] == "mut"
    assert mission_server.STATUS_CLASS["skipped"] == "mut"


def test_a_decorated_word_keeps_its_base_status_colour(tmp_path):
    """"sent back for rework (1 of 2)" is NOT a glossary entry — `node_word`
    synthesizes it whenever heal_round > 0, and it appends map counters too.
    Rework is a MODIFIER: the row keeps its base status and the redone segments
    draw `ser`. The counter is text, never colour."""
    run = page_run(tmp_path)
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    row = next(r for r in wf["rows"] if r["node_id"] == "produce")
    assert row["word"] == "done (sent back once)"
    assert row["cls"] == "good", "the row keeps its base status"
    assert [s["cls"] for s in row["segments"]] == ["ser", "good"]


def test_an_unknown_status_renders_muted_with_the_raw_string(tmp_path):
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"]["status"] = "quarantined"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    row = next(r for r in mv.step_rows(run, ROOT, collapsed=False)
               if r["node_id"] == "deliver")
    assert row["word"] == "quarantined"           # node_word falls back to the raw string
    assert mission_server.STATUS_CLASS.get(row["status"], "mut") == "mut"
    assert row["icon"] == "○"


def test_the_cost_stack_hexes_are_the_validated_ones():
    """Recorded output of
       node scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500" \\
            --mode dark --surface "#1a1a19"
    ALL CHECKS PASS; worst adjacent CVD separation dE 8.4 (protan)."""
    assert mission_server.COST_HEX == ("#3987e5", "#d95926", "#199e70", "#c98500")
    assert mission_server.COST_SERIES == ("input", "output", "cache read", "cache write")
    assert len(mission_server.COST_HEX) == len(mission_server.COST_FIELDS)


def test_the_palette_has_exactly_one_home():
    """The stylesheet's cost slots are generated from COST_HEX, and the stack
    and legend reference the slots — so the tuple the validator output is
    pinned against is the only place a hex is written."""
    sheet = mission_server.stylesheet()
    for i, hexv in enumerate(mission_server.COST_HEX):
        assert f"--c{i + 1}:{hexv};" in sheet
    assert "COST_VARS" not in sheet
    # no raw cost hex written out in the RULES (the recorded validator
    # invocation in the comment is documentation, and is meant to be there)
    import re
    rules = re.sub(r"/\*.*?\*/", "", mission_server.CSS, flags=re.S)
    assert "#d95926" not in rules


def test_the_surface_the_palette_was_validated_against_is_the_one_it_renders_on():
    """A surface change silently invalidates every contrast figure. This pins
    the surface to the value in the comment recording the validator run."""
    sheet = mission_server.stylesheet()
    assert "--surface:#141517;" in sheet
    assert '--surface "#141517"' in sheet, "the recorded validator invocation"
    assert "ALL CHECKS PASS" in sheet


def test_no_categorical_hue_is_used_as_chrome():
    """Slot 1 is the sequential default and carries the spend meter's single
    magnitude; slots 2-4 mean input/output/cache in the stack. A categorical
    hue doing duty as chrome would give one colour two meanings on one page."""
    import re
    sheet = mission_server.stylesheet()
    body = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    users = [ln.strip() for ln in body.splitlines() if re.search(r"var\(--c[1-4]\)", ln)]
    assert len(users) == 1 and users[0].startswith(".fill{"), users


# ---------------------------------------------- the client renders nothing

def test_no_step_word_and_no_time_string_is_rendered_by_client_code():
    """Every word and every formatted time comes from `mission_view` over the
    wire. A formatter in the browser is a glossary pytest cannot execute."""
    js = mission_server.JS
    for word in mv.GLOSSARY.values():
        assert word not in js, f"the client renders the word {word!r}"
    for api in ("Date", "toLocale", "getHours", "getMinutes", "Intl", "padStart",
                "toFixed"):
        assert api not in js, f"the client formats with {api}"


def test_the_client_only_swaps_server_rendered_html():
    js = mission_server.JS
    assert "doc.html" in js and "innerHTML" in js
    assert "doc.token !== token" in js and "cursor = '0'" in js


def test_the_poll_holds_the_previous_render_rather_than_a_skeleton():
    """The page polls every second; a skeleton flash would be the dominant
    visual experience of a healthy run."""
    assert "stale" in mission_server.JS
    assert ".wrap.stale{opacity:" in mission_server.CSS


# ------------------------------------------- the heartbeat is the cheap route

def test_the_heartbeat_is_the_events_route_and_it_gates_the_refresh():
    """The first cut polled `/api/state` once a second and never called
    `/api/events` at all: 128 ms and 227 KB per tick on a 40-node run, forever,
    for a page that had mostly not changed, while the cursor sat in a dataset
    attribute nothing read. A dead channel that looks wired is worse than no
    channel (`cockpit.ps1:302-308`)."""
    js = mission_server.JS
    assert "fetch('api/events?after=' + cursor" in js
    assert "setInterval(tick" in js, "the interval drives the CHEAP route"
    # and /api/state is fetched only from refresh(), which is gated
    assert js.count("fetch('api/state'") == 1
    assert "if (dirty || (doc.live && quiet >= IDLE_REFRESH_TICKS)) {" in js


def test_a_quiet_tick_costs_almost_nothing(tmp_path):
    run = page_run(tmp_path)
    total = json.loads(get(run, "/api/events?after=0", tmp_path)[2])["next"]
    body = get(run, f"/api/events?after={total}", tmp_path)[2]
    doc = json.loads(body)
    assert doc["events"] == [] and doc["next"] == total
    assert len(body) < 160, "a quiet second must not ship a page"


def test_a_quiet_tick_does_not_read_the_journal_prefix(tmp_path):
    """S1.1's whole point, asserted in BYTES rather than in milliseconds: a
    heartbeat with nothing to report must not pay for the journal it has
    already read. Before the byte cursor this read 732 KB against a 740 KB
    journal, once a second, forever."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mission_bench", CONTRIB / "mission_bench.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    run = page_run(tmp_path)
    journal = run / "events.jsonl"
    with open(journal, "a", encoding="utf-8") as fh:
        for i in range(3000):
            fh.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "kind": "transition",
                                 "node": "a", "status": "running", "pad": "x" * 80}) + "\n")
    size = journal.stat().st_size
    assert size > 300_000, "precondition: a journal worth not re-reading"

    cursor = mission_server.mission_cursor.initial(run)
    with bench.counting() as c:
        events, nxt = mission_server._events_after(
            run, mission_server.mission_cursor.parse_cursor(cursor))
    assert events == [] and nxt == cursor
    assert c.bytes < size / 10, (
        f"a quiet tick read {c.bytes} bytes of a {size}-byte journal - the "
        "cursor is not being honoured")


def test_the_events_route_says_whether_the_clock_is_ticking(tmp_path):
    """Only a running node makes the page change with no journal entry behind
    it, so that is the one extra bit the heartbeat carries. It is `live`, not
    `running`: a GLOSSARY word in the client would be the second glossary this
    design forbids, and the test that catches that is a substring check with no
    exceptions in it."""
    run = page_run(tmp_path)
    assert json.loads(get(run, "/api/events?after=0", tmp_path)[2])["live"] is False
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"]["status"] = "running"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert json.loads(get(run, "/api/events?after=0", tmp_path)[2])["live"] is True


def test_a_skipped_refresh_is_not_forgotten():
    """A reader who leaves text selected would otherwise consume the cursor
    advance and never see the refresh it should have caused — the page sits
    stale until the NEXT event, which on a finished run never comes."""
    js = mission_server.JS
    assert "dirty = true" in js
    assert "if (refresh()) { dirty = false; quiet = 0; }" in js
    assert "return false;" in js, "refresh() must report that it bailed"


def test_the_offline_note_is_above_the_fold(tmp_path):
    """A `you are looking at stale data` warning under 200 KB of content is a
    warning nobody sees."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.index('id="offline-note"') < body.index('class="hero"')
    # and the live dot stops pretending, in presentation not in words
    assert "body.offline .live .dot{animation:none" in mission_server.CSS
    assert "classList.toggle('offline', down)" in mission_server.JS


def test_the_timeline_narrows_for_a_phone():
    """`--host` is advertised for phone use; a 250px label gutter on a 375px
    screen leaves no track to draw on."""
    css = mission_server.CSS
    assert "@media (max-width:700px){.wf-plot{--gutter:118px}" in css
    assert ".seg .hint{white-space:normal;max-width:60vw}" in css


def test_a_new_run_discards_the_old_cursor_rather_than_its_next(tmp_path):
    """`doc.next` is computed against the cursor the client sent, so across a
    segment boundary it describes the wrong run. Keeping it would leave the
    client asking for `after=400` of a twelve-event run — the exact failure the
    run token exists to prevent."""
    js = mission_server.JS
    branch = js.split("if (doc.token !== token) {")[1].split("return;")[0]
    assert "cursor = '0'" in branch and "refresh()" in branch
    assert "doc.next" not in branch, "the old run's next must not survive the boundary"


# --------------------------------- the page is not taken away from its reader

def test_a_refresh_never_lands_while_the_reader_is_selecting_text():
    """`innerHTML` destroys the selection. On a page whose whole purpose is
    reading evidence, wiping a selection once a second means you cannot copy a
    path out of the block you are being asked to decide from."""
    js = mission_server.JS
    assert "function selecting()" in js
    assert "if (busy || selecting()) return false;" in js
    assert "getSelection" in js


def test_open_drawers_focus_and_the_key_echo_survive_a_refresh():
    js = mission_server.JS
    assert "details[open][id]" in js, "open drawers are re-opened after the swap"
    assert "document.activeElement && document.activeElement.id" in js
    assert "if (echoShown)" in js, "the a/r sentence is not wiped a second later"


def test_every_details_the_reader_can_open_carries_an_id(tmp_path):
    """State can only be restored across a swap for elements that can be named."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    import re
    opens = re.findall(r"<details([^>]*)>", body)
    assert opens
    for attrs in opens:
        assert "id=" in attrs, f"<details{attrs}> cannot survive a refresh"


def test_polling_stops_while_the_tab_is_hidden():
    assert "document.visibilityState === 'hidden'" in mission_server.JS


def test_a_page_that_stops_hearing_from_the_run_says_so(tmp_path):
    """The first cut swallowed every failure and went on pulsing its `live` dot
    over frozen data. The guide promises blank never means broken; a silently
    stale board is worse than a blank one."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert 'id="offline-note"' in body
    assert html.escape(mission_server.OFFLINE_SENTENCE) in body
    assert 'id="offline-note" role="status" hidden' in body, "hidden until it is true"
    js = mission_server.JS
    assert "if (++fails >= 3) { offline(true); }" in js, "three strikes, not one blip"
    assert "note.hidden = !down" in js


# --------------------------------------------------- one read, not five

def test_a_refresh_collects_usage_exactly_once(tmp_path, monkeypatch):
    """`collect_run` walks every phase dir and parses every envelope. The page
    draws a meter, a spend block, a cost stack and two cost panels from it; the
    first cut computed it five times per render, at 1 Hz."""
    import cost_report
    calls = []
    real = cost_report.collect_run
    monkeypatch.setattr(cost_report, "collect_run",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    run = page_run(tmp_path)
    mission_server.render_wrap(run, ROOT, tmp_path, now=PAGE_NOW)
    assert len(calls) == 1, f"collect_run ran {len(calls)} times in one render"


def test_the_spend_card_does_not_repeat_the_meter(tmp_path):
    """Two identical sentences, adjacent, read as two different numbers."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count("agent tasks used 9 of 25") == 0
    assert "agent tasks used" in body and "9 of 25" in body   # once, in the meter
    assert "of node time" in body                             # the qualifiers stay


# ----------------------------------------------------------------- the meter

def test_the_meter_shows_no_denominator_without_a_declared_cap(tmp_path):
    """Rewritten to target the TILE (Batch 1, F5): the meter card is gone and
    its promises moved, not vanished. No declared cap -> the count with no
    denominator, no bar, and the honest sentence instead of the consent one."""
    meter = mission_server.spend_meter([{"token_spawns": 9}], [None])
    assert meter["cap"] is None and meter["pct"] is None
    assert meter["label"] == "agent tasks used 9"

    run = page_run(tmp_path, cap=None)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "of 25" not in body
    tile = body.split('<div class="k">agent tasks used</div>')[1].split("</div></div>")[0]
    assert '<div class="v">9</div>' in tile
    assert "track" not in tile, "no bar without a denominator"
    assert "declares no ceiling" in tile


def test_the_meter_lives_in_the_agent_tasks_tile(tmp_path):
    """F5: `agent tasks — 10` (tile) beside `agent tasks used 10 of 40`
    (meter card) was one number rendered three times. The tile now IS the
    meter — value, bar, ceiling mark — and the consent sentence, a
    guide-level promise, sits in the tile foot adjacent to the denominator
    it glosses (D5)."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    tile = body.split('<div class="k">agent tasks used</div>')[1].split('<div class="tile">')[0]
    assert '<div class="v">9 of 25</div>' in tile
    assert 'class="track"' in tile and 'class="ceil"' in tile
    assert "you agreed to before anything started" in tile
    # once on the whole page: one number per fact
    assert body.count("you agreed to before anything started") == 1
    assert body.count('class="track"') == 1


def test_the_cost_card_is_one_disclosure_with_two_views(tmp_path):
    """F6: `history` and `head` are one fact with a mode. Both bodies are
    server-rendered inside ONE <details>; JS hides one; with JS off both
    render stacked — each opening with its own mode tag line, so the stack
    is legible. The switch words are the guide's ("every attempt",
    "kept only"), entered in the same commit."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count("<details id=\"cost-tree\">") == 1
    assert "<details id=\"cost-history\">" not in body   # the siblings are gone
    assert "<details id=\"cost-head\">" not in body
    tree = body.split('<details id="cost-tree">')[1].split("</details>")[0]
    assert '>every attempt</button>' in tree and '>kept only</button>' in tree
    assert '<pre id="cost-history">' in tree and '<pre id="cost-head">' in tree
    # served without `hidden`: the no-JS reader gets both, stacked
    assert 'hidden' not in tree.split('<pre id="cost-history">')[0].rsplit("<", 1)[-1]
    assert "history: every attempt is counted" in tree
    assert "head: kept attempts only" in tree


def test_the_view_switch_is_generic_over_groups():
    """The l0/l1 machinery generalised rather than being copied: membership
    comes from the server-rendered buttons, and the client still renders no
    word."""
    js = mission_server.JS
    assert "function show(group, which)" in js
    assert '.viewswitch[data-group="' in js
    assert "restoreAll(pressed)" in js, "the reader's chosen views survive a swap"


def test_the_meter_degrades_to_of_at_least_across_segments():
    """Caps sum across segments; one segment declaring none makes the ceiling
    admittedly incomplete rather than precise-looking and wrong. The guard that
    fixed a real `used 38 of 25`."""
    meter = mission_server.spend_meter(
        [{"token_spawns": 20}, {"token_spawns": 18}], [25, None])
    assert meter["label"] == "agent tasks used 38 of at least 25"
    assert meter["over"] is True


def test_the_meter_has_no_severity_ramp():
    """Nothing in the run dir says 80% of a ceiling is a warning, and inventing
    that threshold would be the first editorial judgment on a view that is
    summary-free by construction."""
    assert mission_server.spend_meter([{"token_spawns": 24}], [25])["over"] is False
    assert mission_server.spend_meter([{"token_spawns": 25}], [25])["over"] is True
    assert mission_server.CSS.count(".fill.over") == 1


# --------------------------------- evidence, the question card, and the keys

def test_the_evidence_is_quoted_and_is_not_stale(tmp_path):
    run = page_run(tmp_path)
    doc = json.loads(get(run, "/api/evidence", tmp_path)[2])
    assert doc["evidence"]["stale"] is False
    assert "--reversible" in doc["evidence"]["text"]
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "--reversible" in body
    assert "needs you — a decision" in body


def test_the_question_card_is_served_verbatim(tmp_path):
    run = page_run(tmp_path, evidence=False, question=True)
    doc = json.loads(get(run, "/api/question", tmp_path)[2])
    assert doc["question"] == "Which of the two schemas is authoritative?\n"
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "Which of the two schemas is authoritative?" in body
    assert "needs you — a question" in body


def test_a_and_r_say_where_the_decision_happens(tmp_path):
    """A silent no-op at a decision moment is the worst available behaviour.
    The sentence is rendered BY THE SERVER; the key handler only unhides it."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count(html.escape(mission_server.TERMINAL_SENTENCE)) == 2
    assert 'id="key-echo"' in body

    js = mission_server.JS
    handler = js.split("addEventListener('keydown'")[1].split("});")[0]
    assert "key-echo" in handler and "hidden = false" in handler
    assert "fetch" not in handler, "the keys must not reach the server"


# ------------------------------------------------------------------ L2 and L3

def test_the_step_drawer_names_its_step_in_l0s_words(tmp_path):
    run = page_run(tmp_path)
    doc = json.loads(get(run, "/api/node/produce", tmp_path)[2])
    assert doc["label"] == "draft the sections"
    body = "\n".join(doc["lines"])
    assert "(step id:" not in body, "the identifier lives at L3"
    assert "state" in body


def test_a_row_in_either_view_opens_its_drawer_without_javascript(tmp_path):
    """A `<details>` a browser jumps into opens itself, so L2 is reachable with
    JavaScript off and `/api/node/<id>` is a route rather than the only way in."""
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count('href="#step-produce"') == 2      # once on the board, once on L1
    assert '<details id="step-produce">' in body


def test_the_drawer_never_carries_stdout_bodies(tmp_path):
    """`stdout.log` is the harness envelope, i.e. the model's whole result text.
    Names, sizes and mtimes — the decision recorded in `mission_view` stands."""
    run = page_run(tmp_path)
    (run / "phases" / "produce" / "stdout.log").write_text(
        "SECRET MODEL OUTPUT\n" * 20, encoding="utf-8")
    doc = json.loads(get(run, "/api/node/produce", tmp_path)[2])
    body = "\n".join(doc["lines"])
    assert "stdout.log" in body
    assert "SECRET MODEL OUTPUT" not in body


def test_every_l3_term_carries_a_gloss(tmp_path):
    """L3's vocabulary is pinned exactly as GLOSSARY is: the page must not
    acquire DE-facing words that nothing checks."""
    run = page_run(tmp_path)
    rows = mission_server.raw_record(run, "produce")
    assert [r["term"] for r in rows] == [
        "step id", "input-hash parts", "what moved", "record head", "record check"]
    for row in rows:
        assert row["gloss"] == mission_server.L3_GLOSSARY[row["term"]]
        assert len(row["gloss"].split()) >= 5, "a gloss is a sentence, not a synonym"


def test_l3_says_what_moved_and_what_did_not(tmp_path):
    run = page_run(tmp_path)
    moved = {r["term"]: r["value"] for r in mission_server.raw_record(run, "produce")}
    assert moved["what moved"] == "prompt.task"
    assert moved["input-hash parts"] == "config, prompt.task"
    reused = {r["term"]: r["value"]
              for r in mission_server.raw_record(run, "render-evidence")}
    assert reused["what moved"] == "nothing — it was reused"


def test_the_l3_glossary_matches_the_domain_experts_guide():
    """The same anti-drift rule the first glossary gets, for the same reason.

    CLAUDE.md makes COCKPIT-FOR-DOMAIN-EXPERTS.md binding on what may be said to
    the human, so a page word that the guide does not carry is a word nobody
    agreed to — which is exactly the defect the GLOSSARY test exists to prevent.
    """
    import re
    text = (ROOT / "docs" / "guides" / "COCKPIT-FOR-DOMAIN-EXPERTS.md").read_text(
        encoding="utf-8")
    pairs = dict(re.findall(r"^\| \*\*([a-z][a-z -]*)\*\* \| (.+?) \|$", text, re.M))
    for term, gloss in mission_server.L3_GLOSSARY.items():
        assert term in pairs, f"the guide does not carry the L3 word {term!r}"
        assert pairs[term] == gloss, f"{term}: page and guide disagree"


def test_the_retired_guide_clauses_are_gone():
    """Three clauses the page breaks, and the promise that replaces them. L3
    puts `invalidated_by` one click away, which is the restart judgment the
    guide used to say was never the reader's."""
    text = (ROOT / "docs" / "guides" / "COCKPIT-FOR-DOMAIN-EXPERTS.md").read_text(
        encoding="utf-8")
    never = text.split("## What you never have to do")[1]
    head = never.split("Three things used to be on that list")[0]
    for retired in ('Know what a "run directory" is',
                    "Work out what a number means",
                    "Decide whether something is safe to restart"):
        assert retired not in head, f"{retired!r} is broken by the page"
    assert "Nothing you need in order to decide is behind a word you do not know" in never
    # the four that stand
    for kept in ("Write or read code", "Use git", "Type a command",
                 "Remember which files matter"):
        assert kept in head


def test_the_raw_record_is_reachable_from_the_page(tmp_path):
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "show the raw record" in body
    for gloss in mission_server.L3_GLOSSARY.values():
        assert html.escape(gloss) in body, gloss


# ----------------- the blocker card and the dead driver (mission-ux Batch 0)

def _hold_lock(run: Path, pid: int) -> None:
    import socket
    (run / "lock").write_text(json.dumps(
        {"pid": pid, "hostname": socket.gethostname(),
         "started": "2026-08-08T09:02:00Z"}), encoding="utf-8")


def _dead_pid() -> int:
    import subprocess
    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(proc.stdout.strip())


def failed_run(tmp_path: Path, *, error: str = "exit code 128 (no result emitted)"):
    """page_run with the approval decided and the tail step failed — the
    release-cut shape the work order measured (§1 F2)."""
    run = page_run(tmp_path, evidence=False)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["approve"].update({"status": "done", "ended_at": _iso(11)})
    state["nodes"]["deliver"].update({"status": "failed", "attempts": 1,
                                      "error": error, "started_at": _iso(11),
                                      "ended_at": _iso(12)})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run


def test_the_blocker_card_sits_above_the_fold_on_a_failed_run(tmp_path):
    """F2: an approval gets a card above the fold; a failure got four hero
    words and a scroll hunt. Mirror of the offline-note placement test."""
    run = failed_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.index('class="card blocker"') < body.index("<h2>the steps</h2>")


def test_the_blocker_card_carries_the_error_verbatim_and_named_as_machinery(tmp_path):
    """A3: verbatim is the S2 evidence rule; NAMED as machinery is the guide's
    rule for machinery vocabulary. And the counts are glossary words — "tried
    once", never "1 attempt"."""
    run = failed_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "exit code 128 (no result emitted)" in body
    assert "the machine&#x27;s own words" in body or "the machine's own words" in body
    assert "tried once" in body
    assert "1 attempt" not in body
    assert "Ask the assistant" in body      # the guide's own next step, last
    # deliver is terminal, and saying nothing invites a scroll hunt for the
    # dependents that do not exist
    assert "nothing else is waiting on it" in body


def test_the_blocker_card_counts_the_steps_waiting_behind_the_failure(tmp_path):
    run = page_run(tmp_path, evidence=False)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["produce"].update({"status": "failed", "attempts": 2,
                                      "error": "provider said 429"})
    for nid in ("render-evidence", "approve", "deliver"):
        state["nodes"][nid].update({"status": "pending"})
        state["nodes"][nid].pop("started_at", None)
        state["nodes"][nid].pop("ended_at", None)
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "3 steps are waiting behind this one" in body
    assert "tried twice" in body


def test_a_decision_still_outranks_the_blocker_card(tmp_path):
    """Precedence (§4.2): a waiting human outranks a stalled branch; both
    render."""
    run = page_run(tmp_path)          # approve blocked, evidence present
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"].update({"status": "failed", "attempts": 1,
                                      "error": "boom"})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.index("needs you — a decision") < body.index('class="card blocker"')


def test_no_blocker_card_on_a_healthy_run(tmp_path):
    body = get(page_run(tmp_path), "/", tmp_path)[2].decode("utf-8")
    assert 'class="card blocker"' not in body


def test_a_dead_drivers_run_does_not_render_as_running(tmp_path):
    """F8: kill the driver mid-node (a session limit does exactly this on
    this machine) and state.json still says `running` — the page said
    "running" with a growing clock and a pulsing dot, forever, over a corpse.
    The guide's rule: nothing on screen may be ambiguous between fine and
    broken."""
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["approve"].update({"status": "done", "ended_at": _iso(11)})
    state["nodes"]["deliver"].update({"status": "running", "attempts": 1,
                                      "started_at": _iso(11)})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _hold_lock(run, _dead_pid())
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "stopped unexpectedly" in body
    assert "NOT alive" in body                       # the lock's words, verbatim
    assert "Ask the assistant to restart it" in body
    assert ">not running</span>" in body             # the chip stops pretending
    assert 'class="chip live"' not in body           # and the dot stops pulsing


def test_a_live_lock_keeps_a_running_run_running(tmp_path):
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"].update({"status": "running", "attempts": 1,
                                      "started_at": _iso(11)})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _hold_lock(run, os.getpid())
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "stopped unexpectedly" not in body
    assert ">running</span>" in body


def test_a_copy_that_cannot_check_liveness_says_so(tmp_path, monkeypatch):
    """D6's named absence: a cockpit copied without the lockstep package must
    not render a healthy "running" by omission — the same rule as the missing
    cost reader."""
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"].update({"status": "running", "attempts": 1,
                                      "started_at": _iso(11)})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    absent = {"state": "unavailable", "pid": None, "hostname": None,
              "line": "whether a driver is alive cannot be checked from this "
                      "copy — the lockstep package is not importable here"}
    monkeypatch.setattr(mission_server.mv, "driver_presence", lambda rd: absent)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "cannot be checked from this copy" in body
    assert "stopped unexpectedly" not in body        # it does not claim what it cannot know


def test_the_rail_does_not_say_running_over_a_dead_driver(tmp_path):
    """The rail row and the board describe the SAME run on the same page; one
    saying "running" while the other says "stopped unexpectedly" is the
    two-surfaces split the glossary tests exist to prevent. Presence changes
    with no state.json write, so the check lives OUTSIDE the row cache."""
    run = page_run(tmp_path)
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["deliver"].update({"status": "running", "attempts": 1,
                                      "started_at": _iso(11)})
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    mission_server._RAIL_MEMBERS.clear()
    mission_server._RAIL_ROWS.clear()
    rows, _ = mission_server.run_list(tmp_path, run)
    row = next(r for r in rows if r["name"] == run.name)
    assert row["word"] == "stopped unexpectedly" and row["cls"] == "bad"

    _hold_lock(run, os.getpid())
    rows, _ = mission_server.run_list(tmp_path, run)
    row = next(r for r in rows if r["name"] == run.name)
    assert row["word"] == "running", "a live driver must not be reported dead"


def test_every_css_variable_the_stylesheet_uses_is_defined():
    """F7: `var(--accent)` and `var(--raise)` were referenced and never
    defined — invalid at computed-value time, so the critical-path edge and
    the rail highlight silently painted nothing, and no test sees pixels.
    Definitions are checkable mechanically even though pixels are not; this
    closes the class."""
    import re
    css = mission_server.stylesheet()
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    assert used <= defined, f"used but never defined: {sorted(used - defined)}"
    assert {"--raise", "--accent"} <= defined     # the two that motivated this


def test_a_stale_open_interval_is_not_measured_to_now(tmp_path):
    """An interval left open by a crash belongs to a step that is NOT running.
    Measuring it to `now` grows forever: the bar stretches across the plot and
    the twin reports the age of the run dir as work."""
    run = page_run(tmp_path)
    # produce is `done`, but its last interval never got a terminal event
    (run / "events.jsonl").write_text(
        json.dumps({"ts": _iso(0), "node": "produce", "status": "running"}) + "\n",
        encoding="utf-8")
    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    row = next(r for r in wf["rows"] if r["node_id"] == "produce")
    assert row["worked"] == "", "a stale interval contributes no duration"
    assert row["segments"][0]["open"] is False, "and is not drawn as still running"

    # ...while a step that really is running still draws to now
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["produce"]["status"] = "running"
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    wf2 = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    row2 = next(r for r in wf2["rows"] if r["node_id"] == "produce")
    assert row2["worked"] == "18m00s" and row2["segments"][0]["open"] is True


# ------------------------------------------------- the page renders MODEL OUTPUT

XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script>"\'`'


def test_nothing_a_model_or_a_flow_author_controls_can_inject_markup(tmp_path):
    """Every string on this page came from somewhere else: a node label from a
    sidecar, a note from the agent, a failure message from the engine, evidence
    from a render node, a question from a gate. The page is served on loopback
    by default but `--host` is documented for phone use, so an injected script
    would run in the reader's browser against whatever else is on that origin.
    """
    run = page_run(tmp_path, question=True)
    (run / "flow.labels.json").write_text(
        json.dumps({"nodes": {"produce": XSS}}), encoding="utf-8")
    (run / "phases" / "produce" / "mission.txt").write_text(XSS, encoding="utf-8")
    (run / "approval-evidence.txt").write_text(XSS, encoding="utf-8")
    (run / "question-card.txt").write_text(XSS, encoding="utf-8")
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["nodes"]["produce"]["error"] = XSS
    state["flow_name"] = XSS
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")

    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "<script>alert(2)</script>" not in body
    assert "<img src=x" not in body
    assert 'onerror="alert(1)"' not in body
    # it is not simply absent — it is present, escaped, and readable
    assert html.escape(XSS) in body
    # The JSON routes return the text RAW, which is right — JSON is a data
    # channel, not a document. What has to be true is that nothing renders it
    # as markup: they are served as application/json with nosniff, so a browser
    # pointed straight at one shows text, and the client never puts an API
    # string into the DOM (it swaps `doc.html`, which is escaped above).
    for route in ("/api/state", "/api/node/produce", "/api/evidence", "/api/question"):
        status, ctype, _payload = get(run, route, tmp_path)
        assert status == 200 and ctype.startswith("application/json"), route
    # /api/state carries the ESCAPED html (that is what the client swaps in);
    # the data routes carry the text raw, which is correct for JSON.
    assert html.escape(XSS) in json.loads(get(run, "/api/state", tmp_path)[2])["html"]
    assert XSS in json.loads(get(run, "/api/question", tmp_path)[2])["question"]
    js = mission_server.JS
    assert js.count("innerHTML") == 1 and "wrap.innerHTML = doc.html;" in js


def test_the_waterfall_escapes_a_label_inside_an_attribute(tmp_path):
    """A bar's accessible name and its hint carry the step's label, and both sit
    inside a quoted attribute — the one place an escaping miss is exploitable
    rather than merely ugly."""
    run = page_run(tmp_path)
    (run / "flow.labels.json").write_text(
        json.dumps({"nodes": {"produce": '" onmouseover="alert(1)'}}), encoding="utf-8")
    timeline = mission_server.render_timeline(
        mission_server.waterfall(run, ROOT, now=PAGE_NOW))
    assert 'onmouseover="alert(1)"' not in timeline
    assert "&quot; onmouseover=&quot;alert(1)" in timeline


def test_the_response_carries_the_headers_that_back_the_escaping():
    """The escaping is the guarantee; these stop a miss from being exploitable,
    and they matter more once `--host` puts the page on a network. The
    load-bearing one is `connect-src 'self'`: even injected script could not
    send a run directory anywhere."""
    headers = dict(mission_server.SECURITY_HEADERS)
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'none'" in csp, "there is no form, and none may be added"
    assert "frame-ancestors 'none'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


# ------------------------------- when a reader is missing rather than empty

def _no_cost_reader(monkeypatch):
    """Make `import cost_report` fail the way a work-repo copy that left the
    file behind makes it fail. `None` in sys.modules is the interpreter's own
    "this import is blocked" mechanism, so the seam under test is the real
    import statement and not a patched function — a caller that goes back to a
    bare `import cost_report` is still caught."""
    monkeypatch.setitem(sys.modules, "cost_report", None)


def test_a_missing_cost_reader_is_named_instead_of_drawing_nothing(tmp_path, monkeypatch):
    """The defect this whole section exists for. `cost_report.py` is imported
    by bare name from a sibling file and every timing and cost figure is a
    projection of it, so a copy of the cockpit without it drew an empty
    timeline, a column of dashes, and no cost — with nothing anywhere saying
    why. The guide promises blank never means broken."""
    run = page_run(tmp_path)
    _no_cost_reader(monkeypatch)

    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    assert not wf["plotted"], "precondition: this is the state that used to be silent"
    assert all(not r["started"] and not r["worked"] and not r["tries"] for r in wf["rows"])
    assert wf["note"]["scope"] == "all"

    timeline = mission_server.render_timeline(wf)
    assert html.escape(mission_server.COST_READER_SENTENCE) in timeline
    # said above the twin, where the dashes are, not below it
    assert (timeline.index(html.escape(mission_server.COST_READER_SENTENCE))
            < timeline.index("the same thing as a table"))

    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count(html.escape(mission_server.COST_READER_SENTENCE)) == 2, \
        "both places the missing thing would have been drawn: the timeline and the cost block"


def test_the_missing_reader_note_names_a_file_and_a_python(tmp_path, monkeypatch):
    """Two causes, one symptom: the file was not copied, or it was copied onto
    a Python below 3.11, where `cost_report`'s `import tomllib` fails. The
    sentence has to carry both or it sends half the readers hunting."""
    assert "cost_report.py" in mission_server.COST_READER_SENTENCE
    assert "3.11" in mission_server.COST_READER_SENTENCE
    assert "not zero" in mission_server.COST_READER_SENTENCE, \
        "an empty chart reads as 'the run did nothing' until this says otherwise"
    run = page_run(tmp_path)
    _no_cost_reader(monkeypatch)
    _, detail = mission_server.cost_absence(run, None)
    assert "cost_report" in detail, "the hover carries the real exception"


def test_a_missing_field_map_costs_the_figures_but_not_the_timings(tmp_path, monkeypatch):
    """A reader with no `cost-fields.toml` still knows when every step ran. So
    the timeline must NOT carry this note — only the cost block does."""
    import cost_report
    run = page_run(tmp_path)
    monkeypatch.setattr(cost_report, "load_field_maps", lambda _: {})

    wf = mission_server.waterfall(run, ROOT, now=PAGE_NOW)
    assert wf["plotted"] and not wf["note"], "timings do not depend on the field map"
    assert html.escape(mission_server.COST_FIELDS_SENTENCE) not in \
        mission_server.render_timeline(wf)

    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert body.count(html.escape(mission_server.COST_FIELDS_SENTENCE)) == 1
    assert "cost-fields.toml" in mission_server.reader_note(run)["detail"]


def test_the_empty_cost_block_tells_setup_apart_from_a_harness_limit(
        tmp_path, monkeypatch):
    """Four events used to print the same seven words. Three are a setup
    problem fixable in a minute; the fourth is a limit no configuration lifts —
    `copilot-cli` has no JSON output mode, so no usage envelope is ever written.
    A reader who cannot tell those apart either chases a phantom or gives up on
    a number that was one file away.

    The field map is INJECTED. `cost_absence` branches on whether
    `contrib/cost-fields.toml` exists, and that file is gitignored — so this
    test passed on a machine that happened to have one and failed on a clean
    checkout of the tag (found downstream). A test whose outcome depends on
    an ambient untracked file is testing the machine.
    """
    run = page_run(tmp_path)
    monkeypatch.setattr(mission_server, "reader_note", lambda *a, **k: {})

    unmapped, _ = mission_server.cost_absence(
        run, {"rows": [{"note": "no field map (copilot)"}]})
    assert "copilot" in unmapped, "name the harness, or the fix is a guessing game"
    assert "cost-fields.toml" in unmapped

    limit, detail = mission_server.cost_absence(run, {"rows": [{"note": "no envelope"}]})
    assert "not a setup problem" in limit
    assert "copilot-cli" in detail
    assert "cost-fields.toml" not in limit, "there is no file to add; saying so wastes a trip"

    assert mission_server.cost_absence(run, {"rows": [{"note": ""}]})[0] == \
        "No usage was reported for this run."


def test_the_cost_panel_itself_distinguishes_a_missing_reader(tmp_path, monkeypatch):
    """`mission_view.cost_lines` feeds the page's two cost panes AND the TUI and
    cockpit.ps1, and it used to print both possibilities in one line — leaving
    the choosing to a reader chosen for not being a programmer. A mid-replace
    `state.json` fixes itself within the second; a missing file never does."""
    run = page_run(tmp_path)
    _no_cost_reader(monkeypatch)
    lines = " ".join(mv.cost_lines(run))
    assert "not installed" in lines and "3.11" in lines
    assert "mid-replace" not in lines, "that is the other cause, and it is not this one"


# ------------------------------------------- the wide plot and the run rail


def mini_run(where: Path, *, nodes: dict, events: list[dict],
             flow: dict | None = None) -> Path:
    """The smallest run dir the page will render: state, journal, flow."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "state.json").write_text(
        json.dumps({"flow_name": where.name.rsplit("-", 1)[0],
                    "started_at": events[0]["ts"] if events else None,
                    "nodes": nodes}), encoding="utf-8")
    (where / "events.jsonl").write_text(
        "".join(json.dumps(ev) + "\n" for ev in events), encoding="utf-8")
    if flow is not None:
        (where / "flow.tg.json").write_text(json.dumps(flow), encoding="utf-8")
    return where


def _span(minutes: int) -> str:
    return (datetime(2026, 8, 10, 5, 0, tzinfo=timezone.utc)
            + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def test_a_finished_run_does_not_stretch_its_plot_to_now(tmp_path):
    """The axis follows the RUN, not the clock.

    `t1 < now` is true of every run that has ever finished, so the old
    condition extended a completed run's span to the present: a four-minute run
    looked at two hours later drew itself in the leftmost 3% with two hours of
    empty grid beside it. Invisible while the plot was 1080px wide, and obvious
    the moment it was widened — which is the argument for rendering the page and
    looking at it, not for one more unit test of the same function.
    """
    run = mini_run(tmp_path / "flow-a", nodes={
        "a": {"node_id": "a", "status": "done", "attempts": 1,
              "started_at": _span(0), "ended_at": _span(4)}},
        events=[{"ts": _span(0), "node": "a", "status": "running"},
                {"ts": _span(4), "node": "a", "status": "done"}])
    late = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)   # four hours later
    wf = mission_server.waterfall(run, tmp_path, now=late)
    assert wf["plotted"]
    assert 230 <= wf["span_s"] <= 250, (
        f"span is {wf['span_s']}s; a 4-minute run must not draw a 4-hour axis")
    ends = [s["left"] + s["width"] for r in wf["rows"] for s in r["segments"]]
    assert ends and max(ends) > 99.0, "the last bar should reach the right edge"


def test_a_running_run_still_extends_to_now(tmp_path):
    """The other half: a live run's bar must keep growing, or a step that has
    been working for ten minutes draws a stub."""
    run = mini_run(tmp_path / "flow-b", nodes={
        "a": {"node_id": "a", "status": "running", "attempts": 1, "started_at": _span(0)}},
        events=[{"ts": _span(0), "node": "a", "status": "running"}])
    wf = mission_server.waterfall(run, tmp_path, now=datetime(
        2026, 8, 10, 5, 10, tzinfo=timezone.utc))
    assert 590 <= wf["span_s"] <= 610


def test_rollback_markers_appear_where_the_tree_was_restored(tmp_path):
    """A heal round is the one event that makes earlier bars stop describing
    files that still exist. Two defects this session turned on exactly that,
    and both were found by hand-reading events.jsonl."""
    run = mini_run(tmp_path / "flow-c", nodes={
        "a": {"node_id": "a", "status": "done", "attempts": 1,
              "started_at": _span(0), "ended_at": _span(4)}},
        events=[{"ts": _span(0), "node": "a", "status": "running"},
                {"ts": _span(2), "node": "g", "status": "heal-round"},
                {"ts": _span(4), "node": "a", "status": "done"}])
    wf = mission_server.waterfall(run, tmp_path, now=datetime(
        2026, 8, 10, 5, 4, tzinfo=timezone.utc))
    assert len(wf["marks"]) == 1
    assert 48 <= wf["marks"][0]["pct"] <= 52
    assert wf["marks"][0]["node"] == "g"


def test_the_critical_path_is_empty_rather_than_guessed(tmp_path):
    """No flow file means no dependency edges, and a highlight that guesses
    which chain mattered is worse than no highlight."""
    run = mini_run(tmp_path / "flow-d", nodes={
        "a": {"node_id": "a", "status": "done", "attempts": 1,
              "started_at": _span(0), "ended_at": _span(1)}},
        events=[{"ts": _span(0), "node": "a", "status": "running"},
                {"ts": _span(1), "node": "a", "status": "done"}])
    wf = mission_server.waterfall(run, tmp_path, now=datetime(
        2026, 8, 10, 5, 1, tzinfo=timezone.utc))
    assert wf["critical"] == []
    assert all(not r["critical"] for r in wf["rows"])


def test_the_critical_path_follows_the_dependency_that_finished_last(tmp_path):
    """`slow` and `quick` both feed `end`; only `slow` held it up."""
    run = mini_run(tmp_path / "flow-e", nodes={
        "quick": {"node_id": "quick", "status": "done", "attempts": 1,
                  "started_at": _span(0), "ended_at": _span(1)},
        "slow": {"node_id": "slow", "status": "done", "attempts": 1,
                 "started_at": _span(0), "ended_at": _span(5)},
        "end": {"node_id": "end", "status": "done", "attempts": 1,
                "started_at": _span(5), "ended_at": _span(6)}},
        events=[{"ts": _span(0), "node": "quick", "status": "running"},
                {"ts": _span(1), "node": "quick", "status": "done"},
                {"ts": _span(0), "node": "slow", "status": "running"},
                {"ts": _span(5), "node": "slow", "status": "done"},
                {"ts": _span(5), "node": "end", "status": "running"},
                {"ts": _span(6), "node": "end", "status": "done"}],
        flow={"format_version": "1.0", "name": "e", "nodes": [
            {"id": "quick"}, {"id": "slow"},
            {"id": "end", "depends_on": ["quick", "slow"]}]})
    wf = mission_server.waterfall(run, tmp_path, now=datetime(
        2026, 8, 10, 5, 6, tzinfo=timezone.utc))
    assert set(wf["critical"]) == {"end", "slow"}, wf["critical"]


def test_resolve_run_never_leaves_the_runs_directory(tmp_path):
    """`?run=` is attacker-controlled. It is matched against the directory
    LISTING rather than joined onto a path, so no traversal, absolute path or
    symlink can name something that is not already a run dir in runs/."""
    runs = tmp_path / "runs"
    real = mini_run(runs / "real-run", nodes={"a": {"node_id": "a", "status": "done"}},
                    events=[{"ts": _span(0), "node": "a", "status": "done"}])
    for name in ("../secret", "..", "/etc/passwd", "D:/Windows", "nope", "runs"):
        got = mission_server.resolve_run(runs, name, None)
        assert got is None, f"{name!r} resolved to {got}"
    assert mission_server.resolve_run(runs, "real-run", None).resolve() == real.resolve()


def test_an_unknown_run_is_a_404_not_a_different_run(tmp_path):
    runs = tmp_path / "runs"
    mini_run(runs / "real-run", nodes={"a": {"node_id": "a", "status": "done"}},
             events=[{"ts": _span(0), "node": "a", "status": "done"}])
    status, _, _ = mission_server.handle("/?run=ghost", runs, None, tmp_path)
    assert status == 404, "showing a different run under an authoritative headline is worse"


def test_the_rail_lists_runs_and_marks_the_current_one(tmp_path):
    runs = tmp_path / "runs"
    a = mini_run(runs / "flow-a-1", nodes={"n": {"node_id": "n", "status": "done"}},
                 events=[{"ts": _span(0), "node": "n", "status": "done"}])
    mini_run(runs / "flow-a-2", nodes={"n": {"node_id": "n", "status": "failed"}},
             events=[{"ts": _span(0), "node": "n", "status": "failed"}])
    html_out = mission_server.render_nav(runs, a)
    assert 'aria-current="page"' in html_out
    assert "?run=flow-a-1" in html_out and "?run=flow-a-2" in html_out
    # Every word in the rail comes from the same glossary as the board.
    assert mv.GLOSSARY["failed"] in html_out


def test_a_failed_run_reads_as_stopped_not_needs_you(tmp_path):
    """A run with a failed node DID stop with a problem; calling that "needs
    you" sends the reader to a terminal to answer a question nobody asked."""
    runs = tmp_path / "runs"
    mini_run(runs / "boom", nodes={"a": {"node_id": "a", "status": "failed"},
                                   "b": {"node_id": "b", "status": "blocked"}},
             events=[{"ts": _span(0), "node": "a", "status": "failed"}])
    rows, _ = mission_server.run_list(runs, None)
    assert rows[0]["word"] == mv.GLOSSARY["failed"]


def test_the_rail_distinguishes_runs_of_the_same_flow(tmp_path):
    """Nine rows all reading "webapp-local" is not navigation. Seen on the real
    page the first time the rail was rendered."""
    runs = tmp_path / "runs"
    for i in (1, 2):
        mini_run(runs / f"same-{i}", nodes={"n": {"node_id": "n", "status": "done"}},
                 events=[{"ts": _span(i * 10), "node": "n", "status": "done"}])
    rows, _ = mission_server.run_list(runs, None)
    assert len({r["when"] for r in rows}) == 2, "each row needs its own time"


def test_the_poll_carries_the_selected_run(tmp_path):
    """A page opened on an OLD run must not quietly start refreshing itself
    with the newest one's data."""
    js = mission_server.client_js()
    assert "runq(" in js and "dataset.run" in js
    runs = tmp_path / "runs"
    mini_run(runs / "r1", nodes={"a": {"node_id": "a", "status": "done"}},
             events=[{"ts": _span(0), "node": "a", "status": "done"}])
    page = mission_server.render_page(
        mission_server.resolve_run(runs, "r1", None), tmp_path, runs)
    assert 'data-run="r1"' in page


def test_ordinary_attempts_do_not_drown_the_feed():
    """Rendering every attempt filled the 12-line "what just happened" pane
    and pushed real transitions out of it. Two rules, and the second is the
    one round 2 missed: `initial` is quiet because the `running` transition
    that follows says the same thing, and MAP ITEMS are quiet whatever the
    cause — a rework round re-runs every item, so a 40-item map emitted 40
    identical "sent back for rework" lines into a 12-line pane."""
    labels = {}

    def render(**ev):
        ev.setdefault("kind", "attempt")
        ev.setdefault("ts", "2026-01-01T00:00:00+00:00")
        return mission_server.event_text(ev, labels)

    assert render(node="w", cause="initial", ordinal=1) == ""
    assert render(node="m", cause="initial", ordinal=1, item=3) == ""
    # ... and the loud ones stay quiet too when they are per-item.
    assert render(node="m", cause="heal", ordinal=2, item=3, heal_round=1) == ""
    assert render(node="m", cause="corrective", ordinal=2, item=3) == ""

    loud = render(node="w", cause="heal", ordinal=2, heal_round=1)
    assert "rework" in loud, loud
    # `served` must NOT be quiet: it is the only place the page says nothing
    # ran, and suppressing it made a --replay read exactly like a real run.
    assert "nothing ran" in render(node="w", cause="served", ordinal=1)


def test_only_narratable_events_cost_a_label_read(tmp_path, monkeypatch):
    """The label sidecar is read per tick; keying that off raw events meant a
    map fan-out read a file every tick to render nothing.

    Exercised through the ROUTE, not just the helper: the first version
    called `_narratable` directly, so reverting the call site left it green
    (the helper is new, and nothing else referenced it)."""
    run = page_run(tmp_path)
    reads = []
    real = mission_server.mv.load_labels
    monkeypatch.setattr(mission_server.mv, "load_labels",
                        lambda *a, **k: (reads.append(1), real(*a, **k))[1])

    start = json.loads(get(run, "/api/events?after=0", tmp_path)[2])["next"]
    with open(run / "events.jsonl", "a", encoding="utf-8") as fh:
        for i in range(20):
            fh.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00",
                                 "kind": "attempt", "node": "m",
                                 "cause": "initial", "item": i,
                                 "ordinal": 1}) + chr(10))
    reads.clear()
    doc = json.loads(get(run, f"/api/events?after={start}", tmp_path)[2])
    assert doc["next"] != start, "precondition: the cursor moved"
    assert doc["events"] == [], "precondition: none of them narrate"
    assert reads == [], "the sidecar was read to render nothing"

    with open(run / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "kind": "attempt",
                             "node": "w", "cause": "heal", "ordinal": 2,
                             "heal_round": 1}) + chr(10))
    reads.clear()
    doc2 = json.loads(get(run, f"/api/events?after={doc['next']}", tmp_path)[2])
    assert doc2["events"], "precondition: this one narrates"
    assert reads, "a narratable event must get its labels"


def test_engine_status_tokens_are_translated_for_the_reader():
    """The journal's status enum is about twice the size of the shared
    GLOSSARY (a 6-entry NODE-STATUS map pinned across surfaces), and
    everything outside it fell through to the raw engine token — a domain
    expert reading `heal-exhausted-pass` in the one pane meant to be in
    their words."""
    for status in ("heal-round", "heal-exhausted-pass", "scope-corrective-respawn",
                   "quarantined", "restored-undeclared", "rolled-back"):
        text = mission_server.event_text(
            {"ts": "2026-01-01T00:00:00+00:00", "node": "w", "status": status}, {})
        assert status not in text, f"raw engine token reached the feed: {text}"
        assert text.strip(), status
