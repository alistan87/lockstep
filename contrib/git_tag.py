#!/usr/bin/env python
"""git_tag.py — create a release tag and SAY SO. The release-cut tag node body.

    python contrib/git_tag.py --tag v0.18.0
    python contrib/git_tag.py --tag v0.18.0 --message "0.18.0: the ... release"
    python contrib/git_tag.py --tag v0.18.0 --commit <sha>

Why this exists rather than a bare `["git", "tag", "{args.tag}"]` in the flow:
**`git tag` is silent on success**, and a silent success is indistinguishable
from a dead node to this engine. `executors/shell.py` sets `result_text` only
when `stdout.strip()` is truthy, and `roles.py::_finish` fails any attempt with
`result_text is None` whatever the exit code — so the successful tag is scored
as a failure, auto-retried, and the retry fails for real with "tag already
exists". Observed live cutting 0.17.0 (`runs/release-cut-20260920T171840Z`):
the tag was correct, the node read `failed`, and the flow's final node could
never report success. Printing one line is the whole fix.

Re-running on the SAME commit is not an error — an auto-retry after a
successful attempt must not turn a finished release into a failed node. A tag
that already exists pointing at a DIFFERENT commit is a hard failure: that is a
real conflict, and moving it silently is the destructive reading of
"idempotent". Use `git tag -d` and decide deliberately.

Unlike `lockstep.probes.*`, this is not a probe: it mutates, and it exits
non-zero when git does. Deterministic and token-free.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _git(*args: str) -> tuple[int, str, str]:
    p = subprocess.run(["git", *args], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _commit_of(ref: str) -> str | None:
    """The COMMIT a ref resolves to, peeling an annotated tag object."""
    code, out, _ = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out or None if code == 0 else None


def _tag_exists(tag: str) -> bool:
    code, out, _ = _git("tag", "--list", tag)
    return code == 0 and bool(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="the tag name, e.g. v0.18.0")
    ap.add_argument("--message", default=None,
                    help="annotated tag with this message; omit for a lightweight tag")
    ap.add_argument("--commit", default="HEAD", help="what to tag (default HEAD)")
    ns = ap.parse_args(argv)

    target = _commit_of(ns.commit)
    if target is None:
        print(f"git_tag: {ns.commit!r} does not resolve to a commit", file=sys.stderr)
        return 1

    if _tag_exists(ns.tag):
        existing = _commit_of(ns.tag)
        if existing == target:
            # The auto-retry path, and a re-run by hand. Report, do not fail.
            kind = "annotated" if _git("cat-file", "-t", ns.tag)[1] == "tag" else "lightweight"
            print(f"{ns.tag} already points at {target[:8]} ({kind}) — nothing to do")
            return 0
        print(f"git_tag: {ns.tag} already exists and points at "
              f"{(existing or '?')[:8]}, not {target[:8]}; refusing to move it "
              f"(git tag -d {ns.tag} first, deliberately)", file=sys.stderr)
        return 1

    args = ["tag"]
    if ns.message is not None:
        args += ["-a", "-m", ns.message]
    args += [ns.tag, target]
    code, _, err = _git(*args)
    if code != 0:
        print(err or f"git tag exited {code}", file=sys.stderr)
        return code or 1

    kind = "annotated" if ns.message is not None else "lightweight"
    subject = _git("log", "-1", "--format=%s", target)[1]
    print(f"tagged {target[:8]} as {ns.tag} ({kind}) — {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
