"""Test 7 (SPEC §13.1): interpolation forms, fencing, spill — plus `when` semantics."""

from __future__ import annotations

import pytest

from lockstep.interpolate import (
    InterpolationError,
    ResolveCtx,
    SkippedReference,
    eval_when,
    fence_context_file,
    parse_when,
    render_template,
)


def ctx(**kw) -> ResolveCtx:
    base = dict(
        args={"topic": "moons"},
        outputs={"a": "raw text"},
        json_results={"a": {"ok": True, "items": ["x", "y"], "name": "foo"}},
        deps=["a"],
    )
    base.update(kw)
    return ResolveCtx(**base)


def render(template, c, **kw):
    defaults = dict(fence=False, max_interp_chars=20000, spill_dir=None)
    defaults.update(kw)
    return render_template(template, c, **defaults)


class TestForms:
    def test_args(self):
        assert render("hello {args.topic}", ctx()).prompt_text == "hello moons"

    def test_steps_output(self):
        assert render("<{steps.a.output}>", ctx()).prompt_text == "<raw text>"

    def test_steps_json_and_path(self):
        c = ctx()
        assert render("{steps.a.json.ok}", c).prompt_text == "true"
        # A STRING leaf inserts raw when fence=False (shell argv): the argv
        # element is already a discrete string, so JSON quotes would become
        # part of the value (r7 fix; see test_r7_fixes.py). Non-strings and
        # whole objects keep compact JSON.
        assert render("{steps.a.json.items.1}", c).prompt_text == "y"
        assert render("{steps.a.json.items}", c).prompt_text == '["x","y"]'
        assert render("{steps.a.json}", c).prompt_text == '{"ok":true,"items":["x","y"],"name":"foo"}'
        # fence=True is the §7 prompt contract: quoting stays.
        assert '"y"' in render("{steps.a.json.items.1}", c, fence=True).prompt_text

    def test_item_and_field(self):
        c = ctx(item={"path": "f.py"}, has_item=True)
        assert render("{item.path}", c).prompt_text == "f.py"  # strings insert raw
        assert render("{item}", c).prompt_text == '{"path":"f.py"}'

    def test_item_var_rename(self):
        c = ctx(item="f.py", has_item=True, item_var="file")
        assert render("{file}", c).prompt_text == "f.py"

    def test_previous_output(self):
        assert render("{previous.output}", ctx()).prompt_text == "raw text"

    def test_previous_requires_exactly_one_dep(self):
        with pytest.raises(InterpolationError, match="exactly one dependency"):
            render("{previous.output}", ctx(deps=["a", "b"]))

    def test_brace_escape(self):
        # Only "{{" escapes (to "{"); "}" needs no escape (SPEC §7).
        assert render("{{args.topic}", ctx()).prompt_text == "{args.topic}"

    def test_unresolved_is_hard_error(self):
        with pytest.raises(InterpolationError, match="unresolved"):
            render("{args.nope}", ctx())
        with pytest.raises(InterpolationError, match="unresolved"):
            render("{steps.zzz.output}", ctx())

    def test_skipped_reference_raises_or_nulls(self):
        c = ctx(skipped={"a"})
        with pytest.raises(SkippedReference):
            render("{steps.a.json}", c)
        assert render("{steps.a.json}", c, null_for_skipped=True).prompt_text == "null"


class TestFencingAndSpill:
    def test_fences_wrap_every_value(self):
        out = render("Task: {args.topic}", ctx(), fence=True)
        assert "--- begin data: args.topic (untrusted) ---" in out.prompt_text
        assert "--- end data ---" in out.prompt_text
        assert "moons" in out.prompt_text

    def test_spill_stub_in_prompt_full_value_in_hash(self, tmp_path):
        big = "z" * 5000
        c = ctx(outputs={"a": big})
        out = render("{steps.a.output}", c, fence=True, max_interp_chars=1000, spill_dir=tmp_path)
        # Prompt gets the stub: head + truncation marker + spill path.
        assert "[truncated: 5000 chars total]" in out.prompt_text
        assert big not in out.prompt_text
        spill_path = list(out.spilled.values())[0]
        assert str(spill_path) in out.prompt_text
        # The FULL value's hash enters input_hash — truncation never masks a change —
        # and the run-specific spill path is EXCLUDED from the hash (SPEC §7).
        assert big in out.hash_text
        assert str(spill_path) not in out.hash_text
        # The spill file holds the full value.
        with open(spill_path, encoding="utf-8") as f:
            assert f.read() == big

    def test_spill_hash_stable_across_run_dirs(self, tmp_path):
        big = "z" * 5000
        c = ctx(outputs={"a": big})
        out1 = render("{steps.a.output}", c, fence=True, max_interp_chars=1000, spill_dir=tmp_path / "r1")
        out2 = render("{steps.a.output}", c, fence=True, max_interp_chars=1000, spill_dir=tmp_path / "r2")
        assert out1.hash_text == out2.hash_text
        assert out1.prompt_text != out2.prompt_text  # different spill paths

    def test_context_file_fenced_and_spilled(self, tmp_path):
        small_p, small_h = fence_context_file("src/x.py", "code", max_interp_chars=100, spill_dir=tmp_path)
        assert small_p == small_h and "--- begin data: file:src/x.py (untrusted) ---" in small_p
        big = "c" * 500
        big_p, big_h = fence_context_file("src/y.py", big, max_interp_chars=100, spill_dir=tmp_path)
        assert "[truncated: 500 chars total]" in big_p
        assert big in big_h


class TestWhen:
    def test_grammar(self):
        assert parse_when('{steps.a.json.ok} == true') == ("steps.a.json.ok", "==", "true")
        for bad in ("steps.a.json.ok == true", "{a} > 5", "{a} == ", "{a} == not-json"):
            with pytest.raises(InterpolationError):
                parse_when(bad)

    def test_boolean_string_null(self):
        c = ctx()
        assert eval_when("{steps.a.json.ok} == true", c)
        assert eval_when('{steps.a.json.name} == "foo"', c)
        assert not eval_when('{steps.a.json.name} == "bar"', c)
        assert eval_when('{steps.a.json.name} != "bar"', c)

    def test_no_numeric_coercion(self):
        c = ctx(json_results={"a": {"n": 5}})
        assert eval_when("{steps.a.json.n} == 5", c)
        assert not eval_when("{steps.a.json.n} == 5.0", c)  # exact serialization match only

    def test_null_matches_skipped_upstream(self):
        # A2: `when` is exempt from transitive skip; skipped refs resolve to null.
        c = ctx(skipped={"a"})
        assert eval_when("{steps.a.json.ok} == null", c)
        assert not eval_when("{steps.a.json.ok} != null", c)
