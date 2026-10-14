"""Unit tests for ``glue_getresolvedoptions_to_argparse_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_getresolvedoptions_to_argparse_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_getresolvedoptions_to_argparse_rewrite"

_ARGPARSE_IMPORT = "import argparse"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(
        l for l in source.splitlines() if not l.lstrip().startswith("#")
    )


def _dedent(source: str) -> str:
    return textwrap.dedent(source).lstrip("\n")


# ---------------------------------------------------------------------------
# Positive: literal list / tuple of string literals
# ---------------------------------------------------------------------------


def test_literal_list_rewritten_to_argparse_block():
    src = """
    import sys
    args = getResolvedOptions(sys.argv, ["JOB_NAME", "INPUT_DATABASE"])
    """
    res = _apply(src)
    code = _code(res.source)
    assert "_parser = argparse.ArgumentParser()" in code
    assert 'for _key in ("JOB_NAME", "INPUT_DATABASE"):' in code
    assert '_parser.add_argument(f"--{_key}")' in code
    assert "args = vars(_parser.parse_known_args()[0])" in code
    assert "getResolvedOptions" not in code
    assert "SPRKCNTPY3601-Fixed" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_result_is_a_dict_not_a_namespace():
    # vars() is mandatory: downstream code does args["JOB_NAME"].
    src = 'args = getResolvedOptions(sys.argv, ["JOB_NAME"])\nprint(args["JOB_NAME"])\n'
    code = _code(_apply(src).source)
    assert "vars(" in code
    assert 'print(args["JOB_NAME"])' in code


def test_uses_parse_known_args_never_parse_args():
    # parse_args() would sys.exit(2) on the extra params Glue passes.
    src = 'args = getResolvedOptions(sys.argv, ["JOB_NAME"])\n'
    code = _code(_apply(src).source)
    assert "parse_known_args()" in code
    assert "parse_args()" not in code


def test_single_key_gets_trailing_comma():
    # ("A") is a string; ``for _key in "A"`` would iterate characters.
    src = 'args = getResolvedOptions(sys.argv, ["JOB_NAME"])\n'
    code = _code(_apply(src).source)
    assert 'for _key in ("JOB_NAME",):' in code


def test_tuple_literal_accepted():
    src = 'args = getResolvedOptions(sys.argv, ("A", "B"))\n'
    code = _code(_apply(src).source)
    assert 'for _key in ("A", "B"):' in code


def test_original_lhs_name_preserved():
    src = 'opts = getResolvedOptions(sys.argv, ["A"])\n'
    code = _code(_apply(src).source)
    assert "opts = vars(_parser.parse_known_args()[0])" in code
    assert "args =" not in code


def test_attribute_lhs_target_preserved():
    src = 'self.args = getResolvedOptions(sys.argv, ["A"])\n'
    code = _code(_apply(src).source)
    assert "self.args = vars(_parser.parse_known_args()[0])" in code


def test_dotted_call_path_accepted():
    src = """
    import awsglue.utils
    args = awsglue.utils.getResolvedOptions(sys.argv, ["A"])
    """
    code = _code(_apply(src).source)
    assert "_parser = argparse.ArgumentParser()" in code
    assert "getResolvedOptions" not in code


def test_keyword_options_argument_accepted():
    src = 'args = getResolvedOptions(args=sys.argv, options=["A", "B"])\n'
    code = _code(_apply(src).source)
    assert 'for _key in ("A", "B"):' in code


def test_indented_inside_function():
    src = """
    def main():
        opts = getResolvedOptions(sys.argv, ["A"])
        return opts
    """
    res = _apply(src)
    code = _code(res.source)
    assert "    _parser = argparse.ArgumentParser()" in code
    assert '        _parser.add_argument(f"--{_key}")' in code
    assert "    opts = vars(_parser.parse_known_args()[0])" in code
    compile(res.source, "t.py", "exec")


# ---------------------------------------------------------------------------
# Import injection
# ---------------------------------------------------------------------------


def test_argparse_import_injected_once():
    src = """
    import sys
    args = getResolvedOptions(sys.argv, ["A"])
    more = getResolvedOptions(sys.argv, ["B"])
    """
    out = _apply(src).source
    assert out.count(_ARGPARSE_IMPORT) == 1


def test_argparse_import_not_duplicated_when_present():
    src = """
    import argparse
    args = getResolvedOptions(sys.argv, ["A"])
    """
    out = _apply(src).source
    assert out.count(_ARGPARSE_IMPORT) == 1


def test_no_import_injected_for_todo_only():
    src = 'args = getResolvedOptions(sys.argv, KEYS)\n'
    out = _apply(src).source
    assert _ARGPARSE_IMPORT not in out


# ---------------------------------------------------------------------------
# Helper-name collision
# ---------------------------------------------------------------------------


def test_helper_names_avoid_collision():
    src = """
    _parser = build_my_parser()
    args = getResolvedOptions(sys.argv, ["A"])
    """
    code = _code(_apply(src).source)
    assert "_parser = build_my_parser()" in code
    assert "_parser_scos = argparse.ArgumentParser()" in code
    assert 'for _key_scos in ("A",):' in code
    assert '_parser_scos.add_argument(f"--{_key_scos}")' in code
    compile(_apply(src).source, "t.py", "exec")


# ---------------------------------------------------------------------------
# Negative: not a literal list of string literals -> TODO, code unchanged
# ---------------------------------------------------------------------------


def _assert_todo_only(src: str):
    src = _dedent(src)
    res = _apply(src)
    code = _code(res.source)
    # code itself untouched
    assert code.strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert "SPRKCNTPY3601" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_variable_options_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, KEYS)\n')


def test_comprehension_options_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, [k for k in keys])\n')


def test_mixed_list_with_non_literal_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, ["A", other])\n')


def test_fstring_element_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, [f"--{x}"])\n')


def test_starred_element_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, ["A", *extra])\n')


def test_empty_list_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv, [])\n')


def test_missing_options_argument_is_todo():
    _assert_todo_only('args = getResolvedOptions(sys.argv)\n')


def test_multi_target_assign_is_todo():
    _assert_todo_only('a = b = getResolvedOptions(sys.argv, ["A"])\n')


# ---------------------------------------------------------------------------
# Negative: recipe must not fire at all
# ---------------------------------------------------------------------------


def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_non_assignment_call_untouched():
    # A bare call has no LHS to preserve -> left for the analyzer.
    src = 'getResolvedOptions(sys.argv, ["A"])\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_already_argparse_untouched():
    src = _dedent(
        """
        import argparse

        _parser = argparse.ArgumentParser()
        for _key in ("JOB_NAME",):
            _parser.add_argument(f"--{_key}")
        args = vars(_parser.parse_known_args()[0])
        """
    )
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_rewrite():
    src = """
    import sys
    args = getResolvedOptions(sys.argv, ["JOB_NAME", "INPUT_DATABASE"])
    """
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []
    compile(once, "t.py", "exec")


def test_idempotent_todo():
    src = 'args = getResolvedOptions(sys.argv, KEYS)\n'
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []
