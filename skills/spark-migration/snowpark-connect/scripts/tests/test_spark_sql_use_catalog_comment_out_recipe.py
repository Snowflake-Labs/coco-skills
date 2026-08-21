"""Unit tests for ``spark_sql_use_catalog_comment_out_rewrite`` LibCST recipe.

Tests that ``spark.sql("USE CATALOG ...")`` / ``SET CATALOG`` statements are
commented out with a SCOS note pointing developers at fully-qualified names or
``SnowflakeSession``, and that supported ``USE DATABASE`` / ``USE SCHEMA`` /
dynamic SQL are left untouched.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_spark_sql_use_catalog_comment_out_recipe.py
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "spark_sql_use_catalog_comment_out_rewrite"
_recipe = load_recipe_module(_RECIPE_DIR)

_MARKER = "spark_sql_use_catalog_comment_out_rewrite"


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


# --------------------------------------------------------------------------
# Positive cases — commented out
# --------------------------------------------------------------------------


def test_comments_out_use_catalog() -> None:
    new, edits = _apply(
        """
        spark.sql("USE CATALOG na_global_risk_systems_explore")
        """
    )
    assert _MARKER in new
    # Original code preserved as a comment
    assert '# spark.sql("USE CATALOG na_global_risk_systems_explore")' in new
    # SCOS explanation is present
    assert "only supports the built-in `snowflake` catalog" in new
    assert "SnowflakeSession" in new
    assert "fully-qualified names" in new
    # Body replaced with pass
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_use_catalog_with_collect() -> None:
    new, edits = _apply(
        """
        spark.sql("USE CATALOG my_catalog").collect()
        """
    )
    assert _MARKER in new
    assert '# spark.sql("USE CATALOG my_catalog").collect()' in new
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_set_catalog_lowercase() -> None:
    new, edits = _apply(
        """
        spark.sql("set catalog my_catalog")
        """
    )
    assert _MARKER in new
    assert '# spark.sql("set catalog my_catalog")' in new
    assert "pass" in new
    assert len(edits) == 1


def test_preserves_indentation_in_function() -> None:
    new, edits = _apply(
        """
        def setup(spark):
            spark.sql("USE CATALOG my_catalog")
            return spark
        """
    )
    assert _MARKER in new
    assert "pass" in new
    assert "return spark" in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# DataFrame/Catalog-API form — spark.catalog.setCurrentCatalog(...)
# --------------------------------------------------------------------------


def test_comments_out_set_current_catalog() -> None:
    new, edits = _apply(
        """
        spark.catalog.setCurrentCatalog("my_catalog")
        """
    )
    assert _MARKER in new
    assert '# spark.catalog.setCurrentCatalog("my_catalog")' in new
    assert "only supports the built-in `snowflake` catalog" in new
    assert "SnowflakeSession" in new
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_set_current_catalog_other_receiver() -> None:
    """Matched on the method name, so any receiver spelling works."""
    new, edits = _apply(
        """
        session.catalog.setCurrentCatalog(name)
        """
    )
    assert _MARKER in new
    assert "# session.catalog.setCurrentCatalog(name)" in new
    assert "pass" in new
    assert len(edits) == 1


def test_embedded_set_current_catalog_is_annotated_not_removed() -> None:
    new, edits = _apply(
        """
        x = spark.catalog.setCurrentCatalog("my_catalog")
        """
    )
    assert _MARKER in new
    assert "SCOS-TODO" in new
    assert 'x = spark.catalog.setCurrentCatalog("my_catalog")' in new
    assert "pass" not in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Embedded form — annotate only, do not remove
# --------------------------------------------------------------------------


def test_embedded_assignment_is_annotated_not_removed() -> None:
    new, edits = _apply(
        """
        df = spark.sql("USE CATALOG my_catalog")
        """
    )
    assert _MARKER in new
    assert "SCOS-TODO" in new
    # The original statement is preserved (not turned into pass)
    assert 'df = spark.sql("USE CATALOG my_catalog")' in new
    assert "pass" not in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Negative cases — must NOT match
# --------------------------------------------------------------------------


def test_no_match_use_database() -> None:
    src = textwrap.dedent(
        """
        spark.sql("USE DATABASE my_db")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_use_schema() -> None:
    src = textwrap.dedent(
        """
        spark.sql("USE SCHEMA my_schema")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_bare_use_namespace() -> None:
    src = textwrap.dedent(
        """
        spark.sql("USE my_namespace")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_use_catalogdb_prefix() -> None:
    """A database literally named starting with 'catalog' must not match."""
    src = textwrap.dedent(
        """
        spark.sql("USE catalogdb")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_dynamic_fstring() -> None:
    src = textwrap.dedent(
        """
        spark.sql(f"USE CATALOG {name}")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_dynamic_variable() -> None:
    src = textwrap.dedent(
        """
        spark.sql(stmt)
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_set_current_database() -> None:
    """setCurrentDatabase maps to USE SCHEMA (supported) → must not match."""
    src = textwrap.dedent(
        """
        spark.catalog.setCurrentDatabase("my_db")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_current_catalog_getter() -> None:
    """The currentCatalog() getter is not a switch → must not match."""
    src = textwrap.dedent(
        """
        c = spark.catalog.currentCatalog()
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_unrelated_sql() -> None:
    src = textwrap.dedent(
        """
        spark.sql("SELECT * FROM t")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_idempotent() -> None:
    new1, edits1 = _apply(
        """
        spark.sql("USE CATALOG my_catalog")
        """
    )
    assert len(edits1) == 1
    new2, edits2 = _apply(new1)
    assert new2 == new1
    assert edits2 == []


def test_idempotent_embedded() -> None:
    new1, edits1 = _apply(
        """
        df = spark.sql("USE CATALOG my_catalog")
        """
    )
    assert len(edits1) == 1
    new2, edits2 = _apply(new1)
    assert new2 == new1
    assert edits2 == []
