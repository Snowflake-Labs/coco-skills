"""Unit tests for the ``spark_config_noop_annotate`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_spark_config_noop_recipe.py
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

_RECIPE_DIR = _RECIPES_DIR / "spark_config_noop_annotate"
_recipe = load_recipe_module(_RECIPE_DIR)

_MARKER = "SCOS-WARN: [SPRKCNTPY3500-Warning] spark_config_noop_annotate"


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


# --------------------------------------------------------------------------
# Positive cases — recipe must annotate (but never alter the code line)
# --------------------------------------------------------------------------


def test_annotates_executor_memory_conf_set() -> None:
    new, edits = _apply(
        """
        spark.conf.set("spark.executor.memory", "4g")
        """
    )
    assert _MARKER in new
    assert "spark.executor.memory" in new
    # Code line itself is preserved verbatim (annotate-only, no removal).
    assert 'spark.conf.set("spark.executor.memory", "4g")' in new
    assert len(edits) == 1


def test_annotates_exact_key_driver_memory() -> None:
    new, edits = _apply(
        """
        spark.conf.set("spark.driver.memory", "8g")
        """
    )
    assert _MARKER in new
    assert len(edits) == 1


def test_annotates_prefix_family_dynamic_allocation() -> None:
    new, edits = _apply(
        """
        spark.conf.set("spark.dynamicAllocation.enabled", "true")
        """
    )
    assert _MARKER in new
    assert len(edits) == 1


def test_annotates_builder_config_form() -> None:
    # Bare .config(...) call (outside a getOrCreate chain the builder recipe
    # would rewrite) is still detected.
    new, edits = _apply(
        """
        builder.config("spark.shuffle.service.enabled", "true")
        """
    )
    assert _MARKER in new
    assert len(edits) == 1


def test_two_noop_sets_both_annotated() -> None:
    new, edits = _apply(
        """
        spark.conf.set("spark.executor.cores", "4")
        spark.conf.set("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
        """
    )
    # spark.executor.cores (prefix) annotated; spark.serializer is NOT in the
    # no-op lists (kryo* is, but bare ``spark.serializer`` is not) -> only one.
    assert new.count(_MARKER) == 1
    assert len(edits) == 1


def test_kryo_prefix_annotated() -> None:
    new, edits = _apply(
        """
        spark.conf.set("spark.kryo.registrationRequired", "true")
        """
    )
    assert _MARKER in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Negative cases — honored / semantics-affecting / unknown keys: NO change
# --------------------------------------------------------------------------


def test_preserves_session_timezone() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("spark.sql.session.timeZone", "UTC")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_preserves_ansi_enabled() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("spark.sql.ansi.enabled", "true")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_preserves_sql_shuffle_partitions() -> None:
    # spark.sql.* is guarded as honored even though shuffle.* (non-sql) is a
    # no-op family — the spark.sql. prefix guard wins.
    src = textwrap.dedent(
        """
        spark.conf.set("spark.sql.shuffle.partitions", "200")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_preserves_s3a_credentials() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("spark.hadoop.fs.s3a.access.key", "AKIA...")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_preserves_snowpark_connect_knob() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("snowpark.connect.integralTypesEmulation", "enabled")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_preserves_app_name_and_jars() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("spark.app.name", "job")
        spark.conf.set("spark.jars", "/path/to/driver.jar")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_unknown_key_deferred_not_annotated() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set("spark.some.future.knob", "1")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_non_string_key_ignored() -> None:
    src = textwrap.dedent(
        """
        spark.conf.set(key_var, "4g")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_unrelated_set_call_ignored() -> None:
    # ``.set(...)`` whose receiver is not ``.conf`` must not match.
    src = textwrap.dedent(
        """
        my_dict.set("spark.executor.memory", "4g")
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
        spark.conf.set("spark.executor.memory", "4g")
        """
    )
    assert len(edits1) == 1
    new2, edits2 = _apply(new1)
    assert new2 == new1
    assert edits2 == []
