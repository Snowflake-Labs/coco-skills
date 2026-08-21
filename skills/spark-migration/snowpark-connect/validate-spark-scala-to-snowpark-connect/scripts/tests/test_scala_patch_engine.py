"""Tests for scala_patch_engine.py — Scala-native known-patches (PySpark parity)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scala_patch_engine as spe  # noqa: E402


def test_suggest_save_as_table_env_indirection():
    src = 'df.write.saveAsTable("orders_out")\n'
    patches = spe.suggest_known_patches(src, "Output/src/Job.scala")
    p = next(p for p in patches if p["id"].startswith("save_as_table_"))
    assert "SCOS_SINK_" in p["replace"]
    assert "System.getProperty" in p["replace"]


def test_suggest_widget_declaration_neutralize():
    src = 'dbutils.widgets.text("batch_date", "2024-01-01")\n'
    patches = spe.suggest_known_patches(src, "Output/src/Job.scala")
    assert any(p["id"].startswith("widget_decl_") for p in patches)


def test_suggest_widget_get_uses_system_get_property():
    src = 'val x = dbutils.widgets.get("batch_date")\n'
    patches = spe.suggest_known_patches(src, "Output/src/Job.scala")
    assert patches
    p = next(p for p in patches if p["id"].startswith("widget_get_"))
    assert "System.getProperty" in p["replace"]
    assert "os.environ" not in p["replace"]
    assert "SCOS_WIDGET_BATCH_DATE" in p["replace"]


def test_suggest_drop_table_and_notebook_exit():
    src = '''
spark.sql("DROP TABLE IF EXISTS foo")
dbutils.notebook.exit("done")
'''
    patches = spe.suggest_known_patches(src, "Output/src/Job.scala")
    ids = {p["id"] for p in patches}
    assert "remove_drop_table_sql" in ids
    assert "remove_dbutils_notebook_exit" in ids


def test_investigation_flags_cloud_and_excel():
    src = '''
df.write.format("com.crealytics.spark.excel").save("s3://bucket/out.xlsx")
'''
    sites = spe.scan_investigation_sites(src, "Output/src/Job.scala")
    cats = {s["category"] for s in sites}
    assert "cloud_read_write" in cats or "connector_read" in cats


def test_investigation_skips_already_patched_lines():
    src = 'spark.read.parquet(System.getProperty("SCOS_INPUT_ORDERS"))\n'
    sites = spe.scan_investigation_sites(src, "Output/src/Job.scala")
    assert sites == []


def test_seed_udf_expected_divergences():
    analysis = {
        "entrypoints": [{
            "id": "job1",
            "udfs": [{"name": "Image$"}],
            "unsupported_constructs": [
                {"kind": "udf", "detail": "my_transform"},
            ],
        }],
    }
    n = spe.seed_udf_expected_divergences(analysis)
    assert n == 2
    divs = analysis["expected_divergences"]["job1.__udf__"]
    scopes = {d["scope"] for d in divs}
    assert scopes == {"udf"}
    # idempotent
    assert spe.seed_udf_expected_divergences(analysis) == 0
