"""Run the suite the way a CONSUMER gets it: clean checkout, other Python.

Two defects reached a downstream consumer in 0.14.0 that four adversarial
review rounds had missed, and neither was subtle:

- `JSONDecodeError.pos` moved in CPython 3.13 (closer -> comma), so the
  deletion-only JSON repair silently stopped repairing and every dangling
  comma fell through to a billed corrective re-spawn;
- a test branched on `contrib/cost-fields.toml`, which is gitignored, so it
  passed on any machine that happened to have one and failed on a clean
  checkout of the tag.

Both are invisible from inside a developer's working tree. The reviews were
adversarial about the CODE and never about the ENVIRONMENT; this closes that
by making the consumer's conditions reproducible here:

    python contrib\\portability_check.py                    # clean checkout, this Python
    python contrib\\portability_check.py --python 3.13      # ... and another one
    python contrib\\portability_check.py --python 3.13 --keep

By default it CLONES `HEAD` into a temp dir, so only tracked files exist:
no `lockstep.toml`, no `cost-fields.toml`, no `runs/` — exactly what
someone who clones the tag has. A clone rather than an archive because
lockstep needs a real repository (GitWorkspace snapshots; the hygiene
tooling shells out to `git ls-files`), and an archive made the checker
report a failure no consumer would ever see.

It checks what is COMMITTED. Commit before trusting a green run.

Read-only with respect to the repo. It writes only inside a temp directory
it creates and removes (unless `--keep`). Spends no tokens: the suite it
runs is the offline one.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_python(spec: str | None) -> tuple[list[str], str]:
    """(argv prefix, label). A bare version like `3.13` goes through the
    Windows launcher; anything else is taken as a path."""
    if not spec:
        return [sys.executable], f"this interpreter ({sys.version.split()[0]})"
    if spec.replace(".", "").isdigit():
        return ["py", f"-V:{spec}"], f"python {spec} (via py launcher)"
    return [spec], spec


def export_clean(dest: Path) -> None:
    """A consumer's view of the tag: `git clone`, not `git archive`.

    Both give tracked files only, which is the point — no `lockstep.toml`,
    no `cost-fields.toml`, no `runs/`. But an archive is not a REPOSITORY,
    and lockstep needs one (GitWorkspace snapshots, and the hygiene tooling
    shells out to `git ls-files`). Exporting with `archive` made the checker
    report a failure that no consumer would ever see, which is its own kind
    of false alarm.

    NOTE: a clone carries HEAD, so this checks what is COMMITTED. Commit
    before trusting a green run.
    """
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks",
                    str(ROOT), str(dest)], check=True)


def run(argv: list[str], cwd: Path, label: str) -> int:
    print(f"\n--- {label}\n    {' '.join(str(a) for a in argv)}", flush=True)
    return subprocess.run(argv, cwd=cwd).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", help="interpreter to test under: `3.13` or a path")
    ap.add_argument("--dirty", action="store_true",
                    help="use the working tree instead of a clean export "
                         "(defeats half the point; for debugging the checker)")
    ap.add_argument("--keep", action="store_true", help="leave the temp tree in place")
    ap.add_argument("--rev", default="HEAD",
                    help="revision to check out in the clone (default HEAD)")
    ap.add_argument("--pytest-args", default="-q", help="passed through to pytest")
    ns = ap.parse_args(argv)

    py, label = resolve_python(ns.python)
    tmp = Path(tempfile.mkdtemp(prefix="lockstep-portability-"))
    try:
        if ns.dirty:
            work = ROOT
            print(f"working tree (NOT a clean checkout): {work}")
        else:
            work = tmp / "checkout"
            export_clean(work)
            if ns.rev != "HEAD":
                subprocess.run(["git", "checkout", "--quiet", ns.rev],
                               cwd=work, check=True)
            tracked = sum(1 for _ in work.rglob("*") if _.is_file())
            print(f"clean checkout of HEAD: {tracked} tracked files in {work}")
            for ambient in ("lockstep.toml", "contrib/cost-fields.toml"):
                assert not (work / ambient).exists(), f"{ambient} leaked into the export"
            print("  confirmed absent: lockstep.toml, contrib/cost-fields.toml")

        venv = tmp / "venv"
        if run([*py, "-m", "venv", str(venv)], work, f"create venv under {label}"):
            print("could not create a venv - is that interpreter installed?",
                  file=sys.stderr)
            return 2
        vpy = venv / ("Scripts" if sys.platform == "win32" else "bin") / "python"

        if run([str(vpy), "-m", "pip", "install", "-q", "-e", ".", "pytest"], work,
               "install (pydantic is the only runtime dependency)"):
            return 2

        code = run([str(vpy), "-m", "pytest", *ns.pytest_args.split(), "-p",
                    "no:cacheprovider"], work, f"suite under {label}, clean checkout")
        print(f"\n{'PASS' if code == 0 else 'FAIL'}: {label}, "
              f"{'working tree' if ns.dirty else 'clean checkout'}")
        return code
    finally:
        if ns.keep:
            print(f"kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
