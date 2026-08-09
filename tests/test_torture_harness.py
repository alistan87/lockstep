"""The scripted agent's own logic, and that the torture flows stay well-formed.

`contrib/torture_suite.py` is the real test — it drives the engine end to end —
but it is a separate command for the same reason `replay_suite.py` is: it
spawns processes and takes about a minute, and the pytest suite runs after
every change. What belongs HERE is the cheap part: the load-bearing string
matching inside the scripted agent, which is what turns "healing appeared to
work" into an assertion, and which fails silently if the driver's wording moves.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TORTURE = ROOT / "flows" / "demo" / "torture"


def flow_name(path: Path) -> str:
    """`Path.stem` leaves `.tg` on a `*.tg.json` name."""
    return path.name[: -len(".tg.json")]


def load_agent():
    spec = importlib.util.spec_from_file_location(
        "scripted_agent", ROOT / "contrib" / "demo" / "scripted_agent.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = load_agent()


# ------------------------------------------------------------- the markers

def test_the_heal_marker_matches_what_the_driver_actually_prepends():
    """If `roles.py` rewords the heal preamble, the agent stops detecting heal
    rounds — and a torture run would then pass while testing nothing, because
    the scenario would just look like it needed no healing. Pinned to the
    source, not to a copy of the string."""
    roles = (ROOT / "src" / "lockstep" / "roles.py").read_text(encoding="utf-8")
    assert agent.HEAL_MARKER in roles, (
        f"scripted_agent.HEAL_MARKER {agent.HEAL_MARKER!r} no longer appears in roles.py"
    )


def test_the_corrective_marker_matches_the_driver():
    roles = (ROOT / "src" / "lockstep" / "roles.py").read_text(encoding="utf-8")
    assert agent.CORRECTIVE_MARKER in roles


def test_the_readonly_marker_matches_the_footer():
    """Against the CONSTRUCTED footer, not the source text: the literal is split
    across two adjacent string literals in harness.py, so a source grep looks
    for something that only exists after concatenation — it would have failed
    here while the agent worked perfectly."""
    from lockstep.executors.harness import FOOTER_READONLY

    assert agent.READONLY_MARKER in FOOTER_READONLY


# ------------------------------------------------------------- directives

@pytest.mark.parametrize("key,text,want", [
    ("SCENARIO", "do a thing\nSCENARIO: heal-after:2\nARTIFACT: a/b.txt\n", "heal-after:2"),
    ("ARTIFACT", "do a thing\nSCENARIO: ok\nARTIFACT: a/b.txt\n", "a/b.txt"),
    ("STRAY", "SCENARIO: stray-write\nSTRAY:    x/y.txt   \n", "x/y.txt"),
    ("SCENARIO", "nothing here", None),
])
def test_directive_parsing(key, text, want):
    assert agent.directive(text, key) == want


def test_a_directive_inside_a_fenced_data_block_is_still_found():
    """Not a security property — the agent is a test double. Documented so the
    next reader does not mistake it for prompt-injection resistance."""
    assert agent.directive("begin data\nSCENARIO: ok\nend data", "SCENARIO") == "ok"


# ------------------------------------------------------------- the flows

@pytest.mark.parametrize("path", sorted(TORTURE.glob("*.tg.json")), ids=flow_name)
def test_torture_flows_parse_and_declare_a_scenario(path):
    from lockstep.taskgraph import TaskGraph

    d = json.loads(path.read_text(encoding="utf-8"))
    TaskGraph.model_validate(d)
    for n in d["nodes"]:
        if n.get("kind") == "harness":
            assert agent.directive(n["spec"]["task"], "SCENARIO"), f"{flow_name(path)}:{n['id']}"


@pytest.mark.parametrize("path", sorted(TORTURE.glob("*.tg.json")), ids=flow_name)
def test_scripted_nodes_do_not_wait_out_a_provider_backoff(path):
    """The harness default is 2 retries with 60s backoff, sized for transient
    429/529s. A scripted agent has no provider: its failures are deterministic,
    so a retry is pure latency. A mutation test took four minutes to fail
    before this was set."""
    for n in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
        if n.get("kind") == "harness":
            assert n.get("retry", {}).get("max") == 0, f"{flow_name(path)}:{n['id']}"


def test_every_scenario_in_the_suite_has_a_flow_and_an_assertion():
    """A scenario list that drifts from the flow directory is a suite that
    silently stops covering something."""
    spec = importlib.util.spec_from_file_location(
        "torture_suite", ROOT / "contrib" / "torture_suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    named = {entry[0] for entry in suite.SCENARIOS}
    on_disk = {flow_name(p) for p in TORTURE.glob("*.tg.json")}
    assert named == on_disk, f"suite covers {named}, flows on disk are {on_disk}"
    for entry in suite.SCENARIOS:
        # (name, expected_exit, assertion[, custom driver]) — `torture-resume`
        # carries the fourth because it interrupts the driver instead of
        # waiting for it.
        name, expected, assertion, *rest = entry
        assert callable(assertion) and expected in (0, 2, 3, 4, 6, 7, 8), name
        assert not rest or callable(rest[0]), name
