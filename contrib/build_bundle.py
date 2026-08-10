#!/usr/bin/env python
"""build_bundle.py — assemble the work-machine bundle: wheel + cockpit + docs.

    python contrib/build_bundle.py [--version 0.3.1]

Lives IN the repo deliberately. The first version of this script sat outside it,
which meant the docs reorganisation rewrote every path in the project except the
ones in the packaging script — it would have shipped a bundle silently missing
half its documentation, reporting each omission as a line of console output
nobody reads. A build script that describes the repo has to live in the repo, so
the repo's own tools maintain it.

Explicit about what goes in. A bundle built by globbing would carry `runs/`,
`lockstep.toml`, and `contrib/cost-fields.toml` — machine-local config at best,
model output over proprietary data at worst.

FAILS on a missing input. The previous version printed "MISSING file:" and
carried on, which is the same fail-open pattern that put five broken references
into a reorganisation: a warning nobody reads is not a check.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Never bundled, whatever the directory rules say.
DENY_NAMES = {"__pycache__", "cost-fields.toml", "lockstep.toml", ".pytest_cache"}
DENY_SUFFIX = (".pyc", ".bak")

DIRS = [
    "contrib",
    "flows",
    "personas",
    "docs",              # the whole tree: bundles, indexes, and the vendored spec
    ".claude/skills",
    ".claude/agents",
]

FILES = [
    "README.md",
    "CLAUDE.md",
    "lockstep.toml.example",
]

# The install guide is copied to the top level under a name nobody can miss.
INSTALL_GUIDE = "contrib/INSTALL-WORK-MACHINE.md"


def keep(rel: Path) -> bool:
    if rel.name in DENY_NAMES or rel.name.endswith(DENY_SUFFIX):
        return False
    return not any(part in DENY_NAMES for part in rel.parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", required=True)
    ns = ap.parse_args(argv)

    dist = REPO / "dist"
    wheel = dist / f"lockstep-{ns.version}-py3-none-any.whl"
    if not wheel.is_file():
        print(f"error: {wheel.name} not built. Run:\n"
              f"  .venv\\Scripts\\python.exe -m pip wheel . --no-deps -w dist",
              file=sys.stderr)
        return 2

    stage = dist / f"lockstep-cockpit-{ns.version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(wheel, stage)

    missing: list[str] = []
    for d in DIRS:
        src = REPO / d
        if not src.is_dir():
            missing.append(d)
            continue
        for f in src.rglob("*"):
            rel = f.relative_to(REPO)
            if f.is_file() and keep(rel):
                dest = stage / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    for f in FILES:
        src = REPO / f
        if not src.is_file():
            missing.append(f)
            continue
        dest = stage / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # The install guide goes to the TOP LEVEL, which is what the comment above
    # promised and the code did not do: it was copied to its own nested path,
    # where the wholesale contrib/ copy had already put it. A recipient opening
    # the zip should not have to go looking for the one file that tells them
    # what to do with it.
    src = REPO / INSTALL_GUIDE
    if src.is_file():
        shutil.copy2(src, stage / Path(INSTALL_GUIDE).name)
    else:
        missing.append(INSTALL_GUIDE)

    if missing:
        # Refuse rather than ship a bundle whose gaps are announced only in
        # console output. This is the check the old print-and-continue was not.
        shutil.rmtree(stage)
        print("error: refusing to build - inputs missing:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2

    shutil.copy2(REPO / INSTALL_GUIDE, stage / "INSTALL.md")

    zip_path = dist / f"lockstep-cockpit-{ns.version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage.parent))

    count = sum(1 for f in stage.rglob("*") if f.is_file())
    print(f"staged {count} files -> {zip_path} ({zip_path.stat().st_size // 1024} KB)")

    # A leak here ships someone's prompts and model output. Cheap to assert.
    # Match BASENAMES exactly, not substrings: `cost-fields.toml.example` is a
    # template that must ship, and a substring test flagged it as the local
    # config it is the template for. A check that cries wolf on correct output
    # gets disabled, which is how the thing it guards eventually ships.
    banned_names = {"cost-fields.toml", "lockstep.toml"}
    banned_parts = {"runs", "__pycache__", "Deliverables", ".venv"}
    leaked = []
    for n in zipfile.ZipFile(zip_path).namelist():
        parts = n.split("/")
        if parts[-1] in banned_names or n.endswith(".pyc") or set(parts) & banned_parts:
            leaked.append(n)
    if leaked:
        print("error: bundle contains machine-local or sensitive paths:", file=sys.stderr)
        for n in leaked:
            print(f"  - {n}", file=sys.stderr)
        return 2
    print("leak check: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
