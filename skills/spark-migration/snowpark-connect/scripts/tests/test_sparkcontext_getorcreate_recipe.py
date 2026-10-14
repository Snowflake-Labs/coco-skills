"""Unit tests for the
``sparkcontext_getorcreate_init_session_rewrite`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_sparkcontext_getorcreate_recipe.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# Make ``scripts/recipes`` importable so we can load the recipe module
# directly (same pattern preprocess_recipes.py uses).
_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "sparkcontext_getorcreate_init_session_rewrite"
_recipe = load_recipe_module(_RECIPE_DIR)


def _apply(src: str):
    """Convenience: run the recipe and return ``(new_source, edits)``."""
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


def _code_only(src: str) -> str:
    """Strip ``# SCOS`` recipe comment lines so assertions on the rewritten
    code don't accidentally match the recipe's own comments."""
    return "\n".join(line for line in src.splitlines() if "# SCOS" not in line)


# --------------------------------------------------------------------------
# Positive cases — recipe must rewrite
# --------------------------------------------------------------------------


def test_sparkcontext_getorcreate_assign() -> None:
    new, edits = _apply(
        """
        from pyspark import SparkContext
        sc = SparkContext.getOrCreate()
        """
    )
    code = _code_only(new)
    assert "SparkContext.getOrCreate()" not in code
    assert "sc = snowpark_connect.init_spark_session()" in code
    assert "from snowflake import snowpark_connect" in code
    assert _recipe.RECIPE_ID in new  # leading comment present
    assert len(edits) == 1


def test_sparksession_ctor_wrapping_context() -> None:
    new, edits = _apply(
        """
        spark = SparkSession(sc)
        """
    )
    code = _code_only(new)
    assert "SparkSession(sc)" not in code
    assert "spark = snowpark_connect.init_spark_session()" in code
    assert len(edits) == 1


def test_two_line_idiom_both_names_preserved() -> None:
    """The canonical legacy idiom: both ``sc`` and ``spark`` must stay
    defined (init_spark_session is idempotent), so downstream ``sc.*`` use
    still resolves to a name."""
    new, edits = _apply(
        """
        from pyspark import SparkContext
        from pyspark.sql import SparkSession
        sc = SparkContext.getOrCreate()
        spark = SparkSession(sc)
        """
    )
    code = _code_only(new)
    assert "sc = snowpark_connect.init_spark_session()" in code
    assert "spark = snowpark_connect.init_spark_session()" in code
    assert code.count("snowpark_connect.init_spark_session()") == 2
    # One edit per rewritten statement.
    assert len(edits) == 2
    # Import injected exactly once.
    assert new.count("from snowflake import snowpark_connect") == 1


def test_sparkcontext_constructor_form() -> None:
    new, _ = _apply(
        """
        sc = SparkContext(conf=conf)
        """
    )
    code = _code_only(new)
    assert "sc = snowpark_connect.init_spark_session()" in code
    # A dropped SparkConf must be surfaced as a visible warning.
    assert "SCOS-WARN" in new


def test_qualified_sparkcontext_getorcreate() -> None:
    new, edits = _apply(
        """
        import pyspark
        sc = pyspark.SparkContext.getOrCreate()
        """
    )
    code = _code_only(new)
    assert "sc = snowpark_connect.init_spark_session()" in code
    assert len(edits) == 1


def test_nested_sparksession_wrapping_getorcreate() -> None:
    new, edits = _apply(
        """
        spark = SparkSession(SparkContext.getOrCreate())
        """
    )
    code = _code_only(new)
    assert "spark = snowpark_connect.init_spark_session()" in code
    # The whole RHS is replaced in a single edit (the outer ctor wins).
    assert len(edits) == 1


def test_getorcreate_with_conf_arg_warns() -> None:
    new, _ = _apply(
        """
        sc = SparkContext.getOrCreate(conf)
        """
    )
    assert "snowpark_connect.init_spark_session()" in new
    assert "SCOS-WARN" in new


def test_return_form() -> None:
    new, edits = _apply(
        """
        def build():
            return SparkSession(sc)
        """
    )
    code = _code_only(new)
    assert "return snowpark_connect.init_spark_session()" in code
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Negative cases — recipe must NOT fire
# --------------------------------------------------------------------------


def test_no_change_for_builder_chain() -> None:
    """``SparkSession.builder...getOrCreate()`` is owned by the builder
    recipe — this one must leave it alone."""
    src = textwrap.dedent(
        """
        spark = SparkSession.builder.appName('x').getOrCreate()
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_change_for_get_active_session() -> None:
    src = textwrap.dedent(
        """
        spark = SparkSession.getActiveSession()
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_change_for_already_migrated() -> None:
    src = textwrap.dedent(
        """
        from snowflake import snowpark_connect
        spark = snowpark_connect.init_spark_session()
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_change_for_property_access() -> None:
    """Property / method access on an established binding is owned by
    ``sparkcontext_property_fallback_rewrite``."""
    src = textwrap.dedent(
        """
        app_id = sc.applicationId
        rdd = sc.parallelize([1, 2, 3])
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_idempotent_second_pass_is_noop() -> None:
    new1, edits1 = _apply(
        """
        sc = SparkContext.getOrCreate()
        spark = SparkSession(sc)
        """
    )
    assert len(edits1) == 2

    new2, edits2 = _apply(new1)
    # Second pass must not change source and must not record another edit.
    assert new2 == new1
    assert edits2 == []
