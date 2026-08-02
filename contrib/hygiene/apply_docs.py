#!/usr/bin/env python
"""apply_docs.py — deterministic execution of a docs reorganisation manifest.

    python contrib/hygiene/apply_docs.py --manifest <file> [--check] [--branch NAME]

No model is involved. A manifest names moves; this performs them, rewrites every
reference to the moved paths, injects OKF frontmatter, writes bundle indexes,
and then VERIFIES the result. `--check` does everything except touch the repo.

Order matters and is not arbitrary:

  1. refuse on a dirty tree          - so the diff is reviewable and revertable
  2. branch                          - git is the second gate; nothing lands on main
  3. git mv                          - history follows the file
  4. rewrite references              - BEFORE frontmatter, so a failure here
                                       leaves a tree with moved files and intact
                                       git history rather than half-annotated docs
  5. inject frontmatter              - body byte-exact
  6. write bundle indexes            - reserved names per OKF
  7. verify                          - zero dangling refs, full conformance

Verification failure is a HARD failure that leaves the branch for autopsy. It
does not attempt repair: a half-repaired reorganisation is harder to reason
about than a clearly broken one.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okf  # noqa: E402

# Never rewritten: build outputs, run dirs, the venv, and the vendored spec.
SKIP_PARTS = {"build", "dist", "runs", ".venv", ".git", "__pycache__", "node_modules"}
REWRITE_SUFFIXES = {".md", ".py", ".json", ".ps1", ".cmd", ".toml", ".lua", ".txt"}


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], capture_output=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_files() -> list[Path]:
    out = set()
    for extra in (["ls-files", "-z"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        for f in git("-c", "core.quotepath=off", *extra).split("\x00"):
            f = f.strip()
            if not f:
                continue
            p = Path(f)
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            if p.suffix in REWRITE_SUFFIXES:
                out.add(p)
    return sorted(out)


def load_manifest(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("placed") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("manifest has no `placed` list")
    for e in entries:
        if not e.get("path") or not e.get("target_path"):
            raise ValueError(f"entry missing path/target_path: {e}")
    return entries


def check_manifest(entries: list[dict]) -> list[str]:
    """Deterministic refusals. Each one is a way a repo gets silently damaged."""
    problems, seen_t, seen_s = [], {}, set()
    for e in entries:
        src, dst = e["path"], e["target_path"]
        if src in seen_s:
            problems.append(f"{src}: listed more than once")
        seen_s.add(src)
        if ".." in Path(dst).parts or Path(dst).is_absolute():
            problems.append(f"{dst}: escapes the repo")
        if not dst.startswith("docs/"):
            problems.append(f"{dst}: outside docs/")
        if dst in seen_t and seen_t[dst] != src:
            problems.append(f"{dst}: claimed by both {seen_t[dst]} and {src} — one would be lost")
        seen_t[dst] = src
        if Path(dst).name in okf.RESERVED:
            problems.append(f"{dst}: uses an OKF reserved filename")
        if not Path(src).is_file():
            problems.append(f"{src}: does not exist")
        if not e.get("okf_type"):
            problems.append(f"{src}: no okf_type")
    return problems


def rewrite_references(moves: dict[str, str], apply: bool) -> tuple[int, int]:
    """Replace every occurrence of each source path with its target.

    Longest-first so `docs/SPEC.md` cannot be partially rewritten by a shorter
    key, and both separators are handled because Windows paths appear in prose.
    """
    ordered = sorted(moves.items(), key=lambda kv: -len(kv[0]))
    files_changed = refs_changed = 0
    for f in repo_files():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        original = text
        for src, dst in ordered:
            for a, b in ((src, dst), (src.replace("/", "\\"), dst.replace("/", "\\"))):
                if a in text:
                    refs_changed += text.count(a)
                    text = text.replace(a, b)
        if text != original:
            files_changed += 1
            if apply:
                f.write_text(text, encoding="utf-8", newline="")
    return files_changed, refs_changed


def inject_frontmatter(entry: dict, path: Path, apply: bool) -> bool:
    """Add OKF fields, preserving the body byte for byte. Returns True if the
    file needed a change. Existing keys are never overwritten — a document that
    already declares its own `type` knows better than this rule table."""
    doc = okf.load(path)
    fields = {
        "type": entry["okf_type"],
        "title": entry.get("title") or path.stem,
        "resource": entry["target_path"],
    }
    if all(k in doc.fm.fields for k in fields):
        return False
    text = okf.render(doc, fields)
    if apply:
        path.write_text(text, encoding="utf-8", newline="")
    return True


BUNDLE_BLURB = {
    "spec": ("The contract. Changing anything here changes what the software is "
             "allowed to do; the deviations register lives beside it because it "
             "only means something next to the spec."),
    "guides": ("How to use the system. Wrong-but-fixable — editing one of these "
               "breaks no promise."),
    "proposals": ("Design under consideration, plus accepted work orders. "
                  "Explicitly NOT authoritative, which is why they do not sit "
                  "beside the specification."),
    "audits": ("Point-in-time findings. Never edited after the fact — an audit "
               "that gets updated is not an audit."),
    "notes": "Working material. No stability promise.",
}


def write_indexes(entries: list[dict], root: Path, apply: bool) -> list[str]:
    """OKF bundle indexes. `index.md` is a reserved name and is exempt from the
    `type` requirement; the bundle ROOT additionally declares okf_version."""
    written = []
    bundles: dict[str, list[dict]] = {}
    for e in entries:
        bundles.setdefault(Path(e["target_path"]).parent.as_posix(), []).append(e)

    for bundle, items in sorted(bundles.items()):
        name = Path(bundle).name
        lines = [
            "---",
            f"title: {name}",
            f"description: {BUNDLE_BLURB.get(name, 'Documents in this bundle.')}",
            "---",
            "",
            f"# {name}",
            "",
            BUNDLE_BLURB.get(name, ""),
            "",
            "| document | type | title |",
            "|---|---|---|",
        ]
        for e in sorted(items, key=lambda e: e["target_path"]):
            fn = Path(e["target_path"]).name
            lines.append(f"| [{fn}]({fn}) | `{e['okf_type']}` | {e.get('title', '')} |")
        lines.append("")
        target = Path(bundle) / "index.md"
        written.append(target.as_posix())
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines), encoding="utf-8", newline="")

    root_lines = [
        "---",
        'okf_version: "0.2"',
        "title: lockstep documentation",
        "description: Documents grouped by authority and lifecycle, not by subject.",
        "---",
        "",
        "# lockstep documentation",
        "",
        "Grouped by **authority and lifecycle** — the question a reader actually",
        "has is \"can I rely on this?\", and the answer differs per bundle.",
        "",
        "| bundle | what it is |",
        "|---|---|",
    ]
    for bundle in sorted(bundles):
        name = Path(bundle).name
        root_lines.append(f"| [{name}]({name}/index.md) | {BUNDLE_BLURB.get(name, '')} |")
    root_lines += ["", "Vendored third-party references live in `okf/` and are not",
                   "reorganised or annotated by this repo.", ""]
    root_index = root / "index.md"
    written.append(root_index.as_posix())
    if apply:
        root_index.write_text("\n".join(root_lines), encoding="utf-8", newline="")
    return written


def verify(entries: list[dict], root: Path) -> list[str]:
    """The gate that decides whether the branch is usable."""
    problems = []
    for e in entries:
        dst = Path(e["target_path"])
        if not dst.is_file():
            problems.append(f"{dst}: missing after apply")
            continue
        for issue in okf.validate(dst):
            problems.append(f"{dst}: {issue}")
        if Path(e["path"]).is_file():
            problems.append(f"{e['path']}: still present after move")

    # Zero dangling references: no file may still point at an old location.
    stale = {e["path"] for e in entries}
    for f in repo_files():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for old in stale:
            if old in text or old.replace("/", "\\") in text:
                problems.append(f"{f.as_posix()}: still references {old}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default="docs")
    ap.add_argument("--branch", default=None, help="branch to create (default: none, apply in place)")
    ap.add_argument("--check", action="store_true", help="report only; touch nothing")
    ns = ap.parse_args(argv)

    entries = load_manifest(ns.manifest)
    problems = check_manifest(entries)
    if problems:
        print("manifest REFUSED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    apply = not ns.check
    if apply:
        dirty = git("status", "--porcelain").strip()
        if dirty:
            print("refusing to apply on a dirty working tree - commit or stash first.",
                  file=sys.stderr)
            print("  (the point of applying on a branch is a reviewable diff)", file=sys.stderr)
            return 2
        if ns.branch:
            git("checkout", "-b", ns.branch)
            print(f"branch: {ns.branch}")

    moves = {e["path"]: e["target_path"] for e in entries}

    for e in entries:
        dst = Path(e["target_path"])
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            git("mv", e["path"], e["target_path"])
    print(f"moved: {len(entries)} documents")

    files_changed, refs_changed = rewrite_references(moves, apply)
    print(f"references rewritten: {refs_changed} in {files_changed} files")

    injected = 0
    for e in entries:
        path = Path(e["target_path"]) if apply else Path(e["path"])
        if path.is_file() and inject_frontmatter(e, path, apply):
            injected += 1
    print(f"frontmatter injected: {injected}")

    written = write_indexes(entries, Path(ns.root), apply)
    print(f"bundle indexes: {len(written)}")

    if not apply:
        print("\n--check: nothing was modified.")
        return 0

    problems = verify(entries, Path(ns.root))
    if problems:
        print("\nVERIFY FAILED - branch left for autopsy, nothing merged:", file=sys.stderr)
        for p in problems[:40]:
            print(f"  - {p}", file=sys.stderr)
        return 3
    print("\nverify: zero dangling references, all documents conformant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
