"""`lockstep doctor` (SPEC §8.5): probe each configured executor stanza.

The only check that catches harness flag drift — the failure mode the offline
suite structurally cannot see. Run it after any harness upgrade and on a weekly
cadence (AMENDMENTS A1); a pre-commit hook would spend a model round-trip per
commit, which is the wrong trade.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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


def run_setup_checks(repo_root: Path, log=print) -> int:
    """Free, token-free checks of everything the cockpit needs to exist.

    This is the half of `doctor` a non-programmer can run alone on a machine
    the author will never see (proposal rev 7, decision 2, step 2b): the author
    cannot reproduce a failure there, so "is this installed correctly" has to
    be answerable mechanically and the output has to be safe to send back —
    machine facts and check results only, never repo contents, and never a path
    from inside the operator's own data.

    Returns the number of FAILED checks. Warnings do not count: a missing
    wezterm costs the panes, not the driver.
    """
    failures = 0

    def check(label: str, ok: bool, detail: str, *, fatal: bool = True) -> None:
        nonlocal failures
        mark = "ok  " if ok else ("FAIL" if fatal else "warn")
        log(f"[{mark}] {label}: {detail}")
        if not ok and fatal:
            failures += 1

    check("python", True, sys.version.split()[0])

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    check("pwsh", bool(pwsh), pwsh or "not on PATH — the cockpit panes need PowerShell")

    if pwsh:
        # A PowerShell profile can SUBSTITUTE the shell — auto-starting an
        # interactive agent in a project directory, so a "terminal" is really a
        # chat composer and anything typed at it goes to a model. This is not
        # hypothetical: it is why the cockpit spawns every pane with -NoProfile
        # and verifies by handshake which program holds the pane.
        #
        # Deliberately NOT probed by running one: the substitution only happens
        # in an INTERACTIVE console, and a probe with piped stdio returns
        # control normally — reporting "ok" for a machine that has the hazard.
        # A check that can only produce false assurance is worse than a stated
        # fact, so this states the fact and leaves the judgment to a human.
        profiles = []
        try:
            proc = subprocess.run(
                [pwsh, "-NoProfile", "-NoLogo", "-Command",
                 "$PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost, "
                 "$PROFILE.AllUsersAllHosts | Where-Object { Test-Path $_ }"],
                capture_output=True, timeout=25, encoding="utf-8", errors="replace")
            profiles = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
        if profiles:
            check("pwsh profile", True,
                  f"{len(profiles)} profile(s) present — cockpit panes ignore them "
                  f"(-NoProfile). If a terminal you open BY HAND starts something other "
                  f"than a shell, that is why, and it is not a lockstep fault")
        else:
            check("pwsh profile", True, "none — a plain shell stays a shell")

    wez = shutil.which("wezterm")
    if wez:
        try:
            # encoding is explicit: wezterm's JSON carries pane titles, and on a
            # cp1252 console the default decode raises inside subprocess's
            # reader thread — a check that crashes on someone else's tab title
            # is worse than no check.
            proc = subprocess.run([wez, "cli", "list", "--format", "json"],
                                  capture_output=True, text=True, timeout=20,
                                  encoding="utf-8", errors="replace")
            responsive = proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            responsive = False
        # `wezterm` on PATH is not the same as `wezterm cli` reaching a live mux:
        # the CLI only answers from inside a running WezTerm. Outside one, the
        # cockpit still works — it falls back to a plain status loop.
        check("wezterm cli", responsive,
              "responsive" if responsive else "installed, but no live WezTerm session "
              "(panes fall back to a plain status loop)", fatal=False)
    else:
        check("wezterm", False, "not on PATH — cockpit falls back to a status loop",
              fatal=False)

    runs = repo_root / "runs"
    check("runs/", True, "present" if runs.is_dir() else "will be created on first run")

    gitignore = repo_root / ".gitignore"
    ignored = gitignore.is_file() and "runs/" in gitignore.read_text(encoding="utf-8")
    # runs/ holds prompts, diffs, and model output. On a machine with a
    # proprietary repo this is the check that matters most.
    check("runs/ gitignored", ignored,
          "yes" if ignored else "NOT ignored — runs/ holds model output and must never be committed")

    # A diagnostic must not change the thing it inspects. An earlier version
    # created Deliverables/ in order to test it, which makes `doctor --setup`
    # a setup STEP masquerading as a check — and leaves a directory behind on a
    # machine where the operator may have meant to point elsewhere.
    deliv = repo_root / "Deliverables"
    if deliv.is_dir():
        try:
            probe = deliv / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            check("Deliverables/", True, "writable")
        except OSError as e:
            check("Deliverables/", False, f"not writable: {e.strerror or e}")
    else:
        # The parent is what actually has to be writable; the flow's delivery
        # node creates the folder itself on first use.
        writable_parent = os.access(repo_root, os.W_OK)
        check("Deliverables/", writable_parent,
              "absent — created on first delivery" if writable_parent
              else f"cannot be created: {repo_root} is not writable")

    personas = repo_root / "personas"
    n_personas = len(list(personas.glob("*.md"))) if personas.is_dir() else 0
    check("personas/", n_personas > 0,
          f"{n_personas} persona(s)" if n_personas else "missing — flows naming a persona will fail")

    fields = repo_root / "contrib" / "cost-fields.toml"
    if fields.is_file():
        try:
            import tomllib
            names = sorted(tomllib.loads(fields.read_text(encoding="utf-8")))
            check("cost-fields.toml", True, f"maps {len(names)} harness binary/ies: {', '.join(names)}")
        except (OSError, ValueError) as e:
            check("cost-fields.toml", False, f"unparseable: {e}")
    else:
        check("cost-fields.toml", False,
              "missing — copy contrib/cost-fields.toml.example and probe your harnesses "
              "(without it, spend shows spawns and wall time but no tokens)", fatal=False)

    for script in ("cost_report.py", "quiescent.py", "render_evidence.py", "cockpit.ps1",
                   "approve.ps1", "start-cockpit.cmd"):
        p = repo_root / "contrib" / script
        check(f"contrib/{script}", p.is_file(), "present" if p.is_file() else "missing")

    return failures


def run_doctor(config: LockstepConfig, log=print, repo_root: Path | None = None,
               setup_only: bool = False) -> int:
    """Exit 7 if any configured executor fails, or any setup check fails."""
    log("--- setup (free) ---")
    setup_failures = run_setup_checks(repo_root or Path.cwd(), log=log)
    if setup_only:
        log("")
        log("setup: " + ("all good" if not setup_failures else f"{setup_failures} problem(s)"))
        return 7 if setup_failures else 0
    log("")
    log("--- executors (spends a small model call each) ---")
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
    return 7 if (failures or setup_failures) else 0
