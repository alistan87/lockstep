"""Tests for the OKF v0.2 frontmatter layer and the docs apply engine.

Two properties matter more than the rest and are tested hardest:

  1. injecting frontmatter NEVER alters the document body (pinned by sha);
  2. the manifest checker refuses every way a reorganisation silently damages a
     repo — collisions, escapes, duplicates, reserved names.

The format is deliberately liberal ("consumers must not reject a bundle for
missing optional fields, unknown types, or broken links"), so the validator is
tested for what it must NOT reject as much as for what it must.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HYG = Path(__file__).resolve().parents[1] / "contrib" / "hygiene"


def _load(name: str):
    """Register in sys.modules BEFORE executing.

    `@dataclass` resolves its own module namespace through
    `sys.modules[cls.__module__]`, which is None for a module loaded by path and
    never registered — the decorator then dies with an unhelpful AttributeError.
    Same family as the pydantic contract-module trap documented in
    FLOW-AUTHORING.md: loading Python by file path is not the same as importing
    it, and anything that reflects on its own module will notice.
    """
    spec = importlib.util.spec_from_file_location(name, HYG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


okf = _load("okf")
apply_docs = _load("apply_docs")

BODY = "# Title\n\nSome prose.\n\n- a list\n- with `code`\n\n```json\n{\"a\": 1}\n```\n"


# --- frontmatter round-trip ----------------------------------------------------

def test_document_without_frontmatter_parses_as_all_body():
    fm, body = okf.parse(BODY)
    assert not fm.present and body == BODY


def test_injection_preserves_the_body_byte_for_byte(tmp_path):
    p = tmp_path / "d.md"
    p.write_text(BODY, encoding="utf-8", newline="")
    before = hashlib.sha256(BODY.encode()).hexdigest()

    doc = okf.load(p)
    out = okf.render(doc, {"type": "guide", "title": "Title"})
    p.write_text(out, encoding="utf-8", newline="")

    _, body_after = okf.parse(p.read_text(encoding="utf-8"))
    assert hashlib.sha256(body_after.encode()).hexdigest() == before


def test_injection_into_a_document_that_already_has_frontmatter(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntype: existing\ntags: [a, b]\n---\n" + BODY, encoding="utf-8", newline="")
    doc = okf.load(p)
    out = okf.render(doc, {"type": "OVERWRITE-ME", "title": "New"})
    fm, body = okf.parse(out)
    assert fm.fields["type"] == "existing", "an existing declaration must win"
    assert fm.fields["title"] == "New"
    assert fm.fields["tags"] == ["a", "b"]
    assert body == BODY


def test_nested_maps_round_trip(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntype: x\ngenerated:\n  by: human:ali\n  at: 2026-08-02\n---\nbody\n",
                 encoding="utf-8", newline="")
    doc = okf.load(p)
    assert doc.fm.fields["generated"]["by"] == "human:ali"
    assert "by: human:ali" in okf.emit(doc.fm.fields)


def test_unrepresentable_frontmatter_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        okf._parse_flat_yaml("sources:\n  - resource: a\n")


# --- conformance: what it must and must not reject -----------------------------

def test_missing_type_is_the_conformance_failure(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntitle: no type here\n---\nbody\n", encoding="utf-8")
    assert okf.validate(p) == ["frontmatter has no non-empty `type`"]


def test_no_frontmatter_at_all_fails(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("just prose\n", encoding="utf-8")
    assert okf.validate(p) == ["no frontmatter block"]


def test_unknown_type_is_accepted(tmp_path):
    """`type` is an open string; rejecting unknown values is forbidden."""
    p = tmp_path / "d.md"
    p.write_text("---\ntype: something-nobody-has-seen\n---\nbody\n", encoding="utf-8")
    assert okf.validate(p) == []


def test_missing_recommended_fields_are_notes_not_failures(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntype: guide\n---\nbody\n", encoding="utf-8")
    assert okf.validate(p) == []
    assert "no `title`" in okf.notes(p)


@pytest.mark.parametrize("name", ["index.md", "log.md"])
def test_reserved_filenames_are_exempt(tmp_path, name):
    p = tmp_path / name
    p.write_text("# listing\n", encoding="utf-8")
    assert okf.validate(p) == []


# --- the manifest checker: every way a repo gets damaged -----------------------

def entry(path="docs/A.md", target="docs/spec/A.md", okf_type="specification"):
    return {"path": path, "target_path": target, "okf_type": okf_type, "title": "A"}


@pytest.fixture()
def real_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "A.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "docs" / "B.md").write_text(BODY, encoding="utf-8")
    return tmp_path


def test_clean_manifest_passes(real_file):
    assert apply_docs.check_manifest([entry()]) == []


def test_two_documents_onto_one_target_is_refused(real_file):
    problems = apply_docs.check_manifest([
        entry(path="docs/A.md", target="docs/spec/X.md"),
        entry(path="docs/B.md", target="docs/spec/X.md"),
    ])
    assert any("one would be lost" in p for p in problems)


@pytest.mark.parametrize("target", ["../escape.md", "/etc/x.md", "docs/../../x.md"])
def test_targets_leaving_the_repo_are_refused(real_file, target):
    problems = apply_docs.check_manifest([entry(target=target)])
    assert any("escapes the repo" in p or "outside docs/" in p for p in problems)


def test_target_outside_docs_is_refused(real_file):
    assert any("outside docs/" in p
               for p in apply_docs.check_manifest([entry(target="src/A.md")]))


def test_reserved_target_name_is_refused(real_file):
    assert any("reserved filename" in p
               for p in apply_docs.check_manifest([entry(target="docs/spec/index.md")]))


def test_duplicate_source_is_refused(real_file):
    problems = apply_docs.check_manifest([entry(), entry(target="docs/guides/A.md")])
    assert any("more than once" in p for p in problems)


def test_missing_source_is_refused(real_file):
    assert any("does not exist" in p
               for p in apply_docs.check_manifest([entry(path="docs/NOPE.md")]))


def test_entry_without_a_type_is_refused(real_file):
    assert any("no okf_type" in p
               for p in apply_docs.check_manifest([entry(okf_type="")]))


# --- reference rewriting -------------------------------------------------------

def test_longest_path_wins_so_a_prefix_cannot_corrupt_a_longer_path():
    """`docs/A.md` must not partially rewrite `docs/A.md.bak`-style neighbours,
    and a shorter key must never be applied before a longer one that contains it."""
    moves = {"docs/A.md": "docs/spec/A.md", "docs/A.md.extra": "docs/spec/A.md.extra"}
    ordered = sorted(moves.items(), key=lambda kv: -len(kv[0]))
    assert ordered[0][0] == "docs/A.md.extra"


def test_index_generation_marks_the_bundle_root_with_okf_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written = apply_docs.write_indexes(
        [entry(target="docs/spec/A.md")], Path("docs"), apply=True)
    assert "docs/index.md" in written and "docs/spec/index.md" in written
    root = Path("docs/index.md").read_text(encoding="utf-8")
    assert 'okf_version: "0.2"' in root
    assert okf.validate(Path("docs/index.md")) == []      # reserved: exempt


def test_manifest_loading_accepts_the_catalog_shape(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"placed": [entry()], "files": [], "conflicts": []}),
                 encoding="utf-8")
    assert len(apply_docs.load_manifest(str(p))) == 1


def test_manifest_without_targets_is_rejected(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"placed": [{"path": "docs/A.md"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        apply_docs.load_manifest(str(p))


def test_tests_directory_is_never_rewritten():
    """A path inside a test is a FIXTURE, not a reference.

    The first real apply rewrote `catalog.classify("docs/SPEC.md")` — an input
    to a rule matcher — into "docs/spec/SPEC.md", changing what the test
    exercised and turning two tests red. Synthetic paths like `docs/NOPE.md`
    live in tests too and are supposed not to resolve. A tool that reorganises
    a repo must not edit the code that checks the repo.
    """
    assert "tests" in apply_docs.SKIP_PARTS
    assert not any(p.parts and p.parts[0] == "tests" for p in apply_docs.repo_files())


# --- the evidence pane must not lie -------------------------------------------

def _evidence():
    return _load("render_docs_evidence")


def test_the_pane_does_not_claim_documents_are_unedited():
    """The pane told a human 'nothing is edited, and no text inside any document
    changes' while the apply rewrote ~100 references INSIDE those documents. A
    false statement at the moment of decision is the evidence rule's central
    failure, and it was approved on."""
    text = _evidence().render({"placed": [entry()], "files": [], "conflicts": []}, None)
    assert "nothing is edited" not in text.lower()
    assert "no text inside any document" not in text.lower()
    # It must state the edits that DO happen.
    assert "header" in text.lower() and "updated" in text.lower()


def test_the_pane_and_the_indexes_share_one_source_of_truth():
    """Three rounds of corrections reached apply_docs.BUNDLE_BLURB and never
    reached the pane, because the pane kept its own copy. The surface a human
    reads and the artefact that gets published must not be able to disagree."""
    assert _evidence().BLURB is apply_docs.BUNDLE_BLURB


# --- audit findings 8-11: okf.py ----------------------------------------------

def test_block_sequences_are_valid_yaml_not_a_conformance_failure(tmp_path):
    """`sources:` entries are the format's own documented spelling. Reporting
    them as unparseable failed documents the spec explicitly permits."""
    p = tmp_path / "d.md"
    p.write_text("---\ntype: guide\nsources:\n  - resource: a\n---\nbody\n", encoding="utf-8")
    assert okf.validate(p) == []


def test_unrepresentable_frontmatter_without_a_type_still_fails(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\nsources:\n  - resource: a\n---\nbody\n", encoding="utf-8")
    assert okf.validate(p) == ["frontmatter has no non-empty `type`"]


def test_reserved_files_are_READ_not_skipped(tmp_path):
    """A reserved name is exempt from the `type` requirement, not from being
    opened. Returning before reading meant the repo's own generated indexes
    could carry broken frontmatter and be reported clean."""
    p = tmp_path / "index.md"
    p.write_text("---\nthis: is: not: yaml\n  - and neither is this\n---\nbody\n",
                 encoding="utf-8")
    assert okf.validate(p) == []          # still exempt from `type`
    p2 = tmp_path / "log.md"
    p2.write_text("# no frontmatter at all\n", encoding="utf-8")
    assert okf.validate(p2) == []


def test_a_quoted_comma_in_a_flow_sequence_is_refused_not_mangled():
    """Naive comma splitting silently corrupted the value, and render() then
    wrote the corruption back into the document."""
    with pytest.raises(okf.Unrepresentable):
        okf._parse_flat_yaml('tags: ["a, b", c]')


def test_render_preserves_a_mixed_newline_body_byte_for_byte(tmp_path):
    """The newline fix used to run over the whole document, so a body with
    mixed endings was rewritten — falsifying the module's one hard guarantee,
    and invisible to a sha test comparing an already-normalised body."""
    body = "# Title\r\n\r\nCRLF above, LF below.\n\nplain\n"
    p = tmp_path / "d.md"
    p.write_text("---\r\ntype: guide\r\n---\r\n" + body, encoding="utf-8", newline="")
    doc = okf.load(p)
    out = okf.render(doc, {"title": "T"})
    assert out.endswith(body), "body was modified"


# --- audit findings 1-7, 12-23 ------------------------------------------------

def test_dangling_regex_matches_nested_paths():
    """`/` was missing from the class, so the check matched only FLAT paths —
    the one form that no longer exists after a reorganisation. It was blind to
    every path it existed to protect."""
    import re
    rx = re.compile(r"docs(?:/[A-Za-z0-9_.+\-]+)+\.md")
    assert rx.fullmatch("docs/spec/SPEC.md")
    assert rx.fullmatch("docs/guides/FLOW-AUTHORING.md")
    assert rx.fullmatch("docs/SPEC.md")


def test_the_reorganisers_own_sources_are_not_rewritten():
    """In contrib/hygiene a path is a subject being discussed, not a link. The
    rewriter proved it by mangling the comment that explains this."""
    assert "hygiene" in apply_docs.SKIP_PARTS


def test_a_target_that_already_exists_is_refused(real_file):
    """git mv refuses it PART WAY THROUGH the loop, leaving a half-moved tree."""
    (real_file / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    (real_file / "docs" / "spec" / "A.md").write_text("existing", encoding="utf-8")
    problems = apply_docs.check_manifest([entry()])
    assert any("already exists" in p for p in problems)


def test_the_digest_covers_every_field_the_engine_consumes():
    """Hashing 3 of 6 let post-approval edits to title/status/superseded_by
    pass the tamper check and still change what got published."""
    base = entry()
    d0 = apply_docs.manifest_digest([base])
    for field in ("title", "okf_type", "target_path", "path"):
        changed = dict(base, **{field: "CHANGED"})
        assert apply_docs.manifest_digest([changed]) != d0, f"{field} not covered"
    for field in ("status", "superseded_by"):
        assert apply_docs.manifest_digest([dict(base, **{field: "x"})]) != d0, \
            f"{field} not covered"


def test_preflight_and_apply_share_one_digest():
    """Two definitions of 'the approved manifest' is how a tamper check ends up
    attesting something other than what the executor will read."""
    preflight = _load("preflight_docs")
    assert preflight.apply_docs.manifest_digest is apply_docs.manifest_digest


def test_an_unreadable_manifest_replaces_the_assurances(tmp_path):
    """It used to become an empty manifest, producing a complete, reassuring
    pane describing zero moves — and exit 0, so the approval proceeded."""
    text = _evidence().render({"__unreadable__": True}, None)
    assert "COULD NOT BE READ" in text
    assert "reject" in text.lower()
    assert "already checked without you" not in text


def test_an_unreadable_verdict_says_do_not_approve():
    ev = _evidence()
    text = ev.render({"placed": [entry()], "files": [], "conflicts": []},
                     ev.FAILED_VERDICT)
    assert "DO NOT APPROVE" in text


def test_findings_are_severity_ordered_before_truncation():
    """A blocker must never be dropped from the only surface a human decides
    from because a model happened to emit it last."""
    findings = [{"severity": "nit", "file": f"n{i}", "claim": "x"} for i in range(15)]
    findings.append({"severity": "blocker", "file": "CRITICAL", "claim": "the bad one"})
    text = _evidence().render(
        {"placed": [entry()], "files": [], "conflicts": []},
        {"verdict": "pass", "reason": "r", "findings": findings})
    assert "CRITICAL" in text
    assert "and 4 more findings" in text


def test_the_pane_states_what_is_NOT_rewritten():
    """It promised 'every reference elsewhere in the project is rewritten' while
    deliberately skipping tests/ and the reorganiser's own code."""
    text = _evidence().render({"placed": [entry()], "files": [], "conflicts": []}, None)
    assert "tests/" in text


def test_the_pane_carries_the_manifest_digest():
    """The tamper check binds an approval to a specific manifest; without the
    digest on the pane there was nothing for it to bind to."""
    text = _evidence().render({"placed": [entry()], "files": [], "conflicts": []}, None)
    assert apply_docs.manifest_digest([entry()]) in text
