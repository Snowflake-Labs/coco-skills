"""Unit tests for the ``spark_io_detect`` recipe.

Covers the read/write API surface enumerated across the customer workloads:
file I/O (cloud / local / wildcard / variable paths) -> IO, JDBC -> Error,
streaming -> Error, Iceberg catalog I/O -> IO, and the skips (Snowflake tables,
@stage paths, and the delta/snowflake formats owned by dedicated recipes).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))
from _common import load_recipe_module  # noqa: E402

_recipe = load_recipe_module(_RECIPES_DIR / "spark_io_detect")


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


def _marker(src: str) -> str:
    new, _ = _apply(src)
    return next((l for l in new.splitlines() if l.lstrip().startswith("# SCOS:")), "")


# --- file I/O -> IO (SPRKCNTPY3200) ------------------------------------------

def test_write_format_save_cloud_path_is_io():
    m = _marker('df.write.format("parquet").mode("overwrite").save("s3://bucket/out")')
    assert "[SPRKCNTPY3200-IO]" in m and "write to" in m


def test_write_shorthand_local_path_is_io():
    m = _marker('df.write.parquet("/mnt/data/out")')
    assert "[SPRKCNTPY3200-IO]" in m


def test_read_wildcard_glob_is_io():
    m = _marker('spark.read.parquet("s3://b/x/*.parquet")')
    assert "[SPRKCNTPY3200-IO]" in m and "read from" in m


def test_read_format_load_with_options_is_io():
    m = _marker('spark.read.format("json").option("k", "v").load("s3://b/x")')
    assert "[SPRKCNTPY3200-IO]" in m


def test_read_nonliteral_path_variable_is_io():
    # A path variable is still file I/O — it must resolve to a stage on SCOS.
    m = _marker("spark.read.json(path_var)")
    assert "[SPRKCNTPY3200-IO]" in m


# --- Iceberg catalog I/O -> IO (SPRKCNTPY3200) -------------------------------

def test_read_iceberg_format_is_io():
    m = _marker('spark.read.format("iceberg").load("db.schema.tbl")')
    assert "[SPRKCNTPY3200-IO]" in m and "Iceberg" in m and "reads from" in m


def test_write_iceberg_format_is_io():
    m = _marker('df.write.format("iceberg").mode("overwrite").save("db.schema.tbl")')
    assert "[SPRKCNTPY3200-IO]" in m and "Iceberg" in m and "writes to" in m


# --- JDBC / streaming -> Error -----------------------------------------------

def test_read_jdbc_is_error():
    m = _marker('spark.read.jdbc(url, "tbl", properties=props)')
    assert "[SPRKCNTPY6000-Error]" in m


def test_write_format_jdbc_is_error():
    m = _marker('df.write.format("jdbc").option("url", u).save()')
    assert "[SPRKCNTPY6000-Error]" in m


def test_read_stream_is_error():
    m = _marker('spark.readStream.format("kafka").load()')
    assert "[SPRKCNTPY2000-Error]" in m


def test_write_stream_is_error():
    m = _marker('df.writeStream.format("console").start()')
    assert "[SPRKCNTPY2000-Error]" in m


# --- table I/O -> IO (verify Snowflake table name) --------------------------

def test_save_as_table_is_io():
    m = _marker('df.write.saveAsTable("db.schema.t")')
    assert "[SPRKCNTPY3200-IO]" in m and "table I/O" in m and "writes to" in m


def test_insert_into_is_io():
    m = _marker('df.write.insertInto("db.t")')
    assert "[SPRKCNTPY3200-IO]" in m and "writes to" in m


def test_read_table_is_io():
    m = _marker('spark.read.table("db.t")')
    assert "[SPRKCNTPY3200-IO]" in m and "reads from" in m


# --- skips (no marker) -------------------------------------------------------

def test_delta_format_on_table_terminal_left_to_dedicated_recipe():
    # delta/snowflake formats stay owned by their dedicated recipes even on a
    # table terminal — no table-verification marker.
    new, edits = _apply('df.write.format("delta").saveAsTable("db.t")')
    assert edits == []


def test_stage_path_is_not_flagged():
    new, edits = _apply('spark.read.parquet("@my_stage/path")')
    assert edits == []


def test_delta_format_left_to_dedicated_recipe():
    new, edits = _apply('df.write.format("delta").save("s3://b/d")')
    assert edits == []


def test_snowflake_format_left_to_dedicated_recipe():
    new, edits = _apply('spark.read.format("snowflake").option("dbtable", t).load()')
    assert edits == []


def test_non_spark_json_call_is_ignored():
    # requests' .json() has no .read/.write anchor -> must not match.
    new, edits = _apply("resp = requests.get(u).json()")
    assert edits == []


def test_non_io_dataframe_op_is_ignored():
    new, edits = _apply('df.select("a").show()')
    assert edits == []


# --- idempotency -------------------------------------------------------------

def test_idempotent_second_pass_is_noop():
    src = 'df.write.parquet("s3://b/out")\n'
    once, e1 = _apply(src)
    twice = _recipe.apply(once, file="t.py")
    assert len(e1) == 1
    assert twice.edits == [] and twice.source == once
