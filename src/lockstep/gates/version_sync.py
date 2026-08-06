"""Version-declaration agreement -> Verdict (the class of defect r7 shipped:
`__version__` 0.2.0 against pyproject's 0.3.1).

Usage from a flow:
    ["python", "-m", "lockstep.gates.version_sync"]
    ["python", "-m", "lockstep.gates.version_sync", "--changelog", "CHANGELOG.md",
     "--tag", "{args.tag}"]

Checks that agree must: pyproject.toml [project].version and the package's
`__version__` (path from --init, or derived from [project].name). Optional:
--changelog requires a markdown heading containing the version; --tag requires
the intended tag to equal the version (a leading "v" is allowed).

The version is read from `__init__.py` by regex, never by import: the target
repo's package may not be importable from the gate's python.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from ._common import emit, finding, read_doc


def _init_candidates(name: str) -> list[Path]:
    mod = name.replace("-", "_")
    return [Path("src") / mod / "__init__.py", Path(mod) / "__init__.py"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lockstep.gates.version_sync")
    ap.add_argument("--pyproject", default="pyproject.toml")
    ap.add_argument("--init", default=None,
                    help="path to the __init__.py carrying __version__ "
                         "(default: derived from [project].name)")
    ap.add_argument("--changelog", default=None,
                    help="require a markdown heading containing the version")
    ap.add_argument("--tag", default=None,
                    help="the tag about to be cut; must equal the version (leading v ok)")
    ns = ap.parse_args(argv)
    findings: list[dict] = []

    try:
        data = tomllib.loads(Path(ns.pyproject).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return emit(
            [finding("blocker", "pyproject", ns.pyproject, "pyproject.toml unreadable",
                     str(e), "check --pyproject")],
            "",
        )
    project = data.get("project") or {}
    version = project.get("version")
    if not isinstance(version, str) or not version:
        return emit(
            [finding("blocker", "pyproject", ns.pyproject, "[project].version missing",
                     f"project table keys: {sorted(project)}",
                     "declare a static [project].version")],
            "",
        )

    candidates = [Path(ns.init)] if ns.init else _init_candidates(str(project.get("name", "")))
    init_path = next((p for p in candidates if p.exists()), None)
    if init_path is None:
        findings.append(
            finding("blocker", "init", str(candidates[0]), "__init__.py not found",
                    f"tried: {', '.join(str(c) for c in candidates)}",
                    "pass --init <path>")
        )
    else:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']',
                      init_path.read_text(encoding="utf-8", errors="replace"))
        if not m:
            findings.append(
                finding("blocker", "init", str(init_path), "no __version__ assignment found",
                        "regex __version__ = \"...\" matched nothing",
                        "declare __version__ or pass --init at the right file")
            )
        elif m.group(1) != version:
            findings.append(
                finding("blocker", "version-drift", str(init_path),
                        f"__version__ {m.group(1)!r} != pyproject {version!r}",
                        "the r7 defect class: two declarations, one stale",
                        "sync __version__ to pyproject before cutting")
            )

    if ns.changelog:
        text, problem = read_doc(ns.changelog)
        if problem:
            findings.append(problem)
        else:
            heads = [m.group(1) for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)]
            # Delimited match: a "## 0.3.10" heading must not satisfy 0.3.1.
            token = re.compile(rf"(?<![\w.]){re.escape(version)}(?![\w.])")
            if not any(token.search(h) for h in heads):
                findings.append(
                    finding("blocker", "changelog", ns.changelog,
                            f"no changelog heading mentions {version}",
                            f"headings: {heads[:5]}",
                            f"add a '## {version}' entry before cutting")
                )

    if ns.tag and ns.tag.removeprefix("v") != version:
        findings.append(
            finding("blocker", "tag", ns.pyproject,
                    f"intended tag {ns.tag!r} does not match version {version!r}",
                    "tag and version must agree before anything is published",
                    f"tag v{version} or fix the version")
        )

    return emit(findings, f"all version declarations agree on {version}",
                f"{len(findings)} version problem(s)")


if __name__ == "__main__":
    sys.exit(main())
