"""Live checks (SPEC §13.3) — NOT in CI, spends real tokens.

Skipped unless LOCKSTEP_LIVE=1. Requires a ./lockstep.toml whose default
executor stanza actually works (`lockstep doctor` first).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LOCKSTEP_LIVE") != "1", reason="live smoke: set LOCKSTEP_LIVE=1"
)

ROOT = Path(__file__).resolve().parents[2]


def test_doctor():
    from lockstep.cli import main

    assert main(["doctor", "--config", str(ROOT / "lockstep.toml")]) == 0


def test_hello_chain_end_to_end(tmp_path, monkeypatch):
    from lockstep.cli import main
    from lockstep.state import load_state

    monkeypatch.chdir(ROOT)
    code = main(
        [
            "run",
            str(ROOT / "flows" / "hello-chain.tg.json"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--fresh",
        ]
    )
    assert code == 0
    run_dir = next((tmp_path / "runs").iterdir())
    st = load_state(run_dir)
    assert st.nodes["elaborate"].status == "done"
    result = Path(st.nodes["elaborate"].result_path).read_text(encoding="utf-8")
    assert result.strip(), "the second node produced output from {previous.output}"
