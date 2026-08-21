"""Unit tests for the ``two_digit_year_century_window_config_rewrite`` recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_two_digit_year_century_recipe.py

The near-miss cases matter more than the positive ones here: the recipe
injects a *session-scoped* config, so a false positive changes the century
window for a workload that never parses a two-digit year.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "two_digit_year_century_window_config_rewrite"
_recipe = load_recipe_module(_RECIPE_DIR)

_KEY = "snowpark.connect.use2000AsTwoDigitCenturyStart"
_MARKER = "SCOS: [SPRKCNTPY5400-Fixed] two_digit_year_century_window_config_rewrite"

_BOOTSTRAP = """
from pyspark.sql import functions as F
from snowflake import snowpark_connect

spark = snowpark_connect.init_spark_session()
"""


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


def _with_bootstrap(body: str) -> str:
    return (textwrap.dedent(_BOOTSTRAP) + textwrap.dedent(body)).lstrip("\n")


# --------------------------------------------------------------------------
# Positive cases — every member of the yy family, on both surfaces
# --------------------------------------------------------------------------


def test_injects_for_to_timestamp_positional_format() -> None:
    new, edits = _apply(
        _with_bootstrap('out = df.select(F.to_timestamp(df.t, "yy-MM-dd"))')
    )
    assert f'spark.conf.set("{_KEY}", "true")' in new
    assert _MARKER in new
    assert len(edits) == 1
    # Injected immediately after the session bootstrap, before first use.
    assert new.index(_KEY) < new.index("F.to_timestamp")


def test_injects_for_to_timestamp_ltz_lit_wrapped_format() -> None:
    new, _ = _apply(
        _with_bootstrap(
            'out = df.select(F.to_timestamp_ltz(df.t, F.lit("yy-MM-dd HH:mm:ss")))'
        )
    )
    assert _KEY in new


def test_injects_for_to_timestamp_ntz_and_to_date_and_unix_timestamp() -> None:
    for call in (
        'F.to_timestamp_ntz(df.t, F.lit("yy/MM/dd"))',
        'F.to_date(df.t, "yy-MM-dd")',
        'F.unix_timestamp(df.t, "yy-MM-dd HH:mm:ss")',
        'F.to_unix_timestamp(df.t, "yy-MM-dd")',
        'F.try_to_timestamp(df.t, F.lit("yy-MM-dd"))',
    ):
        new, _ = _apply(_with_bootstrap(f"out = df.select({call})"))
        assert _KEY in new, call


def test_injects_for_format_keyword_argument() -> None:
    new, _ = _apply(
        _with_bootstrap('out = df.select(F.to_timestamp(df.t, format="yy-MM-dd"))')
    )
    assert _KEY in new


def test_injects_for_bare_name_import_star_spelling() -> None:
    new, _ = _apply(
        """
        from pyspark.sql.functions import *
        from snowflake import snowpark_connect

        spark = snowpark_connect.init_spark_session()
        out = df.select(to_timestamp(df.t, "yy-MM-dd"))
        """
    )
    assert _KEY in new


def test_injects_for_sql_surface() -> None:
    new, _ = _apply(
        _with_bootstrap(
            "out = spark.sql(\"SELECT TO_TIMESTAMP(t, 'yy-MM-dd') FROM tab\")"
        )
    )
    assert _KEY in new


def test_injects_for_triple_quoted_sql() -> None:
    new, _ = _apply(
        _with_bootstrap(
            'out = spark.sql("""\n'
            "    SELECT TRY_TO_TIMESTAMP(t, 'yy-MM-dd HH:mm:ss') FROM tab\n"
            '    """)'
        )
    )
    assert _KEY in new


def test_injects_inside_builder_function_using_its_own_receiver() -> None:
    new, _ = _apply(
        """
        from snowflake import snowpark_connect

        def build():
            sess = snowpark_connect.init_spark_session()
            return sess

        def run(df):
            return df.select(F.to_date(df.t, "yy-MM-dd"))
        """
    )
    assert f'sess.conf.set("{_KEY}", "true")' in new


# --------------------------------------------------------------------------
# Near misses — the recipe must NOT fire
# --------------------------------------------------------------------------


def test_four_digit_year_does_not_fire() -> None:
    new, edits = _apply(
        _with_bootstrap('out = df.select(F.to_timestamp(df.t, "yyyy-MM-dd HH:mm:ss"))')
    )
    assert _KEY not in new
    assert edits == []


def test_three_digit_and_single_y_do_not_fire() -> None:
    for fmt in ("yyy-MM-dd", "y-MM-dd", "yyyyy-MM-dd"):
        new, _ = _apply(_with_bootstrap(f'out = df.select(F.to_date(df.t, "{fmt}"))'))
        assert _KEY not in new, fmt


def test_yy_inside_java_quoted_literal_does_not_fire() -> None:
    # In a Java pattern, 'yy' quoted is the literal text "yy", not a year.
    new, _ = _apply(
        _with_bootstrap("out = df.select(F.to_timestamp(df.t, \"'yy'-yyyy-MM-dd\"))")
    )
    assert _KEY not in new


def test_format_only_api_does_not_fire() -> None:
    # date_format RENDERS a two-digit year; the century start is a parse-side
    # parameter, so rendering must not drag the config in.
    new, _ = _apply(_with_bootstrap('out = df.select(F.date_format(df.ts, "yy-MM"))'))
    assert _KEY not in new


def test_value_argument_is_not_a_format() -> None:
    # "yy" in argument 0 is data, not a pattern.
    new, _ = _apply(
        _with_bootstrap('out = df.select(F.to_date(F.lit("yy"), "yyyy-MM-dd"))')
    )
    assert _KEY not in new


def test_dynamic_format_is_left_to_the_fixer() -> None:
    # Deliberate limit: a format in a variable is not decidable here.
    new, _ = _apply(
        _with_bootstrap(
            """
            fmt = "yy-MM-dd"
            out = df.select(F.to_timestamp(df.t, fmt))
            """
        )
    )
    assert _KEY not in new


def test_unquoted_yy_in_sql_is_an_identifier_not_a_format() -> None:
    new, _ = _apply(
        _with_bootstrap('out = spark.sql("SELECT yy, TO_DATE(t) FROM tab")')
    )
    assert _KEY not in new


def test_quoted_yy_without_a_parse_function_does_not_fire() -> None:
    new, _ = _apply(
        _with_bootstrap("out = spark.sql(\"SELECT * FROM tab WHERE label = 'yy'\")")
    )
    assert _KEY not in new


def test_sql_with_four_digit_format_does_not_fire() -> None:
    new, _ = _apply(
        _with_bootstrap(
            "out = spark.sql(\"SELECT TO_TIMESTAMP(t, 'yyyy-MM-dd') FROM tab\")"
        )
    )
    assert _KEY not in new


def test_module_without_datetime_parsing_does_not_fire() -> None:
    new, edits = _apply(_with_bootstrap("out = df.select(F.col(\"t\"))"))
    assert _KEY not in new
    assert edits == []


# --------------------------------------------------------------------------
# Idempotency / no unbound names
# --------------------------------------------------------------------------


def test_already_configured_module_is_untouched() -> None:
    src = _with_bootstrap(
        f"""
        spark.conf.set("{_KEY}", "true")
        out = df.select(F.to_timestamp(df.t, "yy-MM-dd"))
        """
    )
    new, edits = _apply(src)
    assert new == src
    assert edits == []
    assert new.count(_KEY) == 1


def test_rerun_is_a_no_op() -> None:
    once, _ = _apply(_with_bootstrap('out = df.select(F.to_date(df.t, "yy-MM-dd"))'))
    twice, edits = _apply(once)
    assert twice == once
    assert edits == []


def test_no_session_anchor_emits_todo_not_unbound_code() -> None:
    new, edits = _apply(
        """
        from pyspark.sql import functions as F

        def parse(df):
            return df.select(F.to_timestamp(df.t, "yy-MM-dd"))
        """
    )
    assert "SCOS-TODO: [SPRKCNTPY5400-Warning]" in new
    # Never emit an unbound receiver: the key appears only inside the comment.
    code_lines = [
        ln for ln in new.splitlines() if not ln.lstrip().startswith("#")
    ]
    assert all(".conf.set(" not in ln for ln in code_lines)
    assert len(edits) == 1
    # The TODO fallback is also idempotent.
    again, again_edits = _apply(new)
    assert again == new
    assert again_edits == []


def test_output_is_valid_python_and_preserves_the_call() -> None:
    import ast

    new, _ = _apply(
        _with_bootstrap('out = df.select(F.to_timestamp(df.t, "yy-MM-dd"))')
    )
    ast.parse(new)
    assert 'F.to_timestamp(df.t, "yy-MM-dd")' in new  # call itself untouched


def test_helper_standalone_yy_matrix() -> None:
    assert _recipe.has_standalone_yy("yy-MM-dd")
    assert _recipe.has_standalone_yy("dd/MM/yy")
    assert _recipe.has_standalone_yy("MM-yy HH:mm")
    assert not _recipe.has_standalone_yy("yyyy-MM-dd")
    assert not _recipe.has_standalone_yy("yyy")
    assert not _recipe.has_standalone_yy("y")
    assert not _recipe.has_standalone_yy("'yy'")
    assert not _recipe.has_standalone_yy("HH:mm:ss")
