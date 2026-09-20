#!/usr/bin/env python
"""git_tag.py — create a release tag and SAY SO. The release-cut tag node body.

    python contrib/git_tag.py --tag v0.18.0                 # annotated
    python contrib/git_tag.py --tag v0.18.0 --message "0.18.0: the ... release"
    python contrib/git_tag.py --tag v0.18.0 --lightweight   # bare tag object
    python contrib/git_tag.py --tag v0.18.0 --commit <sha>

**Annotated is the default.** v0.9.0 and v0.16.0 are annotated; v0.17.0 went
out lightweight only because the flow node said nothing either way, and a
release tag that carries no author, date or message is worth less than the
one before it. `--lightweight` is still there, but it has to be asked for.

The default message is the TAG NAME. It is predictable and never wrong;
deriving one from the tagged commit's subject would describe the last commit
("passdown 0.16.0 -> 0.17.0 for the work mirror"), not the release. Pass
`--message` for a real one. An EMPTY `--message` falls back to the default
rather than erroring, so a flow can wire `--message "{args.message}"` against
an optional arg and still produce a proper tag when nobody sets it.

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


# Output stays ASCII on purpose: this stdout IS the node's result channel, and
# a piped CPython on Windows encodes with the locale codec (cp1252 here), so an
# em dash reaches `shell.py` as a byte that is not valid UTF-8 and lands in the
# recorded result as a replacement character.
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="the tag name, e.g. v0.18.0")
    ap.add_argument("--message", default="",
                    help="annotation message; empty or omitted uses the tag name")
    ap.add_argument("--lightweight", action="store_true",
                    help="make a bare tag object instead of an annotated one")
    ap.add_argument("--commit", default="HEAD", help="what to tag (default HEAD)")
    ns = ap.parse_args(argv)

    message = (ns.message or "").strip()
    if ns.lightweight and message:
        # Dropping the message silently is how a release ends up with a tag
        # nobody can explain.
        print("git_tag: --lightweight and --message contradict each other; "
              "pick one", file=sys.stderr)
        return 2
    annotate = not ns.lightweight

    target = _commit_of(ns.commit)
    if target is None:
        print(f"git_tag: {ns.commit!r} does not resolve to a commit", file=sys.stderr)
        return 1

    if _tag_exists(ns.tag):
        existing = _commit_of(ns.tag)
        if existing == target:
            # The auto-retry path, and a re-run by hand. Report, do not fail.
            kind = "annotated" if _git("cat-file", "-t", ns.tag)[1] == "tag" else "lightweight"
            print(f"{ns.tag} already points at {target[:8]} ({kind}) - nothing to do")
            return 0
        print(f"git_tag: {ns.tag} already exists and points at "
              f"{(existing or '?')[:8]}, not {target[:8]}; refusing to move it "
              f"(git tag -d {ns.tag} first, deliberately)", file=sys.stderr)
        return 1

    args = ["tag"]
    if annotate:
        args += ["-a", "-m", message or ns.tag]
    args += [ns.tag, target]
    code, _, err = _git(*args)
    if code != 0:
        print(err or f"git tag exited {code}", file=sys.stderr)
        return code or 1

    kind = "annotated" if annotate else "lightweight"
    subject = _git("log", "-1", "--format=%s", target)[1]
    print(f"tagged {target[:8]} as {ns.tag} ({kind}) - {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
