"""Execute `contrib/pi-extension/lockstep-guard.ts` — the real module, the real
handler, the real return value.

Every other test of the guard reads its source as text. That is how it shipped
three defects in a row, two of them silent: a global `pi` that live pi refused
to load, `event.block = true` instead of RETURNING `{block: true}` (loaded fine,
blocked nothing), and `spec.writes` resolved against cwd instead of the repo
root. Reading the file proves the characters are present; only running it proves
the decision.

`node --experimental-strip-types` runs the TypeScript directly, so this needs no
build step and no dependency. Skipped where node is absent — including CI — so
it is a local sharpener, not a gate; `flows/starter/pi-guard-smoke.tg.json`
remains the live-pi check.

A child process per case is deliberate: the guard reads its env into
module-level consts at import time, so a cached import would answer every case
with the first case's scope.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "contrib" / "pi-extension" / "lockstep-guard.ts"
PROBE = Path(__file__).resolve().parent / "pi_guard_probe.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed on this machine"
)

REPO = "D:/lockstep-probe" if os.name == "nt" else "/lockstep-probe"


def call(tool: str, target: str, *, writes: list[str] | None = None) -> dict:
    """Fire one tool_call at the guard and return its verdict."""
    env = {
        **os.environ,
        "LOCKSTEP_NODE_ID": "probe-node",
        "LOCKSTEP_ROLE": "work",
        "LOCKSTEP_REPO_ROOT": REPO,
        # Not a real path: the phase-dir exemption must not accidentally cover
        # the cases below.
        "LOCKSTEP_PHASE_DIR": REPO + "/runs/r/phases/probe-node",
        "LOCKSTEP_VERDICT_FILE": "",  # write-only channel; off for this probe
    }
    if writes is None:
        env.pop("LOCKSTEP_WRITE_SCOPE", None)
    else:
        env["LOCKSTEP_WRITE_SCOPE"] = json.dumps(writes)
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(PROBE), str(GUARD), tool, target],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_module_loads_the_way_pi_loads_it():
    """A default-exported registration function that subscribes `tool_call`.

    The first cut used a bare top-level `pi.on(...)`, and live pi answered
    `Failed to load extension: pi is not defined`.
    """
    assert call("write", REPO + "/anywhere.tmp", writes=["docs"])["loaded"] is True


def test_an_out_of_scope_write_is_blocked():
    got = call("write", REPO + "/src/cli.py", writes=["docs"])
    assert got["blocked"] is True
    assert "outside this node's declared write scope" in got["reason"]


def test_an_in_scope_write_is_allowed():
    assert call("write", REPO + "/docs/guide.md", writes=["docs"])["blocked"] is False


def test_a_globbed_scope_entry_still_allows_its_own_writes():
    """The regression this test was written for.

    The driver matches `spec.writes` with fnmatch; the guard prefix-matches.
    `path.resolve(root, "flows/pi-guard-*.tmp")` is a literal directory name,
    so `under()` said a write to `flows/pi-guard-ok.tmp` was OUTSIDE the very
    scope that names it. The guard would then block a write the driver allows —
    an ADDENDUM-A A.1 violation, because deleting the extension would change
    what a correct agent can accomplish.
    """
    scope = ["flows/pi-guard-*.tmp"]
    assert call("write", REPO + "/flows/pi-guard-ok.tmp", writes=scope)["blocked"] is False
    # ...while the escape it exists to stop is still stopped.
    assert call("write", REPO + "/pi-guard-escape.tmp", writes=scope)["blocked"] is True


def test_a_top_level_glob_degrades_to_allow_not_to_deny():
    """`*.md` has no literal directory prefix. The permissive reading is the
    required one: the driver's post-hoc check still adjudicates, whereas
    over-blocking removes capability."""
    assert call("write", REPO + "/deep/nested/file.md", writes=["*.md"])["blocked"] is False


def test_a_node_that_declares_no_writes_is_not_gated():
    """`spec.writes` is the write boundary, and its absence means "unscoped".
    Falling back to cwd here is what preamble note 2 objected to."""
    assert call("write", REPO + "/anywhere.tmp", writes=None)["blocked"] is False


def test_read_tools_are_never_gated():
    """The guard governs writes. Gating a read would break every reviewer."""
    assert call("read", REPO + "/src/secret.py", writes=["docs"])["blocked"] is False


def test_the_phase_dir_is_always_writable():
    """SPEC §8.3: the phase dir is the sanctioned result channel, and the
    harness scratches there too. Blocking it would break the footer contract
    for exactly the scope-narrowed nodes this guard is attached to."""
    target = REPO + "/runs/r/phases/probe-node/result.json"
    assert call("write", target, writes=["docs"])["blocked"] is False
