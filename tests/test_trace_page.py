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
    run = page_run(tmp_path)
    first = json.loads(get(run, "/api/events?after=0", tmp_path)[2])
    assert first["events"] and first["next"] == len(first["events"])
    again = json.loads(get(run, f"/api/events?after={first['next']}", tmp_path)[2])
    assert again["events"] == [] and again["next"] == first["next"]


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
    run = page_run(tmp_path)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "ACTIVITY" in body
    assert "every attempt counted" in body and "kept attempts only" in body
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
    assert row["word"].startswith("sent back for rework (1 of ")
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
    assert "doc.token !== token" in js and "cursor = 0" in js


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
    assert len(body) < 120, "a quiet second must not ship a page"


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
    assert "cursor = 0" in branch and "refresh()" in branch
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
    meter = mission_server.spend_meter([{"token_spawns": 9}], [None])
    assert meter["cap"] is None and meter["pct"] is None
    assert meter["label"] == "agent tasks used 9"

    run = page_run(tmp_path, cap=None)
    body = get(run, "/", tmp_path)[2].decode("utf-8")
    assert "agent tasks used 9" in body
    assert "of 25" not in body


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
