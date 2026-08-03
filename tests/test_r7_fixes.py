"""The r7 candidates recorded in docs/notes/ROADMAP-NOTES.md, now fixed.

- 2026-07-28: a corrective re-spawn can exceed the Windows command-line limit,
  and the spawn error is then masked by a ContractError for a process that
  never ran. Two defects, two guards.
- 2026-07-27: a JSON string interpolated into shell argv carries its quotes.
- packaging: __version__ and pyproject drifted apart silently.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from lockstep.executors.proc import ARGV_LIMIT, ArgvTooLong, argv_overflow, spawn
from lockstep.interpolate import ResolveCtx, render_template
from lockstep.state import load_state

from conftest import PY, build

# --------------------------------------------------- argv length guard (B1)


def test_argv_overflow_is_none_for_ordinary_argv():
    assert argv_overflow(["python", "-c", "print(1)"]) is None


def test_argv_overflow_names_the_stdin_remedy():
    msg = argv_overflow(["harness", "-p", "x" * (ARGV_LIMIT + 10)])
    assert msg is not None
    assert 'prompt_via = "stdin"' in msg
    assert str(ARGV_LIMIT) in msg


def test_argv_overflow_names_the_offending_element():
    msg = argv_overflow(["harness", "-p", "x" * (ARGV_LIMIT + 10)])
    assert "argv[2]" in msg


def test_argv_too_long_is_an_oserror():
    """Both executors already funnel OSError into RawResult(exit 127); the
    guard must ride that path rather than crashing the run."""
    assert issubclass(ArgvTooLong, OSError)


def test_spawn_refuses_an_overlong_command_line(tmp_path):
    with pytest.raises(ArgvTooLong):
        spawn(
            [PY, "-c", "x" * (ARGV_LIMIT + 10)],
            cwd=tmp_path,
            stdout_path=tmp_path / "o.log",
            stderr_path=tmp_path / "e.log",
        )


def test_shell_node_reports_the_overflow_with_its_remedy(tmp_path, git_repo):
    f = {
        "name": "argv-overflow",
        "nodes": [
            {
                "id": "n",
                "kind": "shell",
                "final": True,
                "spec": {"cmd": [PY, "-c", "x" * (ARGV_LIMIT + 10)]},
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    err = load_state(h.run_dir).nodes["n"].error or ""
    assert 'prompt_via = "stdin"' in err


# ------------------------------------------ masked re-spawn diagnosis (B2)


def test_failed_corrective_respawn_reports_the_spawn_error(tmp_path, git_repo):
    """r5 A2 inflates the corrective prompt, so the re-spawn is the one that
    overflows. When it never starts, the operator must see THAT — not
    'not valid JSON' for a process that produced no output because it did
    not run."""
    f = {
        "name": "respawn-failure",
        "nodes": [
            {
                "id": "n",
                "kind": "fake",
                "final": True,
                "output": "json",
                "contract": "StepResult",
                "spec": {
                    "outputs": ["not json at all"],
                    "spawn_error": "spawn failed: argv too long",
                    "spawn_error_after": 1,
                },
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    err = load_state(h.run_dir).nodes["n"].error or ""
    assert "argv too long" in err
    assert "valid JSON" not in err


def test_successful_respawn_still_reports_the_contract_error(tmp_path, git_repo):
    """The masking fix must not swallow the ordinary case: when the re-spawn
    DID run and produced invalid output, that is still the diagnosis."""
    f = {
        "name": "respawn-contract",
        "nodes": [
            {
                "id": "n",
                "kind": "fake",
                "final": True,
                "output": "json",
                "contract": "StepResult",
                "spec": {"outputs": ["garbage", "still garbage"]},
            }
        ],
    }
    h = build(tmp_path, f, git_repo)
    assert h.engine.run() == 3
    assert "twice" in (load_state(h.run_dir).nodes["n"].error or "")


# ------------------------------------- JSON strings in shell argv (B3, §7)


def _json_ctx(payload: dict) -> ResolveCtx:
    return ResolveCtx(
        args={}, outputs={}, json_results={"g": payload}, skipped=set(), deps=["g"]
    )


def _render(template: str, payload: dict, *, fence: bool) -> str:
    return render_template(
        template, _json_ctx(payload), fence=fence, max_interp_chars=1000, spill_dir=None
    ).prompt_text


def test_json_string_renders_raw_in_shell_argv():
    """The bug: the program opened a file whose name started with a quote."""
    assert _render("{steps.g.json.path}", {"path": "docs/x.json"}, fence=False) == "docs/x.json"


def test_json_non_strings_keep_compact_json_in_argv():
    payload = {"n": 3, "items": ["a", "b"], "ok": True}
    assert _render("{steps.g.json.n}", payload, fence=False) == "3"
    assert _render("{steps.g.json.items}", payload, fence=False) == '["a","b"]'
    assert _render("{steps.g.json.ok}", payload, fence=False) == "true"


def test_prompt_fencing_keeps_json_quoting():
    """fence=True is the §7 prompt contract and must not move."""
    assert '"docs/x.json"' in _render("{steps.g.json.path}", {"path": "docs/x.json"}, fence=True)


def test_when_comparison_semantics_are_untouched():
    """`when` resolves through eval_when, a separate path from render_template;
    string comparison stays compact-JSON on both sides."""
    from lockstep.interpolate import eval_when

    ctx = _json_ctx({"verdict": "pass"})
    assert eval_when('{steps.g.json.verdict} == "pass"', ctx)
    assert not eval_when('{steps.g.json.verdict} == "block"', ctx)


# ------------------------------------------------------------ version (B4)


def test_package_version_matches_pyproject():
    import lockstep

    root = Path(__file__).resolve().parents[1]
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert lockstep.__version__ == meta["project"]["version"]
