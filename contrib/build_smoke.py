#!/usr/bin/env python
"""build_smoke.py — build the wheel, install it into a scratch venv, smoke it.
Emits a Verdict, so a release-cut flow uses it directly as a gate body (D1).

    python contrib/build_smoke.py
    python contrib/build_smoke.py --package lockstep --console-script lockstep

Steps, each a blocker Finding on failure:
  1. `pip wheel . -w <scratch>/dist` (no build-backend dependency assumed);
  2. `python -m venv <scratch>/venv` and install the built wheel into it;
  3. import the package in that venv and print its __version__;
  4. run the console script with --help (skipped if --console-script "" ).

Deterministic and token-free; slow is fine (a release is worth a minute).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _finding(category: str, claim: str, evidence: str) -> dict:
    return {
        "severity": "blocker", "category": category, "file": ".", "line": None,
        "claim": claim, "evidence": evidence[-2000:],
        "fix_hint": "fix the build/package before cutting a release",
    }


def _run(argv: list[str], timeout_s: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout_s}s"
    except OSError as e:
        return 127, f"spawn failed: {e}"
    return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", default="lockstep", help="importable package name")
    ap.add_argument("--console-script", default="lockstep",
                    help='console script to smoke with --help; "" skips')
    ns = ap.parse_args(argv)
    findings: list[dict] = []
    summary = ""
    # ignore_cleanup_errors: an AV-held handle at cleanup must not crash the
    # gate BEFORE its Verdict prints — that turns a green build into a node
    # failure (CLAUDE.md ops note on transient PermissionError).
    with tempfile.TemporaryDirectory(prefix="lockstep-build-smoke-",
                                     ignore_cleanup_errors=True) as td:
        scratch = Path(td)
        dist = scratch / "dist"
        code, out = _run([sys.executable, "-m", "pip", "wheel", ".", "-w", str(dist),
                          "--no-deps"])
        wheels = sorted(dist.glob("*.whl")) if dist.exists() else []
        if code != 0 or not wheels:
            findings.append(_finding("build", f"pip wheel failed with exit {code}", out))
        else:
            wheel = wheels[0]
            venv_dir = scratch / "venv"
            code, out = _run([sys.executable, "-m", "venv", str(venv_dir)])
            if code != 0:
                findings.append(_finding("venv", f"venv creation failed with exit {code}", out))
            else:
                bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
                vpy = str(bin_dir / ("python.exe" if sys.platform == "win32" else "python"))
                code, out = _run([vpy, "-m", "pip", "install", str(wheel)])
                if code != 0:
                    findings.append(_finding(
                        "install", f"wheel install failed with exit {code}", out))
                else:
                    code, out = _run([
                        vpy, "-c",
                        f"import {ns.package}; print({ns.package}.__version__)",
                    ])
                    if code != 0:
                        findings.append(_finding(
                            "import", f"import {ns.package} failed with exit {code}", out))
                    else:
                        summary = f"{wheel.name} imports as {out.strip()}"
                    if ns.console_script:
                        script = bin_dir / (
                            f"{ns.console_script}.exe" if sys.platform == "win32"
                            else ns.console_script
                        )
                        code, out = _run([str(script), "--help"])
                        if code != 0:
                            findings.append(_finding(
                                "console-script",
                                f"{ns.console_script} --help failed with exit {code}", out))
    verdict = "pass" if not findings else "block"
    reason = (
        f"wheel builds, installs, imports, and answers --help ({summary})"
        if verdict == "pass" else f"{len(findings)} build problem(s)"
    )
    print(json.dumps({"findings": findings, "verdict": verdict, "reason": reason},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
