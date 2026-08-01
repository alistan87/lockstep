"""`lockstep doctor` (SPEC §8.5): probe each configured executor stanza.

The only check that catches harness flag drift — the failure mode the offline
suite structurally cannot see. Run it after any harness upgrade and on a weekly
cadence (AMENDMENTS A1); a pre-commit hook would spend a model round-trip per
commit, which is the wrong trade.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from .executors.harness import FOOTER, extract_last_json
from .executors.proc import spawn, wait_or_kill
from .registry import ExecutorStanza, LockstepConfig

PROBE = "Reply with the single word OK."
PROBE_TIMEOUT_S = 300


def _probe_once(
    name: str, stanza: ExecutorStanza, extra_argv: list[str], phase_dir: Path, log
) -> tuple[bool, str]:
    prompt = PROBE + FOOTER.format(result_file="result.txt", phase_dir=str(phase_dir))
    argv: list[str] = []
    stdin_text: str | None = None
    for part in stanza.argv:
        if stanza.prompt_via == "stdin" and part == "{prompt}":
            continue
        # {phase_dir} expands here too: a stanza using e.g. `--session-dir
        # {phase_dir}` (ADDENDUM-A A.3.4) must probe with a real dir, not the
        # literal placeholder — doctor exists to catch flag drift, not add it.
        argv.append(part.replace("{prompt}", prompt).replace("{phase_dir}", str(phase_dir)))
    argv += extra_argv
    if stanza.prompt_via == "stdin":
        stdin_text = prompt
    stdout_path = phase_dir / "stdout.log"
    stderr_path = phase_dir / "stderr.log"
    started = time.monotonic()
    try:
        proc = spawn(
            argv,
            cwd=Path.cwd(),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdin_text=stdin_text,
            extra_env={"LOCKSTEP_PHASE_DIR": str(phase_dir)},
        )
    except OSError as e:
        return False, f"spawn failed: {e}"
    exit_code, timed_out = wait_or_kill(proc, PROBE_TIMEOUT_S, stdin_text=stdin_text)
    rtt = time.monotonic() - started
    if timed_out:
        return False, f"timed out after {PROBE_TIMEOUT_S}s"
    if exit_code != 0:
        err = stderr_path.read_text(encoding="utf-8", errors="replace")[:400]
        return False, f"exit code {exit_code}: {err}"
    # Accept EITHER result channel: a probe this trivial may not write
    # result.json — doctor tests the plumbing, not the convention (SPEC §8.5).
    answer = None
    for fname in ("result.json", "result.txt"):  # §8.3 order, matching the executors
        p = phase_dir / fname
        if p.exists():
            answer = p.read_text(encoding="utf-8")
            break
    if answer is None:
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        candidate = extract_last_json(stdout)
        if candidate is not None and stanza.json_field:
            value = json.loads(candidate)
            if isinstance(value, dict) and stanza.json_field in value:
                inner = value[stanza.json_field]
                answer = inner if isinstance(inner, str) else json.dumps(inner)
        if answer is None:
            answer = stdout
    if "ok" not in answer.lower():
        return False, f"no OK in response ({len(answer)} chars); parseable channel though"
    return True, f"OK in {rtt:.1f}s"


def run_doctor(config: LockstepConfig, log=print) -> int:
    """Exit 7 if any configured executor fails."""
    if not config.executors:
        log("doctor: no executors configured (no lockstep.toml?)")
        return 7
    failures = 0
    for name, stanza in config.executors.items():
        binary = stanza.argv[0] if stanza.argv else ""
        if not shutil.which(binary):
            log(f"[{name}] FAIL: binary {binary!r} not on PATH")
            failures += 1
            continue
        with tempfile.TemporaryDirectory(prefix="lockstep-doctor-") as td:
            ok, msg = _probe_once(name, stanza, [], Path(td), log)
        log(f"[{name}] {'ok' if ok else 'FAIL'}: {msg}")
        if not ok:
            failures += 1
            continue
        if stanza.persona_flag:
            # persona_flag honored if declared: spawn succeeds with a trivial persona.
            with tempfile.TemporaryDirectory(prefix="lockstep-doctor-p-") as td:
                persona = Path(td) / "probe-persona.md"
                persona.write_text("---\nname: probe\n---\nYou answer tersely.\n", encoding="utf-8")
                ok_p, msg_p = _probe_once(
                    name, stanza, [*stanza.persona_flag, str(persona)], Path(td), log
                )
            log(f"[{name}] persona_flag {'ok' if ok_p else 'FAIL'}: {msg_p}")
            if not ok_p:
                failures += 1
        if stanza.readonly_argv:
            # readonly_argv accepted if declared: spawn succeeds with flags appended.
            with tempfile.TemporaryDirectory(prefix="lockstep-doctor-ro-") as td:
                ok_ro, msg_ro = _probe_once(name, stanza, list(stanza.readonly_argv), Path(td), log)
            log(f"[{name}] readonly_argv {'ok' if ok_ro else 'FAIL'}: {msg_ro}")
            if not ok_ro:
                failures += 1
    return 7 if failures else 0
